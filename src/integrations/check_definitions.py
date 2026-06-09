"""Deklaratives Check-Schema für die zentrale Monitoring-Engine.

Ein Check ist ein YAML-Eintrag unter ``projects.<name>.monitor.checks``. Dieses
Modul mappt solche Einträge auf typsichere dataclasses und definiert, welche
Heal-Aktionen reversibel (autonom erlaubt) bzw. genehmigungspflichtig sind.

Spiegelt die server-safety/autonomy-Regeln: reversible Aktionen darf die Engine
selbst ausführen, riskante nur nach Discord-Approval.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class CheckType(str, Enum):
    """Typ eines Checks — bestimmt, wie der CheckRunner ihn ausführt."""

    HTTP = "http"            # HTTP-Status (+ optionale JSON-Assertion)
    SCRIPT = "script"        # externes Skript, Exit-Code/Marker → synthetic
    RESOURCE = "resource"    # disk/mem/netz
    CONTAINER = "container"  # Container up / Netz-Anbindung


class HealAction(str, Enum):
    """Heilungs-Aktion eines Checks. ``is_reversible`` entscheidet, ob die
    Engine sie autonom ausführen darf oder ein Approval braucht."""

    ALERT_ONLY = "alert-only"
    RESTART_CONTAINER = "restart-container"   # reversibel
    RESTART_SERVICE = "restart-service"       # reversibel
    NETWORK_RECONNECT = "network-reconnect"   # reversibel
    DISK_PRUNE = "disk-prune"                 # reversibel
    DEPLOY = "deploy"                          # genehmigungspflichtig
    CODE_FIX = "code-fix"                      # genehmigungspflichtig

    @property
    def is_reversible(self) -> bool:
        """True für reversible Aktionen, die autonom (mit Circuit-Breaker)
        ausgeführt werden dürfen. ``alert-only`` ist NICHT reversibel im Sinne
        von "autonom heilen" — es macht schlicht nichts."""
        return self in {
            HealAction.RESTART_CONTAINER,
            HealAction.RESTART_SERVICE,
            HealAction.NETWORK_RECONNECT,
            HealAction.DISK_PRUNE,
        }


class CheckStatus(str, Enum):
    OK = "ok"
    FAIL = "fail"      # Ziel ungesund
    ERROR = "error"    # Check selbst kaputt (nicht das Ziel)


@dataclass
class HealPolicy:
    """Was bei einem fehlgeschlagenen Check zu tun ist."""

    action: HealAction = HealAction.ALERT_ONLY
    target: Optional[str] = None  # Container-/Service-Name etc.

    @property
    def is_reversible(self) -> bool:
        return self.action.is_reversible

    @classmethod
    def from_dict(cls, d: Optional[dict[str, Any]]) -> "HealPolicy":
        if not d:
            return cls()
        return cls(action=HealAction(d["action"]), target=d.get("target"))


@dataclass
class CheckDefinition:
    """Ein deklarativer Check aus der config.yaml."""

    id: str
    type: CheckType
    target: str           # URL-Pfad / Skript-Pfad / Metrik-Key / Container-Name
    interval: int         # Sekunden zwischen zwei Läufen
    timeout: int = 10
    expect: dict[str, Any] = field(default_factory=dict)  # z.B. {"status": 200}
    heal: HealPolicy = field(default_factory=HealPolicy)
    flake_polls: int = 1  # konsekutive Fehl-Polls vor Alert (Flake-Filter)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CheckDefinition":
        try:
            ctype = CheckType(d["type"])
        except ValueError as e:
            raise ValueError(f"unbekannter Check-Typ: {d.get('type')!r}") from e
        return cls(
            id=d["id"],
            type=ctype,
            target=d["target"],
            interval=int(d["interval"]),
            timeout=int(d.get("timeout", 10)),
            expect=d.get("expect", {}),
            heal=HealPolicy.from_dict(d.get("heal")),
            flake_polls=int(d.get("flake_polls", 1)),
        )


@dataclass
class CheckResult:
    """Ergebnis eines einzelnen Check-Laufs."""

    check_id: str
    status: CheckStatus
    value: Optional[float] = None
    message: str = ""
