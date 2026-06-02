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
    """Gibt (request_channel, result_channel) fuer einen Worker-Typ zurueck."""
    return f"sec:job:{worker_type}:request", f"sec:job:{worker_type}:result"


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
