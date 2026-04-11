# Jules SecOps Workflow Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Ersetze die gescheiterte Gemini-Jules-Integration durch einen robusten, selbstlernenden SecOps-Workflow mit 7 Defense-in-Depth-Schichten gegen Review-Loops.

**Design Reference:** [`docs/plans/2026-04-11-jules-secops-workflow-design.md`](./2026-04-11-jules-secops-workflow-design.md) — Jede Design-Entscheidung hier ist dort begründet. Bei Unklarheit: Design-Doc lesen.

**Architecture:** Modular Monolith im bestehenden `github_integration/` Mixin-Pattern. Neuer `JulesWorkflowMixin` koordiniert PR-Reviews via `ai_engine.review_pr()` mit strukturiertem Prompt. State in PostgreSQL (`security_analyst.jules_pr_reviews`), Learning-Kontext aus `agent_learning` DB.

**Tech Stack:** Python 3.12, asyncpg, aiohttp, discord.py, Claude Opus via CLI-Provider, jsonschema, Redis, PostgreSQL, pytest + pytest-asyncio + fakeredis.

**Execution Notes (kritisch):**
- VPS hat **8 GB RAM** — Tests EINZELN ausführen: `pytest tests/unit/test_X.py::test_Y -x`. **NIE** `pytest tests/` ohne `-x` (OOM-Gefahr).
- **Bot NICHT restarten** bis explizit in Phase 15. Der Bot läuft gerade noch mit Gemini-Code im Memory — wir implementieren gegen disk-state, testen isoliert, rollen kontrolliert aus.
- Nach jedem Task: `git add <files> && git commit -m "<msg>"`. Der `commit-msg-hook.sh` validiert Conventional Commits — halte dich daran.
- **Keine neuen Dependencies außer `fakeredis`** (optional für Tests). Alles andere ist schon installiert.
- Der Design-Doc Anhang A listet die 12 PR-#123-Fehler, die das neue System abfangen muss. Jeder Gate-Test sollte sich auf einen dieser Fehler beziehen.
- **Sprache:** Code-Kommentare und Error-Messages auf Deutsch, analog zum Rest des Bots.

**Wo du Patterns kopierst:**
- Async DB-Layer: `src/integrations/security_engine/db.py` (SecurityDB, asyncpg Pool Management)
- Mixin-Pattern: `src/integrations/github_integration/event_handlers_mixin.py` (Methodensignaturen, Logging)
- Tests async DB: `tests/unit/test_deep_scan_mode.py` und `tests/unit/test_fix_providers.py`
- jsonschema-Validierung: `src/integrations/ai_engine.py` (`verify_fix` benutzt es schon)
- Prompt-Builder: `src/integrations/github_integration/ai_patch_notes_mixin.py` (`_classify_commit`, `_build_code_changes_context`)

---

## Phase 0: Vorbereitung (kein Code, nur Verständnis)

**Lies vor dem ersten Task:**
1. Design-Doc komplett: `docs/plans/2026-04-11-jules-secops-workflow-design.md`
2. Bestehender Handler-Flow: `src/integrations/github_integration/core.py` + `event_handlers_mixin.py`
3. Bestehende DB-Abstraktion: `src/integrations/security_engine/db.py`
4. Config-Ladelogik: `src/utils/config.py` (wie `self.config.foo.bar` aufgelöst wird)
5. PR #123 Kommentare (in Design-Doc Anhang A zusammengefasst) — verstehe *warum* jeder Gate existiert

---

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
  notification_channel: "🛡️-security-ops"
  escalation_channel: "🚨-alerts"
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
            logger.info("✅ JulesState connected to security_analyst DB")

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

    # Gleicher SHA → kein neuer Review
    retry = await state.try_claim_review(repo, pr, "sha_aaa", "worker-1")
    assert retry is None, "Gleicher SHA darf nicht erneut reviewt werden"


@pytest.mark.asyncio
async def test_try_claim_review_new_sha_allowed(state, seeded_row):
    repo, pr = seeded_row
    first = await state.try_claim_review(repo, pr, "sha_aaa", "worker-1")
    await state.mark_reviewed_sha(first.id, "sha_aaa")
    await state.release_lock(first.id, new_status="revision_requested")

    # Neuer SHA → erneuter Review erlaubt
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
            - PR-Row existiert nicht (kein Jules-Issue → ignorieren)
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
                    f"🔓 Jules Stale-Lock-Recovery: {count} Lock(s) älter als "
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

## Phase 4: JSON-Schema für Claude-Reviews

### Task 4.1: `jules_review.json` anlegen

**Files:**
- Create: `src/schemas/jules_review.json`

**Step 1: Schema-Datei schreiben**

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "JulesReview",
  "type": "object",
  "required": ["verdict", "summary", "blockers", "suggestions", "nits", "scope_check"],
  "properties": {
    "verdict": {
      "type": "string",
      "enum": ["approved", "revision_requested"]
    },
    "summary": {
      "type": "string",
      "maxLength": 800
    },
    "blockers": {
      "type": "array",
      "items": { "$ref": "#/$defs/issue" }
    },
    "suggestions": {
      "type": "array",
      "items": { "$ref": "#/$defs/issue" }
    },
    "nits": {
      "type": "array",
      "items": { "$ref": "#/$defs/issue" }
    },
    "scope_check": {
      "type": "object",
      "required": ["in_scope", "explanation"],
      "properties": {
        "in_scope": { "type": "boolean" },
        "explanation": { "type": "string", "maxLength": 500 }
      }
    }
  },
  "$defs": {
    "issue": {
      "type": "object",
      "required": ["title", "reason", "file", "severity"],
      "properties": {
        "title":         { "type": "string", "maxLength": 200 },
        "reason":        { "type": "string", "maxLength": 1000 },
        "file":          { "type": "string", "maxLength": 300 },
        "line":          { "type": ["integer", "null"] },
        "severity":      { "type": "string", "enum": ["critical", "high", "medium"] },
        "suggested_fix": { "type": "string", "maxLength": 1000 }
      }
    }
  }
}
```

**Step 2: jsonschema-Validierung testen**

Erstelle `tests/unit/test_jules_review_schema.py`:

```python
import json
import pathlib
import pytest
import jsonschema


SCHEMA_PATH = pathlib.Path("src/schemas/jules_review.json")


@pytest.fixture
def schema():
    return json.loads(SCHEMA_PATH.read_text())


def test_schema_loads(schema):
    jsonschema.Draft7Validator.check_schema(schema)


def test_valid_minimal_review_passes(schema):
    review = {
        "verdict": "approved",
        "summary": "Clean dependency bump.",
        "blockers": [],
        "suggestions": [],
        "nits": [],
        "scope_check": {"in_scope": True, "explanation": "Matches finding"},
    }
    jsonschema.validate(review, schema)  # raises on fail


def test_valid_revision_with_blocker_passes(schema):
    review = {
        "verdict": "revision_requested",
        "summary": "Scope violation detected.",
        "blockers": [{
            "title": "defu removal out of scope",
            "reason": "Finding was picomatch only.",
            "file": "web/package.json",
            "line": 23,
            "severity": "high",
            "suggested_fix": "Revert defu removal",
        }],
        "suggestions": [],
        "nits": [],
        "scope_check": {"in_scope": False, "explanation": "Extra removal"},
    }
    jsonschema.validate(review, schema)


def test_missing_scope_check_fails(schema):
    review = {
        "verdict": "approved",
        "summary": "x",
        "blockers": [],
        "suggestions": [],
        "nits": [],
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(review, schema)


def test_invalid_verdict_fails(schema):
    review = {
        "verdict": "maybe",
        "summary": "x",
        "blockers": [],
        "suggestions": [],
        "nits": [],
        "scope_check": {"in_scope": True, "explanation": "x"},
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(review, schema)
```

**Step 3: Tests ausführen**

```bash
pytest tests/unit/test_jules_review_schema.py -x -v
```

Erwartet: 5 passed.

**Step 4: Commit**

```bash
git add src/schemas/jules_review.json tests/unit/test_jules_review_schema.py
git commit -m "feat: jules_review.json Schema + Validierungstests"
```

---

## Phase 5: Prompt-Builder

### Task 5.1: `jules_review_prompt.py` Skeleton + `compute_verdict()`

**Files:**
- Create: `src/integrations/github_integration/jules_review_prompt.py`
- Create: `tests/unit/test_jules_review_prompt.py`

**Step 1: Tests für `compute_verdict` (reine Funktion, einfach zu testen)**

```python
# tests/unit/test_jules_review_prompt.py
import pytest
from src.integrations.github_integration.jules_review_prompt import (
    compute_verdict,
    build_review_prompt,
    truncate_diff,
)


def _base_review():
    return {
        "verdict": "approved",
        "summary": "x",
        "blockers": [],
        "suggestions": [],
        "nits": [],
        "scope_check": {"in_scope": True, "explanation": "x"},
    }


def test_compute_verdict_empty_blockers_in_scope_approved():
    assert compute_verdict(_base_review()) == "approved"


def test_compute_verdict_with_blockers_revision():
    r = _base_review()
    r["blockers"] = [{"title": "x", "reason": "y", "file": "z", "severity": "high"}]
    assert compute_verdict(r) == "revision_requested"


def test_compute_verdict_out_of_scope_revision():
    r = _base_review()
    r["scope_check"]["in_scope"] = False
    assert compute_verdict(r) == "revision_requested"


def test_compute_verdict_ignores_suggestions_and_nits():
    r = _base_review()
    r["suggestions"] = [{"title": "s", "reason": "r", "file": "f", "severity": "medium"}]
    r["nits"] = [{"title": "n", "reason": "r", "file": "f", "severity": "medium"}]
    assert compute_verdict(r) == "approved"
```

**Step 2: Implementation**

```python
# src/integrations/github_integration/jules_review_prompt.py
"""
Jules Review Prompt Builder.

Baut den Claude-Prompt für strukturierte PR-Reviews mit Learning-Kontext
aus agent_learning DB (few-shot examples + project knowledge).

Siehe docs/plans/2026-04-11-jules-secops-workflow-design.md §8.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

MAX_DIFF_CHARS_DEFAULT = 8000


def truncate_diff(diff: str, max_chars: int = MAX_DIFF_CHARS_DEFAULT) -> str:
    """Schneidet den Diff auf max_chars, mit Marker am Ende wenn gekürzt."""
    if len(diff) <= max_chars:
        return diff
    cut = diff[:max_chars]
    remaining = len(diff) - max_chars
    return cut + f"\n\n[... {remaining} Zeichen abgeschnitten ...]"


def compute_verdict(review: Dict[str, Any]) -> str:
    """
    Deterministische Verdict-Regel — überschreibt Claudes selbst gesetzten
    verdict nach der AI-Antwort. Verhindert Confidence-Oszillation.

    Regel: approved nur wenn (0 blockers) AND (scope in_scope=True).
    """
    if review.get("blockers"):
        return "revision_requested"
    scope = review.get("scope_check") or {}
    if not scope.get("in_scope", False):
        return "revision_requested"
    return "approved"


def build_review_prompt(
    *,
    finding: Dict[str, Any],
    project: str,
    diff: str,
    iteration: int,
    project_knowledge: List[str],
    few_shot_examples: List[Dict[str, Any]],
    max_diff_chars: int = MAX_DIFF_CHARS_DEFAULT,
) -> str:
    """
    Baut den kompletten Review-Prompt.

    Args:
        finding: Dict aus security_analyst.findings (title, severity, description, ...)
        project: z.B. "ZERODOX"
        diff: git diff Output
        iteration: aktuelle Review-Iteration (1-indexed)
        project_knowledge: List[str] aus agent_knowledge
        few_shot_examples: List[dict] aus jules_review_examples
    """
    knowledge_block = (
        "\n".join(f"- {k}" for k in project_knowledge)
        if project_knowledge else "(noch keine gelernten Konventionen)"
    )

    examples_block = _format_examples(few_shot_examples)
    diff_short = truncate_diff(diff, max_diff_chars)

    return f"""Du bist ein Senior Security-Reviewer. Dein Job: einen Pull-Request von
Jules (Googles AI-Coding-Agent) strukturiert prüfen und Blocker/Suggestions/Nits
klassifizieren.

**Grundregeln:**
- Sei STRIKT bei Security (CVEs, Credentials, Injection, Secrets).
- Sei PRAGMATISCH bei Stil (Nits blockieren NIE das Approval).
- Prüfe STRENG, dass der PR genau das Original-Finding löst und NICHTS anderes.
- Du darfst NICHT den Fix selbst schreiben — nur bewerten.

---

## Original-Finding

- **Projekt:** {project}
- **Iteration:** {iteration} of 5
- **Titel:** {finding.get('title', 'n/a')}
- **Severity:** {finding.get('severity', 'n/a')}
- **Kategorie:** {finding.get('category', 'n/a')}
- **CVE:** {finding.get('cve') or 'n/a'}

**Beschreibung:**
{finding.get('description', '(keine Beschreibung)')}

**Erwarteter Scope:** Nur die betroffenen Dateien/Module des Findings — kein Refactoring, keine unrelated Dependency-Changes, keine "Drive-by" Verbesserungen.

---

## Projekt-Konventionen (gelernt aus vorigen Reviews)

{knowledge_block}

---

## Beispiele (aus echten vergangenen Reviews dieses Projekts)

{examples_block}

---

## Diff des aktuellen Pull-Requests

```diff
{diff_short}
```

---

## Deine Aufgabe

Gib ausschließlich folgendes JSON zurück (ohne Markdown-Fence, ohne Kommentare, ohne Text davor/danach):

```json
{{
  "verdict": "approved" oder "revision_requested",
  "summary": "1-3 Sätze was der PR macht",
  "blockers": [
    {{
      "title": "Kurze Zusammenfassung des Problems",
      "reason": "Warum ist das ein Blocker",
      "file": "web/package.json",
      "line": 23,
      "severity": "critical|high|medium",
      "suggested_fix": "Wie Jules es fixen soll"
    }}
  ],
  "suggestions": [ ... gleiches Format ... ],
  "nits": [ ... gleiches Format ... ],
  "scope_check": {{
    "in_scope": true/false,
    "explanation": "Bleibt der PR im Scope des Findings?"
  }}
}}
```

**Definitionen:**
- **BLOCKER:** Security-Risk, Breaking Change, Out-of-Scope Refactoring, fehlende Acceptance-Criteria, neue CVEs, gelöschte Tests.
- **SUGGESTION:** Verbesserungsvorschlag ohne Blocker-Charakter (Dep-Dedup, Logging-Qualität, Performance).
- **NIT:** Reiner Stil (Naming, Formatierung, Trailing-Whitespace).

**Wichtig:**
- Blockers leer + in_scope=true → approved. Sonst revision_requested.
- Dein `verdict`-Feld wird nach der Antwort von einer deterministischen Regel überschrieben — du musst es trotzdem korrekt setzen.
- Erwähne KEIN Confidence-Score (das war Teil des PR #123 Problems).
"""


def _format_examples(examples: List[Dict[str, Any]]) -> str:
    if not examples:
        return "(noch keine Beispiele für dieses Projekt)"
    lines = []
    for ex in examples[:4]:
        outcome = ex.get("outcome", "unknown")
        summary = ex.get("diff_summary", "")
        review = ex.get("review_json", {})
        if isinstance(review, str):
            try:
                review = json.loads(review)
            except Exception:
                review = {}
        verdict = review.get("verdict", "unknown")
        blockers_n = len(review.get("blockers", []))
        lines.append(
            f"- **[{outcome}]** {summary} → verdict={verdict}, blockers={blockers_n}"
        )
    return "\n".join(lines)
```

**Step 3: Tests laufen — PASS**

```bash
pytest tests/unit/test_jules_review_prompt.py -x -v
```

Erwartet: 4 passed.

**Step 4: Commit**

```bash
git add src/integrations/github_integration/jules_review_prompt.py tests/unit/test_jules_review_prompt.py
git commit -m "feat: Jules Review Prompt-Builder + compute_verdict"
```

---

### Task 5.2: Prompt-Builder Integration-Test mit echtem Finding

**Files:**
- Modify: `tests/unit/test_jules_review_prompt.py`

**Step 1: Test hinzufügen**

```python
def test_build_review_prompt_contains_all_blocks():
    finding = {
        "title": "ReDoS in picomatch",
        "severity": "high",
        "category": "npm_audit",
        "cve": "CVE-2024-45296",
        "description": "Vulnerable regex in picomatch <4.0.4",
    }
    prompt = build_review_prompt(
        finding=finding,
        project="ZERODOX",
        diff="diff --git a/x b/x\n+new line\n",
        iteration=2,
        project_knowledge=["ZERODOX nutzt Prisma, Schema-Änderungen brauchen migrate"],
        few_shot_examples=[{
            "outcome": "good_catch",
            "diff_summary": "Dep bump mit Drive-by removal",
            "review_json": {"verdict": "revision_requested", "blockers": [{"x": 1}]},
        }],
    )
    assert "ReDoS in picomatch" in prompt
    assert "CVE-2024-45296" in prompt
    assert "Iteration: 2 of 5" in prompt
    assert "Prisma" in prompt
    assert "good_catch" in prompt
    assert "verdict" in prompt
    assert "diff --git" in prompt


def test_truncate_diff_cuts_and_marks():
    long = "x" * 10000
    out = truncate_diff(long, max_chars=100)
    assert len(out) < 200
    assert "abgeschnitten" in out


def test_truncate_diff_short_unchanged():
    short = "abc"
    assert truncate_diff(short) == "abc"
```

**Step 2: Tests — PASS**

```bash
pytest tests/unit/test_jules_review_prompt.py -x -v
```

Erwartet: 7 passed.

**Step 3: Commit**

```bash
git add tests/unit/test_jules_review_prompt.py
git commit -m "test: jules_review_prompt Integration-Assertions"
```

---

### Task 5.3: Learning-Context-Loader (`jules_learning.py`)

**Files:**
- Create: `src/integrations/github_integration/jules_learning.py`
- Create: `tests/unit/test_jules_learning.py`

**Step 1: Test schreiben**

```python
# tests/unit/test_jules_learning.py
import os
import pytest
from src.integrations.github_integration.jules_learning import JulesLearning


DSN = os.environ.get("AGENT_LEARNING_DB_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="AGENT_LEARNING_DB_URL nicht gesetzt")


@pytest.fixture
async def learning():
    l = JulesLearning(DSN)
    await l.connect()
    async with l._pool.acquire() as conn:
        await conn.execute("DELETE FROM jules_review_examples WHERE project LIKE 'test_%'")
    yield l
    async with l._pool.acquire() as conn:
        await conn.execute("DELETE FROM jules_review_examples WHERE project LIKE 'test_%'")
    await l.close()


@pytest.mark.asyncio
async def test_fetch_few_shot_empty_when_no_examples(learning):
    out = await learning.fetch_few_shot_examples("test_empty", limit=3)
    assert out == []


@pytest.mark.asyncio
async def test_fetch_few_shot_orders_by_weight(learning):
    async with learning._pool.acquire() as conn:
        for i, (outcome, weight) in enumerate([
            ("good_catch", 1.0),
            ("good_catch", 2.5),
            ("false_positive", 0.8),
        ]):
            await conn.execute(
                """
                INSERT INTO jules_review_examples (project, diff_summary, review_json, outcome, weight)
                VALUES ($1, $2, '{}', $3, $4)
                """,
                "test_weight", f"example_{i}", outcome, weight,
            )

    out = await learning.fetch_few_shot_examples("test_weight", limit=10)
    assert len(out) == 3
    assert out[0]["weight"] >= out[1]["weight"] >= out[2]["weight"]


@pytest.mark.asyncio
async def test_fetch_project_knowledge_returns_strings(learning):
    # Wir gehen davon aus dass agent_knowledge bereits existiert (teil von agent_learning)
    # Hier testen wir nur, dass leere Projekte leere Listen zurückgeben
    out = await learning.fetch_project_knowledge("test_knowledge_empty", limit=10)
    assert isinstance(out, list)
```

**Step 2: Implementation**

```python
# src/integrations/github_integration/jules_learning.py
"""
Jules Learning — Kontext-Loader aus agent_learning DB.

Stellt few-shot-Examples und Projekt-Knowledge für den Review-Prompt bereit.
Schreibt später (Phase 14) klassifizierte Outcomes zurück.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

import asyncpg

logger = logging.getLogger(__name__)


class JulesLearning:
    def __init__(self, dsn: str):
        self._dsn = dsn
        self._pool: Optional[asyncpg.Pool] = None

    async def connect(self) -> None:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                self._dsn, min_size=1, max_size=3, command_timeout=10
            )
            logger.info("✅ JulesLearning connected to agent_learning DB")

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None

    async def fetch_few_shot_examples(
        self, project: str, limit: int = 3
    ) -> List[Dict[str, Any]]:
        """Liefert die besten Examples nach weight DESC, created_at DESC."""
        sql = """
            SELECT project, pr_ref, diff_summary, review_json, outcome, weight, created_at
            FROM jules_review_examples
            WHERE project = $1
            ORDER BY weight DESC, created_at DESC
            LIMIT $2
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, project, limit)
        out = []
        for r in rows:
            d = dict(r)
            # review_json ist JSONB — asyncpg gibt String zurück wenn nicht decoded
            if isinstance(d.get("review_json"), str):
                try:
                    d["review_json"] = json.loads(d["review_json"])
                except Exception:
                    d["review_json"] = {}
            out.append(d)
        return out

    async def fetch_project_knowledge(
        self, project: str, limit: int = 10
    ) -> List[str]:
        """
        Liefert Strings aus agent_knowledge für agent_name='jules_reviewer' und
        project=$1. Returns [] wenn Tabelle fehlt (Soft-Fail).
        """
        sql = """
            SELECT content FROM agent_knowledge
            WHERE agent_name = 'jules_reviewer'
              AND project = $1
            ORDER BY confidence DESC NULLS LAST, updated_at DESC
            LIMIT $2
        """
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(sql, project, limit)
            return [r["content"] for r in rows if r["content"]]
        except asyncpg.UndefinedTableError:
            logger.warning("agent_knowledge-Tabelle fehlt — Learning-Context leer")
            return []
        except asyncpg.UndefinedColumnError as e:
            # Falls Schema leicht anders: Soft-Fail, log, leer zurück
            logger.warning(f"agent_knowledge Schema-Mismatch: {e}")
            return []
```

**Step 3: Tests — PASS**

```bash
export AGENT_LEARNING_DB_URL=$(python -c "from src.utils.config import Config; print(Config().agent_learning_dsn)")
pytest tests/unit/test_jules_learning.py -x -v
```

Erwartet: 3 passed.

**Step 4: Commit**

```bash
git add src/integrations/github_integration/jules_learning.py tests/unit/test_jules_learning.py
git commit -m "feat: JulesLearning — Few-Shot + Projekt-Knowledge-Loader"
```

---

## Phase 6: AI-Engine `review_pr()` Methode

### Task 6.1: Neue Methode in `ai_engine.py` mit Schema-Validierung

**Files:**
- Modify: `src/integrations/ai_engine.py`
- Create: `tests/unit/test_ai_engine_review_pr.py`

**Step 1: Test mit gemocktem Provider**

```python
# tests/unit/test_ai_engine_review_pr.py
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.integrations.ai_engine import AIEngine


@pytest.fixture
def engine_with_mock_claude():
    """AIEngine mit gemocktem ClaudeProvider, der festes JSON zurückgibt."""
    engine = MagicMock(spec=AIEngine)
    # Wir testen nur die neue Methode — reale AIEngine-Instanzierung umgehen
    return engine


def _valid_review_json():
    return {
        "verdict": "approved",
        "summary": "Clean upgrade",
        "blockers": [],
        "suggestions": [],
        "nits": [],
        "scope_check": {"in_scope": True, "explanation": "matches finding"},
    }


@pytest.mark.asyncio
async def test_review_pr_returns_validated_dict(monkeypatch):
    from src.integrations import ai_engine as aie

    engine = aie.AIEngine.__new__(aie.AIEngine)  # Bypass __init__
    engine.logger = __import__("logging").getLogger("test")

    # Mock den Claude-Call
    async def fake_query_raw(prompt, model=None, timeout=None):
        return json.dumps(_valid_review_json())
    engine.claude = MagicMock()
    engine.claude.query_raw = AsyncMock(side_effect=fake_query_raw)

    result = await engine.review_pr(
        diff="diff --git a/x b/x",
        finding_context={"title": "t", "severity": "high", "description": "d"},
        project="test_project",
        iteration=1,
        project_knowledge=[],
        few_shot_examples=[],
    )

    assert result is not None
    assert result["verdict"] == "approved"
    assert result["scope_check"]["in_scope"] is True


@pytest.mark.asyncio
async def test_review_pr_invalid_json_returns_none():
    from src.integrations import ai_engine as aie

    engine = aie.AIEngine.__new__(aie.AIEngine)
    engine.logger = __import__("logging").getLogger("test")

    async def fake_bad(prompt, model=None, timeout=None):
        return "not json at all"
    engine.claude = MagicMock()
    engine.claude.query_raw = AsyncMock(side_effect=fake_bad)

    result = await engine.review_pr(
        diff="d", finding_context={}, project="p", iteration=1,
        project_knowledge=[], few_shot_examples=[],
    )
    assert result is None


@pytest.mark.asyncio
async def test_review_pr_schema_invalid_returns_none():
    from src.integrations import ai_engine as aie

    engine = aie.AIEngine.__new__(aie.AIEngine)
    engine.logger = __import__("logging").getLogger("test")

    async def fake_missing_fields(prompt, model=None, timeout=None):
        return json.dumps({"verdict": "approved"})  # fehlende felder
    engine.claude = MagicMock()
    engine.claude.query_raw = AsyncMock(side_effect=fake_missing_fields)

    result = await engine.review_pr(
        diff="d", finding_context={}, project="p", iteration=1,
        project_knowledge=[], few_shot_examples=[],
    )
    assert result is None


@pytest.mark.asyncio
async def test_review_pr_verdict_overridden_deterministic():
    """Claude sagt approved, aber scope_check.in_scope=False → überschreiben auf revision."""
    from src.integrations import ai_engine as aie

    engine = aie.AIEngine.__new__(aie.AIEngine)
    engine.logger = __import__("logging").getLogger("test")

    bad = _valid_review_json()
    bad["scope_check"]["in_scope"] = False  # aber verdict bleibt approved

    async def fake(prompt, model=None, timeout=None):
        return json.dumps(bad)
    engine.claude = MagicMock()
    engine.claude.query_raw = AsyncMock(side_effect=fake)

    result = await engine.review_pr(
        diff="d", finding_context={}, project="p", iteration=1,
        project_knowledge=[], few_shot_examples=[],
    )
    assert result["verdict"] == "revision_requested"
```

**Step 2: Implementation in `ai_engine.py`**

Suche das Ende der `AIEngine`-Klasse (nach `verify_fix` oder ähnlich) und füge folgende Methode an. Die Imports `json`, `jsonschema`, `pathlib`, `List`, `Dict`, `Any`, `Optional` müssen oben existieren.

```python
    async def review_pr(
        self,
        *,
        diff: str,
        finding_context: Dict[str, Any],
        project: str,
        iteration: int,
        project_knowledge: List[str],
        few_shot_examples: List[Dict[str, Any]],
        max_diff_chars: int = 8000,
    ) -> Optional[Dict[str, Any]]:
        """
        Strukturiertes PR-Review via Claude Opus.

        Der Prompt wird via jules_review_prompt.build_review_prompt() gebaut,
        das Ergebnis gegen src/schemas/jules_review.json validiert, und der
        verdict-Feld deterministisch via compute_verdict() überschrieben.

        Returns:
            Validiertes Review-Dict mit forciertem verdict, oder None bei:
            - AI-Call fehlgeschlagen / Timeout
            - Non-JSON Response (auch nach Fence-Strip)
            - jsonschema-Validation Fail
        """
        from src.integrations.github_integration.jules_review_prompt import (
            build_review_prompt,
            compute_verdict,
        )
        import json as _json
        import pathlib as _pl
        import jsonschema as _js

        prompt = build_review_prompt(
            finding=finding_context,
            project=project,
            diff=diff,
            iteration=iteration,
            project_knowledge=project_knowledge,
            few_shot_examples=few_shot_examples,
            max_diff_chars=max_diff_chars,
        )

        try:
            raw = await self.claude.query_raw(
                prompt, model="thinking", timeout=300
            )
        except Exception as e:
            self.logger.error(f"[jules] Claude-Call failed: {e}")
            return None

        if not raw:
            self.logger.error("[jules] Claude returned empty response")
            return None

        # Strip markdown fences falls vorhanden
        clean = raw.strip()
        if clean.startswith("```"):
            lines = clean.split("\n")
            clean = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])

        try:
            review = _json.loads(clean)
        except _json.JSONDecodeError as e:
            self.logger.error(f"[jules] JSON parse failed: {e} / raw[:200]={raw[:200]!r}")
            return None

        # Schema-Validierung
        schema_path = _pl.Path(__file__).parent.parent / "schemas" / "jules_review.json"
        try:
            schema = _json.loads(schema_path.read_text())
            _js.validate(review, schema)
        except _js.ValidationError as e:
            self.logger.error(f"[jules] Schema validation failed: {e.message}")
            return None
        except FileNotFoundError:
            self.logger.error(f"[jules] Schema not found at {schema_path}")
            return None

        # Deterministischer Verdict-Override
        review["verdict"] = compute_verdict(review)

        self.logger.info(
            f"[jules] review ok: verdict={review['verdict']} "
            f"blockers={len(review['blockers'])} "
            f"suggestions={len(review['suggestions'])} "
            f"nits={len(review['nits'])} "
            f"in_scope={review['scope_check']['in_scope']}"
        )
        return review
```

**Step 3: Tests — PASS**

```bash
pytest tests/unit/test_ai_engine_review_pr.py -x -v
```

Erwartet: 4 passed.

**Step 4: Commit**

```bash
git add src/integrations/ai_engine.py tests/unit/test_ai_engine_review_pr.py
git commit -m "feat: ai_engine.review_pr — strukturiertes PR-Review mit Schema-Validierung"
```

---

*Phase 6 abgeschlossen.*

---

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

## Phase 8: `JulesWorkflowMixin` + Handler-Wiring

### Task 8.1: Mixin-Skeleton + `should_review()` Gate-Pipeline

**Files:**
- Create: `src/integrations/github_integration/jules_workflow_mixin.py`
- Create: `tests/unit/test_jules_workflow_mixin.py`

**Step 1: Mixin-Skeleton**

```python
# src/integrations/github_integration/jules_workflow_mixin.py
"""
Jules SecOps Workflow Mixin.

Handler-Eintritt für GitHub pull_request und issue_comment Events.
Koordiniert die Gate-Pipeline, den AI-Call und das Comment-Management.

Siehe docs/plans/2026-04-11-jules-secops-workflow-design.md §4-§6.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from .jules_gates import (
    ALLOWED_TRIGGERS,
    ReviewDecision,
    check_circuit_breaker,
    gate_cooldown,
    gate_iteration_cap,
    gate_time_cap,
    gate_trigger_whitelist,
)
from .jules_state import JulesReviewRow

logger = logging.getLogger(__name__)


class JulesWorkflowMixin:
    """
    Wird in GitHubIntegration via Mixin-Pattern eingehängt.

    Erwartete Attribute auf self (von core.py bereitgestellt):
    - self.jules_state: JulesState
    - self.jules_learning: JulesLearning
    - self.ai_service: AIEngine
    - self.redis: Redis-Client (async)
    - self.config: Config-Instanz mit .jules_workflow Block
    - self.logger: Logger
    - self.bot: Bot-Referenz (für Discord-Logger)
    """

    async def should_review(
        self, repo: str, pr_number: int, head_sha: str, event_type: str
    ) -> ReviewDecision:
        """
        Gate-Pipeline — billig zuerst, teuer später.
        Gibt ReviewDecision zurück; proceed=True nur bei allen Gates passed.
        """
        cfg = self.config.jules_workflow

        # Gate 0: Feature-Flag
        if not cfg.enabled:
            return ReviewDecision.skip("feature_disabled")

        # Gate 1: Trigger-Whitelist
        blocked = gate_trigger_whitelist(event_type)
        if blocked:
            logger.debug(f"[jules] {repo}#{pr_number} gate=trigger skip={blocked}")
            return ReviewDecision.skip(blocked)

        # Gate 5: Circuit-Breaker (vor DB-Claim — billig)
        is_open, count = await check_circuit_breaker(
            self.redis,
            repo,
            threshold=cfg.circuit_breaker.max_reviews_per_hour,
            ttl_seconds=cfg.circuit_breaker.pause_duration_seconds,
        )
        if is_open:
            logger.warning(
                f"[jules] 🚨 Circuit Breaker OPEN for {repo} (count={count}, skipping)"
            )
            await self._jules_notify_discord_alarm(
                f"Jules Circuit Breaker OPEN für {repo}: {count} Reviews in 1h"
            )
            return ReviewDecision.skip("circuit_breaker_open")

        # Gate 2: Atomic Claim (DB)
        row = await self.jules_state.try_claim_review(
            repo, pr_number, head_sha, self.jules_state.process_id
        )
        if row is None:
            logger.info(f"[jules] {repo}#{pr_number} skip=already_reviewed_or_locked sha={head_sha[:7]}")
            return ReviewDecision.skip("already_reviewed_or_locked")

        # Nach Claim: row ist jetzt im status='reviewing' Lock.
        # Wenn wir danach skip machen, MÜSSEN wir den Lock freigeben.

        # Gate 4: Iteration-Cap
        if (reason := gate_iteration_cap(row, cfg.max_iterations)):
            logger.warning(f"[jules] {repo}#{pr_number} ESCALATE {reason}")
            await self._jules_escalate_to_human(row, reason)
            return ReviewDecision.skip(reason)

        # Gate 6: Time-Cap
        if (reason := gate_time_cap(row, cfg.max_hours_per_pr)):
            logger.warning(f"[jules] {repo}#{pr_number} ESCALATE {reason}")
            await self._jules_escalate_to_human(row, reason)
            return ReviewDecision.skip(reason)

        # Gate 3: Cooldown
        if (reason := gate_cooldown(row, cfg.cooldown_seconds)):
            logger.info(f"[jules] {repo}#{pr_number} skip=cooldown")
            await self.jules_state.release_lock(row.id, row.status_before_reviewing or "revision_requested")
            return ReviewDecision.skip(reason)

        return ReviewDecision.advance(row)

    # Diese Methoden werden in späteren Tasks implementiert —
    # Skeleton jetzt, damit der Mixin importierbar ist
    async def _jules_escalate_to_human(self, row: JulesReviewRow, reason: str) -> None:
        logger.warning(f"[jules] STUB _jules_escalate_to_human row={row.id} reason={reason}")
        await self.jules_state.mark_terminal(row.id, "escalated")

    async def _jules_notify_discord_alarm(self, msg: str) -> None:
        logger.warning(f"[jules] STUB _jules_notify_discord_alarm: {msg}")
```

Wichtig: Der Code nutzt `row.status_before_reviewing`, was es noch nicht gibt. Das ist ein Platzhalter für den Cooldown-Fall — wir brauchen eigentlich den Status **vor** dem Claim, den die atomic UPDATE überschrieben hat. Das ist ein Bug in meiner Formulierung — korrekter Weg: wir kennen den Status vor dem Claim nicht, weil RETURNING \* nur den neuen Zustand zurückgibt. Wir setzen stattdessen auf `revision_requested` als sicheren Default, weil das der häufigste "non-pending-non-terminal" Status ist.

Fix: Ersetze die Zeile
```python
await self.jules_state.release_lock(row.id, row.status_before_reviewing or "revision_requested")
```
mit
```python
# Cooldown greift — Lock freigeben, Status zurück auf revision_requested
# (der häufigste Grund für Gate-Eintritt; pending wird beim ersten Review erreicht)
prev = "pending" if row.iteration_count == 0 else "revision_requested"
await self.jules_state.release_lock(row.id, prev)
```

**Step 2: Basis-Tests**

```python
# tests/unit/test_jules_workflow_mixin.py
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
import fakeredis.aioredis

from src.integrations.github_integration.jules_workflow_mixin import JulesWorkflowMixin
from src.integrations.github_integration.jules_state import JulesReviewRow


def _row(**overrides):
    now = datetime.now(timezone.utc)
    defaults = dict(
        id=1, repo="X", pr_number=1, issue_number=None, finding_id=None,
        status="reviewing", last_reviewed_sha=None, iteration_count=0,
        last_review_at=None, lock_acquired_at=now, lock_owner="w",
        review_comment_id=None, last_review_json=None, last_blockers=None,
        tokens_consumed=0, created_at=now, updated_at=now,
        closed_at=None, human_override=False,
    )
    defaults.update(overrides)
    return JulesReviewRow(**defaults)


class _Cfg:
    """Minimal config mock — was self.config.jules_workflow liefern würde."""
    class circuit_breaker:
        max_reviews_per_hour = 20
        pause_duration_seconds = 3600
    enabled = True
    max_iterations = 5
    cooldown_seconds = 300
    max_hours_per_pr = 2


class _Harness(JulesWorkflowMixin):
    """Bare-bones Instanz für Tests."""
    def __init__(self, jules_state, redis):
        self.jules_state = jules_state
        self.redis = redis
        self.config = MagicMock()
        self.config.jules_workflow = _Cfg()
        self.config.jules_workflow.circuit_breaker = _Cfg.circuit_breaker
        self.bot = MagicMock()


@pytest_asyncio.fixture
async def harness():
    redis = fakeredis.aioredis.FakeRedis()
    state = MagicMock()
    state.process_id = "test-worker"
    state.try_claim_review = AsyncMock(return_value=None)
    state.mark_terminal = AsyncMock()
    state.release_lock = AsyncMock()
    return _Harness(state, redis)


@pytest.mark.asyncio
async def test_should_review_skips_when_disabled(harness):
    harness.config.jules_workflow.enabled = False
    d = await harness.should_review("X", 1, "sha", "pull_request:opened")
    assert d.proceed is False
    assert d.reason == "feature_disabled"


@pytest.mark.asyncio
async def test_should_review_skips_issue_comment(harness):
    """REGRESSION: PR #123 Hauptursache."""
    d = await harness.should_review("X", 1, "sha", "issue_comment:created")
    assert d.proceed is False
    assert d.reason == "blocked_trigger"
    harness.jules_state.try_claim_review.assert_not_called()


@pytest.mark.asyncio
async def test_should_review_skips_when_claim_fails(harness):
    harness.jules_state.try_claim_review.return_value = None
    d = await harness.should_review("X", 1, "sha", "pull_request:opened")
    assert d.proceed is False
    assert d.reason == "already_reviewed_or_locked"


@pytest.mark.asyncio
async def test_should_review_escalates_on_max_iterations(harness):
    harness.jules_state.try_claim_review.return_value = _row(iteration_count=5)
    d = await harness.should_review("X", 1, "sha", "pull_request:opened")
    assert d.proceed is False
    assert d.reason == "max_iterations"
    harness.jules_state.mark_terminal.assert_called_once()


@pytest.mark.asyncio
async def test_should_review_escalates_on_timeout(harness):
    harness.jules_state.try_claim_review.return_value = _row(
        created_at=datetime.now(timezone.utc) - timedelta(hours=3)
    )
    d = await harness.should_review("X", 1, "sha", "pull_request:opened")
    assert d.proceed is False
    assert d.reason == "timeout_per_pr"


@pytest.mark.asyncio
async def test_should_review_skips_on_cooldown(harness):
    harness.jules_state.try_claim_review.return_value = _row(
        last_review_at=datetime.now(timezone.utc) - timedelta(seconds=60)
    )
    d = await harness.should_review("X", 1, "sha", "pull_request:opened")
    assert d.proceed is False
    assert d.reason == "cooldown"
    harness.jules_state.release_lock.assert_called_once()


@pytest.mark.asyncio
async def test_should_review_advances_on_fresh_row(harness):
    harness.jules_state.try_claim_review.return_value = _row(iteration_count=0)
    d = await harness.should_review("X", 1, "sha", "pull_request:opened")
    assert d.proceed is True
    assert d.row is not None
```

**Step 3: Tests — PASS**

```bash
pytest tests/unit/test_jules_workflow_mixin.py -x -v
```

Erwartet: 7 passed. Falls 1 Test wegen `row.status_before_reviewing` fehlschlägt, stelle sicher, dass du den im Step 1 angegebenen Fix angewendet hast.

**Step 4: Commit**

```bash
git add src/integrations/github_integration/jules_workflow_mixin.py tests/unit/test_jules_workflow_mixin.py
git commit -m "feat: JulesWorkflowMixin.should_review — Gate-Pipeline mit Escalation"
```

---

### Task 8.2: `handle_pr_event` — PR-Event-Eintritt

**Files:**
- Modify: `src/integrations/github_integration/jules_workflow_mixin.py`
- Modify: `tests/unit/test_jules_workflow_mixin.py`

**Step 1: Tests für `handle_jules_pr_event`**

```python
@pytest.mark.asyncio
async def test_handle_jules_pr_event_ignores_non_jules_pr(harness):
    """PR ohne jules-Label und ohne Fixes-Reference wird ignoriert."""
    harness._jules_is_jules_pr = AsyncMock(return_value=False)

    payload = {
        "action": "opened",
        "pull_request": {
            "number": 1,
            "head": {"sha": "abc"},
            "user": {"login": "SomeHuman"},
            "body": "",
            "labels": [],
        },
        "repository": {"name": "X", "owner": {"login": "o"}},
    }
    await harness.handle_jules_pr_event(payload)
    harness.jules_state.try_claim_review.assert_not_called()


@pytest.mark.asyncio
async def test_handle_jules_pr_event_calls_should_review(harness):
    """PR mit jules-Label triggert should_review."""
    harness._jules_is_jules_pr = AsyncMock(return_value=True)
    harness.should_review = AsyncMock(return_value=MagicMock(proceed=False, reason="cooldown"))
    harness._jules_run_review_pipeline = AsyncMock()

    payload = {
        "action": "opened",
        "pull_request": {
            "number": 1,
            "head": {"sha": "abc123"},
            "user": {"login": "google-labs-jules[bot]"},
            "body": "Fixes #42",
            "labels": [{"name": "jules"}],
        },
        "repository": {"name": "X", "owner": {"login": "o"}},
    }
    await harness.handle_jules_pr_event(payload)
    harness.should_review.assert_called_once_with("X", 1, "abc123", "pull_request:opened")
    harness._jules_run_review_pipeline.assert_not_called()


@pytest.mark.asyncio
async def test_handle_jules_pr_event_runs_pipeline_on_proceed(harness):
    harness._jules_is_jules_pr = AsyncMock(return_value=True)
    fake_row = _row()
    harness.should_review = AsyncMock(return_value=MagicMock(proceed=True, row=fake_row))
    harness._jules_run_review_pipeline = AsyncMock()

    payload = {
        "action": "synchronize",
        "pull_request": {
            "number": 1,
            "head": {"sha": "def456"},
            "user": {"login": "google-labs-jules[bot]"},
            "body": "Fixes #42",
            "labels": [{"name": "jules"}],
        },
        "repository": {"name": "X", "owner": {"login": "o"}},
    }
    await harness.handle_jules_pr_event(payload)
    harness._jules_run_review_pipeline.assert_called_once()
```

**Step 2: Implementation im Mixin**

Hänge an `JulesWorkflowMixin`:

```python
    async def handle_jules_pr_event(self, payload: Dict[str, Any]) -> None:
        """
        Eintrittspunkt für pull_request Events.
        Wird von WebhookMixin dispatcher aufgerufen (vor dem normalen EventHandlersMixin).
        Returns ohne Aktion wenn's kein Jules-PR ist — der normale Handler läuft danach.
        """
        try:
            action = payload.get("action", "")
            pr = payload.get("pull_request") or {}
            repo = (payload.get("repository") or {}).get("name", "")
            pr_number = pr.get("number")
            head_sha = (pr.get("head") or {}).get("sha", "")

            if not (repo and pr_number and head_sha):
                return

            # Event-Type für Gate 1
            event_type = f"pull_request:{action}"
            if event_type not in ALLOWED_TRIGGERS:
                logger.debug(f"[jules] {repo}#{pr_number} action={action} not in ALLOWED_TRIGGERS")
                return

            # Ist das überhaupt ein Jules-PR?
            is_jules = await self._jules_is_jules_pr(pr, repo)
            if not is_jules:
                return

            logger.info(f"[jules] Detected Jules PR {repo}#{pr_number} sha={head_sha[:7]} action={action}")

            decision = await self.should_review(repo, pr_number, head_sha, event_type)
            if not decision.proceed:
                logger.info(f"[jules] {repo}#{pr_number} skip reason={decision.reason}")
                return

            await self._jules_run_review_pipeline(
                repo=repo, pr_number=pr_number, head_sha=head_sha,
                pr_payload=pr, row=decision.row,
            )
        except Exception:
            logger.exception("[jules] handle_jules_pr_event crashed")
            # NIE eine Exception zurück zum Webhook-Handler — Webhook-200-Immer-Regel

    async def _jules_is_jules_pr(self, pr: Dict[str, Any], repo: str) -> bool:
        """
        Erkennt Jules-PRs robust:
        1. Label 'jules' gesetzt
        2. ODER Author == 'google-labs-jules[bot]'
        3. ODER PR-Body enthält 'Fixes #N' UND das Issue hat Label 'jules'
        """
        labels = [l.get("name", "").lower() for l in (pr.get("labels") or [])]
        if "jules" in labels:
            return True

        author = ((pr.get("user") or {}).get("login") or "").lower()
        if author.startswith("google-labs-jules"):
            return True

        # Body-Reference-Check: optional, braucht Issue-Lookup — skippen wenn teuer
        # (wir verlassen uns primär auf Label)
        return False

    async def _jules_run_review_pipeline(
        self,
        *,
        repo: str,
        pr_number: int,
        head_sha: str,
        pr_payload: Dict[str, Any],
        row: JulesReviewRow,
    ) -> None:
        """STUB — wird in Task 8.3 implementiert."""
        logger.info(f"[jules] STUB _jules_run_review_pipeline {repo}#{pr_number}")
        # Lock freigeben damit Tests nicht hängen
        await self.jules_state.release_lock(row.id, "pending")
```

**Step 3: Tests — PASS**

```bash
pytest tests/unit/test_jules_workflow_mixin.py -x -v
```

Erwartet: 10 passed.

**Step 4: Commit**

```bash
git add src/integrations/github_integration/jules_workflow_mixin.py tests/unit/test_jules_workflow_mixin.py
git commit -m "feat: handle_jules_pr_event — PR-Event-Eintritt mit Jules-Detection"
```

---

### Task 8.3: `_jules_run_review_pipeline` — der AI-Review-Lauf

**Files:**
- Modify: `src/integrations/github_integration/jules_workflow_mixin.py`

**Step 1: Implementation — ersetze den STUB aus Task 8.2**

```python
    async def _jules_run_review_pipeline(
        self,
        *,
        repo: str,
        pr_number: int,
        head_sha: str,
        pr_payload: Dict[str, Any],
        row: JulesReviewRow,
    ) -> None:
        """
        Führt den eigentlichen Review durch:
        1. Diff holen via gh
        2. Finding-Kontext + Learning-Kontext laden
        3. ai_engine.review_pr() aufrufen
        4. Verdict anwenden → Comment posten/editieren
        5. DB-State aktualisieren, Lock freigeben
        """
        cfg = self.config.jules_workflow
        owner = (pr_payload.get("base") or {}).get("repo", {}).get("owner", {}).get("login") or \
                (pr_payload.get("head") or {}).get("repo", {}).get("owner", {}).get("login") or \
                "Commandershadow9"

        iteration = row.iteration_count + 1

        try:
            # Dry-Run-Mode: kein AI-Call, kein Write
            if getattr(cfg, "dry_run", False):
                logger.info(f"[jules] DRY-RUN {repo}#{pr_number} iter={iteration}")
                await self.jules_state.release_lock(row.id, "revision_requested")
                return

            # 1. Diff holen
            diff = await self._jules_fetch_pr_diff(owner, repo, pr_number)
            if diff is None:
                await self._jules_escalate_to_human(row, "diff_fetch_failed")
                return

            # 2. Finding-Kontext
            finding_ctx = await self._jules_load_finding_context(row.finding_id)
            # Falls kein Finding: degraded mode, nutze PR-Body
            if finding_ctx is None:
                finding_ctx = {
                    "title": (pr_payload.get("title") or "n/a"),
                    "severity": "medium",
                    "description": (pr_payload.get("body") or "")[:2000],
                    "category": "code_fix",
                    "cve": None,
                }

            # 3. Learning-Kontext (tolerant — Fehler darf Review nicht blocken)
            knowledge = []
            examples = []
            try:
                knowledge = await self.jules_learning.fetch_project_knowledge(
                    repo, limit=cfg.project_knowledge_limit
                )
                examples = await self.jules_learning.fetch_few_shot_examples(
                    repo, limit=cfg.few_shot_examples
                )
            except Exception as e:
                logger.warning(f"[jules] Learning-Context fetch failed (tolerant): {e}")

            # 4. AI-Call
            review = await self.ai_service.review_pr(
                diff=diff,
                finding_context=finding_ctx,
                project=repo,
                iteration=iteration,
                project_knowledge=knowledge,
                few_shot_examples=examples,
                max_diff_chars=cfg.max_diff_chars,
            )

            if review is None:
                await self._jules_escalate_to_human(row, "ai_review_failed")
                return

            # 5. Comment posten/editieren + DB-Update
            await self._jules_post_or_edit_review_comment(
                owner=owner, repo=repo, pr_number=pr_number,
                review=review, row=row, iteration=iteration,
            )

            # SHA + iteration_count updaten
            await self.jules_state.mark_reviewed_sha(row.id, head_sha)
            await self.jules_state.store_review_result(
                row.id, review, review.get("blockers", []), tokens=0
            )

            # Verdict anwenden
            if review["verdict"] == "approved":
                await self._jules_apply_approval(owner, repo, pr_number, row)
                await self.jules_state.release_lock(row.id, "approved")
            else:
                await self.jules_state.release_lock(row.id, "revision_requested")

        except Exception:
            logger.exception(f"[jules] review pipeline crashed for {repo}#{pr_number}")
            try:
                await self.jules_state.release_lock(row.id, "revision_requested")
            except Exception:
                pass
            await self._jules_notify_discord_alarm(
                f"Jules-Review crashed für {repo}#{pr_number}"
            )

    async def _jules_fetch_pr_diff(self, owner: str, repo: str, pr: int) -> Optional[str]:
        """Holt den Diff via gh CLI."""
        repo_slug = f"{owner}/{repo}"
        try:
            proc = await asyncio.create_subprocess_exec(
                "gh", "pr", "diff", str(pr), "--repo", repo_slug,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            if proc.returncode != 0:
                logger.error(f"[jules] gh pr diff failed: {stderr.decode()[:200]}")
                return None
            return stdout.decode()
        except asyncio.TimeoutError:
            logger.error(f"[jules] gh pr diff timeout for {repo_slug}#{pr}")
            return None

    async def _jules_load_finding_context(self, finding_id: Optional[int]) -> Optional[Dict[str, Any]]:
        """Lädt Finding aus security_analyst.findings."""
        if finding_id is None:
            return None
        try:
            async with self.jules_state._pool.acquire() as conn:
                rec = await conn.fetchrow(
                    "SELECT title, severity, description, category, cve FROM findings WHERE id = $1",
                    finding_id,
                )
                return dict(rec) if rec else None
        except Exception as e:
            logger.warning(f"[jules] finding lookup failed: {e}")
            return None

    async def _jules_post_or_edit_review_comment(
        self, *, owner: str, repo: str, pr_number: int,
        review: Dict[str, Any], row: JulesReviewRow, iteration: int,
    ) -> None:
        """STUB — Task 9 implementiert Body-Builder + Post/Edit."""
        logger.info(f"[jules] STUB post/edit review comment {repo}#{pr_number} iter={iteration}")

    async def _jules_apply_approval(
        self, owner: str, repo: str, pr_number: int, row: JulesReviewRow
    ) -> None:
        """STUB — Task 10 implementiert Label-Setzen + Discord-Ping."""
        logger.info(f"[jules] STUB apply approval {repo}#{pr_number}")
```

**Step 2: Smoke-Test**

```bash
python -c "from src.integrations.github_integration.jules_workflow_mixin import JulesWorkflowMixin; print('OK')"
pytest tests/unit/test_jules_workflow_mixin.py -x -v
```

Erwartet: OK + 10 passed (keine Tests für den neuen Code hinzugefügt, Stubs werden in Phase 9/10 richtig getestet).

**Step 3: Commit**

```bash
git add src/integrations/github_integration/jules_workflow_mixin.py
git commit -m "feat: _jules_run_review_pipeline — AI-Call, Diff-Fetch, Finding-Context"
```

---

*Phase 8 abgeschlossen.*

---

## Phase 9: Comment-Management (Single-Comment-Edit-Strategie)

### Task 9.1: Comment-Body-Builder

**Files:**
- Create: `src/integrations/github_integration/jules_comment.py`
- Create: `tests/unit/test_jules_comment.py`

**Step 1: Tests**

```python
# tests/unit/test_jules_comment.py
from src.integrations.github_integration.jules_comment import (
    build_review_comment_body,
)


def _review(verdict="approved", blockers=None, suggestions=None, nits=None, in_scope=True):
    return {
        "verdict": verdict,
        "summary": "Test summary",
        "blockers": blockers or [],
        "suggestions": suggestions or [],
        "nits": nits or [],
        "scope_check": {"in_scope": in_scope, "explanation": "exp"},
    }


def test_comment_approved_has_green_marker():
    body = build_review_comment_body(
        review=_review(), iteration=1, pr_number=123, finding_id=42,
    )
    assert "🟢" in body or "APPROVED" in body.upper()
    assert "Iteration 1 of 5" in body
    assert "PR #123" in body
    assert "Finding #42" in body


def test_comment_revision_lists_blockers():
    blockers = [{
        "title": "Scope violation",
        "reason": "defu removed",
        "file": "web/package.json",
        "line": 23,
        "severity": "high",
        "suggested_fix": "Revert",
    }]
    body = build_review_comment_body(
        review=_review(verdict="revision_requested", blockers=blockers, in_scope=False),
        iteration=2, pr_number=123, finding_id=42,
    )
    assert "REVISION" in body.upper()
    assert "Scope violation" in body
    assert "web/package.json" in body
    assert "Out of scope" in body or "❌" in body


def test_comment_suggestions_shown_but_not_blocking():
    body = build_review_comment_body(
        review=_review(suggestions=[{
            "title": "Dedup",
            "reason": "nicer",
            "file": "x",
            "severity": "medium",
            "suggested_fix": "npm dedupe",
        }]),
        iteration=1, pr_number=1, finding_id=1,
    )
    assert "Dedup" in body
    assert "APPROVED" in body.upper()  # Suggestions blocken NICHT


def test_comment_has_marker_prefix_for_self_filter():
    """PR #123 Fix: Bot-Comments müssen erkennbar sein am Body-Prefix."""
    body = build_review_comment_body(
        review=_review(), iteration=1, pr_number=1, finding_id=1,
    )
    assert body.startswith("### 🛡️")
```

**Step 2: Implementation**

```python
# src/integrations/github_integration/jules_comment.py
"""
Jules Review Comment Body-Builder.

Erzeugt das Markdown für den einzigen PR-Comment, der bei jeder
Iteration via PATCH editiert wird.

Siehe Design-Doc §8.4.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

# Marker — Body-Prefix für Self-Comment-Filter
COMMENT_MARKER = "### 🛡️ Claude Security Review"


def build_review_comment_body(
    *,
    review: Dict[str, Any],
    iteration: int,
    pr_number: int,
    finding_id: int,
    max_iterations: int = 5,
) -> str:
    verdict = review.get("verdict", "revision_requested")
    blockers = review.get("blockers", [])
    suggestions = review.get("suggestions", [])
    nits = review.get("nits", [])
    summary = review.get("summary", "")
    scope = review.get("scope_check", {})

    if verdict == "approved":
        status_line = "**Verdict:** 🟢 APPROVED"
    else:
        status_line = "**Verdict:** 🔴 REVISION REQUESTED"

    scope_line = (
        "**Scope-Check:** ✅ In scope" if scope.get("in_scope")
        else f"**Scope-Check:** ❌ Out of scope — {scope.get('explanation', '')}"
    )

    parts = [
        f"{COMMENT_MARKER} — Iteration {iteration} of {max_iterations}",
        "",
        status_line,
        "",
        f"**Summary:** {summary}",
        "",
        "---",
        "",
    ]

    if blockers:
        parts.append("#### 🔴 Blockers (muss gefixt werden)")
        parts.append("")
        for i, b in enumerate(blockers, 1):
            parts.extend(_format_issue(i, b))
        parts.append("")

    if suggestions:
        parts.append("#### 🟡 Suggestions (nicht blockierend)")
        parts.append("")
        for i, s in enumerate(suggestions, 1):
            parts.extend(_format_issue(i, s))
        parts.append("")

    if nits:
        parts.append("#### ⚪ Nits")
        parts.append("")
        for i, n in enumerate(nits, 1):
            parts.extend(_format_issue(i, n))
        parts.append("")

    if not (blockers or suggestions or nits):
        parts.append("_Keine Anmerkungen._")
        parts.append("")

    parts.append(scope_line)
    parts.append("")
    parts.append("---")
    parts.append(
        f"*ShadowOps SecOps Workflow · PR #{pr_number} · Finding #{finding_id}*"
    )

    return "\n".join(parts)


def _format_issue(idx: int, issue: Dict[str, Any]) -> List[str]:
    lines = []
    title = issue.get("title", "Untitled")
    reason = issue.get("reason", "")
    file_ = issue.get("file", "")
    line_no = issue.get("line")
    severity = issue.get("severity", "medium")
    fix = issue.get("suggested_fix", "")

    loc = f"{file_}:{line_no}" if line_no else file_
    lines.append(f"{idx}. **{title}** ({severity})")
    lines.append(f"   - Datei: `{loc}`")
    lines.append(f"   - Grund: {reason}")
    if fix:
        lines.append(f"   - Fix: {fix}")
    lines.append("")
    return lines


def is_bot_comment(body: str) -> bool:
    """Self-Comment-Filter — erkennt Bot-eigene Reviews am Marker."""
    return body.lstrip().startswith(COMMENT_MARKER)
```

**Step 3: Tests — PASS**

```bash
pytest tests/unit/test_jules_comment.py -x -v
```

Erwartet: 4 passed.

**Step 4: Commit**

```bash
git add src/integrations/github_integration/jules_comment.py tests/unit/test_jules_comment.py
git commit -m "feat: jules_comment — Review-Body-Builder + Self-Filter-Marker"
```

---

### Task 9.2: Comment Post/Edit via gh CLI

**Files:**
- Modify: `src/integrations/github_integration/jules_workflow_mixin.py`

**Step 1: Implementation — ersetze den STUB `_jules_post_or_edit_review_comment`**

```python
    async def _jules_post_or_edit_review_comment(
        self, *, owner: str, repo: str, pr_number: int,
        review: Dict[str, Any], row: JulesReviewRow, iteration: int,
    ) -> None:
        """
        Postet oder editiert den Single-Review-Comment.

        - Erster Review: gh pr comment → Body, dann comment_id aus Response parsen
        - Zweite+ Reviews: gh api ... --method PATCH auf existierende comment_id
          (PATCH erzeugt kein issue_comment:created Event → kein Webhook-Loop)
        """
        from .jules_comment import build_review_comment_body
        import re

        cfg = self.config.jules_workflow
        max_iter = cfg.max_iterations

        body = build_review_comment_body(
            review=review,
            iteration=iteration,
            pr_number=pr_number,
            finding_id=row.finding_id or 0,
            max_iterations=max_iter,
        )

        repo_slug = f"{owner}/{repo}"

        if row.review_comment_id:
            # EDIT: PATCH
            proc = await asyncio.create_subprocess_exec(
                "gh", "api",
                f"repos/{repo_slug}/issues/comments/{row.review_comment_id}",
                "--method", "PATCH",
                "-f", f"body={body}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            if proc.returncode != 0:
                logger.error(f"[jules] comment PATCH failed: {stderr.decode()[:200]}")
                # Fallback: neu posten
                row.review_comment_id = None

        if not row.review_comment_id:
            # POST: gh pr comment
            proc = await asyncio.create_subprocess_exec(
                "gh", "pr", "comment", str(pr_number),
                "--repo", repo_slug, "--body", body,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            if proc.returncode != 0:
                logger.error(f"[jules] gh pr comment failed: {stderr.decode()[:200]}")
                return

            # Parse Comment-ID aus der URL in stdout
            # Format: https://github.com/o/r/pull/N#issuecomment-12345678
            url = stdout.decode().strip()
            m = re.search(r"#issuecomment-(\d+)", url)
            if m:
                comment_id = int(m.group(1))
                await self.jules_state.update_comment_id(row.id, comment_id)
                logger.info(f"[jules] posted review comment id={comment_id}")
            else:
                logger.warning(f"[jules] couldn't parse comment id from: {url}")
```

**Step 2: Smoke-Import-Test**

```bash
python -c "from src.integrations.github_integration.jules_workflow_mixin import JulesWorkflowMixin; print('OK')"
pytest tests/unit/test_jules_workflow_mixin.py -x -v
```

Erwartet: OK + 10 passed (Tests existieren nur für should_review + handle_pr_event).

**Step 3: Commit**

```bash
git add src/integrations/github_integration/jules_workflow_mixin.py
git commit -m "feat: Single-Comment-Edit-Strategie via gh api PATCH"
```

---

## Phase 10: Escalation + Approval

### Task 10.1: `_jules_escalate_to_human`

**Files:**
- Modify: `src/integrations/github_integration/jules_workflow_mixin.py`

**Step 1: Implementation — ersetze den STUB**

```python
    async def _jules_escalate_to_human(
        self, row: JulesReviewRow, reason: str
    ) -> None:
        """
        Setzt den PR in 'escalated' Terminal-State, postet einmaligen
        Eskalations-Kommentar, pingt Discord-Alerts.
        """
        cfg = self.config.jules_workflow

        # DB: terminal setzen
        await self.jules_state.mark_terminal(row.id, "escalated")

        # Discord Ping
        msg = (
            f"🚨 **Jules PR Escalation**\n"
            f"Repo: `{row.repo}` · PR #{row.pr_number}\n"
            f"Grund: `{reason}`\n"
            f"Iterations: {row.iteration_count}/{cfg.max_iterations}\n"
            f"Finding-ID: {row.finding_id or 'n/a'}\n"
            f"{cfg.role_ping_on_escalation} bitte manuell prüfen."
        )
        await self._jules_notify_discord_alarm(msg)

        # GitHub Comment (einmalig)
        try:
            body = (
                f"### 🚨 Jules SecOps — Human Approval Needed\n\n"
                f"**Grund:** `{reason}`\n"
                f"**Iterationen:** {row.iteration_count} of {cfg.max_iterations}\n\n"
                f"Der automatische Review-Workflow wurde gestoppt. Bitte prüft den PR manuell.\n\n"
                f"Letzte bekannte Blockers:\n```json\n{row.last_blockers}\n```\n\n"
                f"---\n*ShadowOps SecOps Workflow · Escalated*"
            )
            owner = "Commandershadow9"  # TODO: dynamisch via PR-Payload, wenn verfügbar
            proc = await asyncio.create_subprocess_exec(
                "gh", "pr", "comment", str(row.pr_number),
                "--repo", f"{owner}/{row.repo}", "--body", body,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=30)
        except Exception:
            logger.exception("[jules] escalation comment failed (ignoring)")

    async def _jules_notify_discord_alarm(self, msg: str) -> None:
        """Postet Alert in den configured escalation_channel."""
        try:
            cfg = self.config.jules_workflow
            channel_name = cfg.escalation_channel
            if hasattr(self.bot, "discord_logger") and self.bot.discord_logger:
                # DiscordChannelLogger hat eine generische send-Methode — Pattern aus anderen Mixins
                await self.bot.discord_logger.send_to_channel(channel_name, msg)
        except Exception:
            logger.exception("[jules] discord alarm failed")
```

**Step 2: Smoke-Test**

```bash
pytest tests/unit/test_jules_workflow_mixin.py -x -v
```

Erwartet: 10 passed (die existing tests schon abdecken, dass `mark_terminal` bei Escalate aufgerufen wird).

**Step 3: Commit**

```bash
git add src/integrations/github_integration/jules_workflow_mixin.py
git commit -m "feat: Jules Human-Escalation mit Discord-Ping und einmaligem PR-Comment"
```

---

### Task 10.2: `_jules_apply_approval` — Label + Ping

**Files:**
- Modify: `src/integrations/github_integration/jules_workflow_mixin.py`

**Step 1: Implementation — ersetze den STUB**

```python
    async def _jules_apply_approval(
        self, owner: str, repo: str, pr_number: int, row: JulesReviewRow
    ) -> None:
        """
        Bei Approval: setze Label claude-approved, pinge Discord.
        Kein Auto-Merge — Shadow merged manuell.
        """
        repo_slug = f"{owner}/{repo}"

        # Label setzen (erstellt wenn fehlt)
        for cmd in (
            ("gh", "pr", "edit", str(pr_number), "--repo", repo_slug,
             "--add-label", "claude-approved"),
        ):
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
                if proc.returncode != 0:
                    err = stderr.decode()[:200]
                    if "not found" in err.lower():
                        # Label fehlt → anlegen, dann Retry
                        await self._jules_ensure_label(repo_slug, "claude-approved", "0e8a16")
                        proc2 = await asyncio.create_subprocess_exec(
                            *cmd,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                        )
                        await asyncio.wait_for(proc2.communicate(), timeout=30)
                    else:
                        logger.warning(f"[jules] label-add failed: {err}")
            except Exception:
                logger.exception("[jules] label add crashed")

        # Discord Ping
        cfg = self.config.jules_workflow
        msg = (
            f"✅ **Jules PR APPROVED**\n"
            f"Repo: `{repo}` · PR #{pr_number}\n"
            f"Iterations: {row.iteration_count + 1}/{cfg.max_iterations}\n"
            f"Finding-ID: {row.finding_id or 'n/a'}\n"
            f"🔗 https://github.com/{repo_slug}/pull/{pr_number}\n"
            f"{cfg.role_ping_on_escalation} — bereit für deinen Merge."
        )
        try:
            if hasattr(self.bot, "discord_logger") and self.bot.discord_logger:
                await self.bot.discord_logger.send_to_channel(
                    cfg.notification_channel, msg
                )
        except Exception:
            logger.exception("[jules] discord approval ping failed")

    async def _jules_ensure_label(
        self, repo_slug: str, name: str, color: str = "0e8a16"
    ) -> None:
        """Erstellt ein Label wenn es nicht existiert (idempotent)."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "gh", "label", "create", name,
                "--repo", repo_slug,
                "--color", color,
                "--description", "Claude security review approved this PR",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=15)
        except Exception:
            pass  # Label exists already — non-fatal
```

**Step 2: Smoke-Test**

```bash
pytest tests/unit/test_jules_workflow_mixin.py -x -v
```

Erwartet: 10 passed.

**Step 3: Commit**

```bash
git add src/integrations/github_integration/jules_workflow_mixin.py
git commit -m "feat: Jules Approval — claude-approved Label + Discord-Ping"
```

---

### Task 10.3: PR-Close-Handler (`merged` vs `abandoned`)

**Files:**
- Modify: `src/integrations/github_integration/jules_workflow_mixin.py`
- Modify: `tests/unit/test_jules_workflow_mixin.py`

**Step 1: Test**

```python
@pytest.mark.asyncio
async def test_handle_pr_close_marks_merged(harness):
    harness._jules_is_jules_pr = AsyncMock(return_value=True)
    harness.jules_state.get = AsyncMock(return_value=_row(status="approved"))
    harness.jules_state.mark_terminal = AsyncMock()
    harness._jules_resolve_finding = AsyncMock()

    payload = {
        "action": "closed",
        "pull_request": {
            "number": 1, "head": {"sha": "x"},
            "user": {"login": "google-labs-jules[bot]"},
            "body": "Fixes #42", "labels": [{"name": "jules"}],
            "merged": True,
        },
        "repository": {"name": "X", "owner": {"login": "o"}},
    }
    await harness.handle_jules_pr_event(payload)
    harness.jules_state.mark_terminal.assert_called_once_with(1, "merged")
    harness._jules_resolve_finding.assert_called_once()


@pytest.mark.asyncio
async def test_handle_pr_close_marks_abandoned(harness):
    harness._jules_is_jules_pr = AsyncMock(return_value=True)
    harness.jules_state.get = AsyncMock(return_value=_row(status="revision_requested"))
    harness.jules_state.mark_terminal = AsyncMock()

    payload = {
        "action": "closed",
        "pull_request": {
            "number": 1, "head": {"sha": "x"},
            "user": {"login": "google-labs-jules[bot]"},
            "body": "", "labels": [{"name": "jules"}],
            "merged": False,
        },
        "repository": {"name": "X", "owner": {"login": "o"}},
    }
    await harness.handle_jules_pr_event(payload)
    harness.jules_state.mark_terminal.assert_called_once_with(1, "abandoned")
```

**Step 2: Erweitere `handle_jules_pr_event` um closed-Branch**

Am Anfang der existing `handle_jules_pr_event`, nach dem `is_jules`-Check, füge ein:

```python
            # PR closed: Terminal-State setzen
            if action == "closed":
                existing = await self.jules_state.get(repo, pr_number)
                if existing and existing.status not in ("merged", "abandoned"):
                    terminal = "merged" if pr.get("merged") else "abandoned"
                    await self.jules_state.mark_terminal(existing.id, terminal)
                    logger.info(f"[jules] {repo}#{pr_number} → {terminal}")
                    if terminal == "merged" and existing.finding_id:
                        await self._jules_resolve_finding(existing.finding_id)
                return

            # Event-Type für Gate 1
            event_type = f"pull_request:{action}"
            ...
```

Und füge die `_jules_resolve_finding`-Methode hinzu:

```python
    async def _jules_resolve_finding(self, finding_id: int) -> None:
        """Setzt das zugehörige Finding auf 'resolved' in security_analyst.findings."""
        try:
            async with self.jules_state._pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE findings
                    SET status = 'resolved',
                        resolved_at = now()
                    WHERE id = $1
                    """,
                    finding_id,
                )
            logger.info(f"[jules] finding {finding_id} marked resolved")
        except Exception:
            logger.exception("[jules] resolve finding failed")
```

**Step 3: Tests — PASS**

```bash
pytest tests/unit/test_jules_workflow_mixin.py -x -v
```

Erwartet: 12 passed.

**Step 4: Commit**

```bash
git add src/integrations/github_integration/jules_workflow_mixin.py tests/unit/test_jules_workflow_mixin.py
git commit -m "feat: PR-Close-Handler — merged → finding resolved, abandoned → terminal"
```

---

## Phase 11: Handler-Wiring in `core.py`

### Task 11.1: `JulesWorkflowMixin` in `GitHubIntegration` einhängen

**Files:**
- Modify: `src/integrations/github_integration/core.py`
- Modify: `tests/unit/test_github_integration.py` (nur Import-Check)

**Step 1: Core-Änderung**

Öffne `src/integrations/github_integration/core.py`.

1. Import-Block ergänzen:

```python
from .jules_workflow_mixin import JulesWorkflowMixin
from .jules_state import JulesState
from .jules_learning import JulesLearning
```

2. Klassen-MRO anpassen — `JulesWorkflowMixin` VOR `EventHandlersMixin` einsetzen, damit der Dispatcher-Lookup via `self.event_handlers['pull_request']` standardmäßig weiterhin auf `handle_pr_event` zeigt, aber unser Jules-Handler vorher läuft:

```python
class GitHubIntegration(
    JulesWorkflowMixin,          # NEU (zuerst, damit MRO priorisiert)
    WebhookMixin,
    PollingMixin,
    EventHandlersMixin,
    CIMixin,
    StateMixin,
    GitOpsMixin,
    NotificationsMixin,
    AIPatchNotesMixin,
):
```

3. `__init__` erweitern — nach der bisherigen Initialisierung von `self.state_manager = get_state_manager()`:

```python
        # Jules SecOps Workflow — lazy init (connect() später in setup_hook)
        self._jules_enabled = bool(
            getattr(getattr(self.config, "jules_workflow", None), "enabled", False)
        )
        if self._jules_enabled:
            self.jules_state = JulesState(self.config.security_analyst_dsn)
            self.jules_learning = JulesLearning(self.config.agent_learning_dsn)
        else:
            self.jules_state = None
            self.jules_learning = None
```

4. `event_handlers`-Dict anpassen — die existing Event-Handler-Registrierung bleibt gleich, ABER wir wrappen `handle_pr_event` so, dass es ZUERST `handle_jules_pr_event` aufruft:

Suche die Zeile
```python
'pull_request': self.handle_pr_event,
```
und ersetze sie durch
```python
'pull_request': self._pr_dispatch,
```

Und füge die Dispatch-Methode zur Klasse hinzu (in `core.py` am Ende der Klasse):

```python
    async def _pr_dispatch(self, payload):
        """
        Dispatches pull_request events. Jules-Workflow läuft ZUERST und
        entscheidet ob er sich "angesprochen" fühlt; danach läuft immer
        der normale Handler für Discord-Notifications.
        """
        if self._jules_enabled:
            try:
                await self.handle_jules_pr_event(payload)
            except Exception:
                self.logger.exception("[jules] pr dispatch crashed (continuing)")
        await self.handle_pr_event(payload)
```

5. `setup_hook` / `start()` erweitern — suche den Startup-Flow, wo andere Services connected werden, und füge ein:

```python
        if self._jules_enabled:
            await self.jules_state.connect()
            await self.jules_learning.connect()
            # Stale-Lock-Recovery beim Start
            cleaned = await self.jules_state.recover_stale_locks(timeout_minutes=10)
            if cleaned:
                self.logger.warning(f"[jules] recovered {cleaned} stale locks on startup")
```

**Step 2: Redis-Client verfügbar machen**

`JulesWorkflowMixin` erwartet `self.redis`. Wenn der Bot bereits einen globalen Redis-Client hat, alias das hier:

```python
        # In GitHubIntegration.__init__, nach dem Jules-init-Block:
        self.redis = getattr(self.bot, "redis", None) if hasattr(self, "bot") else None
```

Falls `self.bot` erst später gesetzt wird, passe das in `setup_hook` an (setze `self.redis = self.bot.redis` dort).

Wenn der Bot noch keinen Redis-Client hat, sieh im Rest des Codes nach (`grep -rn "redis.asyncio\|aioredis" src/`) — falls keiner existiert, nutze Fallback:

```python
            import redis.asyncio as aioredis
            self.redis = aioredis.from_url(
                self.config.redis_url or "redis://127.0.0.1:6379/0",
                decode_responses=True,
            )
```

**Step 3: Smoke-Test**

```bash
python -c "from src.integrations.github_integration.core import GitHubIntegration; print('OK')"
```

Erwartet: `OK`. Falls `ImportError` wegen fehlendem Redis-Client: Redis optional machen und Jules-Features deaktivieren wenn Redis fehlt.

**Step 4: Commit**

```bash
git add src/integrations/github_integration/core.py
git commit -m "feat: GitHubIntegration — Jules Workflow-Mixin + Dispatcher-Wiring"
```

---

### Task 11.2: Regression-Test für PR #123 Szenario

**Files:**
- Create: `tests/unit/test_jules_pr123_regression.py`

**Step 1: Test schreiben**

```python
# tests/unit/test_jules_pr123_regression.py
"""
REGRESSION TEST für PR #123 Vorfall.

Szenario: 31 issue_comment:created Events kommen rein, jedes ist ein
Bot-eigener Review-Kommentar. Erwartung: KEIN einziger davon triggert
einen Auto-Review. tokens_consumed bleibt 0. should_review wird nie
für die Comments aufgerufen.

Siehe Design-Doc Anhang A.
"""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
import fakeredis.aioredis

from src.integrations.github_integration.jules_workflow_mixin import JulesWorkflowMixin
from src.integrations.github_integration.jules_state import JulesReviewRow


class _Cfg:
    class circuit_breaker:
        max_reviews_per_hour = 20
        pause_duration_seconds = 3600
    enabled = True
    max_iterations = 5
    cooldown_seconds = 300
    max_hours_per_pr = 2


class _Harness(JulesWorkflowMixin):
    def __init__(self):
        self.jules_state = MagicMock()
        self.jules_state.process_id = "test-worker"
        self.jules_state.try_claim_review = AsyncMock(return_value=None)
        self.redis = fakeredis.aioredis.FakeRedis()
        self.config = MagicMock()
        self.config.jules_workflow = _Cfg()
        self.config.jules_workflow.circuit_breaker = _Cfg.circuit_breaker
        self.bot = MagicMock()


@pytest.mark.asyncio
async def test_pr_123_regression_31_issue_comments_zero_reviews():
    """
    Reproduziert den PR #123 Loop: 31 identische issue_comment Events.
    Jedes MUSS vom Trigger-Whitelist-Gate gestoppt werden.
    """
    harness = _Harness()

    # Simuliere 31 Comment-Events
    comment_events = [
        {
            "action": "created",
            "comment": {
                "body": "### 🛡️ Claude Security Review\n\nGENEHMIGT 92%",
                "user": {"login": "Commandershadow9"},
            },
            "issue": {"number": 123, "pull_request": {"html_url": "x"}},
            "repository": {"name": "ZERODOX", "owner": {"login": "x"}},
        }
        for _ in range(31)
    ]

    # In der neuen Architektur gehen issue_comment Events NICHT durch
    # handle_jules_pr_event. Sie gehen durch handle_comment_event (existiert
    # nur für manuellen /review). Daher testen wir direkt should_review:
    for _ in range(31):
        decision = await harness.should_review(
            "ZERODOX", 123, "any_sha", "issue_comment:created"
        )
        assert decision.proceed is False
        assert decision.reason == "blocked_trigger"

    # try_claim_review darf NIE aufgerufen worden sein
    harness.jules_state.try_claim_review.assert_not_called()


@pytest.mark.asyncio
async def test_pr_123_regression_bot_own_comment_not_reviewed():
    """
    Zweiter Regression-Aspekt: Bot-Comments haben Marker-Prefix.
    Der manuelle /review-Handler muss sie ignorieren.
    """
    from src.integrations.github_integration.jules_comment import is_bot_comment

    claude_comment = "### 🛡️ Claude Security Review — Iteration 1 of 5\n\n**Verdict:** 🟢 APPROVED"
    human_comment = "Looks good to me!"

    assert is_bot_comment(claude_comment) is True
    assert is_bot_comment(human_comment) is False


@pytest.mark.asyncio
async def test_pr_123_regression_circuit_breaker_stops_runaway():
    """
    Selbst wenn alle anderen Gates versagen würden: Circuit Breaker
    greift nach 20 Reviews/h und stoppt Jules-Loops global.
    """
    harness = _Harness()
    harness.jules_state.try_claim_review = AsyncMock(return_value=JulesReviewRow(
        id=1, repo="X", pr_number=1, issue_number=None, finding_id=None,
        status="reviewing", last_reviewed_sha=None, iteration_count=0,
        last_review_at=None, lock_acquired_at=datetime.now(timezone.utc),
        lock_owner="w", review_comment_id=None, last_review_json=None,
        last_blockers=None, tokens_consumed=0,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        closed_at=None, human_override=False,
    ))
    harness._jules_notify_discord_alarm = AsyncMock()

    # Trigger 25 Reviews in kurzer Zeit
    for i in range(25):
        await harness.should_review("X", i, f"sha_{i}", "pull_request:synchronize")

    # Nach Threshold kommt circuit_breaker_open
    d = await harness.should_review("X", 99, "sha_99", "pull_request:synchronize")
    assert d.reason == "circuit_breaker_open"
    harness._jules_notify_discord_alarm.assert_called()
```

**Step 2: Tests — PASS**

```bash
pytest tests/unit/test_jules_pr123_regression.py -x -v
```

Erwartet: 3 passed.

**Step 3: Commit**

```bash
git add tests/unit/test_jules_pr123_regression.py
git commit -m "test: Regression für PR #123 Review-Loop (31 comments → 0 reviews)"
```

---

*Phase 11 abgeschlossen.*

---

## Phase 12: ScanAgent-Integration

### Task 12.1: `FIX_MODE_DECISION` + `classify_fix_mode()` hinzufügen

**Files:**
- Modify: `src/integrations/security_engine/scan_agent.py`
- Create: `tests/unit/test_scan_agent_jules_classification.py`

**Step 1: Test schreiben**

```python
# tests/unit/test_scan_agent_jules_classification.py
import pytest
from unittest.mock import MagicMock

from src.integrations.security_engine.scan_agent import (
    FIX_MODE_DECISION,
    classify_fix_mode,
)


def _finding(category, project="ZERODOX"):
    f = MagicMock()
    f.category = category
    f.project = project
    return f


def test_npm_audit_routes_to_jules():
    assert classify_fix_mode(_finding("npm_audit")) == "jules"


def test_pip_audit_routes_to_jules():
    assert classify_fix_mode(_finding("pip_audit")) == "jules"


def test_dockerfile_routes_to_jules():
    assert classify_fix_mode(_finding("dockerfile")) == "jules"


def test_code_vulnerability_routes_to_jules():
    assert classify_fix_mode(_finding("code_vulnerability")) == "jules"


def test_ufw_routes_to_self_fix():
    assert classify_fix_mode(_finding("ufw")) == "self_fix"


def test_fail2ban_routes_to_self_fix():
    assert classify_fix_mode(_finding("fail2ban")) == "self_fix"


def test_unknown_category_is_human_only():
    assert classify_fix_mode(_finding("mysterious_thing")) == "human_only"


def test_ssh_config_is_human_only_even_if_code():
    assert classify_fix_mode(_finding("ssh_config")) == "human_only"


def test_skipped_project_falls_back_to_human():
    from src.integrations.security_engine.scan_agent import SKIP_ISSUE_PROJECTS
    if "test_skipped" not in SKIP_ISSUE_PROJECTS:
        SKIP_ISSUE_PROJECTS.add("test_skipped")
    assert classify_fix_mode(_finding("npm_audit", project="test_skipped")) == "human_only"
```

**Step 2: Implementation in `scan_agent.py`**

Suche in `src/integrations/security_engine/scan_agent.py` die Stelle wo `SKIP_ISSUE_PROJECTS` oder `PROJECT_REPO_MAP` definiert sind, und füge direkt danach:

```python
# === Jules SecOps Workflow — Fix-Mode-Klassifizierung ===
# Siehe docs/plans/2026-04-11-jules-secops-workflow-design.md §9.1

FIX_MODE_DECISION: Dict[str, str] = {
    # Code-Findings → Jules (PR via GitHub-Issue)
    'npm_audit':          'jules',
    'pip_audit':          'jules',
    'dockerfile':         'jules',
    'code_vulnerability': 'jules',

    # Infrastruktur → Self-Fix durch den ScanAgent
    'ufw':                'self_fix',
    'fail2ban':           'self_fix',
    'crowdsec':           'self_fix',
    'aide':               'self_fix',
    'docker_config':      'self_fix',

    # Explizit NICHT automatisiert
    'ssh_config':         'human_only',
    'database_schema':    'human_only',
}


def classify_fix_mode(finding) -> str:
    """
    Gibt 'jules', 'self_fix' oder 'human_only' zurück.

    Priorität:
    1. Projekt in SKIP_ISSUE_PROJECTS → 'human_only' (keine Issue-Erstellung)
    2. Kein Eintrag in PROJECT_REPO_MAP → 'human_only' (kein Repo zum Öffnen)
    3. FIX_MODE_DECISION Lookup
    4. Fallback: 'human_only'
    """
    project = getattr(finding, "project", None)
    category = getattr(finding, "category", None)

    if project in SKIP_ISSUE_PROJECTS:
        return "human_only"

    mode = FIX_MODE_DECISION.get(category, "human_only")
    if mode == "jules":
        if project not in PROJECT_REPO_MAP:
            return "human_only"
    return mode
```

**Step 3: Tests — PASS**

```bash
pytest tests/unit/test_scan_agent_jules_classification.py -x -v
```

Erwartet: 9 passed.

**Step 4: Commit**

```bash
git add src/integrations/security_engine/scan_agent.py tests/unit/test_scan_agent_jules_classification.py
git commit -m "feat: ScanAgent classify_fix_mode — Jules/Self-Fix/Human-only Routing"
```

---

### Task 12.2: `build_jules_issue_body()` + Label-Integration

**Files:**
- Modify: `src/integrations/security_engine/scan_agent.py`

**Step 1: Body-Builder als Funktion hinzufügen**

```python
def build_jules_issue_body(finding) -> str:
    """
    Erzeugt den GitHub-Issue-Body für Jules-Delegation.

    Enthält Acceptance-Criteria, Scope-Warnung und explizite Anweisung
    KEIN "Acknowledged"-Comment zu posten (PR #123 Second Line of Defense).
    """
    cve = getattr(finding, "cve", None) or "N/A"
    severity = getattr(finding, "severity", "medium")
    category = getattr(finding, "category", "n/a")
    title = getattr(finding, "title", "Security Finding")
    description = getattr(finding, "description", "(keine Beschreibung)")
    files = getattr(finding, "affected_files", None) or []
    finding_id = getattr(finding, "id", 0)

    files_block = "\n".join(f"- `{f}`" for f in files) if files else "(im Scan-Report)"

    return f"""## 🛡️ Security Finding

**Severity:** {severity.upper()}
**Category:** `{category}`
**CVE:** {cve}

### Problem

{description}

### Betroffene Dateien

{files_block}

### Acceptance Criteria

- [ ] Das spezifische Problem oben ist behoben
- [ ] Keine unrelated Changes (Scope strikt halten!)
- [ ] `npm audit` / `pip audit` zeigt kein Finding mehr für diese CVE
- [ ] Existing Tests laufen noch (`npm run test` / `pytest`)
- [ ] Keine neuen Dependencies ohne Begründung

---

### 🤖 Task for Jules

@google-labs-jules please fix the security issue described above.

**Wichtig — bitte lies das:**

1. **Scope strikt halten** — nur die in "Betroffene Dateien" genannten Dateien anfassen
2. **Kein Refactoring** — auch wenn du "besseren" Code siehst
3. **PR-Body muss `Fixes #N` enthalten** (mit diesem Issue-Number)
4. **Reagiere NICHT mit "Acknowledged" auf Review-Kommentare** — das hat in der Vergangenheit zu Review-Loops geführt
5. Du wirst automatisch von Claude Opus reviewt (strukturiert: Blockers/Suggestions/Nits)
6. Bei Approval → Shadow merged manuell. Max 5 Review-Iterationen, sonst Human-Eskalation.

---
*Auto-created by ShadowOps SecOps Workflow · Finding ID: {finding_id}*
"""
```

**Step 2: Issue-Erstellung im bestehenden Flow erweitern**

Suche die Stelle in `scan_agent.py` wo GitHub-Issues erstellt werden (vermutlich `_create_github_issue` oder ähnlich). Der konkrete Funktionsname kann variieren — suche nach `gh issue create` in der Datei.

Ersetze den Body-Build-Part durch:

```python
        # Fix-Mode bestimmen
        mode = classify_fix_mode(finding)
        if mode == "human_only":
            # Existing Pfad: normaler Issue ohne Jules
            body = self._build_standard_issue_body(finding)
            labels = ["security", f"severity-{finding.severity}"]
        elif mode == "jules":
            body = build_jules_issue_body(finding)
            labels = ["security", "jules", f"severity-{finding.severity}"]
        else:  # self_fix
            # Kein Issue — wird direkt gefixt (existing Flow)
            return None
```

Wichtig: Der `_build_standard_issue_body` Aufruf muss die bestehende Body-Builder-Logik kapseln. Falls heute der Body inline gebaut wird, extrahiere ihn zuerst in eine private Methode:

```python
    def _build_standard_issue_body(self, finding) -> str:
        """Standard Issue-Body für Human/Self-Fix Findings."""
        # ... bestehende Logik hier ...
```

**Step 3: Smoke-Test**

```bash
python -c "from src.integrations.security_engine.scan_agent import build_jules_issue_body, classify_fix_mode; print('OK')"
pytest tests/unit/test_scan_agent_jules_classification.py -x -v
```

**Step 4: Commit**

```bash
git add src/integrations/security_engine/scan_agent.py
git commit -m "feat: build_jules_issue_body + Issue-Label-Routing (jules/self_fix/human_only)"
```

---

### Task 12.3: `ensure_pending` Row anlegen wenn Jules-Issue erstellt wird

**Files:**
- Modify: `src/integrations/security_engine/scan_agent.py`

**Step 1: Implementation**

Nach dem erfolgreichen `gh issue create` Aufruf im `mode == "jules"` Branch, lege in der `jules_pr_reviews`-Tabelle eine `pending`-Row an. Da der ScanAgent aber keinen direkten Zugriff auf `JulesState` hat (getrennte Module), verwenden wir den gleichen asyncpg-Pool wie die `findings`-Tabelle.

Direkter SQL-Insert (kein Mixin-Dependency):

```python
    async def _create_jules_pending_row(
        self,
        *,
        repo: str,
        issue_number: int,
        finding_id: int,
    ) -> None:
        """
        Legt einen 'pending' Eintrag in jules_pr_reviews an, damit
        der JulesWorkflowMixin den später öffnenden PR findet.

        pr_number ist zu diesem Zeitpunkt 0 (kein PR bekannt), wird beim
        ersten PR-Event auf den echten PR-Number aktualisiert.
        """
        # WICHTIG: Wir nutzen pr_number=0 als Placeholder, das aber weil UNIQUE (repo, pr_number) wäre
        # ein Konflikt bei mehreren pending-Issues pro Repo.
        # Stattdessen: keinen Row anlegen, sondern ensure_pending beim PR-Open aufrufen (JulesWorkflowMixin).
        pass  # STUB — siehe unten
```

**Design-Entscheidung:** Da `jules_pr_reviews` `UNIQUE (repo, pr_number)` hat, können wir nicht mehrere pending-Rows pro Repo mit `pr_number=0` anlegen. Stattdessen macht `ensure_pending` **zum Zeitpunkt des ersten PR-Webhook-Events** den Insert:

Im `handle_jules_pr_event` (in `jules_workflow_mixin.py`, Task 8.2), direkt nach dem `is_jules`-Check, füge ein:

```python
            # Pending-Row anlegen falls noch nicht vorhanden (PR war neu)
            issue_number = self._jules_extract_fixes_ref(pr.get("body") or "")
            finding_id = await self._jules_lookup_finding_by_issue(repo, issue_number) if issue_number else None
            await self.jules_state.ensure_pending(
                repo=repo,
                pr_number=pr_number,
                issue_number=issue_number,
                finding_id=finding_id,
            )
```

Und füge die Helper-Methoden zum Mixin hinzu:

```python
    def _jules_extract_fixes_ref(self, body: str) -> Optional[int]:
        """Parst 'Fixes #42' aus dem PR-Body."""
        import re
        m = re.search(r"(?:Fixes|Closes|Resolves)\s+#(\d+)", body, re.IGNORECASE)
        return int(m.group(1)) if m else None

    async def _jules_lookup_finding_by_issue(self, repo: str, issue_number: int) -> Optional[int]:
        """Findet finding.id via issue_number im Repo (Best-Effort)."""
        if not self.jules_state or not issue_number:
            return None
        try:
            async with self.jules_state._pool.acquire() as conn:
                rec = await conn.fetchrow(
                    """
                    SELECT id FROM findings
                    WHERE github_issue_number = $1 AND project = $2
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    issue_number, repo,
                )
                return rec["id"] if rec else None
        except Exception:
            return None
```

**Step 2: Smoke-Test**

```bash
pytest tests/unit/test_jules_workflow_mixin.py -x -v
```

**Step 3: Commit**

```bash
git add src/integrations/github_integration/jules_workflow_mixin.py
git commit -m "feat: ensure_pending beim ersten PR-Event + Fixes-Ref Parser"
```

---

## Phase 13: Health-Endpoint

### Task 13.1: `/health/jules` in `health_server.py`

**Files:**
- Modify: `src/utils/health_server.py`
- Create: `tests/unit/test_health_jules.py`

**Step 1: Test**

```python
# tests/unit/test_health_jules.py
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web

from src.utils.health_server import HealthCheckServer


@pytest.mark.asyncio
async def test_health_jules_returns_disabled_when_off(aiohttp_client):
    bot = MagicMock()
    bot.config = MagicMock()
    bot.config.jules_workflow = MagicMock(enabled=False)
    bot.github_integration = MagicMock(_jules_enabled=False)

    server = HealthCheckServer(bot)
    client = await aiohttp_client(server._app)
    resp = await client.get("/health/jules")
    assert resp.status == 200
    data = await resp.json()
    assert data["enabled"] is False
    assert data["status"] == "disabled"


@pytest.mark.asyncio
async def test_health_jules_returns_metrics_when_enabled(aiohttp_client):
    bot = MagicMock()
    bot.config = MagicMock()
    bot.config.jules_workflow = MagicMock(enabled=True)

    # Fake jules_state
    gh_int = MagicMock()
    gh_int._jules_enabled = True
    gh_int.jules_state = MagicMock()
    gh_int.jules_state._pool = MagicMock()

    # Mock fetch_health_stats
    async def fake_stats():
        return {
            "active_reviews": 2,
            "pending_prs": 1,
            "escalated_24h": 0,
            "stats_24h": {"total_reviews": 5, "approved": 4, "revisions": 1},
        }
    gh_int.jules_state.fetch_health_stats = fake_stats
    bot.github_integration = gh_int

    server = HealthCheckServer(bot)
    client = await aiohttp_client(server._app)
    resp = await client.get("/health/jules")
    assert resp.status == 200
    data = await resp.json()
    assert data["enabled"] is True
    assert data["active_reviews"] == 2
```

**Step 2: `fetch_health_stats` in `jules_state.py` hinzufügen**

```python
    async def fetch_health_stats(self) -> Dict[str, Any]:
        """Kompakte Health-Metriken für /health/jules."""
        async with self._pool.acquire() as conn:
            active = await conn.fetchval(
                "SELECT COUNT(*) FROM jules_pr_reviews WHERE status = 'reviewing'"
            )
            pending = await conn.fetchval(
                "SELECT COUNT(*) FROM jules_pr_reviews WHERE status = 'pending'"
            )
            escalated_24h = await conn.fetchval(
                """
                SELECT COUNT(*) FROM jules_pr_reviews
                WHERE status = 'escalated' AND closed_at > now() - interval '24 hours'
                """
            )
            stats_24h = await conn.fetchrow(
                """
                SELECT
                    COUNT(*)                                             AS total,
                    COUNT(*) FILTER (WHERE status = 'approved')          AS approved,
                    COUNT(*) FILTER (WHERE status = 'revision_requested') AS revisions,
                    COUNT(*) FILTER (WHERE status = 'merged')            AS merged,
                    COALESCE(SUM(tokens_consumed), 0)                    AS tokens
                FROM jules_pr_reviews
                WHERE updated_at > now() - interval '24 hours'
                """
            )
            last_review = await conn.fetchval(
                "SELECT MAX(last_review_at) FROM jules_pr_reviews"
            )

        return {
            "active_reviews": int(active or 0),
            "pending_prs": int(pending or 0),
            "escalated_24h": int(escalated_24h or 0),
            "stats_24h": {
                "total_reviews": int(stats_24h["total"] or 0),
                "approved": int(stats_24h["approved"] or 0),
                "revisions": int(stats_24h["revisions"] or 0),
                "merged": int(stats_24h["merged"] or 0),
                "tokens_consumed": int(stats_24h["tokens"] or 0),
            },
            "last_review_at": last_review.isoformat() if last_review else None,
        }
```

**Step 3: Endpoint in `health_server.py`**

Suche die Stelle in `src/utils/health_server.py` wo andere Routes definiert werden (z.B. `app.router.add_get('/health', ...)`), und füge hinzu:

```python
        self._app.router.add_get("/health/jules", self._health_jules)
```

Und die Handler-Methode:

```python
    async def _health_jules(self, request):
        """
        Jules SecOps Workflow Health-Endpoint.
        Gibt Metriken aus jules_pr_reviews zurück.
        """
        cfg_enabled = bool(
            getattr(getattr(self.bot.config, "jules_workflow", None), "enabled", False)
        )
        gh = getattr(self.bot, "github_integration", None)
        gh_enabled = bool(gh and getattr(gh, "_jules_enabled", False))

        if not (cfg_enabled and gh_enabled and getattr(gh, "jules_state", None)):
            return web.json_response({
                "enabled": False,
                "status": "disabled",
            })

        try:
            stats = await gh.jules_state.fetch_health_stats()
            return web.json_response({
                "enabled": True,
                "status": "healthy",
                **stats,
            })
        except Exception as e:
            return web.json_response({
                "enabled": True,
                "status": "error",
                "error": str(e),
            }, status=500)
```

**Step 4: Tests**

```bash
pytest tests/unit/test_health_jules.py -x -v
```

Erwartet: 2 passed. Falls fehlschlägt wegen `aiohttp_client` fixture: das ist die `pytest-aiohttp` Fixture — falls nicht installiert, teste manuell via `curl http://127.0.0.1:8766/health/jules` in Phase 15.

**Step 5: Commit**

```bash
git add src/utils/health_server.py src/integrations/github_integration/jules_state.py tests/unit/test_health_jules.py
git commit -m "feat: /health/jules Endpoint mit Metriken aus jules_pr_reviews"
```

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
    - human_override=True nach approved → 'missed_issue'
    - status=merged + positive feedback (oder keines) → 'approved_clean'
    - status=merged + negative feedback → 'false_positive'
    - status=revision_requested + negative feedback → 'good_catch'
    - Fallback → 'approved_clean'
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
        # Post in 🧠-ai-learning
        if hasattr(self, "discord_logger"):
            await self.discord_logger.send_to_channel(
                "🧠-ai-learning",
                f"🛡️ Jules Nightly: classified={counts['classified']}, "
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

## Phase 15: Rollout

### Task 15.1: Config-Vorbereitung — `enabled: false`, `dry_run: true`

**Files:**
- Modify: `config/config.yaml` (LIVE, NICHT in Git)

**Step 1: Config manuell anpassen**

```yaml
jules_workflow:
  enabled: true
  dry_run: true       # Phase 4 Testing
  max_iterations: 5
  cooldown_seconds: 300
  max_hours_per_pr: 2
  circuit_breaker:
    max_reviews_per_hour: 20
    pause_duration_seconds: 3600
  excluded_projects:
    - sicherheitsdienst
  max_diff_chars: 8000
  few_shot_examples: 3
  project_knowledge_limit: 10
  token_cap_per_pr: 50000
  notification_channel: "🛡️-security-ops"
  escalation_channel: "🚨-alerts"
  role_ping_on_escalation: "@Shadow"
```

**Step 2: Bot restart über `scripts/restart.sh`**

```bash
cd /home/cmdshadow/shadowops-bot
scripts/restart.sh --logs
```

Beobachte Startup-Log auf:
- `[jules] connected to security_analyst DB`
- `[jules] connected to agent_learning DB`
- Keine `ImportError` oder `AttributeError`
- Stale-Lock-Recovery-Message (wenn vorhanden)

**Step 3: Health-Check**

```bash
curl -s http://127.0.0.1:8766/health/jules | jq
```

Erwartet: `{"enabled": true, "status": "healthy", "active_reviews": 0, ...}`.

**Step 4: Kein Commit** — config.yaml ist nicht in Git.

---

### Task 15.2: Dry-Run Phase — 24h Live-Events beobachten

**Keine Code-Änderung, nur Beobachtung.**

**Step 1: Logs monitoren**

```bash
journalctl -u shadowops-bot -f | grep -i "jules"
```

Für 24 Stunden beobachten. Erwartete Log-Muster:
- `[jules] Detected Jules PR ... action=opened` bei neuen PRs
- `[jules] DRY-RUN ...` statt echten AI-Calls
- Keine echten PR-Comments
- Keine DB-Writes außer `ensure_pending`

**Step 2: DB-Check**

```bash
psql "$SECURITY_ANALYST_DB_URL" -c "SELECT repo, pr_number, status, iteration_count FROM jules_pr_reviews;"
```

Alle Rows sollten `status='revision_requested'` oder `'pending'` haben, `iteration_count=0`.

**Step 3: Wenn 24h OK → Commit einer Notiz**

```bash
echo "2026-04-12 Dry-Run 24h OK: N Rows in jules_pr_reviews, 0 Errors" >> docs/rollout-notes.md
git add docs/rollout-notes.md
git commit -m "docs: Jules Dry-Run Phase 24h abgeschlossen"
```

---

### Task 15.3: Live-Schaltung für ZERODOX

**Files:**
- Modify: `config/config.yaml`

**Step 1: `dry_run: false`**

```yaml
jules_workflow:
  enabled: true
  dry_run: false   # ← war true
  ...
```

**Step 2: Restart**

```bash
scripts/restart.sh --logs
```

**Step 3: Beobachtung**

Der nächste Jules-PR in ZERODOX wird live reviewt. Erwartete Indikatoren:
- 1 Comment im PR (nicht 10+)
- Label `claude-approved` bei Approval
- Discord-Ping im `🛡️-security-ops` Channel
- `iteration_count` in DB steigt korrekt

**Step 4: Wenn Problem → Sofort-Rollback**

```bash
# In config/config.yaml:
# enabled: false
# scripts/restart.sh
```

~30 Sekunden.

**Step 5: Wenn 48h OK → Rollout auf weitere Repos**

Keine Änderung nötig — `excluded_projects` steuert das. Standardmäßig sind alle Projekte außer `sicherheitsdienst` aktiv.

---

### Task 15.4: Gemini-Stash final löschen (nach erfolgreichem Rollout)

**Voraussetzung:** Phase 15.3 läuft 1 Woche ohne Incident.

```bash
git stash list | grep "gemini jules"
# Erwartet: stash@{0}: On main: gemini jules integration (buggy, replaced by 2026-04-11 design)
git stash drop stash@{0}
```

Der Gemini-Code ist danach final weg.

---

## Phase 16: Dokumentation

### Task 16.1: CLAUDE.md erweitern

**Files:**
- Modify: `CLAUDE.md` (root des shadowops-bot repos)

**Step 1: Neuer Abschnitt in der Integrations-Tabelle**

Suche in `CLAUDE.md` den Block "Einzelne Module" unter `src/integrations/` und füge hinzu:

```markdown
| `github_integration/jules_workflow_mixin.py` | Jules SecOps Workflow — PR-Handler, Gate-Pipeline (7 Schichten), Review-Orchestrierung |
| `github_integration/jules_state.py` | asyncpg-Layer für security_analyst.jules_pr_reviews, atomic Lock-Claim |
| `github_integration/jules_learning.py` | Few-Shot + Projekt-Knowledge Loader aus agent_learning DB |
| `github_integration/jules_review_prompt.py` | Claude-Prompt-Builder für strukturierte PR-Reviews |
| `github_integration/jules_gates.py` | Pure Loop-Schutz-Gates (Trigger-Whitelist, Cooldown, Cap, Circuit-Breaker) |
| `github_integration/jules_comment.py` | PR-Comment-Body-Builder + Self-Filter-Marker |
| `github_integration/jules_batch.py` | Nightly Outcome-Klassifizierung + jules_review_examples Update |
```

**Step 2: Architektur-Abschnitt**

Füge im Abschnitt "Architektur-Entscheidungen" hinzu:

```markdown
### Jules SecOps Workflow (seit 2026-04-11)
- **Hybrid-Fix:** ScanAgent fixt Server-Hardening selbst, delegiert Code-Fixes an Jules via GitHub-Issue mit `jules` Label
- **Claude-Review:** Strukturiert (BLOCKER/SUGGESTION/NIT), Schema-validiert, deterministischer Verdict
- **Loop-Schutz:** 7 Schichten (Trigger-Whitelist, SHA-Dedupe, Cooldown, Iteration-Cap 5, Circuit-Breaker 20/h, Time-Cap 2h, Single-Comment-Edit)
- **State:** `security_analyst.jules_pr_reviews` mit atomic Lock-Claim, Stale-Lock-Recovery nach 10min
- **Learning:** `agent_learning.jules_review_examples` + `agent_knowledge` (Few-Shot + Projekt-Konventionen), Nightly-Batch klassifiziert Outcomes
- **Rollback:** Config-Flag `jules_workflow.enabled: false` → ~30s
- **Design-Doc:** `docs/plans/2026-04-11-jules-secops-workflow-design.md`
- **Implementation-Plan:** `docs/plans/2026-04-11-jules-secops-workflow.md`
- **Vorfall-Referenz:** PR #123 (ZERODOX) — 31 Kommentare Loop durch `issue_comment` Re-Trigger; siehe Design-Doc Anhang A
```

**Step 3: Safety-Rules erweitern**

In `.claude/rules/safety.md` neuer Block:

```markdown
## Jules SecOps Workflow (seit 2026-04-11)
- **NIEMALS `issue_comment` Events für Auto-Reviews whitelisten** — das war die PR #123 Hauptursache
- **NIEMALS `_validate_ai_output` aus Jules-Pipeline entfernen** — schützt vor halluzinierten Blockern
- **Single-Comment-Edit Strategie ist Pflicht** — neuer Comment pro Iteration triggert Webhook-Loop
- **`compute_verdict` ist deterministisch, nicht AI-überschreibbar** — schützt vor Confidence-Oszillation
- **max_iterations: 5 und max_hours_per_pr: 2 sind harte Limits** — bei Änderung Design-Doc Anhang A re-reviewen
- **Circuit-Breaker 20/h pro Repo NIE erhöhen** ohne Incident-Analyse
- **Stale-Lock-Timeout 10min nicht verkürzen** — manche AI-Calls brauchen 5-8 Minuten
- **Bei Jules-Workflow-Änderungen IMMER `test_jules_pr123_regression.py` laufen lassen**
```

**Step 4: Commit**

```bash
git add CLAUDE.md .claude/rules/safety.md
git commit -m "docs: Jules SecOps Workflow — CLAUDE.md + safety.md Integration"
```

---

## Execution Summary

**Total Tasks:** 30 Tasks über 16 Phasen.

**Geschätzte Dauer:**
- Phase 1-3 (DB + State): 2h
- Phase 4-6 (Schema + Prompt + AI): 2h
- Phase 7-8 (Gates + Mixin): 2h
- Phase 9-11 (Comment + Escalation + Wiring): 3h
- Phase 12-14 (ScanAgent + Health + Learning): 2h
- Phase 15 (Rollout): 3 Tage (nicht aktiv-Zeit)
- Phase 16 (Docs): 30min

**Aktive Entwicklungszeit:** ~11h. Rollout dauert zusätzlich 3-5 Tage Beobachtung.

**Abbruch-Kriterien:**
- Wenn Tests in Phase 1-14 auf mehr als 2 Iterationen fehlschlagen: Design-Doc erneut lesen, Gate-Reihenfolge prüfen
- Wenn Dry-Run in Phase 15.2 auch nur 1 echten PR-Comment postet: SOFORT `enabled: false`
- Wenn Live-Phase 15.3 mehr als 3 Reviews pro PR erzeugt: Circuit-Breaker triggert — Post-Mortem schreiben, nicht einfach Limit erhöhen

**Rollback-Garantie:**
- Code-Änderungen: `git stash` (Gemini) ist noch da, plus alle neuen Änderungen sind revertierbar via `git revert <commit>`
- Config-Änderungen: `jules_workflow.enabled: false` → ~30s
- DB-Änderungen: Tabellen sind `CREATE TABLE IF NOT EXISTS`, optional `DROP TABLE jules_pr_reviews; DROP TABLE jules_review_examples;`

---

**Plan complete and saved to `docs/plans/2026-04-11-jules-secops-workflow.md`.**

Next step siehe unten — Execution-Handoff.
