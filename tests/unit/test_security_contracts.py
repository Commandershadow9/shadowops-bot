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
