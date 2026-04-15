---
title: Queue, API-Client und Suggestions-Poller
status: active
version: v1
last_reviewed: 2026-04-15
owner: CommanderShadow9
related:
  - ../../adr/008-multi-agent-review-pipeline.md
  - ../../design/multi-agent-review.md
  - ../jules-workflow/README.md
---

# Queue, API-Client und Suggestions-Poller

Phase 3 ergaenzt die Multi-Agent-Pipeline um eine Queue fuer Jules-Session-Starts, einen
Jules-API-Client und einen Suggestions-Poller. Die Queue respektiert das Jules-Limit von
100 neuen Sessions pro 24 Stunden sowie maximal 15 concurrent Sessions.

---

## Phase 3: Jules Suggestions Poller + Queue

### Task 3.1: Queue-Layer (asyncpg)

**Files:**

- Create: `src/integrations/github_integration/agent_review/queue.py`
- Create: `tests/unit/agent_review/test_queue.py`

**Step 1: Tests** — CRUD-Operations: `enqueue`, `dequeue` mit Priority-Sort,
`mark_released`, `mark_failed`. Verwende denselben testcontainer-aehnlichen Ansatz wie
`test_jules_state.py`.

**Step 2: Implementation**

Skeleton:

```python
# queue.py
import asyncpg, json
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional


@dataclass
class QueuedTask:
    id: int
    source: str
    priority: int
    payload: dict
    project: Optional[str]
    retry_count: int


class TaskQueue:
    def __init__(self, dsn: str):
        self._dsn = dsn
        self._pool: Optional[asyncpg.Pool] = None

    async def connect(self):
        self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=3)

    async def enqueue(self, source, priority, payload, project=None) -> int:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO agent_task_queue(source, priority, payload, project)
                   VALUES ($1,$2,$3::jsonb,$4) RETURNING id""",
                source, priority, json.dumps(payload), project,
            )
            return row["id"]

    async def get_next_batch(self, limit: int) -> List[QueuedTask]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT id, source, priority, payload, project, retry_count
                   FROM agent_task_queue
                   WHERE status='queued' AND scheduled_for <= now()
                   ORDER BY priority ASC, created_at ASC
                   LIMIT $1""",
                limit,
            )
            return [QueuedTask(id=r["id"], source=r["source"], priority=r["priority"],
                               payload=json.loads(r["payload"]) if isinstance(r["payload"], str) else r["payload"],
                               project=r["project"], retry_count=r["retry_count"]) for r in rows]

    async def mark_released(self, task_id: int, external_id: str):
        async with self._pool.acquire() as conn:
            await conn.execute(
                """UPDATE agent_task_queue SET status='released', released_at=now(),
                   released_as=$1, updated_at=now() WHERE id=$2""",
                external_id, task_id,
            )

    async def mark_failed(self, task_id: int, reason: str, retry: bool = False):
        async with self._pool.acquire() as conn:
            if retry:
                await conn.execute(
                    """UPDATE agent_task_queue SET retry_count=retry_count+1,
                       failure_reason=$1, scheduled_for=now()+interval '5 minutes',
                       updated_at=now() WHERE id=$2""",
                    reason, task_id,
                )
            else:
                await conn.execute(
                    """UPDATE agent_task_queue SET status='failed',
                       failure_reason=$1, updated_at=now() WHERE id=$2""",
                    reason, task_id,
                )

    async def count_by_status(self) -> dict:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT status, COUNT(*) FROM agent_task_queue GROUP BY status"
            )
            return {r["status"]: r["count"] for r in rows}
```

**Step 3: Tests PASS + Commit**

```bash
git commit -m "feat: TaskQueue asyncpg-Layer (enqueue, get_next_batch, mark_*)"
```

---

### Task 3.2: Jules API-Client Helper

**Files:**

- Create: `src/integrations/github_integration/agent_review/jules_api.py`
- Create: `tests/unit/agent_review/test_jules_api.py`

**Step 1: Tests mit mocked httpx/aiohttp**

Tests fuer:

- `create_session(prompt, repo)` -> POST /sessions
- `count_concurrent_sessions()` -> GET /sessions?state=IN_PROGRESS
- `get_suggestions(repo)` -> stub (API-Endpoint noch nicht final dokumentiert)

**Step 2: Implementation**

```python
# jules_api.py
import aiohttp, logging
from typing import Optional, List, Dict

logger = logging.getLogger(__name__)


class JulesAPIError(Exception):
    pass


class JulesAPIClient:
    BASE_URL = "https://jules.googleapis.com/v1alpha"

    def __init__(self, api_key: str):
        self._api_key = api_key

    @property
    def _headers(self):
        return {
            "X-Goog-Api-Key": self._api_key,
            "Content-Type": "application/json",
        }

    async def create_session(self, prompt: str, owner: str, repo: str,
                             title: str = "", branch: str = "main") -> str:
        body = {
            "title": title or prompt[:80],
            "prompt": prompt,
            "sourceContext": {
                "source": f"sources/github/{owner}/{repo}",
                "githubRepoContext": {"startingBranch": branch},
            },
            "automationMode": "AUTO_CREATE_PR",
        }
        async with aiohttp.ClientSession() as http:
            async with http.post(f"{self.BASE_URL}/sessions", json=body, headers=self._headers) as r:
                if r.status == 429:
                    raise JulesAPIError("rate_limited")
                if r.status != 200:
                    text = await r.text()
                    raise JulesAPIError(f"http {r.status}: {text[:200]}")
                data = await r.json()
                return data.get("id", "")

    async def count_concurrent_sessions(self) -> int:
        async with aiohttp.ClientSession() as http:
            async with http.get(f"{self.BASE_URL}/sessions?pageSize=50",
                                headers=self._headers) as r:
                if r.status != 200:
                    return 0
                data = await r.json()
                sessions = data.get("sessions", [])
                return sum(1 for s in sessions if s.get("state") == "IN_PROGRESS")
```

**Step 3: Tests PASS + Commit**

```bash
git commit -m "feat: JulesAPIClient (create_session, count_concurrent)"
```

---

### Task 3.3: Suggestions-Poller

**Files:**

- Create: `src/integrations/github_integration/agent_review/suggestions_poller.py`
- Create: `tests/unit/agent_review/test_suggestions_poller.py`

**Step 1: Implementation**

```python
# suggestions_poller.py
class JulesSuggestionsPoller:
    def __init__(self, queue, jules_api, repos: list, max_per_run: int = 20):
        self.queue = queue
        self.jules_api = jules_api
        self.repos = repos
        self.max_per_run = max_per_run

    async def poll_and_queue(self) -> int:
        """Laeuft 3x/Tag. Holt Suggestions pro Repo, queued sie."""
        total = 0
        for full_repo in self.repos:
            owner, repo = full_repo.split("/", 1)
            try:
                # Note: Exakter Suggestions-Endpoint noch nicht dokumentiert.
                # Fallback: GET /sessions filter nach state=SUGGESTED (wenn verfuegbar)
                # Alternative: Top-Suggestions aus Dashboard scrapen (noch nicht implementiert)
                # Phase-3.3: Platzhalter mit Warnung
                logger.info(f"[suggestions-poller] {full_repo}: API noch nicht verfuegbar, skipping")
            except Exception:
                logger.exception(f"[suggestions-poller] {full_repo} failed")
        return total
```

**Hinweis:** Die Jules-Suggestions-API ist noch im Alpha. Phase 3.3 implementiert das
Skeleton; die volle Integration wartet auf einen stabilen Endpoint. Prioritaet: Queue +
API-Client zuerst, Suggestions-Poll als Stub.

**Step 2: Tests + Commit**

```bash
git commit -m "feat: Suggestions-Poller Skeleton (wartet auf stabilen Jules-API-Endpoint)"
```

---

### Task 3.4: Queue-Scheduler in `bot.py`

**Files:**

- Modify: `src/bot.py` — neuer `@tasks.loop` Task

**Step 1: Scheduler-Task hinzufuegen**

Nach dem bestehenden `jules_nightly_batch_task`:

```python
    @tasks.loop(seconds=60)
    async def agent_task_queue_scheduler(self):
        """Released Jules-Tasks aus Queue respektiert 100/24h + 15 concurrent."""
        try:
            gh = getattr(self, "github_integration", None)
            if not gh or not getattr(gh, "_agent_review_enabled", False):
                return
            queue = gh.agent_task_queue
            jules_api = gh.jules_api_client

            concurrent = await jules_api.count_concurrent_sessions()
            if concurrent >= 15:
                return

            started_24h = await self._count_started_last_24h()  # neue Helper-Methode
            budget = min(15 - concurrent, 100 - started_24h)
            if budget <= 0:
                return

            tasks = await queue.get_next_batch(limit=budget)
            for task in tasks:
                try:
                    sid = await jules_api.create_session(
                        prompt=task.payload["prompt"],
                        owner=task.payload["owner"],
                        repo=task.payload["repo"],
                        title=task.payload.get("title", ""),
                    )
                    await queue.mark_released(task.id, sid)
                    logger.info(f"[queue] Task {task.id} -> Jules-Session {sid}")
                except Exception as e:
                    await queue.mark_failed(task.id, str(e), retry=True)

        except Exception:
            logger.exception("[queue] scheduler crashed")
```

**Step 2: Helper `_count_started_last_24h`:**

```python
    async def _count_started_last_24h(self) -> int:
        pool = self.github_integration.agent_task_queue._pool
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT COUNT(*) FROM agent_task_queue WHERE released_at > now() - interval '24 hours'"
            )
            return int(row[0] or 0)
```

**Step 3: Scheduler-Start in Startup-Flow + Commit**

```bash
git commit -m "feat: agent_task_queue_scheduler (60s loop, 100/24h + 15 concurrent limits)"
```
