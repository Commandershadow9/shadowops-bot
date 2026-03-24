"""
PhaseTypeExecutor — Semantische Phase-Ausfuehrung

Steuert Execution basierend auf Phase-Typ:
- recon/verify/monitor: Read-only, kein Fix
- contain/fix: Provider-Chain durchlaufen (NoOp → Fixer)

Dedup: Events werden pro Batch nur 1x gefixt (ausser verify/monitor).
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Set

from .models import SecurityEvent, PhaseType, FixResult
from .registry import FixerRegistry

logger = logging.getLogger('shadowops.phase_executor')


class PhaseTypeExecutor:
    """Fuehrt Phasen semantisch aus: Read-only vs. Fix mit Provider-Chain + Dedup."""

    def __init__(self, registry: FixerRegistry, db=None, backup_manager=None):
        self.registry = registry
        self.db = db
        self.backup_manager = backup_manager
        self._fixed_events: Set[str] = set()

    def reset_batch(self):
        """Setzt Dedup-Tracking zurueck (neuer Batch)."""
        self._fixed_events.clear()

    async def execute_phase(
        self,
        phase: Dict[str, Any],
        events: List[SecurityEvent],
        batch_id: str = '',
        discord_callback=None,
    ) -> bool:
        """
        Fuehrt eine Phase aus.

        Returns: True wenn alle Events erfolgreich verarbeitet wurden.
        """
        phase_type_str = phase.get('type', 'fix')
        try:
            phase_type = PhaseType(phase_type_str)
        except ValueError:
            logger.warning(f"Unbekannter Phase-Typ '{phase_type_str}', Fallback auf 'fix'")
            phase_type = PhaseType.FIX

        phase_name = phase.get('name', 'Unnamed')
        start = time.time()
        events_processed = 0
        all_success = True

        logger.info(f"Phase '{phase_name}' ({phase_type.value}) mit {len(events)} Events...")

        if phase_type.is_read_only:
            # Read-only Phasen: kein Fix, nur zaehlen
            logger.info(f"   Read-only Phase ({phase_type.value}) — kein Fix")
            events_processed = len(events)
        else:
            for event in events:
                # Dedup: bereits gefixt? (nur fuer FIX, nicht fuer CONTAIN)
                if event.event_id in self._fixed_events and phase_type == PhaseType.FIX:
                    logger.info(f"   Event {event.event_id} bereits gefixt — uebersprungen")
                    if self.db:
                        await self.db.record_fix_attempt(
                            event_source=event.source,
                            event_type=event.event_type,
                            event_signature=event.signature,
                            phase_type=phase_type.value,
                            approach='dedup_skip',
                            commands=[],
                            result='skipped_duplicate',
                            batch_id=batch_id,
                        )
                    continue

                result = await self._execute_provider_chain(event, phase, phase_type)
                events_processed += 1

                if result and result.is_success:
                    # no_op markiert Event NICHT als gefixt (kann spaeter nochmal geprueft werden)
                    if result.status != 'no_op':
                        self._fixed_events.add(event.event_id)
                    if self.db:
                        await self.db.record_fix_attempt(
                            event_source=event.source,
                            event_type=event.event_type,
                            event_signature=event.signature,
                            phase_type=phase_type.value,
                            approach=result.message,
                            commands=[],
                            result=result.status,
                            duration_ms=int(result.duration_seconds * 1000),
                            batch_id=batch_id,
                        )
                elif result:
                    all_success = False
                    if self.db:
                        await self.db.record_fix_attempt(
                            event_source=event.source,
                            event_type=event.event_type,
                            event_signature=event.signature,
                            phase_type=phase_type.value,
                            approach='provider_chain',
                            commands=[],
                            result='failed',
                            error_message=result.error,
                            duration_ms=int(result.duration_seconds * 1000),
                            batch_id=batch_id,
                        )

        duration_ms = int((time.time() - start) * 1000)
        if self.db:
            await self.db.record_phase_execution(
                batch_id=batch_id,
                phase_type=phase_type.value,
                phase_name=phase_name,
                events_processed=events_processed,
                result='success' if all_success else 'failed',
                duration_ms=duration_ms,
            )

        status = 'OK' if all_success else 'WARN'
        logger.info(f"   [{status}] Phase '{phase_name}' abgeschlossen ({duration_ms}ms)")
        return all_success

    async def _execute_provider_chain(
        self,
        event: SecurityEvent,
        phase: Dict[str, Any],
        phase_type: PhaseType,
    ) -> FixResult:
        """Durchlaeuft die Provider-Chain fuer ein Event. Gibt FixResult zurueck."""
        providers = self.registry.get_providers(event.source, phase_type)
        if not providers:
            logger.warning(f"   Kein Provider fuer {event.source}/{phase_type.value}")
            return FixResult.failed(
                f"Kein Provider registriert fuer {event.source}/{phase_type.value}",
                phase_type=phase_type,
            )

        strategy = {
            'description': phase.get('description', ''),
            'steps': phase.get('steps', []),
            'phase_name': phase.get('name', ''),
        }
        context = {'phase_type': phase_type}

        for provider in providers:
            try:
                result = await provider.execute(event, strategy, context)
                if result is not None:
                    return result
            except Exception as e:
                logger.error(f"   Provider {type(provider).__name__} Fehler: {e}")
                continue

        return FixResult.failed(
            "Alle Provider haben None zurueckgegeben",
            phase_type=phase_type,
        )
