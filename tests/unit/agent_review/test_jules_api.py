"""Tests fuer JulesAPIClient mit gemockter aiohttp."""
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from src.integrations.github_integration.agent_review.jules_api import (
    JulesAPIClient, JulesAPIError,
)

pytestmark = pytest.mark.asyncio


# ─────────── Helpers ───────────

class _FakeResponse:
    def __init__(self, status: int, json_data=None, text_data: str = ""):
        self.status = status
        self._json = json_data or {}
        self._text = text_data

    async def json(self):
        return self._json

    async def text(self):
        return self._text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


class _FakeSession:
    def __init__(self, response: _FakeResponse):
        self._response = response
        self.posted_json = None
        self.posted_url = None
        self.posted_headers = None
        self.get_url = None
        self.get_params = None

    def post(self, url, json=None, headers=None):
        self.posted_url = url
        self.posted_json = json
        self.posted_headers = headers
        return self._response

    def get(self, url, params=None, headers=None):
        self.get_url = url
        self.get_params = params
        return self._response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


def _patch_session(fake: _FakeSession):
    """Patcht aiohttp.ClientSession so dass es fake zurueckgibt."""
    return patch(
        "src.integrations.github_integration.agent_review.jules_api.aiohttp.ClientSession",
        return_value=fake,
    )


# ─────────── Init-Validation ───────────

class TestInit:
    def test_rejects_empty_api_key(self):
        with pytest.raises(ValueError, match="api_key"):
            JulesAPIClient(api_key="")

    def test_accepts_custom_timeout(self):
        client = JulesAPIClient(api_key="k", timeout_seconds=120)
        assert client._timeout.total == 120


# ─────────── create_session ───────────

class TestCreateSession:
    async def test_happy_path_returns_session_id(self):
        client = JulesAPIClient(api_key="key")
        fake = _FakeSession(_FakeResponse(200, {"id": "sess-abc123"}))
        with _patch_session(fake):
            sid = await client.create_session(
                prompt="fix bug", owner="Commandershadow9", repo="ZERODOX",
                title="Fix XSS", branch="main",
            )
        assert sid == "sess-abc123"

    async def test_extracts_id_from_name_field(self):
        client = JulesAPIClient(api_key="key")
        fake = _FakeSession(_FakeResponse(200, {"name": "sessions/xyz789"}))
        with _patch_session(fake):
            sid = await client.create_session(
                prompt="p", owner="o", repo="r",
            )
        assert sid == "xyz789"

    async def test_builds_correct_request_body(self):
        client = JulesAPIClient(api_key="key")
        fake = _FakeSession(_FakeResponse(200, {"id": "s1"}))
        with _patch_session(fake):
            await client.create_session(
                prompt="fix XSS", owner="Commandershadow9", repo="ZERODOX",
                title="Security Fix", branch="dev",
            )
        body = fake.posted_json
        assert body["prompt"] == "fix XSS"
        assert body["title"] == "Security Fix"
        assert body["sourceContext"]["source"] == "sources/github/Commandershadow9/ZERODOX"
        assert body["sourceContext"]["githubRepoContext"]["startingBranch"] == "dev"
        assert body["automationMode"] == "AUTO_CREATE_PR"

    async def test_uses_api_key_header(self):
        client = JulesAPIClient(api_key="secret-key")
        fake = _FakeSession(_FakeResponse(200, {"id": "s1"}))
        with _patch_session(fake):
            await client.create_session(prompt="p", owner="o", repo="r")
        assert fake.posted_headers["X-Goog-Api-Key"] == "secret-key"
        assert fake.posted_headers["Content-Type"] == "application/json"

    async def test_title_fallback_to_prompt_prefix(self):
        client = JulesAPIClient(api_key="k")
        fake = _FakeSession(_FakeResponse(200, {"id": "s"}))
        with _patch_session(fake):
            await client.create_session(
                prompt="a" * 200, owner="o", repo="r", title="",
            )
        body = fake.posted_json
        assert len(body["title"]) == 80  # truncated

    async def test_rate_limited_raises(self):
        client = JulesAPIClient(api_key="k")
        fake = _FakeSession(_FakeResponse(429))
        with _patch_session(fake):
            with pytest.raises(JulesAPIError) as exc:
                await client.create_session(prompt="p", owner="o", repo="r")
        assert exc.value.code == "rate_limited"

    async def test_http_error_raises(self):
        client = JulesAPIClient(api_key="k")
        fake = _FakeSession(_FakeResponse(500, text_data="internal server error"))
        with _patch_session(fake):
            with pytest.raises(JulesAPIError) as exc:
                await client.create_session(prompt="p", owner="o", repo="r")
        assert exc.value.code == "http_500"
        assert "internal server error" in exc.value.detail

    async def test_invalid_response_raises(self):
        """Wenn Response keine id und kein name hat."""
        client = JulesAPIClient(api_key="k")
        fake = _FakeSession(_FakeResponse(200, {"something_else": True}))
        with _patch_session(fake):
            with pytest.raises(JulesAPIError) as exc:
                await client.create_session(prompt="p", owner="o", repo="r")
        assert exc.value.code == "invalid_response"

    async def test_network_error_raises(self):
        client = JulesAPIClient(api_key="k")
        with patch(
            "src.integrations.github_integration.agent_review.jules_api.aiohttp.ClientSession",
            side_effect=aiohttp.ClientError("connection refused"),
        ):
            with pytest.raises(JulesAPIError) as exc:
                await client.create_session(prompt="p", owner="o", repo="r")
        assert exc.value.code == "network"


# ─────────── count_concurrent_sessions ───────────

class TestCountConcurrent:
    async def test_counts_in_progress(self):
        client = JulesAPIClient(api_key="k")
        fake = _FakeSession(_FakeResponse(200, {"sessions": [
            {"id": "1", "state": "IN_PROGRESS"},
            {"id": "2", "state": "IN_PROGRESS"},
            {"id": "3", "state": "COMPLETED"},
            {"id": "4", "state": "FAILED"},
        ]}))
        with _patch_session(fake):
            n = await client.count_concurrent_sessions()
        assert n == 2

    async def test_returns_zero_on_error(self):
        """Fehler NICHT raisen — Scheduler soll nicht blockieren."""
        client = JulesAPIClient(api_key="k")
        fake = _FakeSession(_FakeResponse(500))
        with _patch_session(fake):
            n = await client.count_concurrent_sessions()
        assert n == 0

    async def test_returns_zero_for_empty_response(self):
        client = JulesAPIClient(api_key="k")
        fake = _FakeSession(_FakeResponse(200, {}))
        with _patch_session(fake):
            n = await client.count_concurrent_sessions()
        assert n == 0


# ─────────── list_sessions ───────────

class TestListSessions:
    async def test_no_filter_returns_all(self):
        client = JulesAPIClient(api_key="k")
        fake = _FakeSession(_FakeResponse(200, {"sessions": [
            {"id": "1", "state": "COMPLETED"},
            {"id": "2", "state": "IN_PROGRESS"},
        ]}))
        with _patch_session(fake):
            sessions = await client.list_sessions()
        assert len(sessions) == 2

    async def test_filters_by_state(self):
        client = JulesAPIClient(api_key="k")
        fake = _FakeSession(_FakeResponse(200, {"sessions": [
            {"id": "1", "state": "COMPLETED"},
            {"id": "2", "state": "IN_PROGRESS"},
            {"id": "3", "state": "IN_PROGRESS"},
        ]}))
        with _patch_session(fake):
            sessions = await client.list_sessions(state_filter="IN_PROGRESS")
        assert len(sessions) == 2
        assert all(s["state"] == "IN_PROGRESS" for s in sessions)

    async def test_error_returns_empty_list(self):
        client = JulesAPIClient(api_key="k")
        fake = _FakeSession(_FakeResponse(500))
        with _patch_session(fake):
            sessions = await client.list_sessions()
        assert sessions == []


# ─────────── get_session ───────────

class TestGetSession:
    async def test_returns_session_data(self):
        client = JulesAPIClient(api_key="k")
        fake = _FakeSession(_FakeResponse(200, {"id": "s1", "state": "COMPLETED"}))
        with _patch_session(fake):
            data = await client.get_session("s1")
        assert data["state"] == "COMPLETED"
