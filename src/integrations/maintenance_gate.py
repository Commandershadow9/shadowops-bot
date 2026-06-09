"""Wartungs-Schalter: pausiert Auto-Heal global oder pro Projekt für eine
befristete Dauer. Checks laufen weiter, nur die Heilung wird unterdrückt.

Löst den Cut-over-Auto-Heal-Vorfall (2026-06-07): statt zwei Systeme manuell
zu pausieren, ein Schalter im einen System. Vor jedem Deploy/Wartung: Gate an.
"""
from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class _Window:
    until: float   # time.monotonic()-Zeitpunkt, ab dem die Wartung abgelaufen ist
    reason: str


class MaintenanceGate:
    GLOBAL = "global"

    def __init__(self):
        self._windows: dict[str, _Window] = {}

    def enable(self, scope: str, minutes: int, reason: str) -> None:
        """Aktiviert ein Wartungs-Fenster für ``scope`` (Projektname oder
        ``"global"``) für ``minutes`` Minuten."""
        self._windows[scope] = _Window(
            until=time.monotonic() + minutes * 60, reason=reason
        )

    def disable(self, scope: str) -> None:
        self._windows.pop(scope, None)

    def _active(self, scope: str) -> bool:
        w = self._windows.get(scope)
        if w is None:
            return False
        if time.monotonic() >= w.until:
            self._windows.pop(scope, None)  # abgelaufen → aufräumen
            return False
        return True

    def is_suppressed(self, project: str) -> bool:
        """True, wenn Auto-Heal für ``project`` aktuell unterdrückt ist
        (global oder projektspezifisch)."""
        return self._active(self.GLOBAL) or self._active(project)


def apply_maintenance_command(
    gate: "MaintenanceGate",
    scope: str,
    state: str,
    minutes: int = 60,
    reason: str = "",
) -> str:
    """Pure Command-Logik für /maintenance: wendet on/off auf das Gate an und
    gibt einen Bestätigungstext zurück. Vom Discord-Command + Tests genutzt."""
    state = state.strip().lower()
    if state == "on":
        gate.enable(scope, minutes=minutes, reason=reason or "manuell")
        return (
            f"🔧 Wartung **AKTIV** für `{scope}` ({minutes} Min) — Auto-Heal "
            f"pausiert. Grund: {reason or 'manuell'}"
        )
    if state == "off":
        gate.disable(scope)
        return f"✅ Wartung **BEENDET** für `{scope}` — Auto-Heal wieder aktiv."
    raise ValueError("state muss 'on' oder 'off' sein")
