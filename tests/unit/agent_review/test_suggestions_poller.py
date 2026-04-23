"""Tests fuer JulesSuggestionsPoller — Queue-Integration + Fehler-Pfade."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.integrations.github_integration.agent_review.suggestions_poller import (
    JulesSuggestionsPoller, JulesSuggestion,
)
from src.integrations.github_integration.agent_review.jules_api import JulesAPIError

pytestmark = pytest.mark.asyncio


def _make_poller(suggestions_by_repo=None, fetch_raises=None, **kwargs):
    """Fabrik fuer Poller mit kontrolliertem Fetch-Verhalten."""
    queue = AsyncMock()
    queue.enqueue = AsyncMock(return_value=1)
    jules_api = MagicMock()

    poller = JulesSuggestionsPoller(
        queue=queue,
        jules_api=jules_api,
        repos=kwargs.get("repos", ["Commandershadow9/ZERODOX"]),
        max_per_run=kwargs.get("max_per_run", 20),
        max_per_repo=kwargs.get("max_per_repo", 5),
    )

    async def fake_fetch(repo):
        if fetch_raises is not None:
            raise fetch_raises
        if suggestions_by_repo:
            return suggestions_by_repo.get(repo, [])
        return []

    poller._fetch_suggestions = fake_fetch
    return poller, queue


# ─────────── poll_and_queue() ───────────

class TestPollAndQueue:
    async def test_empty_suggestions_returns_zero(self):
        poller, queue = _make_poller(suggestions_by_repo={})
        n = await poller.poll_and_queue()
        assert n == 0
        queue.enqueue.assert_not_awaited()

    async def test_queues_all_suggestions_per_repo(self):
        suggestions = {
            "Commandershadow9/ZERODOX": [
                JulesSuggestion(repo="Commandershadow9/ZERODOX",
                                title="Fix typos", prompt="Fix typos in docs"),
                JulesSuggestion(repo="Commandershadow9/ZERODOX",
                                title="Add CI", prompt="Add GH actions workflow"),
            ]
        }
        poller, queue = _make_poller(suggestions_by_repo=suggestions)
        n = await poller.poll_and_queue()
        assert n == 2
        assert queue.enqueue.await_count == 2

    async def test_respects_max_per_repo(self):
        sugs = [
            JulesSuggestion(repo="Commandershadow9/ZERODOX",
                            title=f"T{i}", prompt=f"task {i}")
            for i in range(10)
        ]
        poller, queue = _make_poller(
            suggestions_by_repo={"Commandershadow9/ZERODOX": sugs},
            max_per_repo=3,
        )
        n = await poller.poll_and_queue()
        assert n == 3

    async def test_respects_max_per_run_across_repos(self):
        sugs_a = [JulesSuggestion(repo="a/b", title="x", prompt="x") for _ in range(5)]
        sugs_c = [JulesSuggestion(repo="c/d", title="x", prompt="x") for _ in range(5)]
        poller, queue = _make_poller(
            suggestions_by_repo={"a/b": sugs_a, "c/d": sugs_c},
            repos=["a/b", "c/d"],
            max_per_run=7,
            max_per_repo=5,
        )
        n = await poller.poll_and_queue()
        # Nach a/b (5) werden wir >= max_per_run, schneidet nach erstem Repo ab
        assert n <= 7

    async def test_api_error_continues_next_repo(self):
        """Wenn ein Repo-Fetch fehlschlaegt, gehen andere weiter."""
        poller, queue = _make_poller(repos=["a/b", "c/d"])
        # a/b raised, c/d returnt []
        call_count = {"n": 0}
        async def fake_fetch(repo):
            call_count["n"] += 1
            if repo == "a/b":
                raise JulesAPIError("http_500", "oops")
            return []
        poller._fetch_suggestions = fake_fetch
        n = await poller.poll_and_queue()
        assert n == 0
        assert call_count["n"] == 2  # beide versucht

    async def test_unknown_exception_also_tolerated(self):
        """Jede Exception in Fetch darf poller nicht killen."""
        poller, queue = _make_poller(fetch_raises=RuntimeError("unexpected"))
        n = await poller.poll_and_queue()
        assert n == 0


# ─────────── _enqueue_batch ───────────

class TestEnqueueBatch:
    async def test_splits_repo_into_owner_repo(self):
        poller, queue = _make_poller(suggestions_by_repo={
            "Commandershadow9/ZERODOX": [
                JulesSuggestion(
                    repo="Commandershadow9/ZERODOX",
                    title="Fix", prompt="Do X", branch="dev",
                ),
            ]
        })
        await poller.poll_and_queue()
        call = queue.enqueue.await_args
        kwargs = call.kwargs
        assert kwargs["source"] == "jules_suggestion"
        assert kwargs["payload"]["owner"] == "Commandershadow9"
        assert kwargs["payload"]["repo"] == "ZERODOX"
        assert kwargs["payload"]["prompt"] == "Do X"
        assert kwargs["payload"]["branch"] == "dev"
        assert kwargs["project"] == "ZERODOX"

    async def test_priority_from_hint(self):
        poller, queue = _make_poller(
            suggestions_by_repo={
                "a/b": [JulesSuggestion(repo="a/b", title="x", prompt="x", priority_hint=0)]
            },
            repos=["a/b"],
        )
        await poller.poll_and_queue()
        assert queue.enqueue.await_args.kwargs["priority"] == 0

    async def test_invalid_repo_format_skipped(self):
        """Wenn repo keinen Slash hat, skippe mit Warnung."""
        poller, queue = _make_poller(suggestions_by_repo={
            "weird-repo": [
                JulesSuggestion(repo="weird-repo", title="x", prompt="x"),
            ]
        }, repos=["weird-repo"])
        n = await poller.poll_and_queue()
        assert n == 0
        queue.enqueue.assert_not_awaited()


# ─────────── Stub-Behavior ───────────

class TestStub:
    async def test_fetch_stub_returns_empty(self):
        """Default-Implementierung (ohne Override) liefert [] und logged."""
        from src.integrations.github_integration.agent_review.suggestions_poller import (
            JulesSuggestionsPoller as Real,
        )
        poller = Real(
            queue=AsyncMock(),
            jules_api=MagicMock(),
            repos=["a/b"],
        )
        result = await poller._fetch_suggestions("a/b")
        assert result == []
