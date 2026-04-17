"""JulesAPIClient — Wrapper um Jules REST API v1alpha.

Dokumentation (Alpha, Stand 2026-04-13):
- Endpoint: https://jules.googleapis.com/v1alpha
- Auth: X-Goog-Api-Key Header
- Key-Endpoints:
  - POST /sessions — neue Session starten (Jules oeffnet PR automatisch)
  - GET  /sessions — Session-Liste (Filter ueber state client-seitig)

Limits (Mittel-Plan, wie vom User bestaetigt):
- 100 neue Sessions / 24h
- 15 concurrent sessions

Diese Klasse NIE direkt aufrufen ohne Queue davor — der Scheduler enforced
die Limits. Fuer Ad-hoc-Tests ok.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import aiohttp

logger = logging.getLogger(__name__)


class JulesAPIError(Exception):
    """Fehler im API-Aufruf. .code: 'rate_limited' | 'http_XXX' | 'network'."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


class JulesAPIClient:
    """aiohttp-basierter Jules API-Client.

    Ein Client pro Bot-Instanz — hält keine persistente Session (aiohttp
    ClientSession wird pro Request aufgemacht, Overhead minimal bei <100 Calls/Tag).
    """

    BASE_URL = "https://jules.googleapis.com/v1alpha"
    DEFAULT_TIMEOUT_SECONDS = 30

    def __init__(self, api_key: str, timeout_seconds: Optional[int] = None):
        if not api_key:
            raise ValueError("api_key darf nicht leer sein")
        self._api_key = api_key
        self._timeout = aiohttp.ClientTimeout(
            total=timeout_seconds or self.DEFAULT_TIMEOUT_SECONDS,
        )

    # ── Public API ───────────────────────────────────────────

    async def create_session(
        self,
        *,
        prompt: str,
        owner: str,
        repo: str,
        title: str = "",
        branch: str = "main",
    ) -> str:
        """Startet neue Jules-Session. Gibt Session-ID zurueck.

        Raises JulesAPIError bei Rate-Limit oder HTTP-Fehler.
        """
        body: Dict[str, Any] = {
            "title": title or (prompt[:80] if prompt else "untitled"),
            "prompt": prompt,
            "sourceContext": {
                "source": f"sources/github/{owner}/{repo}",
                "githubRepoContext": {"startingBranch": branch},
            },
            "automationMode": "AUTO_CREATE_PR",
        }
        data = await self._post("sessions", body)
        # Response enthaelt "name": "sessions/abc123" oder "id": "abc123"
        session_id = data.get("id") or data.get("name", "").split("/")[-1]
        if not session_id:
            raise JulesAPIError("invalid_response", f"kein session-id: {data}")
        return session_id

    async def count_concurrent_sessions(self) -> int:
        """Zaehlt Sessions in state=IN_PROGRESS.

        Fehler werden nicht geraised — gibt 0 zurueck damit der Scheduler
        nicht blockiert. Logging in DEBUG.
        """
        try:
            data = await self._get("sessions", params={"pageSize": "50"})
        except JulesAPIError as e:
            logger.debug("[jules-api] count_concurrent failed: %s", e)
            return 0
        sessions = data.get("sessions", []) or []
        return sum(1 for s in sessions if s.get("state") == "IN_PROGRESS")

    async def get_session(self, session_id: str) -> Dict[str, Any]:
        """Holt Status einer Session."""
        return await self._get(f"sessions/{session_id}")

    async def list_sessions(
        self, *, state_filter: Optional[str] = None, page_size: int = 50,
    ) -> List[Dict[str, Any]]:
        """Listet Sessions, optional gefiltert nach state (client-seitig)."""
        try:
            data = await self._get("sessions", params={"pageSize": str(page_size)})
        except JulesAPIError:
            return []
        sessions = data.get("sessions", []) or []
        if state_filter:
            sessions = [s for s in sessions if s.get("state") == state_filter]
        return sessions

    # ── HTTP internals ───────────────────────────────────────

    @property
    def _headers(self) -> Dict[str, str]:
        return {
            "X-Goog-Api-Key": self._api_key,
            "Content-Type": "application/json",
        }

    async def _post(self, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.BASE_URL}/{path}"
        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as http:
                async with http.post(url, json=body, headers=self._headers) as r:
                    return await self._parse_response(r)
        except aiohttp.ClientError as e:
            raise JulesAPIError("network", str(e)) from e

    async def _get(
        self, path: str, *, params: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        url = f"{self.BASE_URL}/{path}"
        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as http:
                async with http.get(url, params=params, headers=self._headers) as r:
                    return await self._parse_response(r)
        except aiohttp.ClientError as e:
            raise JulesAPIError("network", str(e)) from e

    @staticmethod
    async def _parse_response(response: aiohttp.ClientResponse) -> Dict[str, Any]:
        if response.status == 429:
            raise JulesAPIError("rate_limited", f"HTTP 429")
        if response.status >= 400:
            text = await response.text()
            raise JulesAPIError(f"http_{response.status}", text[:300])
        try:
            return await response.json()
        except aiohttp.ContentTypeError:
            text = await response.text()
            raise JulesAPIError("invalid_json", text[:200])
