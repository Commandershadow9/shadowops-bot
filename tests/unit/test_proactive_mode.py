import pytest
from unittest.mock import AsyncMock
from datetime import datetime, timezone, timedelta
from src.integrations.security_engine.proactive import ProactiveMode, SCAN_AREAS, COVERAGE_GAP_DAYS


class TestCoverageGaps:
    @pytest.mark.asyncio
    async def test_all_areas_without_coverage_tracking(self):
        """Ohne Coverage-Tracking → alle Bereiche als Lücke"""
        db = AsyncMock(spec=[])  # Kein get_scan_coverage
        mode = ProactiveMode(db=db)
        gaps = await mode.get_coverage_gaps()
        assert len(gaps) == len(SCAN_AREAS)
        assert all(g['priority'] == 'low' for g in gaps)

    @pytest.mark.asyncio
    async def test_old_scan_is_gap(self):
        db = AsyncMock()
        old_date = datetime.now(timezone.utc) - timedelta(days=10)
        db.get_scan_coverage = AsyncMock(return_value={'last_checked': old_date})
        mode = ProactiveMode(db=db)
        gaps = await mode.get_coverage_gaps()
        old_gaps = [g for g in gaps if g.get('days_since') and g['days_since'] >= 7]
        assert len(old_gaps) > 0

    @pytest.mark.asyncio
    async def test_recent_scan_no_gap(self):
        db = AsyncMock()
        recent = datetime.now(timezone.utc) - timedelta(days=2)
        db.get_scan_coverage = AsyncMock(return_value={'last_checked': recent})
        mode = ProactiveMode(db=db)
        gaps = await mode.get_coverage_gaps()
        real_gaps = [g for g in gaps if g.get('days_since') and g['days_since'] >= 7]
        assert len(real_gaps) == 0

    @pytest.mark.asyncio
    async def test_never_scanned_is_high_priority(self):
        db = AsyncMock()
        db.get_scan_coverage = AsyncMock(return_value=None)
        mode = ProactiveMode(db=db)
        gaps = await mode.get_coverage_gaps()
        never_scanned = [g for g in gaps if g['last_checked'] is None]
        assert len(never_scanned) == len(SCAN_AREAS)
        assert all(g['priority'] == 'high' for g in never_scanned)

    @pytest.mark.asyncio
    async def test_gaps_sorted_by_priority(self):
        db = AsyncMock(spec=[])
        mode = ProactiveMode(db=db)
        gaps = await mode.get_coverage_gaps()
        priorities = [g['priority'] for g in gaps]
        # Alle gleich (low) → sortiert
        assert priorities == sorted(priorities, key=lambda p: {'high': 0, 'medium': 1, 'low': 2}[p])

    @pytest.mark.asyncio
    async def test_exception_handling(self):
        db = AsyncMock()
        db.get_scan_coverage = AsyncMock(side_effect=RuntimeError("DB down"))
        mode = ProactiveMode(db=db)
        gaps = await mode.get_coverage_gaps()
        # Sollte nicht crashen, leere Gaps
        assert isinstance(gaps, list)


class TestFixEffectiveness:
    @pytest.mark.asyncio
    async def test_good_rate(self):
        db = AsyncMock()
        db.get_success_rate = AsyncMock(return_value=0.9)
        mode = ProactiveMode(db=db)
        stats = await mode.get_fix_effectiveness()
        assert stats['fail2ban']['status'] == 'good'

    @pytest.mark.asyncio
    async def test_warning_rate(self):
        db = AsyncMock()
        db.get_success_rate = AsyncMock(return_value=0.6)
        mode = ProactiveMode(db=db)
        stats = await mode.get_fix_effectiveness()
        assert stats['fail2ban']['status'] == 'warning'

    @pytest.mark.asyncio
    async def test_critical_rate(self):
        db = AsyncMock()
        db.get_success_rate = AsyncMock(return_value=0.3)
        mode = ProactiveMode(db=db)
        stats = await mode.get_fix_effectiveness()
        assert stats['fail2ban']['status'] == 'critical'

    @pytest.mark.asyncio
    async def test_all_sources_checked(self):
        db = AsyncMock()
        db.get_success_rate = AsyncMock(return_value=0.8)
        mode = ProactiveMode(db=db)
        stats = await mode.get_fix_effectiveness()
        for source in ['fail2ban', 'crowdsec', 'trivy', 'aide']:
            assert source in stats


class TestHardeningReport:
    @pytest.mark.asyncio
    async def test_report_structure(self):
        db = AsyncMock(spec=[])
        db.get_success_rate = AsyncMock(return_value=0.8)
        db.get_phase_stats = AsyncMock(return_value={})
        mode = ProactiveMode(db=db)
        report = await mode.generate_hardening_report()
        assert 'coverage_gaps' in report
        assert 'fix_effectiveness' in report
        assert 'recommendations' in report
        assert 'generated_at' in report

    @pytest.mark.asyncio
    async def test_critical_effectiveness_generates_recommendation(self):
        db = AsyncMock(spec=[])
        db.get_success_rate = AsyncMock(return_value=0.2)
        db.get_phase_stats = AsyncMock(return_value={})
        mode = ProactiveMode(db=db)
        report = await mode.generate_hardening_report()
        effectiveness_recs = [r for r in report['recommendations'] if r['category'] == 'effectiveness']
        assert len(effectiveness_recs) > 0

    @pytest.mark.asyncio
    async def test_run_proactive_scan(self):
        db = AsyncMock(spec=[])
        db.get_success_rate = AsyncMock(return_value=0.8)
        db.get_phase_stats = AsyncMock(return_value={})
        mode = ProactiveMode(db=db)
        result = await mode.run_proactive_scan()
        assert 'recommendations' in result


class TestScanAreas:
    def test_scan_areas_defined(self):
        assert len(SCAN_AREAS) >= 6
        assert 'firewall' in SCAN_AREAS
        assert 'ssh' in SCAN_AREAS
        assert 'docker' in SCAN_AREAS

    def test_coverage_gap_days(self):
        assert COVERAGE_GAP_DAYS == 7
