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
    channel = redis.publish.await_args.args[0]
    assert channel == "sec:job:npm_audit:request"
    assert db.pool.execute.await_count == 1


@pytest.mark.asyncio
async def test_handle_trigger_fans_out_per_project_with_path():
    redis, db = _redis(), _db()
    orch = SecurityOrchestrator(redis=redis, db=db)
    jobs = await orch.handle_trigger(
        projects={
            "guildscout": {"npm_audit_path": "/g/web"},
            "zerodox": {"npm_audit_path": "/z"},
            "nopath": {"something_else": "/n"},
        },
        active_workers=["npm_audit"],
    )
    assert len(jobs) == 2
    assert {j.project for j in jobs} == {"guildscout", "zerodox"}
    assert redis.publish.await_count == 2
