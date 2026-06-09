"""Unit-Tests für das deklarative Check-Schema (Plan 1, Task 1)."""
import pytest

from src.integrations.check_definitions import (
    CheckType,
    HealAction,
    CheckDefinition,
    HealPolicy,
    CheckResult,
    CheckStatus,
)


def test_check_definition_from_dict_minimal():
    spec = {"id": "web-liveness", "type": "http", "target": "/api/health", "interval": 300}
    cd = CheckDefinition.from_dict(spec)
    assert cd.id == "web-liveness"
    assert cd.type is CheckType.HTTP
    assert cd.interval == 300
    # Ohne heal-Angabe → alert-only (macht nichts autonom)
    assert cd.heal.action is HealAction.ALERT_ONLY


def test_check_definition_with_reversible_heal():
    spec = {
        "id": "web",
        "type": "http",
        "target": "/h",
        "interval": 60,
        "heal": {"action": "restart-container", "target": "zerodox-web"},
    }
    cd = CheckDefinition.from_dict(spec)
    assert cd.heal.action is HealAction.RESTART_CONTAINER
    assert cd.heal.target == "zerodox-web"
    # restart-container = reversibel → autonom erlaubt
    assert cd.heal.is_reversible is True


def test_heal_action_reversibility_classification():
    assert HealAction.RESTART_CONTAINER.is_reversible is True
    assert HealAction.NETWORK_RECONNECT.is_reversible is True
    assert HealAction.RESTART_SERVICE.is_reversible is True
    assert HealAction.DISK_PRUNE.is_reversible is True
    # riskante / passive Aktionen sind nicht "reversibel autonom"
    assert HealAction.DEPLOY.is_reversible is False
    assert HealAction.CODE_FIX.is_reversible is False
    assert HealAction.ALERT_ONLY.is_reversible is False


def test_unknown_check_type_raises():
    with pytest.raises(ValueError, match="unbekannter Check-Typ"):
        CheckDefinition.from_dict(
            {"id": "x", "type": "telepathy", "target": "/", "interval": 60}
        )


def test_check_result_defaults():
    r = CheckResult(check_id="web", status=CheckStatus.OK)
    assert r.value is None
    assert r.message == ""
