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
