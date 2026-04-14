"""JulesSuggestionsPoller — holt Jules-Task-Vorschlaege und queued sie.

Jules Dashboard zeigt fuer jeden verbundenen Repo eine Liste an "Top Suggestions"
— vorgeschlagene Tasks die Jules selbst lohnenswert findet. Der offizielle
API-Endpoint dafuer ist noch nicht stabil dokumentiert (Stand 2026-04-14).

Diese Klasse implementiert das Skeleton:
- Queue-Integration bereits fertig (`enqueue_suggestion()` funktioniert)
- Fetch-Teil ist aktuell No-Op mit Warnung — sobald Endpoint verfuegbar,
  nur `_fetch_suggestions()` implementieren.

Aufruf-Rhythmus: 3x taeglich (alle 8h) via `@tasks.loop` in bot.py.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional

from .jules_api import JulesAPIClient, JulesAPIError
from .queue import TaskQueue

logger = logging.getLogger(__name__)


@dataclass
class JulesSuggestion:
    """Ein von Jules vorgeschlagener Task."""
    repo: str           # "Commandershadow9/ZERODOX"
    title: str
    prompt: str
    branch: str = "main"
    priority_hint: int = 2  # default = jules_suggestion priority


class JulesSuggestionsPoller:
    """Poll-Loop fuer Jules Top-Suggestions.

    Phase 3 Status: Fetch ist STUB (loggt Warnung). Queue-Integration und
    Tests sind komplett — sobald der Endpoint da ist, einzig Methode
    `_fetch_suggestions(repo)` implementieren.
    """

    def __init__(
        self,
        *,
        queue: TaskQueue,
        jules_api: JulesAPIClient,
        repos: List[str],
        max_per_run: int = 20,
        max_per_repo: int = 5,
    ):
        self.queue = queue
        self.jules_api = jules_api
        self.repos = repos
        self.max_per_run = max_per_run
        self.max_per_repo = max_per_repo

    async def poll_and_queue(self) -> int:
        """Holt Suggestions fuer alle konfigurierten Repos, queued sie.

        Returns:
            Anzahl der neu enqueued Tasks.
        """
        total_queued = 0
        for full_repo in self.repos:
            if total_queued >= self.max_per_run:
                logger.info(
                    "[suggestions-poller] max_per_run=%d erreicht, stoppe",
                    self.max_per_run,
                )
                break
            try:
                suggestions = await self._fetch_suggestions(full_repo)
            except JulesAPIError as e:
                logger.warning(
                    "[suggestions-poller] %s fetch failed: %s", full_repo, e,
                )
                continue
            except Exception:
                logger.exception(
                    "[suggestions-poller] %s fetch crashed", full_repo,
                )
                continue

            remaining_budget = self.max_per_run - total_queued
            batch = suggestions[: min(self.max_per_repo, remaining_budget)]
            queued = await self._enqueue_batch(batch)
            total_queued += queued
            logger.info(
                "[suggestions-poller] %s: %d suggestions -> %d queued",
                full_repo, len(suggestions), queued,
            )

        return total_queued

    async def _enqueue_batch(self, suggestions: List[JulesSuggestion]) -> int:
        """Enqueued eine Liste von Suggestions. Fehler pro Item werden gefangen."""
        queued = 0
        for sug in suggestions:
            try:
                owner, repo = sug.repo.split("/", 1)
            except ValueError:
                logger.warning(
                    "[suggestions-poller] invalid repo format: %s", sug.repo,
                )
                continue

            await self.queue.enqueue(
                source="jules_suggestion",
                priority=sug.priority_hint,
                payload={
                    "owner": owner,
                    "repo": repo,
                    "prompt": sug.prompt,
                    "title": sug.title,
                    "branch": sug.branch,
                },
                project=repo,  # Projekt = repo-Name
            )
            queued += 1
        return queued

    async def _fetch_suggestions(self, full_repo: str) -> List[JulesSuggestion]:
        """STUB — Jules API v1alpha hat KEINEN Suggestions-Endpoint.

        Verifiziert 2026-04-14 via Discovery-Doc:
        https://jules.googleapis.com/$discovery/rest?version=v1alpha
        Nur 2 Resources: 'sessions' + 'sources'. Dashboard-Suggestions sind
        ein reines UI-Feature, nicht via oeffentliche API zugaenglich.

        Auch nicht via:
        - Jules CLI (`jules` hat keinen suggestions/recommend-Command)
        - MCP-Server (kein offizieller Jules-MCP verfuegbar; MCP waere nur
          Transport-Layer, kann keine Daten hervorholen die API nicht exposed)

        Wenn Google die API erweitert, nur diese Methode implementieren:
        - Vermutlich GET /v1alpha/sources/{source}/suggestions
        - Mapping: suggestion.title → JulesSuggestion.title, .prompt, .repo
        - Rest (Queue-Enqueue, Dedupe, Priority) bleibt unveraendert.

        Alternative Task-Quellen die HEUTE funktionieren:
        - SecurityScanAgent (seit 2026-04-14, queued Code-Fixes direkt)
        - Dependabot-PRs mit jules-Label (werden automatisch reviewt)
        - Manuelle Sessions via `jules new "..."` CLI
        - GitHub-Issues mit jules-Label (Jules iteriert darauf)
        """
        logger.debug(
            "[suggestions-poller] %s: Jules API hat keinen suggestions-Endpoint, skipping",
            full_repo,
        )
        return []
