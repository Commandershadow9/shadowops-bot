import pytest
from unittest.mock import AsyncMock, MagicMock
from integrations.security_engine.learning_bridge import LearningBridge


@pytest.mark.asyncio
async def test_record_dedup_decision_writes_to_agent_feedback():
    lb = LearningBridge.__new__(LearningBridge)
    lb.pool = MagicMock()
    lb.pool.execute = AsyncMock()

    await lb.record_dedup_decision(parent_id=123, new_title="neuer titel", project="zerodox")

    lb.pool.execute.assert_awaited_once()
    args = lb.pool.execute.call_args[0]
    assert "agent_feedback" in args[0]
    # reference_id ist die parent_id als string
    assert "123" in args


@pytest.mark.asyncio
async def test_record_manual_merge_writes_feedback():
    lb = LearningBridge.__new__(LearningBridge)
    lb.pool = MagicMock()
    lb.pool.execute = AsyncMock()

    await lb.record_manual_merge(
        parent_id=100, child_id=101, user_id=42, user_name="christian", project="zerodox"
    )

    lb.pool.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_dedup_noop_when_disconnected():
    lb = LearningBridge.__new__(LearningBridge)
    lb.pool = None  # nicht verbunden
    # darf nicht werfen
    await lb.record_dedup_decision(parent_id=1, new_title="x", project="y")
    await lb.record_manual_merge(1, 2, 3, "u", "p")
