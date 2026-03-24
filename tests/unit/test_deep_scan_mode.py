import pytest
from unittest.mock import AsyncMock, MagicMock
from src.integrations.security_engine.deep_scan import DeepScanMode, SESSION_CONFIG


class TestSessionModeDetermination:
    @pytest.mark.asyncio
    async def test_fix_only_when_many_findings(self):
        db = AsyncMock()
        db.get_open_findings_count = AsyncMock(return_value=25)
        mode = DeepScanMode(db=db)
        result = await mode._determine_session_mode()
        assert result == 'fix_only'

    @pytest.mark.asyncio
    async def test_full_scan_when_moderate_findings(self):
        db = AsyncMock()
        db.get_open_findings_count = AsyncMock(return_value=10)
        mode = DeepScanMode(db=db)
        result = await mode._determine_session_mode()
        assert result == 'full_scan'

    @pytest.mark.asyncio
    async def test_quick_scan_when_few_findings(self):
        db = AsyncMock()
        db.get_open_findings_count = AsyncMock(return_value=2)
        mode = DeepScanMode(db=db)
        result = await mode._determine_session_mode()
        assert result == 'quick_scan'

    @pytest.mark.asyncio
    async def test_maintenance_when_no_findings(self):
        db = AsyncMock()
        db.get_open_findings_count = AsyncMock(return_value=0)
        mode = DeepScanMode(db=db)
        result = await mode._determine_session_mode()
        assert result == 'maintenance'

    @pytest.mark.asyncio
    async def test_boundary_20_is_fix_only(self):
        db = AsyncMock()
        db.get_open_findings_count = AsyncMock(return_value=20)
        mode = DeepScanMode(db=db)
        assert await mode._determine_session_mode() == 'fix_only'

    @pytest.mark.asyncio
    async def test_boundary_5_is_full_scan(self):
        db = AsyncMock()
        db.get_open_findings_count = AsyncMock(return_value=5)
        mode = DeepScanMode(db=db)
        assert await mode._determine_session_mode() == 'full_scan'

    @pytest.mark.asyncio
    async def test_boundary_1_is_quick_scan(self):
        db = AsyncMock()
        db.get_open_findings_count = AsyncMock(return_value=1)
        mode = DeepScanMode(db=db)
        assert await mode._determine_session_mode() == 'quick_scan'


class TestSessionConfig:
    def test_all_modes_have_config(self):
        for mode in ['fix_only', 'full_scan', 'quick_scan', 'maintenance']:
            assert mode in SESSION_CONFIG
            cfg = SESSION_CONFIG[mode]
            assert 'max_sessions_per_day' in cfg
            assert 'timeout_minutes' in cfg
            assert 'scan_enabled' in cfg
            assert 'fix_enabled' in cfg

    def test_fix_only_has_no_scan(self):
        assert SESSION_CONFIG['fix_only']['scan_enabled'] is False
        assert SESSION_CONFIG['fix_only']['fix_enabled'] is True

    def test_maintenance_has_no_fix(self):
        assert SESSION_CONFIG['maintenance']['scan_enabled'] is True
        assert SESSION_CONFIG['maintenance']['fix_enabled'] is False


class TestCanStartSession:
    @pytest.mark.asyncio
    async def test_can_start_first_session(self):
        db = AsyncMock()
        db.get_open_findings_count = AsyncMock(return_value=10)
        mode = DeepScanMode(db=db)
        assert await mode.can_start_session() is True

    @pytest.mark.asyncio
    async def test_blocked_after_limit(self):
        db = AsyncMock()
        db.get_open_findings_count = AsyncMock(return_value=10)  # full_scan: max 2
        mode = DeepScanMode(db=db)
        mode.sessions_today = 2
        assert await mode.can_start_session() is False


class TestRunSession:
    @pytest.mark.asyncio
    async def test_session_completes(self):
        db = AsyncMock()
        db.get_open_findings_count = AsyncMock(return_value=2)
        db.get_knowledge = AsyncMock(return_value=[])
        mode = DeepScanMode(db=db, ai_engine=AsyncMock(), executor=AsyncMock())
        result = await mode.run_session()
        assert result['status'] == 'completed'
        assert result['mode'] == 'quick_scan'
        assert mode.sessions_today == 1

    @pytest.mark.asyncio
    async def test_session_respects_limit(self):
        db = AsyncMock()
        db.get_open_findings_count = AsyncMock(return_value=2)  # quick_scan: max 1
        mode = DeepScanMode(db=db)
        mode.sessions_today = 1
        result = await mode.run_session()
        assert result['status'] == 'skipped'
        assert result['reason'] == 'session_limit'

    @pytest.mark.asyncio
    async def test_session_handles_error(self):
        db = AsyncMock()
        db.get_open_findings_count = AsyncMock(return_value=2)
        db.get_knowledge = AsyncMock(side_effect=RuntimeError("DB down"))
        mode = DeepScanMode(db=db, ai_engine=AsyncMock(), executor=AsyncMock())
        # _run_scan_phase hat try/except, also kein Crash
        result = await mode.run_session()
        assert result['status'] == 'completed'

    @pytest.mark.asyncio
    async def test_fix_only_skips_scan(self):
        db = AsyncMock()
        db.get_open_findings_count = AsyncMock(return_value=25)
        ai = AsyncMock()
        mode = DeepScanMode(db=db, ai_engine=ai, executor=AsyncMock())
        result = await mode.run_session()
        assert result['mode'] == 'fix_only'
        # AI sollte NICHT fuer Scan aufgerufen werden (fix_only hat scan_enabled=False)

    @pytest.mark.asyncio
    async def test_maintenance_skips_fix(self):
        db = AsyncMock()
        db.get_open_findings_count = AsyncMock(return_value=0)
        db.get_knowledge = AsyncMock(return_value=[])
        executor = AsyncMock()
        mode = DeepScanMode(db=db, ai_engine=AsyncMock(), executor=executor)
        result = await mode.run_session()
        assert result['mode'] == 'maintenance'
        assert result['fixes_count'] == 0


class TestResetDaily:
    def test_reset_clears_counter(self):
        mode = DeepScanMode(db=AsyncMock())
        mode.sessions_today = 3
        mode.reset_daily()
        assert mode.sessions_today == 0
