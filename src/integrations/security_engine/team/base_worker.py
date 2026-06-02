"""BaseSecurityWorker — Pattern für alle Security-Team-Worker.

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
