"""Führt deklarative Checks aus und liefert ein typsicheres CheckResult.

Dispatcht nach CheckType. ``http`` nutzt aiohttp (wie der bestehende
ProjectMonitor-HTTP-Check), ``script`` führt ein externes Kommando aus und
wertet Exit-Code/Timeout aus (synthetic). resource/container folgen in
späteren Tasks.
"""
from __future__ import annotations

import asyncio
from typing import Callable

import aiohttp

from src.integrations.check_definitions import (
    CheckDefinition,
    CheckType,
    CheckResult,
    CheckStatus,
)


class CheckRunner:
    def __init__(self, base_url_resolver: Callable[[str, str], str]):
        # resolver(project_name, target) -> vollständige URL (Projekt-Basis + Pfad)
        self._resolve = base_url_resolver

    async def run(self, check: CheckDefinition, project_name: str) -> CheckResult:
        if check.type is CheckType.HTTP:
            return await self._run_http(check, project_name)
        if check.type is CheckType.SCRIPT:
            return await self._run_script(check)
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
                    return CheckResult(
                        check.id,
                        CheckStatus.FAIL,
                        message=f"HTTP {resp.status} (erwartet {expected})",
                    )
        except Exception as e:  # Netzwerk/Timeout = FAIL (Ziel nicht erreichbar)
            return CheckResult(check.id, CheckStatus.FAIL, message=f"unerreichbar: {e}")

    async def _run_script(self, check: CheckDefinition) -> CheckResult:
        try:
            proc = await asyncio.create_subprocess_shell(
                check.target,
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
        except Exception as e:
            return CheckResult(check.id, CheckStatus.ERROR, message=f"Skript-Fehler: {e}")
