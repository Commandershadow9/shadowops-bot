"""
SecurityScanAgent — Autonomer Security Agent (ersetzt DeepScanMode + alten SecurityAnalyst)

Vereint die Tiefe des alten Analyst (freie KI-Sessions, Learning Pipeline,
Activity Monitor, Discord-Briefings) mit der Architektur der Security Engine v6
(SecurityDB, PhaseTypeExecutor, Cross-Mode-Lock, CircuitBreaker, LearningBridge).

Hauptkomponenten:
  - Main-Loop: Prueft periodisch ob eine Session gestartet werden kann
  - Adaptive Session-Planung: fix_only/full_scan/quick_scan/maintenance
  - Activity Monitor: Nur starten wenn User idle
  - Pre-Session Maintenance: Git-Sync, Fix-Verifikation, Knowledge-Decay
  - Scan-Phase: AI analysiert Server mit Shell-Zugriff
  - Fix-Phase: Claude fixt Findings selbstaendig
  - Health-Snapshots: Vorher/Nachher Vergleich aller Services
  - Discord-Briefings: Ergebniszusammenfassung
  - GitHub-Issues: Automatische Issue-Erstellung mit Quality-Gates
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone, date
from typing import Dict, List, Optional

import discord

from .activity_monitor import ActivityMonitor
from .prompts import ANALYST_SYSTEM_PROMPT, ANALYST_CONTEXT_TEMPLATE, FIX_SESSION_PROMPT

logger = logging.getLogger('shadowops.scan_agent')

# ─────────────────────────────────────────────────────────────────────
# Konstanten (vom alten Analyst uebernommen)
# ─────────────────────────────────────────────────────────────────────

DEFAULT_MAX_SESSIONS_PER_DAY = 1

# Session-Modi: (timeout_seconds, max_turns, beschreibung)
SESSION_MODES = {
    'full_scan':   (2700, 120, 'Voller Scan aller Bereiche'),
    'quick_scan':  (1200, 50,  'Schnellcheck der kritischsten Bereiche'),
    'fix_only':    (7200, 200, 'Nur Findings abarbeiten, kein Scan'),
    'maintenance': (600,  15,  'Nur Verifikation + Maintenance, kein Scan/Fix'),
}

# Workload-Schwellenwerte
WORKLOAD_THRESHOLDS = {
    'heavy':  20,
    'normal': 5,
    'light':  1,
    'clean':  0,
}

# Main-Loop Intervall
MAIN_LOOP_INTERVAL = 60

# Heartbeat alle N Loops
HEARTBEAT_EVERY = 10

# Maintenance-Scan-Schwelle: Wie viele Tage zwischen Scans bei 0 Backlog?
# Default 1 = mindestens 1 Scan pro Tag (konfigurierbar via security_analyst.maintenance_scan_days)
MAINTENANCE_SCAN_INTERVAL_DAYS = 1

# Failure-Backoff (30 Min, 2h, 6h)
FAILURE_BACKOFF_SECONDS = [1800, 7200, 21600]
MAX_CONSECUTIVE_FAILURES = 3

# Projekt-Repo-Mapping
PROJECT_REPO_MAP = {
    'guildscout': 'Commandershadow9/GuildScout',
    'zerodox': 'Commandershadow9/ZERODOX',
    'shadowops': 'Commandershadow9/shadowops-bot',
    'sicherheitsdienst': 'Commandershadow9/sicherheitsdienst-tool',
    'project': 'Commandershadow9/sicherheitsdienst-tool',
}
DEFAULT_REPO = 'Commandershadow9/shadowops-bot'
SKIP_ISSUE_PROJECTS = {'openclaw', 'agents', 'blogger', 'content-pipeline'}

# Issue-Quality-Gates
MIN_ISSUE_TITLE_LEN = 10
MIN_ISSUE_BODY_LEN = 30

# Health-Check Services
USER_SERVICES = [
    'guildscout-bot',
    'guildscout-feedback-agent',
    'zerodox-support-agent',
    'seo-agent',
]
SYSTEM_SERVICES = [
    'shadowops-bot',
    'earlyoom',
]

# Geschuetzte Port-Bindings (Vorfall 2026-03-17: 11h Ausfall nach Aenderung)
PROTECTED_PORT_BINDINGS = {
    8766: 'Health/Changelog API (GuildScout+ZERODOX Proxy)',
    9090: 'GitHub Webhook (Traefik→Host)',
    9091: 'GuildScout Alerts (Docker→Host)',
}

# Projekt-Security-Profile
PROJECT_SECURITY_PROFILES = {
    'guildscout': {
        'path': '/home/cmdshadow/GuildScout',
        'stack': 'Go API + Next.js + Python Bot',
        'attack_surface': [
            'REST API (Go/Fiber, Port 8091 via Docker)',
            'OAuth2 Discord Login (Cookie-basiert)',
            'Datei-Upload (Magic-Byte Validierung, 5MB, UUID-Namen)',
            'WebSocket fuer Bot-Dashboard',
        ],
        'critical_files': [
            'api/internal/handlers/',
            'api/internal/middleware/auth.go',
            'bot/config/config.yaml',
            'docker-compose.yml',
            '.env',
        ],
        'services': 'Docker: guildscout-api-v3(8091), guildscout-postgres(5433), guildscout-redis(6379), guildscout-web(3000)',
        'auth': 'Discord OAuth2, Session-Cookies, Redis-Session-Store',
        'secrets': 'bot/config/config.yaml, .env (Docker), REDIS_PASSWORD',
    },
    'zerodox': {
        'path': '/home/cmdshadow/ZERODOX',
        'stack': 'Next.js 16, Prisma, PostgreSQL',
        'attack_surface': [
            'Kunden-Portal mit NextAuth v5 (Credentials + TOTP 2FA + Passkeys)',
            'REST API Endpoints (/api/)',
            'Cron-Jobs mit API-Key Auth',
            'Web-Chat + Support-Ticketing',
            'Stripe Webhook',
        ],
        'critical_files': [
            'web/src/app/api/',
            'web/src/lib/auth/',
            'web/src/lib/db/',
            'docker-compose.yml',
            'scripts/daily-cron.sh',
        ],
        'services': 'Docker: zerodox-web(3000 intern), zerodox-db(5434)',
        'auth': 'NextAuth v5: Credentials + TOTP + Passkeys/WebAuthn + Magic Links',
        'secrets': 'DATABASE_URL, AUTH_SECRET, TOTP_ENCRYPTION_KEY, BACKUP_ENCRYPTION_KEY, Stripe Keys',
    },
    'ai-agent-framework': {
        'path': '/home/cmdshadow/agents',
        'stack': 'Python Agent Framework',
        'attack_surface': [
            'AI-Provider-Chain (Codex CLI Subprocesses)',
            'Redis Pub/Sub Event-Subscriber',
            'PostgreSQL NOTIFY Listener',
        ],
        'critical_files': [
            'core/ai/',
            'run.sh',
            'projects/*/config.yaml',
        ],
        'services': 'systemd: guildscout-feedback-agent, zerodox-support-agent, seo-agent',
        'auth': 'OAuth Token (OpenAI), Setup-Token (Anthropic)',
        'secrets': '.env pro Projekt, OAuth-Tokens in auth-profiles.json',
    },
    'shadowops-bot': {
        'path': '/home/cmdshadow/shadowops-bot',
        'stack': 'Python 3.12, discord.py 2.7, Dual-Engine AI (Codex + Claude)',
        'attack_surface': [
            'GitHub Webhook Server (Port 9090, HMAC-Validierung)',
            'Health/Changelog REST API + RSS Feed (Port 8766)',
            'GuildScout Alert Forwarding (Port 9091)',
            'Discord Gateway (2 Guilds: DEV + ZERODOX)',
            'AI Subprocess Calls (Codex/Claude via stdin)',
            'Auto-Fix Executor (Shell-Commands auf Produktiv-Server)',
        ],
        'critical_files': [
            'config/config.yaml',
            'src/bot.py',
            'src/integrations/ai_engine.py',
            'src/integrations/security_engine/',
            'src/integrations/command_executor.py',
            'deploy/shadowops-bot.service',
        ],
        'services': 'systemd: shadowops-bot (system-level), Ports: 8766, 9090, 9091',
        'auth': 'Discord Bot Token, GitHub Token, HMAC Webhook Secret',
        'secrets': 'config/config.yaml (Discord Token, GitHub Token, API Keys, DB DSNs)',
    },
}


class SecurityScanAgent:
    """Autonomer Security Scan Agent — vereint Analyst-Tiefe mit Engine-v6-Architektur.

    Wartet bis der Server-Owner idle ist, startet dann eine KI-Session
    die den Server frei analysieren darf. Dokumentiert Findings, fixt
    sichere Probleme automatisch und erstellt GitHub-Issues fuer Code-Probleme.

    Nutzt SecurityDB (unified asyncpg) statt AnalystDB.
    Nutzt PhaseTypeExecutor + remediation_status fuer koordinierte Fixes.
    """

    def __init__(self, bot, config, ai_engine, db, context_manager=None,
                 executor=None, learning_bridge=None):
        self.bot = bot
        self.config = config
        self.ai_engine = ai_engine
        self.db = db
        self.context_manager = context_manager
        self.executor = executor
        self.learning_bridge = learning_bridge

        # Activity Monitor
        self.activity_monitor = ActivityMonitor(bot)

        # Data-Verzeichnis (fuer Force-Scan Flag, nicht /tmp wegen PrivateTmp)
        self._data_dir = os.path.join(os.getcwd(), 'data')

        # Config-Werte
        analyst_cfg = config._config.get('security_analyst', {})
        self.max_sessions_per_day = analyst_cfg.get(
            'max_sessions_per_day', DEFAULT_MAX_SESSIONS_PER_DAY
        )
        self.codex_model = analyst_cfg.get('model', 'gpt-5.3-codex')
        self.claude_model = analyst_cfg.get('fallback_model', 'claude-opus-4-6')
        self.maintenance_scan_days = analyst_cfg.get(
            'maintenance_scan_days', MAINTENANCE_SCAN_INTERVAL_DAYS
        )

        # State
        self._task: Optional[asyncio.Task] = None
        self._current_session_id: Optional[int] = None
        self._sessions_today: int = 0
        self._today: date = date.today()
        self._session_tokens_start: int = 0
        self._running: bool = False
        self._briefing_pending: bool = False
        self._pending_result: Optional[Dict] = None
        self._session_lock = asyncio.Lock()
        self._consecutive_failures: int = 0
        self._failure_cooldown_until: float = 0.0
        self._last_event_scan: float = 0.0

    # ─── Lifecycle ────────────────────────────────────────────────────

    async def start(self):
        """Agent starten — Main-Loop als Background-Task"""
        if self._running:
            logger.warning("SecurityScanAgent laeuft bereits")
            return
        self._sessions_today = await self._count_sessions_today()
        if self._sessions_today > 0:
            logger.info("Session-Counter aus DB: %d", self._sessions_today)
        self._running = True
        self._task = asyncio.create_task(self._main_loop())
        logger.info("SecurityScanAgent gestartet")

    async def stop(self):
        """Agent stoppen"""
        if not self._running:
            return
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("SecurityScanAgent gestoppt")

    def reset_daily(self):
        """Taeglicher Reset"""
        self._sessions_today = 0

    # ─── Main Loop ────────────────────────────────────────────────────

    async def _main_loop(self):
        await asyncio.sleep(30)
        logger.info("SecurityScanAgent Main-Loop aktiv")
        loop_count = 0

        while self._running:
            try:
                loop_count += 1

                today = date.today()
                if today != self._today:
                    self._today = today
                    self._sessions_today = await self._count_sessions_today()
                    self._consecutive_failures = 0
                    self._failure_cooldown_until = 0.0
                    logger.info("Neuer Tag — Sessions: %d, Fehler zurueckgesetzt", self._sessions_today)

                if self._briefing_pending and self._pending_result:
                    ds = await self.activity_monitor.is_user_on_discord()
                    if ds in ('online', 'idle'):
                        await self._post_briefing(self._pending_result)
                        self._briefing_pending = False
                        self._pending_result = None

                # Force-Scan: data/force_scan erstellen um Activity-Check zu umgehen
                # NICHT /tmp/ wegen PrivateTmp=true in systemd (isoliertes /tmp)
                force_scan_flag = os.path.join(self._data_dir, 'force_scan')
                force_scan = os.path.exists(force_scan_flag)
                if force_scan:
                    try:
                        os.remove(force_scan_flag)
                    except OSError:
                        pass
                    logger.info("Force-Scan Flag erkannt — Activity-Check uebersprungen")
                    user_active = False
                else:
                    user_active = await self.activity_monitor.is_user_active()

                if loop_count % HEARTBEAT_EVERY == 0:
                    cd = max(0, self._failure_cooldown_until - time.time())
                    logger.debug("Heartbeat: active=%s, sessions=%d/%d, failures=%d, cd=%.0fs",
                                 user_active, self._sessions_today, self.max_sessions_per_day,
                                 self._consecutive_failures, cd)

                if self._failure_cooldown_until > time.time():
                    await asyncio.sleep(MAIN_LOOP_INTERVAL)
                    continue

                if not user_active and self._current_session_id is None:
                    try:
                        fixable = await self._get_fixable_findings()
                        plan = self._plan_session(len(fixable))
                        effective_limit = min(plan['max_sessions'],
                                              self.max_sessions_per_day + plan['max_sessions'] - 1)
                    except Exception:
                        effective_limit = self.max_sessions_per_day

                    if self._sessions_today < effective_limit:
                        trigger = 'force_scan' if force_scan else 'idle_detected'
                        logger.info("User idle, Sessions %d/%d — starte Analyse (trigger=%s)",
                                    self._sessions_today, effective_limit, trigger)
                        await self._run_session(trigger_type=trigger)

            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.error("Main-Loop Fehler: %s", e, exc_info=True)
                await asyncio.sleep(300)
                continue

            await asyncio.sleep(MAIN_LOOP_INTERVAL)

    # ─── Session-Planung ──────────────────────────────────────────────

    def _plan_session(self, open_count: int) -> dict:
        if open_count >= WORKLOAD_THRESHOLDS['heavy']:
            return {'mode': 'fix_only', 'max_sessions': 3, 'scan': False, 'fix': True,
                    'reason': f'{open_count} Findings — fokussiert fixen'}
        elif open_count >= WORKLOAD_THRESHOLDS['normal']:
            return {'mode': 'full_scan', 'max_sessions': 2, 'scan': True, 'fix': True,
                    'reason': f'{open_count} Findings — Scan + Fix'}
        elif open_count >= WORKLOAD_THRESHOLDS['light']:
            return {'mode': 'quick_scan', 'max_sessions': 1, 'scan': True, 'fix': True,
                    'reason': f'{open_count} Findings — Quickcheck + Fix'}
        else:
            return {'mode': 'maintenance', 'max_sessions': 1, 'scan': False, 'fix': False,
                    'reason': 'Alles clean — nur Maintenance'}

    # ─── Session-Ausfuehrung ──────────────────────────────────────────

    async def _run_session(self, trigger_type: str = 'idle_detected'):
        if self._session_lock.locked():
            return
        async with self._session_lock:
            await self._run_session_inner(trigger_type=trigger_type)

    async def _run_session_inner(self, trigger_type: str = 'idle_detected'):
        session_id = None
        try:
            self._mark_token_start()
            await self._pre_session_maintenance()

            fixable = await self._get_fixable_findings()
            plan = self._plan_session(len(fixable))
            mode_config = SESSION_MODES[plan['mode']]

            logger.info("Session-Plan: %s (Backlog: %d, Reason: %s)",
                        plan['mode'], len(fixable), plan['reason'])

            # Maintenance: Bei 0 Backlog trotzdem regelmässig scannen
            # Vergleiche Kalendertage (nicht volle 24h-Perioden), damit ein Scan
            # von gestern 23:00 heute als "anderer Tag" zaehlt
            if plan['mode'] == 'maintenance':
                last = await self._get_last_session()
                days_since_scan = 99
                if last and last.get('ended_at'):
                    last_date = last['ended_at'].date()
                    today = datetime.now(timezone.utc).date()
                    days_since_scan = (today - last_date).days
                if days_since_scan >= self.maintenance_scan_days:
                    plan['mode'] = 'full_scan'
                    plan['scan'] = True
                    mode_config = SESSION_MODES['full_scan']
                    logger.info("Taeglicher Scan (letzter: vor %d Tag(en), Schwelle: %d) — starte full_scan",
                                days_since_scan, self.maintenance_scan_days)
                else:
                    logger.info("Scan heute bereits gelaufen — skip")
                    return

            # Fix-Only
            if plan['mode'] == 'fix_only':
                session_id = await self._start_session('fix_backlog')
                self._current_session_id = session_id
                self._sessions_today += 1
                await self._notify_session_start(session_id, mode='fix')
                await self._run_fix_phase(session_id)
                await self._end_session(session_id=session_id,
                    summary=f"Fix-Session: {len(fixable)} Findings",
                    topics=['backlog_fix'], model=self.claude_model,
                    findings_count=0, auto_fixes=0, issues_created=0)
                self._consecutive_failures = 0
                return

            # Scan + Fix
            session_id = await self._start_session(trigger_type)
            self._current_session_id = session_id
            self._sessions_today += 1

            logger.info("%s-Session (Timeout: %ds, Turns: %d)",
                        plan['mode'], mode_config[0], mode_config[1])
            await self._notify_session_start(session_id, mode='scan')

            health_before = await self._take_health_snapshot(session_id)

            knowledge_context = await self._build_ai_context()
            open_findings = await self._get_open_findings_summary()
            open_text = "\n".join(
                f"- [{f['severity'].upper()}] {f['title']}" for f in open_findings[:20]
            ) if open_findings else "(keine offenen Findings)"

            scan_plan = await self._build_scan_plan()
            context = ANALYST_CONTEXT_TEMPLATE.format(
                knowledge_context=knowledge_context,
                open_findings=open_text,
                scan_plan=scan_plan,
            )
            prompt = ANALYST_SYSTEM_PROMPT + "\n\n" + context

            if plan['mode'] == 'quick_scan':
                prompt += (
                    "\n\n## QUICK-SCAN MODUS\n"
                    "Weniger Zeit. Fokus auf: geaenderte Bereiche, Scan-Luecken, Critical/High.\n"
                )

            if trigger_type == 'idle_detected' and await self.activity_monitor.is_user_active():
                logger.info("User aktiv — Session #%d abgebrochen", session_id)
                await self._pause_session(session_id)
                self._current_session_id = None
                self._sessions_today -= 1
                return

            result = await self.ai_engine.run_analyst_session(
                prompt=prompt, timeout=mode_config[0], max_turns=mode_config[1],
                codex_model=self.codex_model, claude_model=self.claude_model,
            )

            health_after = await self._take_health_snapshot(session_id)
            health_ok = self._compare_health(health_before, health_after)
            if not health_ok:
                await self._send_health_alert(health_before, health_after)

            if result:
                provider = result.pop('_provider', 'unknown')
                model_used = result.pop('_model', 'unknown')
                self._consecutive_failures = 0
                self._failure_cooldown_until = 0.0
                logger.info("Session #%d OK via %s/%s", session_id, provider, model_used)
                await self._process_results(session_id, result, health_ok, model_used)
                await self._notify_learning(session_id, plan['mode'], result)
                await self._run_fix_phase(session_id)
            else:
                self._consecutive_failures += 1
                self._apply_failure_backoff(session_id)
                await self._notify_session_failure(session_id)
                await self._end_session(session_id=session_id,
                    summary=f"Fehler #{self._consecutive_failures}",
                    topics=[], model=self.codex_model,
                    findings_count=0, auto_fixes=0, issues_created=0)

        except asyncio.CancelledError:
            if session_id:
                try:
                    await self._pause_session(session_id)
                except Exception:
                    pass
            raise
        except Exception as e:
            logger.error("Session-Fehler: %s", e, exc_info=True)
            self._consecutive_failures += 1
            self._apply_failure_backoff(session_id)
            await self._notify_session_failure(session_id, error=str(e)[:200])
            if session_id:
                try:
                    await self._end_session(session_id=session_id,
                        summary=f"Fehler: {str(e)[:200]}",
                        topics=[], model=self.codex_model,
                        findings_count=0, auto_fixes=0, issues_created=0)
                except Exception:
                    pass
        finally:
            self._current_session_id = None

    # ─── Pre-Session Maintenance ──────────────────────────────────────

    async def _pre_session_maintenance(self):
        await self._sync_project_security_profiles()
        await self._sync_git_activity_to_db()
        await self._verify_recent_fixes()
        await self._decay_knowledge()
        await self._sync_cross_agent_knowledge()

    async def _sync_project_security_profiles(self):
        try:
            for name, profile in PROJECT_SECURITY_PROFILES.items():
                attack_surface = '; '.join(profile['attack_surface'])
                critical = ', '.join(profile['critical_files'][:5])
                content = (
                    f"Stack: {profile['stack']}. "
                    f"Angriffsoberflaeche: {attack_surface}. "
                    f"Auth: {profile['auth']}. "
                    f"Kritische Dateien: {critical}. "
                    f"Services: {profile['services']}. "
                    f"Secrets: {profile['secrets']}."
                )
                await self.db.store_knowledge('project_security',
                    f'{name}_security_profile', content, confidence=0.95)
        except Exception as e:
            logger.warning("Security-Profile-Sync fehlgeschlagen: %s", e)

    async def _sync_git_activity_to_db(self):
        if not self.context_manager:
            return
        try:
            self.context_manager.reload_git_history()
            for project_name, analyzer in self.context_manager.git_analyzers.items():
                try:
                    patterns = analyzer.analyze_patterns()
                except Exception:
                    continue
                total = patterns.get('total_commits', 0)
                fixes = patterns.get('total_fixes', 0)
                security = patterns.get('total_security', 0)
                hotspots = patterns.get('frequently_changed_files', [])[:3]
                hotspot_text = ', '.join(f"{f}({n}x)" for f, n in hotspots) if hotspots else 'keine'
                sec_fixes = patterns.get('recent_security_fixes', [])[:3]
                sec_text = '; '.join(f"{s['date']}: {s['subject'][:60]}" for s in sec_fixes) if sec_fixes else 'keine'
                content = (f"{total} Commits (30d), {fixes} Fixes, {security} Security. "
                           f"Hotspots: {hotspot_text}. Sec-Fixes: {sec_text}.")
                confidence = min(0.95, 0.5 + (total / 200))
                await self.db.store_knowledge('project_activity',
                    f'{project_name}_git_activity', content, confidence=confidence)
            logger.info("Git-Activity synchronisiert")
        except Exception as e:
            logger.warning("Git-Activity-Sync fehlgeschlagen: %s", e)

    async def _verify_recent_fixes(self):
        try:
            if not self.db.pool:
                return
            rows = await self.db.pool.fetch("""
                SELECT fa.id, fa.finding_id, f.category, f.affected_project,
                       f.affected_files, f.title as finding_title
                FROM fix_attempts fa
                JOIN findings f ON f.id = fa.finding_id
                LEFT JOIN fix_verifications fv ON fv.fix_attempt_id = fa.id
                WHERE fa.result = 'success' AND fa.created_at >= NOW() - INTERVAL '14 days'
                  AND fv.id IS NULL LIMIT 10
            """)
            if not rows:
                return
            logger.info("Verifiziere %d kuerzliche Fixes...", len(rows))
            regressions = 0
            for fix in rows:
                still_valid = True
                check_method = 'existence_check'
                regression_details = None
                category = fix.get('category', '')
                try:
                    if category in ('firewall', 'network'):
                        proc = await asyncio.create_subprocess_exec(
                            'sudo', 'ufw', 'status',
                            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
                        check_method = 'ufw status'
                        if b'Status: active' not in stdout:
                            still_valid = False
                            regression_details = 'UFW nicht aktiv'
                    elif category == 'docker':
                        proc = await asyncio.create_subprocess_exec(
                            'docker', 'ps', '--format', '{{.Names}}:{{.Status}}',
                            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                        await asyncio.wait_for(proc.communicate(), timeout=10)
                        check_method = 'docker ps'
                    elif category == 'permissions':
                        files = fix.get('affected_files') or []
                        for f in files[:3]:
                            if os.path.exists(f):
                                mode = oct(os.stat(f).st_mode)[-3:]
                                if mode.endswith('4') or mode.endswith('6'):
                                    if any(s in f for s in ['.env', 'credential', 'secret', 'key']):
                                        still_valid = False
                                        regression_details = f'{f} world-readable ({mode})'
                                        break
                        check_method = 'file permission check'
                    elif category in ('config', 'ssh'):
                        proc = await asyncio.create_subprocess_exec(
                            'systemctl', 'is-active', 'sshd',
                            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
                        check_method = 'systemctl sshd'
                        if b'active' not in stdout:
                            still_valid = False
                            regression_details = 'sshd nicht aktiv'
                except (asyncio.TimeoutError, Exception):
                    continue
                try:
                    await self.db.pool.execute("""
                        INSERT INTO fix_verifications
                            (fix_attempt_id, session_id, still_valid, check_method, regression_details)
                        VALUES ($1, $2, $3, $4, $5)
                    """, fix['id'], self._current_session_id, still_valid, check_method, regression_details)
                except Exception:
                    pass
                if not still_valid:
                    regressions += 1
                    try:
                        await self.db.pool.execute(
                            "UPDATE findings SET status = 'open', fixed_at = NULL WHERE id = $1",
                            fix['finding_id'])
                    except Exception:
                        pass
                    logger.warning("REGRESSION: Fix #%d Finding #%d: %s",
                                   fix['id'], fix['finding_id'], regression_details)
            if regressions > 0:
                await self._notify_regressions(regressions)
            logger.info("Fix-Verifikation: %d geprueft, %d Regressionen", len(rows), regressions)
        except Exception as e:
            logger.warning("Fix-Verifikation fehlgeschlagen: %s", e)

    async def _decay_knowledge(self):
        try:
            if self.db.pool:
                result = await self.db.pool.execute("""
                    UPDATE knowledge SET confidence = GREATEST(0.2, confidence - 0.05)
                    WHERE last_verified < NOW() - INTERVAL '14 days' AND confidence > 0.2
                """)
                count = int(result.split()[-1]) if result else 0
                if count > 0:
                    logger.info("Knowledge-Decay: %d Eintraege", count)
        except Exception as e:
            logger.debug("Knowledge-Decay fehlgeschlagen: %s", e)

    async def _sync_cross_agent_knowledge(self):
        try:
            if self.learning_bridge and self.learning_bridge.is_connected:
                rows = await self.db.pool.fetch("""
                    SELECT affected_project, COUNT(*) as cnt, severity
                    FROM findings WHERE status = 'open' AND severity IN ('critical', 'high')
                    GROUP BY affected_project, severity
                """)
                for r in rows:
                    project = r['affected_project'] or 'infrastructure'
                    await self.learning_bridge.share_knowledge(
                        'security_alerts', f'{project}_open_criticals',
                        f"{r['cnt']} offene {r['severity'].upper()}-Findings.",
                        confidence=0.9)
        except Exception as e:
            logger.debug("Cross-Agent-Sync fehlgeschlagen: %s", e)

    # ─── Ergebnis-Verarbeitung ────────────────────────────────────────

    async def _process_results(self, session_id: int, result: Dict,
                                health_ok: bool, model_used: str = 'unknown'):
        findings = result.get('findings', [])
        knowledge_updates = result.get('knowledge_updates', [])
        topics = result.get('topics_investigated', [])
        summary = result.get('summary', 'Keine Zusammenfassung')
        next_priority = result.get('next_priority', '')

        for ku in knowledge_updates:
            try:
                await self.db.store_knowledge(
                    category=ku.get('category', 'unknown'),
                    subject=ku.get('subject', 'unknown'),
                    content=ku.get('content', ''),
                    confidence=ku.get('confidence', 0.5))
            except Exception as e:
                logger.error("Knowledge-Update fehlgeschlagen: %s", e)

        auto_fixes = 0
        issues_created = 0
        duplicates_skipped = 0

        for finding in findings:
            try:
                title = finding.get('title', 'Unbenannt')
                existing = await self._find_similar_open_finding(title)
                if existing:
                    duplicates_skipped += 1
                    continue

                fix_type = finding.get('fix_type', 'info_only')
                if fix_type == 'auto_fixed':
                    fix_type = 'issue_needed'
                github_issue_url = None

                should_issue = (fix_type == 'issue_needed' or
                    (fix_type == 'needs_decision' and finding.get('severity') in ('critical', 'high', 'medium')))
                if should_issue:
                    github_issue_url = await self._create_github_issue(finding)
                    if github_issue_url:
                        issues_created += 1

                await self.db.pool.execute("""
                    INSERT INTO findings (severity, category, title, description, session_id,
                        affected_project, affected_files, fix_type, github_issue_url)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                """, finding.get('severity', 'info'), finding.get('category', 'unknown'),
                    title, finding.get('description', ''), session_id,
                    finding.get('affected_project'), finding.get('affected_files'),
                    fix_type, github_issue_url)
            except Exception as e:
                logger.error("Finding-Verarbeitung fehlgeschlagen: %s", e)

        # Finding-Quality Assessments
        for fa in result.get('finding_assessments', []):
            try:
                fid = int(fa.get('finding_id', 0))
                if fid > 0:
                    await self.db.pool.execute("""
                        INSERT INTO finding_quality (finding_id, is_actionable, is_false_positive,
                            false_positive_reason, discovery_method, confidence_score)
                        VALUES ($1,$2,$3,$4,$5,$6)
                        ON CONFLICT (finding_id) DO UPDATE SET
                            is_actionable=$2, is_false_positive=$3, confidence_score=$6
                    """, fid, fa.get('is_actionable', True), fa.get('is_false_positive', False),
                        fa.get('false_positive_reason'), fa.get('discovery_method'),
                        fa.get('confidence', 0.5))
            except Exception:
                pass

        # Coverage
        for area in result.get('areas_checked', []):
            try:
                await self.db.pool.execute(
                    "INSERT INTO scan_coverage (session_id, area, checked, depth) VALUES ($1,$2,TRUE,'basic')",
                    session_id, area)
            except Exception:
                pass
        for area in result.get('areas_deferred', []):
            try:
                await self.db.pool.execute(
                    "INSERT INTO scan_coverage (session_id, area, checked, depth) VALUES ($1,$2,FALSE,'skipped')",
                    session_id, area)
            except Exception:
                pass

        # Auto-Close stale
        try:
            await self.db.pool.execute("""
                UPDATE findings SET status='fixed', fixed_at=NOW()
                WHERE status='open' AND github_issue_url IS NULL AND found_at < NOW()-INTERVAL '30 days'
            """)
        except Exception:
            pass

        await self._end_session(session_id=session_id, summary=summary, topics=topics,
            model=model_used, findings_count=len(findings),
            auto_fixes=auto_fixes, issues_created=issues_created)

        logger.info("Session #%d: %d Findings, %d Issues, %d Duplikate",
                     session_id, len(findings), issues_created, duplicates_skipped)

        briefing = {'summary': summary, 'topics': topics, 'findings': findings,
                     'auto_fixes': auto_fixes, 'issues_created': issues_created,
                     'health_ok': health_ok, 'next_priority': next_priority}
        ds = await self.activity_monitor.is_user_on_discord()
        if ds in ('online', 'idle'):
            await self._post_briefing(briefing)
        else:
            self._briefing_pending = True
            self._pending_result = briefing

    # ─── Fix-Phase ────────────────────────────────────────────────────

    async def _run_fix_phase(self, scan_session_id: int):
        try:
            fixable = await self._get_fixable_findings()
            if not fixable:
                logger.info("Fix-Phase: Keine fixbaren Findings")
                return

            logger.info("Fix-Phase: %d Findings", len(fixable))
            findings_text = []
            for f in fixable:
                files = ', '.join(f.get('affected_files') or []) or '(keine)'
                issue_info = f" (Issue: {f['github_issue_url']})" if f.get('github_issue_url') else ''
                prior = ''
                try:
                    attempts = await self.db.pool.fetch("""
                        SELECT approach, result, error_message FROM fix_attempts
                        WHERE finding_id=$1 ORDER BY created_at DESC LIMIT 3
                    """, f['id'])
                    if attempts:
                        lines = []
                        for a in attempts:
                            icon = "✅" if a['result'] == 'success' else "❌"
                            err = f" — {a['error_message'][:80]}" if a['error_message'] else ""
                            lines.append(f"  {icon} {a['approach'][:100]}{err}")
                        prior = "\n**Vorherige Versuche (ANDERER Ansatz!):**\n" + "\n".join(lines) + "\n"
                except Exception:
                    pass
                findings_text.append(
                    f"### Finding #{f['id']} [{f['severity'].upper()}] — {f['category']}\n"
                    f"**{f['title']}**\n{f['description']}\n"
                    f"Projekt: {f.get('affected_project','?')} | Dateien: {files}{issue_info}{prior}\n")

            knowledge = ''
            try:
                knowledge = await self._build_ai_context()
            except Exception:
                pass

            prompt = FIX_SESSION_PROMPT.format(findings_list='\n'.join(findings_text))
            if knowledge:
                prompt += "\n\n## GELERNTES WISSEN\n\n" + knowledge

            await self._notify_fix_phase_start(len(fixable))
            fix_result = await self.ai_engine.run_fix_session(
                prompt=prompt, timeout=7200, max_turns=200, model=self.claude_model)

            if not fix_result:
                logger.warning("Fix-Phase: Kein Ergebnis")
                return

            fixed_count = 0
            pr_count = 0
            for r in fix_result.get('results', []):
                try:
                    finding_id = int(r.get('finding_id'))
                except (ValueError, TypeError):
                    continue
                action = r.get('action', 'fixed')
                details = r.get('details', '')

                if action == 'fixed':
                    await self._mark_finding_fixed(finding_id)
                    try:
                        await self.db.pool.execute("""
                            INSERT INTO fix_attempts (finding_id, session_id, approach, result)
                            VALUES ($1,$2,$3,'success')
                        """, finding_id, scan_session_id, (details[:200] or 'direct fix'))
                    except Exception:
                        pass
                    # Cross-Mode-Lock
                    try:
                        eid = f"finding_{finding_id}"
                        await self.db.claim_event(eid, 'deep_scan')
                        await self.db.release_event(eid, 'completed')
                    except Exception:
                        pass
                    fixed_count += 1
                elif action == 'pr_created':
                    await self._mark_finding_fixed(finding_id)
                    try:
                        await self.db.pool.execute(
                            "UPDATE findings SET github_issue_url=$1 WHERE id=$2",
                            details, finding_id)
                    except Exception:
                        pass
                    pr_count += 1
                elif action == 'failed':
                    try:
                        await self.db.pool.execute("""
                            INSERT INTO fix_attempts (finding_id, session_id, approach, result, error_message)
                            VALUES ($1,$2,$3,'failure',$4)
                        """, finding_id, scan_session_id,
                            (details[:200] or 'unknown'), r.get('error', '')[:500])
                    except Exception:
                        pass

            logger.info("Fix-Phase: %d gefixt, %d PRs (von %d)", fixed_count, pr_count, len(fixable))
            await self._notify_fix_phase_complete(fixed_count, pr_count, len(fixable),
                                                   fix_result.get('summary', ''))
        except Exception as e:
            logger.error("Fix-Phase Fehler: %s", e, exc_info=True)

    # ─── Event-Scan ───────────────────────────────────────────────────

    async def trigger_event_scan(self, event_type: str, details: str = ""):
        if self._session_lock.locked():
            return
        if time.time() - self._last_event_scan < 7200:
            return
        logger.info("Event-Scan: %s — %s", event_type, details[:100])
        self._last_event_scan = time.time()
        async with self._session_lock:
            await self._run_session_inner(trigger_type=f'event:{event_type}')

    # ─── DB-Wrapper ───────────────────────────────────────────────────

    async def _start_session(self, trigger_type: str) -> int:
        row = await self.db.pool.fetchrow(
            "INSERT INTO sessions (started_at, trigger_type, status) VALUES ($1,$2,'running') RETURNING id",
            datetime.now(timezone.utc), trigger_type)
        return row['id']

    async def _end_session(self, session_id: int, summary: str, topics: List[str],
                           model: str, findings_count: int, auto_fixes: int, issues_created: int):
        await self.db.pool.execute("""
            UPDATE sessions SET ended_at=$1, ai_summary=$2, topics_investigated=$3,
                tokens_used=$4, model_used=$5, findings_count=$6,
                auto_fixes_count=$7, issues_created=$8, status='completed' WHERE id=$9
        """, datetime.now(timezone.utc), summary, topics, self._get_session_tokens(),
            model, findings_count, auto_fixes, issues_created, session_id)

    async def _pause_session(self, session_id: int):
        await self.db.pool.execute(
            "UPDATE sessions SET ended_at=$1, status='paused' WHERE id=$2",
            datetime.now(timezone.utc), session_id)

    async def _count_sessions_today(self) -> int:
        if not self.db.pool:
            return 0
        count = await self.db.pool.fetchval("""
            SELECT COUNT(*) FROM sessions WHERE started_at::date=CURRENT_DATE
              AND status IN ('completed','running')
              AND (findings_count>0 OR tokens_used>0 OR status='running')
        """)
        return count or 0

    async def _get_last_session(self) -> Optional[Dict]:
        """Letzte ERFOLGREICHE Session (mit Findings oder Auto-Fixes)."""
        row = await self.db.pool.fetchrow("""
            SELECT * FROM sessions WHERE status='completed'
              AND (findings_count > 0 OR auto_fixes_count > 0 OR tokens_used > 0)
            ORDER BY ended_at DESC LIMIT 1
        """)
        return dict(row) if row else None

    async def _find_similar_open_finding(self, title: str) -> Optional[Dict]:
        row = await self.db.pool.fetchrow(
            "SELECT id, title, github_issue_url FROM findings WHERE status='open' AND LOWER(title)=LOWER($1)",
            title)
        if row:
            return dict(row)
        keywords = [w for w in title.lower().split() if len(w) >= 8]
        for kw in keywords[:3]:
            row = await self.db.pool.fetchrow("""
                SELECT id, title, github_issue_url FROM findings
                WHERE status='open' AND LOWER(title) LIKE '%' || $1 || '%' LIMIT 1
            """, kw)
            if row:
                return dict(row)
        return None

    async def _get_open_findings_summary(self) -> List[Dict]:
        rows = await self.db.pool.fetch("""
            SELECT f.id, f.severity, f.title, f.category FROM findings f
            LEFT JOIN finding_quality fq ON fq.finding_id=f.id
            WHERE f.status='open' AND (fq.is_false_positive IS NULL OR fq.is_false_positive=FALSE)
            ORDER BY f.found_at DESC LIMIT 30
        """)
        return [dict(r) for r in rows]

    async def _get_fixable_findings(self) -> List[Dict]:
        rows = await self.db.pool.fetch("""
            SELECT DISTINCT ON (LOWER(LEFT(f.title,60)))
                f.id, f.severity, f.category, f.title, f.description,
                f.fix_type, f.affected_project, f.affected_files, f.github_issue_url
            FROM findings f LEFT JOIN finding_quality fq ON fq.finding_id=f.id
            WHERE f.status='open' AND f.fix_type!='info_only'
              AND (fq.is_false_positive IS NULL OR fq.is_false_positive=FALSE)
            ORDER BY LOWER(LEFT(f.title,60)),
                CASE f.severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1
                    WHEN 'medium' THEN 2 WHEN 'low' THEN 3 ELSE 4 END,
                f.found_at DESC
        """)
        sev = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3, 'info': 4}
        result = [dict(r) for r in rows]
        result.sort(key=lambda f: (sev.get(f['severity'], 5), f.get('affected_project', '')))
        return result

    async def _mark_finding_fixed(self, finding_id: int):
        now = datetime.now(timezone.utc)
        title = await self.db.pool.fetchval("SELECT title FROM findings WHERE id=$1", finding_id)
        if title:
            await self.db.pool.execute("""
                UPDATE findings SET status='fixed', fixed_at=$1
                WHERE id=$2 OR (status='open' AND LOWER(LEFT(title,60))=LOWER(LEFT($3,60)))
            """, now, finding_id, title)
        else:
            await self.db.pool.execute(
                "UPDATE findings SET status='fixed', fixed_at=$1 WHERE id=$2", now, finding_id)

    # ─── AI-Kontext ───────────────────────────────────────────────────

    async def _build_ai_context(self) -> str:
        parts = []
        try:
            rows = await self.db.pool.fetch("SELECT * FROM knowledge ORDER BY category, subject")
            if rows:
                parts.append("## Akkumuliertes Wissen\n")
                cats: Dict[str, list] = {}
                for e in rows:
                    cats.setdefault(e['category'], []).append(e)
                for cat, entries in sorted(cats.items()):
                    parts.append(f"### {cat}\n")
                    for e in entries:
                        c = int(e['confidence'] * 100) if e['confidence'] else 50
                        parts.append(f"- **{e['subject']}** ({c}%): {e['content']}")
                    parts.append("")
        except Exception:
            pass
        try:
            last = await self._get_last_session()
            if last:
                parts.append("## Letzte Session\n")
                s, e = last.get('started_at'), last.get('ended_at')
                if s and e:
                    parts.append(f"- **Dauer:** {(e - s).total_seconds() / 60:.0f} Min")
                parts.append(f"- **Findings:** {last.get('findings_count', 0)}")
                parts.append(f"- **Fixes:** {last.get('auto_fixes_count', 0)}")
                sm = last.get('ai_summary')
                if sm:
                    parts.append(f"- **Zusammenfassung:** {sm}")
                parts.append("")
        except Exception:
            pass
        try:
            pats = await self.db.pool.fetch(
                "SELECT * FROM learned_patterns ORDER BY times_seen DESC LIMIT 10")
            if pats:
                parts.append("## Patterns\n")
                for p in pats:
                    parts.append(f"- **{p['pattern_type']}** ({p['times_seen']}x): {p['description']}")
                parts.append("")
        except Exception:
            pass
        try:
            threats = await self.db.pool.fetch("""
                SELECT ip_address::TEXT, total_bans, threat_score, permanent_blocked
                FROM ip_reputation WHERE total_bans>=2 ORDER BY threat_score DESC LIMIT 5
            """)
            if threats:
                parts.append("## Top-Bedrohungen\n")
                for t in threats:
                    parts.append(f"- {'🔒' if t['permanent_blocked'] else '⚠️'} "
                                 f"`{t['ip_address']}` — {t['total_bans']}x, Score: {t['threat_score']}/100")
                parts.append("")
        except Exception:
            pass
        try:
            stats = await self.db.pool.fetchrow("""
                SELECT COUNT(*) as cnt, COALESCE(SUM(tokens_used),0) as tokens
                FROM sessions WHERE started_at>=NOW()-INTERVAL '30 days'
            """)
            fo = await self.db.pool.fetchval("SELECT COUNT(*) FROM findings WHERE status='open'")
            parts.append("## 30-Tage-Statistik\n")
            parts.append(f"- Sessions: {stats['cnt']}, Offene Findings: {fo}, Token: {stats['tokens']}")
            parts.append("")
        except Exception:
            pass
        return "\n".join(parts)

    async def _build_scan_plan(self) -> str:
        lines = ["Priorisierter Scan-Plan:\n"]
        try:
            gaps = await self.db.pool.fetch("""
                SELECT area, EXTRACT(DAY FROM NOW()-MAX(s.started_at)) as days_ago
                FROM scan_coverage sc JOIN sessions s ON s.id=sc.session_id
                WHERE sc.checked=TRUE GROUP BY area
                HAVING MAX(s.started_at)<NOW()-INTERVAL '7 days'
                ORDER BY days_ago DESC
            """)
            if gaps:
                lines.append("### 1. Coverage-Luecken (hoechste Prio)")
                for g in gaps:
                    lines.append(f"- **{g['area']}** — {int(g['days_ago'])} Tage")
                lines.append("")
        except Exception:
            pass
        try:
            activity = await self.db.pool.fetch("""
                SELECT subject, content FROM knowledge
                WHERE category='project_activity' AND subject LIKE '%_git_activity'
            """)
            if activity:
                lines.append("### 2. Aktive Projekte")
                for r in activity:
                    lines.append(f"- **{r['subject'].replace('_git_activity','')}:** {r['content'][:120]}")
                lines.append("")
        except Exception:
            pass
        lines.append("### 3. Standard-Bereiche")
        lines.append("- firewall, docker, ssh, permissions, packages, logs, network")
        return "\n".join(lines)

    # ─── Token-Tracking ──────────────────────────────────────────────

    def _get_session_tokens(self) -> int:
        try:
            return max(0, (self.ai_engine._daily_tokens_used if self.ai_engine else 0) - self._session_tokens_start)
        except Exception:
            return 0

    def _mark_token_start(self):
        try:
            self._session_tokens_start = self.ai_engine._daily_tokens_used if self.ai_engine else 0
        except Exception:
            self._session_tokens_start = 0

    # ─── Failure-Backoff ──────────────────────────────────────────────

    def _apply_failure_backoff(self, session_id: Optional[int]):
        if self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            self._sessions_today = self.max_sessions_per_day
            logger.error("Session #%s: %d Fehler — deaktiviert", session_id, self._consecutive_failures)
            return
        idx = min(self._consecutive_failures - 1, len(FAILURE_BACKOFF_SECONDS) - 1)
        self._failure_cooldown_until = time.time() + FAILURE_BACKOFF_SECONDS[idx]
        logger.warning("Session #%s Fehler #%d — Backoff %d Min",
                        session_id, self._consecutive_failures, FAILURE_BACKOFF_SECONDS[idx] // 60)

    # ─── Health-Monitoring ────────────────────────────────────────────

    async def _take_health_snapshot(self, session_id: int) -> Dict:
        containers = {}
        services = {}
        resources = {}
        try:
            proc = await asyncio.create_subprocess_exec(
                'docker', 'ps', '--format', '{{.Names}}:{{.Status}}',
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            for line in stdout.decode().strip().split('\n'):
                if ':' in line:
                    n, s = line.split(':', 1)
                    containers[n.strip()] = s.strip()
        except Exception:
            pass
        user_env = {**os.environ, 'XDG_RUNTIME_DIR': '/run/user/1000'}
        for svc in USER_SERVICES:
            try:
                proc = await asyncio.create_subprocess_exec(
                    'systemctl', '--user', 'is-active', svc,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=user_env)
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
                services[svc] = stdout.decode().strip()
            except Exception:
                services[svc] = 'unknown'
        for svc in SYSTEM_SERVICES:
            try:
                proc = await asyncio.create_subprocess_exec(
                    'systemctl', 'is-active', svc,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
                services[svc] = stdout.decode().strip()
            except Exception:
                services[svc] = 'unknown'
        for cmd_name, cmd in [('disk', ['df', '-h', '--output=target,pcent', '/']),
                               ('memory', ['free', '-h'])]:
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
                resources[cmd_name] = stdout.decode().strip()
            except Exception:
                resources[cmd_name] = 'unknown'
        port_bindings = {}
        try:
            proc = await asyncio.create_subprocess_exec(
                'ss', '-tlnp', stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            for line in stdout.decode().strip().split('\n'):
                for port in PROTECTED_PORT_BINDINGS:
                    if f':{port}' in line:
                        for part in line.split():
                            if f':{port}' in part:
                                port_bindings[str(port)] = part.rsplit(':', 1)[0]
                                break
        except Exception:
            pass
        try:
            await self.db.pool.execute("""
                INSERT INTO health_snapshots (session_id, services, docker_containers, system_resources)
                VALUES ($1, $2::jsonb, $3::jsonb, $4::jsonb)
            """, session_id, json.dumps(services), json.dumps(containers), json.dumps(resources))
        except Exception:
            pass
        return {'containers': containers, 'services': services,
                'resources': resources, 'port_bindings': port_bindings}

    def _compare_health(self, before: Dict, after: Dict) -> bool:
        ok = True
        for n, sb in before.get('containers', {}).items():
            if 'up' in sb.lower():
                sa = after.get('containers', {}).get(n, 'MISSING')
                if 'up' not in sa.lower():
                    logger.critical("HEALTH-REGRESSION: Container '%s': UP→%s", n, sa)
                    ok = False
        for n, sb in before.get('services', {}).items():
            if sb == 'active':
                sa = after.get('services', {}).get(n, 'unknown')
                if sa != 'active':
                    logger.critical("HEALTH-REGRESSION: Service '%s': active→%s", n, sa)
                    ok = False
        for ps in [str(p) for p in PROTECTED_PORT_BINDINGS]:
            bb = before.get('port_bindings', {}).get(ps, '')
            ba = after.get('port_bindings', {}).get(ps, '')
            if bb == '0.0.0.0' and ba != '0.0.0.0' and ba:
                logger.critical("PORT-REGRESSION: %s: 0.0.0.0→%s", ps, ba)
                ok = False
        return ok

    async def _send_health_alert(self, before: Dict, after: Dict):
        try:
            ch_id = getattr(self.config, 'critical_channel', None)
            if not ch_id or not self.bot:
                return
            ch = self.bot.get_channel(int(ch_id))
            if not ch:
                return
            changes = []
            for n, sb in before.get('containers', {}).items():
                if 'up' in sb.lower():
                    sa = after.get('containers', {}).get(n, 'MISSING')
                    if 'up' not in sa.lower():
                        changes.append(f"Container `{n}`: UP → {sa}")
            for n, sb in before.get('services', {}).items():
                if sb == 'active':
                    sa = after.get('services', {}).get(n, 'unknown')
                    if sa != 'active':
                        changes.append(f"Service `{n}`: active → {sa}")
            embed = discord.Embed(title="CRITICAL: Health-Regression",
                description="\n".join(changes) or "Unbekannte Aenderungen",
                color=discord.Color.red(), timestamp=datetime.now(timezone.utc))
            embed.set_footer(text="SecurityScanAgent")
            await ch.send(embed=embed)
        except Exception:
            pass

    # ─── Discord-Benachrichtigungen ───────────────────────────────────

    def _get_briefing_channel(self) -> Optional[discord.TextChannel]:
        ch_id = (self.config.channels.get('security_briefing')
                 or self.config.channels.get('ai_learning', 0))
        return self.bot.get_channel(int(ch_id)) if ch_id else None

    async def _notify_session_start(self, session_id: int, mode: str = 'scan'):
        ch = self._get_briefing_channel()
        if not ch:
            return
        today_str = date.today().strftime('%d.%m.%Y')
        if mode == 'fix':
            embed = discord.Embed(title=f"🔧 Fix-Session #{session_id}",
                description=f"**Datum:** {today_str}\n**Engine:** `{self.claude_model}`",
                color=discord.Color.orange(), timestamp=datetime.now(timezone.utc))
        else:
            embed = discord.Embed(title=f"🔍 Scan-Session #{session_id}",
                description=f"**Datum:** {today_str}\n**Scan:** `{self.codex_model}` / `{self.claude_model}`\n"
                            f"**Sessions:** {self._sessions_today}/{self.max_sessions_per_day}",
                color=discord.Color.blue(), timestamp=datetime.now(timezone.utc))
        embed.set_footer(text="SecurityScanAgent")
        try:
            await ch.send(embed=embed)
        except Exception:
            pass

    async def _notify_session_failure(self, session_id: Optional[int], error: str = ""):
        ch = self._get_briefing_channel()
        if not ch:
            return
        disabled = self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES
        color = discord.Color.red() if disabled else discord.Color.orange()
        status = "DEAKTIVIERT" if disabled else (
            f"Retry in {FAILURE_BACKOFF_SECONDS[min(self._consecutive_failures-1, len(FAILURE_BACKOFF_SECONDS)-1)]//60}m")
        embed = discord.Embed(
            title=f"❌ Session #{session_id or '?'} fehlgeschlagen",
            description=f"**Fehler:** {self._consecutive_failures}/{MAX_CONSECUTIVE_FAILURES}\n**Status:** {status}"
                        + (f"\n**Details:** `{error}`" if error else ""),
            color=color, timestamp=datetime.now(timezone.utc))
        embed.set_footer(text="SecurityScanAgent")
        try:
            await ch.send(embed=embed)
        except Exception:
            pass

    async def _notify_regressions(self, count: int):
        ch = self._get_briefing_channel()
        if not ch:
            return
        try:
            await ch.send(embed=discord.Embed(
                title=f"⚠️ {count} Fix-Regression(en)", description="Findings re-opened.",
                color=0xe67e22, timestamp=datetime.now(timezone.utc)))
        except Exception:
            pass

    async def _notify_fix_phase_start(self, count: int):
        ch = self._get_briefing_channel()
        if not ch:
            return
        try:
            await ch.send(embed=discord.Embed(
                title="🔧 Fix-Phase gestartet",
                description=f"Arbeite {count} Findings ab...", color=0x3498db))
        except Exception:
            pass

    async def _notify_fix_phase_complete(self, fixed: int, prs: int, total: int, summary: str):
        ch = self._get_briefing_channel()
        if not ch:
            return
        embed = discord.Embed(
            title=f"✅ Fix-Phase: {fixed + prs}/{total} bearbeitet",
            description=summary[:500] if summary else "—",
            color=0x2ecc71 if (fixed + prs) > 0 else 0xe74c3c)
        embed.add_field(name="Gefixt", value=str(fixed), inline=True)
        embed.add_field(name="PRs", value=str(prs), inline=True)
        try:
            await ch.send(embed=embed)
        except Exception:
            pass

    async def _post_briefing(self, result: Dict):
        ch = self._get_briefing_channel()
        if not ch:
            return
        findings = result.get('findings', [])
        health_ok = result.get('health_ok', True)
        has_crit = any(f.get('severity') in ('critical', 'high') for f in findings)
        if not health_ok:
            color, emoji = discord.Color.red(), "❌"
        elif has_crit or findings:
            color, emoji = discord.Color.orange(), "⚠️"
        else:
            color, emoji = discord.Color.green(), "✅"
        embed = discord.Embed(
            title=f"{emoji} Security Briefing — {date.today().strftime('%d.%m.%Y')}",
            description=result.get('summary', '—'), color=color,
            timestamp=datetime.now(timezone.utc))
        topics = result.get('topics', [])
        if topics:
            embed.add_field(name="Themen", value="\n".join(f"- {t}" for t in topics)[:1024], inline=False)
        decision = [f for f in findings if f.get('fix_type') in ('needs_decision', 'issue_needed')]
        if decision:
            sev_e = {'critical': '🔴', 'high': '🟠', 'medium': '🟡', 'low': '🔵', 'info': '⚪'}
            embed.add_field(name=f"Findings ({len(decision)})",
                value="\n".join(f"{sev_e.get(f.get('severity','info'),'⚪')} {f.get('title','?')}"
                                for f in decision)[:1024], inline=False)
        embed.set_footer(text=f"{len(findings)} Findings | {result.get('auto_fixes',0)} Fixes | "
                              f"{result.get('issues_created',0)} Issues | Health: {'OK' if health_ok else 'FEHLER'}")
        try:
            await ch.send(embed=embed)
        except Exception:
            pass

    async def _notify_learning(self, session_id: int, mode: str, result: Dict):
        notifier = getattr(self.bot, 'learning_notifier', None) if self.bot else None
        if notifier:
            try:
                await notifier.notify_analyst_session(
                    session_id=session_id, mode=mode,
                    findings_count=len(result.get('findings', [])), fixed_count=0, pr_count=0,
                    tokens_used=self._get_session_tokens(),
                    coverage_areas=len(result.get('areas_checked', [])))
            except Exception:
                pass

    # ─── GitHub-Issues ────────────────────────────────────────────────

    def _resolve_repo(self, affected_project: str) -> str:
        pl = affected_project.lower()
        for k, r in PROJECT_REPO_MAP.items():
            if k in pl:
                return r
        return DEFAULT_REPO

    async def _create_github_issue(self, finding: Dict) -> Optional[str]:
        ap = finding.get('affected_project', '').strip()
        title = finding.get('issue_title', finding.get('title', '')).strip()
        body = finding.get('issue_body', finding.get('description', '')).strip()
        severity = finding.get('severity', 'medium')

        if not title or len(title) < MIN_ISSUE_TITLE_LEN:
            return None
        if not body or len(body) < MIN_ISSUE_BODY_LEN:
            return None
        for skip in SKIP_ISSUE_PROJECTS:
            if skip in ap.lower():
                return None

        repo = self._resolve_repo(ap)
        existing = await self._find_similar_open_finding(finding.get('title', ''))
        if existing:
            return existing.get('github_issue_url') or None

        try:
            search = title[:60].replace('[', '').replace(']', '').replace('"', '')
            proc = await asyncio.create_subprocess_exec(
                'gh', 'issue', 'list', '--repo', repo, '--state', 'open',
                '--search', search, '--limit', '5', '--json', 'number,title,url',
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
            if proc.returncode == 0:
                issues = json.loads(out.decode() or '[]')
                if issues:
                    return issues[0].get('url', '')
        except Exception:
            pass

        ft = f"[Security] {title}"
        bb = f"**Severity:** {severity.upper()} | **Projekt:** {ap or 'Server'}\n\n{body}"
        try:
            proc = await asyncio.create_subprocess_exec(
                'gh', 'issue', 'create', '--repo', repo,
                '--title', ft, '--body', bb, '--label', f"security,priority:{severity}",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            out, err = await asyncio.wait_for(proc.communicate(), timeout=30)
            if proc.returncode == 0:
                return out.decode().strip()
            if 'label' in err.decode().lower():
                proc2 = await asyncio.create_subprocess_exec(
                    'gh', 'issue', 'create', '--repo', repo, '--title', ft, '--body', bb,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                out2, _ = await asyncio.wait_for(proc2.communicate(), timeout=30)
                if proc2.returncode == 0:
                    return out2.decode().strip()
        except Exception as e:
            logger.error("GitHub-Issue Fehler: %s", e)
        return None

    # ─── Stats ────────────────────────────────────────────────────────

    def get_stats(self) -> Dict:
        return {
            'sessions_today': self._sessions_today,
            'max_sessions': self.max_sessions_per_day,
            'consecutive_failures': self._consecutive_failures,
            'running': self._running,
            'session_active': self._current_session_id is not None,
            'briefing_pending': self._briefing_pending,
        }

    async def can_start_session(self) -> bool:
        if not self._running:
            return False
        fixable = await self._get_fixable_findings()
        plan = self._plan_session(len(fixable))
        limit = min(plan['max_sessions'], self.max_sessions_per_day + plan['max_sessions'] - 1)
        return self._sessions_today < limit

    async def run_session(self) -> Dict:
        if self._session_lock.locked():
            return {'status': 'skipped', 'reason': 'lock'}
        async with self._session_lock:
            await self._run_session_inner(trigger_type='engine_triggered')
            return {'status': 'completed', 'sessions_today': self._sessions_today}
