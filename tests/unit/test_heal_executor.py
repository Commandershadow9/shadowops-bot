"""Unit-Tests für den HealExecutor (Plan 1, Task 4).

Prüft die gestufte Policy: reversible Aktionen laufen autonom (mit
Circuit-Breaker), riskante nur nach Approval, alert-only macht nichts.
"""
import pytest
from unittest.mock import AsyncMock

from src.integrations.heal_executor import HealExecutor, HealOutcome
from src.integrations.check_definitions import HealPolicy, HealAction


@pytest.mark.asyncio
async def test_reversible_heal_runs_autonomously():
    runner = AsyncMock(return_value=0)  # Shell-Runner gibt Exit 0
    ex = HealExecutor(shell_runner=runner, approval_cb=AsyncMock(), max_per_hour=5)
    policy = HealPolicy(action=HealAction.RESTART_CONTAINER, target="zerodox-web")
    outcome = await ex.heal("zerodox", "web-liveness", policy)
    assert outcome is HealOutcome.HEALED
    runner.assert_awaited_once()


@pytest.mark.asyncio
async def test_approval_required_action_does_not_run_without_approval():
    runner = AsyncMock()
    approval = AsyncMock(return_value=False)  # abgelehnt
    ex = HealExecutor(shell_runner=runner, approval_cb=approval, max_per_hour=5)
    policy = HealPolicy(action=HealAction.DEPLOY, target="zerodox")
    outcome = await ex.heal("zerodox", "x", policy)
    assert outcome is HealOutcome.AWAITING_OR_DENIED
    runner.assert_not_awaited()


@pytest.mark.asyncio
async def test_circuit_breaker_blocks_after_max():
    runner = AsyncMock(return_value=0)
    ex = HealExecutor(shell_runner=runner, approval_cb=AsyncMock(), max_per_hour=2)
    policy = HealPolicy(action=HealAction.RESTART_CONTAINER, target="c")
    await ex.heal("p", "c1", policy)
    await ex.heal("p", "c1", policy)
    outcome = await ex.heal("p", "c1", policy)  # 3. → blockiert
    assert outcome is HealOutcome.CIRCUIT_OPEN
    assert runner.await_count == 2


@pytest.mark.asyncio
async def test_alert_only_never_runs_shell():
    runner = AsyncMock()
    ex = HealExecutor(shell_runner=runner, approval_cb=AsyncMock(), max_per_hour=5)
    outcome = await ex.heal("p", "c", HealPolicy(action=HealAction.ALERT_ONLY))
    assert outcome is HealOutcome.ALERT_ONLY
    runner.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_shell_returns_failed():
    runner = AsyncMock(return_value=1)  # Exit 1
    ex = HealExecutor(shell_runner=runner, approval_cb=AsyncMock(), max_per_hour=5)
    policy = HealPolicy(action=HealAction.RESTART_CONTAINER, target="c")
    outcome = await ex.heal("p", "c", policy)
    assert outcome is HealOutcome.FAILED
