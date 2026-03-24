"""
SecurityEngine — Ein Hirn, drei Modi, eine Datenbank

Vereint EventWatcher, Orchestrator, Self-Healing und Analyst
in einer einheitlichen Engine mit:
- ReactiveMode: Sofortige Reaktion auf Events
- ProactiveMode: Geplante Scans und Haertung (Phase 4)
- DeepScanMode: AI-Sessions mit Learning Pipeline

Hooks (Agent Framework Pattern):
- on_fix_failed: Custom Error-Handling
- on_regression_detected: Custom Regression-Handling
"""
from __future__ import annotations
import logging
from typing import Any, Dict, List, Optional

from .models import SecurityEvent, PhaseType, FixResult, EngineMode, Severity
from .db import SecurityDB
from .executor import PhaseTypeExecutor
from .reactive import ReactiveMode
from .registry import FixerRegistry
from .providers import NoOpProvider
from .circuit_breaker import CircuitBreaker
from .fixer_adapters import (
    Fail2banFixerAdapter, TrivyFixerAdapter,
    CrowdSecFixerAdapter, AideFixerAdapter,
)

logger = logging.getLogger('shadowops.security_engine')


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

        # Stats
        self._events_processed: int = 0
        self._events_skipped: int = 0

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

        logger.info("SecurityEngine initialisiert")

    async def shutdown(self):
        """Graceful Shutdown"""
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
            logger.warning(f"Circuit Breaker offen — Event {event.event_id} uebersprungen")
            self._events_skipped += 1
            return False

        try:
            success = await self.reactive.handle_events([event])
            self._events_processed += 1

            if success:
                self.circuit_breaker.record_success(event.source)
            else:
                self.circuit_breaker.record_failure(event.source)

            return success

        except Exception as e:
            self.circuit_breaker.record_failure(event.source)
            await self.on_fix_failed(event, str(e))
            return False

    async def handle_event_batch(self, events: List[SecurityEvent]) -> bool:
        """Verarbeitet einen Batch von Events"""
        if not self.circuit_breaker.can_attempt:
            logger.warning(f"Circuit Breaker offen — Batch mit {len(events)} Events uebersprungen")
            self._events_skipped += len(events)
            return False

        try:
            success = await self.reactive.handle_events(events)
            self._events_processed += len(events)
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

    # ── Stats ───────────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        """Gibt Engine-Statistiken zurueck"""
        return {
            'events_processed': self._events_processed,
            'events_skipped': self._events_skipped,
            'circuit_breaker': self.circuit_breaker.get_status(),
            'registered_fixers': self.registry.list_registered(),
            'mode': 'reactive',  # Erweitern wenn ProactiveMode/DeepScan aktiv
        }
