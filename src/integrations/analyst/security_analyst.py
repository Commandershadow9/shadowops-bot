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

# Maximale Anzahl Sessions pro Tag — Default, wird von Config ueberschrieben
DEFAULT_MAX_SESSIONS_PER_DAY = 3

# Timeout fuer eine einzelne Analyse-Session (30 Minuten)
SESSION_TIMEOUT = 1800

# Maximale Anzahl Tool-Aufrufe pro Session
SESSION_MAX_TURNS = 50

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


class SecurityAnalyst:
    """Autonomer Security Analyst Agent

    Wartet bis der Server-Owner idle ist, startet dann eine
    Claude-Session die den Server frei analysieren darf.
    Dokumentiert Findings, fixt sichere Probleme automatisch
    und erstellt GitHub-Issues fuer Code-Probleme.
    """

    def __init__(self, bot, config, ai_engine):
        """
        Args:
            bot: Discord Bot-Instanz
            config: ShadowOps Config-Objekt
            ai_engine: AIEngine-Instanz mit run_analyst_session()
        """
        self.bot = bot
        self.config = config
        self.ai_engine = ai_engine

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

                # Session starten wenn: User idle + Tages-Limit nicht erreicht + keine laufende Session
                if (
                    not user_active
                    and self._sessions_today < self.max_sessions_per_day
                    and self._current_session_id is None
                ):
                    logger.info("User ist idle und Session-Limit nicht erreicht — starte Analyse")
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

    async def _run_session_inner(self, trigger_type: str = 'idle_detected'):
        """Interne Session-Logik (wird unter Lock aufgerufen)."""
        session_id = None
        try:
            # Session in DB starten
            session_id = await self.db.start_session(trigger_type=trigger_type)
            self._current_session_id = session_id
            self._sessions_today += 1
            logger.info("Analyse-Session #%d gestartet", session_id)

            # Discord: Session-Start melden
            await self._notify_session_start(session_id)

            # Health-Snapshot VOR der Analyse
            health_before = await self._take_health_snapshot(session_id)

            # AI-Kontext aus DB zusammenstellen + offene Findings laden
            knowledge_context = await self.db.build_ai_context()
            open_findings = await self.db.get_open_findings_summary()
            open_findings_text = "\n".join(
                f"- [{f['severity'].upper()}] {f['title']}" for f in open_findings[:20]
            ) if open_findings else "(keine offenen Findings)"

            context_section = ANALYST_CONTEXT_TEMPLATE.format(
                knowledge_context=knowledge_context,
                open_findings=open_findings_text,
            )
            prompt = ANALYST_SYSTEM_PROMPT + "\n\n" + context_section

            # Nochmal pruefen ob User immer noch idle ist (nur bei auto-trigger)
            if trigger_type == 'idle_detected' and await self.activity_monitor.is_user_active():
                logger.info("User ist wieder aktiv — Session #%d abgebrochen", session_id)
                try:
                    await self.db.pause_session(session_id)
                except Exception as pause_err:
                    logger.warning("pause_session fehlgeschlagen: %s", pause_err)
                self._current_session_id = None
                self._sessions_today -= 1  # Abbruch durch User zaehlt nicht
                return

            # AI-Session starten (Codex primaer, Claude Fallback)
            logger.info(
                "AI-Session wird gestartet (Codex: %s, Fallback: %s, Timeout: %ds)",
                self.codex_model, self.claude_model, SESSION_TIMEOUT,
            )
            result = await self.ai_engine.run_analyst_session(
                prompt=prompt,
                timeout=SESSION_TIMEOUT,
                max_turns=SESSION_MAX_TURNS,
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

    async def _notify_session_start(self, session_id: int):
        """Meldet den Start einer Analyst-Session in Discord."""
        channel = self._get_briefing_channel()
        if not channel:
            logger.warning("Kein Briefing-Channel verfuegbar — Start-Notification uebersprungen")
            return

        today_str = date.today().strftime('%d.%m.%Y')
        embed = discord.Embed(
            title=f"\U0001f50d Analyst Session #{session_id} gestartet",
            description=(
                f"**Datum:** {today_str}\n"
                f"**Primaer:** `{self.codex_model}` (Codex)\n"
                f"**Fallback:** `{self.claude_model}` (Claude)\n"
                f"**Sessions heute:** {self._sessions_today}/{self.max_sessions_per_day}\n"
                f"**Trigger:** User idle"
            ),
            color=discord.Color.blue(),
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
                    context_section = ANALYST_CONTEXT_TEMPLATE.format(
                        knowledge_context=knowledge_context,
                        open_findings=open_findings_text,
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
