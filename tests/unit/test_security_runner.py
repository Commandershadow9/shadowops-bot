from src.integrations.security_engine.team.runner import worker_channels
from src.integrations.security_engine.team.workers.npm_audit_worker import NpmAuditWorker


def test_worker_channels():
    req, res = worker_channels(NpmAuditWorker.worker_type)
    assert req == "sec:job:npm_audit:request"
    assert res == "sec:job:npm_audit:result"
