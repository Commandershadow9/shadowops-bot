"""Unit-Tests für das MaintenanceGate (Plan 1, Task 5)."""
from src.integrations.maintenance_gate import MaintenanceGate


def test_default_not_suppressed():
    g = MaintenanceGate()
    assert g.is_suppressed("zerodox") is False


def test_project_suppression():
    g = MaintenanceGate()
    g.enable("zerodox", minutes=30, reason="Deploy")
    assert g.is_suppressed("zerodox") is True
    assert g.is_suppressed("guildscout") is False


def test_global_suppression_covers_all():
    g = MaintenanceGate()
    g.enable("global", minutes=30, reason="Wartung")
    assert g.is_suppressed("zerodox") is True
    assert g.is_suppressed("mayday") is True


def test_disable_clears():
    g = MaintenanceGate()
    g.enable("zerodox", minutes=30, reason="x")
    g.disable("zerodox")
    assert g.is_suppressed("zerodox") is False


def test_expiry(monkeypatch):
    import src.integrations.maintenance_gate as m

    t = [1000.0]
    monkeypatch.setattr(m.time, "monotonic", lambda: t[0])
    g = MaintenanceGate()
    g.enable("zerodox", minutes=10, reason="x")
    assert g.is_suppressed("zerodox") is True
    t[0] = 1000.0 + 11 * 60  # 11 Minuten später → abgelaufen
    assert g.is_suppressed("zerodox") is False
