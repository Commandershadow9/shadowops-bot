"""
Research Fetcher

Kleiner, abgesicherter HTTP-Fetcher mit Allowlist:
- Unterstützt GET (JSON/Plain) für definierte Domains (PyPI, npm, GitHub API/Raw).
- Beschränkt Response-Größe und Timeout.
- Loggt alle Zugriffe (Info/Errors) über Discord-Logger (ai_learning).
"""

import json
import logging
from typing import Optional, Dict, Any

import httpx

logger = logging.getLogger("shadowops.research")


class ResearchFetcher:
    def __init__(self, config=None, discord_logger=None):
        self.config = config
        self.discord_logger = discord_logger
        self.allowed_prefixes = [
            "https://pypi.org/pypi/",
            "https://registry.npmjs.org/",
            "https://api.github.com/repos/",
            "https://raw.githubusercontent.com/",
        ]
        self.max_bytes = 200_000
        self.timeout = 12

    def _is_allowed(self, url: str) -> bool:
        return any(url.startswith(prefix) for prefix in self.allowed_prefixes)

    async def fetch(self, url: str, reason: str = "", expect_json: bool = False) -> Optional[Any]:
        """
        Holt eine Ressource, wenn sie auf der Allowlist steht.
        - expect_json=True: parsed JSON zurück
        - sonst: Text (utf-8, errors=ignore)
        """
        if not self._is_allowed(url):
            msg = f"Blocked fetch (not allowed): {url}"
            logger.warning(msg)
            self._log_discord(f"🚫 {msg}", severity="warning")
            return None

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(url)

            content_len = len(resp.content or b"")
            if content_len > self.max_bytes:
                self._log_discord(f"🚫 Fetch too large ({content_len} bytes): {url}", severity="warning")
                return None

            if expect_json:
                data = resp.json()
                self._log_discord(f"🌐 Fetch OK (JSON) {url} ({content_len} bytes) {reason}", severity="info")
                return data
            else:
                text = resp.text
                self._log_discord(f"🌐 Fetch OK {url} ({content_len} bytes) {reason}", severity="info")
                return text
        except Exception as e:
            self._log_discord(f"❌ Fetch failed {url}: {e}", severity="error")
            logger.debug(f"Fetch error {url}: {e}", exc_info=True)
            return None

    def _log_discord(self, message: str, severity: str = "info"):
        if self.discord_logger:
            try:
                self.discord_logger.log_ai_learning(message, severity=severity)
            except Exception:
                logger.debug("Could not log to Discord", exc_info=True)
