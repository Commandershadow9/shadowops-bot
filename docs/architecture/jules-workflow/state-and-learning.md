---
title: Jules Workflow — State & Learning
status: active
version: v1
last_reviewed: 2026-04-15
owner: CommanderShadow9
related:
  - ../../adr/007-jules-secops-workflow.md
  - ../../plans/2026-04-11-jules-secops-workflow-design.md
---

# Jules Workflow — State & Learning

## Phase 1: Datenbank-Schemas

### Task 1.1: Migration für `security_analyst.jules_pr_reviews`

**Files:**
- Create: `src/integrations/github_integration/jules_state_schema.sql`

**Step 1: Schema-Datei schreiben**

Erstelle die Datei mit folgendem Inhalt:

```sql
-- Jules SecOps Workflow — PR-Review State
-- Lebt in der security_analyst Datenbank
-- Siehe docs/plans/2026-04-11-jules-secops-workflow-design.md Abschnitt 7.1

CREATE TABLE IF NOT EXISTS jules_pr_reviews (
    id              BIGSERIAL PRIMARY KEY,
    repo            TEXT NOT NULL,
    pr_number       INTEGER NOT NULL,
    issue_number    INTEGER,
    finding_id      BIGINT,

    status          TEXT NOT NULL CHECK (status IN (
                      'pending','reviewing','approved','revision_requested',
                      'escalated','merged','abandoned')),

    last_reviewed_sha  TEXT,
    iteration_count    INTEGER NOT NULL DEFAULT 0,
    last_review_at     TIMESTAMPTZ,
    lock_acquired_at   TIMESTAMPTZ,
    lock_owner         TEXT,

    review_comment_id  BIGINT,
    last_review_json   JSONB,
    last_blockers      JSONB,
    tokens_consumed    INTEGER NOT NULL DEFAULT 0,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at       TIMESTAMPTZ,
    human_override  BOOLEAN NOT NULL DEFAULT false,

    UNIQUE (repo, pr_number)
);

CREATE INDEX IF NOT EXISTS idx_jules_status ON jules_pr_reviews(status)
    WHERE status NOT IN ('merged','abandoned');

CREATE INDEX IF NOT EXISTS idx_jules_finding ON jules_pr_reviews(finding_id);

-- FK wird nachträglich hinzugefügt wenn findings-Tabelle existiert (soft coupling)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'findings') THEN
        BEGIN
            ALTER TABLE jules_pr_reviews
                ADD CONSTRAINT fk_jules_finding
                FOREIGN KEY (finding_id) REFERENCES findings(id) ON DELETE SET NULL;
        EXCEPTION WHEN duplicate_object THEN NULL;
        END;
    END IF;
END $$;

-- View für Metriken
CREATE OR REPLACE VIEW jules_daily_stats AS
SELECT
    date_trunc('day', created_at) AS day,
    repo,
    COUNT(*) FILTER (WHERE status = 'approved')           AS approved,
    COUNT(*) FILTER (WHERE status = 'revision_requested') AS revisions,
    COUNT(*) FILTER (WHERE status = 'escalated')          AS escalated,
    COUNT(*) FILTER (WHERE status = 'merged')             AS merged,
    AVG(iteration_count)                                  AS avg_iterations,
    SUM(tokens_consumed)                                  AS total_tokens
FROM jules_pr_reviews
GROUP BY 1, 2
ORDER BY 1 DESC, 2;
```

**Step 2: Manuelle Schema-Anwendung gegen security_analyst DB**

```bash
# DSN aus config.yaml lesen (security_analyst.database_dsn)
DSN=$(python -c "from src.utils.config import Config; print(Config().security_analyst_dsn)")
psql "$DSN" -f src/integrations/github_integration/jules_state_schema.sql
```

Erwartet: `CREATE TABLE`, `CREATE INDEX` (2x), `DO`, `CREATE VIEW` — keine Errors.

**Step 3: Verifikation**

```bash
psql "$DSN" -c "\d jules_pr_reviews"
psql "$DSN" -c "SELECT * FROM jules_pr_reviews LIMIT 1;"
```

Erwartet: Tabellen-Struktur wird ausgegeben, SELECT gibt 0 Rows zurück.

**Step 4: Commit**

```bash
git add src/integrations/github_integration/jules_state_schema.sql
git commit -m "feat: jules_pr_reviews Schema — State-Tabelle für Jules Review Workflow"
```

---

### Task 1.2: Migration für `agent_learning.jules_review_examples`

**Files:**
- Create: `src/integrations/github_integration/jules_learning_schema.sql`

**Step 1: Schema-Datei schreiben**

```sql
-- Jules Review Learning — Few-Shot Examples
-- Lebt in der agent_learning Datenbank (Port 5433, GuildScout Postgres)
-- Siehe docs/plans/2026-04-11-jules-secops-workflow-design.md Abschnitt 7.2

CREATE TABLE IF NOT EXISTS jules_review_examples (
    id              BIGSERIAL PRIMARY KEY,
    project         TEXT NOT NULL,
    pr_ref          TEXT,
    diff_summary    TEXT NOT NULL,
    review_json     JSONB NOT NULL,
    outcome         TEXT NOT NULL CHECK (outcome IN (
                      'good_catch','false_positive','missed_issue','approved_clean')),
    user_feedback   TEXT,
    weight          REAL NOT NULL DEFAULT 1.0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_jrex_project_outcome
    ON jules_review_examples(project, outcome);

CREATE INDEX IF NOT EXISTS idx_jrex_weight
    ON jules_review_examples(project, weight DESC, created_at DESC);
```

**Step 2: Anwenden gegen agent_learning DB**

```bash
AGENT_DSN=$(python -c "from src.utils.config import Config; print(Config().agent_learning_dsn)")
psql "$AGENT_DSN" -f src/integrations/github_integration/jules_learning_schema.sql
```

Erwartet: `CREATE TABLE`, `CREATE INDEX` (2x) ohne Errors.

**Step 3: Verifikation**

```bash
psql "$AGENT_DSN" -c "\d jules_review_examples"
```

**Step 4: Commit**

```bash
git add src/integrations/github_integration/jules_learning_schema.sql
git commit -m "feat: jules_review_examples Schema — Few-Shot-Learning Tabelle"
```

---

## Phase 2: Konfiguration

### Task 2.1: `jules_workflow` Block zu `config.example.yaml` hinzufügen

**Files:**
- Modify: `config/config.example.yaml`

**Step 1: Block finden**

Finde in `config/config.example.yaml` einen logischen Einfüge-Punkt nach dem `security_analyst:` Block. Falls nicht vorhanden, am Ende der Datei.

**Step 2: Block hinzufügen**

```yaml
jules_workflow:
  enabled: false                   # Master-Switch (Default aus bis Phase 15)
  dry_run: false                   # Logge Aktionen statt sie auszuführen (Phase 14)
  max_iterations: 5                # Schicht 4: Hard-Cap pro PR
  cooldown_seconds: 300            # Schicht 3: 5 Min zwischen Reviews
  max_hours_per_pr: 2              # Schicht 6: Timeout pro PR

  circuit_breaker:
    max_reviews_per_hour: 20       # Schicht 5: pro Repo
    pause_duration_seconds: 3600

  excluded_projects:
    - sicherheitsdienst

  # Prompt-Konfiguration
  max_diff_chars: 8000
  few_shot_examples: 3
  project_knowledge_limit: 10
  token_cap_per_pr: 50000

  # Discord
  notification_channel: "security-ops"
  escalation_channel: "alerts"
  role_ping_on_escalation: "@Shadow"
```

**Step 3: Gleichen Block auch in `config/config.yaml` (Laufzeit-Config, NICHT in Git) nachtragen**

```bash
# Zeige Shadow den Diff zum manuellen Review — NICHT automatisch committen
diff -u config/config.yaml{,_neu} 2>/dev/null || true
# Dann von Hand nach config/config.yaml kopieren
```

**Step 4: Config-Loader-Access testen**

Erstelle ad-hoc Test-Script (nicht committen):

```python
# /tmp/test_jules_config.py
from src.utils.config import Config
c = Config()
print(c.jules_workflow)
print(c.jules_workflow.enabled)
print(c.jules_workflow.max_iterations)
```

```bash
cd /home/cmdshadow/shadowops-bot
source .venv/bin/activate
python /tmp/test_jules_config.py
```

Erwartet: Ausgabe des kompletten `jules_workflow`-Blocks + `False` + `5`. Wenn `AttributeError`: Config-Klasse akzeptiert `self.raw['jules_workflow']` — dann muss `Config.__getattr__` schon das Nested-Dict-Wrapping machen (der Pattern existiert bereits, siehe `self.config.security_analyst.database_dsn`).

**Step 5: Commit**

```bash
git add config/config.example.yaml
git commit -m "feat: jules_workflow Config-Block (disabled default)"
```

---

## Phase 3: State-Layer (asyncpg)

### Task 3.1: Skeleton `jules_state.py` mit Dataclass + Pool-Init

**Files:**
- Create: `src/integrations/github_integration/jules_state.py`

**Step 1: Datei mit Skeleton schreiben**

```python
"""
Jules Workflow — State Management.

Asyncpg-Layer für security_analyst.jules_pr_reviews.
Atomic Lock-Claim, Stale-Lock-Recovery, CRUD.

Siehe docs/plans/2026-04-11-jules-secops-workflow-design.md §7.1.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional

import asyncpg

logger = logging.getLogger(__name__)


@dataclass
class JulesReviewRow:
    id: int
    repo: str
    pr_number: int
    issue_number: Optional[int]
    finding_id: Optional[int]
    status: str
    last_reviewed_sha: Optional[str]
    iteration_count: int
    last_review_at: Optional[datetime]
    lock_acquired_at: Optional[datetime]
    lock_owner: Optional[str]
    review_comment_id: Optional[int]
    last_review_json: Optional[dict]
    last_blockers: Optional[list]
    tokens_consumed: int
    created_at: datetime
    updated_at: datetime
    closed_at: Optional[datetime]
    human_override: bool

    @classmethod
    def from_record(cls, rec: asyncpg.Record) -> "JulesReviewRow":
        return cls(**dict(rec))


class JulesState:
    """Thin asyncpg wrapper um jules_pr_reviews."""

    def __init__(self, dsn: str):
        self._dsn = dsn
        self._pool: Optional[asyncpg.Pool] = None

    async def connect(self) -> None:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                self._dsn, min_size=1, max_size=4, command_timeout=10
            )
            logger.info("JulesState connected to security_analyst DB")

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None

    @property
    def process_id(self) -> str:
        return f"shadowops-bot-pid-{os.getpid()}"
```

**Step 2: Import smoke test**

```bash
cd /home/cmdshadow/shadowops-bot
source .venv/bin/activate
python -c "from src.integrations.github_integration.jules_state import JulesState, JulesReviewRow; print('OK')"
```

Erwartet: `OK`.

**Step 3: Commit**

```bash
git add src/integrations/github_integration/jules_state.py
git commit -m "feat: JulesState Skeleton — Dataclass + Pool-Init"
```

---

### Task 3.2: `try_claim_review()` mit atomic UPDATE (TDD)

**Files:**
- Modify: `src/integrations/github_integration/jules_state.py`
- Create: `tests/unit/test_jules_state.py`

**Step 1: Test schreiben (wird fehlschlagen)**

```python
# tests/unit/test_jules_state.py
"""Tests für JulesState — nutzt echte Postgres, springt bei fehlender DSN."""
import os
import pytest
import asyncpg

from src.integrations.github_integration.jules_state import JulesState, JulesReviewRow


# Skip das gesamte Modul wenn kein Test-DSN konfiguriert
DSN = os.environ.get("JULES_TEST_DSN") or os.environ.get("SECURITY_ANALYST_DB_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="JULES_TEST_DSN/SECURITY_ANALYST_DB_URL nicht gesetzt")


@pytest.fixture
async def state():
    s = JulesState(DSN)
    await s.connect()
    # Clean slate für Test-Isolation
    async with s._pool.acquire() as conn:
        await conn.execute("DELETE FROM jules_pr_reviews WHERE repo LIKE 'test_%'")
    yield s
    async with s._pool.acquire() as conn:
        await conn.execute("DELETE FROM jules_pr_reviews WHERE repo LIKE 'test_%'")
    await s.close()


@pytest.fixture
async def seeded_row(state):
    """Lege eine pending-Row an für Claim-Tests."""
    async with state._pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO jules_pr_reviews (repo, pr_number, issue_number, status)
            VALUES ($1, $2, $3, 'pending')
            """,
            "test_repo", 42, 100,
        )
    yield ("test_repo", 42)


@pytest.mark.asyncio
async def test_try_claim_review_first_call_succeeds(state, seeded_row):
    repo, pr = seeded_row
    row = await state.try_claim_review(repo, pr, "sha_aaa", "worker-1")
    assert row is not None
    assert row.status == "reviewing"
    assert row.lock_owner == "worker-1"
    assert row.last_reviewed_sha != "sha_aaa"  # SHA wird erst bei Success gesetzt


@pytest.mark.asyncio
async def test_try_claim_review_second_call_blocked_by_lock(state, seeded_row):
    repo, pr = seeded_row
    first = await state.try_claim_review(repo, pr, "sha_aaa", "worker-1")
    assert first is not None

    # Zweiter Claim-Versuch während noch reviewing
    second = await state.try_claim_review(repo, pr, "sha_bbb", "worker-2")
    assert second is None, "Zweiter Claim darf nicht greifen (PR #123 Race!)"


@pytest.mark.asyncio
async def test_try_claim_review_same_sha_blocked(state, seeded_row):
    repo, pr = seeded_row
    first = await state.try_claim_review(repo, pr, "sha_aaa", "worker-1")
    # Simuliere erfolgreichen Review-Abschluss
    await state.mark_reviewed_sha(first.id, "sha_aaa")
    await state.release_lock(first.id, new_status="revision_requested")

    # Gleicher SHA -> kein neuer Review
    retry = await state.try_claim_review(repo, pr, "sha_aaa", "worker-1")
    assert retry is None, "Gleicher SHA darf nicht erneut reviewt werden"


@pytest.mark.asyncio
async def test_try_claim_review_new_sha_allowed(state, seeded_row):
    repo, pr = seeded_row
    first = await state.try_claim_review(repo, pr, "sha_aaa", "worker-1")
    await state.mark_reviewed_sha(first.id, "sha_aaa")
    await state.release_lock(first.id, new_status="revision_requested")

    # Neuer SHA -> erneuter Review erlaubt
    second = await state.try_claim_review(repo, pr, "sha_bbb", "worker-1")
    assert second is not None
    assert second.status == "reviewing"
```

**Step 2: Test ausführen — FAIL erwartet**

```bash
export JULES_TEST_DSN=$(python -c "from src.utils.config import Config; print(Config().security_analyst_dsn)")
pytest tests/unit/test_jules_state.py -x -v 2>&1 | head -40
```

Erwartet: 4 Tests FAIL mit `AttributeError: 'JulesState' object has no attribute 'try_claim_review'`.

**Step 3: Implementation hinzufügen**

Füge in `jules_state.py` zur `JulesState`-Klasse hinzu:

```python
    async def try_claim_review(
        self, repo: str, pr_number: int, head_sha: str, lock_owner: str
    ) -> Optional[JulesReviewRow]:
        """
        Atomic Lock-Claim + SHA-Dedupe in einem UPDATE.

        Returns:
            JulesReviewRow wenn Claim erfolgreich, None bei:
            - status == 'reviewing' (Lock gehalten)
            - last_reviewed_sha == head_sha (kein neuer Commit)
            - PR-Row existiert nicht (kein Jules-Issue -> ignorieren)
        """
        sql = """
            UPDATE jules_pr_reviews
            SET status = 'reviewing',
                lock_acquired_at = now(),
                lock_owner = $1,
                updated_at = now()
            WHERE repo = $2
              AND pr_number = $3
              AND status IN ('pending', 'revision_requested', 'approved')
              AND (last_reviewed_sha IS NULL OR last_reviewed_sha != $4)
            RETURNING *
        """
        async with self._pool.acquire() as conn:
            rec = await conn.fetchrow(sql, lock_owner, repo, pr_number, head_sha)
            if rec is None:
                return None
            return JulesReviewRow.from_record(rec)

    async def release_lock(self, row_id: int, new_status: str) -> None:
        """Gibt Lock frei und setzt neuen Status."""
        valid = {"pending", "approved", "revision_requested", "escalated",
                 "merged", "abandoned"}
        if new_status not in valid:
            raise ValueError(f"Ungültiger Status: {new_status}")
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE jules_pr_reviews
                SET status = $1,
                    lock_owner = NULL,
                    lock_acquired_at = NULL,
                    updated_at = now()
                WHERE id = $2
                """,
                new_status, row_id,
            )

    async def mark_reviewed_sha(self, row_id: int, sha: str) -> None:
        """Setzt last_reviewed_sha + last_review_at nach erfolgreichem Review."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE jules_pr_reviews
                SET last_reviewed_sha = $1,
                    last_review_at = now(),
                    iteration_count = iteration_count + 1,
                    updated_at = now()
                WHERE id = $2
                """,
                sha, row_id,
            )
```

**Step 4: Tests erneut ausführen — PASS erwartet**

```bash
pytest tests/unit/test_jules_state.py -x -v
```

Erwartet: 4 passed.

**Step 5: Commit**

```bash
git add src/integrations/github_integration/jules_state.py tests/unit/test_jules_state.py
git commit -m "feat: try_claim_review atomic Lock-Claim mit SHA-Dedupe"
```

---

### Task 3.3: Stale-Lock-Recovery

**Files:**
- Modify: `src/integrations/github_integration/jules_state.py`
- Modify: `tests/unit/test_jules_state.py`

**Step 1: Test hinzufügen**

```python
@pytest.mark.asyncio
async def test_stale_lock_recovery_frees_old_locks(state, seeded_row):
    repo, pr = seeded_row
    # Claim + manuell Lock-Zeit in die Vergangenheit setzen
    row = await state.try_claim_review(repo, pr, "sha_aaa", "dead-worker")
    async with state._pool.acquire() as conn:
        await conn.execute(
            "UPDATE jules_pr_reviews SET lock_acquired_at = now() - interval '15 minutes' WHERE id = $1",
            row.id,
        )

    # Recovery
    recovered = await state.recover_stale_locks(timeout_minutes=10)
    assert recovered == 1

    # Danach sollte ein neuer Claim möglich sein
    new_claim = await state.try_claim_review(repo, pr, "sha_bbb", "worker-2")
    assert new_claim is not None


@pytest.mark.asyncio
async def test_stale_lock_recovery_spares_fresh_locks(state, seeded_row):
    repo, pr = seeded_row
    row = await state.try_claim_review(repo, pr, "sha_aaa", "live-worker")
    # Lock ist frisch (gerade eben gesetzt)

    recovered = await state.recover_stale_locks(timeout_minutes=10)
    assert recovered == 0

    # Frischer Lock blockt noch
    blocked = await state.try_claim_review(repo, pr, "sha_bbb", "worker-2")
    assert blocked is None
```

**Step 2: Test ausführen — FAIL**

```bash
pytest tests/unit/test_jules_state.py::test_stale_lock_recovery_frees_old_locks -x -v
```

**Step 3: Implementation**

```python
    async def recover_stale_locks(self, timeout_minutes: int = 10) -> int:
        """
        Setzt reviewing-Rows zurück auf revision_requested wenn der Lock älter
        als timeout_minutes ist. Returns Anzahl der zurückgesetzten Rows.
        """
        sql = """
            UPDATE jules_pr_reviews
            SET status = 'revision_requested',
                lock_owner = NULL,
                lock_acquired_at = NULL,
                updated_at = now()
            WHERE status = 'reviewing'
              AND lock_acquired_at < now() - ($1 || ' minutes')::interval
            RETURNING id
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, str(timeout_minutes))
            count = len(rows)
            if count:
                logger.warning(
                    f"Jules Stale-Lock-Recovery: {count} Lock(s) älter als "
                    f"{timeout_minutes}min zurückgesetzt"
                )
            return count
```

**Step 4: Test erneut — PASS**

```bash
pytest tests/unit/test_jules_state.py -x -v
```

Erwartet: 6 passed.

**Step 5: Commit**

```bash
git add src/integrations/github_integration/jules_state.py tests/unit/test_jules_state.py
git commit -m "feat: Stale-Lock-Recovery für Jules Workflow"
```

---

### Task 3.4: CRUD-Helper — `get`, `ensure_pending`, `update_comment_id`, `mark_terminal`

**Files:**
- Modify: `src/integrations/github_integration/jules_state.py`
- Modify: `tests/unit/test_jules_state.py`

**Step 1: Tests hinzufügen**

```python
@pytest.mark.asyncio
async def test_ensure_pending_creates_row(state):
    row = await state.ensure_pending(
        repo="test_ensure", pr_number=1, issue_number=50, finding_id=None
    )
    assert row.status == "pending"
    assert row.iteration_count == 0


@pytest.mark.asyncio
async def test_ensure_pending_idempotent(state):
    a = await state.ensure_pending("test_idem", 2, 51, None)
    b = await state.ensure_pending("test_idem", 2, 51, None)
    assert a.id == b.id  # Gleiche Row, nicht neue Row


@pytest.mark.asyncio
async def test_get_returns_none_for_missing(state):
    row = await state.get("nonexistent_repo", 999)
    assert row is None


@pytest.mark.asyncio
async def test_update_comment_id(state):
    row = await state.ensure_pending("test_cid", 3, 52, None)
    await state.update_comment_id(row.id, 12345)
    fresh = await state.get("test_cid", 3)
    assert fresh.review_comment_id == 12345


@pytest.mark.asyncio
async def test_mark_terminal_sets_closed_at(state):
    row = await state.ensure_pending("test_term", 4, 53, None)
    await state.mark_terminal(row.id, "merged")
    fresh = await state.get("test_term", 4)
    assert fresh.status == "merged"
    assert fresh.closed_at is not None
```

**Step 2: Implementation**

```python
    async def ensure_pending(
        self,
        repo: str,
        pr_number: int,
        issue_number: Optional[int],
        finding_id: Optional[int],
    ) -> JulesReviewRow:
        """Upsert: erstellt eine pending-Row wenn noch nicht vorhanden."""
        sql = """
            INSERT INTO jules_pr_reviews (repo, pr_number, issue_number, finding_id, status)
            VALUES ($1, $2, $3, $4, 'pending')
            ON CONFLICT (repo, pr_number) DO UPDATE
              SET issue_number = COALESCE(jules_pr_reviews.issue_number, EXCLUDED.issue_number),
                  finding_id   = COALESCE(jules_pr_reviews.finding_id, EXCLUDED.finding_id),
                  updated_at   = now()
            RETURNING *
        """
        async with self._pool.acquire() as conn:
            rec = await conn.fetchrow(sql, repo, pr_number, issue_number, finding_id)
            return JulesReviewRow.from_record(rec)

    async def get(self, repo: str, pr_number: int) -> Optional[JulesReviewRow]:
        async with self._pool.acquire() as conn:
            rec = await conn.fetchrow(
                "SELECT * FROM jules_pr_reviews WHERE repo = $1 AND pr_number = $2",
                repo, pr_number,
            )
            return JulesReviewRow.from_record(rec) if rec else None

    async def update_comment_id(self, row_id: int, comment_id: int) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE jules_pr_reviews SET review_comment_id = $1, updated_at = now() WHERE id = $2",
                comment_id, row_id,
            )

    async def store_review_result(
        self,
        row_id: int,
        review_json: dict,
        blockers: list,
        tokens: int,
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE jules_pr_reviews
                SET last_review_json = $1::jsonb,
                    last_blockers    = $2::jsonb,
                    tokens_consumed  = tokens_consumed + $3,
                    updated_at       = now()
                WHERE id = $4
                """,
                __import__("json").dumps(review_json),
                __import__("json").dumps(blockers),
                tokens, row_id,
            )

    async def mark_terminal(self, row_id: int, status: str) -> None:
        """Final state (merged/abandoned/escalated) mit closed_at."""
        if status not in {"merged", "abandoned", "escalated"}:
            raise ValueError(f"mark_terminal nur für Terminal-States, nicht {status}")
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE jules_pr_reviews
                SET status = $1,
                    closed_at = now(),
                    lock_owner = NULL,
                    lock_acquired_at = NULL,
                    updated_at = now()
                WHERE id = $2
                """,
                status, row_id,
            )
```

**Step 3: Tests — PASS**

```bash
pytest tests/unit/test_jules_state.py -x -v
```

Erwartet: 11 passed.

**Step 4: Commit**

```bash
git add src/integrations/github_integration/jules_state.py tests/unit/test_jules_state.py
git commit -m "feat: JulesState CRUD — ensure_pending, get, update_comment_id, mark_terminal"
```

---

*Phase 3 abgeschlossen.*

---

## Phase 14: Learning-Batch + Dry-Run

### Task 14.1: Outcome-Klassifikation

**Files:**
- Create: `src/integrations/github_integration/jules_batch.py`
- Create: `tests/unit/test_jules_batch.py`

**Step 1: Test**

```python
# tests/unit/test_jules_batch.py
from src.integrations.github_integration.jules_batch import classify_outcome


def _fake(status, feedback_rating=None, human_override=False):
    class F:
        pass
    r = F()
    r.status = status
    r.human_override = human_override
    r.feedback_rating = feedback_rating
    return r


def test_merged_with_positive_feedback_is_approved_clean():
    assert classify_outcome(_fake("merged", feedback_rating=1)) == "approved_clean"


def test_merged_with_negative_feedback_is_false_positive():
    assert classify_outcome(_fake("merged", feedback_rating=-1)) == "false_positive"


def test_revision_with_negative_feedback_is_good_catch():
    assert classify_outcome(_fake("revision_requested", feedback_rating=-1)) == "good_catch"


def test_human_override_after_approval_is_missed_issue():
    assert classify_outcome(_fake("approved", human_override=True)) == "missed_issue"


def test_merged_without_feedback_is_approved_clean():
    assert classify_outcome(_fake("merged")) == "approved_clean"
```

**Step 2: Implementation**

```python
# src/integrations/github_integration/jules_batch.py
"""
Jules Learning — Nightly Batch Job.

Klassifiziert abgeschlossene Reviews basierend auf Feedback + Outcome,
schreibt jules_review_examples Einträge, aktualisiert agent_quality_scores.

Läuft 1x/Tag (23:00 lokal) via bestehendes Scheduler-Pattern.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


def classify_outcome(row) -> str:
    """
    Klassifiziert ein abgeschlossenes Review-Outcome.

    Regeln:
    - human_override=True nach approved -> 'missed_issue'
    - status=merged + positive feedback (oder keines) -> 'approved_clean'
    - status=merged + negative feedback -> 'false_positive'
    - status=revision_requested + negative feedback -> 'good_catch'
    - Fallback -> 'approved_clean'
    """
    status = getattr(row, "status", "")
    override = getattr(row, "human_override", False)
    rating = getattr(row, "feedback_rating", None)  # +1 / -1 / None

    if override and status == "approved":
        return "missed_issue"
    if status == "merged":
        if rating is not None and rating < 0:
            return "false_positive"
        return "approved_clean"
    if status == "revision_requested":
        if rating is not None and rating < 0:
            return "good_catch"
    return "approved_clean"


async def run_nightly_batch(
    jules_state_pool, learning_pool, logger_channel=None
) -> Dict[str, int]:
    """
    Hauptfunktion — wird 1x/Tag aufgerufen.

    Returns:
        Zähler {classified, examples_written, quality_updated}
    """
    counts = {"classified": 0, "examples_written": 0, "quality_updated": 0}

    # 1. Alle Reviews mit Feedback aus den letzten 24h
    async with jules_state_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT r.*, f.rating AS feedback_rating
            FROM jules_pr_reviews r
            LEFT JOIN LATERAL (
                SELECT rating FROM agent_feedback
                WHERE agent_name = 'jules_reviewer'
                  AND reference_id = r.pr_number::text
                ORDER BY created_at DESC LIMIT 1
            ) f ON true
            WHERE r.updated_at > now() - interval '24 hours'
              AND r.status IN ('approved', 'merged', 'revision_requested')
            """
        )

    # 2. Für jede Row: Outcome klassifizieren und example schreiben
    for row in rows:
        outcome = classify_outcome(row)
        counts["classified"] += 1

        # Schreibe in agent_learning
        try:
            async with learning_pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO jules_review_examples
                      (project, pr_ref, diff_summary, review_json, outcome, weight)
                    VALUES ($1, $2, $3, $4::jsonb, $5, 1.0)
                    """,
                    row["repo"],
                    f"{row['repo']}#{row['pr_number']}",
                    (row.get("last_review_json", {}) or {}).get("summary", ""),
                    json.dumps(row.get("last_review_json", {}) or {}),
                    outcome,
                )
                counts["examples_written"] += 1
        except Exception as e:
            logger.warning(f"[jules-batch] example write failed: {e}")

    logger.info(
        f"[jules-batch] classified={counts['classified']} "
        f"written={counts['examples_written']}"
    )
    return counts
```

**Step 3: Tests — PASS**

```bash
pytest tests/unit/test_jules_batch.py -x -v
```

Erwartet: 5 passed.

**Step 4: Commit**

```bash
git add src/integrations/github_integration/jules_batch.py tests/unit/test_jules_batch.py
git commit -m "feat: Jules Nightly-Batch — Outcome-Klassifikation + Learning-Examples"
```

---

### Task 14.2: Batch-Job in bestehenden Scheduler einhängen

**Files:**
- Modify: `src/bot.py` oder die Stelle, wo andere tägliche Jobs registriert werden

**Step 1: Scheduler-Point finden**

```bash
grep -rn "scheduler\|CronScheduler\|daily\|@tasks.loop" src/bot.py src/integrations/ 2>&1 | head -20
```

Wenn du den Pattern findest (z.B. `@tasks.loop(hours=24)` oder `scheduler.add_job(...)`, dann dort registrieren. Zeitpunkt: 23:00 lokal.

**Step 2: Job registrieren**

```python
# In der Stelle wo andere tägliche Jobs laufen:
async def _jules_nightly_batch():
    if not getattr(self.github_integration, "_jules_enabled", False):
        return
    try:
        from src.integrations.github_integration.jules_batch import run_nightly_batch
        counts = await run_nightly_batch(
            jules_state_pool=self.github_integration.jules_state._pool,
            learning_pool=self.github_integration.jules_learning._pool,
        )
        # Post in AI-learning Channel
        if hasattr(self, "discord_logger"):
            await self.discord_logger.send_to_channel(
                "ai-learning",
                f"Jules Nightly: classified={counts['classified']}, "
                f"examples={counts['examples_written']}"
            )
    except Exception:
        logger.exception("[jules] nightly batch crashed")

# Scheduler-Eintrag — an der Stelle wo andere jobs mit "23:00" laufen
scheduler.add_job(_jules_nightly_batch, "cron", hour=23, minute=7)
```

Wichtig: Minute 7 statt 0 (siehe Bot-weites Pattern — Off-Minute bevorzugen).

**Step 3: Smoke-Test (Import)**

```bash
python -c "from src.integrations.github_integration.jules_batch import run_nightly_batch; print('OK')"
```

**Step 4: Commit**

```bash
git add src/bot.py
git commit -m "feat: Jules Nightly-Batch Scheduler-Einbindung (23:07 daily)"
```

---

### Task 14.3: Dry-Run-Mode vollständig implementieren

**Files:**
- Modify: `src/integrations/github_integration/jules_workflow_mixin.py`
- Modify: `tests/unit/test_jules_workflow_mixin.py`

**Step 1: Test**

```python
@pytest.mark.asyncio
async def test_dry_run_skips_ai_and_comment(harness):
    harness.config.jules_workflow.dry_run = True
    harness._jules_is_jules_pr = AsyncMock(return_value=True)
    harness.should_review = AsyncMock(return_value=MagicMock(proceed=True, row=_row()))
    harness.ai_service = MagicMock()
    harness.ai_service.review_pr = AsyncMock()
    harness.jules_state.release_lock = AsyncMock()

    payload = {
        "action": "opened",
        "pull_request": {
            "number": 1, "head": {"sha": "x"},
            "user": {"login": "google-labs-jules[bot]"},
            "body": "Fixes #1", "labels": [{"name": "jules"}],
        },
        "repository": {"name": "X", "owner": {"login": "o"}},
    }
    await harness.handle_jules_pr_event(payload)

    harness.ai_service.review_pr.assert_not_called()
    harness.jules_state.release_lock.assert_called()
```

**Step 2: Implementation**

Die Dry-Run-Logik ist bereits in `_jules_run_review_pipeline` (Task 8.3). Stelle sicher dass:

```python
            if getattr(cfg, "dry_run", False):
                logger.info(f"[jules] DRY-RUN {repo}#{pr_number} iter={iteration}")
                await self.jules_state.release_lock(row.id, "revision_requested")
                return
```

**Step 3: Tests — PASS**

```bash
pytest tests/unit/test_jules_workflow_mixin.py -x -v
```

**Step 4: Commit**

```bash
git add tests/unit/test_jules_workflow_mixin.py
git commit -m "test: Dry-Run Mode überspringt AI-Call"
```

---
