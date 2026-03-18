"""
SecurityAnalyst — Autonomer Security Engineer fuer den ShadowOps Bot

Orchestriert autonome Claude-Sessions zur Server-Sicherheitsanalyse.
Wartet bis der User idle ist, fuehrt eine Analyse-Session durch,
dokumentiert Findings, erstellt GitHub-Issues und postet Briefings.

Hauptkomponenten:
  - Main-Loop: Prueft periodisch ob eine Session gestartet werden kann
  - Health-Snapshots: Vorher/Nachher Vergleich aller Services
  - Briefings: Discord-Embeds mit Ergebniszusammenfassung
  - GitHub-Issues: Automatische Issue-Erstellung fuer Code-Findings
"""

import asyncio
import logging
import time
from datetime import datetime, timezone, date
from typing import Dict, List, Optional

import discord

from .analyst_db import AnalystDB
from .activity_monitor import ActivityMonitor
from .prompts import ANALYST_SYSTEM_PROMPT, ANALYST_CONTEXT_TEMPLATE

logger = logging.getLogger('shadowops.analyst')

# ─────────────────────────────────────────────────────────────────────
# Konstanten
# ─────────────────────────────────────────────────────────────────────

# Session-Limits — werden dynamisch angepasst basierend auf Workload
DEFAULT_MAX_SESSIONS_PER_DAY = 1

# Session-Modi mit unterschiedlicher Intensitaet
# Format: (timeout_seconds, max_turns, beschreibung)
SESSION_MODES = {
    'full_scan':    (2700, 60, 'Voller Scan aller Bereiche'),
    'quick_scan':   (1200, 30, 'Schnellcheck der kritischsten Bereiche'),
    'fix_only':     (7200, 200, 'Nur Findings abarbeiten, kein Scan'),
    'maintenance':  (600, 15, 'Nur Verifikation + Maintenance, kein Scan/Fix'),
}

# Workload-Schwellenwerte fuer adaptive Steuerung
WORKLOAD_THRESHOLDS = {
    'heavy':   20,  # >=20 offene Findings → fix_only, bis zu 3 Sessions
    'normal':  5,   # 5-19 offene Findings → full_scan + fix, 1-2 Sessions
    'light':   1,   # 1-4 offene Findings  → quick_scan + fix, 1 Session
    'clean':   0,   # 0 offene Findings    → maintenance oder full_scan (selten)
}

# Timeout fuer Scan-Session (wird durch SESSION_MODES ueberschrieben)
SESSION_TIMEOUT = 2700

# Maximale Tool-Aufrufe Scan-Session (wird durch SESSION_MODES ueberschrieben)
SESSION_MAX_TURNS = 60

# Timeout fuer User-Approval-Anfragen (5 Minuten)
APPROVAL_TIMEOUT = 300

# Intervall des Main-Loops (1 Minute)
MAIN_LOOP_INTERVAL = 60

# Heartbeat-Log alle N Loops (10 = alle 10 Minuten bei 60s Intervall)
HEARTBEAT_EVERY = 10

# Backoff bei konsekutiven Fehlern (grosszuegig um Token zu sparen)
# Nach 1. Fehler: 30 Min, nach 2.: 2 Stunden, nach 3.+: 6 Stunden
FAILURE_BACKOFF_SECONDS = [1800, 7200, 21600]

# Nach dieser Anzahl konsekutiver Fehler wird fuer den Tag deaktiviert
MAX_CONSECUTIVE_FAILURES = 3

# Projekt-zu-Repo Mapping fuer GitHub-Issues
# Keys werden als Substring-Match in affected_project gesucht
PROJECT_REPO_MAP = {
    'guildscout': 'Commandershadow9/GuildScout',
    'zerodox': 'Commandershadow9/ZERODOX',
    'shadowops': 'Commandershadow9/shadowops-bot',
}

# Fallback-Repo fuer Server-/Infrastruktur-Findings
DEFAULT_REPO = 'Commandershadow9/shadowops-bot'

# Services fuer Health-Checks
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

# Geschuetzte Port-Bindings — muessen auf 0.0.0.0 binden (Docker-Bridge Zugriff)
# Format: {port: beschreibung}
# Vorfall 2026-03-17: Analyst aenderte auf 127.0.0.1 → 11h Ausfall
PROTECTED_PORT_BINDINGS = {
    8766: 'Health/Changelog API (GuildScout+ZERODOX Proxy)',
    9090: 'GitHub Webhook (Traefik→Host)',
    9091: 'GuildScout Alerts (Docker→Host)',
}


class SecurityAnalyst:
    """Autonomer Security Analyst Agent

    Wartet bis der Server-Owner idle ist, startet dann eine
    Claude-Session die den Server frei analysieren darf.
    Dokumentiert Findings, fixt sichere Probleme automatisch
    und erstellt GitHub-Issues fuer Code-Probleme.
    """

    def __init__(self, bot, config, ai_engine, context_manager=None):
        """
        Args:
            bot: Discord Bot-Instanz
            config: ShadowOps Config-Objekt
            ai_engine: AIEngine-Instanz mit run_analyst_session()
            context_manager: Optional ContextManager mit Git-History + Code-Analyse
        """
        self.bot = bot
        self.config = config
        self.ai_engine = ai_engine
        self.context_manager = context_manager

        # Max Sessions aus Config oder Default
        analyst_cfg = config._config.get('security_analyst', {})
        self.max_sessions_per_day = analyst_cfg.get(
            'max_sessions_per_day', DEFAULT_MAX_SESSIONS_PER_DAY
        )

        # Modelle aus Config (Codex primaer, Claude Fallback)
        self.codex_model = analyst_cfg.get('model', 'gpt-5.3-codex')
        self.claude_model = analyst_cfg.get('fallback_model', 'claude-opus-4-6')

        # Datenbank-DSN aus Config oder Default
        dsn = analyst_cfg.get(
            'database_dsn',
            'postgresql://security_analyst:sec_analyst_2026@127.0.0.1:5433/security_analyst',
        )
        self.db = AnalystDB(dsn)
        self.activity_monitor = ActivityMonitor(bot)

        # State-Tracking
        self._task: Optional[asyncio.Task] = None
        self._current_session_id: Optional[int] = None
        self._sessions_today: int = 0
        self._today: date = date.today()
        self._running: bool = False
        self._briefing_pending: bool = False
        self._pending_result: Optional[Dict] = None

        # Mutex: Verhindert parallele Sessions (Main-Loop + manual_scan)
        self._session_lock = asyncio.Lock()

        # Failure-Tracking (verhindert Endlos-Retries)
        self._consecutive_failures: int = 0
        self._failure_cooldown_until: float = 0.0

    # ─────────────────────────────────────────────────────────────────
    # Knowledge-Sync: Git-Activity → DB
    # ─────────────────────────────────────────────────────────────────

    async def _sync_git_activity_to_db(self):
        """Synchronisiert Git-Aktivitaet aller Projekte in die Knowledge-DB.

        Wird VOR jeder Session aufgerufen. Schreibt kompakte Summaries
        in die knowledge-Tabelle (category: project_activity), damit der
        Analyst weiss welche Projekte sich seit dem letzten Scan geaendert haben.

        Daten kommen vom ContextManager (GitHistoryAnalyzer + CodeAnalyzer).
        """
        if not self.context_manager:
            return

        try:
            # Git-History neu laden (frische Daten)
            self.context_manager.reload_git_history()

            # Direkt auf die Analyzer zugreifen fuer volle Pattern-Daten
            for project_name, analyzer in self.context_manager.git_analyzers.items():
                try:
                    patterns = analyzer.analyze_patterns()
                except Exception:
                    continue

                total = patterns.get('total_commits', 0)
                fixes = patterns.get('total_fixes', 0)
                security = patterns.get('total_security', 0)

                # Hotspot-Files (Top 3)
                hotspots = patterns.get('frequently_changed_files', [])[:3]
                hotspot_text = ', '.join(
                    f"{f}({n}x)" for f, n in hotspots
                ) if hotspots else 'keine'

                # Letzte Security-Fixes
                sec_fixes = patterns.get('recent_security_fixes', [])[:3]
                sec_text = '; '.join(
                    f"{s['date']}: {s['subject'][:60]}" for s in sec_fixes
                ) if sec_fixes else 'keine'

                content = (
                    f"{total} Commits (30 Tage), {fixes} Fixes, {security} Security-Commits. "
                    f"Hotspot-Dateien: {hotspot_text}. "
                    f"Letzte Security-Fixes: {sec_text}."
                )

                # Confidence: Hoeher bei mehr Aktivitaet (aktive Projekte = wichtiger)
                confidence = min(0.95, 0.5 + (total / 200))

                await self.db.upsert_knowledge(
                    category='project_activity',
                    subject=f'{project_name}_git_activity',
                    content=content,
                    confidence=confidence,
                )

            # Code-Analyse Stats (Groesse der Projekte)
            code_stats = self.context_manager.get_code_analysis_stats()
            if code_stats.get('enabled'):
                for project_name, stats in code_stats.get('projects', {}).items():
                    if 'error' in stats:
                        continue
                    files = stats.get('total_files', 0)
                    loc = stats.get('total_lines', 0)
                    await self.db.upsert_knowledge(
                        category='project_activity',
                        subject=f'{project_name}_codebase_size',
                        content=f"{files} Dateien, {loc} LOC",
                        confidence=0.9,
                    )

            logger.info("Git-Activity in Knowledge-DB synchronisiert")

        except Exception as e:
            logger.warning("Git-Activity-Sync fehlgeschlagen: %s", e)

    # ─────────────────────────────────────────────────────────────────
    # Selbstkontrolle: Fix-Verifikation + Quality + Coverage + Decay
    # ─────────────────────────────────────────────────────────────────

    async def _verify_recent_fixes(self):
        """Prueft ob kuerzlich gemachte Fixes noch aktiv sind.

        Wird VOR jeder Session aufgerufen. Holt bis zu 10 unverified
        Fixes der letzten 14 Tage und fuehrt einfache Checks durch.
        Regressierte Fixes → Finding re-open + Discord-Alert.
        """
        try:
            unverified = await self.db.get_unverified_fixes(days=14, limit=10)
            if not unverified:
                return

            logger.info("Verifiziere %d kuerzliche Fixes...", len(unverified))
            regressions = 0

            for fix in unverified:
                still_valid = True
                check_method = 'existence_check'
                regression_details = None

                # Pruefen ob das Finding noch relevant ist
                # Strategie: Datei-Existenz + Service-Status fuer betroffene Bereiche
                category = fix.get('category', '')
                project = fix.get('affected_project', '')
                files = fix.get('affected_files') or []

                try:
                    if category in ('firewall', 'network'):
                        # UFW-Regel pruefen
                        proc = await asyncio.create_subprocess_exec(
                            'sudo', 'ufw', 'status',
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                        )
                        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
                        check_method = 'ufw status'
                        # Einfacher Check: UFW muss aktiv sein
                        if b'Status: active' not in stdout:
                            still_valid = False
                            regression_details = 'UFW ist nicht mehr aktiv'

                    elif category == 'docker':
                        # Docker-Container muessen laufen
                        proc = await asyncio.create_subprocess_exec(
                            'docker', 'ps', '--format', '{{.Names}}:{{.Status}}',
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                        )
                        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
                        check_method = 'docker ps'

                    elif category == 'permissions' and files:
                        # Datei-Berechtigungen pruefen
                        import os as _os
                        for f in files[:3]:
                            if _os.path.exists(f):
                                mode = oct(_os.stat(f).st_mode)[-3:]
                                # Warnung wenn Datei world-readable geworden ist
                                if mode.endswith('4') or mode.endswith('6'):
                                    if any(s in f for s in ['.env', 'credential', 'secret', 'key']):
                                        still_valid = False
                                        regression_details = f'{f} ist world-readable ({mode})'
                                        break
                        check_method = 'file permission check'

                    elif category in ('config', 'ssh'):
                        # SSH-Service muss laufen
                        proc = await asyncio.create_subprocess_exec(
                            'systemctl', 'is-active', 'sshd',
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                        )
                        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
                        check_method = 'systemctl is-active sshd'
                        if b'active' not in stdout:
                            still_valid = False
                            regression_details = 'sshd ist nicht aktiv'

                    else:
                        # Generischer Check: Finding-Dateien existieren noch
                        check_method = 'file_existence'

                except asyncio.TimeoutError:
                    check_method = 'timeout'
                    logger.debug("Verifikation Timeout fuer Fix #%d", fix['id'])
                    continue
                except Exception as e:
                    logger.debug("Verifikation Fehler fuer Fix #%d: %s", fix['id'], e)
                    continue

                # Ergebnis speichern
                session_id = self._current_session_id or 0
                await self.db.record_verification(
                    fix_attempt_id=fix['id'],
                    session_id=session_id,
                    still_valid=still_valid,
                    check_method=check_method,
                    regression_details=regression_details,
                )

                if not still_valid:
                    regressions += 1
                    # Finding re-open
                    await self.db.reopen_finding(
                        fix['finding_id'],
                        f"Regression erkannt: {regression_details}",
                    )
                    logger.warning(
                        "REGRESSION: Fix #%d fuer Finding #%d (%s) haelt nicht mehr: %s",
                        fix['id'], fix['finding_id'], fix.get('finding_title', '?'),
                        regression_details,
                    )

            if regressions > 0:
                await self._notify_regressions(regressions)
                # Pattern lernen
                await self.db.add_pattern(
                    pattern_type='fix_regression',
                    description=f'{regressions} Fix(es) regressiert bei Verifikation',
                    example=f'Session am {datetime.now(timezone.utc).date()}',
                )

            logger.info(
                "Fix-Verifikation: %d geprueft, %d Regressionen",
                len(unverified), regressions,
            )

        except Exception as e:
            logger.warning("Fix-Verifikation fehlgeschlagen: %s", e)

    async def _notify_regressions(self, count: int):
        """Discord-Alert bei Fix-Regressionen."""
        try:
            channel_id = self.config.critical_channel if hasattr(self.config, 'critical_channel') else None
            if not channel_id:
                channel_id = self.bot.config.get_channel_for_alert('analyst') if self.bot else None
            if not channel_id or not self.bot:
                return
            channel = self.bot.get_channel(int(channel_id))
            if channel:
                embed = discord.Embed(
                    title=f"⚠️ {count} Fix-Regression(en) erkannt",
                    description=(
                        f"Bei der Verifikation wurden {count} Fixes gefunden die nicht mehr greifen.\n"
                        f"Betroffene Findings wurden re-opened und werden in der naechsten Fix-Session bearbeitet."
                    ),
                    color=0xe67e22,
                    timestamp=datetime.now(timezone.utc),
                )
                await channel.send(embed=embed)
        except Exception:
            pass

    async def _pre_session_maintenance(self):
        """Alle Pre-Session Tasks ausfuehren.

        Buendelt alle Wartungsaufgaben die vor jeder Session laufen:
        1. Git-Activity in DB synchronisieren
        2. Kuerzliche Fixes verifizieren
        3. Knowledge-Confidence abschwaechen
        """
        await self._sync_git_activity_to_db()
        await self._verify_recent_fixes()
        await self.db.decay_knowledge_confidence()

    # ─────────────────────────────────────────────────────────────────
    # Lifecycle
    # ─────────────────────────────────────────────────────────────────

    async def start(self):
        """Analyst starten — DB verbinden und Main-Loop starten"""
        if self._running:
            logger.warning("SecurityAnalyst laeuft bereits")
            return

        await self.db.connect()

        # Session-Counter aus DB laden (ueberlebt Bot-Restarts)
        self._sessions_today = await self.db.count_sessions_today()
        if self._sessions_today > 0:
            logger.info(
                "Session-Counter aus DB geladen: %d Sessions heute bereits gelaufen",
                self._sessions_today,
            )

        self._running = True
        self._task = asyncio.create_task(self._main_loop())
        logger.info("SecurityAnalyst gestartet")

    async def stop(self):
        """Analyst stoppen — Loop beenden und DB schliessen"""
        if not self._running:
            return

        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        await self.db.close()
        logger.info("SecurityAnalyst gestoppt")

    # ─────────────────────────────────────────────────────────────────
    # Main Loop
    # ─────────────────────────────────────────────────────────────────

    async def _main_loop(self):
        """Hauptschleife — prueft periodisch ob eine Session moeglich ist"""
        # 30s Startup-Delay (Bot muss erst vollstaendig verbunden sein)
        await asyncio.sleep(30)
        logger.info("SecurityAnalyst Main-Loop aktiv")

        loop_count = 0

        while self._running:
            try:
                loop_count += 1

                # Tages-Reset: Zaehler + Failure-State zuruecksetzen
                today = date.today()
                if today != self._today:
                    self._today = today
                    self._sessions_today = await self.db.count_sessions_today()
                    self._consecutive_failures = 0
                    self._failure_cooldown_until = 0.0
                    logger.info(
                        "Neuer Tag — Session-Zaehler aus DB: %d, Fehler-Counter zurueckgesetzt",
                        self._sessions_today,
                    )

                # Pending Briefing senden wenn User auf Discord erreichbar
                if self._briefing_pending and self._pending_result:
                    discord_status = await self.activity_monitor.is_user_on_discord()
                    if discord_status in ('online', 'idle'):
                        await self._post_briefing(self._pending_result)
                        self._briefing_pending = False
                        self._pending_result = None
                        logger.info("Pending Briefing gesendet (User ist %s)", discord_status)

                # Activity-Check
                user_active = await self.activity_monitor.is_user_active()

                # Heartbeat alle 10 Minuten auf DEBUG-Level
                if loop_count % HEARTBEAT_EVERY == 0:
                    cooldown_remaining = max(0, self._failure_cooldown_until - time.time())
                    logger.debug(
                        "Heartbeat: user_active=%s, sessions_today=%d/%d, "
                        "consecutive_failures=%d, cooldown=%.0fs, briefing_pending=%s",
                        user_active, self._sessions_today, self.max_sessions_per_day,
                        self._consecutive_failures, cooldown_remaining,
                        self._briefing_pending,
                    )

                # Failure-Cooldown pruefen
                if self._failure_cooldown_until > time.time():
                    await asyncio.sleep(MAIN_LOOP_INTERVAL)
                    continue

                # Session starten wenn: User idle + Limit nicht erreicht + keine laufende Session
                # Dynamisches Limit: Workload bestimmt max_sessions
                if (
                    not user_active
                    and self._current_session_id is None
                ):
                    # Dynamisches Session-Limit berechnen
                    try:
                        fixable = await self.db.get_fixable_findings()
                        plan = self._plan_session(len(fixable))
                        effective_limit = min(
                            plan['max_sessions'],
                            self.max_sessions_per_day + plan['max_sessions'] - 1,
                        )
                    except Exception:
                        effective_limit = self.max_sessions_per_day

                    if self._sessions_today < effective_limit:
                        logger.info(
                            "User idle, Sessions %d/%d — starte Analyse",
                            self._sessions_today, effective_limit,
                        )
                        await self._run_session()

            except asyncio.CancelledError:
                logger.info("Main-Loop abgebrochen")
                return
            except Exception as e:
                logger.error("Main-Loop Fehler: %s", e, exc_info=True)
                # Bei Fehler laenger warten um Spam zu vermeiden
                await asyncio.sleep(300)
                continue

            await asyncio.sleep(MAIN_LOOP_INTERVAL)

    # ─────────────────────────────────────────────────────────────────
    # Session-Ausfuehrung
    # ─────────────────────────────────────────────────────────────────

    async def _run_session(self):
        """Fuehrt eine komplette Analyse-Session durch.

        Fehlgeschlagene Sessions zaehlen GEGEN das Tages-Limit und
        loesen einen exponentiellen Backoff aus, um Token-Verschwendung
        bei wiederholten Fehlern zu verhindern.

        Jeder Session-Start und jedes Ergebnis wird via Discord gemeldet.
        Nutzt _session_lock um parallele Sessions zu verhindern.
        """
        if self._session_lock.locked():
            logger.debug("Session-Lock aktiv — ueberspringe")
            return

        async with self._session_lock:
            await self._run_session_inner(trigger_type='idle_detected')

    def _plan_session(self, open_count: int) -> dict:
        """Intelligenter Session-Planner — entscheidet Modus und Intensitaet.

        Denkt wie ein Agent: Viel Arbeit → fokussiert fixen.
        Wenig Arbeit → kurz scannen. Nichts offen → nur Maintenance.

        Args:
            open_count: Anzahl offener Findings

        Returns:
            Dict mit mode, max_sessions, scan, fix, reason
        """
        if open_count >= WORKLOAD_THRESHOLDS['heavy']:
            return {
                'mode': 'fix_only',
                'max_sessions': 3,
                'scan': False,
                'fix': True,
                'reason': f'{open_count} offene Findings — fokussiert fixen',
            }
        elif open_count >= WORKLOAD_THRESHOLDS['normal']:
            return {
                'mode': 'full_scan',
                'max_sessions': 2,
                'scan': True,
                'fix': True,
                'reason': f'{open_count} offene Findings — Scan + Fix',
            }
        elif open_count >= WORKLOAD_THRESHOLDS['light']:
            return {
                'mode': 'quick_scan',
                'max_sessions': 1,
                'scan': True,
                'fix': True,
                'reason': f'{open_count} offene Findings — Quickcheck + Fix',
            }
        else:
            # 0 Findings — Maintenance oder seltener Full-Scan
            # Full-Scan nur wenn letzte Session >3 Tage her
            return {
                'mode': 'maintenance',
                'max_sessions': 1,
                'scan': False,
                'fix': False,
                'reason': 'Alles clean — nur Maintenance',
            }

    async def _run_session_inner(self, trigger_type: str = 'idle_detected'):
        """Interne Session-Logik — plant und fuehrt die optimale Session durch."""
        session_id = None
        try:
            # ── Schritt 0: Pre-Session Maintenance ──
            await self._pre_session_maintenance()

            # ── Schritt 1: Backlog checken + Session planen ──
            fixable = await self.db.get_fixable_findings()
            open_count = len(fixable)
            plan = self._plan_session(open_count)

            # Dynamisches Session-Limit anpassen
            effective_max = min(plan['max_sessions'], self.max_sessions_per_day + plan['max_sessions'] - 1)
            mode_config = SESSION_MODES[plan['mode']]

            logger.info(
                "Session-Plan: %s (Backlog: %d, Max-Sessions: %d, Reason: %s)",
                plan['mode'], open_count, effective_max, plan['reason'],
            )

            # ── Schritt 2: Maintenance-Modus — kein Scan, kein Fix ──
            if plan['mode'] == 'maintenance':
                # Pruefen ob ein Full-Scan ueberfaellig ist
                last_session = await self.db.get_last_session()
                days_since_scan = 99
                if last_session and last_session.get('ended_at'):
                    days_since_scan = (datetime.now(timezone.utc) - last_session['ended_at']).days

                if days_since_scan >= 3:
                    # Laenger als 3 Tage kein Scan → doch Full-Scan
                    plan['mode'] = 'full_scan'
                    plan['scan'] = True
                    plan['reason'] = f'Letzter Scan vor {days_since_scan} Tagen — ueberfaellig'
                    mode_config = SESSION_MODES['full_scan']
                    logger.info("Maintenance → Full-Scan hochgestuft (letzter Scan: %d Tage)", days_since_scan)
                else:
                    logger.info("Maintenance-Modus: Alles clean, letzter Scan vor %d Tagen — ueberspringe", days_since_scan)
                    return

            # ── Schritt 3: Fix-Only Modus ──
            if plan['mode'] == 'fix_only':
                session_id = await self.db.start_session(trigger_type='fix_backlog')
                self._current_session_id = session_id
                self._sessions_today += 1
                await self._notify_session_start(session_id, mode='fix')
                await self._run_fix_phase(session_id)

                await self.db.end_session(
                    session_id=session_id,
                    summary=f"Fix-Session ({plan['reason']}): {open_count} Findings bearbeitet",
                    topics=['backlog_fix'],
                    tokens_used=0,
                    model=self.claude_model,
                    findings_count=0,
                    auto_fixes=0,
                    issues_created=0,
                )
                self._consecutive_failures = 0
                return

            # ── Schritt 4: Scan (full oder quick) + optional Fix ──
            session_id = await self.db.start_session(trigger_type=trigger_type)
            self._current_session_id = session_id
            self._sessions_today += 1

            logger.info(
                "%s-Session startet (Timeout: %ds, Max-Turns: %d)",
                plan['mode'], mode_config[0], mode_config[1],
            )

            await self._notify_session_start(session_id, mode='scan')

            # Health-Snapshot VOR der Analyse
            health_before = await self._take_health_snapshot(session_id)

            # AI-Kontext aus DB zusammenstellen + offene Findings + Scan-Plan
            knowledge_context = await self.db.build_ai_context()
            open_findings = await self.db.get_open_findings_summary()
            open_findings_text = "\n".join(
                f"- [{f['severity'].upper()}] {f['title']}" for f in open_findings[:20]
            ) if open_findings else "(keine offenen Findings)"

            # Datengetriebenen Scan-Plan erstellen
            scan_plan = await self.db.build_scan_plan()

            context_section = ANALYST_CONTEXT_TEMPLATE.format(
                knowledge_context=knowledge_context,
                open_findings=open_findings_text,
                scan_plan=scan_plan,
            )
            prompt = ANALYST_SYSTEM_PROMPT + "\n\n" + context_section

            # Bei Quick-Scan: Fokus-Anweisung anhaengen
            if plan['mode'] == 'quick_scan':
                prompt += (
                    "\n\n## QUICK-SCAN MODUS\n"
                    "Du hast weniger Zeit als normal. Fokussiere dich auf:\n"
                    "1. Bereiche die sich seit dem letzten Scan geaendert haben (siehe project_activity)\n"
                    "2. Bereiche aus den Scan-Luecken (oben gelistet)\n"
                    "3. Critical/High Severity zuerst\n"
                    "Ueberspringe Low/Info und Bereiche die kuerzlich gecheckt wurden.\n"
                )

            # Nochmal pruefen ob User immer noch idle ist (nur bei auto-trigger)
            if trigger_type == 'idle_detected' and await self.activity_monitor.is_user_active():
                logger.info("User ist wieder aktiv — Session #%d abgebrochen", session_id)
                try:
                    await self.db.pause_session(session_id)
                except Exception as pause_err:
                    logger.warning("pause_session fehlgeschlagen: %s", pause_err)
                self._current_session_id = None
                self._sessions_today -= 1
                return

            # Scan-Session starten (Timeout/Turns aus Session-Plan)
            scan_timeout = mode_config[0]
            scan_max_turns = mode_config[1]
            logger.info(
                "Scan-Session wird gestartet (Codex: %s, Fallback: %s, Timeout: %ds, Turns: %d)",
                self.codex_model, self.claude_model, scan_timeout, scan_max_turns,
            )
            result = await self.ai_engine.run_analyst_session(
                prompt=prompt,
                timeout=scan_timeout,
                max_turns=scan_max_turns,
                codex_model=self.codex_model,
                claude_model=self.claude_model,
            )

            # Health-Snapshot NACH der Analyse
            health_after = await self._take_health_snapshot(session_id)

            # Health-Vergleich
            health_ok = self._compare_health(health_before, health_after)
            if not health_ok:
                await self._send_health_alert(health_before, health_after)

            # Ergebnisse verarbeiten
            if result:
                # Erfolg — Failure-Counter zuruecksetzen
                provider = result.pop('_provider', 'unknown')
                model_used = result.pop('_model', 'unknown')
                self._consecutive_failures = 0
                self._failure_cooldown_until = 0.0
                logger.info(
                    "Session #%d erfolgreich via %s/%s",
                    session_id, provider, model_used,
                )
                await self._process_results(session_id, result, health_ok, model_used)

                # Phase 2: Fix-Session — offene Findings abarbeiten
                await self._run_fix_phase(session_id)
            else:
                # Fehlgeschlagen — Backoff aktivieren + Discord-Alert
                self._consecutive_failures += 1
                self._apply_failure_backoff(session_id)
                await self._notify_session_failure(session_id)

                await self.db.end_session(
                    session_id=session_id,
                    summary=f"Session ohne Ergebnis (Fehler #{self._consecutive_failures})",
                    topics=[],
                    tokens_used=0,
                    model=self.codex_model,
                    findings_count=0,
                    auto_fixes=0,
                    issues_created=0,
                )

        except asyncio.CancelledError:
            if session_id:
                try:
                    await self.db.pause_session(session_id)
                except Exception:
                    pass
            raise
        except Exception as e:
            logger.error("Session-Fehler: %s", e, exc_info=True)
            # Fehler zaehlt gegen Limit + Backoff + Discord
            self._consecutive_failures += 1
            self._apply_failure_backoff(session_id)
            await self._notify_session_failure(session_id, error=str(e)[:200])

            if session_id:
                try:
                    await self.db.end_session(
                        session_id=session_id,
                        summary=f"Session mit Fehler beendet: {str(e)[:200]}",
                        topics=[],
                        tokens_used=0,
                        model=self.codex_model,
                        findings_count=0,
                        auto_fixes=0,
                        issues_created=0,
                    )
                except Exception:
                    logger.error("Konnte fehlerhafte Session nicht beenden", exc_info=True)
        finally:
            self._current_session_id = None

    def _apply_failure_backoff(self, session_id: Optional[int]):
        """Wendet exponentiellen Backoff bei Fehlern an.

        Bei >= MAX_CONSECUTIVE_FAILURES wird der Analyst fuer den Tag
        deaktiviert (sessions_today = max). Der Cooldown-Timer wird
        dann nicht gesetzt, da der Session-Counter bereits sperrt.
        """
        # Bei zu vielen Fehlern: Tages-Limit erreichen (kein Cooldown noetig)
        if self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            self._sessions_today = self.max_sessions_per_day
            self._failure_cooldown_until = 0.0  # Redundant, Counter sperrt
            logger.error(
                "Session #%s: %d konsekutive Fehler — Analyst fuer heute deaktiviert "
                "(sessions_today=%d/%d)",
                session_id, self._consecutive_failures,
                self._sessions_today, self.max_sessions_per_day,
            )
            return

        idx = min(self._consecutive_failures - 1, len(FAILURE_BACKOFF_SECONDS) - 1)
        backoff = FAILURE_BACKOFF_SECONDS[idx]
        self._failure_cooldown_until = time.time() + backoff

        logger.warning(
            "Session #%s fehlgeschlagen (Fehler #%d) — "
            "naechster Versuch in %d Minuten (sessions_today=%d/%d)",
            session_id, self._consecutive_failures,
            backoff // 60, self._sessions_today, self.max_sessions_per_day,
        )

    # ─────────────────────────────────────────────────────────────────
    # Discord-Benachrichtigungen (volle Transparenz)
    # ─────────────────────────────────────────────────────────────────

    def _get_briefing_channel(self) -> Optional[discord.TextChannel]:
        """Gibt den Discord-Channel fuer Analyst-Benachrichtigungen zurueck."""
        channel_id = (
            self.config.channels.get('security_briefing')
            or self.config.channels.get('ai_learning', 0)
        )
        if not channel_id:
            return None
        return self.bot.get_channel(int(channel_id))

    async def _notify_session_start(self, session_id: int, mode: str = 'scan'):
        """Meldet den Start einer Analyst-Session in Discord."""
        channel = self._get_briefing_channel()
        if not channel:
            logger.warning("Kein Briefing-Channel verfuegbar — Start-Notification uebersprungen")
            return

        today_str = date.today().strftime('%d.%m.%Y')
        if mode == 'fix':
            title = f"\U0001f527 Fix-Session #{session_id} gestartet"
            desc = (
                f"**Datum:** {today_str}\n"
                f"**Modus:** Backlog abarbeiten (Findings fixen + PRs)\n"
                f"**Engine:** `{self.claude_model}` (Claude direkt)\n"
                f"**Trigger:** Viele offene Findings"
            )
            color = discord.Color.orange()
        else:
            title = f"\U0001f50d Scan-Session #{session_id} gestartet"
            desc = (
                f"**Datum:** {today_str}\n"
                f"**Modus:** Server scannen + anschliessend Findings fixen\n"
                f"**Scan:** `{self.codex_model}` / `{self.claude_model}`\n"
                f"**Sessions heute:** {self._sessions_today}/{self.max_sessions_per_day}"
            )
            color = discord.Color.blue()

        embed = discord.Embed(
            title=title,
            description=desc,
            color=color,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text="SecurityAnalyst")

        try:
            await channel.send(embed=embed)
        except Exception as e:
            logger.error("Session-Start-Notification fehlgeschlagen: %s", e)

    async def _notify_session_failure(self, session_id: Optional[int], error: str = ""):
        """Meldet eine fehlgeschlagene Session in Discord (bei JEDEM Fehler)."""
        channel = self._get_briefing_channel()
        if not channel:
            return

        idx = min(self._consecutive_failures - 1, len(FAILURE_BACKOFF_SECONDS) - 1)
        next_backoff_min = FAILURE_BACKOFF_SECONDS[idx] // 60

        # Farbe: Orange bei einzelnem Fehler, Rot wenn deaktiviert
        is_disabled = self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES
        color = discord.Color.red() if is_disabled else discord.Color.orange()
        status = "FUR HEUTE DEAKTIVIERT" if is_disabled else f"Naechster Versuch in {next_backoff_min} Min"

        error_detail = f"\n**Fehler:** `{error}`" if error else ""

        embed = discord.Embed(
            title=f"\u274c Analyst Session #{session_id or '?'} fehlgeschlagen",
            description=(
                f"**Konsekutive Fehler:** {self._consecutive_failures}/{MAX_CONSECUTIVE_FAILURES}\n"
                f"**Sessions heute:** {self._sessions_today}/{self.max_sessions_per_day}\n"
                f"**Codex:** `{self.codex_model}` — kein Ergebnis\n"
                f"**Claude:** `{self.claude_model}` — kein Ergebnis\n"
                f"**Status:** {status}"
                f"{error_detail}\n\n"
                f"Logs: `sudo journalctl -u shadowops-bot --since '30m ago' | grep -i analyst`"
            ),
            color=color,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text="SecurityAnalyst Failure-Alert")

        try:
            await channel.send(embed=embed)
            logger.info("Session-Failure-Notification in Discord gesendet")
        except Exception as e:
            logger.error("Session-Failure-Notification fehlgeschlagen: %s", e)

    async def _run_fix_phase(self, scan_session_id: int):
        """Phase 2: Offene Findings aus DB holen und systematisch abarbeiten.

        Wird automatisch nach erfolgreicher Scan-Session aufgerufen.
        Startet eine Claude-Session die Findings fixt, PRs erstellt oder Issues anlegt.
        """
        from .prompts import FIX_SESSION_PROMPT

        try:
            # ALLE fixbaren Findings aus DB holen
            fixable = await self.db.get_fixable_findings()

            if not fixable:
                logger.info("Fix-Phase: Keine fixbaren Findings vorhanden")
                return

            logger.info("Fix-Phase: %d Findings zum Abarbeiten", len(fixable))

            # Findings-Liste fuer den Prompt formatieren — inkl. vorheriger Fix-Versuche
            findings_text = []
            for f in fixable:
                files = ', '.join(f.get('affected_files') or []) or '(keine Dateien)'
                issue_url = f.get('github_issue_url', '')
                issue_info = f" (Issue: {issue_url})" if issue_url else ''

                # Vorherige Fix-Versuche fuer dieses Finding laden
                prior_attempts = ''
                try:
                    attempts = await self.db.pool.fetch(
                        """SELECT approach, result, error_message, created_at
                           FROM fix_attempts
                           WHERE finding_id = $1
                           ORDER BY created_at DESC LIMIT 3""",
                        f['id'],
                    )
                    if attempts:
                        lines = []
                        for a in attempts:
                            icon = "✅" if a['result'] == 'success' else "❌"
                            err = f" — {a['error_message'][:80]}" if a['error_message'] else ""
                            lines.append(f"  {icon} {a['approach'][:100]}{err}")
                        prior_attempts = (
                            "\n**Vorherige Versuche (waehle einen ANDEREN Ansatz!):**\n"
                            + "\n".join(lines) + "\n"
                        )
                except Exception:
                    pass

                findings_text.append(
                    f"### Finding #{f['id']} [{f['severity'].upper()}] — {f['category']}\n"
                    f"**{f['title']}**\n"
                    f"{f['description']}\n"
                    f"Projekt: {f.get('affected_project', '?')} | Dateien: {files}{issue_info}"
                    f"{prior_attempts}\n"
                )

            # Knowledge-Kontext aus DB laden — Fix-AI braucht das gelernte Wissen
            knowledge_context = ''
            try:
                knowledge_context = await self.db.build_ai_context()
            except Exception:
                pass

            prompt = FIX_SESSION_PROMPT.format(
                findings_list='\n'.join(findings_text),
            )

            # Knowledge-Kontext NACH dem Fix-Prompt anhaengen (schlank)
            if knowledge_context:
                prompt += "\n\n## DEIN GELERNTES WISSEN\n\n" + knowledge_context

            # Discord-Notification: Fix-Phase startet
            await self._notify_fix_phase_start(len(fixable))

            # Fix-Session starten (Claude direkt, kein Codex)
            fix_result = await self.ai_engine.run_fix_session(
                prompt=prompt,
                timeout=7200,
                max_turns=200,
                model=self.claude_model,
            )

            if not fix_result:
                logger.warning("Fix-Phase: Kein Ergebnis von Claude")
                return

            # Ergebnisse verarbeiten — Findings in DB aktualisieren
            results = fix_result.get('results', [])
            fixed_count = 0
            pr_count = 0

            for r in results:
                raw_id = r.get('finding_id')
                action = r.get('action', 'fixed')
                details = r.get('details', '')

                # finding_id kann String sein (Claude gibt manchmal "15" statt 15)
                try:
                    finding_id = int(raw_id)
                except (ValueError, TypeError):
                    logger.debug("Finding-ID '%s' nicht numerisch — übersprungen", raw_id)
                    continue

                if action == 'fixed':
                    await self.db.mark_finding_fixed(finding_id)
                    # Fix-Versuch aufzeichnen (Learning Pipeline)
                    try:
                        await self.db.record_fix_attempt(
                            finding_id=finding_id,
                            session_id=scan_session_id,
                            approach=details[:200] if details else 'direct fix',
                            result='success',
                            commands_used=r.get('commands'),
                        )
                    except Exception as rec_err:
                        logger.debug("Fix-Attempt Recording fehlgeschlagen: %s", rec_err)
                    fixed_count += 1
                    logger.info("Finding #%d gefixt: %s", finding_id, details[:100])
                elif action == 'pr_created':
                    # PR-URL in Finding speichern + als gefixt markieren (PR = bearbeitet)
                    await self.db.mark_finding_fixed(finding_id)
                    try:
                        await self.db.pool.execute(
                            "UPDATE findings SET github_issue_url = $1 WHERE id = $2",
                            details, finding_id,
                        )
                    except Exception:
                        pass
                    # Fix-Versuch als PR aufzeichnen
                    try:
                        await self.db.record_fix_attempt(
                            finding_id=finding_id,
                            session_id=scan_session_id,
                            approach=f'PR erstellt: {details[:150]}',
                            result='success',
                        )
                    except Exception:
                        pass
                    pr_count += 1
                    logger.info("Finding #%d PR erstellt: %s", finding_id, details[:100])
                elif action == 'failed':
                    # Fehlgeschlagener Fix aufzeichnen (lernen!)
                    try:
                        await self.db.record_fix_attempt(
                            finding_id=finding_id,
                            session_id=scan_session_id,
                            approach=details[:200] if details else 'unknown',
                            result='failure',
                            error_message=r.get('error', ''),
                        )
                    except Exception:
                        pass
                    logger.warning("Finding #%d Fix fehlgeschlagen: %s", finding_id, details[:100])

            logger.info(
                "Fix-Phase abgeschlossen: %d direkt gefixt, %d PRs erstellt (von %d Findings)",
                fixed_count, pr_count, len(fixable),
            )

            # Discord-Notification: Ergebnisse
            await self._notify_fix_phase_complete(
                fixed_count, pr_count, len(fixable), fix_result.get('summary', ''),
            )

        except Exception as e:
            logger.error("Fix-Phase Fehler: %s", e, exc_info=True)

    async def _notify_fix_phase_start(self, count: int):
        """Discord-Notification: Fix-Phase startet"""
        try:
            channel_id = self.bot.config.get_channel_for_alert('analyst') if self.bot else None
            if not channel_id or not self.bot:
                return
            import discord
            embed = discord.Embed(
                title="🔧 Fix-Phase gestartet",
                description=f"Arbeite {count} offene Findings ab...",
                color=0x3498db,
            )
            channel = self.bot.get_channel(int(channel_id))
            if channel:
                await channel.send(embed=embed)
        except Exception:
            pass

    async def _notify_fix_phase_complete(self, fixed: int, prs: int, total: int, summary: str):
        """Discord-Notification: Fix-Phase Ergebnis"""
        try:
            channel_id = self.bot.config.get_channel_for_alert('analyst') if self.bot else None
            if not channel_id or not self.bot:
                return
            import discord
            done = fixed + prs
            color = 0x2ecc71 if done > 0 else 0xe74c3c
            embed = discord.Embed(
                title=f"✅ Fix-Phase: {done}/{total} Findings bearbeitet",
                description=summary[:500] if summary else "Keine Zusammenfassung",
                color=color,
            )
            embed.add_field(name="Direkt gefixt", value=str(fixed), inline=True)
            embed.add_field(name="PRs erstellt", value=str(prs), inline=True)
            embed.add_field(name="Gesamt", value=str(total), inline=True)
            channel = self.bot.get_channel(int(channel_id))
            if channel:
                await channel.send(embed=embed)
        except Exception:
            pass

    async def _process_results(self, session_id: int, result: Dict, health_ok: bool, model_used: str = 'unknown'):
        """Verarbeitet die AI-Ergebnisse und speichert sie in der DB

        Args:
            session_id: Aktuelle Session-ID
            result: Strukturiertes Ergebnis der AI-Session
            health_ok: Ob der Health-Check bestanden wurde
            model_used: Name des verwendeten AI-Modells
        """
        findings = result.get('findings', [])
        knowledge_updates = result.get('knowledge_updates', [])
        topics = result.get('topics_investigated', [])
        summary = result.get('summary', 'Keine Zusammenfassung')
        next_priority = result.get('next_priority', '')

        # Knowledge-Updates in DB speichern
        for ku in knowledge_updates:
            try:
                await self.db.upsert_knowledge(
                    category=ku.get('category', 'unknown'),
                    subject=ku.get('subject', 'unknown'),
                    content=ku.get('content', ''),
                    confidence=ku.get('confidence', 0.5),
                )
            except Exception as e:
                logger.error("Knowledge-Update fehlgeschlagen: %s", e)

        # Findings verarbeiten
        auto_fixes = 0
        issues_created = 0
        duplicates_skipped = 0

        for finding in findings:
            try:
                title = finding.get('title', 'Unbenannt')

                # Duplikat-Check: Gleicher Titel schon als offenes Finding?
                existing = await self.db.find_similar_open_finding(title)
                if existing:
                    logger.info(
                        "Finding-Duplikat übersprungen: '%s' (existiert als #%d)",
                        title[:50], existing['id'],
                    )
                    duplicates_skipped += 1
                    continue

                fix_type = finding.get('fix_type', 'info_only')
                # Analyst ist reine Analyse — auto_fixed kommt nicht mehr vor,
                # aber falls die KI es trotzdem schickt → als issue_needed behandeln
                if fix_type == 'auto_fixed':
                    fix_type = 'issue_needed'
                github_issue_url = None

                # Fix-Policy pro Projekt berücksichtigen
                affected_project = finding.get('affected_project', '')
                project_cfg = self.config.projects.get(affected_project, {}) if hasattr(self.config, 'projects') else {}
                fix_policy = project_cfg.get('fix_policy', 'all') if isinstance(project_cfg, dict) else 'all'

                if fix_policy == 'critical_only' and finding.get('severity') not in ('critical',):
                    # Aktives Projekt → nur Critical auto-fixen, Rest als Issue
                    if fix_type == 'auto_fixed':
                        fix_type = 'issue_needed'
                        logger.info("Fix-Policy '%s' für %s: auto_fix → issue (severity: %s)",
                                    fix_policy, affected_project, finding.get('severity'))
                elif fix_policy == 'issues_only':
                    # Nur Issues, keine Auto-Fixes
                    if fix_type == 'auto_fixed':
                        fix_type = 'issue_needed'
                elif fix_policy == 'monitor_only':
                    # Nur dokumentieren
                    fix_type = 'info_only'

                # GitHub-Issue erstellen fuer Code-Findings und wichtige Entscheidungen
                should_create_issue = (
                    fix_type == 'issue_needed'
                    or (fix_type == 'needs_decision' and finding.get('severity') in ('critical', 'high', 'medium'))
                )
                if should_create_issue:
                    github_issue_url = await self._create_github_issue(finding)
                    if github_issue_url:
                        issues_created += 1

                # Finding in DB speichern
                finding_id = await self.db.add_finding(
                    severity=finding.get('severity', 'info'),
                    category=finding.get('category', 'unknown'),
                    title=finding.get('title', 'Unbenannt'),
                    description=finding.get('description', ''),
                    session_id=session_id,
                    affected_project=finding.get('affected_project'),
                    affected_files=finding.get('affected_files'),
                    fix_type=fix_type,
                    github_issue_url=github_issue_url,
                    auto_fix_details=finding.get('auto_fix_details'),
                    rollback_command=finding.get('rollback_command'),
                )

                # Auto-fixierte Findings direkt als behoben markieren
                if fix_type == 'auto_fixed':
                    await self.db.mark_finding_fixed(finding_id)
                    auto_fixes += 1

            except Exception as e:
                logger.error("Finding-Verarbeitung fehlgeschlagen: %s", e)

        # ── Finding-Quality bewerten (Learning Pipeline) ──
        finding_assessments = result.get('finding_assessments', [])
        for fa in finding_assessments:
            try:
                fid = int(fa.get('finding_id', 0))
                if fid > 0:
                    await self.db.assess_finding_quality(
                        finding_id=fid,
                        is_actionable=fa.get('is_actionable', True),
                        is_false_positive=fa.get('is_false_positive', False),
                        false_positive_reason=fa.get('false_positive_reason'),
                        discovery_method=fa.get('discovery_method'),
                        confidence_score=fa.get('confidence', 0.5),
                    )
            except Exception as qa_err:
                logger.debug("Finding-Quality-Assessment fehlgeschlagen: %s", qa_err)

        # ── Scan-Coverage aufzeichnen (Learning Pipeline) ──
        areas_checked = result.get('areas_checked', [])
        areas_deferred = result.get('areas_deferred', [])
        if areas_checked or areas_deferred:
            coverage_data = []
            for area in areas_checked:
                coverage_data.append({'area': area, 'checked': True, 'depth': 'basic'})
            for area in areas_deferred:
                coverage_data.append({'area': area, 'checked': False, 'depth': 'skipped'})
            try:
                await self.db.record_scan_coverage(session_id, coverage_data)
            except Exception as cov_err:
                logger.debug("Scan-Coverage Recording fehlgeschlagen: %s", cov_err)

        # Session in DB abschliessen
        await self.db.end_session(
            session_id=session_id,
            summary=summary,
            topics=topics,
            tokens_used=0,  # Token-Zaehlung kommt spaeter
            model=model_used,
            findings_count=len(findings),
            auto_fixes=auto_fixes,
            issues_created=issues_created,
        )

        # Auto-Close: Veraltete Findings schliessen (>30 Tage offen, kein Issue)
        try:
            stale_closed = await self.db.close_stale_findings(days=30)
        except Exception:
            stale_closed = 0

        logger.info(
            "Session #%d abgeschlossen: %d Findings, %d Auto-Fixes, %d Issues, "
            "%d Duplikate übersprungen, %d veraltete Findings geschlossen",
            session_id, len(findings), auto_fixes, issues_created,
            duplicates_skipped, stale_closed,
        )

        # Briefing erstellen
        briefing_result = {
            'summary': summary,
            'topics': topics,
            'findings': findings,
            'auto_fixes': auto_fixes,
            'issues_created': issues_created,
            'health_ok': health_ok,
            'next_priority': next_priority,
        }

        # Briefing senden oder als pending markieren
        discord_status = await self.activity_monitor.is_user_on_discord()
        if discord_status in ('online', 'idle'):
            await self._post_briefing(briefing_result)
        else:
            self._briefing_pending = True
            self._pending_result = briefing_result
            logger.info("User offline — Briefing wird spaeter gesendet")

    # ─────────────────────────────────────────────────────────────────
    # Health-Monitoring
    # ─────────────────────────────────────────────────────────────────

    async def _take_health_snapshot(self, session_id: int) -> Dict:
        """Erstellt einen Health-Snapshot aller Services und Ressourcen

        Args:
            session_id: Zugehoerige Session-ID

        Returns:
            Dict mit containers, services, resources
        """
        containers = {}
        services = {}
        resources = {}

        # Docker-Container Status
        try:
            proc = await asyncio.create_subprocess_exec(
                'docker', 'ps', '--format', '{{.Names}}:{{.Status}}',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            for line in stdout.decode().strip().split('\n'):
                if ':' in line:
                    name, status = line.split(':', 1)
                    containers[name.strip()] = status.strip()
        except Exception as e:
            logger.warning("Docker-Status konnte nicht abgefragt werden: %s", e)

        # User-Services (systemctl --user)
        # XDG_RUNTIME_DIR nötig weil Bot als System-Service läuft
        import os as _os
        user_env = {**_os.environ, 'XDG_RUNTIME_DIR': '/run/user/1000'}
        for svc in USER_SERVICES:
            try:
                proc = await asyncio.create_subprocess_exec(
                    'systemctl', '--user', 'is-active', svc,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=user_env,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
                services[svc] = stdout.decode().strip()
            except Exception:
                services[svc] = 'unknown'

        # System-Services (systemctl ohne --user)
        for svc in SYSTEM_SERVICES:
            try:
                proc = await asyncio.create_subprocess_exec(
                    'systemctl', 'is-active', svc,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
                services[svc] = stdout.decode().strip()
            except Exception:
                services[svc] = 'unknown'

        # Festplattennutzung
        try:
            proc = await asyncio.create_subprocess_exec(
                'df', '-h', '--output=target,pcent', '/',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            resources['disk'] = stdout.decode().strip()
        except Exception:
            resources['disk'] = 'unknown'

        # Arbeitsspeicher
        try:
            proc = await asyncio.create_subprocess_exec(
                'free', '-h',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            resources['memory'] = stdout.decode().strip()
        except Exception:
            resources['memory'] = 'unknown'

        # Port-Bindings pruefen (geschuetzte Ports)
        port_bindings = {}
        try:
            proc = await asyncio.create_subprocess_exec(
                'ss', '-tlnp',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            for line in stdout.decode().strip().split('\n'):
                for port in PROTECTED_PORT_BINDINGS:
                    if f':{port}' in line:
                        # Bind-Adresse extrahieren (z.B. "0.0.0.0:8766" oder "127.0.0.1:8766")
                        parts = line.split()
                        for part in parts:
                            if f':{port}' in part:
                                bind_addr = part.rsplit(':', 1)[0]
                                port_bindings[str(port)] = bind_addr
                                break
        except Exception as e:
            logger.warning("Port-Binding-Check fehlgeschlagen: %s", e)

        # In DB speichern
        await self.db.save_health_snapshot(
            session_id=session_id,
            services=services,
            containers=containers,
            resources=resources,
        )

        return {
            'containers': containers,
            'services': services,
            'resources': resources,
            'port_bindings': port_bindings,
        }

    def _compare_health(self, before: Dict, after: Dict) -> bool:
        """Vergleicht zwei Health-Snapshots auf Regressionen

        Args:
            before: Snapshot vor der Analyse
            after: Snapshot nach der Analyse

        Returns:
            True wenn alles OK, False wenn Services/Container ausgefallen sind
        """
        all_ok = True

        # Container pruefen: Alles was vorher UP war muss noch UP sein
        before_containers = before.get('containers', {})
        after_containers = after.get('containers', {})

        for name, status_before in before_containers.items():
            if 'up' in status_before.lower():
                status_after = after_containers.get(name, 'MISSING')
                if 'up' not in status_after.lower():
                    logger.critical(
                        "HEALTH-REGRESSION: Container '%s' war UP, jetzt: %s",
                        name, status_after,
                    )
                    all_ok = False

        # Services pruefen: Alles was vorher active war muss noch active sein
        before_services = before.get('services', {})
        after_services = after.get('services', {})

        for name, status_before in before_services.items():
            if status_before == 'active':
                status_after = after_services.get(name, 'unknown')
                if status_after != 'active':
                    logger.critical(
                        "HEALTH-REGRESSION: Service '%s' war active, jetzt: %s",
                        name, status_after,
                    )
                    all_ok = False

        # Port-Bindings pruefen: Geschuetzte Ports muessen auf 0.0.0.0 bleiben
        after_bindings = after.get('port_bindings', {})
        before_bindings = before.get('port_bindings', {})
        for port_str, description in {str(p): d for p, d in PROTECTED_PORT_BINDINGS.items()}.items():
            bind_after = after_bindings.get(port_str, '')
            bind_before = before_bindings.get(port_str, '')
            if bind_before == '0.0.0.0' and bind_after != '0.0.0.0' and bind_after:
                logger.critical(
                    "PORT-BINDING-REGRESSION: Port %s (%s) war 0.0.0.0, jetzt: %s "
                    "— Docker-Container koennen Host nicht mehr erreichen!",
                    port_str, description, bind_after,
                )
                all_ok = False
            elif bind_after and bind_after != '0.0.0.0':
                logger.warning(
                    "Port %s (%s) bindet auf %s statt 0.0.0.0 — Docker-Zugriff moeglicherweise blockiert",
                    port_str, description, bind_after,
                )

        if all_ok:
            logger.info("Health-Check bestanden — alle Services stabil")
        else:
            logger.critical("Health-Check FEHLGESCHLAGEN — Regressionen erkannt!")

        return all_ok

    async def _send_health_alert(self, before: Dict, after: Dict):
        """Sendet eine kritische Discord-Warnung bei Health-Regressionen

        Args:
            before: Snapshot vor der Analyse
            after: Snapshot nach der Analyse
        """
        channel_id = self.config.critical_channel
        if not channel_id:
            logger.error("Kein Critical-Channel konfiguriert — Health-Alert kann nicht gesendet werden")
            return

        channel = self.bot.get_channel(channel_id)
        if not channel:
            logger.error("Critical-Channel %d nicht gefunden", channel_id)
            return

        # Aenderungen sammeln
        changes = []

        # Container-Ausfaelle
        before_containers = before.get('containers', {})
        after_containers = after.get('containers', {})
        for name, status_before in before_containers.items():
            if 'up' in status_before.lower():
                status_after = after_containers.get(name, 'MISSING')
                if 'up' not in status_after.lower():
                    changes.append(f"Container `{name}`: UP -> {status_after}")

        # Service-Ausfaelle
        before_services = before.get('services', {})
        after_services = after.get('services', {})
        for name, status_before in before_services.items():
            if status_before == 'active':
                status_after = after_services.get(name, 'unknown')
                if status_after != 'active':
                    changes.append(f"Service `{name}`: active -> {status_after}")

        embed = discord.Embed(
            title="CRITICAL: Health-Regression nach Analyst-Session",
            description="\n".join(changes) if changes else "Unbekannte Aenderungen erkannt",
            color=discord.Color.red(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(
            name="Aktion erforderlich",
            value="Bitte sofort pruefen! Der Analyst hat moeglicherweise einen Service beschaedigt.",
            inline=False,
        )
        embed.set_footer(text="SecurityAnalyst Health-Monitor")

        try:
            await channel.send(embed=embed)
            logger.info("Health-Alert in Channel %d gesendet", channel_id)
        except Exception as e:
            logger.error("Health-Alert konnte nicht gesendet werden: %s", e)

    # ─────────────────────────────────────────────────────────────────
    # Discord Briefing
    # ─────────────────────────────────────────────────────────────────

    async def _post_briefing(self, result: Dict):
        """Sendet ein Briefing-Embed mit den Session-Ergebnissen nach Discord

        Args:
            result: Zusammengefasste Ergebnisse der Session
        """
        # Channel bestimmen
        channel_id = (
            self.config.channels.get('security_briefing')
            or self.config.channels.get('ai_learning', 0)
        )
        if not channel_id:
            logger.warning("Kein Briefing-Channel konfiguriert")
            return

        channel = self.bot.get_channel(int(channel_id))
        if not channel:
            logger.error("Briefing-Channel %s nicht gefunden", channel_id)
            return

        # Farbe basierend auf Ergebnis
        health_ok = result.get('health_ok', True)
        findings = result.get('findings', [])
        has_critical = any(f.get('severity') in ('critical', 'high') for f in findings)

        if not health_ok:
            color = discord.Color.red()
            status_emoji = "\u274c"  # Rotes X
        elif has_critical or findings:
            color = discord.Color.orange()
            status_emoji = "\u26a0\ufe0f"  # Warnung
        else:
            color = discord.Color.green()
            status_emoji = "\u2705"  # Gruener Haken

        today_str = date.today().strftime('%d.%m.%Y')
        embed = discord.Embed(
            title=f"{status_emoji} Security Briefing — {today_str}",
            description=result.get('summary', 'Keine Zusammenfassung verfuegbar'),
            color=color,
            timestamp=datetime.now(timezone.utc),
        )

        # Untersuchte Themen
        topics = result.get('topics', [])
        if topics:
            topics_text = "\n".join(f"- {t}" for t in topics)
            embed.add_field(
                name="Untersuchte Themen",
                value=topics_text[:1024],
                inline=False,
            )

        # Auto-Fixes
        auto_fixes = result.get('auto_fixes', 0)
        if auto_fixes > 0:
            auto_fix_findings = [f for f in findings if f.get('fix_type') == 'auto_fixed']
            fixes_text = "\n".join(
                f"\u2705 {f.get('title', 'Unbenannt')}"
                for f in auto_fix_findings
            )
            embed.add_field(
                name=f"Auto-Fixes ({auto_fixes})",
                value=fixes_text[:1024] if fixes_text else "Keine Details",
                inline=False,
            )

        # Findings die Entscheidung brauchen
        decision_findings = [
            f for f in findings
            if f.get('fix_type') in ('needs_decision', 'issue_needed')
        ]
        if decision_findings:
            severity_emoji_map = {
                'critical': '\U0001f534',  # Roter Kreis
                'high': '\U0001f7e0',      # Orangener Kreis
                'medium': '\U0001f7e1',    # Gelber Kreis
                'low': '\U0001f535',        # Blauer Kreis
                'info': '\u26aa',           # Weisser Kreis
            }
            decision_text = "\n".join(
                f"{severity_emoji_map.get(f.get('severity', 'info'), '\u26aa')} "
                f"**{f.get('severity', 'info').upper()}**: {f.get('title', 'Unbenannt')}"
                for f in decision_findings
            )
            embed.add_field(
                name=f"Erfordert Entscheidung ({len(decision_findings)})",
                value=decision_text[:1024],
                inline=False,
            )

        # Naechste Prioritaet
        next_priority = result.get('next_priority', '')
        if next_priority:
            embed.add_field(
                name="Naechste Prioritaet",
                value=next_priority[:1024],
                inline=False,
            )

        # Footer mit Statistiken
        issues_created = result.get('issues_created', 0)
        health_status = "OK" if health_ok else "FEHLGESCHLAGEN"
        footer_text = (
            f"{len(findings)} Findings | {auto_fixes} Fixes | "
            f"{issues_created} Issues | Health: {health_status}"
        )
        embed.set_footer(text=footer_text)

        try:
            await channel.send(embed=embed)
            logger.info("Briefing in Channel %s gesendet", channel_id)
        except Exception as e:
            logger.error("Briefing konnte nicht gesendet werden: %s", e)

    # ─────────────────────────────────────────────────────────────────
    # GitHub Issues
    # ─────────────────────────────────────────────────────────────────

    def _resolve_repo(self, affected_project: str) -> str:
        """Bestimmt das GitHub-Repo anhand des affected_project Strings.

        Nutzt Substring-Matching: "GuildScout / Security Analyst" matcht "guildscout".
        Bei mehreren Matches gewinnt der erste. Server/Infra-Findings landen
        im DEFAULT_REPO (shadowops-bot).

        Args:
            affected_project: Freitext-Projekt-String aus dem Finding

        Returns:
            GitHub Repo im Format "Owner/Repo"
        """
        project_lower = affected_project.lower()
        for key, repo in PROJECT_REPO_MAP.items():
            if key in project_lower:
                return repo
        return DEFAULT_REPO

    async def _create_github_issue(self, finding: Dict) -> Optional[str]:
        """Erstellt ein GitHub-Issue fuer ein Code-Finding

        Args:
            finding: Finding-Dict mit issue_title, issue_body, affected_project

        Returns:
            Issue-URL oder None bei Fehler
        """
        affected_project = finding.get('affected_project', '').strip()
        repo = self._resolve_repo(affected_project)

        title = finding.get('issue_title', finding.get('title', 'Security Finding'))

        # Duplikaterkennung: Offenes Finding mit gleichem Titel in DB?
        existing = await self.db.find_similar_open_finding(finding.get('title', ''))
        if existing:
            existing_url = existing.get('github_issue_url', '')
            logger.info(
                "Duplikat erkannt: Finding '%s' existiert bereits (ID #%d, Issue: %s) — ueberspringe",
                existing['title'][:50], existing['id'], existing_url or 'kein Issue',
            )
            return existing_url or None

        logger.info("Issue-Routing: '%s' -> %s", affected_project, repo)
        body = finding.get('issue_body', finding.get('description', ''))
        severity = finding.get('severity', 'medium')

        # Issue-Titel mit Security-Prefix
        full_title = f"[Security] {title}"

        # Severity-Badge im Body (Labels koennten fehlen)
        body_with_badge = (
            f"**Severity:** {severity.upper()} | "
            f"**Projekt:** {affected_project or 'Server'}\n\n"
            f"{body}"
        )

        try:
            # Erst MIT Labels versuchen
            proc = await asyncio.create_subprocess_exec(
                'gh', 'issue', 'create',
                '--repo', repo,
                '--title', full_title,
                '--body', body_with_badge,
                '--label', f"security,priority:{severity}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)

            if proc.returncode == 0:
                issue_url = stdout.decode().strip()
                logger.info("GitHub-Issue erstellt: %s", issue_url)
                return issue_url

            # Label-Fehler? Retry ohne Labels
            error = stderr.decode().strip()
            if 'label' in error.lower():
                logger.warning("Labels nicht gefunden, erstelle Issue ohne Labels: %s", error[:150])
                proc2 = await asyncio.create_subprocess_exec(
                    'gh', 'issue', 'create',
                    '--repo', repo,
                    '--title', full_title,
                    '--body', body_with_badge,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout2, stderr2 = await asyncio.wait_for(proc2.communicate(), timeout=30)
                if proc2.returncode == 0:
                    issue_url = stdout2.decode().strip()
                    logger.info("GitHub-Issue erstellt (ohne Labels): %s", issue_url)
                    return issue_url
                error = stderr2.decode().strip()

            logger.error("GitHub-Issue Erstellung fehlgeschlagen: %s", error[:300])
            return None

        except asyncio.TimeoutError:
            logger.error("GitHub-Issue Erstellung: Timeout nach 30s")
            return None
        except Exception as e:
            logger.error("GitHub-Issue Erstellung fehlgeschlagen: %s", e)
            return None

    # ─────────────────────────────────────────────────────────────────
    # Manueller Scan
    # ─────────────────────────────────────────────────────────────────

    async def manual_scan(self, focus: Optional[str] = None) -> Optional[Dict]:
        """Fuehrt einen manuellen Security-Scan durch

        Args:
            focus: Optionaler Fokus-Bereich (z.B. "Docker", "SSL", "Permissions")

        Returns:
            Strukturiertes Ergebnis-Dict oder None bei Fehler
        """
        if self._session_lock.locked():
            logger.warning("Manueller Scan abgelehnt — automatische Session laeuft")
            return None

        async with self._session_lock:
            logger.info("Manueller Scan gestartet (Fokus: %s)", focus or "keiner")

            session_id = await self.db.start_session(trigger_type='manual')
            self._current_session_id = session_id

            # Discord: Manuellen Scan-Start melden
            channel = self._get_briefing_channel()
            if channel:
                embed = discord.Embed(
                    title=f"\U0001f50d Manueller Scan #{session_id} gestartet",
                    description=(
                        f"**Fokus:** {focus or 'Allgemein'}\n"
                        f"**Primaer:** `{self.codex_model}` (Codex)\n"
                        f"**Fallback:** `{self.claude_model}` (Claude)"
                    ),
                    color=discord.Color.blue(),
                    timestamp=datetime.now(timezone.utc),
                )
                embed.set_footer(text="SecurityAnalyst — Manuell")
                try:
                    await channel.send(embed=embed)
                except Exception:
                    pass

            try:
                # Health-Snapshot vorher
                health_before = await self._take_health_snapshot(session_id)

                # Prompt zusammenbauen
                if focus:
                    prompt = (
                        f"{ANALYST_SYSTEM_PROMPT}\n\n"
                        f"## SPEZIFISCHER FOKUS\n\n"
                        f"Der User hat einen gezielten Scan angefordert.\n"
                        f"Fokussiere dich auf: **{focus}**\n\n"
                        f"Untersuche diesen Bereich besonders gruendlich."
                    )
                else:
                    knowledge_context = await self.db.build_ai_context()
                    open_findings = await self.db.get_open_findings_summary()
                    open_findings_text = "\n".join(
                        f"- [{f['severity'].upper()}] {f['title']}" for f in open_findings[:20]
                    ) if open_findings else "(keine offenen Findings)"
                    scan_plan = await self.db.build_scan_plan()
                    context_section = ANALYST_CONTEXT_TEMPLATE.format(
                        knowledge_context=knowledge_context,
                        open_findings=open_findings_text,
                        scan_plan=scan_plan,
                    )
                    prompt = ANALYST_SYSTEM_PROMPT + "\n\n" + context_section

                # AI-Session (Codex primaer, Claude Fallback)
                result = await self.ai_engine.run_analyst_session(
                    prompt=prompt,
                    timeout=SESSION_TIMEOUT,
                    max_turns=SESSION_MAX_TURNS,
                    codex_model=self.codex_model,
                    claude_model=self.claude_model,
                )

                # Health-Snapshot nachher
                health_after = await self._take_health_snapshot(session_id)
                health_ok = self._compare_health(health_before, health_after)

                if not health_ok:
                    await self._send_health_alert(health_before, health_after)

                if result:
                    provider = result.pop('_provider', 'unknown')
                    model_used = result.pop('_model', 'unknown')
                    # Manueller Erfolg setzt auch Failure-Counter zurueck
                    self._consecutive_failures = 0
                    self._failure_cooldown_until = 0.0
                    await self._process_results(session_id, result, health_ok, model_used)
                else:
                    self._consecutive_failures += 1
                    self._apply_failure_backoff(session_id)
                    await self._notify_session_failure(session_id)
                    await self.db.end_session(
                        session_id=session_id,
                        summary="Manueller Scan ohne Ergebnis",
                        topics=[],
                        tokens_used=0,
                        model=self.codex_model,
                        findings_count=0,
                        auto_fixes=0,
                        issues_created=0,
                    )

                return result

            except Exception as e:
                logger.error("Manueller Scan fehlgeschlagen: %s", e, exc_info=True)
                self._consecutive_failures += 1
                self._apply_failure_backoff(session_id)
                await self._notify_session_failure(session_id, error=str(e)[:200])
                try:
                    await self.db.end_session(
                        session_id=session_id,
                        summary=f"Manueller Scan fehlgeschlagen: {str(e)[:200]}",
                        topics=[],
                        tokens_used=0,
                        model=self.codex_model,
                        findings_count=0,
                        auto_fixes=0,
                        issues_created=0,
                    )
                except Exception:
                    pass
                return None
            finally:
                self._current_session_id = None
