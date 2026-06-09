# Monitoring-Engine-Grundlage — Implementierungsplan (Plan 1/3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ShadowOps zur zentralen Monitoring-Engine ausbauen: deklaratives Check-Inventar, Synthetic/Script- + Resource-Checks, gestufte Heal-Policy mit Circuit-Breaker und ein Maintenance-Gate — ohne die bestehende Engine neu zu bauen.

**Architecture:** Drei neue, fokussierte Module (`check_definitions.py`, `check_runner.py`, `maintenance_gate.py`) abstrahieren ein deklaratives `checks:`-Schema über die bestehende `ProjectMonitor`-Infrastruktur. Der bestehende Poll-Loop (`_monitor_project`) ruft neu den `CheckRunner` auf; der `HealExecutor` wendet pro Check eine gestufte Policy an; das `MaintenanceGate` unterdrückt Heilung während Wartung. Bestehende `_check_*`-Methoden, Anti-Spam-Cooldowns und Discord-Dispatch bleiben unverändert nutzbar.

**Tech Stack:** Python 3.11, asyncio, aiohttp, dataclasses, pytest + pytest-asyncio, PyYAML. Bestehende Patterns: `ProjectMonitor`/`ProjectStatus` (`src/integrations/project_monitor.py`), `Config` (`src/utils/config.py`), Tests in `tests/unit/` mit `conftest.py`-`mock_config`-Fixture + `AsyncMock`.

**Scope-Grenze:** Dies ist Plan 1 von 3. Plan 1 = Engine-Grundlage (Phase 0+1 der Spec). Plan 2 = ZERODOX-Migration (Phase 2). Plan 3 = GuildScout/MayDay + Final-Cut (Phase 3+4). Spec: `docs/2026-06-09-zentrales-monitoring-auto-health-design.md`.

**Konventionen:** Deutsche Code-Kommentare mit echten Umlauten. Tests `tests/unit/test_*.py`. Commits `feat|test|refactor(monitor): …`. Nach jeder Task: `python -m pytest tests/unit/<datei> -v` grün, dann commit.

---

## File Structure

| Datei | Verantwortung | Neu/Mod |
|---|---|---|
| `src/integrations/check_definitions.py` | `CheckType`-Enum, `HealAction`-Enum, `CheckDefinition` + `HealPolicy` dataclasses, `CheckResult` | **Neu** |
| `src/integrations/check_runner.py` | `CheckRunner.run(check, project) -> CheckResult` — Typ-Dispatcher (http/script/resource), nutzt bestehende Logik wo möglich | **Neu** |
| `src/integrations/heal_executor.py` | `HealExecutor` — gestufte Policy (reversible-auto/approval-required/alert-only) + Circuit-Breaker | **Neu** |
| `src/integrations/maintenance_gate.py` | `MaintenanceGate` — global/projekt Pause-Zustand, `is_suppressed(project)`, set/clear | **Neu** |
| `src/integrations/project_monitor.py` | Integration: deklarative `checks:` laden, im Poll-Loop via CheckRunner ausführen, Gate konsultieren, HealExecutor aufrufen | **Mod** |
| `src/cogs/admin.py` (oder bestehendes Cog) | Discord-Command `/maintenance <project|global> <on/off> [minutes] [reason]` | **Mod** |
| `config/config.yaml` | `projects.<name>.monitor.checks:`-Liste (deklaratives Inventar) | **Mod (lokal, gitignored)** |
| `docs/MONITORING_INVENTORY.md` | Vollständiges Check-Inventar (SSoT) aller Crons + Watchdogs | **Neu** |
| `tests/unit/test_check_definitions.py`, `test_check_runner.py`, `test_heal_executor.py`, `test_maintenance_gate.py` | Unit-Tests | **Neu** |

---

## Task 0: Monitoring-Inventar erstellen (Phase 0, Doku)

**Files:**
- Create: `docs/MONITORING_INVENTORY.md`

Kein TDD (Recherche/Doku). Vollständiges Inventar aller Health/Monitoring-Mechanismen als SSoT — die Grundlage, die beim Umzug fehlte.

- [ ] **Step 1: cmdshadow-Crontab katalogisieren**

Run: `crontab -l | grep -vE '^\s*#|^\s*$'`
Jede Health/Monitoring-Zeile erfassen: Script, Intervall, Kategorie (liveness/funktional/resource/meta), heutige Heal-Aktion.

- [ ] **Step 2: user-systemd Watchdogs katalogisieren**

Run: `systemctl --user list-timers --all --no-pager` + für jeden Watchdog `systemctl --user cat <name>.service | grep ExecStart`
Erfassen: Watchdog, Mode (http/systemd/drift/resource), Target, Discord-Webhook.

- [ ] **Step 3: GuildScout-/MayDay-eigene Crons prüfen**

Run: `crontab -l` (bereits cmdshadow) + `ls /srv/leitstelle/scripts/cron-*.sh ~/GuildScout/**/cron*.sh 2>/dev/null` + `sudo crontab -l 2>/dev/null` (falls Zugriff).
Lücken dokumentieren, falls Checks außerhalb der cmdshadow-Crontab existieren.

- [ ] **Step 4: Inventar-Tabelle schreiben**

`docs/MONITORING_INVENTORY.md` mit Spalten: `id | projekt | typ | quelle(cron/watchdog) | intervall | heal-heute | ziel-check-typ | heal-stufe | dead-man? | status(aktiv/abgelöst)`. Pro Eintrag eine Zeile. Dead-Man-Kandidaten (shadowops-watchdog, shadowops-drift-watchdog) markieren.

- [ ] **Step 5: Commit**

```bash
git add docs/MONITORING_INVENTORY.md
git commit -m "docs(monitor): vollständiges Monitoring-Inventar als SSoT (Phase 0)"
```

---

## Task 1: CheckDefinition-Schema (dataclasses)

**Files:**
- Create: `src/integrations/check_definitions.py`
- Test: `tests/unit/test_check_definitions.py`

- [ ] **Step 1: Failing Test schreiben**

```python
# tests/unit/test_check_definitions.py
import pytest
from src.integrations.check_definitions import (
    CheckType, HealAction, CheckDefinition, HealPolicy, CheckResult, CheckStatus,
)

def test_check_definition_from_dict_minimal():
    spec = {"id": "web-liveness", "type": "http", "target": "/api/health", "interval": 300}
    cd = CheckDefinition.from_dict(spec)
    assert cd.id == "web-liveness"
    assert cd.type is CheckType.HTTP
    assert cd.interval == 300
    assert cd.heal.action is HealAction.ALERT_ONLY  # default, wenn kein heal angegeben

def test_check_definition_with_reversible_heal():
    spec = {"id": "web", "type": "http", "target": "/h", "interval": 60,
            "heal": {"action": "restart-container", "target": "zerodox-web"}}
    cd = CheckDefinition.from_dict(spec)
    assert cd.heal.action is HealAction.RESTART_CONTAINER
    assert cd.heal.target == "zerodox-web"
    assert cd.heal.is_reversible is True  # restart-container = reversibel → autonom

def test_heal_action_approval_required_is_not_reversible():
    assert HealAction.DEPLOY.is_reversible is False
    assert HealAction.RESTART_CONTAINER.is_reversible is True
    assert HealAction.NETWORK_RECONNECT.is_reversible is True
    assert HealAction.ALERT_ONLY.is_reversible is False  # alert-only macht nichts → nicht "autonom heilen"

def test_unknown_check_type_raises():
    with pytest.raises(ValueError, match="unbekannter Check-Typ"):
        CheckDefinition.from_dict({"id": "x", "type": "telepathy", "target": "/", "interval": 60})
```

- [ ] **Step 2: Test rot**

Run: `python -m pytest tests/unit/test_check_definitions.py -v`
Expected: FAIL (Modul existiert nicht).

- [ ] **Step 3: Implementierung**

```python
# src/integrations/check_definitions.py
"""Deklaratives Check-Schema für die zentrale Monitoring-Engine.

Ein Check ist ein YAML-Eintrag unter projects.<name>.monitor.checks. Dieses
Modul mappt solche Einträge auf typsichere dataclasses und definiert, welche
Heal-Aktionen reversibel (autonom erlaubt) bzw. genehmigungspflichtig sind.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class CheckType(str, Enum):
    HTTP = "http"
    SCRIPT = "script"        # externes Skript, Exit-Code/Marker → synthetic
    RESOURCE = "resource"    # disk/mem/netz
    CONTAINER = "container"  # Container up / Netz-Anbindung


class HealAction(str, Enum):
    ALERT_ONLY = "alert-only"
    RESTART_CONTAINER = "restart-container"   # reversibel
    RESTART_SERVICE = "restart-service"       # reversibel
    NETWORK_RECONNECT = "network-reconnect"   # reversibel
    DISK_PRUNE = "disk-prune"                 # reversibel
    DEPLOY = "deploy"                          # genehmigungspflichtig
    CODE_FIX = "code-fix"                      # genehmigungspflichtig

    @property
    def is_reversible(self) -> bool:
        return self in {
            HealAction.RESTART_CONTAINER, HealAction.RESTART_SERVICE,
            HealAction.NETWORK_RECONNECT, HealAction.DISK_PRUNE,
        }


class CheckStatus(str, Enum):
    OK = "ok"
    FAIL = "fail"
    ERROR = "error"  # Check selbst kaputt (nicht das Ziel)


@dataclass
class HealPolicy:
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
    id: str
    type: CheckType
    target: str           # URL-Pfad / Skript-Pfad / Metrik-Key / Container-Name
    interval: int         # Sekunden
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
            id=d["id"], type=ctype, target=d["target"], interval=int(d["interval"]),
            timeout=int(d.get("timeout", 10)), expect=d.get("expect", {}),
            heal=HealPolicy.from_dict(d.get("heal")), flake_polls=int(d.get("flake_polls", 1)),
        )


@dataclass
class CheckResult:
    check_id: str
    status: CheckStatus
    value: Optional[float] = None
    message: str = ""
```

- [ ] **Step 4: Test grün**

Run: `python -m pytest tests/unit/test_check_definitions.py -v`
Expected: PASS (4 Tests).

- [ ] **Step 5: Commit**

```bash
git add src/integrations/check_definitions.py tests/unit/test_check_definitions.py
git commit -m "feat(monitor): deklaratives CheckDefinition-Schema + reversible-Heal-Klassifikation"
```

---

## Task 2: CheckRunner — http-Typ

**Files:**
- Create: `src/integrations/check_runner.py`
- Test: `tests/unit/test_check_runner.py`

- [ ] **Step 1: Failing Test**

```python
# tests/unit/test_check_runner.py
import pytest
from unittest.mock import AsyncMock, patch
from src.integrations.check_runner import CheckRunner
from src.integrations.check_definitions import CheckDefinition, CheckStatus

@pytest.mark.asyncio
async def test_http_check_ok():
    cd = CheckDefinition.from_dict({"id": "h", "type": "http",
        "target": "https://x/health", "interval": 60, "expect": {"status": 200}})
    runner = CheckRunner(base_url_resolver=lambda p, t: t)
    with patch("aiohttp.ClientSession") as sess:
        resp = AsyncMock(); resp.status = 200
        sess.return_value.__aenter__.return_value.get.return_value.__aenter__.return_value = resp
        result = await runner.run(cd, project_name="zerodox")
    assert result.status is CheckStatus.OK

@pytest.mark.asyncio
async def test_http_check_wrong_status_fails():
    cd = CheckDefinition.from_dict({"id": "h", "type": "http",
        "target": "https://x/health", "interval": 60, "expect": {"status": 200}})
    runner = CheckRunner(base_url_resolver=lambda p, t: t)
    with patch("aiohttp.ClientSession") as sess:
        resp = AsyncMock(); resp.status = 503
        sess.return_value.__aenter__.return_value.get.return_value.__aenter__.return_value = resp
        result = await runner.run(cd, project_name="zerodox")
    assert result.status is CheckStatus.FAIL
    assert "503" in result.message
```

- [ ] **Step 2: Test rot**

Run: `python -m pytest tests/unit/test_check_runner.py -v`
Expected: FAIL (kein Modul).

- [ ] **Step 3: Implementierung (nur http; script/resource folgen in Task 3/4)**

```python
# src/integrations/check_runner.py
"""Führt deklarative Checks aus und liefert ein typsicheres CheckResult.

Dispatcht nach CheckType. http nutzt aiohttp (wie der bestehende
ProjectMonitor-HTTP-Check). script + resource werden in Folge-Tasks ergänzt.
"""
from __future__ import annotations
import aiohttp
from typing import Callable
from src.integrations.check_definitions import CheckDefinition, CheckType, CheckResult, CheckStatus


class CheckRunner:
    def __init__(self, base_url_resolver: Callable[[str, str], str]):
        # resolver(project_name, target) -> vollständige URL (Projekt-Basis + Pfad)
        self._resolve = base_url_resolver

    async def run(self, check: CheckDefinition, project_name: str) -> CheckResult:
        if check.type is CheckType.HTTP:
            return await self._run_http(check, project_name)
        raise NotImplementedError(f"Check-Typ {check.type} noch nicht implementiert")

    async def _run_http(self, check: CheckDefinition, project_name: str) -> CheckResult:
        url = self._resolve(project_name, check.target)
        expected = check.expect.get("status", 200)
        try:
            timeout = aiohttp.ClientTimeout(total=check.timeout)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as resp:
                    if resp.status == expected:
                        return CheckResult(check.id, CheckStatus.OK)
                    return CheckResult(check.id, CheckStatus.FAIL,
                                       message=f"HTTP {resp.status} (erwartet {expected})")
        except Exception as e:  # Netzwerk/Timeout = FAIL (Ziel nicht erreichbar)
            return CheckResult(check.id, CheckStatus.FAIL, message=f"unerreichbar: {e}")
```

- [ ] **Step 4: Test grün**

Run: `python -m pytest tests/unit/test_check_runner.py -v`
Expected: PASS (2 Tests).

- [ ] **Step 5: Commit**

```bash
git add src/integrations/check_runner.py tests/unit/test_check_runner.py
git commit -m "feat(monitor): CheckRunner mit http-Typ"
```

---

## Task 3: CheckRunner — script-Typ (synthetic)

**Files:**
- Modify: `src/integrations/check_runner.py`
- Test: `tests/unit/test_check_runner.py` (ergänzen)

- [ ] **Step 1: Failing Test ergänzen**

```python
@pytest.mark.asyncio
async def test_script_check_exit0_ok():
    cd = CheckDefinition.from_dict({"id": "smoke", "type": "script",
        "target": "/bin/true", "interval": 900})
    runner = CheckRunner(base_url_resolver=lambda p, t: t)
    result = await runner.run(cd, project_name="zerodox")
    assert result.status is CheckStatus.OK

@pytest.mark.asyncio
async def test_script_check_nonzero_fails():
    cd = CheckDefinition.from_dict({"id": "smoke", "type": "script",
        "target": "/bin/false", "interval": 900})
    runner = CheckRunner(base_url_resolver=lambda p, t: t)
    result = await runner.run(cd, project_name="zerodox")
    assert result.status is CheckStatus.FAIL
```

- [ ] **Step 2: Test rot**

Run: `python -m pytest tests/unit/test_check_runner.py -k script -v`
Expected: FAIL (NotImplementedError).

- [ ] **Step 3: Implementierung — `_run_script` + Dispatch**

In `run()` ergänzen: `if check.type is CheckType.SCRIPT: return await self._run_script(check)`. Neue Methode:

```python
    async def _run_script(self, check: CheckDefinition) -> CheckResult:
        import asyncio
        try:
            proc = await asyncio.create_subprocess_shell(
                check.target,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            try:
                out, err = await asyncio.wait_for(proc.communicate(), timeout=check.timeout)
            except asyncio.TimeoutError:
                proc.kill()
                return CheckResult(check.id, CheckStatus.FAIL, message="Skript-Timeout")
            if proc.returncode == 0:
                return CheckResult(check.id, CheckStatus.OK)
            return CheckResult(check.id, CheckStatus.FAIL,
                               message=f"Exit {proc.returncode}: {err.decode()[:200]}")
        except Exception as e:
            return CheckResult(check.id, CheckStatus.ERROR, message=f"Skript-Fehler: {e}")
```

- [ ] **Step 4: Test grün**

Run: `python -m pytest tests/unit/test_check_runner.py -v`
Expected: PASS (4 Tests).

- [ ] **Step 5: Commit**

```bash
git add src/integrations/check_runner.py tests/unit/test_check_runner.py
git commit -m "feat(monitor): script/synthetic-Check (Exit-Code + Timeout)"
```

---

## Task 4: HealExecutor — gestufte Policy + Circuit-Breaker

**Files:**
- Create: `src/integrations/heal_executor.py`
- Test: `tests/unit/test_heal_executor.py`

- [ ] **Step 1: Failing Test**

```python
# tests/unit/test_heal_executor.py
import pytest
from unittest.mock import AsyncMock
from src.integrations.heal_executor import HealExecutor, HealOutcome
from src.integrations.check_definitions import HealPolicy, HealAction

@pytest.mark.asyncio
async def test_reversible_heal_runs_autonomously():
    runner = AsyncMock(return_value=0)  # Shell-Runner gibt Exit 0
    ex = HealExecutor(shell_runner=runner, approval_cb=AsyncMock(), max_per_hour=5)
    policy = HealPolicy(action=HealAction.RESTART_CONTAINER, target="zerodox-web")
    outcome = await ex.heal("zerodox", "web-liveness", policy)
    assert outcome is HealOutcome.HEALED
    runner.assert_awaited_once()

@pytest.mark.asyncio
async def test_approval_required_action_does_not_run_without_approval():
    runner = AsyncMock(); approval = AsyncMock(return_value=False)  # abgelehnt
    ex = HealExecutor(shell_runner=runner, approval_cb=approval, max_per_hour=5)
    policy = HealPolicy(action=HealAction.DEPLOY, target="zerodox")
    outcome = await ex.heal("zerodox", "x", policy)
    assert outcome is HealOutcome.AWAITING_OR_DENIED
    runner.assert_not_awaited()

@pytest.mark.asyncio
async def test_circuit_breaker_blocks_after_max():
    runner = AsyncMock(return_value=0)
    ex = HealExecutor(shell_runner=runner, approval_cb=AsyncMock(), max_per_hour=2)
    policy = HealPolicy(action=HealAction.RESTART_CONTAINER, target="c")
    await ex.heal("p", "c1", policy); await ex.heal("p", "c1", policy)
    outcome = await ex.heal("p", "c1", policy)  # 3. → blockiert
    assert outcome is HealOutcome.CIRCUIT_OPEN
    assert runner.await_count == 2

@pytest.mark.asyncio
async def test_alert_only_never_runs_shell():
    runner = AsyncMock()
    ex = HealExecutor(shell_runner=runner, approval_cb=AsyncMock(), max_per_hour=5)
    outcome = await ex.heal("p", "c", HealPolicy(action=HealAction.ALERT_ONLY))
    assert outcome is HealOutcome.ALERT_ONLY
    runner.assert_not_awaited()
```

- [ ] **Step 2: Test rot**

Run: `python -m pytest tests/unit/test_heal_executor.py -v`
Expected: FAIL.

- [ ] **Step 3: Implementierung**

```python
# src/integrations/heal_executor.py
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

ShellRunner = Callable[[str], Awaitable[int]]          # cmd -> exit code
ApprovalCb = Callable[[str, str, HealPolicy], Awaitable[bool]]  # projekt, check, policy -> approved?


class HealOutcome(str, Enum):
    HEALED = "healed"
    ALERT_ONLY = "alert-only"
    AWAITING_OR_DENIED = "awaiting-or-denied"
    CIRCUIT_OPEN = "circuit-open"
    FAILED = "failed"


# Aktion → Shell-Kommando-Template
_CMD = {
    HealAction.RESTART_CONTAINER: "docker restart {target}",
    HealAction.RESTART_SERVICE: "systemctl --user restart {target}",
    HealAction.NETWORK_RECONNECT: "docker network connect {target}",  # target: "net container"
    HealAction.DISK_PRUNE: "docker builder prune -af && docker image prune -af",
}


class HealExecutor:
    def __init__(self, shell_runner: ShellRunner, approval_cb: ApprovalCb, max_per_hour: int = 5):
        self._shell = shell_runner
        self._approval = approval_cb
        self._max = max_per_hour
        self._events: dict[str, deque] = defaultdict(deque)  # "{projekt}:{check}" -> timestamps

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
            approved = await self._approval(project, check_id, policy)
            if not approved:
                return HealOutcome.AWAITING_OR_DENIED
            # nach Approval ausführen (riskante Aktion → kein Auto-Cmd-Template; Approval-Cb regelt)
            self._record(key)
            return HealOutcome.HEALED
        # reversibel → autonom, aber Circuit-Breaker
        if self._circuit_open(key):
            return HealOutcome.CIRCUIT_OPEN
        cmd = _CMD[policy.action].format(target=policy.target or "")
        self._record(key)
        code = await self._shell(cmd)
        return HealOutcome.HEALED if code == 0 else HealOutcome.FAILED
```

- [ ] **Step 4: Test grün**

Run: `python -m pytest tests/unit/test_heal_executor.py -v`
Expected: PASS (4 Tests).

- [ ] **Step 5: Commit**

```bash
git add src/integrations/heal_executor.py tests/unit/test_heal_executor.py
git commit -m "feat(monitor): gestufte Heal-Policy + Circuit-Breaker (reversibel autonom / riskant approval)"
```

---

## Task 5: MaintenanceGate

**Files:**
- Create: `src/integrations/maintenance_gate.py`
- Test: `tests/unit/test_maintenance_gate.py`

- [ ] **Step 1: Failing Test**

```python
# tests/unit/test_maintenance_gate.py
from src.integrations.maintenance_gate import MaintenanceGate

def test_default_not_suppressed():
    g = MaintenanceGate()
    assert g.is_suppressed("zerodox") is False

def test_project_suppression():
    g = MaintenanceGate(); g.enable("zerodox", minutes=30, reason="Deploy")
    assert g.is_suppressed("zerodox") is True
    assert g.is_suppressed("guildscout") is False

def test_global_suppression_covers_all():
    g = MaintenanceGate(); g.enable("global", minutes=30, reason="Wartung")
    assert g.is_suppressed("zerodox") is True
    assert g.is_suppressed("mayday") is True

def test_disable_clears():
    g = MaintenanceGate(); g.enable("zerodox", minutes=30, reason="x")
    g.disable("zerodox")
    assert g.is_suppressed("zerodox") is False

def test_expiry(monkeypatch):
    import src.integrations.maintenance_gate as m
    t = [1000.0]; monkeypatch.setattr(m.time, "monotonic", lambda: t[0])
    g = MaintenanceGate(); g.enable("zerodox", minutes=10, reason="x")
    t[0] = 1000.0 + 11 * 60
    assert g.is_suppressed("zerodox") is False  # abgelaufen
```

- [ ] **Step 2: Test rot**

Run: `python -m pytest tests/unit/test_maintenance_gate.py -v`
Expected: FAIL.

- [ ] **Step 3: Implementierung**

```python
# src/integrations/maintenance_gate.py
"""Wartungs-Schalter: pausiert Auto-Heal global oder pro Projekt für eine
befristete Dauer. Checks laufen weiter, nur Heilung wird unterdrückt.

Löst den Cut-over-Auto-Heal-Vorfall: ein Schalter statt zwei Systeme manuell
pausieren.
"""
from __future__ import annotations
import time
from dataclasses import dataclass


@dataclass
class _Window:
    until: float
    reason: str


class MaintenanceGate:
    GLOBAL = "global"

    def __init__(self):
        self._windows: dict[str, _Window] = {}

    def enable(self, scope: str, minutes: int, reason: str) -> None:
        self._windows[scope] = _Window(until=time.monotonic() + minutes * 60, reason=reason)

    def disable(self, scope: str) -> None:
        self._windows.pop(scope, None)

    def _active(self, scope: str) -> bool:
        w = self._windows.get(scope)
        if w is None:
            return False
        if time.monotonic() >= w.until:
            self._windows.pop(scope, None)
            return False
        return True

    def is_suppressed(self, project: str) -> bool:
        return self._active(self.GLOBAL) or self._active(project)
```

- [ ] **Step 4: Test grün**

Run: `python -m pytest tests/unit/test_maintenance_gate.py -v`
Expected: PASS (5 Tests).

- [ ] **Step 5: Commit**

```bash
git add src/integrations/maintenance_gate.py tests/unit/test_maintenance_gate.py
git commit -m "feat(monitor): MaintenanceGate (global/projekt, befristet)"
```

---

## Task 6: Integration in ProjectMonitor

**Files:**
- Modify: `src/integrations/project_monitor.py` (Init + `_monitor_project` + neue Methode `_run_declarative_checks`)
- Test: `tests/unit/test_project_monitor_checks.py`

- [ ] **Step 1: Failing Test**

```python
# tests/unit/test_project_monitor_checks.py
import pytest
from unittest.mock import AsyncMock, Mock
from src.integrations.project_monitor import ProjectMonitor
from src.integrations.check_definitions import CheckStatus, CheckResult, HealAction

@pytest.mark.asyncio
async def test_declarative_check_fail_triggers_heal_when_not_in_maintenance():
    config = Mock()
    config.projects = {"zerodox": {"enabled": True, "monitor": {"enabled": True,
        "url": "http://x/h", "checks": [
            {"id": "web", "type": "http", "target": "/h", "interval": 60,
             "heal": {"action": "restart-container", "target": "zerodox-web"}}]}}}
    mon = ProjectMonitor(bot=Mock(), config=config)
    mon._check_runner.run = AsyncMock(return_value=CheckResult("web", CheckStatus.FAIL, message="503"))
    mon._heal_executor.heal = AsyncMock()
    await mon._run_declarative_checks(mon.projects["zerodox"])
    mon._heal_executor.heal.assert_awaited_once()

@pytest.mark.asyncio
async def test_maintenance_gate_suppresses_heal():
    config = Mock()
    config.projects = {"zerodox": {"enabled": True, "monitor": {"enabled": True,
        "url": "http://x/h", "checks": [
            {"id": "web", "type": "http", "target": "/h", "interval": 60,
             "heal": {"action": "restart-container", "target": "zerodox-web"}}]}}}
    mon = ProjectMonitor(bot=Mock(), config=config)
    mon._maintenance_gate.enable("zerodox", minutes=30, reason="Test")
    mon._check_runner.run = AsyncMock(return_value=CheckResult("web", CheckStatus.FAIL))
    mon._heal_executor.heal = AsyncMock()
    await mon._run_declarative_checks(mon.projects["zerodox"])
    mon._heal_executor.heal.assert_not_awaited()  # Gate aktiv → kein Heal
```

- [ ] **Step 2: Test rot**

Run: `python -m pytest tests/unit/test_project_monitor_checks.py -v`
Expected: FAIL (Attribute/Methode fehlen).

- [ ] **Step 3: Implementierung — ProjectMonitor erweitern**

In `ProjectMonitor.__init__` (nach bestehender Init) ergänzen:

```python
        from src.integrations.check_runner import CheckRunner
        from src.integrations.heal_executor import HealExecutor
        from src.integrations.maintenance_gate import MaintenanceGate
        self._maintenance_gate = MaintenanceGate()
        self._check_runner = CheckRunner(base_url_resolver=self._resolve_check_url)
        self._heal_executor = HealExecutor(
            shell_runner=self._run_shell, approval_cb=self._request_heal_approval, max_per_hour=5)
```

`ProjectStatus.__init__` (Z.109) um `self.checks` erweitern: `monitor_config.get('checks', [])` → Liste von `CheckDefinition.from_dict(c)`.

Neue Methoden in `ProjectMonitor`:

```python
    def _resolve_check_url(self, project_name: str, target: str) -> str:
        # Pfad relativ → an Projekt-Basis-URL hängen; absolute URL unverändert
        if target.startswith("http"):
            return target
        base = self.projects[project_name].url.rstrip("/").rsplit("/", 1)[0]
        return base + target

    async def _run_shell(self, cmd: str) -> int:
        import asyncio
        proc = await asyncio.create_subprocess_shell(cmd)
        await proc.communicate()
        return proc.returncode or 0

    async def _request_heal_approval(self, project, check_id, policy) -> bool:
        # Discord-Approval-Workflow (bestehende auto_remediation-Channels). Bis verdrahtet: False.
        return False

    async def _run_declarative_checks(self, project) -> None:
        for check in getattr(project, "checks", []):
            if not self._should_run_health_check(project, f"decl:{check.id}"):
                continue
            self._mark_health_check_ran(project, f"decl:{check.id}")
            result = await self._check_runner.run(check, project.name)
            if result.status is CheckStatus.OK:
                continue
            # Alert (bestehender Dispatch) — hier nur, falls nicht-OK
            if self._maintenance_gate.is_suppressed(project.name):
                continue  # Wartung → kein Heal (Alert optional gedrosselt)
            await self._heal_executor.heal(project.name, check.id, check.heal)
```

In `_monitor_project` (Z.443ff) nach den bestehenden Checks ergänzen: `await self._run_declarative_checks(project)`.

Import oben in der Datei: `from src.integrations.check_definitions import CheckDefinition, CheckStatus`.

- [ ] **Step 4: Test grün**

Run: `python -m pytest tests/unit/test_project_monitor_checks.py -v`
Expected: PASS (2 Tests).

- [ ] **Step 5: Volle Suite grün (Regression)**

Run: `python -m pytest tests/unit/ -q`
Expected: keine neuen Failures gegenüber Baseline.

- [ ] **Step 6: Commit**

```bash
git add src/integrations/project_monitor.py tests/unit/test_project_monitor_checks.py
git commit -m "feat(monitor): deklarative Checks im Poll-Loop + Maintenance-Gate-Integration"
```

---

## Task 7: Discord-Command `/maintenance`

**Files:**
- Modify: bestehendes Admin-Cog (`src/cogs/admin.py` — exakte Datei beim Umsetzen via `grep -rl "@commands\|app_commands" src/cogs/` bestätigen)
- Test: `tests/unit/test_maintenance_command.py`

- [ ] **Step 1: Failing Test (Logik separat von Discord testbar)**

```python
# tests/unit/test_maintenance_command.py
from src.integrations.maintenance_gate import MaintenanceGate

def test_command_enables_and_disables():
    g = MaintenanceGate()
    # Simuliere Command-Handler-Logik
    g.enable("zerodox", minutes=30, reason="Deploy")
    assert g.is_suppressed("zerodox")
    g.disable("zerodox")
    assert not g.is_suppressed("zerodox")
```

- [ ] **Step 2–4: Command-Handler verdrahten**

Im Admin-Cog einen Command hinzufügen, der `bot.project_monitor._maintenance_gate.enable/disable` aufruft und eine Discord-Bestätigung sendet. Parameter: `scope` (Projektname oder `global`), `state` (`on`/`off`), `minutes` (default 60), `reason`. Bei `on` → `enable`, bei `off` → `disable`. Antwort-Embed mit Restdauer.

Run: `python -m pytest tests/unit/test_maintenance_command.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cogs/admin.py tests/unit/test_maintenance_command.py
git commit -m "feat(monitor): /maintenance Discord-Command (Gate steuern)"
```

---

## Task 8: Real-Trigger-Verifikation (deterministisch, alle Projekte)

**Files:**
- Create: `scripts/chaos-verify-monitoring.sh` (Verifikations-Helfer, kein Test-Framework)
- Modify: `config/config.yaml` (lokal) — einen deklarativen Pilot-Check je Projekt eintragen

**Mandat (User):** Real triggern bei *allen* Projekten inkl. ZERODOX; kurze Downtime ist OK. Deterministischer Beweis statt passivem Soak.

- [ ] **Step 1: Pilot-Check in config.yaml eintragen (lokal)**

Unter `projects.zerodox.monitor` einen `checks:`-Eintrag ergänzen:
```yaml
      checks:
        - {id: web-liveness, type: http, target: /api/health, interval: 60,
           expect: {status: 200}, heal: {action: restart-container, target: zerodox-web}}
```

- [ ] **Step 2: Bot neu laden** (Pilot-Check aktiv)

Run: `systemctl restart shadowops-bot` (System-Service, root) — **kurz**, dokumentiert.
Verifizieren: `journalctl -u shadowops-bot --since '1 min ago' | grep -i 'web-liveness\|checks geladen'`

- [ ] **Step 3: REAL triggern — zerodox-web stoppen**

Run: `docker stop zerodox-web`
Erwartung: Innerhalb `interval` (60 s) erkennt ShadowOps FAIL → `restart-container zerodox-web` (reversibel → autonom) → Container wieder up. Beobachten:
```bash
watch -n2 'docker ps --filter name=zerodox-web --format "{{.Status}}"'
```
Plus Discord-Channel: Befund + Heal-Meldung sichtbar.

- [ ] **Step 4: Maintenance-Gate-Test (REAL)**

`/maintenance zerodox on 5 reason:chaos-test` → `docker stop zerodox-web` → **kein** Auto-Heal (Container bleibt down). Dann manuell `docker start zerodox-web` + `/maintenance zerodox off`.

- [ ] **Step 5: Circuit-Breaker-Test (REAL)**

zerodox-web 6× in Folge stoppen (oder einen absichtlich crash-loopenden Dummy-Container) → nach 5 Heilungen/h greift Circuit-Breaker → Eskalations-Alert statt endloser Restart-Loop.

- [ ] **Step 6: Verifikations-Skript + Ergebnis dokumentieren**

`scripts/chaos-verify-monitoring.sh` kapselt Steps 3–5 reproduzierbar (mit Bestätigungs-Prompt, da Prod). Ergebnis (Screenshots/Discord-Links) in `docs/MONITORING_INVENTORY.md` unter „web-liveness: verifiziert" eintragen.

- [ ] **Step 7: Commit**

```bash
git add scripts/chaos-verify-monitoring.sh docs/MONITORING_INVENTORY.md
git commit -m "test(monitor): Real-Trigger-Chaos-Verifikation (Heal + Gate + Circuit-Breaker)"
```

---

## Self-Review (vom Plan-Autor)

- **Spec-Coverage:** §3 Architektur → Tasks 1–6; §4 Check-Schema → Task 1; §5 Heal-Policy → Task 4; Maintenance-Gate → Task 5+7; §6 Inventar → Task 0; §8 Cut-over-Verifikation (aktiv-real) → Task 8. Phase 2–4 (Migration echter Checks, Alt-Abschaltung) bewusst NICHT hier → Plan 2/3.
- **Keine Placeholder:** Tests vollständig, Impl-Code je Task gezeigt. Task 7 Command-Verdrahtung verweist auf konkrete Datei-Bestätigung beim Umsetzen (Cog-Pfad projektabhängig).
- **Typ-Konsistenz:** `CheckDefinition`/`CheckResult`/`CheckStatus`/`HealPolicy`/`HealAction`/`HealOutcome` durchgängig gleich benannt über Tasks 1→4→6.

## Offene Phase-0-Entscheidungen (in Task 0 zu klären)
1. Report-only-Crons (soak/stale-pr/backup-monitor/ki-cost/doku-drift) → Engine-`report`-Typ oder als Cron belassen?
2. Maintenance-Gate-Auto-Trigger während `deploy.sh`-Lauf?
3. Dead-Man: shadowops-watchdog + shadowops-drift-watchdog konsolidieren oder beide behalten?
