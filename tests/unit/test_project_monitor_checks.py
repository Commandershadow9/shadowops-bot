"""Integrationstests: deklarative Checks im ProjectMonitor (Plan 1, Task 6 + Review-Fixes).

Deckt ab: Heal bei FAIL, Discord-Alert bei jedem Nicht-OK-Outcome,
Maintenance-Gate, Flake-Filter, ERROR-Handling (Alert ohne Heal).
"""
import pytest
from unittest.mock import AsyncMock, Mock, MagicMock

from src.integrations.project_monitor import ProjectMonitor
from src.integrations.check_definitions import CheckStatus, CheckResult
from src.integrations.heal_executor import HealOutcome


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
    mon = ProjectMonitor(bot=Mock(), config=config)
    mon._send_health_alert = AsyncMock()  # kein echter Discord-Versand im Test
    return mon


def _check(flake_polls=1):
    return [{
        "id": "web", "type": "http", "target": "/h", "interval": 60,
        "heal": {"action": "restart-container", "target": "zerodox-web"},
        "flake_polls": flake_polls,
    }]


@pytest.mark.asyncio
async def test_declarative_checks_loaded_into_project():
    mon = _make_monitor(_check())
    assert len(mon.projects["zerodox"].checks) == 1
    assert mon.projects["zerodox"].checks[0].id == "web"


@pytest.mark.asyncio
async def test_fail_triggers_heal_and_alert():
    mon = _make_monitor(_check())
    mon._check_runner.run = AsyncMock(return_value=CheckResult("web", CheckStatus.FAIL, "503"))
    mon._heal_executor.heal = AsyncMock(return_value=HealOutcome.HEALED)
    await mon._run_declarative_checks(mon.projects["zerodox"])
    mon._heal_executor.heal.assert_awaited_once()
    mon._send_health_alert.assert_awaited()  # auch bei HEALED wird informiert


@pytest.mark.asyncio
async def test_failed_heal_sends_critical_alert():
    mon = _make_monitor(_check())
    mon._check_runner.run = AsyncMock(return_value=CheckResult("web", CheckStatus.FAIL))
    mon._heal_executor.heal = AsyncMock(return_value=HealOutcome.FAILED)
    await mon._run_declarative_checks(mon.projects["zerodox"])
    mon._send_health_alert.assert_awaited()
    # Severity CRITICAL bei fehlgeschlagenem Heal (Name-Vergleich — robust gegen
    # den try/except-Doppelimport von Severity in project_monitor.py)
    _, kwargs = mon._send_health_alert.call_args
    assert kwargs["severity"].name == "CRITICAL"


@pytest.mark.asyncio
async def test_maintenance_gate_suppresses_heal_but_alerts():
    mon = _make_monitor(_check())
    mon._maintenance_gate.enable("zerodox", minutes=30, reason="Test")
    mon._check_runner.run = AsyncMock(return_value=CheckResult("web", CheckStatus.FAIL))
    mon._heal_executor.heal = AsyncMock()
    await mon._run_declarative_checks(mon.projects["zerodox"])
    mon._heal_executor.heal.assert_not_awaited()  # Gate aktiv → kein Heal
    mon._send_health_alert.assert_awaited()        # aber Operator wird informiert


@pytest.mark.asyncio
async def test_error_status_alerts_without_heal():
    mon = _make_monitor(_check())
    mon._check_runner.run = AsyncMock(return_value=CheckResult("web", CheckStatus.ERROR, "Plan 2"))
    mon._heal_executor.heal = AsyncMock()
    await mon._run_declarative_checks(mon.projects["zerodox"])
    mon._heal_executor.heal.assert_not_awaited()  # ERROR = Check kaputt → kein Heal
    mon._send_health_alert.assert_awaited()


@pytest.mark.asyncio
async def test_ok_check_no_heal_no_alert():
    mon = _make_monitor(_check())
    mon._check_runner.run = AsyncMock(return_value=CheckResult("web", CheckStatus.OK))
    mon._heal_executor.heal = AsyncMock()
    await mon._run_declarative_checks(mon.projects["zerodox"])
    mon._heal_executor.heal.assert_not_awaited()
    mon._send_health_alert.assert_not_awaited()


@pytest.mark.asyncio
async def test_flake_filter_delays_heal_until_threshold():
    mon = _make_monitor(_check(flake_polls=2))
    mon._check_runner.run = AsyncMock(return_value=CheckResult("web", CheckStatus.FAIL))
    mon._heal_executor.heal = AsyncMock(return_value=HealOutcome.HEALED)
    # 1. FAIL: fails=1 < 2 → noch kein Heal/Alert
    await mon._run_declarative_checks(mon.projects["zerodox"])
    mon._heal_executor.heal.assert_not_awaited()
    mon._send_health_alert.assert_not_awaited()
    # Min-Intervall zurücksetzen, damit der Check erneut läuft
    mon._health_check_last_run.clear()
    # 2. FAIL: fails=2 >= 2 → jetzt Heal + Alert
    await mon._run_declarative_checks(mon.projects["zerodox"])
    mon._heal_executor.heal.assert_awaited_once()
    mon._send_health_alert.assert_awaited()
