"""Tests fuer OutcomeTracker (auto_merge_outcomes Tabelle).

Live gegen security_analyst DB. Cleanup via project LIKE 'test_%'.
"""
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

try:
    from src.utils.config import Config
    _DSN = Config().security_analyst_dsn
    if not _DSN:
        raise RuntimeError("kein DSN")
except Exception:
    _DSN = None

from src.integrations.github_integration.agent_review.outcome_tracker import (
    OutcomeTracker, AutoMergeOutcome,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(_DSN is None, reason="Keine DB-Verbindung verfuegbar"),
]


@pytest_asyncio.fixture
async def tracker():
    t = OutcomeTracker(_DSN)
    await t.connect()
    async with t._pool.acquire() as conn:
        await conn.execute("DELETE FROM auto_merge_outcomes WHERE project LIKE 'test_%'")
    yield t
    async with t._pool.acquire() as conn:
        await conn.execute("DELETE FROM auto_merge_outcomes WHERE project LIKE 'test_%'")
    await t.close()


# ─────────── record_auto_merge ───────────

class TestRecord:
    async def test_inserts_pending_row(self, tracker):
        oid = await tracker.record_auto_merge(
            agent_type="jules", project="test_z",
            repo="Commandershadow9/test_z", pr_number=42,
            rule_matched="approved_small_test_change",
        )
        assert isinstance(oid, int) and oid > 0
        async with tracker._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT agent_type, pr_number, reverted, checked_at FROM auto_merge_outcomes WHERE id=$1",
                oid,
            )
        assert row["agent_type"] == "jules"
        assert row["pr_number"] == 42
        assert row["reverted"] is False
        assert row["checked_at"] is None


# ─────────── get_pending_outcomes ───────────

class TestPending:
    async def test_ignores_fresh_rows(self, tracker):
        # Fresh row — merged_at = now
        await tracker.record_auto_merge(
            agent_type="jules", project="test_fresh",
            repo="x/y", pr_number=1, rule_matched="r",
        )
        pending = await tracker.get_pending_outcomes(min_age_hours=24)
        assert all(p.project != "test_fresh" for p in pending)

    async def test_returns_old_unchecked(self, tracker):
        oid = await tracker.record_auto_merge(
            agent_type="seo", project="test_old",
            repo="x/y", pr_number=5, rule_matched="content_only",
        )
        # Manipuliere merged_at auf 25h zurueck
        async with tracker._pool.acquire() as conn:
            await conn.execute(
                "UPDATE auto_merge_outcomes SET merged_at = now() - interval '25 hours' WHERE id=$1",
                oid,
            )
        pending = await tracker.get_pending_outcomes(min_age_hours=24)
        our = [p for p in pending if p.id == oid]
        assert len(our) == 1
        assert our[0].project == "test_old"

    async def test_ignores_already_checked(self, tracker):
        oid = await tracker.record_auto_merge(
            agent_type="codex", project="test_done",
            repo="x/y", pr_number=6, rule_matched="r",
        )
        async with tracker._pool.acquire() as conn:
            await conn.execute(
                "UPDATE auto_merge_outcomes SET merged_at=now()-interval '25 hours', checked_at=now() WHERE id=$1",
                oid,
            )
        pending = await tracker.get_pending_outcomes(min_age_hours=24)
        assert all(p.id != oid for p in pending)


# ─────────── mark_checked ───────────

class TestMarkChecked:
    async def test_sets_check_result(self, tracker):
        oid = await tracker.record_auto_merge(
            agent_type="jules", project="test_check",
            repo="x/y", pr_number=10, rule_matched="r",
        )
        await tracker.mark_checked(
            oid, reverted=True,
            reverted_at=datetime.now(timezone.utc),
            ci_passed=True, deployed_ok=False,
            follow_up_fix_needed=True,
        )
        async with tracker._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT reverted, checked_at, ci_passed_after_merge, deployed_without_incident, follow_up_fix_needed FROM auto_merge_outcomes WHERE id=$1",
                oid,
            )
        assert row["reverted"] is True
        assert row["checked_at"] is not None
        assert row["ci_passed_after_merge"] is True
        assert row["deployed_without_incident"] is False
        assert row["follow_up_fix_needed"] is True

    async def test_no_revert_defaults(self, tracker):
        """mark_checked ohne Args = kein Revert, checked_at gesetzt."""
        oid = await tracker.record_auto_merge(
            agent_type="jules", project="test_chk2",
            repo="x/y", pr_number=11, rule_matched="r",
        )
        await tracker.mark_checked(oid)
        async with tracker._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT reverted, checked_at FROM auto_merge_outcomes WHERE id=$1", oid,
            )
        assert row["reverted"] is False
        assert row["checked_at"] is not None


# ─────────── revert_rate_by_rule ───────────

class TestRevertRate:
    async def test_computes_rate_per_rule(self, tracker):
        # 2 Merges rule_a, 1 reverted. 1 Merge rule_b, 0 reverted.
        ids = []
        for i in range(2):
            oid = await tracker.record_auto_merge(
                agent_type="seo", project="test_rate",
                repo="x/y", pr_number=100 + i, rule_matched="rule_a",
            )
            ids.append(oid)
        oid_b = await tracker.record_auto_merge(
            agent_type="seo", project="test_rate",
            repo="x/y", pr_number=200, rule_matched="rule_b",
        )

        # Check mit revert-outcomes
        await tracker.mark_checked(ids[0], reverted=True)
        await tracker.mark_checked(ids[1], reverted=False)
        await tracker.mark_checked(oid_b, reverted=False)

        stats = await tracker.revert_rate_by_rule(agent_type="seo", days=30)
        a = next((s for s in stats if s["rule_matched"] == "rule_a"), None)
        b = next((s for s in stats if s["rule_matched"] == "rule_b"), None)
        assert a is not None and a["total"] == 2 and a["reverted"] == 1
        assert a["rate_pct"] == 50.0
        assert b is not None and b["reverted"] == 0


# ─────────── last_24h_summary ───────────

class TestSummary:
    async def test_counts_last_24h(self, tracker):
        before = await tracker.last_24h_summary()

        oid1 = await tracker.record_auto_merge(
            agent_type="jules", project="test_sum",
            repo="x/y", pr_number=300, rule_matched="r",
        )
        oid2 = await tracker.record_auto_merge(
            agent_type="jules", project="test_sum",
            repo="x/y", pr_number=301, rule_matched="r",
        )
        await tracker.mark_checked(oid1, reverted=True)
        # oid2 bleibt pending

        after = await tracker.last_24h_summary()
        assert after["total"] >= before["total"] + 2
        assert after["reverted"] >= before["reverted"] + 1
        assert after["pending"] >= before["pending"] + 1
