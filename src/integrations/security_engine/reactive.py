"""
ReactiveMode — Sofortige Reaktion auf Security Events

Entscheidungslogik:
1. Event claimen (Cross-Mode Lock)
2. 1-2 Events, nicht CRITICAL? -> Fast-Path (direkt Fixer, kein KI-Plan)
3. 3+ Events oder CRITICAL? -> KI-Plan mit typed Phases
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .models import SecurityEvent, PhaseType, FixResult, Severity

logger = logging.getLogger('shadowops.reactive')

BATCH_PLAN_THRESHOLD = 3


class ReactiveMode:

    def __init__(self, db, executor, ai_service=None, approval_manager=None):
        self.db = db
        self.executor = executor
        self.ai_service = ai_service
        self.approval_manager = approval_manager

    async def handle_events(self, events: List[SecurityEvent]) -> bool:
        """Verarbeitet Events. Returns True wenn alle erfolgreich/uebersprungen."""
        self.executor.reset_batch()

        # 1. Events claimen
        claimable = []
        for event in events:
            claimed = await self.db.claim_event(event.event_id, 'reactive')
            if claimed:
                claimable.append(event)
            else:
                logger.info(f"Event {event.event_id} bereits claimed — uebersprungen")

        if not claimable:
            return True

        try:
            if len(claimable) < BATCH_PLAN_THRESHOLD and not self._has_critical(claimable):
                return await self._fast_path(claimable)
            else:
                return await self._planned_path(claimable)
        finally:
            for event in claimable:
                await self.db.release_event(event.event_id, 'completed')

    async def _fast_path(self, events: List[SecurityEvent]) -> bool:
        """Direct fix ohne KI-Plan — fuer 1-2 einfache Events"""
        logger.info(f"Fast-Path: {len(events)} Event(s) direkt fixen")
        all_success = True

        for event in events:
            phase_type = 'contain' if not event.is_persistent else 'fix'
            phase = {
                'name': f'Fast-Fix {event.source}',
                'type': phase_type,
                'description': f'Direkte Behandlung: {event.source} {event.event_type}',
                'steps': [],
            }
            success = await self.executor.execute_phase(
                phase, [event], batch_id=f'fast_{event.event_id}'
            )
            if not success:
                all_success = False

        return all_success

    async def _planned_path(self, events: List[SecurityEvent]) -> bool:
        """KI-gestuetzter Plan mit typed Phases"""
        logger.info(f"Planned-Path: {len(events)} Events -> KI-Plan")

        if not self.ai_service:
            logger.warning("Kein AI-Service — Fallback auf Fast-Path")
            return await self._fast_path(events)

        plan = await self.ai_service.generate_coordinated_plan(
            self._build_plan_prompt(events),
            context={'events': [e.to_dict() for e in events]},
        )

        if not plan or not plan.get('phases'):
            logger.error("KI konnte keinen Plan erstellen — Fallback auf Fast-Path")
            return await self._fast_path(events)

        batch_id = f"batch_{events[0].event_id}" if events else "batch_unknown"
        all_success = True

        for phase in plan['phases']:
            success = await self.executor.execute_phase(phase, events, batch_id=batch_id)
            if not success:
                all_success = False
                logger.warning(f"Phase '{phase.get('name')}' fehlgeschlagen")

        return all_success

    def _has_critical(self, events: List[SecurityEvent]) -> bool:
        return any(e.severity == Severity.CRITICAL for e in events)

    def _build_plan_prompt(self, events: List[SecurityEvent]) -> str:
        event_summary = "\n".join([
            f"- {e.source.upper()} ({e.severity.value}): {e.event_type} — {e.details}"
            for e in events
        ])
        return f"""Erstelle einen koordinierten Remediation-Plan.

## Events
{event_summary}

## Regeln
- Maximal 3-4 Phasen
- Jede Phase MUSS einen type haben: recon, contain, fix, verify, monitor
- recon/verify/monitor sind read-only
- contain ist fuer sofortige Eindaemmung (IP blocken)
- fix ist fuer dauerhafte Behebung (Config aendern)
- NIEMALS gleichen Fix in mehreren Phasen wiederholen
- Bei SSH-Bans: contain zuerst, dann fix, dann verify
"""
