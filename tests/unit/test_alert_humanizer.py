"""Unit-Tests fuer utils.alert_humanizer.

Reine Funktionen — keine Discord-, Netzwerk- oder DB-Abhaengigkeit.
Schwerpunkte (siehe docs/2026-05-28-alert-humanizer-design.md):
  - Status-Uebergangs-Matrix (Teilmenge der 4x4)
  - Bekannte Alert-Codes mit echten Prod-message-Strings
  - Fallback-Pfade: unbekannter Code / unparsebare Metrik -> Rohwert bleibt
  - Dezimal-Lokalisierung (32.23 -> "32,2")
"""

from __future__ import annotations

import pytest

from integrations.health_schema_v1 import HealthAlert
from utils.alert_humanizer import (
    RUNBOOKS,
    STATUS_COLOR,
    STATUS_EMOJI,
    TransitionInfo,
    Urgency,
    humanize_alert,
    humanize_transition,
    parse_disk,
    parse_load,
    runbook_for,
    urgency_line,
)


# ---------- humanize_transition ----------

def test_transition_unreachable_to_critical() -> None:
    info = humanize_transition("unreachable", "critical")
    assert isinstance(info, TransitionInfo)
    assert info.headline == "überlastet (war kurz nicht erreichbar)"
    assert info.urgency == Urgency.HIGH
    assert info.is_recovery is False
    assert info.emoji == STATUS_EMOJI["critical"]


def test_transition_degraded_to_ok_is_recovery() -> None:
    info = humanize_transition("degraded", "ok")
    assert info.headline == "wieder stabil"
    assert info.urgency == Urgency.NONE
    assert info.is_recovery is True
    assert info.emoji == STATUS_EMOJI["ok"]


def test_transition_ok_to_unreachable_is_critical() -> None:
    info = humanize_transition("ok", "unreachable")
    assert info.headline == "nicht mehr erreichbar"
    assert info.urgency == Urgency.CRITICAL
    assert info.is_recovery is False


def test_transition_critical_to_degraded_recovering() -> None:
    info = humanize_transition("critical", "degraded")
    assert info.urgency == Urgency.MEDIUM
    assert info.is_recovery is False
    assert "erholt" in info.headline.lower() or "stabil" in info.headline.lower()


def test_transition_ok_to_critical_high_or_critical() -> None:
    info = humanize_transition("ok", "critical")
    assert info.urgency in (Urgency.HIGH, Urgency.CRITICAL)
    assert info.is_recovery is False
    assert info.headline  # nicht leer


def test_transition_critical_to_ok_full_recovery() -> None:
    info = humanize_transition("critical", "ok")
    assert info.is_recovery is True
    assert info.urgency == Urgency.NONE


def test_transition_ok_to_degraded() -> None:
    info = humanize_transition("ok", "degraded")
    assert info.is_recovery is False
    assert info.headline
    assert info.urgency in (Urgency.LOW, Urgency.MEDIUM)


def test_transition_same_status_no_change() -> None:
    info = humanize_transition("ok", "ok")
    assert info.is_recovery is False
    assert info.headline


def test_transition_unknown_combo_has_sane_default() -> None:
    info = humanize_transition("foo", "bar")
    assert isinstance(info, TransitionInfo)
    assert info.headline  # nie leer
    assert isinstance(info.urgency, Urgency)
    assert isinstance(info.emoji, str) and info.emoji


def test_transition_emoji_matches_status_map() -> None:
    info = humanize_transition("ok", "degraded")
    assert info.emoji == STATUS_EMOJI["degraded"]


# ---------- humanize_alert: bekannte Codes ----------

def _alert(code: str, message: str, component: str = "x", severity: str = "warning") -> HealthAlert:
    return HealthAlert(code=code, severity=severity, component=component, message=message)


def test_humanize_alert_load_critical() -> None:
    line = humanize_alert(_alert("LOAD_CRITICAL", "Load 1min=32.23 on 8 CPUs", "load", "critical"))
    assert "CPU-Last" in line
    assert "32,2" in line  # Dezimalkomma
    assert "8 Kernen" in line
    assert "überlastet" in line
    assert "32.2" not in line  # kein Punkt


def test_humanize_alert_disk_high() -> None:
    line = humanize_alert(_alert("DISK_HIGH", "Disk usage 84.8% on /", "disk"))
    assert "Platte" in line
    assert "84,8" in line
    assert "/" in line
    assert "84.8" not in line


def test_humanize_alert_disk_critical() -> None:
    line = humanize_alert(_alert("DISK_CRITICAL", "Disk usage 95.0% on /var", "disk", "critical"))
    assert "Platte" in line
    assert "95,0" in line
    assert "/var" in line


def test_humanize_alert_memory_high() -> None:
    line = humanize_alert(_alert("MEMORY_HIGH", "Memory usage 87%", "memory"))
    assert "Arbeitsspeicher" in line
    assert "87" in line


def test_humanize_alert_mem_short_code() -> None:
    line = humanize_alert(_alert("MEM_CRITICAL", "Memory usage 96%", "memory", "critical"))
    assert "Arbeitsspeicher" in line
    assert "96" in line


def test_humanize_alert_service_down() -> None:
    line = humanize_alert(_alert("SERVICE_DOWN", "github-runner-1 inactive", "github_runners", "critical"))
    assert "Dienst" in line
    assert "läuft nicht" in line
    # Service-Name aus der Message bleibt erhalten
    assert "github-runner-1" in line


def test_humanize_alert_service_failed() -> None:
    line = humanize_alert(_alert("SERVICE_FAILED", "wg-quick@wg0 failed", "wireguard", "critical"))
    assert "Dienst" in line
    assert "wg-quick@wg0" in line


# ---------- humanize_alert: Fallback ----------

def test_humanize_alert_unknown_code_keeps_info() -> None:
    line = humanize_alert(_alert("WEIRD_NEW_CODE", "etwas Ungewoehnliches passiert", "foo"))
    assert "etwas Ungewoehnliches passiert" in line  # message nie verloren
    assert line  # nie leer
    # Title-Case-Variante des Codes
    assert "Weird New Code" in line


def test_humanize_alert_unknown_code_empty_message() -> None:
    line = humanize_alert(_alert("MYSTERY_CODE", "", "foo"))
    assert line.strip()  # nie leer, auch ohne message
    assert "Mystery Code" in line


def test_humanize_alert_known_code_unparsable_metric_falls_back() -> None:
    # LOAD_CRITICAL, aber Message passt nicht zum Regex -> Rohmessage bleibt
    line = humanize_alert(_alert("LOAD_CRITICAL", "irgendwas ohne Zahlen", "load", "critical"))
    assert "irgendwas ohne Zahlen" in line
    assert line


def test_humanize_alert_disk_unparsable_falls_back() -> None:
    line = humanize_alert(_alert("DISK_HIGH", "kein Prozentwert hier", "disk"))
    assert "kein Prozentwert hier" in line
    assert line


def test_humanize_alert_duck_typed_object() -> None:
    class Fake:
        code = "LOAD_HIGH"
        component = "load"
        message = "Load 1min=12.0 on 8 CPUs"

    line = humanize_alert(Fake())
    assert "CPU-Last" in line
    assert "12,0" in line


# ---------- parse_load ----------

def test_parse_load_overloaded() -> None:
    out = parse_load("Load 1min=32.23 on 8 CPUs")
    assert out is not None
    assert "32,2" in out
    assert "8 Kernen" in out
    assert "überlastet" in out
    # 32.23 / 8 = 4.03 -> 4x
    assert "4" in out


def test_parse_load_normal_ratio() -> None:
    out = parse_load("Load 1min=2.0 on 8 CPUs")
    assert out is not None
    assert "2,0" in out
    assert "8 Kernen" in out
    # Faktor 0.25 -> nicht "ueberlastet"
    assert "überlastet" not in out


def test_parse_load_no_match_returns_none() -> None:
    assert parse_load("kein Load hier") is None
    assert parse_load("") is None


def test_parse_load_decimal_localization() -> None:
    out = parse_load("Load 1min=1.50 on 4 CPUs")
    assert out is not None
    assert "1,5" in out
    assert "1.5" not in out


# ---------- parse_disk ----------

def test_parse_disk_basic() -> None:
    out = parse_disk("Disk usage 84.8% on /")
    assert out is not None
    assert "84,8" in out
    assert "voll" in out
    assert "/" in out


def test_parse_disk_other_mount() -> None:
    out = parse_disk("Disk usage 91.2% on /var/lib/docker")
    assert out is not None
    assert "91,2" in out
    assert "/var/lib/docker" in out


def test_parse_disk_no_match_returns_none() -> None:
    assert parse_disk("nichts brauchbares") is None
    assert parse_disk("") is None


# ---------- urgency_line ----------

@pytest.mark.parametrize("urgency", list(Urgency))
def test_urgency_line_never_empty(urgency: Urgency) -> None:
    line = urgency_line(urgency)
    assert isinstance(line, str)
    if urgency == Urgency.NONE:
        # NONE darf leer sein (kein Handlungsbedarf)
        assert line == "" or line
    else:
        assert line.strip()


def test_urgency_line_high_german_label() -> None:
    line = urgency_line(Urgency.HIGH)
    assert "hoch" in line.lower()


def test_urgency_line_critical_german_label() -> None:
    line = urgency_line(Urgency.CRITICAL)
    assert "kritisch" in line.lower() or "sofort" in line.lower()


# ---------- runbook_for ----------

def test_runbook_for_ci_runner() -> None:
    rb = runbook_for("ci-runner", [])
    assert rb == RUNBOOKS["ci-runner"]
    assert "mayday-ci-runner" in rb


def test_runbook_for_unknown_role_returns_none() -> None:
    assert runbook_for("nonexistent-role", []) is None


def test_runbook_for_component_fallback() -> None:
    # Wenn Rolle unbekannt, aber Komponente bekannt -> Runbook der Komponente
    rb = runbook_for("unknown", ["disk"])
    # Entweder None oder ein bekanntes Runbook, aber kein Crash
    assert rb is None or isinstance(rb, str)


# ---------- Konstanten-Konsistenz ----------

def test_status_maps_cover_all_statuses() -> None:
    for status in ("ok", "degraded", "critical", "unreachable"):
        assert status in STATUS_EMOJI
        assert status in STATUS_COLOR
