import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timedelta, timezone

from integrations.security_engine.scan_agent import SecurityScanAgent


@pytest.mark.asyncio
async def test_idempotency_blocks_repeat_fix_within_24h():
    agent = SecurityScanAgent.__new__(SecurityScanAgent)
    agent.db = MagicMock()
    recent = datetime.now(timezone.utc) - timedelta(hours=3)
    agent.db.pool = MagicMock()
    agent.db.pool.fetchval = AsyncMock(return_value=recent)

    blocked = await agent._was_fix_attempted_recently(
        fingerprint="abc123" * 6 + "abcd", fix_type="apt_upgrade", cooldown_hours=24
    )
    assert blocked is True


@pytest.mark.asyncio
async def test_idempotency_allows_after_cooldown():
    agent = SecurityScanAgent.__new__(SecurityScanAgent)
    agent.db = MagicMock()
    old = datetime.now(timezone.utc) - timedelta(hours=48)
    agent.db.pool = MagicMock()
    agent.db.pool.fetchval = AsyncMock(return_value=old)

    blocked = await agent._was_fix_attempted_recently(
        fingerprint="x" * 40, fix_type="apt_upgrade", cooldown_hours=24
    )
    assert blocked is False


@pytest.mark.asyncio
async def test_idempotency_no_previous_attempt():
    agent = SecurityScanAgent.__new__(SecurityScanAgent)
    agent.db = MagicMock()
    agent.db.pool = MagicMock()
    agent.db.pool.fetchval = AsyncMock(return_value=None)

    blocked = await agent._was_fix_attempted_recently("x" * 40, "apt_upgrade")
    assert blocked is False
