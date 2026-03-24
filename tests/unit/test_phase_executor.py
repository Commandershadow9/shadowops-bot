"""Tests fuer PhaseTypeExecutor — Dedup, Provider-Chain, Read-only Phasen."""
import pytest
from unittest.mock import AsyncMock

from src.integrations.security_engine.executor import PhaseTypeExecutor
from src.integrations.security_engine.models import BanEvent, PhaseType, FixResult, Severity
from src.integrations.security_engine.registry import FixerRegistry


class TestPhaseTypeExecutor:
    """Tests fuer den PhaseTypeExecutor."""

    def _make_event(self, event_id='test', source='fail2ban'):
        return BanEvent(
            source=source,
            severity=Severity.HIGH,
            details={'ip': '1.2.3.4'},
            event_id=event_id,
        )

    def _make_db(self):
        db = AsyncMock()
        db.record_phase_execution = AsyncMock(return_value=1)
        db.record_fix_attempt = AsyncMock(return_value=1)
        return db

    # ── Read-only Phasen ──────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_recon_phase_read_only(self):
        """Recon-Phase ist read-only, kein Provider-Aufruf."""
        registry = FixerRegistry()
        db = self._make_db()
        executor = PhaseTypeExecutor(registry=registry, db=db)
        event = self._make_event()
        phase = {'name': 'Beweissicherung', 'type': 'recon', 'description': 'Status', 'steps': []}

        result = await executor.execute_phase(phase, [event], batch_id='b1')

        assert result is True
        db.record_phase_execution.assert_called_once()
        db.record_fix_attempt.assert_not_called()

    @pytest.mark.asyncio
    async def test_verify_phase_read_only(self):
        """Verify-Phase ist read-only."""
        registry = FixerRegistry()
        db = self._make_db()
        executor = PhaseTypeExecutor(registry=registry, db=db)
        event = self._make_event()
        phase = {'name': 'Verify', 'type': 'verify', 'description': 'Check', 'steps': []}

        result = await executor.execute_phase(phase, [event], batch_id='b1')
        assert result is True

    @pytest.mark.asyncio
    async def test_monitor_phase_read_only(self):
        """Monitor-Phase ist read-only."""
        registry = FixerRegistry()
        db = self._make_db()
        executor = PhaseTypeExecutor(registry=registry, db=db)
        event = self._make_event()
        phase = {'name': 'Monitor', 'type': 'monitor', 'description': 'Watch', 'steps': []}

        result = await executor.execute_phase(phase, [event], batch_id='b1')
        assert result is True

    # ── Fix/Contain mit Provider ──────────────────────────────────

    @pytest.mark.asyncio
    async def test_fix_phase_calls_provider(self):
        """Fix-Phase ruft Provider auf und gibt True bei Erfolg zurueck."""
        registry = FixerRegistry()
        mock_provider = AsyncMock()
        mock_provider.execute = AsyncMock(return_value=FixResult.success("Fixed", phase_type=PhaseType.FIX))
        registry.register('fail2ban', PhaseType.FIX, mock_provider)
        db = self._make_db()
        executor = PhaseTypeExecutor(registry=registry, db=db)
        event = self._make_event()
        phase = {'name': 'Fix', 'type': 'fix', 'description': 'Haerten', 'steps': []}

        result = await executor.execute_phase(phase, [event], batch_id='b1')

        assert result is True
        mock_provider.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_contain_phase_calls_provider(self):
        """Contain-Phase ruft Provider auf."""
        registry = FixerRegistry()
        mock_provider = AsyncMock()
        mock_provider.execute = AsyncMock(return_value=FixResult.success("Blocked", phase_type=PhaseType.CONTAIN))
        registry.register('fail2ban', PhaseType.CONTAIN, mock_provider)
        db = self._make_db()
        executor = PhaseTypeExecutor(registry=registry, db=db)
        event = self._make_event()
        phase = {'name': 'Contain', 'type': 'contain', 'description': 'Block IP', 'steps': []}

        result = await executor.execute_phase(phase, [event], batch_id='b1')
        assert result is True

    # ── Dedup-Verhalten ───────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_dedup_skips_already_fixed_in_fix_phase(self):
        """Bereits gefixtes Event wird in zweiter Fix-Phase uebersprungen."""
        registry = FixerRegistry()
        mock_provider = AsyncMock()
        mock_provider.execute = AsyncMock(return_value=FixResult.success("Fixed", phase_type=PhaseType.FIX))
        registry.register('fail2ban', PhaseType.FIX, mock_provider)
        db = self._make_db()
        executor = PhaseTypeExecutor(registry=registry, db=db)
        event = self._make_event(event_id='evt1')

        await executor.execute_phase(
            {'name': 'Fix1', 'type': 'fix', 'description': '', 'steps': []},
            [event], batch_id='b1',
        )
        await executor.execute_phase(
            {'name': 'Fix2', 'type': 'fix', 'description': '', 'steps': []},
            [event], batch_id='b1',
        )

        assert mock_provider.execute.call_count == 1

    @pytest.mark.asyncio
    async def test_contain_does_not_dedup(self):
        """Contain-Phase ueberspringt NICHT bei bereits gefixtem Event."""
        registry = FixerRegistry()
        mock_provider = AsyncMock()
        mock_provider.execute = AsyncMock(return_value=FixResult.success("Blocked", phase_type=PhaseType.CONTAIN))
        registry.register('fail2ban', PhaseType.CONTAIN, mock_provider)
        registry.register('fail2ban', PhaseType.FIX, mock_provider)
        db = self._make_db()
        executor = PhaseTypeExecutor(registry=registry, db=db)
        event = self._make_event(event_id='evt1')

        await executor.execute_phase(
            {'name': 'Fix', 'type': 'fix', 'description': '', 'steps': []},
            [event], batch_id='b1',
        )
        await executor.execute_phase(
            {'name': 'Contain', 'type': 'contain', 'description': '', 'steps': []},
            [event], batch_id='b1',
        )

        assert mock_provider.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_no_op_result_does_not_mark_as_fixed(self):
        """no_op-Ergebnis markiert Event NICHT als gefixt."""
        registry = FixerRegistry()
        noop = AsyncMock()
        noop.execute = AsyncMock(return_value=FixResult.no_op("Already correct"))
        registry.register('fail2ban', PhaseType.FIX, noop)
        db = self._make_db()
        executor = PhaseTypeExecutor(registry=registry, db=db)
        event = self._make_event(event_id='evt1')

        await executor.execute_phase(
            {'name': 'Fix1', 'type': 'fix', 'description': '', 'steps': []},
            [event], batch_id='b1',
        )

        assert 'evt1' not in executor._fixed_events

    @pytest.mark.asyncio
    async def test_reset_batch_clears_dedup(self):
        """reset_batch() setzt das Dedup-Tracking zurueck."""
        registry = FixerRegistry()
        mock_provider = AsyncMock()
        mock_provider.execute = AsyncMock(return_value=FixResult.success("Fixed"))
        registry.register('fail2ban', PhaseType.FIX, mock_provider)
        db = self._make_db()
        executor = PhaseTypeExecutor(registry=registry, db=db)
        event = self._make_event(event_id='evt1')

        await executor.execute_phase(
            {'name': 'Fix', 'type': 'fix', 'description': '', 'steps': []},
            [event], batch_id='b1',
        )
        executor.reset_batch()
        await executor.execute_phase(
            {'name': 'Fix', 'type': 'fix', 'description': '', 'steps': []},
            [event], batch_id='b2',
        )

        assert mock_provider.execute.call_count == 2

    # ── Fehlerbehandlung ──────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_no_provider_returns_failed(self):
        """Ohne registrierten Provider schlaegt die Phase fehl."""
        registry = FixerRegistry()
        db = self._make_db()
        executor = PhaseTypeExecutor(registry=registry, db=db)
        event = self._make_event()
        phase = {'name': 'Fix', 'type': 'fix', 'description': '', 'steps': []}

        result = await executor.execute_phase(phase, [event], batch_id='b1')
        assert result is False

    @pytest.mark.asyncio
    async def test_provider_exception_continues_chain(self):
        """Exception in Provider wird gefangen, naechster Provider wird versucht."""
        registry = FixerRegistry()
        bad_provider = AsyncMock()
        bad_provider.execute = AsyncMock(side_effect=RuntimeError("crash"))
        good_provider = AsyncMock()
        good_provider.execute = AsyncMock(return_value=FixResult.success("OK"))
        registry.register('fail2ban', PhaseType.FIX, bad_provider)
        registry.register('fail2ban', PhaseType.FIX, good_provider)
        db = self._make_db()
        executor = PhaseTypeExecutor(registry=registry, db=db)
        event = self._make_event()
        phase = {'name': 'Fix', 'type': 'fix', 'description': '', 'steps': []}

        result = await executor.execute_phase(phase, [event], batch_id='b1')
        assert result is True

    @pytest.mark.asyncio
    async def test_unknown_phase_type_defaults_to_fix(self):
        """Unbekannter Phase-Typ faellt auf 'fix' zurueck."""
        registry = FixerRegistry()
        mock_provider = AsyncMock()
        mock_provider.execute = AsyncMock(return_value=FixResult.success("OK"))
        registry.register('fail2ban', PhaseType.FIX, mock_provider)
        db = self._make_db()
        executor = PhaseTypeExecutor(registry=registry, db=db)
        event = self._make_event()
        phase = {'name': 'Unknown', 'type': 'nonexistent', 'description': '', 'steps': []}

        result = await executor.execute_phase(phase, [event], batch_id='b1')
        assert result is True

    # ── Mehrere Events / Ohne DB ──────────────────────────────────

    @pytest.mark.asyncio
    async def test_multiple_events_in_phase(self):
        """Mehrere Events werden einzeln verarbeitet."""
        registry = FixerRegistry()
        mock = AsyncMock()
        mock.execute = AsyncMock(return_value=FixResult.success("OK"))
        registry.register('fail2ban', PhaseType.FIX, mock)
        db = self._make_db()
        executor = PhaseTypeExecutor(registry=registry, db=db)
        events = [self._make_event(event_id=f'evt_{i}') for i in range(3)]
        phase = {'name': 'Fix', 'type': 'fix', 'description': '', 'steps': []}

        result = await executor.execute_phase(phase, events, batch_id='b1')

        assert result is True
        assert mock.execute.call_count == 3

    @pytest.mark.asyncio
    async def test_works_without_db(self):
        """Executor funktioniert auch ohne DB (db=None)."""
        registry = FixerRegistry()
        mock = AsyncMock()
        mock.execute = AsyncMock(return_value=FixResult.success("OK"))
        registry.register('fail2ban', PhaseType.FIX, mock)
        executor = PhaseTypeExecutor(registry=registry, db=None)
        event = self._make_event()
        phase = {'name': 'Fix', 'type': 'fix', 'description': '', 'steps': []}

        result = await executor.execute_phase(phase, [event], batch_id='b1')
        assert result is True
