"""Tests fuer FixProvider ABC, NoOpProvider, BashFixProvider und FixerRegistry."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from src.integrations.security_engine.models import BanEvent, PhaseType, FixResult, Severity
from src.integrations.security_engine.providers import FixProvider, NoOpProvider, BashFixProvider
from src.integrations.security_engine.registry import FixerRegistry


class TestNoOpProvider:
    @pytest.mark.asyncio
    async def test_detects_no_change(self):
        provider = NoOpProvider()
        event = BanEvent(source='fail2ban', severity=Severity.HIGH, details={'ip': '1.2.3.4'}, event_id='test')
        result = await provider.execute(
            event,
            strategy={'description': 'harden'},
            context={'current_config': {'maxretry': 3}, 'target_config': {'maxretry': 3}},
        )
        assert result.status == 'no_op'
        assert result.is_success is True

    @pytest.mark.asyncio
    async def test_detects_change_needed(self):
        provider = NoOpProvider()
        event = BanEvent(source='fail2ban', severity=Severity.HIGH, details={'ip': '1.2.3.4'}, event_id='test')
        result = await provider.execute(
            event,
            strategy={'description': 'harden'},
            context={'current_config': {'maxretry': 5}, 'target_config': {'maxretry': 3}},
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_no_context_returns_none(self):
        provider = NoOpProvider()
        event = BanEvent(source='fail2ban', severity=Severity.HIGH, details={}, event_id='test')
        result = await provider.execute(event, strategy={}, context=None)
        assert result is None

    @pytest.mark.asyncio
    async def test_missing_configs_returns_none(self):
        provider = NoOpProvider()
        event = BanEvent(source='fail2ban', severity=Severity.HIGH, details={}, event_id='test')
        result = await provider.execute(event, strategy={}, context={'current_config': None})
        assert result is None


class TestBashFixProvider:
    @pytest.mark.asyncio
    async def test_executes_commands(self):
        mock_executor = AsyncMock()
        mock_executor.execute = AsyncMock(return_value={'success': True})
        provider = BashFixProvider(mock_executor)
        event = BanEvent(source='fail2ban', severity=Severity.HIGH, details={}, event_id='test')
        result = await provider.execute(
            event,
            strategy={'commands': ['cmd1', 'cmd2'], 'description': 'Test fix'},
            context={'phase_type': PhaseType.FIX},
        )
        assert result.status == 'success'
        assert mock_executor.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_no_commands_returns_none(self):
        provider = BashFixProvider(AsyncMock())
        event = BanEvent(source='fail2ban', severity=Severity.HIGH, details={}, event_id='test')
        result = await provider.execute(event, strategy={}, context={})
        assert result is None

    @pytest.mark.asyncio
    async def test_command_failure(self):
        mock_executor = AsyncMock()
        mock_executor.execute = AsyncMock(return_value={'success': False, 'error': 'Permission denied'})
        provider = BashFixProvider(mock_executor)
        event = BanEvent(source='fail2ban', severity=Severity.HIGH, details={}, event_id='test')
        result = await provider.execute(
            event,
            strategy={'commands': ['bad_cmd']},
            context={'phase_type': PhaseType.FIX},
        )
        assert result.status == 'failed'
        assert 'Permission denied' in result.error


class TestFixerRegistry:
    def test_register_and_lookup(self):
        registry = FixerRegistry()
        mock = MagicMock(spec=FixProvider)
        registry.register('fail2ban', PhaseType.FIX, mock)
        providers = registry.get_providers('fail2ban', PhaseType.FIX)
        assert mock in providers

    def test_fallback_to_source_only(self):
        registry = FixerRegistry()
        mock = MagicMock(spec=FixProvider)
        registry.register('fail2ban', None, mock)
        providers = registry.get_providers('fail2ban', PhaseType.CONTAIN)
        assert mock in providers

    def test_no_op_always_first(self):
        registry = FixerRegistry()
        noop = NoOpProvider()
        fixer = MagicMock(spec=FixProvider)
        registry.register('fail2ban', PhaseType.FIX, fixer)
        registry.register_noop(noop)
        providers = registry.get_providers('fail2ban', PhaseType.FIX)
        assert providers[0] is noop

    def test_empty_registry_returns_empty(self):
        registry = FixerRegistry()
        providers = registry.get_providers('unknown', PhaseType.FIX)
        assert providers == []

    def test_list_registered(self):
        registry = FixerRegistry()
        registry.register('fail2ban', PhaseType.FIX, NoOpProvider())
        result = registry.list_registered()
        assert 'fail2ban/fix' in result

    def test_noop_in_empty_registry(self):
        registry = FixerRegistry()
        registry.register_noop(NoOpProvider())
        providers = registry.get_providers('anything', PhaseType.FIX)
        assert len(providers) == 1
        assert isinstance(providers[0], NoOpProvider)
