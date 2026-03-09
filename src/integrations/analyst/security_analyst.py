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

# Maximale Anzahl Sessions pro Tag (Token-Kosten begrenzen)
MAX_SESSIONS_PER_DAY = 1

# Timeout fuer eine einzelne Analyse-Session (30 Minuten)
SESSION_TIMEOUT = 1800

# Maximale Anzahl Tool-Aufrufe pro Session
SESSION_MAX_TURNS = 25

# Timeout fuer User-Approval-Anfragen (5 Minuten)
APPROVAL_TIMEOUT = 300

# Intervall des Main-Loops (1 Minute)
MAIN_LOOP_INTERVAL = 60

# Projekt-zu-Repo Mapping fuer GitHub-Issues
PROJECT_REPO_MAP = {
    'guildscout': 'Commandershadow9/GuildScout',
    'zerodox': 'Commandershadow9/ZERODOX',
    'shadowops': 'Commandershadow9/shadowops-bot',
    'shadowops-bot': 'Commandershadow9/shadowops-bot',
}

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

        # Datenbank-DSN aus Config oder Default
        dsn = config._config.get('security_analyst', {}).get(
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

    # ─────────────────────────────────────────────────────────────────
    # Lifecycle
    # ─────────────────────────────────────────────────────────────────

    async def start(self):
        """Analyst starten — DB verbinden und Main-Loop starten"""
        if self._running:
            logger.warning("SecurityAnalyst laeuft bereits")
            return

        await self.db.connect()
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

        while self._running:
            try:
                # Tages-Reset: Zaehler zuruecksetzen wenn neuer Tag
                today = date.today()
                if today != self._today:
                    self._today = today
                    self._sessions_today = 0
                    logger.debug("Neuer Tag — Session-Zaehler zurueckgesetzt")

                # Pending Briefing senden wenn User auf Discord erreichbar
                if self._briefing_pending and self._pending_result:
                    discord_status = await self.activity_monitor.is_user_on_discord()
                    if discord_status in ('online', 'idle'):
                        await self._post_briefing(self._pending_result)
                        self._briefing_pending = False
                        self._pending_result = None
                        logger.info("Pending Briefing gesendet (User ist %s)", discord_status)

                # Session starten wenn: User idle + Tages-Limit nicht erreicht + keine laufende Session
                if (
                    not await self.activity_monitor.is_user_active()
                    and self._sessions_today < MAX_SESSIONS_PER_DAY
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
        """Fuehrt eine komplette Analyse-Session durch"""
        session_id = None
        try:
            # Session in DB starten
            session_id = await self.db.start_session(trigger_type='idle_detected')
            self._current_session_id = session_id
            self._sessions_today += 1
            logger.info("Analyse-Session #%d gestartet", session_id)

            # Health-Snapshot VOR der Analyse
            health_before = await self._take_health_snapshot(session_id)

            # AI-Kontext aus DB zusammenstellen
            knowledge_context = await self.db.build_ai_context()
            context_section = ANALYST_CONTEXT_TEMPLATE.format(
                knowledge_context=knowledge_context,
            )
            prompt = ANALYST_SYSTEM_PROMPT + "\n\n" + context_section

            # Nochmal pruefen ob User immer noch idle ist
            if await self.activity_monitor.is_user_active():
                logger.info("User ist wieder aktiv — Session #%d abgebrochen", session_id)
                await self.db.pause_session(session_id)
                self._current_session_id = None
                self._sessions_today -= 1  # Zaehlt nicht als verbrauchte Session
                return

            # Claude-Session starten
            logger.info("Claude-Session wird gestartet (Timeout: %ds, Max-Turns: %d)",
                         SESSION_TIMEOUT, SESSION_MAX_TURNS)
            result = await self.ai_engine.run_analyst_session(
                prompt=prompt,
                timeout=SESSION_TIMEOUT,
                max_turns=SESSION_MAX_TURNS,
            )

            # Health-Snapshot NACH der Analyse
            health_after = await self._take_health_snapshot(session_id)

            # Health-Vergleich
            health_ok = self._compare_health(health_before, health_after)
            if not health_ok:
                await self._send_health_alert(health_before, health_after)

            # Ergebnisse verarbeiten
            if result:
                await self._process_results(session_id, result, health_ok)
            else:
                logger.warning("Session #%d: Kein Ergebnis von der AI erhalten", session_id)
                await self.db.end_session(
                    session_id=session_id,
                    summary="Session ohne Ergebnis beendet",
                    topics=[],
                    tokens_used=0,
                    model='claude-opus-4-6',
                    findings_count=0,
                    auto_fixes=0,
                    issues_created=0,
                )

        except asyncio.CancelledError:
            if session_id:
                await self.db.pause_session(session_id)
            raise
        except Exception as e:
            logger.error("Session-Fehler: %s", e, exc_info=True)
            if session_id:
                try:
                    await self.db.end_session(
                        session_id=session_id,
                        summary=f"Session mit Fehler beendet: {str(e)[:200]}",
                        topics=[],
                        tokens_used=0,
                        model='claude-opus-4-6',
                        findings_count=0,
                        auto_fixes=0,
                        issues_created=0,
                    )
                except Exception:
                    logger.error("Konnte fehlerhafte Session nicht beenden", exc_info=True)
        finally:
            self._current_session_id = None

    async def _process_results(self, session_id: int, result: Dict, health_ok: bool):
        """Verarbeitet die AI-Ergebnisse und speichert sie in der DB

        Args:
            session_id: Aktuelle Session-ID
            result: Strukturiertes Ergebnis der AI-Session
            health_ok: Ob der Health-Check bestanden wurde
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

        for finding in findings:
            try:
                fix_type = finding.get('fix_type', 'info_only')
                github_issue_url = None

                # GitHub-Issue erstellen fuer Code-Findings
                if fix_type == 'issue_needed':
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
            model='claude-opus-4-6',
            findings_count=len(findings),
            auto_fixes=auto_fixes,
            issues_created=issues_created,
        )

        logger.info(
            "Session #%d abgeschlossen: %d Findings, %d Auto-Fixes, %d Issues",
            session_id, len(findings), auto_fixes, issues_created,
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
        for svc in USER_SERVICES:
            try:
                proc = await asyncio.create_subprocess_exec(
                    'systemctl', '--user', 'is-active', svc,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
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

    async def _create_github_issue(self, finding: Dict) -> Optional[str]:
        """Erstellt ein GitHub-Issue fuer ein Code-Finding

        Args:
            finding: Finding-Dict mit issue_title, issue_body, affected_project

        Returns:
            Issue-URL oder None bei Fehler
        """
        project = finding.get('affected_project', '').lower().strip()
        repo = PROJECT_REPO_MAP.get(project)

        if not repo:
            logger.warning(
                "Kein Repo-Mapping fuer Projekt '%s' — Issue wird nicht erstellt",
                project,
            )
            return None

        title = finding.get('issue_title', finding.get('title', 'Security Finding'))
        body = finding.get('issue_body', finding.get('description', ''))
        severity = finding.get('severity', 'medium')

        # Issue-Titel mit Security-Prefix
        full_title = f"[Security] {title}"

        # Labels vorbereiten
        labels = f"security,priority:{severity}"

        try:
            proc = await asyncio.create_subprocess_exec(
                'gh', 'issue', 'create',
                '--repo', repo,
                '--title', full_title,
                '--body', body,
                '--label', labels,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)

            if proc.returncode == 0:
                issue_url = stdout.decode().strip()
                logger.info("GitHub-Issue erstellt: %s", issue_url)
                return issue_url
            else:
                error = stderr.decode().strip()
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
        logger.info("Manueller Scan gestartet (Fokus: %s)", focus or "keiner")

        session_id = await self.db.start_session(trigger_type='manual')
        self._current_session_id = session_id

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
                context_section = ANALYST_CONTEXT_TEMPLATE.format(
                    knowledge_context=knowledge_context,
                )
                prompt = ANALYST_SYSTEM_PROMPT + "\n\n" + context_section

            # Claude-Session
            result = await self.ai_engine.run_analyst_session(
                prompt=prompt,
                timeout=SESSION_TIMEOUT,
                max_turns=SESSION_MAX_TURNS,
            )

            # Health-Snapshot nachher
            health_after = await self._take_health_snapshot(session_id)
            health_ok = self._compare_health(health_before, health_after)

            if not health_ok:
                await self._send_health_alert(health_before, health_after)

            if result:
                await self._process_results(session_id, result, health_ok)
            else:
                await self.db.end_session(
                    session_id=session_id,
                    summary="Manueller Scan ohne Ergebnis",
                    topics=[],
                    tokens_used=0,
                    model='claude-opus-4-6',
                    findings_count=0,
                    auto_fixes=0,
                    issues_created=0,
                )

            return result

        except Exception as e:
            logger.error("Manueller Scan fehlgeschlagen: %s", e, exc_info=True)
            try:
                await self.db.end_session(
                    session_id=session_id,
                    summary=f"Manueller Scan fehlgeschlagen: {str(e)[:200]}",
                    topics=[],
                    tokens_used=0,
                    model='claude-opus-4-6',
                    findings_count=0,
                    auto_fixes=0,
                    issues_created=0,
                )
            except Exception:
                pass
            return None
        finally:
            self._current_session_id = None
