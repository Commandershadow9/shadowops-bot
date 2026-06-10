"""Führt deklarative Checks aus und liefert ein typsicheres CheckResult.

Dispatcht nach CheckType:
- ``http``   : HTTP-Status + optionale JSON-Assertion (expect.json_path/json_eq),
               optionale Header (Werte mit $VAR werden aus os.environ aufgelöst)
- ``script`` : externes Kommando via create_subprocess_exec (kein Shell → kein
               Injection), Exit-Code/Timeout-Auswertung (synthetic)
- ``container``: Container-Netz-Anbindung (network-attached) via docker inspect
- ``resource``: noch nicht implementiert (Plan 3) → CheckStatus.ERROR (graceful)
"""
from __future__ import annotations

import asyncio
import os
import shlex
from typing import Any, Callable

import aiohttp

from .check_definitions import (
    CheckDefinition,
    CheckType,
    CheckResult,
    CheckStatus,
)


def _dig(obj: Any, dotted_path: str) -> Any:
    """Navigiert einen JSON-Body entlang eines Dot-Pfads ('data.ready').
    Gibt _MISSING zurück, wenn der Pfad nicht existiert."""
    cur = obj
    for part in dotted_path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return _MISSING
    return cur


_MISSING = object()


class CheckRunner:
    def __init__(self, base_url_resolver: Callable[[str, str], str]):
        # resolver(project_name, target) -> vollständige URL (Projekt-Basis + Pfad)
        self._resolve = base_url_resolver

    async def run(self, check: CheckDefinition, project_name: str) -> CheckResult:
        if check.type is CheckType.HTTP:
            return await self._run_http(check, project_name)
        if check.type is CheckType.SCRIPT:
            return await self._run_script(check)
        if check.type is CheckType.CONTAINER:
            return await self._run_container(check)
        # resource: Plan 3 — kein Crash, sondern klarer ERROR-Status.
        return CheckResult(
            check.id,
            CheckStatus.ERROR,
            message=f"Check-Typ '{check.type.value}' noch nicht implementiert (Plan 3)",
        )

    @staticmethod
    def _resolve_headers(headers: dict) -> dict:
        """Header-Werte mit $VAR-Syntax aus os.environ auflösen (kein Secret in config nötig)."""
        return {
            k: (os.environ.get(v[1:], "") if isinstance(v, str) and v.startswith("$") else v)
            for k, v in (headers or {}).items()
        }

    async def _run_http(self, check: CheckDefinition, project_name: str) -> CheckResult:
        url = self._resolve(project_name, check.target)
        expected = check.expect.get("status", 200)
        headers = self._resolve_headers(check.headers)
        try:
            timeout = aiohttp.ClientTimeout(total=check.timeout)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers=headers or None) as resp:
                    if resp.status != expected:
                        return CheckResult(
                            check.id,
                            CheckStatus.FAIL,
                            message=f"HTTP {resp.status} (erwartet {expected})",
                        )
                    # Optionale JSON-Assertion (expect.json_path + expect.json_eq)
                    json_path = check.expect.get("json_path")
                    if json_path:
                        try:
                            body = await resp.json()
                        except Exception as e:
                            return CheckResult(
                                check.id,
                                CheckStatus.FAIL,
                                message=f"JSON erwartet (json_path={json_path}), aber kein gültiges JSON: {e}",
                            )
                        actual = _dig(body, json_path)
                        expected_val = check.expect.get("json_eq")
                        if actual is _MISSING:
                            return CheckResult(
                                check.id,
                                CheckStatus.FAIL,
                                message=f"json_path '{json_path}' nicht im Response",
                            )
                        if actual != expected_val:
                            return CheckResult(
                                check.id,
                                CheckStatus.FAIL,
                                message=f"json_path '{json_path}'={actual!r}, erwartet {expected_val!r}",
                            )
                    return CheckResult(check.id, CheckStatus.OK)
        except Exception as e:  # Netzwerk/Timeout = FAIL (Ziel nicht erreichbar = ungesund)
            return CheckResult(check.id, CheckStatus.FAIL, message=f"unerreichbar: {e}")

    async def _run_script(self, check: CheckDefinition) -> CheckResult:
        try:
            argv = shlex.split(check.target)
        except ValueError as e:
            return CheckResult(check.id, CheckStatus.ERROR, message=f"ungültiges Skript-Kommando: {e}")
        if not argv:
            return CheckResult(check.id, CheckStatus.ERROR, message="leeres Skript-Kommando")
        try:
            proc = await asyncio.create_subprocess_exec(
                argv[0],
                *argv[1:],
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _out, err = await asyncio.wait_for(
                    proc.communicate(), timeout=check.timeout
                )
            except asyncio.TimeoutError:
                proc.kill()
                return CheckResult(check.id, CheckStatus.FAIL, message="Skript-Timeout")
            if proc.returncode == 0:
                return CheckResult(check.id, CheckStatus.OK)
            return CheckResult(
                check.id,
                CheckStatus.FAIL,
                message=f"Exit {proc.returncode}: {err.decode(errors='replace')[:200]}",
            )
        except FileNotFoundError:
            return CheckResult(check.id, CheckStatus.ERROR, message=f"Skript nicht gefunden: {argv[0]}")
        except Exception as e:
            return CheckResult(check.id, CheckStatus.ERROR, message=f"Skript-Fehler: {e}")

    async def _run_container(self, check: CheckDefinition) -> CheckResult:
        """Prüft, ob ein Container an einem Docker-Netzwerk hängt (network-attached).
        target = Container-Name, expect.network = erwartetes Netz. Heilbar via
        network-reconnect. Liest `docker inspect` JSON exec-sicher (kein Shell)."""
        import json as _json

        want_net = check.expect.get("network")
        if not want_net:
            return CheckResult(check.id, CheckStatus.ERROR, message="container-Check braucht expect.network")
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "inspect", "-f", "{{json .NetworkSettings.Networks}}", check.target,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            out, err = await asyncio.wait_for(proc.communicate(), timeout=check.timeout)
        except asyncio.TimeoutError:
            return CheckResult(check.id, CheckStatus.ERROR, message="docker inspect Timeout")
        except FileNotFoundError:
            return CheckResult(check.id, CheckStatus.ERROR, message="docker nicht im PATH")
        except Exception as e:
            return CheckResult(check.id, CheckStatus.ERROR, message=f"docker inspect Fehler: {e}")
        if proc.returncode != 0:
            return CheckResult(
                check.id, CheckStatus.FAIL,
                message=f"Container {check.target} nicht inspizierbar: {err.decode(errors='replace')[:120]}",
            )
        try:
            nets = _json.loads(out.decode() or "{}") or {}
        except Exception:
            nets = {}
        if want_net in nets:
            return CheckResult(check.id, CheckStatus.OK)
        return CheckResult(
            check.id, CheckStatus.FAIL,
            message=f"{check.target} nicht im Netz '{want_net}' (hat: {list(nets.keys())})",
        )
