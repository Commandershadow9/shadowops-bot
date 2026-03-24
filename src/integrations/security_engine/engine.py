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
from .deep_scan import DeepScanMode
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

        # DB DSN aus Config oder Parameter
        self._db_dsn = db_dsn
        if not self._db_dsn and config:
            self._db_dsn = getattr(config, 'security_db_dsn', None)
            if not self._db_dsn:
                # Fallback: aus security_analyst config
                sa_cfg = config.raw.get('security_analyst', {}) if hasattr(config, 'raw') else {}
                self._db_dsn = sa_cfg.get('database_dsn',
                    'postgresql://security_analyst:sec_analyst_2026@127.0.0.1:5433/security_analyst')

        # Unified DB
        self.db: Optional[SecurityDB] = None

        # Fixer-Registry
        self.registry = FixerRegistry()
        self.registry.register_noop(NoOpProvider())

        # Phase Executor
        self.executor: Optional[PhaseTypeExecutor] = None

        # Modi (werden in initialize() erstellt)
        self.reactive: Optional[ReactiveMode] = None
        self.deep_scan = None  # DeepScanMode (Phase 3.1)
        self.proactive = None  # ProactiveMode (Phase 4)

        # Circuit Breaker
        self.circuit_breaker = CircuitBreaker(failure_threshold=5, cooldown_seconds=3600)

        # LearningBridge (Cross-Agent)
        self.learning_bridge: Optional[LearningBridge] = None

        # Discord Logger
        self.discord_logger = None

        # Background Tasks
        self._proactive_task: Optional[asyncio.Task] = None

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

        # LearningBridge
        try:
            self.learning_bridge = LearningBridge()
            await self.learning_bridge.initialize()
        except Exception as e:
            logger.warning(f"LearningBridge nicht verfuegbar: {e}")
            self.learning_bridge = None

        # Discord Logger vom Bot holen
        if self.bot and hasattr(self.bot, 'discord_logger'):
            self.discord_logger = self.bot.discord_logger

        logger.info("✅ SecurityEngine initialisiert")

    async def start(self):
        """Startet Background-Tasks (Proactive Scheduler)"""
        self._proactive_task = asyncio.create_task(self._proactive_loop())
        logger.info("🔄 Proactive Scheduler gestartet (alle 6h)")

    async def shutdown(self):
        """Graceful Shutdown"""
        if self._proactive_task:
            self._proactive_task.cancel()
            try:
                await self._proactive_task
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

    # ── Proactive Scheduler ───────────────────────────────────────

    async def _proactive_loop(self):
        """Background-Task: Alle 6h Proactive Report generieren"""
        await asyncio.sleep(60)  # 1min nach Start warten
        while True:
            try:
                logger.info("📊 Proactive Scan gestartet...")
                report = await self.proactive.generate_hardening_report()

                # Report in Discord posten
                gaps = len(report.get('coverage_gaps', []))
                recs = report.get('recommendations', [])
                high_recs = [r for r in recs if r.get('priority') == 'high']

                # Fix-Effektivitaet formatieren
                eff = report.get('fix_effectiveness', {})
                eff_lines = []
                for source, stats in eff.items():
                    emoji = '🟢' if stats.get('status') == 'good' else '🟡' if stats.get('status') == 'warning' else '🔴'
                    eff_lines.append(f"{emoji} {source}: {stats.get('success_rate', 0):.0%}")

                msg = (
                    f"📊 **Proactive Security Report**\n\n"
                    f"**Coverage-Luecken:** {gaps} Bereiche\n"
                    f"**Empfehlungen:** {len(recs)} ({len(high_recs)} kritisch)\n\n"
                    f"**Fix-Effektivitaet:**\n" + '\n'.join(eff_lines)
                )

                if high_recs:
                    msg += "\n\n**⚠️ Kritische Empfehlungen:**\n"
                    for r in high_recs[:3]:
                        msg += f"→ {r.get('message', '')}\n"

                await self._notify_discord(msg, color=0x3498DB if not high_recs else 0xF39C12)
                logger.info(f"📊 Proactive Report: {gaps} Luecken, {len(recs)} Empfehlungen")

            except Exception as e:
                logger.error(f"Proactive Scan Fehler: {e}")

            await asyncio.sleep(PROACTIVE_INTERVAL)

    # ── Stats ───────────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        """Gibt Engine-Statistiken zurueck"""
        return {
            'events_processed': self._events_processed,
            'events_skipped': self._events_skipped,
            'fixes_applied': self._fixes_applied,
            'noops_detected': self._noops_detected,
            'circuit_breaker': self.circuit_breaker.get_status(),
            'registered_fixers': self.registry.list_registered(),
            'proactive_running': self._proactive_task is not None and not self._proactive_task.done(),
        }
