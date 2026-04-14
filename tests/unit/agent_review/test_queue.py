"""Tests fuer TaskQueue asyncpg-Layer.

Benoetigt eine echte PostgreSQL-Verbindung (security_analyst DB, gleiche DB
wie jules_pr_reviews). Test-Rows verwenden source LIKE 'test_%' und werden
vor/nach jedem Test geloescht.
"""
import asyncio
import json

import pytest
import pytest_asyncio

# DSN aus Config laden — skip wenn nicht verfuegbar
try:
    from src.utils.config import Config
    _DSN = Config().security_analyst_dsn
    if not _DSN:
        raise RuntimeError("Kein DSN")
except Exception:
    _DSN = None

from src.integrations.github_integration.agent_review.queue import (
    TaskQueue, QueuedTask,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(_DSN is None, reason="Keine security_analyst DB-Verbindung verfuegbar"),
]


@pytest_asyncio.fixture
async def queue():
    """TaskQueue-Instanz mit Verbindung, raeumt test_*-Rows auf."""
    q = TaskQueue(_DSN)
    await q.connect()
    async with q._pool.acquire() as conn:
        await conn.execute("DELETE FROM agent_task_queue WHERE source LIKE 'test_%'")
    yield q
    async with q._pool.acquire() as conn:
        await conn.execute("DELETE FROM agent_task_queue WHERE source LIKE 'test_%'")
    await q.close()


# ─────────── enqueue ───────────

class TestEnqueue:
    async def test_enqueue_returns_id(self, queue: TaskQueue):
        tid = await queue.enqueue(
            source="test_manual", priority=0,
            payload={"repo": "zerodox", "prompt": "fix X"},
            project="zerodox",
        )
        assert isinstance(tid, int)
        assert tid > 0

    async def test_enqueue_stores_payload_as_jsonb(self, queue: TaskQueue):
        tid = await queue.enqueue(
            "test_manual", 0, {"nested": {"key": "val"}, "list": [1, 2, 3]},
        )
        async with queue._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT payload FROM agent_task_queue WHERE id=$1", tid
            )
        pl = row["payload"]
        if isinstance(pl, str):
            pl = json.loads(pl)
        assert pl["nested"]["key"] == "val"
        assert pl["list"] == [1, 2, 3]

    async def test_enqueue_default_status_queued(self, queue: TaskQueue):
        tid = await queue.enqueue("test_manual", 1, {})
        async with queue._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT status FROM agent_task_queue WHERE id=$1", tid
            )
        assert row["status"] == "queued"


# ─────────── get_next_batch ───────────

class TestGetNextBatch:
    async def test_empty_queue(self, queue: TaskQueue):
        batch = await queue.get_next_batch(limit=10)
        assert batch == []

    async def test_respects_limit(self, queue: TaskQueue):
        for i in range(5):
            await queue.enqueue("test_batch", 1, {"i": i})
        batch = await queue.get_next_batch(limit=3)
        assert len(batch) == 3

    async def test_sorts_by_priority_asc(self, queue: TaskQueue):
        low_id = await queue.enqueue("test_prio", 4, {"lbl": "low"})
        mid_id = await queue.enqueue("test_prio", 2, {"lbl": "mid"})
        high_id = await queue.enqueue("test_prio", 0, {"lbl": "high"})
        batch = await queue.get_next_batch(limit=3)
        assert [t.id for t in batch] == [high_id, mid_id, low_id]

    async def test_sorts_by_created_at_within_priority(self, queue: TaskQueue):
        first = await queue.enqueue("test_fifo", 1, {"i": 1})
        await asyncio.sleep(0.02)
        second = await queue.enqueue("test_fifo", 1, {"i": 2})
        batch = await queue.get_next_batch(limit=2)
        assert [t.id for t in batch] == [first, second]

    async def test_skips_future_scheduled(self, queue: TaskQueue):
        await queue.enqueue("test_future", 1, {"k": "v"})
        # Manipuliere scheduled_for in Zukunft
        async with queue._pool.acquire() as conn:
            await conn.execute(
                """UPDATE agent_task_queue SET scheduled_for = now() + interval '1 hour'
                   WHERE source='test_future'"""
            )
        batch = await queue.get_next_batch(limit=10)
        assert batch == []

    async def test_skips_non_queued_status(self, queue: TaskQueue):
        tid = await queue.enqueue("test_status", 1, {})
        await queue.mark_released(tid, "session-xyz")
        batch = await queue.get_next_batch(limit=10)
        assert all(t.id != tid for t in batch)

    async def test_returns_queued_task_dataclass(self, queue: TaskQueue):
        await queue.enqueue("test_dc", 2, {"foo": "bar"}, project="zerodox")
        batch = await queue.get_next_batch(limit=1)
        assert len(batch) == 1
        t = batch[0]
        assert isinstance(t, QueuedTask)
        assert t.source == "test_dc"
        assert t.priority == 2
        assert t.project == "zerodox"
        assert t.payload == {"foo": "bar"}
        assert t.retry_count == 0


# ─────────── mark_released ───────────

class TestMarkReleased:
    async def test_sets_status_and_external_id(self, queue: TaskQueue):
        tid = await queue.enqueue("test_rel", 1, {})
        await queue.mark_released(tid, "jules-session-abc123")
        async with queue._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT status, released_as, released_at FROM agent_task_queue WHERE id=$1",
                tid,
            )
        assert row["status"] == "released"
        assert row["released_as"] == "jules-session-abc123"
        assert row["released_at"] is not None


# ─────────── mark_failed ───────────

class TestMarkFailed:
    async def test_permanent_failure(self, queue: TaskQueue):
        tid = await queue.enqueue("test_fail", 1, {})
        await queue.mark_failed(tid, "rate_limited", retry=False)
        async with queue._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT status, failure_reason, retry_count FROM agent_task_queue WHERE id=$1",
                tid,
            )
        assert row["status"] == "failed"
        assert row["failure_reason"] == "rate_limited"
        assert row["retry_count"] == 0

    async def test_retry_keeps_queued_and_bumps_count(self, queue: TaskQueue):
        tid = await queue.enqueue("test_retry", 1, {})
        await queue.mark_failed(tid, "transient", retry=True, retry_delay_seconds=10)
        async with queue._pool.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT status, retry_count, failure_reason,
                          scheduled_for > now() AS future
                   FROM agent_task_queue WHERE id=$1""",
                tid,
            )
        assert row["status"] == "queued"
        assert row["retry_count"] == 1
        assert row["failure_reason"] == "transient"
        assert row["future"] is True  # scheduled_for in Zukunft

    async def test_retry_multiple_times_stacks_counter(self, queue: TaskQueue):
        tid = await queue.enqueue("test_retry_stack", 1, {})
        await queue.mark_failed(tid, "e1", retry=True, retry_delay_seconds=1)
        await queue.mark_failed(tid, "e2", retry=True, retry_delay_seconds=1)
        async with queue._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT retry_count FROM agent_task_queue WHERE id=$1", tid
            )
        assert row["retry_count"] == 2


# ─────────── counts ───────────

class TestCounts:
    async def test_count_by_status(self, queue: TaskQueue):
        t1 = await queue.enqueue("test_cnt", 1, {})
        t2 = await queue.enqueue("test_cnt", 1, {})
        t3 = await queue.enqueue("test_cnt", 1, {})
        await queue.mark_released(t1, "s1")
        await queue.mark_failed(t2, "x", retry=False)
        counts = await queue.count_by_status()
        # Es koennten andere Rows existieren — wir pruefen nur dass unsere drin sind
        assert counts.get("queued", 0) >= 1
        assert counts.get("released", 0) >= 1
        assert counts.get("failed", 0) >= 1

    async def test_count_released_last_24h(self, queue: TaskQueue):
        before = await queue.count_released_last_24h()
        t1 = await queue.enqueue("test_24h", 1, {})
        await queue.mark_released(t1, "sess-24h-1")
        after = await queue.count_released_last_24h()
        assert after == before + 1


# ─────────── cancel ───────────

class TestCancel:
    async def test_cancel_queued_task(self, queue: TaskQueue):
        tid = await queue.enqueue("test_cancel", 1, {})
        await queue.cancel(tid, reason="superseded")
        async with queue._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT status, failure_reason FROM agent_task_queue WHERE id=$1", tid
            )
        assert row["status"] == "cancelled"
        assert row["failure_reason"] == "superseded"

    async def test_cancel_released_task_is_noop(self, queue: TaskQueue):
        tid = await queue.enqueue("test_cancel2", 1, {})
        await queue.mark_released(tid, "s")
        await queue.cancel(tid, reason="too late")
        async with queue._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT status FROM agent_task_queue WHERE id=$1", tid
            )
        # bleibt 'released' — Cancel greift nur bei status='queued'
        assert row["status"] == "released"
