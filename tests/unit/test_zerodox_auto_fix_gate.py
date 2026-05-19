"""Unit-Tests für zerodox_auto_fix_gate (Welle 16 P9c)."""
from datetime import date
from unittest.mock import MagicMock, patch, AsyncMock
import pytest

from src.integrations.zerodox_auto_fix_gate import (
    AutoFixGate,
    LOC_LIMIT,
    DAILY_MERGE_RATE_LIMIT,
    WHITELIST_FILES,
)


@pytest.fixture
def gate():
    """AutoFixGate mit enabled=true."""
    config = {"zerodox": {"auto_fix_pipeline": {"enabled": True}}}
    return AutoFixGate(config, discord_bot=MagicMock(), admin_user_id=123)


@pytest.fixture
def disabled_gate():
    """AutoFixGate mit enabled=false."""
    config = {"zerodox": {"auto_fix_pipeline": {"enabled": False}}}
    return AutoFixGate(config)


def test_disabled_gate_skips_everything(disabled_gate):
    assert not disabled_gate.enabled


def test_enabled_when_config_true(gate):
    assert gate.enabled


@pytest.mark.asyncio
async def test_loc_limit_escalates(gate):
    pr = {"number": 1, "additions": 600, "deletions": 500, "title": "Big PR"}
    with patch.object(gate, "get_pr_files", return_value=[]):
        safe, reason = gate.check_safety_constraints(pr)
    assert not safe
    assert "LOC" in reason
    assert str(LOC_LIMIT) in reason


@pytest.mark.asyncio
async def test_whitelist_escalates(gate):
    pr = {"number": 1, "additions": 10, "deletions": 5, "title": "Auth Fix"}
    with patch.object(
        gate, "get_pr_files",
        return_value=["web/src/lib/auth.ts", "web/tests/foo.test.ts"],
    ):
        safe, reason = gate.check_safety_constraints(pr)
    assert not safe
    assert "auth.ts" in reason


@pytest.mark.asyncio
async def test_all_whitelist_files_block(gate):
    for whitelist_file in WHITELIST_FILES:
        pr = {"number": 1, "additions": 10, "deletions": 5}
        with patch.object(gate, "get_pr_files", return_value=[whitelist_file]):
            safe, reason = gate.check_safety_constraints(pr)
        assert not safe, f"{whitelist_file} should block merge"
        assert whitelist_file.split("/")[-1] in reason


@pytest.mark.asyncio
async def test_rate_limit_escalates(gate):
    gate._merged_today[date.today().isoformat()] = DAILY_MERGE_RATE_LIMIT
    pr = {"number": 1, "additions": 10, "deletions": 5}
    with patch.object(gate, "get_pr_files", return_value=[]):
        safe, reason = gate.check_safety_constraints(pr)
    assert not safe
    assert "rate-limit" in reason.lower()


@pytest.mark.asyncio
async def test_failing_checks_escalate(gate):
    pr = {"number": 1, "additions": 10, "deletions": 5}
    with patch.object(gate, "get_pr_files", return_value=[]), \
         patch.object(gate, "get_pr_checks", return_value=[
             {"state": "FAILURE", "name": "E2E Shard 1/1"},
         ]):
        safe, reason = gate.check_safety_constraints(pr)
    assert not safe
    assert "Required Checks nicht grün" in reason
    assert "E2E Shard" in reason


@pytest.mark.asyncio
async def test_safe_pr_passes(gate):
    pr = {"number": 1, "additions": 50, "deletions": 30}
    with patch.object(gate, "get_pr_files",
                      return_value=["web/tests/api/foo.api.test.ts"]), \
         patch.object(gate, "get_pr_checks", return_value=[
             {"state": "SUCCESS", "name": "Quality Gate"},
             {"state": "SUCCESS", "name": "E2E Shard 1/1"},
         ]):
        safe, reason = gate.check_safety_constraints(pr)
    assert safe
    assert reason is None


@pytest.mark.asyncio
async def test_run_poll_cycle_disabled(disabled_gate):
    result = await disabled_gate.run_poll_cycle()
    assert result == {"enabled": False}


@pytest.mark.asyncio
async def test_run_poll_cycle_merges_safe_pr(gate):
    safe_pr = {"number": 100, "additions": 30, "deletions": 10, "title": "Safe Fix"}
    with patch.object(gate, "poll_eligible_prs", return_value=[safe_pr]), \
         patch.object(gate, "get_pr_files", return_value=["web/tests/foo.test.ts"]), \
         patch.object(gate, "get_pr_checks", return_value=[
             {"state": "SUCCESS", "name": "All"},
         ]), \
         patch.object(gate, "attempt_merge", return_value=True):
        stats = await gate.run_poll_cycle()
    assert stats["merged"] == 1
    assert stats["escalated"] == 0


@pytest.mark.asyncio
async def test_run_poll_cycle_escalates_unsafe(gate):
    unsafe_pr = {"number": 200, "additions": 600, "deletions": 500, "title": "Large"}
    gate.escalate = AsyncMock()
    with patch.object(gate, "poll_eligible_prs", return_value=[unsafe_pr]), \
         patch.object(gate, "get_pr_files", return_value=[]):
        stats = await gate.run_poll_cycle()
    assert stats["merged"] == 0
    assert stats["escalated"] == 1
    gate.escalate.assert_called_once()
