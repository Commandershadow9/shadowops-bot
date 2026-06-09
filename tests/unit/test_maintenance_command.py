"""Unit-Tests für die /maintenance-Command-Logik (Plan 1, Task 7)."""
import pytest

from src.integrations.maintenance_gate import MaintenanceGate, apply_maintenance_command


def test_apply_on_enables_gate():
    g = MaintenanceGate()
    msg = apply_maintenance_command(g, "zerodox", "on", minutes=30, reason="Deploy")
    assert g.is_suppressed("zerodox") is True
    assert "AKTIV" in msg


def test_apply_off_disables_gate():
    g = MaintenanceGate()
    g.enable("zerodox", minutes=30, reason="x")
    msg = apply_maintenance_command(g, "zerodox", "off")
    assert g.is_suppressed("zerodox") is False
    assert "BEENDET" in msg


def test_apply_global_scope():
    g = MaintenanceGate()
    apply_maintenance_command(g, "global", "on", minutes=15, reason="Wartung")
    assert g.is_suppressed("mayday") is True


def test_apply_invalid_state_raises():
    with pytest.raises(ValueError, match="on.*off"):
        apply_maintenance_command(MaintenanceGate(), "zerodox", "maybe")
