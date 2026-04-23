"""Tests fuer Daily-Digest (Rendering + Aggregate-Collect)."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.integrations.github_integration.agent_review.daily_digest import (
    DigestData, render_digest, collect_digest_data,
    _render_reviews_section, _render_auto_merge_section,
    _render_queue_section, _render_trend_section, _render_pending_section,
)

pytestmark = pytest.mark.asyncio


# ─────────── Rendering: Empty States ───────────

class TestEmptyRendering:
    def _empty(self):
        return DigestData(
            reviews_24h=[], auto_merges_24h={"total": 0, "reverted": 0, "pending": 0},
            queue_status={}, pending_manual_merges=0, revert_trend_7d=[],
        )

    def test_empty_digest_still_renders(self):
        out = render_digest(self._empty())
        assert "Daily Digest" in out
        assert "Keine Reviews" in out
        assert "Keine Auto-Merges" in out

    def test_reviews_section_empty(self):
        out = "\n".join(_render_reviews_section([]))
        assert "Keine Reviews" in out

    def test_auto_merge_section_empty(self):
        out = "\n".join(_render_auto_merge_section({"total": 0}))
        assert "Keine Auto-Merges" in out

    def test_queue_section_empty(self):
        out = "\n".join(_render_queue_section({}))
        assert "leer" in out.lower()

    def test_trend_section_empty(self):
        out = "\n".join(_render_trend_section([]))
        assert "Keine geprueften" in out


# ─────────── Rendering: Populated ───────────

class TestPopulatedRendering:
    def test_reviews_table_format(self):
        reviews = [
            {"agent_type": "jules", "verdict": "approved", "count": 5},
            {"agent_type": "jules", "verdict": "revision_requested", "count": 2},
            {"agent_type": "seo", "verdict": "approved", "count": 3},
        ]
        out = "\n".join(_render_reviews_section(reviews))
        assert "| jules |" in out
        assert "| seo |" in out
        # jules: 5 approved, 2 revision
        assert "| jules | 5 | 2 |" in out
        # seo: 3 approved, 0 revision
        assert "| seo | 3 | 0 |" in out

    def test_auto_merge_with_revert(self):
        out = "\n".join(_render_auto_merge_section({"total": 10, "reverted": 2, "pending": 1}))
        assert "Gesamt:** 10" in out
        assert "Revertet:** 2 (20.0%)" in out
        assert "Noch offen" in out

    def test_auto_merge_clean(self):
        out = "\n".join(_render_auto_merge_section({"total": 5, "reverted": 0, "pending": 0}))
        assert "Revertet:** 0 ✅" in out
        assert "Noch offen" not in out

    def test_pending_zero(self):
        out = "\n".join(_render_pending_section(0))
        assert "Keine offenen Reviews" in out

    def test_pending_positive(self):
        out = "\n".join(_render_pending_section(7))
        assert "**7** PRs" in out

    def test_queue_status(self):
        out = "\n".join(_render_queue_section({
            "queued": 3, "released": 42, "failed": 1,
        }))
        assert "queued: 3" in out
        assert "released: 42" in out
        assert "failed: 1" in out

    def test_trend_filters_zero_reverts(self):
        trend = [
            {"rule_matched": "jules_approved_0b", "total": 50, "reverted": 0, "rate_pct": 0.0},
            {"rule_matched": "seo_approved_0b", "total": 20, "reverted": 3, "rate_pct": 15.0},
            {"rule_matched": "codex_approved_0b", "total": 5, "reverted": 1, "rate_pct": 20.0},
        ]
        out = "\n".join(_render_trend_section(trend))
        # Nur Rules mit Reverts werden gezeigt
        assert "jules_approved_0b" not in out
        assert "seo_approved_0b" in out
        assert "codex_approved_0b" in out

    def test_trend_all_clean_shows_checkmark(self):
        trend = [
            {"rule_matched": "r1", "total": 10, "reverted": 0, "rate_pct": 0.0},
        ]
        out = "\n".join(_render_trend_section(trend))
        assert "Alle Auto-Merges stabil" in out

    def test_trend_caps_at_5(self):
        trend = [
            {"rule_matched": f"rule_{i}", "total": 10, "reverted": 1, "rate_pct": 10.0}
            for i in range(10)
        ]
        out = "\n".join(_render_trend_section(trend))
        # Max 5 Rules in Tabelle (alle haben reverted>0)
        assert out.count("| rule_") <= 5


# ─────────── collect_digest_data ───────────

class TestCollect:
    async def test_happy_path_assembles_data(self):
        pool = _FakeAsyncpgPool(
            reviews=[("jules", "approved", 5), ("seo", "approved", 2)],
            pending=3,
        )
        queue = AsyncMock()
        queue.count_by_status = AsyncMock(return_value={"queued": 1, "released": 10})
        tracker = AsyncMock()
        tracker.last_24h_summary = AsyncMock(return_value={
            "total": 7, "reverted": 1, "pending": 0,
        })
        tracker.revert_rate_by_rule = AsyncMock(return_value=[
            {"rule_matched": "seo_approved_0b", "total": 3, "reverted": 1, "rate_pct": 33.3},
        ])

        data = await collect_digest_data(
            jules_state_pool=pool, task_queue=queue, outcome_tracker=tracker,
        )
        assert len(data.reviews_24h) == 2
        assert data.auto_merges_24h["total"] == 7
        assert data.queue_status["queued"] == 1
        assert data.pending_manual_merges == 3
        assert len(data.revert_trend_7d) == 1

    async def test_tracker_error_uses_defaults(self):
        pool = _FakeAsyncpgPool(reviews=[], pending=0)
        queue = AsyncMock()
        queue.count_by_status = AsyncMock(return_value={})
        tracker = AsyncMock()
        tracker.last_24h_summary = AsyncMock(side_effect=RuntimeError("db error"))
        tracker.revert_rate_by_rule = AsyncMock(side_effect=RuntimeError("db error"))

        data = await collect_digest_data(
            jules_state_pool=pool, task_queue=queue, outcome_tracker=tracker,
        )
        assert data.auto_merges_24h == {"total": 0, "reverted": 0, "pending": 0}
        assert data.revert_trend_7d == []

    async def test_no_jules_pool_returns_empty(self):
        queue = AsyncMock()
        queue.count_by_status = AsyncMock(return_value={})
        tracker = AsyncMock()
        tracker.last_24h_summary = AsyncMock(return_value={"total": 0, "reverted": 0, "pending": 0})
        tracker.revert_rate_by_rule = AsyncMock(return_value=[])

        data = await collect_digest_data(
            jules_state_pool=None, task_queue=queue, outcome_tracker=tracker,
        )
        assert data.reviews_24h == []
        assert data.pending_manual_merges == 0


# ─────────── Fakes ───────────

class _FakeAsyncpgPool:
    """Mini-Pool der die 2 Queries im Digest beantwortet."""
    def __init__(self, reviews, pending):
        self._reviews = reviews
        self._pending = pending

    def acquire(self):
        return _FakeAcquire(self)


class _FakeAcquire:
    def __init__(self, pool):
        self.pool = pool

    async def __aenter__(self):
        return _FakeConn(self.pool)

    async def __aexit__(self, *a):
        return None


class _FakeConn:
    def __init__(self, pool):
        self.pool = pool

    async def fetch(self, query):
        """SELECT agent_type, verdict, cnt."""
        return [
            {"agent_type": a, "verdict": v, "cnt": c}
            for (a, v, c) in self.pool._reviews
        ]

    async def fetchrow(self, query):
        return {"cnt": self.pool._pending}
