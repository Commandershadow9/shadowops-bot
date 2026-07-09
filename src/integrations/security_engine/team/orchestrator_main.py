"""systemd-Entrypoint: Security-Orchestrator mit sec:trigger-Subscribe-Loop (W1).

Lauscht auf sec:trigger (Redis Pub/Sub) und faechert pro Trigger Jobs an die
aktiven Worker auf (SecurityOrchestrator.handle_trigger). Result-Aggregation
+ Token-Cap-Enforcement folgen in W2. Muster: team/runner.py::_amain.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal

import redis.asyncio as aioredis

try:
    from utils.config import Config
except ImportError:  # Tests importieren via src.-Praefix
    from src.utils.config import Config

from ..db import SecurityDB
from .orchestrator import SecurityOrchestrator

logger = logging.getLogger("security.orchestrator.main")

TRIGGER_CHANNEL = "sec:trigger"


async def handle_trigger_message(orchestrator, config, raw) -> list:
    """Verarbeitet EINE sec:trigger-Message. Ungueltige Payload → trigger='manual'."""
    trigger = "manual"
    try:
        payload = json.loads(raw) if raw else {}
        if isinstance(payload, dict):
            trigger = str(payload.get("trigger", "manual"))
    except (json.JSONDecodeError, TypeError):
        logger.warning("Ungueltige sec:trigger-Payload: %r", raw)
    jobs = await orchestrator.handle_trigger(
        projects=config.security_team_projects,
        active_workers=config.security_team_active_workers,
        trigger=trigger,
    )
    logger.info("sec:trigger (%s) → %d Jobs dispatched", trigger, len(jobs))
    return jobs


async def _amain() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    config = Config()
    if not config.security_team_enabled:
        logger.info("security_team disabled — orchestrator exit")
        return

    dsn = os.environ.get("SECURITY_ANALYST_DB_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("SECURITY_ANALYST_DB_URL oder DATABASE_URL muss gesetzt sein")
    redis_url = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")

    db = SecurityDB(dsn)
    await db.initialize()
    rconn = aioredis.from_url(redis_url, decode_responses=True)
    orchestrator = SecurityOrchestrator(redis=rconn, db=db)

    pubsub = rconn.pubsub()
    await pubsub.subscribe(TRIGGER_CHANNEL)
    logger.info("Orchestrator bereit — subscribed=%s", TRIGGER_CHANNEL)

    stop = asyncio.Event()

    def _graceful(*_):
        logger.info("SIGTERM — fahre Orchestrator herunter")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _graceful)

    try:
        while not stop.is_set():
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message is None or message.get("type") != "message":
                continue
            try:
                await handle_trigger_message(orchestrator, config, message.get("data"))
            except Exception:
                # Ein kaputter Trigger darf den Loop nie beenden.
                logger.exception("Trigger-Verarbeitung fehlgeschlagen")
    finally:
        await pubsub.unsubscribe(TRIGGER_CHANNEL)
        await rconn.aclose()
        await db.close()


if __name__ == "__main__":
    asyncio.run(_amain())
