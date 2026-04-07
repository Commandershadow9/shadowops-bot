"""
SecurityEngine — Ein Hirn, drei Modi, eine Datenbank

Vereint EventWatcher, Orchestrator, Self-Healing und Analyst
in einer einheitlichen Engine mit:
- ReactiveMode: Sofortige Reaktion auf Events
- ProactiveMode: Geplante Scans und Haertung
- DeepScanMode: AI-Sessions mit Learning Pipeline

Hooks (Agent Framework Pattern):
- on_fix_failed: Custom Error-Handling
- on_regression_detected: Custom Regression-Handling

Discord-Notifications:
- Events, Fixes, NoOps, Fehler werden in Discord geloggt

Proactive Scheduler:
- Alle 6h Coverage-Report + Trend-Analyse
"""
from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import discord

from .models import SecurityEvent, PhaseType, FixResult, EngineMode, Severity
from .db import SecurityDB
from .executor import PhaseTypeExecutor
from .reactive import ReactiveMode
from .proactive import ProactiveMode
from .deep_scan import DeepScanMode  # Legacy, wird durch SecurityScanAgent ersetzt
from .scan_agent import SecurityScanAgent
from .registry import FixerRegistry
from .providers import NoOpProvider
from .circuit_breaker import CircuitBreaker
from .learning_bridge import LearningBridge
from .fixer_adapters import (
    Fail2banFixerAdapter, TrivyFixerAdapter,
    CrowdSecFixerAdapter, AideFixerAdapter,
)

logger = logging.getLogger('shadowops.security_engine')

# Proactive Scan Intervall (Sekunden)
PROACTIVE_INTERVAL = 6 * 3600  # 6 Stunden


class SecurityEngine:
    """Unified Security Engine — vereint alle Security-Subsysteme"""

    def __init__(self, bot=None, config=None, ai_service=None, context_manager=None, db_dsn: str = None):
        self.bot = bot
        self.config = config
        self.ai_service = ai_service
        self.context_manager = context_manager

        # DB DSN aus Parameter, Config-Property oder Env
        self._db_dsn = db_dsn
        if not self._db_dsn and config:
            self._db_dsn = getattr(config, 'security_analyst_dsn', None)
        if not self._db_dsn:
            logger.error("security_analyst DSN nicht konfiguriert (SECURITY_ANALYST_DB_URL oder config.yaml)")

        # Unified DB
        self.db: Optional[SecurityDB] = None

        # Fixer-Registry
        self.registry = FixerRegistry()
        self.registry.register_noop(NoOpProvider())

        # Phase Executor
        self.executor: Optional[PhaseTypeExecutor] = None

        # Modi (werden in initialize() erstellt)
        self.reactive: Optional[ReactiveMode] = None
        self.deep_scan = None  # Legacy DeepScanMode (wird durch scan_agent ersetzt)
        self.scan_agent: Optional[SecurityScanAgent] = None  # Neuer SecurityScanAgent
        self.proactive = None  # ProactiveMode (Phase 4)

        # Circuit Breaker
        self.circuit_breaker = CircuitBreaker(failure_threshold=5, cooldown_seconds=3600)

        # LearningBridge (Cross-Agent)
        self.learning_bridge: Optional[LearningBridge] = None

        # Discord Logger
        self.discord_logger = None

        # Background Tasks
        self._proactive_task: Optional[asyncio.Task] = None

        # Letzter Proactive Report (fuer Weekly-Recap)
        self._last_proactive_report: Optional[Dict] = None

        # Stats
        self._events_processed: int = 0
        self._events_skipped: int = 0
        self._fixes_applied: int = 0
        self._noops_detected: int = 0

    async def initialize(self):
        """Async-Init: DB verbinden, Executor erstellen, Modi initialisieren"""
        # DB
        if self._db_dsn:
            self.db = SecurityDB(self._db_dsn)
            await self.db.initialize()
            logger.info("SecurityDB verbunden")

        # Executor
        self.executor = PhaseTypeExecutor(
            registry=self.registry,
            db=self.db,
        )

        # Modi
        self.reactive = ReactiveMode(
            db=self.db,
            executor=self.executor,
            ai_service=self.ai_service,
        )
        self.proactive = ProactiveMode(
            db=self.db,
            executor=self.executor,
            ai_service=self.ai_service,
        )
        self.deep_scan = DeepScanMode(
            db=self.db,
            ai_engine=self.ai_service,
            executor=self.executor,
            context_manager=self.context_manager,
        )

        # SecurityScanAgent (ersetzt DeepScanMode wenn Bot verfuegbar)
        if self.bot:
            self.scan_agent = SecurityScanAgent(
                bot=self.bot,
                config=self.bot.config if hasattr(self.bot, 'config') else self.config,
                ai_engine=self.ai_service,
                db=self.db,
                context_manager=self.context_manager,
                executor=self.executor,
                learning_bridge=None,  # wird nach LearningBridge-Init gesetzt
            )

        # LearningBridge
        try:
            self.learning_bridge = LearningBridge()
            await self.learning_bridge.initialize()
        except Exception as e:
            logger.warning(f"LearningBridge nicht verfuegbar: {e}")
            self.learning_bridge = None

        # LearningBridge an ScanAgent weitergeben
        if self.scan_agent and self.learning_bridge:
            self.scan_agent.learning_bridge = self.learning_bridge

        # Discord Logger vom Bot holen
        if self.bot and hasattr(self.bot, 'discord_logger'):
            self.discord_logger = self.bot.discord_logger

        logger.info("✅ SecurityEngine initialisiert")

    async def start(self):
        """Startet Background-Tasks (Proactive Scheduler + ScanAgent + Midnight Reset)"""
        self._proactive_task = asyncio.create_task(self._proactive_loop())
        self._midnight_task = asyncio.create_task(self._midnight_reset_loop())
        logger.info("🔄 Proactive Scheduler gestartet (alle 6h)")

        # SecurityScanAgent starten (ersetzt den alten daily_scan_loop)
        if self.scan_agent:
            await self.scan_agent.start()
            logger.info("🔍 SecurityScanAgent gestartet (adaptiv, Activity-Monitor)")
        else:
            # Fallback: Legacy Daily-Scan-Loop (wenn kein Bot verfuegbar)
            self._scan_task = asyncio.create_task(self._daily_scan_loop())
            logger.info("🔍 Legacy Daily Scan Scheduler gestartet")

    async def shutdown(self):
        """Graceful Shutdown"""
        # ScanAgent stoppen
        if self.scan_agent:
            await self.scan_agent.stop()

        for task in [self._proactive_task, getattr(self, '_scan_task', None), getattr(self, '_midnight_task', None)]:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        if self.learning_bridge:
            await self.learning_bridge.close()
        if self.db:
            await self.db.close()
        logger.info("SecurityEngine heruntergefahren")

    # ── Fixer Registration ──────────────────────────────────────────

    def register_fixer(self, source: str, phase_type: Optional[PhaseType], provider):
        """Registriert einen FixProvider fuer eine Event-Source"""
        self.registry.register(source, phase_type, provider)
        logger.info(f"Fixer registriert: {source}/{phase_type.value if phase_type else '*'} -> {type(provider).__name__}")

    def register_existing_fixers(self, fail2ban_fixer=None, trivy_fixer=None,
                                  crowdsec_fixer=None, aide_fixer=None):
        """Registriert bestehende Fixer als FixProvider-Adapter"""
        if fail2ban_fixer:
            adapter = Fail2banFixerAdapter(fail2ban_fixer)
            self.registry.register('fail2ban', None, adapter)  # Fuer alle PhaseTypes
            logger.info("Fail2ban-Fixer registriert (mit No-Op-Detection)")

        if trivy_fixer:
            adapter = TrivyFixerAdapter(trivy_fixer)
            self.registry.register('trivy', None, adapter)
            logger.info("Trivy-Fixer registriert")

        if crowdsec_fixer:
            adapter = CrowdSecFixerAdapter(crowdsec_fixer)
            self.registry.register('crowdsec', None, adapter)
            logger.info("CrowdSec-Fixer registriert")

        if aide_fixer:
            adapter = AideFixerAdapter(aide_fixer)
            self.registry.register('aide', None, adapter)
            logger.info("AIDE-Fixer registriert")

    # ── Event-Handler ───────────────────────────────────────────────

    async def handle_security_event(self, event: SecurityEvent) -> bool:
        """
        Haupteinstieg fuer alle Security-Events.
        Wird vom EventWatcher aufgerufen.
        """
        if not self.circuit_breaker.can_attempt:
            logger.warning(f"⚡ Circuit Breaker offen — Event {event.event_id} uebersprungen")
            self._events_skipped += 1
            return False

        try:
            success = await self.reactive.handle_events([event])
            self._events_processed += 1

            if success:
                self.circuit_breaker.record_success(event.source)
                self._fixes_applied += 1
                await self._notify_discord(
                    f"✅ **Security Event behandelt**\n"
                    f"Source: `{event.source}` | Severity: `{event.severity.value if hasattr(event.severity, 'value') else event.severity}`\n"
                    f"Modus: Fast-Path | Event: `{event.event_id}`",
                    color=0x2ECC71
                )
            else:
                self.circuit_breaker.record_failure(event.source)
                await self._notify_discord(
                    f"⚠️ **Security Event fehlgeschlagen**\n"
                    f"Source: `{event.source}` | Event: `{event.event_id}`",
                    color=0xE74C3C
                )

            # Learning Bridge: Fix-Feedback
            if self.learning_bridge and self.learning_bridge.is_connected:
                project = event.details.get('project', 'shadowops-bot')
                await self.learning_bridge.record_fix_feedback(
                    project=project, fix_id=event.event_id,
                    success=success, fix_type='reactive_fix',
                )

            return success

        except Exception as e:
            self.circuit_breaker.record_failure(event.source)
            await self.on_fix_failed(event, str(e))
            return False

    async def handle_event_batch(self, events: List[SecurityEvent]) -> bool:
        """Verarbeitet einen Batch von Events"""
        if not self.circuit_breaker.can_attempt:
            logger.warning(f"⚡ Circuit Breaker offen — Batch mit {len(events)} Events uebersprungen")
            self._events_skipped += len(events)
            return False

        try:
            success = await self.reactive.handle_events(events)
            self._events_processed += len(events)

            sources = ', '.join(set(e.source for e in events))
            await self._notify_discord(
                f"📦 **Batch verarbeitet** ({len(events)} Events)\n"
                f"Sources: `{sources}` | Ergebnis: {'✅ Erfolg' if success else '⚠️ Teilweise fehlgeschlagen'}",
                color=0x2ECC71 if success else 0xF39C12
            )

            return success
        except Exception as e:
            self.circuit_breaker.record_failure('batch')
            logger.error(f"Batch-Verarbeitung fehlgeschlagen: {e}")
            return False

    # ── Hooks (Agent Framework Pattern) ─────────────────────────────

    async def on_fix_failed(self, event: SecurityEvent, error: str):
        """Hook: Was passiert wenn ein Fix fehlschlaegt? Override-faehig."""
        logger.error(f"Fix fehlgeschlagen fuer {event.event_id}: {error}")

    async def on_regression_detected(self, finding: Dict, verification: Dict):
        """Hook: Was passiert bei Regression? Override-faehig."""
        logger.warning(f"Regression erkannt: Finding {finding.get('id')} wieder offen")

    # ── Discord Notifications ─────────────────────────────────────

    async def _notify_discord(self, message: str, color: int = 0x3498DB):
        """Sendet Status-Update an Discord (via DiscordChannelLogger)"""
        try:
            if self.discord_logger:
                self.discord_logger.log_orchestrator(message, severity="info")
            elif self.bot:
                # Fallback: Direkt in den Security-Kanal posten
                from utils.state_manager import StateManager
                state = StateManager()
                channel_id = state.get('auto_remediation_orchestrator')
                if channel_id:
                    channel = self.bot.get_channel(int(channel_id))
                    if channel:
                        embed = discord.Embed(
                            description=message,
                            color=color,
                            timestamp=datetime.now(timezone.utc)
                        )
                        embed.set_footer(text="Security Engine v6")
                        await channel.send(embed=embed)
        except Exception as e:
            logger.debug(f"Discord-Notification fehlgeschlagen: {e}")

    # ── Daily Scan Scheduler ─────────────────────────────────────

    async def _daily_scan_loop(self):
        """Background-Task: Taegliche Security-Scans (adaptiv 1-3x/Tag)"""
        # Erster Scan nach 5 Minuten (Bot soll erstmal vollstaendig hochfahren)
        await asyncio.sleep(300)

        while True:
            try:
                if not self.deep_scan:
                    logger.warning("DeepScan nicht verfuegbar — uebersprungen")
                    await asyncio.sleep(3600)
                    continue

                can_scan = await self.deep_scan.can_start_session()
                if not can_scan:
                    logger.info("🔍 Session-Limit erreicht — naechster Versuch in 2h")
                    await asyncio.sleep(7200)
                    continue

                logger.info("🔍 Starte geplanten Security Deep-Scan...")
                session_result = await self.deep_scan.run_session()

                # Discord-Report
                status_emoji = "✅" if session_result['status'] == 'completed' else "❌"
                mode = session_result.get('mode', '?')
                findings = session_result.get('findings_count', 0)
                fixes = session_result.get('fixes_count', 0)
                issues = session_result.get('issues_created', 0)

                msg = (
                    f"🔍 **Security Deep-Scan abgeschlossen** {status_emoji}\n\n"
                    f"**Modus:** `{mode}`\n"
                    f"**Findings:** {findings} neue\n"
                    f"**Fixes:** {fixes} angewendet\n"
                    f"**Issues:** {issues} erstellt\n"
                )

                if session_result.get('findings'):
                    msg += "\n**Neue Findings:**\n"
                    for f in session_result['findings'][:5]:
                        sev = f.get('severity', '?')
                        emoji = '🔴' if sev == 'CRITICAL' else '🟠' if sev == 'HIGH' else '🟡' if sev == 'MEDIUM' else '⚪'
                        msg += f"{emoji} [{sev}] {f.get('title', '?')}\n"

                if session_result.get('error'):
                    msg += f"\n⚠️ **Fehler:** {session_result['error'][:200]}"

                color = 0x2ECC71 if session_result['status'] == 'completed' and findings == 0 else \
                        0xF39C12 if findings > 0 else 0xE74C3C
                await self._notify_discord(msg, color=color)

                # Learning Bridge: Session-Ergebnis teilen
                if self.learning_bridge and self.learning_bridge.is_connected:
                    await self.learning_bridge.share_knowledge(
                        'security', f'scan_{datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")}',
                        f'Scan ({mode}): {findings} Findings, {fixes} Fixes, {issues} Issues',
                        confidence=0.8,
                    )

                # Naechster Scan: Adaptiv
                # Viele Findings → 4h, wenige → 8h, keine → 12h
                if findings >= 5:
                    wait_hours = 4
                elif findings >= 1:
                    wait_hours = 8
                else:
                    wait_hours = 12

                logger.info(f"🔍 Naechster Scan in {wait_hours}h")
                await asyncio.sleep(wait_hours * 3600)

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Daily Scan Fehler: {e}", exc_info=True)
                await asyncio.sleep(3600)  # 1h warten bei Fehler

    # ── Midnight Reset ────────────────────────────────────────────

    async def _midnight_reset_loop(self):
        """Setzt taegliche Counter zurueck (Sessions, Stats)"""
        while True:
            now = datetime.now(timezone.utc)
            # Naechste Mitternacht berechnen
            tomorrow = now.replace(hour=0, minute=0, second=0, microsecond=0)
            if tomorrow <= now:
                tomorrow = tomorrow.replace(day=tomorrow.day + 1)
            wait = (tomorrow - now).total_seconds()
            await asyncio.sleep(wait)

            if self.scan_agent:
                self.scan_agent.reset_daily()
                logger.info("🔄 Daily Reset: ScanAgent Session-Counter zurueckgesetzt")
            elif self.deep_scan:
                self.deep_scan.reset_daily()
                logger.info("🔄 Daily Reset: Legacy Session-Counter zurueckgesetzt")

    # ── Proactive Scheduler ───────────────────────────────────────

    async def _proactive_loop(self):
        """Background-Task: Alle 6h Proactive Report generieren.

        Daten werden gesammelt fuer den Weekly-Recap.
        Discord-Post NUR bei kritischen Empfehlungen (kein Routine-Spam).
        """
        await asyncio.sleep(60)
        while True:
            try:
                report = await self.proactive.generate_hardening_report()
                self._last_proactive_report = report  # Fuer Weekly-Recap verfuegbar

                recs = report.get('recommendations', [])
                high_recs = [r for r in recs if r.get('priority') == 'high']

                logger.info("Proactive Report: %d Gaps, %d Empfehlungen (%d kritisch)",
                            len(report.get('coverage_gaps', [])), len(recs), len(high_recs))

                # NUR bei kritischen Empfehlungen in Discord posten
                if high_recs:
                    msg = f"**{len(high_recs)} kritische Empfehlung(en):**\n"
                    for r in high_recs[:5]:
                        msg += f"→ {r.get('message', '')}\n"
                    await self._notify_discord(msg, color=0xE74C3C)

            except Exception as e:
                logger.error(f"Proactive Scan Fehler: {e}")

            await asyncio.sleep(PROACTIVE_INTERVAL)

    # ── Stats ───────────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        """Gibt Engine-Statistiken zurueck"""
        stats = {
            'events_processed': self._events_processed,
            'events_skipped': self._events_skipped,
            'fixes_applied': self._fixes_applied,
            'noops_detected': self._noops_detected,
            'circuit_breaker': self.circuit_breaker.get_status(),
            'registered_fixers': self.registry.list_registered(),
            'proactive_running': self._proactive_task is not None and not self._proactive_task.done(),
        }
        if self.scan_agent:
            stats['scan_agent'] = self.scan_agent.get_stats()
        return stats
