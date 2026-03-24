import pytest
from unittest.mock import AsyncMock, MagicMock, PropertyMock
from src.integrations.security_engine.fixer_adapters import (
    Fail2banFixerAdapter, TrivyFixerAdapter, CrowdSecFixerAdapter, AideFixerAdapter
)
from src.integrations.security_engine.models import (
    BanEvent, VulnEvent, IntegrityEvent, ThreatEvent, Severity, PhaseType
)


class TestFail2banAdapter:
    def _make_event(self):
        return BanEvent(
            source='fail2ban', severity=Severity.HIGH,
            details={'jail': 'sshd', 'ip': '1.2.3.4'}, event_id='test',
        )

    @pytest.mark.asyncio
    async def test_no_op_when_config_unchanged(self):
        mock_fixer = AsyncMock()
        mock_fixer._get_jail_config = AsyncMock(
            return_value={'maxretry': 3, 'bantime': 3600},
        )
        mock_fixer.hardened_config = {'maxretry': 3, 'bantime': 3600, 'findtime': 600}
        adapter = Fail2banFixerAdapter(mock_fixer)
        result = await adapter.execute(
            self._make_event(),
            strategy={'description': 'harden'},
            context={'phase_type': PhaseType.FIX},
        )
        assert result.status == 'no_op'
        mock_fixer.fix.assert_not_called()

    @pytest.mark.asyncio
    async def test_delegates_when_change_needed(self):
        mock_fixer = AsyncMock()
        mock_fixer._get_jail_config = AsyncMock(
            return_value={'maxretry': 5, 'bantime': 600},
        )
        mock_fixer.hardened_config = {'maxretry': 3, 'bantime': 3600, 'findtime': 600}
        mock_fixer.fix = AsyncMock(
            return_value={'status': 'success', 'message': 'Jail hardened'},
        )
        adapter = Fail2banFixerAdapter(mock_fixer)
        result = await adapter.execute(
            self._make_event(),
            strategy={'description': 'harden'},
            context={'phase_type': PhaseType.FIX},
        )
        assert result.status == 'success'
        mock_fixer.fix.assert_called_once()

    @pytest.mark.asyncio
    async def test_handles_fix_failure(self):
        mock_fixer = AsyncMock()
        mock_fixer._get_jail_config = AsyncMock(
            return_value={'maxretry': 5, 'bantime': 600},
        )
        mock_fixer.hardened_config = {'maxretry': 3, 'bantime': 3600, 'findtime': 600}
        mock_fixer.fix = AsyncMock(
            return_value={'status': 'failed', 'error': 'Permission denied'},
        )
        adapter = Fail2banFixerAdapter(mock_fixer)
        result = await adapter.execute(
            self._make_event(), strategy={},
            context={'phase_type': PhaseType.FIX},
        )
        assert result.status == 'failed'
        assert 'Permission denied' in result.error

    @pytest.mark.asyncio
    async def test_no_op_check_exception_continues(self):
        mock_fixer = AsyncMock()
        mock_fixer._get_jail_config = AsyncMock(side_effect=RuntimeError("DB down"))
        mock_fixer.fix = AsyncMock(return_value={'status': 'success', 'message': 'OK'})
        adapter = Fail2banFixerAdapter(mock_fixer)
        result = await adapter.execute(
            self._make_event(), strategy={},
            context={'phase_type': PhaseType.FIX},
        )
        assert result.status == 'success'

    @pytest.mark.asyncio
    async def test_works_without_context(self):
        mock_fixer = AsyncMock()
        mock_fixer._get_jail_config = AsyncMock(return_value=None)
        mock_fixer.fix = AsyncMock(return_value={'status': 'success', 'message': 'OK'})
        adapter = Fail2banFixerAdapter(mock_fixer)
        result = await adapter.execute(
            self._make_event(), strategy={}, context=None,
        )
        assert result.status == 'success'


class TestTrivyAdapter:
    @pytest.mark.asyncio
    async def test_success(self):
        mock_fixer = AsyncMock()
        mock_fixer.fix = AsyncMock(
            return_value={'status': 'success', 'message': 'Patched'},
        )
        adapter = TrivyFixerAdapter(mock_fixer)
        event = VulnEvent(
            source='trivy', severity=Severity.HIGH,
            details={'cve': 'CVE-2026-1'}, event_id='t1',
        )
        result = await adapter.execute(
            event, strategy={}, context={'phase_type': PhaseType.FIX},
        )
        assert result.status == 'success'

    @pytest.mark.asyncio
    async def test_failure(self):
        mock_fixer = AsyncMock()
        mock_fixer.fix = AsyncMock(
            return_value={'status': 'failed', 'error': 'Build failed'},
        )
        adapter = TrivyFixerAdapter(mock_fixer)
        event = VulnEvent(
            source='trivy', severity=Severity.HIGH,
            details={}, event_id='t1',
        )
        result = await adapter.execute(
            event, strategy={}, context={'phase_type': PhaseType.FIX},
        )
        assert result.status == 'failed'


class TestCrowdSecAdapter:
    @pytest.mark.asyncio
    async def test_success(self):
        mock_fixer = AsyncMock()
        mock_fixer.fix = AsyncMock(
            return_value={'status': 'success', 'message': 'IP blocked'},
        )
        adapter = CrowdSecFixerAdapter(mock_fixer)
        event = ThreatEvent(
            source='crowdsec', severity=Severity.HIGH,
            details={'ip': '5.6.7.8'}, event_id='cs1',
        )
        result = await adapter.execute(
            event, strategy={}, context={'phase_type': PhaseType.CONTAIN},
        )
        assert result.status == 'success'


class TestAideAdapter:
    @pytest.mark.asyncio
    async def test_success(self):
        mock_fixer = AsyncMock()
        mock_fixer.fix = AsyncMock(
            return_value={'status': 'success', 'message': 'File restored'},
        )
        adapter = AideFixerAdapter(mock_fixer)
        event = IntegrityEvent(
            source='aide', severity=Severity.MEDIUM,
            details={'path': '/etc/passwd'}, event_id='a1',
        )
        result = await adapter.execute(
            event, strategy={}, context={'phase_type': PhaseType.FIX},
        )
        assert result.status == 'success'
