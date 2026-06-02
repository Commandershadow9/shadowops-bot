# Security-Agent-Team P1 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Das Fundament des Security-Agent-Teams bauen — Job-Contract, BaseWorker, Orchestrator-Stub und einen echten `npm-audit-worker` — neben dem laufenden `scan_agent`-Monolithen, Feature-Flag default OFF.

**Architecture:** Always-on systemd-Worker subscriben einen Redis-Channel (`sec:job:<type>:request`), verarbeiten Jobs isoliert (Exception → FAILED, kein Bot-Crash), schreiben Findings über einen neuen geteilten `SecurityDB.store_finding()`-Helper und persistieren den Lifecycle in `sec_jobs`. Der Orchestrator zerlegt einen `sec:trigger` in Jobs. Token-Cap ist als Seam vorbereitet (Enforcement erst P2).

**Tech Stack:** Python 3.11, pydantic, asyncpg (bestehender `SecurityDB`-Pool), `redis.asyncio`, pytest mit AsyncMock. Vorbild: `~/agents/projects/seo/` (Pattern-Copy, **kein** Cross-Repo-Import).

**Spec:** [`docs/design/2026-06-02-security-agent-team-p1-spec.md`](../design/2026-06-02-security-agent-team-p1-spec.md)

---

## File Structure

**Neu:**
| Datei | Verantwortung |
|---|---|
| `src/integrations/security_engine/team/__init__.py` | Package-Marker |
| `src/integrations/security_engine/team/contracts.py` | `SecurityJob`, `JobResult`, `JobStatus` (pydantic) |
| `src/integrations/security_engine/team/base_worker.py` | `BaseSecurityWorker` (Lifecycle + Exception-Isolation) |
| `src/integrations/security_engine/team/orchestrator.py` | `SecurityOrchestrator` (Trigger → Dispatch) |
| `src/integrations/security_engine/team/runner.py` | `run_worker()` Entrypoint (Redis-Subscribe-Loop) |
| `src/integrations/security_engine/team/workers/__init__.py` | Package-Marker |
| `src/integrations/security_engine/team/workers/npm_audit_worker.py` | `NpmAuditWorker` |
| `src/integrations/security_engine/migrations/002_sec_jobs.sql` | DDL-Doku-Parität zu 001 (operativ: `_ensure_schema()`) |
| `deploy/security-orchestrator.service` | systemd-Template (Install = Ops-Schritt) |
| `deploy/security-npm-audit-worker.service` | systemd-Template |
| `tests/unit/test_security_contracts.py` | Contract-Tests |
| `tests/unit/test_store_finding.py` | store_finding-Helper-Tests |
| `tests/unit/test_store_finding_extraction.py` | Regression: Call-Sites delegieren |
| `tests/unit/test_base_security_worker.py` | Lifecycle/Isolation |
| `tests/unit/test_security_orchestrator.py` | Dispatch/Fan-out |
| `tests/unit/test_npm_audit_worker.py` | Parser/Dedup/Subprocess-Mock |
| `tests/unit/test_config_security_team.py` | Config-Properties |

**Geändert:**
| Datei | Änderung |
|---|---|
| `src/integrations/security_engine/db.py` | `store_finding()`-Methode + `sec_jobs` in `_ensure_schema()` |
| `src/integrations/security_engine/deep_scan.py` | `_store_finding` → `db.store_finding`-Call |
| `src/integrations/security_engine/scan_agent.py` | inline-INSERT → `db.store_finding`-Call |
| `src/utils/config.py` | `security_team_enabled` / `_projects` / `_active_workers` |
| `config/config.example.yaml` | `security_team`-Sektion |
| `CLAUDE.md` | `team/`-Module dokumentieren |

---

## Task 1: `store_finding()`-Helper auf SecurityDB

**Files:**
- Modify: `src/integrations/security_engine/db.py`
- Test: `tests/unit/test_store_finding.py`

Verhaltenstreue Obermenge der zwei bestehenden INSERTs (`deep_scan.py:409` = 5 Spalten ohne Fingerprint; `scan_agent.py:1206` = 10 Spalten mit Fingerprint). Fehlende Felder → `NULL`. `status` + `found_at` werden explizit gesetzt (equivalent zu den heutigen Defaults). Gibt `findings.id` zurück.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_store_finding.py
import pytest
from unittest.mock import AsyncMock
from src.integrations.security_engine.db import SecurityDB


def _db_with_mock_pool(return_id=42):
    db = SecurityDB("postgres://unused")
    db.pool = AsyncMock()
    db.pool.fetchrow = AsyncMock(return_value={"id": return_id})
    return db


@pytest.mark.asyncio
async def test_store_finding_minimal_profile_returns_id():
    db = _db_with_mock_pool(return_id=7)
    fid = await db.store_finding(
        severity="HIGH", category="npm_audit",
        title="t", description="d", affected_project="guildscout",
    )
    assert fid == 7
    # Genau ein INSERT, RETURNING id
    sql = db.pool.fetchrow.call_args.args[0]
    assert "INSERT INTO findings" in sql
    assert "RETURNING id" in sql
    # Nicht uebergebene Felder kommen als None in den Args
    args = db.pool.fetchrow.call_args.args[1:]
    assert "guildscout" in args
    assert None in args  # session_id/fingerprint etc. = None


@pytest.mark.asyncio
async def test_store_finding_full_profile_passes_fingerprint():
    db = _db_with_mock_pool(return_id=99)
    fid = await db.store_finding(
        severity="info", category="code_security", title="t", description="d",
        session_id=5, affected_project="zerodox",
        affected_files=["a.ts"], fix_type="manual",
        github_issue_url="https://x/1", finding_fingerprint="abc123",
    )
    assert fid == 99
    args = db.pool.fetchrow.call_args.args[1:]
    assert "abc123" in args
    assert "zerodox" in args


@pytest.mark.asyncio
async def test_store_finding_db_error_returns_none():
    db = SecurityDB("postgres://unused")
    db.pool = AsyncMock()
    db.pool.fetchrow = AsyncMock(side_effect=RuntimeError("boom"))
    fid = await db.store_finding(
        severity="LOW", category="x", title="t", description="d",
    )
    assert fid is None  # Fehler wird geschluckt, kein Re-raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_store_finding.py -x -q`
Expected: FAIL mit `AttributeError: 'SecurityDB' object has no attribute 'store_finding'`

- [ ] **Step 3: Write minimal implementation**

In `src/integrations/security_engine/db.py`, neue Methode in der Klasse `SecurityDB` (z.B. direkt nach `_ensure_schema`):

```python
    async def store_finding(
        self,
        *,
        severity: str,
        category: str,
        title: str,
        description: str,
        affected_project: Optional[str] = None,
        session_id: Optional[int] = None,
        affected_files: Optional[list] = None,
        fix_type: Optional[str] = None,
        github_issue_url: Optional[str] = None,
        finding_fingerprint: Optional[str] = None,
        status: str = "open",
    ) -> Optional[int]:
        """Schreibt EIN Finding (verhaltenstreue Obermenge beider Alt-INSERTs).

        Fehlende Felder werden als NULL gespeichert. Dedup ist NICHT Aufgabe
        dieses Helpers — der Caller berechnet/prueft den Fingerprint selbst.
        Gibt findings.id zurueck oder None bei Fehler.
        """
        try:
            row = await self.pool.fetchrow(
                """
                INSERT INTO findings (
                    severity, category, title, description, session_id,
                    affected_project, affected_files, fix_type, github_issue_url,
                    finding_fingerprint, status, found_at
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11, NOW())
                RETURNING id
                """,
                severity, category, title, description, session_id,
                affected_project, affected_files, fix_type, github_issue_url,
                finding_fingerprint, status,
            )
            return row["id"] if row else None
        except Exception:
            logger.warning("store_finding fehlgeschlagen (title=%r)", title, exc_info=True)
            return None
```

Sicherstellen, dass `Optional` und `logger` in `db.py` bereits importiert sind (sie sind es — `logger` wird oben verwendet, `Optional` im Pool-Typ).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_store_finding.py -x -q`
Expected: PASS (3 Tests)

- [ ] **Step 5: Commit**

```bash
git add src/integrations/security_engine/db.py tests/unit/test_store_finding.py
git commit -m "feat(secops): SecurityDB.store_finding() geteilter Helper (#290 P1)"
```

---

## Task 2: Call-Sites auf `store_finding()` migrieren (Regression)

**Files:**
- Modify: `src/integrations/security_engine/deep_scan.py:409-430`
- Modify: `src/integrations/security_engine/scan_agent.py:1205-1214`
- Test: `tests/unit/test_store_finding_extraction.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_store_finding_extraction.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from src.integrations.security_engine.deep_scan import DeepScanMode  # Klassenname ggf. anpassen


@pytest.mark.asyncio
async def test_deep_scan_store_finding_delegates_to_helper():
    # DeepScan haelt eine .db (SecurityDB-aehnlich) mit store_finding
    db = MagicMock()
    db.store_finding = AsyncMock(return_value=11)
    scanner = DeepScanMode.__new__(DeepScanMode)  # ohne __init__-Seiteneffekte
    scanner.db = db
    fid = await scanner._store_finding({
        "severity": "HIGH", "category": "general",
        "title": "T", "description": "D", "affected_project": "server",
    })
    assert fid == 11
    db.store_finding.assert_awaited_once()
    kwargs = db.store_finding.await_args.kwargs
    assert kwargs["severity"] == "HIGH"
    assert kwargs["affected_project"] == "server"
```

> Hinweis: Den exakten Klassennamen in `deep_scan.py` mit `grep -n "^class" src/integrations/security_engine/deep_scan.py` verifizieren und im Test einsetzen.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_store_finding_extraction.py -x -q`
Expected: FAIL (alter `_store_finding` macht inline-INSERT, ruft `db.store_finding` nicht)

- [ ] **Step 3: Implement — deep_scan migrieren**

`src/integrations/security_engine/deep_scan.py`, Methode `_store_finding` ersetzen durch Delegation:

```python
    async def _store_finding(self, finding: Dict) -> Optional[int]:
        """Speichert ein Finding via geteilten Helper (Verhalten 1:1)."""
        if not (hasattr(self.db, "store_finding")):
            return None
        return await self.db.store_finding(
            severity=finding.get("severity", "MEDIUM"),
            category=finding.get("category", "general"),
            title=finding.get("title", "Unknown"),
            description=finding.get("description", ""),
            affected_project=finding.get("affected_project", "server"),
        )
```

- [ ] **Step 4: Implement — scan_agent migrieren**

`src/integrations/security_engine/scan_agent.py`, den inline-Block bei `~1205` (`await self.db.pool.execute("""INSERT INTO findings ...""", ...)`) ersetzen durch:

```python
                # fp wurde oben beim Dedup-Lookup berechnet — wiederverwenden.
                await self.db.store_finding(
                    severity=finding.get('severity', 'info'),
                    category=finding.get('category', 'unknown'),
                    title=title,
                    description=finding.get('description', ''),
                    session_id=session_id,
                    affected_project=finding.get('affected_project'),
                    affected_files=finding.get('affected_files'),
                    fix_type=fix_type,
                    github_issue_url=github_issue_url,
                    finding_fingerprint=fp,
                )
```

- [ ] **Step 5: Run tests to verify pass + no regression**

Run: `.venv/bin/python -m pytest tests/unit/test_store_finding_extraction.py tests/unit/test_store_finding.py -x -q`
Expected: PASS. Zusätzlich, falls vorhanden: `.venv/bin/python -m pytest tests/unit/test_scan_agent.py -x -q` und `test_deep_scan*.py` grün.

- [ ] **Step 6: Commit**

```bash
git add src/integrations/security_engine/deep_scan.py src/integrations/security_engine/scan_agent.py tests/unit/test_store_finding_extraction.py
git commit -m "refactor(secops): findings-INSERTs auf store_finding() vereinheitlichen (#290 P1)"
```

---

## Task 3: `sec_jobs`-Tabelle in `_ensure_schema()`

**Files:**
- Modify: `src/integrations/security_engine/db.py` (`_ensure_schema`)
- Create: `src/integrations/security_engine/migrations/002_sec_jobs.sql`
- Test: `tests/unit/test_sec_jobs_schema.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_sec_jobs_schema.py
import inspect
from src.integrations.security_engine.db import SecurityDB


def test_ensure_schema_creates_sec_jobs():
    src = inspect.getsource(SecurityDB._ensure_schema)
    assert "CREATE TABLE IF NOT EXISTS sec_jobs" in src
    assert "job_id" in src and "worker_type" in src and "status" in src
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_sec_jobs_schema.py -x -q`
Expected: FAIL (`sec_jobs` nicht in `_ensure_schema`)

- [ ] **Step 3: Implement**

In `db.py` `_ensure_schema`, am Ende des `async with self.pool.acquire() as conn:`-Blocks ergänzen:

```python
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS sec_jobs (
                    job_id        UUID PRIMARY KEY,
                    worker_type   TEXT NOT NULL,
                    project       TEXT NOT NULL,
                    trigger       TEXT NOT NULL DEFAULT 'manual',
                    status        TEXT NOT NULL DEFAULT 'queued',
                    payload       JSONB NOT NULL DEFAULT '{}'::jsonb,
                    result        JSONB,
                    tokens_used   INTEGER NOT NULL DEFAULT 0,
                    error_message TEXT,
                    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    started_at    TIMESTAMPTZ,
                    completed_at  TIMESTAMPTZ
                );
                CREATE INDEX IF NOT EXISTS idx_sec_jobs_status  ON sec_jobs (status);
                CREATE INDEX IF NOT EXISTS idx_sec_jobs_project ON sec_jobs (project, created_at DESC);
            """)
```

Und die Doku-Parität `migrations/002_sec_jobs.sql` mit demselben DDL anlegen (Kommentarkopf: „operativ angewandt via SecurityDB._ensure_schema(); diese Datei ist Doku-Parität zu 001").

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_sec_jobs_schema.py -x -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/integrations/security_engine/db.py src/integrations/security_engine/migrations/002_sec_jobs.sql tests/unit/test_sec_jobs_schema.py
git commit -m "feat(secops): sec_jobs-Tabelle in _ensure_schema (#290 P1)"
```

---

## Task 4: `team/contracts.py`

**Files:**
- Create: `src/integrations/security_engine/team/__init__.py` (leer)
- Create: `src/integrations/security_engine/team/contracts.py`
- Test: `tests/unit/test_security_contracts.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_security_contracts.py
import uuid
import pytest
from pydantic import ValidationError
from src.integrations.security_engine.team.contracts import (
    SecurityJob, JobResult, JobStatus,
)


def test_security_job_defaults():
    job = SecurityJob(worker_type="npm_audit", project="guildscout")
    assert isinstance(job.job_id, uuid.UUID)
    assert job.trigger == "manual"
    assert job.token_cost == 0
    assert job.payload == {}


def test_security_job_roundtrip():
    job = SecurityJob(worker_type="npm_audit", project="zerodox",
                      payload={"path": "/x"})
    again = SecurityJob.model_validate_json(job.model_dump_json())
    assert again.job_id == job.job_id
    assert again.payload == {"path": "/x"}


def test_security_job_rejects_empty_worker_type():
    with pytest.raises(ValidationError):
        SecurityJob(worker_type="  ", project="guildscout")


def test_job_result_roundtrip():
    jid = uuid.uuid4()
    r = JobResult(job_id=jid, worker="npm_audit", project="guildscout",
                  status=JobStatus.OK, findings_added=3)
    again = JobResult.model_validate_json(r.model_dump_json())
    assert again.status == JobStatus.OK
    assert again.findings_added == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_security_contracts.py -x -q`
Expected: FAIL (`ModuleNotFoundError: ...team.contracts`)

- [ ] **Step 3: Write implementation**

`src/integrations/security_engine/team/__init__.py` → leere Datei.

`src/integrations/security_engine/team/contracts.py`:

```python
"""Job-Contract zwischen Security-Orchestrator und Workern.

Worker subscriben "sec:job:<worker_type>:request" und publishen Result auf
"sec:job:<worker_type>:result". Persistierung parallel in sec_jobs. Muster aus
~/agents/projects/seo/contracts/job.py — nativ nachgebaut (kein Cross-Repo-Import).
"""
from __future__ import annotations

import datetime as dt
import enum
import uuid
from typing import Any

from pydantic import BaseModel, Field, field_validator


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    OK = "ok"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SecurityJob(BaseModel):
    """Orchestrator → Worker."""
    job_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    worker_type: str
    project: str
    trigger: str = "manual"
    token_cost: int = 0  # SEAM fuer P2-Token-Bucket; npm_audit=0
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: dt.datetime = Field(
        default_factory=lambda: dt.datetime.now(dt.timezone.utc)
    )

    @field_validator("worker_type", "project")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must be non-empty")
        return v


class JobResult(BaseModel):
    """Worker → Orchestrator."""
    job_id: uuid.UUID
    worker: str
    project: str
    status: JobStatus
    findings_added: int = 0
    duration_ms: int = 0
    tokens_used: int = 0
    errors: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_security_contracts.py -x -q`
Expected: PASS (4 Tests)

- [ ] **Step 5: Commit**

```bash
git add src/integrations/security_engine/team/__init__.py src/integrations/security_engine/team/contracts.py tests/unit/test_security_contracts.py
git commit -m "feat(secops): SecurityJob/JobResult Contract (#290 P1)"
```

---

## Task 5: `team/base_worker.py`

**Files:**
- Create: `src/integrations/security_engine/team/base_worker.py`
- Test: `tests/unit/test_base_security_worker.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_base_security_worker.py
import uuid
import pytest
from unittest.mock import AsyncMock
from src.integrations.security_engine.team.base_worker import BaseSecurityWorker
from src.integrations.security_engine.team.contracts import (
    SecurityJob, JobResult, JobStatus,
)


class _OkWorker(BaseSecurityWorker):
    worker_type = "test_ok"
    async def process(self, job):
        return JobResult(job_id=job.job_id, worker=self.worker_type,
                         project=job.project, status=JobStatus.OK, findings_added=2)


class _BoomWorker(BaseSecurityWorker):
    worker_type = "test_boom"
    async def process(self, job):
        raise RuntimeError("explode")


class _NoType(BaseSecurityWorker):
    async def process(self, job):
        return None


def _db():
    db = AsyncMock()
    db.pool = AsyncMock()
    return db


def test_missing_worker_type_raises():
    with pytest.raises(TypeError):
        _NoType(db=_db())


@pytest.mark.asyncio
async def test_success_persists_lifecycle():
    db = _db()
    w = _OkWorker(db=db)
    job = SecurityJob(worker_type="test_ok", project="guildscout")
    res = await w.handle_request(job)
    assert res.status == JobStatus.OK
    assert res.findings_added == 2
    # in_progress INSERT + completion UPDATE
    assert db.pool.execute.await_count == 2


@pytest.mark.asyncio
async def test_exception_returns_failed_no_reraise():
    db = _db()
    w = _BoomWorker(db=db)
    job = SecurityJob(worker_type="test_boom", project="zerodox")
    res = await w.handle_request(job)  # darf NICHT raisen
    assert res.status == JobStatus.FAILED
    assert any("explode" in e for e in res.errors)


@pytest.mark.asyncio
async def test_db_persist_error_does_not_crash():
    db = _db()
    db.pool.execute = AsyncMock(side_effect=RuntimeError("db down"))
    w = _OkWorker(db=db)
    job = SecurityJob(worker_type="test_ok", project="guildscout")
    res = await w.handle_request(job)  # DB-Fehler geschluckt
    assert res.status == JobStatus.OK
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_base_security_worker.py -x -q`
Expected: FAIL (`ModuleNotFoundError: ...team.base_worker`)

- [ ] **Step 3: Write implementation**

`src/integrations/security_engine/team/base_worker.py`:

```python
"""BaseSecurityWorker — Pattern fuer alle Security-Team-Worker.

Subclasses implementieren `process(job) -> JobResult`. Die Basis handhabt
Lifecycle-Persistierung (sec_jobs), Exception-Catching (→ FAILED, kein Re-raise),
Timing. Muster aus ~/agents/projects/seo/workers/base.py — nativ nachgebaut.
"""
from __future__ import annotations

import abc
import json
import logging
import time
from typing import Any

from .contracts import SecurityJob, JobResult, JobStatus

logger = logging.getLogger("security.worker.base")


class BaseSecurityWorker(abc.ABC):
    worker_type: str = ""  # Subclass MUSS setzen

    def __init__(self, db: Any) -> None:
        if not self.worker_type:
            raise TypeError(f"{self.__class__.__name__} muss worker_type setzen")
        self.db = db

    @abc.abstractmethod
    async def process(self, job: SecurityJob) -> JobResult:
        raise NotImplementedError

    async def handle_request(self, job: SecurityJob) -> JobResult:
        t0 = time.monotonic()
        await self._persist_in_progress(job)
        try:
            result = await self.process(job)
        except Exception as exc:
            duration_ms = int((time.monotonic() - t0) * 1000)
            logger.exception("Worker %s crashed bei job %s", self.worker_type, job.job_id)
            result = JobResult(
                job_id=job.job_id, worker=self.worker_type, project=job.project,
                status=JobStatus.FAILED, errors=[f"{type(exc).__name__}: {exc}"],
                duration_ms=duration_ms,
            )
        else:
            if result.duration_ms == 0:
                result.duration_ms = int((time.monotonic() - t0) * 1000)
        await self._persist_completion(job, result)
        return result

    async def _persist_in_progress(self, job: SecurityJob) -> None:
        try:
            await self.db.pool.execute(
                """INSERT INTO sec_jobs
                   (job_id, worker_type, project, trigger, status, payload, started_at)
                   VALUES ($1,$2,$3,$4,'in_progress',$5::jsonb, NOW())
                   ON CONFLICT (job_id) DO UPDATE
                   SET status='in_progress', started_at=NOW()""",
                job.job_id, job.worker_type, job.project, job.trigger,
                json.dumps(job.payload, default=str),
            )
        except Exception:
            logger.warning("sec_jobs in_progress nicht persistiert (job=%s)",
                           job.job_id, exc_info=True)

    async def _persist_completion(self, job: SecurityJob, result: JobResult) -> None:
        try:
            await self.db.pool.execute(
                """UPDATE sec_jobs
                   SET status=$2, completed_at=NOW(), result=$3::jsonb,
                       tokens_used=$4, error_message=$5
                   WHERE job_id=$1""",
                job.job_id, result.status.value, result.model_dump_json(),
                result.tokens_used,
                "\n".join(result.errors) if result.errors else None,
            )
        except Exception:
            logger.warning("sec_jobs completion nicht persistiert (job=%s)",
                           job.job_id, exc_info=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_base_security_worker.py -x -q`
Expected: PASS (4 Tests)

- [ ] **Step 5: Commit**

```bash
git add src/integrations/security_engine/team/base_worker.py tests/unit/test_base_security_worker.py
git commit -m "feat(secops): BaseSecurityWorker mit Lifecycle + Exception-Isolation (#290 P1)"
```

---

## Task 6: `team/orchestrator.py`

**Files:**
- Create: `src/integrations/security_engine/team/orchestrator.py`
- Test: `tests/unit/test_security_orchestrator.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_security_orchestrator.py
import pytest
from unittest.mock import AsyncMock
from src.integrations.security_engine.team.orchestrator import SecurityOrchestrator
from src.integrations.security_engine.team.contracts import SecurityJob


def _redis():
    r = AsyncMock()
    r.publish = AsyncMock(return_value=1)
    return r


def _db():
    db = AsyncMock()
    db.pool = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_dispatch_publishes_and_persists():
    redis, db = _redis(), _db()
    orch = SecurityOrchestrator(redis=redis, db=db)
    job = await orch.dispatch_job("npm_audit", "guildscout", payload={"path": "/x"})
    assert isinstance(job, SecurityJob)
    # publish auf richtigen Channel
    channel = redis.publish.await_args.args[0]
    assert channel == "sec:job:npm_audit:request"
    # queued persistiert
    assert db.pool.execute.await_count == 1


@pytest.mark.asyncio
async def test_handle_trigger_fans_out_per_project_with_path():
    redis, db = _redis(), _db()
    orch = SecurityOrchestrator(redis=redis, db=db)
    jobs = await orch.handle_trigger(
        projects={
            "guildscout": {"npm_audit_path": "/g/web"},
            "zerodox": {"npm_audit_path": "/z"},
            "nopath": {"something_else": "/n"},  # ohne npm_audit_path → skip
        },
        active_workers=["npm_audit"],
    )
    assert len(jobs) == 2
    assert {j.project for j in jobs} == {"guildscout", "zerodox"}
    assert redis.publish.await_count == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_security_orchestrator.py -x -q`
Expected: FAIL (`ModuleNotFoundError: ...team.orchestrator`)

- [ ] **Step 3: Write implementation**

`src/integrations/security_engine/team/orchestrator.py`:

```python
"""Security-Orchestrator — Stub fuer P1.

Nimmt einen Trigger, zerlegt ihn in 1 Job pro aktivem Worker-Typ × Projekt,
persistiert queued und publisht sec:job:<type>:request. Result-Aggregation
folgt in P2. Muster aus ~/agents/projects/seo/orchestrator.py.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from .contracts import SecurityJob

logger = logging.getLogger("security.orchestrator")


class SecurityOrchestrator:
    def __init__(self, redis: Any, db: Any) -> None:
        self.redis = redis
        self.db = db

    async def dispatch_job(
        self,
        worker_type: str,
        project: str,
        payload: dict | None = None,
        trigger: str = "manual",
        token_cost: int = 0,
    ) -> SecurityJob:
        job = SecurityJob(
            worker_type=worker_type, project=project,
            payload=payload or {}, trigger=trigger, token_cost=token_cost,
        )
        # P2-SEAM: vor dem Dispatch ein Redis-Token erwerben, wenn token_cost > 0
        # (LLM-Worker). P1: npm_audit hat token_cost=0 → kein Cap noetig.
        try:
            await self.db.pool.execute(
                """INSERT INTO sec_jobs
                   (job_id, worker_type, project, trigger, status, payload)
                   VALUES ($1,$2,$3,$4,'queued',$5::jsonb)""",
                job.job_id, job.worker_type, job.project, job.trigger,
                json.dumps(job.payload, default=str),
            )
        except Exception:
            logger.warning("queued-Job nicht persistiert (job=%s)", job.job_id, exc_info=True)

        channel = f"sec:job:{worker_type}:request"
        subs = await self.redis.publish(channel, job.model_dump_json())
        logger.info("Job dispatched: type=%s project=%s job=%s subs=%s",
                    worker_type, project, job.job_id, subs)
        return job

    async def handle_trigger(
        self, projects: dict[str, dict], active_workers: list[str],
    ) -> list[SecurityJob]:
        """Fan-out: je aktivem Worker-Typ × Projekt (mit passendem <type>_path) ein Job."""
        jobs: list[SecurityJob] = []
        for worker_type in active_workers:
            path_key = f"{worker_type}_path"
            for project, cfg in projects.items():
                if path_key not in cfg:
                    continue
                jobs.append(await self.dispatch_job(
                    worker_type=worker_type, project=project,
                    payload={"path": cfg[path_key]},
                ))
        return jobs
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_security_orchestrator.py -x -q`
Expected: PASS (2 Tests)

- [ ] **Step 5: Commit**

```bash
git add src/integrations/security_engine/team/orchestrator.py tests/unit/test_security_orchestrator.py
git commit -m "feat(secops): SecurityOrchestrator Stub mit Fan-out (#290 P1)"
```

---

## Task 7: `team/workers/npm_audit_worker.py`

**Files:**
- Create: `src/integrations/security_engine/team/workers/__init__.py` (leer)
- Create: `src/integrations/security_engine/team/workers/npm_audit_worker.py`
- Test: `tests/unit/test_npm_audit_worker.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_npm_audit_worker.py
import json
import pytest
from unittest.mock import AsyncMock, patch
from src.integrations.security_engine.team.workers.npm_audit_worker import NpmAuditWorker
from src.integrations.security_engine.team.contracts import SecurityJob, JobStatus

_AUDIT_JSON = json.dumps({
    "vulnerabilities": {
        "lodash": {"severity": "high", "range": "<4.17.21",
                   "via": [{"title": "Prototype Pollution", "url": "https://x/1"}]},
        "minimist": {"severity": "critical", "range": "<1.2.6", "via": ["lodash"]},
    }
})


def _db():
    db = AsyncMock()
    db.pool = AsyncMock()
    db.pool.execute = AsyncMock()
    db.pool.fetchval = AsyncMock(return_value=None)  # nichts dedupt
    db.store_finding = AsyncMock(side_effect=[101, 102])
    return db


def test_parse_extracts_findings():
    out = NpmAuditWorker._parse(_AUDIT_JSON, "guildscout")
    assert len(out) == 2
    assert any(f["severity"] == "HIGH" for f in out)
    assert all(f["category"] == "npm_audit" for f in out)


def test_parse_broken_json_returns_empty():
    assert NpmAuditWorker._parse("not-json", "guildscout") == []


@pytest.mark.asyncio
async def test_process_missing_path_is_partial():
    w = NpmAuditWorker(db=_db())
    job = SecurityJob(worker_type="npm_audit", project="guildscout", payload={})
    res = await w.process(job)
    assert res.status == JobStatus.PARTIAL


@pytest.mark.asyncio
async def test_process_stores_new_findings():
    db = _db()
    w = NpmAuditWorker(db=db)
    job = SecurityJob(worker_type="npm_audit", project="guildscout",
                      payload={"path": "/tmp"})
    with patch.object(NpmAuditWorker, "_run_npm_audit",
                      new=AsyncMock(return_value=_AUDIT_JSON)), \
         patch("os.path.isdir", return_value=True):
        res = await w.process(job)
    assert res.status == JobStatus.OK
    assert res.findings_added == 2
    assert db.store_finding.await_count == 2


@pytest.mark.asyncio
async def test_process_skips_deduped_findings():
    db = _db()
    db.pool.fetchval = AsyncMock(return_value=1)  # alles existiert bereits
    w = NpmAuditWorker(db=db)
    job = SecurityJob(worker_type="npm_audit", project="guildscout",
                      payload={"path": "/tmp"})
    with patch.object(NpmAuditWorker, "_run_npm_audit",
                      new=AsyncMock(return_value=_AUDIT_JSON)), \
         patch("os.path.isdir", return_value=True):
        res = await w.process(job)
    assert res.findings_added == 0
    assert db.store_finding.await_count == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_npm_audit_worker.py -x -q`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Write implementation**

`src/integrations/security_engine/team/workers/__init__.py` → leer.

`src/integrations/security_engine/team/workers/npm_audit_worker.py`:

```python
"""npm-audit-Worker — Dependency-CVE-Scan via `npm audit --json`.

Kleinster Worker-Scope (der #1069-Fall). Dedup im Worker via
compute_finding_fingerprint, schreibt Findings ueber db.store_finding().
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

from ..base_worker import BaseSecurityWorker
from ..contracts import SecurityJob, JobResult, JobStatus
from ...fingerprint import compute_finding_fingerprint

logger = logging.getLogger("security.worker.npm_audit")

_NPM_TIMEOUT_S = 180


class NpmAuditWorker(BaseSecurityWorker):
    worker_type = "npm_audit"

    async def process(self, job: SecurityJob) -> JobResult:
        path = job.payload.get("path")
        if not path or not os.path.isdir(path):
            return JobResult(job_id=job.job_id, worker=self.worker_type,
                             project=job.project, status=JobStatus.PARTIAL,
                             errors=[f"Pfad fehlt/ungueltig: {path!r}"])
        try:
            raw = await self._run_npm_audit(path)
        except FileNotFoundError:
            return JobResult(job_id=job.job_id, worker=self.worker_type,
                             project=job.project, status=JobStatus.PARTIAL,
                             errors=["npm nicht im PATH"])
        except asyncio.TimeoutError:
            return JobResult(job_id=job.job_id, worker=self.worker_type,
                             project=job.project, status=JobStatus.PARTIAL,
                             errors=[f"npm audit Timeout (>{_NPM_TIMEOUT_S}s)"])

        findings = self._parse(raw, job.project)
        added = 0
        for f in findings:
            fp = compute_finding_fingerprint("npm_audit", job.project, None, f["title"])
            exists = await self.db.pool.fetchval(
                "SELECT 1 FROM findings WHERE finding_fingerprint=$1 AND status='open' LIMIT 1",
                fp,
            )
            if exists:
                continue
            fid = await self.db.store_finding(
                severity=f["severity"], category=f["category"],
                title=f["title"], description=f["description"],
                affected_project=job.project, finding_fingerprint=fp,
            )
            if fid:
                added += 1

        return JobResult(job_id=job.job_id, worker=self.worker_type,
                         project=job.project, status=JobStatus.OK,
                         findings_added=added,
                         metadata={"scanned_path": path, "raw_count": len(findings)})

    async def _run_npm_audit(self, path: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            "npm", "audit", "--json", cwd=path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "CLAUDECODE": ""},  # nested-session-Schutz
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=_NPM_TIMEOUT_S)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise
        # npm exit-code != 0 ist NORMAL wenn Vulns existieren → stdout trotzdem parsen
        return stdout.decode("utf-8", errors="replace")

    @staticmethod
    def _parse(raw: str, project: str) -> list[dict]:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return []
        out: list[dict] = []
        for name, info in (data.get("vulnerabilities") or {}).items():
            severity = str(info.get("severity", "unknown")).upper()
            advisories = [v for v in info.get("via", []) if isinstance(v, dict)]
            title = advisories[0].get("title") if advisories else f"Vulnerable package: {name}"
            url = advisories[0].get("url", "") if advisories else ""
            out.append({
                "severity": severity,
                "category": "npm_audit",
                "title": f"[{name}] {title}"[:300],
                "description": f"Package {name} ({info.get('range', '?')}) — {url}",
            })
        return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_npm_audit_worker.py -x -q`
Expected: PASS (5 Tests)

- [ ] **Step 5: Commit**

```bash
git add src/integrations/security_engine/team/workers/ tests/unit/test_npm_audit_worker.py
git commit -m "feat(secops): npm-audit-worker mit Dedup (#290 P1)"
```

---

## Task 8: `team/runner.py` (Entrypoint)

**Files:**
- Create: `src/integrations/security_engine/team/runner.py`
- Test: `tests/unit/test_security_runner.py`

Der Subscribe-Loop ist Integrations-Code; Unit-Test deckt nur die Verdrahtung (Worker-Instanziierung + Channel-Namen) ab.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_security_runner.py
from src.integrations.security_engine.team.runner import worker_channels
from src.integrations.security_engine.team.workers.npm_audit_worker import NpmAuditWorker


def test_worker_channels():
    req, res = worker_channels(NpmAuditWorker.worker_type)
    assert req == "sec:job:npm_audit:request"
    assert res == "sec:job:npm_audit:result"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_security_runner.py -x -q`
Expected: FAIL (`ImportError: cannot import name 'worker_channels'`)

- [ ] **Step 3: Write implementation**

`src/integrations/security_engine/team/runner.py`:

```python
"""Worker-Runner — DRY-Entrypoint fuer alle Security-Team-Worker.

Jeder Worker-Service ruft run_worker(WorkerCls) in seiner main(). Der Runner
baut DB-Pool (SecurityDB) + Redis, subscribed den Request-Channel und publisht
Results. Muster aus ~/agents/projects/seo/workers/runner.py.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal

import redis.asyncio as aioredis

from ..db import SecurityDB
from .base_worker import BaseSecurityWorker
from .contracts import SecurityJob

logger = logging.getLogger("security.worker.runner")


def worker_channels(worker_type: str) -> tuple[str, str]:
    return f"sec:job:{worker_type}:request", f"sec:job:{worker_type}:request".replace(
        ":request", ":result"
    )


async def _amain(worker_cls: type[BaseSecurityWorker]) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    dsn = os.environ.get("SECURITY_ANALYST_DB_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("SECURITY_ANALYST_DB_URL oder DATABASE_URL muss gesetzt sein")
    redis_url = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")

    db = SecurityDB(dsn)
    await db.initialize()  # stellt sec_jobs + findings sicher (_ensure_schema)
    rconn = aioredis.from_url(redis_url, decode_responses=True)

    worker = worker_cls(db=db)
    channel_request, channel_result = worker_channels(worker.worker_type)

    pubsub = rconn.pubsub()
    await pubsub.subscribe(channel_request)
    logger.info("%s bereit — subscribed=%s publish=%s",
                worker.worker_type, channel_request, channel_result)

    stop = asyncio.Event()

    def _graceful(*_):
        logger.info("SIGTERM — fahre %s herunter", worker.worker_type)
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _graceful)

    try:
        async for message in pubsub.listen():
            if stop.is_set():
                break
            if message.get("type") != "message":
                continue
            try:
                job = SecurityJob.model_validate(json.loads(message["data"]))
            except Exception:
                logger.exception("Invalid job-request fuer %s: %r",
                                 worker.worker_type, message.get("data"))
                continue
            result = await worker.handle_request(job)
            await rconn.publish(channel_result, result.model_dump_json())
    finally:
        await pubsub.unsubscribe(channel_request)
        await rconn.aclose()
        await db.close()


def run_worker(worker_cls: type[BaseSecurityWorker]) -> None:
    """Sync-Entry-Point fuer systemd."""
    asyncio.run(_amain(worker_cls))
```

> **Hinweis zur `worker_channels`-Implementierung:** Bewusst simpel gehalten, damit der
> Unit-Test sie ohne Redis prüfen kann. Sie liefert `(…:request, …:result)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_security_runner.py -x -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/integrations/security_engine/team/runner.py tests/unit/test_security_runner.py
git commit -m "feat(secops): Worker-Runner (Redis-Subscribe + Graceful-Shutdown) (#290 P1)"
```

---

## Task 9: Config-Properties + `config.example.yaml`

**Files:**
- Modify: `src/utils/config.py`
- Modify: `config/config.example.yaml`
- Test: `tests/unit/test_config_security_team.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_config_security_team.py
import os
from src.utils.config import Config  # Klassenname ggf. via grep verifizieren


def _cfg(d):
    c = Config.__new__(Config)
    c._config = d
    return c


def test_security_team_disabled_by_default():
    assert _cfg({})._security_team_enabled_value() is False


def test_security_team_enabled_via_config():
    c = _cfg({"security_team": {"enabled": True}})
    assert c._security_team_enabled_value() is True


def test_env_override_wins(monkeypatch):
    monkeypatch.setenv("SECURITY_TEAM_ENABLED", "true")
    c = _cfg({"security_team": {"enabled": False}})
    assert c._security_team_enabled_value() is True


def test_projects_and_workers():
    c = _cfg({"security_team": {
        "projects": {"guildscout": {"npm_audit_path": "/g/web"}},
        "active_workers": ["npm_audit"],
    }})
    assert c.security_team_projects == {"guildscout": {"npm_audit_path": "/g/web"}}
    assert c.security_team_active_workers == ["npm_audit"]
```

> Den exakten Klassennamen (`Config`?) und ob `_config` das interne Dict ist mit
> `grep -n "^class\|self\._config" src/utils/config.py` prüfen. Falls die Property
> nicht über ein Hilfsattribut testbar ist, statt `_security_team_enabled_value()`
> direkt `security_team_enabled` testen (ohne Env-Seiteneffekte auf Modulebene).

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_config_security_team.py -x -q`
Expected: FAIL (Properties existieren nicht)

- [ ] **Step 3: Write implementation**

In `src/utils/config.py` sicherstellen, dass `import os` oben steht. Properties ergänzen (analog `ai_enabled`):

```python
    @property
    def security_team_enabled(self) -> bool:
        """Feature-Flag fuer das Security-Agent-Team (P1). Default OFF.

        Env SECURITY_TEAM_ENABLED ueberschreibt config security_team.enabled.
        """
        return self._security_team_enabled_value()

    def _security_team_enabled_value(self) -> bool:
        env = os.environ.get("SECURITY_TEAM_ENABLED")
        if env is not None:
            return env.strip().lower() in ("1", "true", "yes", "on")
        return bool(self._config.get("security_team", {}).get("enabled", False))

    @property
    def security_team_projects(self) -> Dict[str, Any]:
        return self._config.get("security_team", {}).get("projects", {})

    @property
    def security_team_active_workers(self) -> List[str]:
        return list(self._config.get("security_team", {}).get("active_workers", []))
```

In `config/config.example.yaml` ergänzen (z.B. nach der `github:`-Sektion):

```yaml
# Security-Agent-Team (P1) — default OFF. Env-Override: SECURITY_TEAM_ENABLED
security_team:
  enabled: false
  active_workers: ["npm_audit"]
  projects:
    guildscout:
      npm_audit_path: "/home/cmdshadow/GuildScout/web"
    zerodox:
      npm_audit_path: "/home/cmdshadow/ZERODOX"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_config_security_team.py -x -q`
Expected: PASS (4 Tests)

- [ ] **Step 5: Commit**

```bash
git add src/utils/config.py config/config.example.yaml tests/unit/test_config_security_team.py
git commit -m "feat(secops): security_team Config-Properties + Beispiel (#290 P1)"
```

---

## Task 10: systemd-Templates (Install = Ops-Schritt, kein Service-State im PR)

**Files:**
- Create: `deploy/security-orchestrator.service`
- Create: `deploy/security-npm-audit-worker.service`

> **Regel (CLAUDE.md):** Der PR ändert KEINEN systemd-Service-State. Nur Templates ins Repo.

- [ ] **Step 1: Create orchestrator template**

`deploy/security-orchestrator.service`:

```ini
[Unit]
Description=Security-Agent-Team Orchestrator (#290 P1, default OFF)
After=network-online.target

[Service]
Type=simple
WorkingDirectory=%h/shadowops-bot
Environment=PYTHONPATH=%h/shadowops-bot/src
Environment=SECURITY_TEAM_ENABLED=false
Environment=REDIS_URL=redis://127.0.0.1:6379/0
# SECURITY_ANALYST_DB_URL via EnvironmentFile (Secrets nicht ins Unit!)
EnvironmentFile=%h/.config/shadowops-security-team.env
ExecStart=%h/shadowops-bot/.venv/bin/python -m integrations.security_engine.team.orchestrator_main
Restart=on-failure
RestartSec=10
MemoryMax=512M

[Install]
WantedBy=default.target
```

- [ ] **Step 2: Create worker template**

`deploy/security-npm-audit-worker.service`:

```ini
[Unit]
Description=Security npm-audit Worker (#290 P1, default OFF)
After=network-online.target

[Service]
Type=simple
WorkingDirectory=%h/shadowops-bot
Environment=PYTHONPATH=%h/shadowops-bot/src
Environment=REDIS_URL=redis://127.0.0.1:6379/0
EnvironmentFile=%h/.config/shadowops-security-team.env
ExecStart=%h/shadowops-bot/.venv/bin/python -m integrations.security_engine.team.workers.npm_audit_main
Restart=on-failure
RestartSec=10
MemoryMax=768M

[Install]
WantedBy=default.target
```

> Die `*_main`-Module sind dünne Entrypoints. Falls noch nicht vorhanden, in Step 3 anlegen.

- [ ] **Step 3: Create thin entrypoints**

`src/integrations/security_engine/team/workers/npm_audit_main.py`:

```python
"""systemd-Entrypoint: startet den npm-audit-Worker."""
from .npm_audit_worker import NpmAuditWorker
from ..runner import run_worker

if __name__ == "__main__":
    run_worker(NpmAuditWorker)
```

> Der Orchestrator-Entrypoint (`orchestrator_main.py`) wird hier als Stub mit
> TODO angelegt — der Trigger-Loop (Cron/Redis-Subscribe `sec:trigger`) ist
> bewusst P1-minimal; die Verdrahtung dazu kann eine Folge-Iteration übernehmen,
> da P1 manuell/per Test getriggert wird. Inhalt:

`src/integrations/security_engine/team/orchestrator_main.py`:

```python
"""systemd-Entrypoint-Stub fuer den Security-Orchestrator (P1).

P1 triggert manuell/per Test. Der dauerhafte sec:trigger-Subscribe-Loop wird
in einer Folge-Iteration ergaenzt (analog runner._amain). Bis dahin beendet
sich der Prozess sofort, wenn das Feature-Flag aus ist.
"""
import os

if __name__ == "__main__":
    if os.environ.get("SECURITY_TEAM_ENABLED", "false").lower() not in ("1", "true", "yes", "on"):
        print("security_team disabled — orchestrator exit")
        raise SystemExit(0)
    print("orchestrator P1-Stub: sec:trigger-Loop folgt in Folge-Iteration")
```

- [ ] **Step 4: Verify files exist + commit**

Run: `ls deploy/security-*.service && .venv/bin/python -c "import ast; [ast.parse(open(f).read()) for f in ['src/integrations/security_engine/team/workers/npm_audit_main.py','src/integrations/security_engine/team/orchestrator_main.py']]; print('OK')"`
Expected: beide `.service`-Dateien gelistet, `OK`

```bash
git add deploy/security-orchestrator.service deploy/security-npm-audit-worker.service src/integrations/security_engine/team/workers/npm_audit_main.py src/integrations/security_engine/team/orchestrator_main.py
git commit -m "feat(secops): systemd-Templates + Entrypoints (Install=Ops) (#290 P1)"
```

---

## Task 11: Doku + Gesamt-Verifikation

**Files:**
- Modify: `CLAUDE.md` (Modul-Tabelle `src/integrations/security_engine/`)

- [ ] **Step 1: CLAUDE.md ergänzen**

Im `security_engine/`-Eintrag der Modul-Beschreibung den Satz ergänzen:

```
- `team/` — Security-Agent-Team (P1, #290): `contracts.py` (SecurityJob/JobResult), `base_worker.py` (BaseSecurityWorker + Lifecycle/Isolation), `orchestrator.py` (Fan-out-Stub), `runner.py` (Redis-Subscribe-Entrypoint), `workers/npm_audit_worker.py`. Always-on systemd-Worker + Redis-Token-Cap (Cap-Enforcement erst P2). Feature-Flag `SECURITY_TEAM_ENABLED` default OFF, Monolith `scan_agent.py` bleibt Source-of-Truth. Spec: `docs/design/2026-06-02-security-agent-team-p1-spec.md`.
```

- [ ] **Step 2: Volle Test-Suite der neuen Dateien (einzeln, OOM-Regel)**

Run:
```bash
for t in test_store_finding test_store_finding_extraction test_sec_jobs_schema \
         test_security_contracts test_base_security_worker test_security_orchestrator \
         test_npm_audit_worker test_security_runner test_config_security_team; do
  .venv/bin/python -m pytest tests/unit/$t.py -x -q || break
done
```
Expected: alle PASS, kein `break`.

- [ ] **Step 3: Smoke — Flag OFF ändert nichts**

Run: `SECURITY_TEAM_ENABLED=false .venv/bin/python -c "import sys; sys.path.insert(0,'src'); from integrations.security_engine.team.orchestrator import SecurityOrchestrator; from integrations.security_engine.team.workers.npm_audit_worker import NpmAuditWorker; print('imports OK, worker_type=', NpmAuditWorker.worker_type)"`
Expected: `imports OK, worker_type= npm_audit`

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(secops): team/-Module in CLAUDE.md (#290 P1)"
```

- [ ] **Step 5: Branch pushen + PR**

```bash
git push -u origin feat/security-agent-team-p1
gh pr create --title "feat(secops): Security-Agent-Team P1 Foundation (#290)" \
  --body "Implementiert P1 aus docs/design/2026-06-02-security-agent-team-p1-spec.md. Feature-Flag SECURITY_TEAM_ENABLED default OFF, Monolith unangetastet. Siehe Plan docs/plans/2026-06-02-security-agent-team-p1.md."
```

---

## Self-Review (vom Autor durchlaufen)

**Spec-Abdeckung:**
- Job-Contract → Task 4 ✓ · BaseWorker → Task 5 ✓ · Orchestrator → Task 6 ✓ · npm-audit-worker → Task 7 ✓ · sec_jobs → Task 3 ✓ · store_finding → Task 1+2 ✓ · Feature-Flag/Config → Task 9 ✓ · systemd-Templates → Task 10 ✓ · Doku → Task 11 ✓ · Tests pro Komponente → in jeder Task ✓
- **Abweichung von Spec (bewusst, korrekter):** `sec_jobs` wird via `_ensure_schema()` angelegt (kein Migrations-Runner existiert); `002_sec_jobs.sql` bleibt als Doku-Parität. Spec wird entsprechend nachgezogen.
- **Bewusst nach P1 verschoben:** dauerhafter `sec:trigger`-Loop im Orchestrator-Entrypoint (P1 triggert manuell/per Test) — als Stub mit klarem TODO in Task 10.

**Typ-/Signatur-Konsistenz:**
- `store_finding(...)` keyword-only Signatur (Task 1) = Aufrufe in deep_scan/scan_agent (Task 2) + npm_audit_worker (Task 7) ✓
- `compute_finding_fingerprint(category, affected_project, affected_files, title)` positional (Task 7) = echte Signatur in `fingerprint.py` ✓
- `SecurityJob`/`JobResult`-Felder (Task 4) = Nutzung in base_worker/orchestrator/worker ✓
- Redis-Channels `sec:job:<type>:request|result` einheitlich in orchestrator + runner ✓

**Platzhalter-Scan:** keine TBD/TODO-ohne-Code; alle Code-Steps zeigen vollständigen Code; Klassennamen-Verifikations-Hinweise (deep_scan-Klasse, Config-Klasse) sind explizite `grep`-Anweisungen, keine offenen Lücken.
