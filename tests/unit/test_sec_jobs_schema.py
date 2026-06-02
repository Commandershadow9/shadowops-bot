import inspect
from src.integrations.security_engine.db import SecurityDB


def test_ensure_schema_creates_sec_jobs():
    src = inspect.getsource(SecurityDB._ensure_schema)
    assert "CREATE TABLE IF NOT EXISTS sec_jobs" in src
    assert "job_id" in src and "worker_type" in src and "status" in src
