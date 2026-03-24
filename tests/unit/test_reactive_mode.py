import pytest
from unittest.mock import AsyncMock, MagicMock

from src.integrations.security_engine.reactive import ReactiveMode, BATCH_PLAN_THRESHOLD
from src.integrations.security_engine.models import BanEvent, VulnEvent, Severity, FixResult, PhaseType


class TestReactiveMode:
    def _make_ban(self, event_id='f2b_001', ip='1.2.3.4'):
        return BanEvent(source='fail2ban', severity=Severity.HIGH, details={'ip': ip}, event_id=event_id)

    def _make_vuln(self, event_id='trivy_001'):
        return VulnEvent(source='trivy', severity=Severity.HIGH, details={'cve': 'CVE-2026-1'}, event_id=event_id)

    def _make_critical_ban(self, event_id='f2b_crit'):
        return BanEvent(source='fail2ban', severity=Severity.CRITICAL, details={'ip': '9.9.9.9'}, event_id=event_id)

    def _make_db(self):
        db = AsyncMock()
        db.claim_event = AsyncMock(return_value=True)
        db.release_event = AsyncMock()
        db.record_fix_attempt = AsyncMock(return_value=1)
        return db

    def _make_executor(self):
        executor = AsyncMock()
        executor.execute_phase = AsyncMock(return_value=True)
        executor.reset_batch = MagicMock()
        return executor

    @pytest.mark.asyncio
    async def test_fast_path_single_event(self):
        db = self._make_db()
        executor = self._make_executor()
        mode = ReactiveMode(db=db, executor=executor, ai_service=None)
        result = await mode.handle_events([self._make_ban()])
        assert result is True
        executor.execute_phase.assert_called_once()
        # Kein KI-Call
        executor.reset_batch.assert_called_once()

    @pytest.mark.asyncio
    async def test_fast_path_two_events(self):
        db = self._make_db()
        executor = self._make_executor()
        mode = ReactiveMode(db=db, executor=executor, ai_service=None)
        events = [self._make_ban('e1'), self._make_ban('e2')]
        result = await mode.handle_events(events)
        assert result is True
        assert executor.execute_phase.call_count == 2

    @pytest.mark.asyncio
    async def test_fast_path_ban_uses_contain(self):
        """Ban-Events (nicht persistent) -> contain statt fix"""
        db = self._make_db()
        executor = self._make_executor()
        mode = ReactiveMode(db=db, executor=executor, ai_service=None)
        await mode.handle_events([self._make_ban()])
        phase_arg = executor.execute_phase.call_args[0][0]
        assert phase_arg['type'] == 'contain'

    @pytest.mark.asyncio
    async def test_fast_path_vuln_uses_fix(self):
        """Vuln-Events (persistent) -> fix statt contain"""
        db = self._make_db()
        executor = self._make_executor()
        mode = ReactiveMode(db=db, executor=executor, ai_service=None)
        await mode.handle_events([self._make_vuln()])
        phase_arg = executor.execute_phase.call_args[0][0]
        assert phase_arg['type'] == 'fix'

    @pytest.mark.asyncio
    async def test_planned_path_three_events(self):
        """3+ Events -> KI-Plan"""
        db = self._make_db()
        executor = self._make_executor()
        ai = AsyncMock()
        ai.generate_coordinated_plan = AsyncMock(return_value={
            'description': 'Test', 'confidence': 0.9,
            'phases': [
                {'name': 'Contain', 'type': 'contain', 'description': 'Block', 'steps': []},
                {'name': 'Verify', 'type': 'verify', 'description': 'Check', 'steps': []},
            ],
            'rollback_plan': 'Rollback'
        })
        mode = ReactiveMode(db=db, executor=executor, ai_service=ai)
        events = [self._make_ban(f'e{i}') for i in range(3)]
        result = await mode.handle_events(events)
        assert result is True
        ai.generate_coordinated_plan.assert_called_once()
        assert executor.execute_phase.call_count == 2  # 2 Phasen im Plan

    @pytest.mark.asyncio
    async def test_critical_triggers_planned_path(self):
        """CRITICAL Event -> immer KI-Plan, auch bei nur 1 Event"""
        db = self._make_db()
        executor = self._make_executor()
        ai = AsyncMock()
        ai.generate_coordinated_plan = AsyncMock(return_value={
            'description': 'Critical', 'confidence': 0.95,
            'phases': [{'name': 'Fix', 'type': 'fix', 'description': 'Fix', 'steps': []}],
            'rollback_plan': 'RB'
        })
        mode = ReactiveMode(db=db, executor=executor, ai_service=ai)
        result = await mode.handle_events([self._make_critical_ban()])
        assert result is True
        ai.generate_coordinated_plan.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_already_claimed(self):
        db = self._make_db()
        db.claim_event = AsyncMock(return_value=False)
        executor = self._make_executor()
        mode = ReactiveMode(db=db, executor=executor, ai_service=None)
        result = await mode.handle_events([self._make_ban()])
        assert result is True
        executor.execute_phase.assert_not_called()

    @pytest.mark.asyncio
    async def test_partial_claim(self):
        """Nur 1 von 2 Events claimbar"""
        db = self._make_db()
        db.claim_event = AsyncMock(side_effect=[True, False])
        executor = self._make_executor()
        mode = ReactiveMode(db=db, executor=executor, ai_service=None)
        events = [self._make_ban('e1'), self._make_ban('e2')]
        result = await mode.handle_events(events)
        assert result is True
        executor.execute_phase.assert_called_once()

    @pytest.mark.asyncio
    async def test_ki_plan_empty_fallback(self):
        """KI liefert leeren Plan -> Fallback auf Fast-Path"""
        db = self._make_db()
        executor = self._make_executor()
        ai = AsyncMock()
        ai.generate_coordinated_plan = AsyncMock(return_value={'phases': []})
        mode = ReactiveMode(db=db, executor=executor, ai_service=ai)
        events = [self._make_ban(f'e{i}') for i in range(3)]
        result = await mode.handle_events(events)
        assert result is True
        assert executor.execute_phase.call_count == 3  # Fast-Path fuer alle 3

    @pytest.mark.asyncio
    async def test_no_ai_service_fallback(self):
        """Kein AI-Service -> Fast-Path auch bei 3+ Events"""
        db = self._make_db()
        executor = self._make_executor()
        mode = ReactiveMode(db=db, executor=executor, ai_service=None)
        events = [self._make_ban(f'e{i}') for i in range(5)]
        result = await mode.handle_events(events)
        assert result is True
        assert executor.execute_phase.call_count == 5

    @pytest.mark.asyncio
    async def test_releases_events_on_success(self):
        db = self._make_db()
        executor = self._make_executor()
        mode = ReactiveMode(db=db, executor=executor, ai_service=None)
        await mode.handle_events([self._make_ban()])
        db.release_event.assert_called_once()

    @pytest.mark.asyncio
    async def test_releases_events_on_failure(self):
        """Events werden auch bei Fehlern freigegeben"""
        db = self._make_db()
        executor = self._make_executor()
        executor.execute_phase = AsyncMock(side_effect=RuntimeError("crash"))
        mode = ReactiveMode(db=db, executor=executor, ai_service=None)
        with pytest.raises(RuntimeError):
            await mode.handle_events([self._make_ban()])
        db.release_event.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_events_returns_true(self):
        db = self._make_db()
        executor = self._make_executor()
        mode = ReactiveMode(db=db, executor=executor, ai_service=None)
        result = await mode.handle_events([])
        assert result is True

    @pytest.mark.asyncio
    async def test_build_plan_prompt(self):
        mode = ReactiveMode(db=AsyncMock(), executor=AsyncMock(), ai_service=None)
        event = self._make_ban()
        prompt = mode._build_plan_prompt([event])
        assert 'FAIL2BAN' in prompt
        assert 'contain' in prompt
        assert 'verify' in prompt
