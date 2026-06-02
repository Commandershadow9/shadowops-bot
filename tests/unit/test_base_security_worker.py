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
    assert db.pool.execute.await_count == 2


@pytest.mark.asyncio
async def test_exception_returns_failed_no_reraise():
    db = _db()
    w = _BoomWorker(db=db)
    job = SecurityJob(worker_type="test_boom", project="zerodox")
    res = await w.handle_request(job)
    assert res.status == JobStatus.FAILED
    assert any("explode" in e for e in res.errors)


@pytest.mark.asyncio
async def test_db_persist_error_does_not_crash():
    db = _db()
    db.pool.execute = AsyncMock(side_effect=RuntimeError("db down"))
    w = _OkWorker(db=db)
    job = SecurityJob(worker_type="test_ok", project="guildscout")
    res = await w.handle_request(job)
    assert res.status == JobStatus.OK
