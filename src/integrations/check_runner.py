"""Führt deklarative Checks aus und liefert ein typsicheres CheckResult.

Dispatcht nach CheckType:
- ``http``   : HTTP-Status + optionale JSON-Assertion (expect.json_path/json_eq)
- ``script`` : externes Kommando via create_subprocess_exec (kein Shell → kein
               Injection), Exit-Code/Timeout-Auswertung (synthetic)
- ``resource``/``container`` : noch nicht implementiert (Plan 2) → CheckStatus.ERROR
               (graceful, crasht den Poll-Loop NICHT)
"""
from __future__ import annotations

import asyncio
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
        # resource/container: Plan 2 — kein Crash, sondern klarer ERROR-Status.
        return CheckResult(
            check.id,
            CheckStatus.ERROR,
            message=f"Check-Typ '{check.type.value}' noch nicht implementiert (Plan 2)",
        )

    async def _run_http(self, check: CheckDefinition, project_name: str) -> CheckResult:
        url = self._resolve(project_name, check.target)
        expected = check.expect.get("status", 200)
        try:
            timeout = aiohttp.ClientTimeout(total=check.timeout)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as resp:
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
