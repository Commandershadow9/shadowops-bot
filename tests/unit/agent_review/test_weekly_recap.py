"""Tests fuer Weekly-Recap (collect + embed rendering)."""
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.integrations.github_integration.agent_review.weekly_recap import (
    WeeklyRecapData, render_weekly_embed,
    REVERT_RATE_RED, REVERT_RATE_YELLOW,
)


# ─────────── Render: Color/Status ───────────

class TestRenderColor:
    def _empty(self, warnings=0):
        return WeeklyRecapData(
            throughput={'jules_delegated': 0, 'manual_tasks': 0,
                        'jules_suggestions': 0, 'released': 0, 'failed': 0},
            reviews_by_agent=[], revert_rates=[],
            queue_status=[], jules_sessions_24h=0, pending_manual=0,
            warnings=warnings,
        )

    def test_no_warnings_green(self):
        e = render_weekly_embed(self._empty(warnings=0))
        assert e.color.value == 0x0e8a16  # gruen
        assert "🟢" in e.title
        assert "gruen" in e.title.lower()

    def test_one_warning_yellow(self):
        e = render_weekly_embed(self._empty(warnings=1))
        assert e.color.value == 0xd4a017  # gelb
        assert "🟡" in e.title

    def test_two_warnings_red(self):
        e = render_weekly_embed(self._empty(warnings=2))
        assert e.color.value == 0xb60205  # rot
        assert "🔴" in e.title

    def test_many_warnings_still_red(self):
        e = render_weekly_embed(self._empty(warnings=7))
        assert e.color.value == 0xb60205
        assert "7 Warnungen" in e.title


# ─────────── Render: Sections ───────────

class TestRenderSections:
    def test_throughput_field(self):
        data = WeeklyRecapData(
            throughput={'jules_delegated': 5, 'manual_tasks': 2,
                        'jules_suggestions': 0, 'released': 6, 'failed': 1},
            reviews_by_agent=[], revert_rates=[],
            queue_status=[], jules_sessions_24h=0, pending_manual=0, warnings=0,
        )
        e = render_weekly_embed(data)
        throughput_field = next(f for f in e.fields if "Throughput" in f.name)
        assert "**5**" in throughput_field.value  # jules_delegated
        assert "**2**" in throughput_field.value  # manual
        assert "**6**" in throughput_field.value  # released

    def test_reviews_by_agent_grouped(self):
        data = WeeklyRecapData(
            throughput={},
            reviews_by_agent=[
                {'agent_type': 'jules', 'verdict': 'approved', 'cnt': 8},
                {'agent_type': 'jules', 'verdict': 'revision_requested', 'cnt': 2},
                {'agent_type': 'seo', 'verdict': 'approved', 'cnt': 3},
            ],
            revert_rates=[], queue_status=[],
            jules_sessions_24h=0, pending_manual=0, warnings=0,
        )
        e = render_weekly_embed(data)
        reviews_field = next(f for f in e.fields if "Reviews" in f.name)
        assert "jules" in reviews_field.value
        assert "8" in reviews_field.value
        assert "2" in reviews_field.value
        assert "seo" in reviews_field.value
        assert "3" in reviews_field.value

    def test_revert_rates_only_with_reverts(self):
        data = WeeklyRecapData(
            throughput={},
            reviews_by_agent=[],
            revert_rates=[
                {'agent_type': 'jules', 'rule_matched': 'good_rule',
                 'total': 10, 'reverted': 0, 'rate': 0.0},
                {'agent_type': 'jules', 'rule_matched': 'bad_rule',
                 'total': 5, 'reverted': 2, 'rate': 40.0},
            ],
            queue_status=[], jules_sessions_24h=0, pending_manual=0, warnings=0,
        )
        e = render_weekly_embed(data)
        revert_field = next((f for f in e.fields if "Revert" in f.name), None)
        assert revert_field is not None
        # Nur die mit Reverts sichtbar
        assert "bad_rule" in revert_field.value
        assert "good_rule" not in revert_field.value
        assert "🔴" in revert_field.value  # 40% = rot

    def test_revert_field_hidden_when_all_clean(self):
        data = WeeklyRecapData(
            throughput={},
            reviews_by_agent=[],
            revert_rates=[
                {'agent_type': 'jules', 'rule_matched': 'good_rule',
                 'total': 10, 'reverted': 0, 'rate': 0.0},
            ],
            queue_status=[], jules_sessions_24h=0, pending_manual=0, warnings=0,
        )
        e = render_weekly_embed(data)
        field_names = [f.name for f in e.fields]
        assert not any("Revert" in n for n in field_names)

    def test_jules_limits_red_at_high_count(self):
        data = WeeklyRecapData(
            throughput={}, reviews_by_agent=[], revert_rates=[],
            queue_status=[], jules_sessions_24h=95, pending_manual=0, warnings=0,
        )
        e = render_weekly_embed(data)
        jules_field = next(f for f in e.fields if "Jules-API" in f.name)
        assert "🔴" in jules_field.value
        assert "**95 / 100**" in jules_field.value

    def test_jules_limits_green_at_low_count(self):
        data = WeeklyRecapData(
            throughput={}, reviews_by_agent=[], revert_rates=[],
            queue_status=[], jules_sessions_24h=5, pending_manual=0, warnings=0,
        )
        e = render_weekly_embed(data)
        jules_field = next(f for f in e.fields if "Jules-API" in f.name)
        assert "✅" in jules_field.value

    def test_pending_merges_yellow_at_many(self):
        data = WeeklyRecapData(
            throughput={}, reviews_by_agent=[], revert_rates=[],
            queue_status=[], jules_sessions_24h=0, pending_manual=15, warnings=0,
        )
        e = render_weekly_embed(data)
        pending_field = next(f for f in e.fields if "Pending" in f.name)
        assert "🟡" in pending_field.value
        assert "**15**" in pending_field.value

    def test_queue_status_shown(self):
        data = WeeklyRecapData(
            throughput={}, reviews_by_agent=[], revert_rates=[],
            queue_status=[
                {'status': 'queued', 'cnt': 3, 'oldest_hours': 0.5},
                {'status': 'released', 'cnt': 42, 'oldest_hours': 168.0},
            ],
            jules_sessions_24h=0, pending_manual=0, warnings=0,
        )
        e = render_weekly_embed(data)
        queue_field = next(f for f in e.fields if "Queue" in f.name)
        assert "queued" in queue_field.value
        assert "3 Tasks" in queue_field.value
        assert "released" in queue_field.value

    def test_queue_red_when_stuck(self):
        data = WeeklyRecapData(
            throughput={}, reviews_by_agent=[], revert_rates=[],
            queue_status=[
                {'status': 'queued', 'cnt': 5, 'oldest_hours': 3.5},  # > 2h red
            ],
            jules_sessions_24h=0, pending_manual=0, warnings=1,
        )
        e = render_weekly_embed(data)
        queue_field = next(f for f in e.fields if "Queue" in f.name)
        assert "🔴" in queue_field.value

    def test_footer_present(self):
        data = WeeklyRecapData(
            throughput={}, reviews_by_agent=[], revert_rates=[],
            queue_status=[], jules_sessions_24h=0, pending_manual=0, warnings=0,
        )
        e = render_weekly_embed(data)
        assert "Weekly Review" in e.footer.text
        assert "Fr 18:00" in e.footer.text


# ─────────── Constants ───────────

class TestConstants:
    def test_thresholds_reasonable(self):
        assert REVERT_RATE_YELLOW == 10.0
        assert REVERT_RATE_RED == 20.0
        assert REVERT_RATE_YELLOW < REVERT_RATE_RED


# ─────────── Collect (mit FakePool) ───────────

class TestCollectData:
    @pytest.mark.asyncio
    async def test_none_pool_returns_empty_data(self):
        from src.integrations.github_integration.agent_review.weekly_recap import (
            collect_weekly_recap_data,
        )
        data = await collect_weekly_recap_data(pool=None)
        assert data.throughput == {}
        assert data.reviews_by_agent == []
        assert data.jules_sessions_24h == 0
        assert data.warnings == 0
