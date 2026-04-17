---
title: Jules Workflow — Loop-Protection
status: active
version: v1
last_reviewed: 2026-04-15
owner: CommanderShadow9
related:
  - ../../adr/007-jules-secops-workflow.md
  - ../../design/jules-workflow.md
---

# Jules Workflow — Loop-Protection

## Phase 7: Loop-Schutz — Gate-Pipeline

### Task 7.1: `ReviewDecision` Dataclass + `ALLOWED_TRIGGERS` Konstanten

**Files:**
- Create: `src/integrations/github_integration/jules_gates.py`
- Create: `tests/unit/test_jules_gates.py`

**Step 1: Gates-Modul mit Konstanten**

```python
# src/integrations/github_integration/jules_gates.py
"""
Jules Workflow — Loop-Schutz-Gates.

7 Schichten Defense-in-Depth, siehe Design-Doc §6.
Jedes Gate ist eine reine Funktion — einfach zu testen, einfach zu verketten.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from .jules_state import JulesReviewRow


ALLOWED_TRIGGERS = frozenset({
    "pull_request:opened",
    "pull_request:synchronize",
    "pull_request:ready_for_review",
})

BLOCKED_TRIGGERS = frozenset({
    "issue_comment:created",
    "issue_comment:edited",
    "pull_request:edited",
    "pull_request:labeled",
    "pull_request:unlabeled",
    "pull_request_review:submitted",
    "pull_request_review:edited",
    "pull_request_review_comment:created",
})


@dataclass
class ReviewDecision:
    """Ergebnis der Gate-Pipeline."""
    proceed: bool
    reason: str  # SKIP-Grund oder 'proceed'
    row: Optional[JulesReviewRow] = None

    @classmethod
    def skip(cls, reason: str) -> "ReviewDecision":
        return cls(proceed=False, reason=reason)

    @classmethod
    def advance(cls, row: JulesReviewRow) -> "ReviewDecision":
        return cls(proceed=True, reason="proceed", row=row)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def gate_trigger_whitelist(event_type: str) -> Optional[str]:
    """
    Schicht 1. Returns None wenn erlaubt, SKIP-Reason wenn geblockt.
    """
    if event_type in BLOCKED_TRIGGERS:
        return "blocked_trigger"
    if event_type not in ALLOWED_TRIGGERS:
        return "unknown_trigger"
    return None


def gate_iteration_cap(row: JulesReviewRow, max_iterations: int = 5) -> Optional[str]:
    """Schicht 4."""
    if row.iteration_count >= max_iterations:
        return "max_iterations"
    return None


def gate_time_cap(row: JulesReviewRow, max_hours: int = 2) -> Optional[str]:
    """Schicht 6."""
    if row.created_at < now_utc() - timedelta(hours=max_hours):
        return "timeout_per_pr"
    return None


def gate_cooldown(row: JulesReviewRow, cooldown_seconds: int = 300) -> Optional[str]:
    """Schicht 3."""
    if row.last_review_at is None:
        return None
    elapsed = (now_utc() - row.last_review_at).total_seconds()
    if elapsed < cooldown_seconds:
        return "cooldown"
    return None
```

**Step 2: Tests für reine Gate-Funktionen**

```python
# tests/unit/test_jules_gates.py
from datetime import datetime, timedelta, timezone

import pytest

from src.integrations.github_integration.jules_gates import (
    ALLOWED_TRIGGERS,
    BLOCKED_TRIGGERS,
    ReviewDecision,
    gate_cooldown,
    gate_iteration_cap,
    gate_time_cap,
    gate_trigger_whitelist,
)
from src.integrations.github_integration.jules_state import JulesReviewRow


def _row(**overrides):
    now = datetime.now(timezone.utc)
    defaults = dict(
        id=1, repo="X", pr_number=1, issue_number=None, finding_id=None,
        status="pending", last_reviewed_sha=None, iteration_count=0,
        last_review_at=None, lock_acquired_at=None, lock_owner=None,
        review_comment_id=None, last_review_json=None, last_blockers=None,
        tokens_consumed=0, created_at=now, updated_at=now,
        closed_at=None, human_override=False,
    )
    defaults.update(overrides)
    return JulesReviewRow(**defaults)


def test_trigger_whitelist_allows_pr_opened():
    assert gate_trigger_whitelist("pull_request:opened") is None


def test_trigger_whitelist_allows_pr_synchronize():
    assert gate_trigger_whitelist("pull_request:synchronize") is None


def test_trigger_whitelist_blocks_issue_comment():
    """PR #123 Hauptursache — issue_comment darf NIEMALS einen Review triggern."""
    assert gate_trigger_whitelist("issue_comment:created") == "blocked_trigger"


def test_trigger_whitelist_blocks_pr_review_comment():
    assert gate_trigger_whitelist("pull_request_review_comment:created") == "blocked_trigger"


def test_trigger_whitelist_blocks_unknown():
    assert gate_trigger_whitelist("release:published") == "unknown_trigger"


def test_iteration_cap_passes_at_4():
    assert gate_iteration_cap(_row(iteration_count=4), 5) is None


def test_iteration_cap_blocks_at_5():
    assert gate_iteration_cap(_row(iteration_count=5), 5) == "max_iterations"


def test_iteration_cap_blocks_above_5():
    assert gate_iteration_cap(_row(iteration_count=10), 5) == "max_iterations"


def test_time_cap_passes_for_fresh_pr():
    row = _row(created_at=datetime.now(timezone.utc) - timedelta(minutes=30))
    assert gate_time_cap(row, max_hours=2) is None


def test_time_cap_blocks_old_pr():
    row = _row(created_at=datetime.now(timezone.utc) - timedelta(hours=3))
    assert gate_time_cap(row, max_hours=2) == "timeout_per_pr"


def test_cooldown_passes_when_never_reviewed():
    assert gate_cooldown(_row(last_review_at=None), 300) is None


def test_cooldown_blocks_within_window():
    row = _row(last_review_at=datetime.now(timezone.utc) - timedelta(seconds=60))
    assert gate_cooldown(row, 300) == "cooldown"


def test_cooldown_passes_after_window():
    row = _row(last_review_at=datetime.now(timezone.utc) - timedelta(seconds=400))
    assert gate_cooldown(row, 300) is None


def test_review_decision_skip_factory():
    d = ReviewDecision.skip("test_reason")
    assert d.proceed is False
    assert d.reason == "test_reason"
    assert d.row is None


def test_review_decision_advance_factory():
    d = ReviewDecision.advance(_row())
    assert d.proceed is True
    assert d.row is not None
```

**Step 3: Tests — PASS**

```bash
pytest tests/unit/test_jules_gates.py -x -v
```

Erwartet: 16 passed.

**Step 4: Commit**

```bash
git add src/integrations/github_integration/jules_gates.py tests/unit/test_jules_gates.py
git commit -m "feat: Jules Loop-Schutz Gates (Trigger-Whitelist, Iteration-Cap, Time-Cap, Cooldown)"
```

---

### Task 7.2: Redis Circuit-Breaker (Schicht 5)

**Files:**
- Modify: `src/integrations/github_integration/jules_gates.py`
- Modify: `tests/unit/test_jules_gates.py`

**Step 1: Test mit fakeredis**

Installiere fakeredis falls noch nicht vorhanden:

```bash
pip install fakeredis
# Nur für tests, NICHT in requirements.txt committen
```

Tests:

```python
# Am Ende von tests/unit/test_jules_gates.py
import fakeredis.aioredis
import pytest_asyncio

from src.integrations.github_integration.jules_gates import check_circuit_breaker


@pytest_asyncio.fixture
async def redis_mock():
    return fakeredis.aioredis.FakeRedis()


@pytest.mark.asyncio
async def test_circuit_breaker_closed_first_call(redis_mock):
    open_, count = await check_circuit_breaker(redis_mock, "test_repo", threshold=20)
    assert open_ is False
    assert count == 1


@pytest.mark.asyncio
async def test_circuit_breaker_opens_at_threshold(redis_mock):
    for i in range(20):
        await check_circuit_breaker(redis_mock, "test_repo_b", threshold=20)
    open_, count = await check_circuit_breaker(redis_mock, "test_repo_b", threshold=20)
    assert open_ is True
    assert count >= 21


@pytest.mark.asyncio
async def test_circuit_breaker_independent_per_repo(redis_mock):
    for i in range(25):
        await check_circuit_breaker(redis_mock, "repo_a", threshold=20)
    open_b, _ = await check_circuit_breaker(redis_mock, "repo_b", threshold=20)
    assert open_b is False
```

**Step 2: Implementation**

```python
# In jules_gates.py am Ende hinzufügen:

async def check_circuit_breaker(
    redis_client, repo: str, threshold: int = 20, ttl_seconds: int = 3600
) -> tuple[bool, int]:
    """
    Schicht 5. Returns (is_open, current_count).

    Inkrementiert den Per-Repo-Zähler in Redis. Key hat TTL von 1h (rolling).
    """
    key = f"jules:circuit:{repo}"
    count = await redis_client.incr(key)
    if count == 1:
        await redis_client.expire(key, ttl_seconds)
    return (count > threshold, int(count))
```

**Step 3: Tests — PASS**

```bash
pytest tests/unit/test_jules_gates.py -x -v
```

Erwartet: 19 passed.

**Step 4: Commit**

```bash
git add src/integrations/github_integration/jules_gates.py tests/unit/test_jules_gates.py
git commit -m "feat: Redis Circuit-Breaker für Jules Reviews (20/h pro Repo)"
```

---
