"""Unit-Tests für den HealExecutor (Plan 1, Task 4 + Review-Fixes).

Prüft die gestufte Policy (reversibel autonom / riskant per Approval /
alert-only), den Circuit-Breaker und die sichere argv-Erzeugung (kein Shell).
"""
import pytest
from unittest.mock import AsyncMock

from src.integrations.heal_executor import HealExecutor, HealOutcome, build_heal_argv
from src.integrations.check_definitions import HealPolicy, HealAction


# ── build_heal_argv: sichere argv-Listen (kein Shell-String) ────────────────

def test_build_argv_restart_container():
    argv = build_heal_argv(HealPolicy(action=HealAction.RESTART_CONTAINER, target="zerodox-web"))
    assert argv == ["docker", "restart", "zerodox-web"]


def test_build_argv_restart_service():
    argv = build_heal_argv(HealPolicy(action=HealAction.RESTART_SERVICE, target="seo-agent"))
    assert argv == ["systemctl", "--user", "restart", "seo-agent"]


def test_build_argv_network_reconnect():
    argv = build_heal_argv(HealPolicy(action=HealAction.NETWORK_RECONNECT, target="gs-net zerodox-web"))
    assert argv == ["docker", "network", "connect", "gs-net", "zerodox-web"]


def test_build_argv_disk_prune_needs_no_target():
    argv = build_heal_argv(HealPolicy(action=HealAction.DISK_PRUNE))
    assert argv[:3] == ["docker", "builder", "prune"]


def test_build_argv_missing_target_raises():
    with pytest.raises(ValueError, match="target"):
        build_heal_argv(HealPolicy(action=HealAction.RESTART_CONTAINER, target=None))


def test_build_argv_metacharacters_stay_one_arg():
    # Shell-Metazeichen im target bleiben EIN Argument (kein Injection via exec).
    argv = build_heal_argv(
        HealPolicy(action=HealAction.RESTART_CONTAINER, target="x; rm -rf /")
    )
    assert argv == ["docker", "restart", "x; rm -rf /"]


# ── heal(): gestufte Policy + Circuit-Breaker ───────────────────────────────

@pytest.mark.asyncio
async def test_reversible_heal_runs_autonomously():
    runner = AsyncMock(return_value=0)  # exec-Runner gibt Exit 0
    ex = HealExecutor(exec_runner=runner, approval_cb=AsyncMock(), max_per_hour=5)
    policy = HealPolicy(action=HealAction.RESTART_CONTAINER, target="zerodox-web")
    outcome = await ex.heal("zerodox", "web-liveness", policy)
    assert outcome is HealOutcome.HEALED
    runner.assert_awaited_once_with(["docker", "restart", "zerodox-web"])


@pytest.mark.asyncio
async def test_approval_required_action_does_not_run_without_approval():
    runner = AsyncMock()
    approval = AsyncMock(return_value=False)  # abgelehnt
    ex = HealExecutor(exec_runner=runner, approval_cb=approval, max_per_hour=5)
    policy = HealPolicy(action=HealAction.DEPLOY, target="zerodox")
    outcome = await ex.heal("zerodox", "x", policy)
    assert outcome is HealOutcome.AWAITING_OR_DENIED
    runner.assert_not_awaited()


@pytest.mark.asyncio
async def test_circuit_breaker_blocks_after_max():
    runner = AsyncMock(return_value=0)
    ex = HealExecutor(exec_runner=runner, approval_cb=AsyncMock(), max_per_hour=2)
    policy = HealPolicy(action=HealAction.RESTART_CONTAINER, target="c")
    await ex.heal("p", "c1", policy)
    await ex.heal("p", "c1", policy)
    outcome = await ex.heal("p", "c1", policy)  # 3. → blockiert
    assert outcome is HealOutcome.CIRCUIT_OPEN
    assert runner.await_count == 2


@pytest.mark.asyncio
async def test_alert_only_never_runs_exec():
    runner = AsyncMock()
    ex = HealExecutor(exec_runner=runner, approval_cb=AsyncMock(), max_per_hour=5)
    outcome = await ex.heal("p", "c", HealPolicy(action=HealAction.ALERT_ONLY))
    assert outcome is HealOutcome.ALERT_ONLY
    runner.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_exec_returns_failed():
    runner = AsyncMock(return_value=1)  # Exit 1
    ex = HealExecutor(exec_runner=runner, approval_cb=AsyncMock(), max_per_hour=5)
    policy = HealPolicy(action=HealAction.RESTART_CONTAINER, target="c")
    outcome = await ex.heal("p", "c", policy)
    assert outcome is HealOutcome.FAILED


@pytest.mark.asyncio
async def test_invalid_heal_config_returns_failed_without_running():
    runner = AsyncMock(return_value=0)
    ex = HealExecutor(exec_runner=runner, approval_cb=AsyncMock(), max_per_hour=5)
    # restart-container ohne target → ungültig → FAILED, exec NICHT aufgerufen
    policy = HealPolicy(action=HealAction.RESTART_CONTAINER, target=None)
    outcome = await ex.heal("p", "c", policy)
    assert outcome is HealOutcome.FAILED
    runner.assert_not_awaited()
