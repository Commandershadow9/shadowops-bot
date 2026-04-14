"""TaskQueue — asyncpg-Layer fuer agent_task_queue.

Speichert Jules-Session-Starts mit Priority + Scheduling. Scheduler liest
Batches, created Sessions via Jules API, markiert released/failed.

Priority-Werte (0 = hoechste):
- 0: manual (User-getriggert)
- 1: scan_agent (Security-Findings)
- 2: jules_suggestion (nightly poll)
- 3: seo_agent (wenn extern queued — aktuell nicht genutzt)
- 4: background (Housekeeping)
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import asyncpg

logger = logging.getLogger(__name__)


@dataclass
class QueuedTask:
    id: int
    source: str
    priority: int
    payload: Dict[str, Any]
    project: Optional[str]
    retry_count: int


class TaskQueue:
    """asyncpg-gepooltes Interface zu agent_task_queue."""

    def __init__(self, dsn: str):
        self._dsn = dsn
        self._pool: Optional[asyncpg.Pool] = None

    async def connect(self) -> None:
        """Oeffnet Pool. Idempotent."""
        if self._pool is not None:
            return
        self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=3)

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def enqueue(
        self,
        source: str,
        priority: int,
        payload: Dict[str, Any],
        project: Optional[str] = None,
    ) -> int:
        """Fuegt Task hinzu. Gibt DB-ID zurueck."""
        assert self._pool is not None, "connect() zuerst aufrufen"
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO agent_task_queue (source, priority, payload, project)
                   VALUES ($1, $2, $3::jsonb, $4)
                   RETURNING id""",
                source, priority, json.dumps(payload), project,
            )
            return int(row["id"])

    async def get_next_batch(self, limit: int) -> List[QueuedTask]:
        """Holt die naechsten `limit` queued Tasks, sortiert Priority ASC, created_at ASC.

        Nur Tasks mit scheduled_for <= now(). Tasks mit zukuenftigem scheduled_for
        (z.B. nach Retry-Delay) werden uebersprungen.
        """
        assert self._pool is not None
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT id, source, priority, payload, project, retry_count
                   FROM agent_task_queue
                   WHERE status = 'queued' AND scheduled_for <= now()
                   ORDER BY priority ASC, created_at ASC
                   LIMIT $1""",
                limit,
            )
        return [self._row_to_task(r) for r in rows]

    async def mark_released(self, task_id: int, external_id: str) -> None:
        """Markiert Task als released (Jules-Session gestartet)."""
        assert self._pool is not None
        async with self._pool.acquire() as conn:
            await conn.execute(
                """UPDATE agent_task_queue
                   SET status='released', released_at=now(),
                       released_as=$1, updated_at=now()
                   WHERE id=$2""",
                external_id, task_id,
            )

    async def mark_failed(
        self, task_id: int, reason: str, retry: bool = False,
        retry_delay_seconds: int = 300,
    ) -> None:
        """Markiert Task als failed.

        Mit `retry=True`: retry_count +1, scheduled_for = now() + Delay,
        status bleibt 'queued' (wird beim naechsten Poll wieder gezogen).

        Ohne retry: status='failed', permanent.
        """
        assert self._pool is not None
        async with self._pool.acquire() as conn:
            if retry:
                await conn.execute(
                    f"""UPDATE agent_task_queue
                        SET retry_count = retry_count + 1,
                            failure_reason = $1,
                            scheduled_for = now() + interval '{int(retry_delay_seconds)} seconds',
                            updated_at = now()
                        WHERE id = $2""",
                    reason, task_id,
                )
            else:
                await conn.execute(
                    """UPDATE agent_task_queue
                       SET status='failed', failure_reason=$1, updated_at=now()
                       WHERE id=$2""",
                    reason, task_id,
                )

    async def count_by_status(self) -> Dict[str, int]:
        """Diagnose: Anzahl Tasks pro Status."""
        assert self._pool is not None
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT status, COUNT(*)::int AS cnt FROM agent_task_queue GROUP BY status"
            )
        return {r["status"]: r["cnt"] for r in rows}

    async def count_released_last_24h(self) -> int:
        """Zaehlt Sessions die in den letzten 24h gestartet wurden.

        Wichtig fuer Jules-Rate-Limit (100/24h).
        """
        assert self._pool is not None
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT COUNT(*)::int AS cnt FROM agent_task_queue
                   WHERE released_at > now() - interval '24 hours'"""
            )
        return int(row["cnt"] or 0)

    async def cancel(self, task_id: int, reason: str = "") -> None:
        """Cancelt einen Task (z.B. weil bereits anders erledigt)."""
        assert self._pool is not None
        async with self._pool.acquire() as conn:
            await conn.execute(
                """UPDATE agent_task_queue
                   SET status='cancelled', failure_reason=$1, updated_at=now()
                   WHERE id=$2 AND status='queued'""",
                reason, task_id,
            )

    # ── internals ──────────────────────────────────────────────

    @staticmethod
    def _row_to_task(row) -> QueuedTask:
        payload = row["payload"]
        # asyncpg liefert JSONB als str oder dict — beides tolerieren
        if isinstance(payload, str):
            payload = json.loads(payload)
        return QueuedTask(
            id=int(row["id"]),
            source=row["source"],
            priority=int(row["priority"]),
            payload=payload,
            project=row["project"],
            retry_count=int(row["retry_count"]),
        )
