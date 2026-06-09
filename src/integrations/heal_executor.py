"""Gestufte Heal-Policy: reversible Aktionen autonom (mit Circuit-Breaker),
riskante nur nach Discord-Approval, alert-only macht nichts.

Spiegelt die server-safety/autonomy-Regeln: reversibel = einfach machen,
riskant = stop & fragen.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from enum import Enum
from typing import Awaitable, Callable

from src.integrations.check_definitions import HealPolicy, HealAction

ShellRunner = Callable[[str], Awaitable[int]]                     # cmd -> exit code
ApprovalCb = Callable[[str, str, HealPolicy], Awaitable[bool]]    # projekt, check, policy -> approved?


class HealOutcome(str, Enum):
    HEALED = "healed"
    ALERT_ONLY = "alert-only"
    AWAITING_OR_DENIED = "awaiting-or-denied"
    CIRCUIT_OPEN = "circuit-open"
    FAILED = "failed"


# Reversible Aktion → Shell-Kommando-Template
_CMD: dict[HealAction, str] = {
    HealAction.RESTART_CONTAINER: "docker restart {target}",
    HealAction.RESTART_SERVICE: "systemctl --user restart {target}",
    HealAction.NETWORK_RECONNECT: "docker network connect {target}",  # target: "net container"
    HealAction.DISK_PRUNE: "docker builder prune -af && docker image prune -af",
}


class HealExecutor:
    def __init__(
        self,
        shell_runner: ShellRunner,
        approval_cb: ApprovalCb,
        max_per_hour: int = 5,
    ):
        self._shell = shell_runner
        self._approval = approval_cb
        self._max = max_per_hour
        # "{projekt}:{check}" -> Zeitstempel der letzten Heilungen (Circuit-Breaker-Fenster)
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
            # Nach Approval: die konkrete Ausführung regelt der Approval-Workflow
            # (kein Auto-Kommando-Template für riskante Aktionen).
            self._record(key)
            return HealOutcome.HEALED

        # Reversibel → autonom, aber Circuit-Breaker gegen Restart-Loops
        if self._circuit_open(key):
            return HealOutcome.CIRCUIT_OPEN

        cmd = _CMD[policy.action].format(target=policy.target or "")
        self._record(key)
        code = await self._shell(cmd)
        return HealOutcome.HEALED if code == 0 else HealOutcome.FAILED
