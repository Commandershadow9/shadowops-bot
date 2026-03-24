import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.integrations.security_engine.engine import SecurityEngine
from src.integrations.security_engine.models import BanEvent, VulnEvent, Severity, PhaseType, FixResult


class TestSecurityEngine:
    def _make_engine(self):
        """Engine ohne DB fuer Unit-Tests"""
        engine = SecurityEngine(db_dsn=None)
        engine.db = AsyncMock()
        engine.executor = AsyncMock()
        engine.reactive = AsyncMock()
        engine.reactive.handle_events = AsyncMock(return_value=True)
        return engine

    def _make_ban(self, event_id='test'):
        return BanEvent(source='fail2ban', severity=Severity.HIGH, details={'ip': '1.2.3.4'}, event_id=event_id)

    @pytest.mark.asyncio
    async def test_handle_single_event(self):
        engine = self._make_engine()
        result = await engine.handle_security_event(self._make_ban())
        assert result is True
        engine.reactive.handle_events.assert_called_once()
        assert engine._events_processed == 1

    @pytest.mark.asyncio
    async def test_handle_event_batch(self):
        engine = self._make_engine()
        events = [self._make_ban(f'e{i}') for i in range(3)]
        result = await engine.handle_event_batch(events)
        assert result is True
        assert engine._events_processed == 3

    @pytest.mark.asyncio
    async def test_circuit_breaker_blocks(self):
        engine = self._make_engine()
        # Oeffne Circuit Breaker
        for _ in range(5):
            engine.circuit_breaker.record_failure('test')
        result = await engine.handle_security_event(self._make_ban())
        assert result is False
        assert engine._events_skipped == 1
        engine.reactive.handle_events.assert_not_called()

    @pytest.mark.asyncio
    async def test_circuit_breaker_batch_blocks(self):
        engine = self._make_engine()
        for _ in range(5):
            engine.circuit_breaker.record_failure('test')
        events = [self._make_ban(f'e{i}') for i in range(3)]
        result = await engine.handle_event_batch(events)
        assert result is False
        assert engine._events_skipped == 3

    @pytest.mark.asyncio
    async def test_failure_records_in_circuit_breaker(self):
        engine = self._make_engine()
        engine.reactive.handle_events = AsyncMock(return_value=False)
        await engine.handle_security_event(self._make_ban())
        assert engine.circuit_breaker.failure_count == 1

    @pytest.mark.asyncio
    async def test_success_records_in_circuit_breaker(self):
        engine = self._make_engine()
        engine.circuit_breaker.record_failure('fail2ban')
        await engine.handle_security_event(self._make_ban())
        assert engine.circuit_breaker.failure_count == 0

    @pytest.mark.asyncio
    async def test_exception_calls_on_fix_failed(self):
        engine = self._make_engine()
        engine.reactive.handle_events = AsyncMock(side_effect=RuntimeError("crash"))
        engine.on_fix_failed = AsyncMock()
        result = await engine.handle_security_event(self._make_ban())
        assert result is False
        engine.on_fix_failed.assert_called_once()

    def test_register_existing_fixers(self):
        engine = SecurityEngine(db_dsn=None)
        mock_f2b = MagicMock()
        mock_trivy = MagicMock()
        mock_cs = MagicMock()
        mock_aide = MagicMock()
        engine.register_existing_fixers(
            fail2ban_fixer=mock_f2b, trivy_fixer=mock_trivy,
            crowdsec_fixer=mock_cs, aide_fixer=mock_aide
        )
        registered = engine.registry.list_registered()
        assert 'fail2ban/*' in registered
        assert 'trivy/*' in registered
        assert 'crowdsec/*' in registered
        assert 'aide/*' in registered

    def test_register_single_fixer(self):
        engine = SecurityEngine(db_dsn=None)
        mock_provider = MagicMock()
        engine.register_fixer('custom', PhaseType.FIX, mock_provider)
        providers = engine.registry.get_providers('custom', PhaseType.FIX)
        assert len(providers) == 2  # NoOp + custom

    def test_get_stats(self):
        engine = SecurityEngine(db_dsn=None)
        stats = engine.get_stats()
        assert stats['events_processed'] == 0
        assert stats['events_skipped'] == 0
        assert 'circuit_breaker' in stats
        assert 'registered_fixers' in stats

    @pytest.mark.asyncio
    async def test_shutdown(self):
        engine = SecurityEngine(db_dsn=None)
        engine.db = AsyncMock()
        await engine.shutdown()
        engine.db.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_shutdown_without_db(self):
        engine = SecurityEngine(db_dsn=None)
        await engine.shutdown()  # Kein Fehler

    def test_noop_registered_by_default(self):
        engine = SecurityEngine(db_dsn=None)
        providers = engine.registry.get_providers('anything', PhaseType.FIX)
        assert len(providers) == 1  # Nur NoOp
        from src.integrations.security_engine.providers import NoOpProvider
        assert isinstance(providers[0], NoOpProvider)

    @pytest.mark.asyncio
    async def test_batch_exception_caught(self):
        engine = self._make_engine()
        engine.reactive.handle_events = AsyncMock(side_effect=RuntimeError("batch crash"))
        result = await engine.handle_event_batch([self._make_ban()])
        assert result is False
