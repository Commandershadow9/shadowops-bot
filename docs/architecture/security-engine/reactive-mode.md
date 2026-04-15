---
title: Security Engine v6 — Reactive Mode
status: active
version: v6
last_reviewed: 2026-04-15
owner: CommanderShadow9
related:
  - ../../plans/2026-03-24-security-engine-v6.md
---

# Security Engine v6 — Reactive Mode (Phase 2)

Diese Datei dokumentiert Phase 2: Smart Execution mit Fast-Path, Event-Claiming, Fixer-Adapter und Planner-Prompt.

Der Reactive Mode ist der haeufigste Pfad: EventWatcher erkennt ein Security-Event (Fail2ban-Ban, Trivy-CVE, CrowdSec-Alert, AIDE-Integritaetsbruch), uebergibt es der Engine, die entscheidet Fast-Path (1-2 Events direkt an Fixer) vs. KI-Plan (3+ Events oder CRITICAL).

Die hier dokumentierten Event-Strukturen und Planning-Regeln werden auch vom Deep-Scan-Mode konsumiert — siehe Verweis in [deep-scan-mode.md](deep-scan-mode.md).

---

## Task 2.1: PhaseTypeExecutor

**Files:**
- Create: `src/integrations/security_engine/executor.py`
- Test: `tests/unit/test_phase_executor.py`

### Test

```python
# tests/unit/test_phase_executor.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from src.integrations.security_engine.executor import PhaseTypeExecutor
from src.integrations.security_engine.models import (
    BanEvent, PhaseType, FixResult, Severity
)
from src.integrations.security_engine.registry import FixerRegistry
from src.integrations.security_engine.providers import NoOpProvider


class TestPhaseTypeExecutor:
    @pytest.mark.asyncio
    async def test_recon_phase_read_only(self):
        """Recon darf keine Fixes ausfuehren"""
        registry = FixerRegistry()
        db = AsyncMock()
        db.record_phase_execution = AsyncMock(return_value=1)
        executor = PhaseTypeExecutor(registry=registry, db=db)

        event = BanEvent(
            source='fail2ban', severity=Severity.HIGH,
            details={'ip': '1.2.3.4'}, event_id='test'
        )
        phase = {
            'name': 'Beweissicherung',
            'type': 'recon',
            'description': 'Status erfassen',
            'steps': ['fail2ban-client status sshd']
        }
        result = await executor.execute_phase(phase, [event], batch_id='b1')
        assert result is True
        db.record_phase_execution.assert_called_once()

    @pytest.mark.asyncio
    async def test_fix_phase_calls_provider(self):
        """Fix-Phase ruft den registrierten Provider auf"""
        registry = FixerRegistry()
        mock_provider = AsyncMock()
        mock_provider.execute = AsyncMock(
            return_value=FixResult.success("Fixed", phase_type=PhaseType.FIX)
        )
        registry.register('fail2ban', PhaseType.FIX, mock_provider)

        db = AsyncMock()
        db.record_phase_execution = AsyncMock(return_value=1)
        db.record_fix_attempt = AsyncMock(return_value=1)
        executor = PhaseTypeExecutor(registry=registry, db=db)

        event = BanEvent(
            source='fail2ban', severity=Severity.HIGH,
            details={'ip': '1.2.3.4'}, event_id='test'
        )
        phase = {
            'name': 'Config haerten',
            'type': 'fix',
            'description': 'Jail haerten',
            'steps': ['maxretry=3']
        }
        result = await executor.execute_phase(phase, [event], batch_id='b1')
        assert result is True
        mock_provider.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_dedup_skips_already_fixed(self):
        """Events die bereits gefixt wurden werden uebersprungen"""
        registry = FixerRegistry()
        mock_provider = AsyncMock()
        mock_provider.execute = AsyncMock(
            return_value=FixResult.success("Fixed", phase_type=PhaseType.FIX)
        )
        registry.register('fail2ban', PhaseType.FIX, mock_provider)

        db = AsyncMock()
        db.record_phase_execution = AsyncMock(return_value=1)
        db.record_fix_attempt = AsyncMock(return_value=1)
        executor = PhaseTypeExecutor(registry=registry, db=db)

        event = BanEvent(
            source='fail2ban', severity=Severity.HIGH,
            details={'ip': '1.2.3.4'}, event_id='test'
        )

        # Phase 1: Fix
        await executor.execute_phase(
            {'name': 'Fix', 'type': 'fix', 'description': 'Fix', 'steps': []},
            [event], batch_id='b1'
        )
        # Phase 2: Gleicher Event -> sollte uebersprungen werden
        await executor.execute_phase(
            {'name': 'Fix2', 'type': 'fix', 'description': 'Fix2', 'steps': []},
            [event], batch_id='b1'
        )
        # Provider sollte nur 1x aufgerufen worden sein
        assert mock_provider.execute.call_count == 1

    @pytest.mark.asyncio
    async def test_verify_phase_does_not_dedup(self):
        """Verify-Phase ueberspringt NICHT — sie muss immer laufen"""
        registry = FixerRegistry()
        db = AsyncMock()
        db.record_phase_execution = AsyncMock(return_value=1)
        executor = PhaseTypeExecutor(registry=registry, db=db)

        event = BanEvent(
            source='fail2ban', severity=Severity.HIGH,
            details={'ip': '1.2.3.4'}, event_id='test'
        )
        # Markiere als bereits gefixt
        executor._fixed_events.add('test')

        result = await executor.execute_phase(
            {'name': 'Verify', 'type': 'verify', 'description': 'Pruefen', 'steps': []},
            [event], batch_id='b1'
        )
        assert result is True  # Verify laeuft trotzdem
```

### Implementation PhaseTypeExecutor

```python
# src/integrations/security_engine/executor.py
"""
PhaseTypeExecutor — Semantische Phase-Ausfuehrung

Steuert die Execution basierend auf Phase-Typ:
- recon/verify/monitor: Read-only, kein Fix
- contain: Sofort-Block, kein KI-Call
- fix: Provider-Chain durchlaufen (NoOp -> Fixer)

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

    def __init__(self, registry: FixerRegistry, db=None, backup_manager=None):
        self.registry = registry
        self.db = db
        self.backup_manager = backup_manager
        self._fixed_events: Set[str] = set()  # Event-IDs die bereits gefixt wurden

    def reset_batch(self):
        """Vor neuem Batch aufrufen"""
        self._fixed_events.clear()

    async def execute_phase(
        self,
        phase: Dict[str, Any],
        events: List[SecurityEvent],
        batch_id: str = '',
        discord_callback=None,
    ) -> bool:
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
            # Read-only Phasen: Logging, kein Fix
            logger.info(f"   Read-only Phase ({phase_type.value}) — kein Fix")
            events_processed = len(events)
        else:
            # Fix/Contain Phasen: Provider-Chain durchlaufen
            for event in events:
                # Dedup: bereits gefixt? (ausser verify/monitor)
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

        status = "OK" if all_success else "FAIL"
        logger.info(f"   {status} Phase '{phase_name}' abgeschlossen ({duration_ms}ms)")
        return all_success

    async def _execute_provider_chain(
        self,
        event: SecurityEvent,
        phase: Dict,
        phase_type: PhaseType,
    ) -> Optional[FixResult]:
        """Durchlaeuft Provider-Chain bis einer ein Ergebnis liefert"""
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

        return FixResult.failed("Alle Provider haben None zurueckgegeben", phase_type=phase_type)
```

---

## Task 2.2: ReactiveMode (Fast-Path + Batch)

**Files:**
- Create: `src/integrations/security_engine/reactive.py`
- Test: `tests/unit/test_reactive_mode.py`

### Test

```python
# tests/unit/test_reactive_mode.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.integrations.security_engine.reactive import ReactiveMode
from src.integrations.security_engine.models import (
    BanEvent, Severity, FixResult, PhaseType
)


class TestReactiveMode:
    @pytest.mark.asyncio
    async def test_fast_path_single_non_critical_event(self):
        """1 nicht-kritisches Event -> Fast-Path, kein KI-Plan"""
        db = AsyncMock()
        db.claim_event = AsyncMock(return_value=True)
        db.release_event = AsyncMock()
        db.record_fix_attempt = AsyncMock(return_value=1)

        executor = AsyncMock()
        executor.execute_phase = AsyncMock(return_value=True)
        executor.reset_batch = MagicMock()

        mode = ReactiveMode(db=db, executor=executor, ai_service=None)

        event = BanEvent(
            source='fail2ban', severity=Severity.HIGH,
            details={'ip': '1.2.3.4'}, event_id='f2b_001'
        )
        result = await mode.handle_events([event])
        assert result is True
        # Kein KI-Call (ai_service nicht aufgerufen)

    @pytest.mark.asyncio
    async def test_batch_triggers_ki_plan(self):
        """3+ Events -> KI-Plan erstellen"""
        db = AsyncMock()
        db.claim_event = AsyncMock(return_value=True)
        db.release_event = AsyncMock()

        executor = AsyncMock()
        executor.execute_phase = AsyncMock(return_value=True)
        executor.reset_batch = MagicMock()

        ai_service = AsyncMock()
        ai_service.generate_coordinated_plan = AsyncMock(return_value={
            'description': 'Test Plan',
            'confidence': 0.9,
            'estimated_duration_minutes': 10,
            'requires_restart': False,
            'phases': [
                {'name': 'Contain', 'type': 'contain', 'description': 'Block',
                 'steps': ['block ip'], 'estimated_minutes': 2},
                {'name': 'Verify', 'type': 'verify', 'description': 'Check',
                 'steps': ['check'], 'estimated_minutes': 2},
            ],
            'rollback_plan': 'Rollback'
        })

        mode = ReactiveMode(db=db, executor=executor, ai_service=ai_service)

        events = [
            BanEvent(source='fail2ban', severity=Severity.HIGH,
                     details={'ip': f'1.2.3.{i}'}, event_id=f'f2b_{i}')
            for i in range(3)
        ]
        result = await mode.handle_events(events)
        assert result is True
        ai_service.generate_coordinated_plan.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_already_claimed_event(self):
        """Event das bereits von einem anderen Mode bearbeitet wird -> skip"""
        db = AsyncMock()
        db.claim_event = AsyncMock(return_value=False)  # Bereits claimed

        executor = AsyncMock()
        executor.reset_batch = MagicMock()
        mode = ReactiveMode(db=db, executor=executor, ai_service=None)

        event = BanEvent(
            source='fail2ban', severity=Severity.HIGH,
            details={'ip': '1.2.3.4'}, event_id='f2b_001'
        )
        result = await mode.handle_events([event])
        assert result is True  # Kein Fehler, nur skip
```

### Implementation ReactiveMode

```python
# src/integrations/security_engine/reactive.py
"""
ReactiveMode — Sofortige Reaktion auf Security Events

Entscheidungslogik:
1. Event claimen (Cross-Mode Lock)
2. Bekanntes Pattern + CRITICAL? -> CONTAIN sofort (kein KI)
3. 1-2 Events, nicht CRITICAL? -> Fast-Path (direkt Fixer)
4. 3+ Events oder CRITICAL? -> KI-Plan mit typed Phases
"""

from __future__ import annotations
import logging
from typing import List, Optional

from .models import SecurityEvent, PhaseType, FixResult, Severity

logger = logging.getLogger('shadowops.reactive')

# Schwelle: ab wie vielen Events wird ein KI-Plan erstellt?
BATCH_PLAN_THRESHOLD = 3


class ReactiveMode:

    def __init__(self, db, executor, ai_service=None, approval_manager=None):
        self.db = db
        self.executor = executor
        self.ai_service = ai_service
        self.approval_manager = approval_manager

    async def handle_events(self, events: List[SecurityEvent]) -> bool:
        """
        Verarbeitet eine Liste von Events.
        Returns True wenn alle erfolgreich (oder uebersprungen).
        """
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
            # 2. Entscheidung: Fast-Path oder KI-Plan?
            if len(claimable) < BATCH_PLAN_THRESHOLD and not self._has_critical(claimable):
                return await self._fast_path(claimable)
            else:
                return await self._planned_path(claimable)
        finally:
            # Events freigeben
            for event in claimable:
                await self.db.release_event(event.event_id, 'completed')

    async def _fast_path(self, events: List[SecurityEvent]) -> bool:
        """Direct fix ohne KI-Plan — fuer 1-2 einfache Events"""
        logger.info(f"Fast-Path: {len(events)} Event(s) direkt fixen")
        all_success = True

        for event in events:
            # Fuer jeden Event eine Mini-Phase: contain (wenn Ban/Threat) oder fix
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

        # KI-Plan generieren
        plan = await self.ai_service.generate_coordinated_plan(
            self._build_plan_prompt(events),
            context={'events': [e.to_dict() for e in events]},
        )

        if not plan or not plan.get('phases'):
            logger.error("KI konnte keinen Plan erstellen — Fallback auf Fast-Path")
            return await self._fast_path(events)

        # Phasen ausfuehren
        batch_id = f"batch_{events[0].event_id}" if events else "batch_unknown"
        all_success = True

        for phase in plan['phases']:
            success = await self.executor.execute_phase(
                phase, events, batch_id=batch_id
            )
            if not success:
                all_success = False
                # Bei Fix-Failure: Rollback-Log, aber weitermachen
                logger.warning(f"Phase '{phase.get('name')}' fehlgeschlagen — weiter mit naechster")

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
- recon/verify/monitor sind read-only (keine Aenderungen)
- contain ist fuer sofortige Eindaemmung (IP blocken)
- fix ist fuer dauerhafte Behebung (Config aendern)
- NIEMALS gleichen Fix in mehreren Phasen wiederholen
- Bei SSH-Bans: contain zuerst (IP blocken), dann fix (Jail haerten), dann verify
"""
```

---

## Task 2.3: Fixer-Adapter (bestehende Fixer -> FixProvider)

**Files:**
- Create: `src/integrations/security_engine/fixer_adapters.py`
- Test: `tests/unit/test_fixer_adapters.py`

**Beschreibung:** Adapter-Klassen die bestehende Fixer (Fail2banFixer, TrivyFixer, etc.) als FixProvider wrappen. Die Fixer selbst bleiben unveraendert — nur ihre Schnittstelle wird adaptiert.

### Test

```python
# tests/unit/test_fixer_adapters.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.integrations.security_engine.fixer_adapters import (
    Fail2banFixerAdapter, TrivyFixerAdapter, CrowdSecFixerAdapter, AideFixerAdapter
)
from src.integrations.security_engine.models import BanEvent, Severity, PhaseType


class TestFail2banAdapter:
    @pytest.mark.asyncio
    async def test_no_op_when_config_unchanged(self):
        """Adapter erkennt No-Op bevor Fixer aufgerufen wird"""
        mock_fixer = AsyncMock()
        mock_fixer._get_jail_config = AsyncMock(return_value={
            'maxretry': 3, 'bantime': 3600
        })
        mock_fixer.hardened_config = {'maxretry': 3, 'bantime': 3600, 'findtime': 600}

        adapter = Fail2banFixerAdapter(mock_fixer)
        event = BanEvent(
            source='fail2ban', severity=Severity.HIGH,
            details={'jail': 'sshd'}, event_id='test'
        )
        result = await adapter.execute(
            event,
            strategy={'description': 'harden_config'},
            context={'phase_type': PhaseType.FIX}
        )
        assert result.status == 'no_op'
        mock_fixer.fix.assert_not_called()  # Fixer wurde NICHT aufgerufen

    @pytest.mark.asyncio
    async def test_delegates_when_change_needed(self):
        """Adapter delegiert an Fixer wenn Aenderung noetig"""
        mock_fixer = AsyncMock()
        mock_fixer._get_jail_config = AsyncMock(return_value={
            'maxretry': 5, 'bantime': 600
        })
        mock_fixer.hardened_config = {'maxretry': 3, 'bantime': 3600, 'findtime': 600}
        mock_fixer.fix = AsyncMock(return_value={
            'status': 'success', 'message': 'Jail hardened'
        })

        adapter = Fail2banFixerAdapter(mock_fixer)
        event = BanEvent(
            source='fail2ban', severity=Severity.HIGH,
            details={'jail': 'sshd'}, event_id='test'
        )
        result = await adapter.execute(
            event,
            strategy={'description': 'harden_config'},
            context={'phase_type': PhaseType.FIX}
        )
        assert result.status == 'success'
        mock_fixer.fix.assert_called_once()
```

### Implementation Adapters

```python
# src/integrations/security_engine/fixer_adapters.py
"""
Adapter: Bestehende Fixer -> FixProvider Interface

Wrappen die existierenden Fixer-Klassen (TrivyFixer, Fail2banFixer, etc.)
als FixProvider, inkl. No-Op-Detection wo moeglich.
"""

from __future__ import annotations
import logging
import time
from typing import Any, Dict, Optional

from .models import SecurityEvent, PhaseType, FixResult
from .providers import FixProvider

logger = logging.getLogger('shadowops.fixer_adapters')


class Fail2banFixerAdapter(FixProvider):
    """Adapter fuer bestehenden Fail2banFixer mit No-Op-Detection"""

    def __init__(self, fixer):
        self.fixer = fixer

    async def execute(
        self,
        event: SecurityEvent,
        strategy: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> Optional[FixResult]:
        phase_type = context.get('phase_type', PhaseType.FIX) if context else PhaseType.FIX

        # No-Op Check: Aktuelle Config vs. Ziel-Config vergleichen
        try:
            jail_name = event.details.get('jail', 'sshd')
            current_config = await self.fixer._get_jail_config(jail_name)
            if current_config:
                target = self.fixer.hardened_config
                if (current_config.get('maxretry') == target.get('maxretry')
                        and current_config.get('bantime') == target.get('bantime')):
                    return FixResult.no_op(
                        f"Jail {jail_name} bereits gehaertet "
                        f"(maxretry={target['maxretry']}, bantime={target['bantime']})",
                        phase_type=phase_type,
                    )
        except Exception as e:
            logger.debug(f"No-Op-Check fehlgeschlagen: {e}")

        # Delegiere an Original-Fixer
        start = time.time()
        result = await self.fixer.fix(event.to_dict(), strategy)
        duration = time.time() - start

        if result.get('status') == 'success':
            return FixResult.success(
                result.get('message', 'Fail2ban fix applied'),
                phase_type=phase_type,
                duration_seconds=duration,
            )
        return FixResult.failed(
            result.get('error', 'Fail2ban fix failed'),
            phase_type=phase_type,
            duration_seconds=duration,
        )


class TrivyFixerAdapter(FixProvider):
    """Adapter fuer TrivyFixer"""

    def __init__(self, fixer):
        self.fixer = fixer

    async def execute(
        self,
        event: SecurityEvent,
        strategy: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> Optional[FixResult]:
        phase_type = context.get('phase_type', PhaseType.FIX) if context else PhaseType.FIX
        start = time.time()
        result = await self.fixer.fix(event.to_dict(), strategy)
        duration = time.time() - start

        if result.get('status') == 'success':
            return FixResult.success(
                result.get('message', 'Trivy fix applied'),
                phase_type=phase_type, duration_seconds=duration,
            )
        return FixResult.failed(
            result.get('error', 'Trivy fix failed'),
            phase_type=phase_type, duration_seconds=duration,
        )


class CrowdSecFixerAdapter(FixProvider):
    """Adapter fuer CrowdSecFixer"""

    def __init__(self, fixer):
        self.fixer = fixer

    async def execute(
        self,
        event: SecurityEvent,
        strategy: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> Optional[FixResult]:
        phase_type = context.get('phase_type', PhaseType.FIX) if context else PhaseType.FIX
        start = time.time()
        result = await self.fixer.fix(event.to_dict(), strategy)
        duration = time.time() - start

        if result.get('status') == 'success':
            return FixResult.success(
                result.get('message', 'CrowdSec fix applied'),
                phase_type=phase_type, duration_seconds=duration,
            )
        return FixResult.failed(
            result.get('error', 'CrowdSec fix failed'),
            phase_type=phase_type, duration_seconds=duration,
        )


class AideFixerAdapter(FixProvider):
    """Adapter fuer AideFixer"""

    def __init__(self, fixer):
        self.fixer = fixer

    async def execute(
        self,
        event: SecurityEvent,
        strategy: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> Optional[FixResult]:
        phase_type = context.get('phase_type', PhaseType.FIX) if context else PhaseType.FIX
        start = time.time()
        result = await self.fixer.fix(event.to_dict(), strategy)
        duration = time.time() - start

        if result.get('status') == 'success':
            return FixResult.success(
                result.get('message', 'AIDE fix applied'),
                phase_type=phase_type, duration_seconds=duration,
            )
        return FixResult.failed(
            result.get('error', 'AIDE fix failed'),
            phase_type=phase_type, duration_seconds=duration,
        )
```

---

## Task 2.4: Planner Prompt-Update

**Files:**
- Modify: `src/integrations/orchestrator/planner_mixin.py` (Prompt-Template)

### Prompt-Guidance fuer Phase-Types und Limit

Finde die `_build_coordinated_planning_prompt()` Methode und ergaenze am Ende des Prompts vor dem JSON-Template:

```
## WICHTIGE REGELN FUeR DEN PLAN
- Maximal 3-4 Phasen (weniger ist besser)
- Jede Phase MUSS ein "type" Feld haben: "recon", "contain", "fix", "verify" oder "monitor"
- recon: Read-only Beweissicherung (Status erfassen, Logs sammeln)
- contain: Sofortige Eindaemmung (IP blocken, Service isolieren)
- fix: Dauerhafte Behebung (Config aendern, Packages updaten)
- verify: Pruefen ob Fix wirkt (Status checken, Tests laufen)
- monitor: Nachbeobachtung (nur Logging, keine Aktion)
- NIEMALS den gleichen Fix in mehreren Phasen wiederholen!
- Wenn nur 1-2 Events: contain + verify reicht meistens
```

---

## Event-Struktur und Planning-Regeln (fuer Deep-Scan-Mode)

Die oben definierten Event-Strukturen (`BanEvent`, `VulnEvent`, `ThreatEvent`, `IntegrityEvent`) und der Planning-Prompt mit den Regeln (`maximal 3-4 Phasen`, `type MUSS gesetzt sein`, `recon/contain/fix/verify/monitor Semantik`) gelten **auch fuer den Deep-Scan-Mode**. Der SecurityScanAgent nutzt dieselben Event-Typen und delegiert Fix-Phasen an den `PhaseTypeExecutor` — mit dem Unterschied dass er typischerweise findings-getrieben arbeitet (nicht event-getrieben) und die Phasen ueber die AI-Session generiert werden.

Siehe [deep-scan-mode.md](deep-scan-mode.md) fuer die Adaptive Session-Steuerung und Fix-Phase Integration.
