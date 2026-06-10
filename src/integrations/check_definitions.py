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
        if "action" not in d:
            raise ValueError(f"heal-Feld 'action' fehlt in {d!r}")
        try:
            action = HealAction(d["action"])
        except ValueError as e:
            raise ValueError(f"unbekannte heal-Aktion: {d.get('action')!r}") from e
        return cls(action=action, target=d.get("target"))


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
    headers: dict[str, Any] = field(default_factory=dict)  # HTTP-Header; Werte mit $VAR werden aus os.environ aufgelöst

    # Längen-Limits gegen versehentlich riesige config-Werte
    _MAX_ID = 100
    _MAX_TARGET = 500

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CheckDefinition":
        if not isinstance(d, dict):
            raise ValueError(f"Check muss ein dict sein, war {type(d).__name__}")
        for req in ("id", "type", "target", "interval"):
            if req not in d:
                raise ValueError(f"Check-Feld '{req}' fehlt in {d!r}")

        cid = str(d["id"])
        if not cid or len(cid) > cls._MAX_ID:
            raise ValueError(f"Check-id ungültig (1-{cls._MAX_ID} Zeichen): {cid!r}")

        try:
            ctype = CheckType(d["type"])
        except ValueError as e:
            raise ValueError(f"unbekannter Check-Typ: {d.get('type')!r}") from e

        target = str(d["target"])
        if not target or len(target) > cls._MAX_TARGET:
            raise ValueError(f"Check-target ungültig (1-{cls._MAX_TARGET} Zeichen): id={cid}")

        try:
            interval = int(d["interval"])
        except (ValueError, TypeError) as e:
            raise ValueError(f"Check-interval muss eine Zahl sein (id={cid}): {d['interval']!r}") from e
        if interval < 1:
            raise ValueError(f"Check-interval muss >= 1 sein (id={cid}): {interval}")

        try:
            timeout = int(d.get("timeout", 10))
        except (ValueError, TypeError) as e:
            raise ValueError(f"Check-timeout muss eine Zahl sein (id={cid})") from e
        if timeout < 1:
            raise ValueError(f"Check-timeout muss >= 1 sein (id={cid}): {timeout}")

        try:
            flake_polls = int(d.get("flake_polls", 1))
        except (ValueError, TypeError) as e:
            raise ValueError(f"Check-flake_polls muss eine Zahl sein (id={cid})") from e
        if flake_polls < 1:
            raise ValueError(f"Check-flake_polls muss >= 1 sein (id={cid}): {flake_polls}")

        return cls(
            id=cid,
            type=ctype,
            target=target,
            interval=interval,
            timeout=timeout,
            expect=d.get("expect", {}),
            heal=HealPolicy.from_dict(d.get("heal")),
            flake_polls=flake_polls,
            headers=d.get("headers", {}),
        )


@dataclass
class CheckResult:
    """Ergebnis eines einzelnen Check-Laufs."""

    check_id: str
    status: CheckStatus
    value: Optional[float] = None
    message: str = ""
