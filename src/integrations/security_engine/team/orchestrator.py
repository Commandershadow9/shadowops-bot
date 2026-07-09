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
        trigger: str = "manual",
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
                    payload={"path": cfg[path_key]}, trigger=trigger,
                ))
        return jobs
