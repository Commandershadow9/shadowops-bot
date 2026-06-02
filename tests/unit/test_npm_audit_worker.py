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
    db.pool.fetchval = AsyncMock(return_value=None)
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
    db.pool.fetchval = AsyncMock(return_value=1)
    w = NpmAuditWorker(db=db)
    job = SecurityJob(worker_type="npm_audit", project="guildscout",
                      payload={"path": "/tmp"})
    with patch.object(NpmAuditWorker, "_run_npm_audit",
                      new=AsyncMock(return_value=_AUDIT_JSON)), \
         patch("os.path.isdir", return_value=True):
        res = await w.process(job)
    assert res.findings_added == 0
    assert db.store_finding.await_count == 0
