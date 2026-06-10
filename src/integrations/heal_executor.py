"""Gestufte Heal-Policy: reversible Aktionen autonom (mit Circuit-Breaker),
riskante nur nach Discord-Approval, alert-only macht nichts.

Spiegelt die server-safety/autonomy-Regeln: reversibel = einfach machen,
riskant = stop & fragen.

Sicherheit: Heal-Aktionen werden als **argv-Liste** an einen exec-Runner
(create_subprocess_exec) übergeben, NICHT als Shell-String. So gibt es keinen
Shell-Interpolations-/Injection-Pfad, selbst wenn ein target aus einer
versehentlich falschen config.yaml stammt.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from enum import Enum
from typing import Awaitable, Callable

from .check_definitions import HealPolicy, HealAction

# exec-Runner: bekommt eine argv-Liste (kein Shell), liefert Exit-Code.
ExecRunner = Callable[[list], Awaitable[int]]
ApprovalCb = Callable[[str, str, HealPolicy], Awaitable[bool]]  # projekt, check, policy -> approved?


class HealOutcome(str, Enum):
    HEALED = "healed"
    ALERT_ONLY = "alert-only"
    AWAITING_OR_DENIED = "awaiting-or-denied"
    CIRCUIT_OPEN = "circuit-open"
    FAILED = "failed"


def build_heal_argv(policy: HealPolicy) -> list:
    """Baut die argv-Liste für eine reversible Heal-Aktion. Wirft ValueError,
    wenn die Aktion kein Auto-Kommando hat oder ein nötiges target fehlt.

    Kein Shell — target wird als eigenes Argument übergeben (kein Injection)."""
    action = policy.action
    target = (policy.target or "").strip()
    if action is HealAction.RESTART_CONTAINER:
        if not target:
            raise ValueError("restart-container braucht ein target (Container-Name)")
        return ["docker", "restart", target]
    if action is HealAction.RESTART_SERVICE:
        if not target:
            raise ValueError("restart-service braucht ein target (Service-Name)")
        return ["systemctl", "--user", "restart", target]
    if action is HealAction.NETWORK_RECONNECT:
        parts = target.split()
        if len(parts) != 2:
            raise ValueError(
                "network-reconnect braucht target im Format 'netzwerk container'"
            )
        return ["docker", "network", "connect", parts[0], parts[1]]
    if action is HealAction.DISK_PRUNE:
        return ["docker", "builder", "prune", "-af"]
    raise ValueError(f"keine Auto-argv für Aktion {action}")


class HealExecutor:
    def __init__(
        self,
        exec_runner: ExecRunner,
        approval_cb: ApprovalCb,
        max_per_hour: int = 5,
    ):
        self._exec = exec_runner
        self._approval = approval_cb
        self._max = max_per_hour
        # "{projekt}:{check}" -> Zeitstempel der letzten Heilungs-VERSUCHE
        # (Circuit-Breaker-Fenster; jeder Versuch zählt, auch fehlgeschlagene —
        # so stoppt der Breaker auch dauerhaft erfolglose Restart-Loops).
        self._events: dict[str, deque] = defaultdict(deque)

    def _circuit_open(self, key: str) -> bool:
        now = time.monotonic()
        q = self._events[key]
        while q and now - q[0] > 3600:
            q.popleft()
        return len(q) >= self._max

    def _record(self, key: str) -> None:
        self._events[key].append(time.monotonic())

    async def heal(self, project: str, check_id: str, policy: HealPolicy) -> HealOutcome:
        if policy.action is HealAction.ALERT_ONLY:
            return HealOutcome.ALERT_ONLY

        key = f"{project}:{check_id}"

        if not policy.is_reversible:
            # Riskante Aktion (Deploy/Code-Fix/...) → erst Discord-Approval
            approved = await self._approval(project, check_id, policy)
            if not approved:
                return HealOutcome.AWAITING_OR_DENIED
            # Nach Approval regelt der Approval-Workflow die Ausführung selbst
            # (kein Auto-Kommando für riskante Aktionen).
            self._record(key)
            return HealOutcome.HEALED

        # Reversibel → autonom, aber Circuit-Breaker gegen Restart-Loops
        if self._circuit_open(key):
            return HealOutcome.CIRCUIT_OPEN

        try:
            argv = build_heal_argv(policy)
        except ValueError:
            # Ungültige Heal-Config (z.B. fehlendes target) → als Fehlschlag
            # melden (der Alert zeigt dem Operator die Ursache).
            self._record(key)
            return HealOutcome.FAILED

        self._record(key)
        code = await self._exec(argv)
        return HealOutcome.HEALED if code == 0 else HealOutcome.FAILED
