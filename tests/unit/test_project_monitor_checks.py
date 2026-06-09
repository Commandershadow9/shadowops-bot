"""Integrationstests: deklarative Checks im ProjectMonitor (Plan 1, Task 6)."""
import pytest
from unittest.mock import AsyncMock, Mock, MagicMock

from src.integrations.project_monitor import ProjectMonitor
from src.integrations.check_definitions import CheckStatus, CheckResult


def _make_monitor(checks):
    config = MagicMock()
    config.projects = {
        "zerodox": {
            "enabled": True,
            "monitor": {"enabled": True, "url": "http://x/h", "checks": checks},
        }
    }
    config.customer_status_channel = 12345
    config.customer_alerts_channel = 12345
    return ProjectMonitor(bot=Mock(), config=config)


_CHECK = [{
    "id": "web", "type": "http", "target": "/h", "interval": 60,
    "heal": {"action": "restart-container", "target": "zerodox-web"},
}]


@pytest.mark.asyncio
async def test_declarative_checks_loaded_into_project():
    mon = _make_monitor(_CHECK)
    assert len(mon.projects["zerodox"].checks) == 1
    assert mon.projects["zerodox"].checks[0].id == "web"


@pytest.mark.asyncio
async def test_declarative_check_fail_triggers_heal_when_not_in_maintenance():
    mon = _make_monitor(_CHECK)
    mon._check_runner.run = AsyncMock(
        return_value=CheckResult("web", CheckStatus.FAIL, message="503")
    )
    mon._heal_executor.heal = AsyncMock()
    await mon._run_declarative_checks(mon.projects["zerodox"])
    mon._heal_executor.heal.assert_awaited_once()


@pytest.mark.asyncio
async def test_maintenance_gate_suppresses_heal():
    mon = _make_monitor(_CHECK)
    mon._maintenance_gate.enable("zerodox", minutes=30, reason="Test")
    mon._check_runner.run = AsyncMock(return_value=CheckResult("web", CheckStatus.FAIL))
    mon._heal_executor.heal = AsyncMock()
    await mon._run_declarative_checks(mon.projects["zerodox"])
    mon._heal_executor.heal.assert_not_awaited()  # Gate aktiv → kein Heal


@pytest.mark.asyncio
async def test_ok_check_no_heal():
    mon = _make_monitor(_CHECK)
    mon._check_runner.run = AsyncMock(return_value=CheckResult("web", CheckStatus.OK))
    mon._heal_executor.heal = AsyncMock()
    await mon._run_declarative_checks(mon.projects["zerodox"])
    mon._heal_executor.heal.assert_not_awaited()
