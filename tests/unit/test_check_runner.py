"""Unit-Tests für den CheckRunner (Plan 1, Task 2+3 + Review-Fixes).

http-Checks mit async-Context-Manager-Fake (inkl. JSON-Assertion). script-
Checks mocken ``asyncio.create_subprocess_exec`` (Unit-Tests starten keine
echten Prozesse). Unimplementierte Typen → graceful ERROR (kein Crash).
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, Mock, patch

from src.integrations.check_runner import CheckRunner
from src.integrations.check_definitions import CheckDefinition, CheckStatus


class _FakeResp:
    def __init__(self, status: int, json_data=None):
        self.status = status
        self._json = json_data

    async def json(self):
        if self._json is None:
            raise ValueError("kein JSON")
        return self._json


def _fake_session(resp: _FakeResp):
    class _GetCM:
        async def __aenter__(self):
            return resp

        async def __aexit__(self, *exc):
            return False

    class _Session:
        def get(self, url, headers=None):
            return _GetCM()

        def post(self, url, json=None, headers=None):
            return _GetCM()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    return _Session()


def _fake_proc(returncode, stderr: bytes = b""):
    proc = Mock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(b"", stderr))
    proc.kill = Mock()
    return proc


# ── http ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_http_check_ok():
    cd = CheckDefinition.from_dict(
        {"id": "h", "type": "http", "target": "https://x/health",
         "interval": 60, "expect": {"status": 200}}
    )
    runner = CheckRunner(base_url_resolver=lambda p, t: t)
    with patch("aiohttp.ClientSession", return_value=_fake_session(_FakeResp(200))):
        result = await runner.run(cd, project_name="zerodox")
    assert result.status is CheckStatus.OK


@pytest.mark.asyncio
async def test_http_check_wrong_status_fails():
    cd = CheckDefinition.from_dict(
        {"id": "h", "type": "http", "target": "https://x/health",
         "interval": 60, "expect": {"status": 200}}
    )
    runner = CheckRunner(base_url_resolver=lambda p, t: t)
    with patch("aiohttp.ClientSession", return_value=_fake_session(_FakeResp(503))):
        result = await runner.run(cd, project_name="zerodox")
    assert result.status is CheckStatus.FAIL
    assert "503" in result.message


@pytest.mark.asyncio
async def test_http_json_assertion_ok():
    cd = CheckDefinition.from_dict(
        {"id": "h", "type": "http", "target": "/h", "interval": 60,
         "expect": {"status": 200, "json_path": "data.ready", "json_eq": True}}
    )
    runner = CheckRunner(base_url_resolver=lambda p, t: t)
    resp = _FakeResp(200, json_data={"data": {"ready": True}})
    with patch("aiohttp.ClientSession", return_value=_fake_session(resp)):
        result = await runner.run(cd, project_name="zerodox")
    assert result.status is CheckStatus.OK


@pytest.mark.asyncio
async def test_http_json_assertion_mismatch_fails():
    cd = CheckDefinition.from_dict(
        {"id": "h", "type": "http", "target": "/h", "interval": 60,
         "expect": {"status": 200, "json_path": "status", "json_eq": "ok"}}
    )
    runner = CheckRunner(base_url_resolver=lambda p, t: t)
    resp = _FakeResp(200, json_data={"status": "degraded"})
    with patch("aiohttp.ClientSession", return_value=_fake_session(resp)):
        result = await runner.run(cd, project_name="zerodox")
    assert result.status is CheckStatus.FAIL
    assert "degraded" in result.message


@pytest.mark.asyncio
async def test_http_json_path_missing_fails():
    cd = CheckDefinition.from_dict(
        {"id": "h", "type": "http", "target": "/h", "interval": 60,
         "expect": {"status": 200, "json_path": "nope.here", "json_eq": 1}}
    )
    runner = CheckRunner(base_url_resolver=lambda p, t: t)
    resp = _FakeResp(200, json_data={"status": "ok"})
    with patch("aiohttp.ClientSession", return_value=_fake_session(resp)):
        result = await runner.run(cd, project_name="zerodox")
    assert result.status is CheckStatus.FAIL
    assert "nicht im Response" in result.message


# ── script (exec, gemockt) ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_script_check_exit0_ok():
    cd = CheckDefinition.from_dict(
        {"id": "smoke", "type": "script", "target": "echo hi", "interval": 900}
    )
    runner = CheckRunner(base_url_resolver=lambda p, t: t)
    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=_fake_proc(0))):
        result = await runner.run(cd, project_name="zerodox")
    assert result.status is CheckStatus.OK


@pytest.mark.asyncio
async def test_script_check_nonzero_fails():
    cd = CheckDefinition.from_dict(
        {"id": "smoke", "type": "script", "target": "false", "interval": 900}
    )
    runner = CheckRunner(base_url_resolver=lambda p, t: t)
    with patch(
        "asyncio.create_subprocess_exec",
        AsyncMock(return_value=_fake_proc(1, stderr=b"boom")),
    ):
        result = await runner.run(cd, project_name="zerodox")
    assert result.status is CheckStatus.FAIL
    assert "Exit 1" in result.message


@pytest.mark.asyncio
async def test_script_check_timeout_fails():
    cd = CheckDefinition.from_dict(
        {"id": "slow", "type": "script", "target": "sleep 5",
         "interval": 900, "timeout": 1}
    )
    runner = CheckRunner(base_url_resolver=lambda p, t: t)

    async def _slow_communicate():
        await asyncio.sleep(5)
        return (b"", b"")

    proc = Mock()
    proc.returncode = None
    proc.communicate = _slow_communicate
    proc.kill = Mock()
    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        result = await runner.run(cd, project_name="zerodox")
    assert result.status is CheckStatus.FAIL
    assert "Timeout" in result.message
    proc.kill.assert_called_once()


@pytest.mark.asyncio
async def test_script_uses_exec_not_shell_with_argv():
    # Sicherheit: metazeichen-haltiges target wird als argv übergeben, nicht als Shell.
    cd = CheckDefinition.from_dict(
        {"id": "s", "type": "script", "target": "bash scripts/smoke.sh", "interval": 900}
    )
    runner = CheckRunner(base_url_resolver=lambda p, t: t)
    fake = AsyncMock(return_value=_fake_proc(0))
    with patch("asyncio.create_subprocess_exec", fake):
        await runner.run(cd, project_name="zerodox")
    # erstes Positionsargument = Programm, dann die restlichen argv
    args = fake.call_args.args
    assert args[0] == "bash"
    assert args[1] == "scripts/smoke.sh"


# ── unimplementierte Typen: graceful ERROR (kein Crash) ─────────────────────

@pytest.mark.asyncio
async def test_resource_type_returns_error_not_crash():
    cd = CheckDefinition.from_dict(
        {"id": "disk", "type": "resource", "target": "disk.free", "interval": 300}
    )
    runner = CheckRunner(base_url_resolver=lambda p, t: t)
    result = await runner.run(cd, project_name="zerodox")
    assert result.status is CheckStatus.ERROR
    assert "Plan 3" in result.message


# ── HTTP-Header mit $ENV-Auflösung (Plan 2) ─────────────────────────────────

@pytest.mark.asyncio
async def test_http_resolves_env_header(monkeypatch):
    monkeypatch.setenv("ZERODOX_AGENT_API_KEY", "secret123")
    cd = CheckDefinition.from_dict(
        {"id": "h", "type": "http", "target": "/h", "interval": 60,
         "expect": {"status": 200}, "headers": {"X-Agent-Key": "$ZERODOX_AGENT_API_KEY"}}
    )
    captured = {}

    def _sess(resp):
        class _G:
            async def __aenter__(s):
                return resp

            async def __aexit__(s, *a):
                return False

        class _S:
            def get(s, url, headers=None):
                captured["headers"] = headers
                return _G()

            async def __aenter__(s):
                return s

            async def __aexit__(s, *a):
                return False

        return _S()

    runner = CheckRunner(base_url_resolver=lambda p, t: t)
    with patch("aiohttp.ClientSession", return_value=_sess(_FakeResp(200))):
        result = await runner.run(cd, project_name="zerodox")
    assert result.status is CheckStatus.OK
    assert captured["headers"] == {"X-Agent-Key": "secret123"}


@pytest.mark.asyncio
async def test_http_embedded_env_header(monkeypatch):
    monkeypatch.setenv("AKQUISE_AI_BEARER_TOKEN", "tok42")
    cd = CheckDefinition.from_dict(
        {"id": "h", "type": "http", "target": "/h", "interval": 60,
         "expect": {"status": 200}, "headers": {"Authorization": "Bearer $AKQUISE_AI_BEARER_TOKEN"}}
    )
    captured = {}

    def _sess(resp):
        class _G:
            async def __aenter__(s):
                return resp

            async def __aexit__(s, *a):
                return False

        class _S:
            def get(s, url, headers=None):
                captured["headers"] = headers
                return _G()

            async def __aenter__(s):
                return s

            async def __aexit__(s, *a):
                return False

        return _S()

    runner = CheckRunner(base_url_resolver=lambda p, t: t)
    with patch("aiohttp.ClientSession", return_value=_sess(_FakeResp(200))):
        await runner.run(cd, project_name="zerodox")
    assert captured["headers"] == {"Authorization": "Bearer tok42"}


@pytest.mark.asyncio
async def test_http_literal_header_unchanged():
    cd = CheckDefinition.from_dict(
        {"id": "h", "type": "http", "target": "/h", "interval": 60,
         "expect": {"status": 200}, "headers": {"X-Static": "literal-value"}}
    )
    captured = {}

    def _sess(resp):
        class _G:
            async def __aenter__(s):
                return resp

            async def __aexit__(s, *a):
                return False

        class _S:
            def get(s, url, headers=None):
                captured["headers"] = headers
                return _G()

            async def __aenter__(s):
                return s

            async def __aexit__(s, *a):
                return False

        return _S()

    runner = CheckRunner(base_url_resolver=lambda p, t: t)
    with patch("aiohttp.ClientSession", return_value=_sess(_FakeResp(200))):
        await runner.run(cd, project_name="zerodox")
    assert captured["headers"] == {"X-Static": "literal-value"}


# ── container-Check (network-attached, Plan 2) ──────────────────────────────

@pytest.mark.asyncio
async def test_container_network_attached_ok():
    cd = CheckDefinition.from_dict(
        {"id": "br", "type": "container", "target": "guildscout-postgres", "interval": 600,
         "expect": {"network": "project_sicherheitsdienst-network"}}
    )
    runner = CheckRunner(base_url_resolver=lambda p, t: t)
    proc = Mock()
    proc.returncode = 0
    proc.communicate = AsyncMock(
        return_value=(b'{"project_sicherheitsdienst-network":{},"other":{}}', b"")
    )
    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        result = await runner.run(cd, project_name="zerodox")
    assert result.status is CheckStatus.OK


@pytest.mark.asyncio
async def test_container_network_detached_fails():
    cd = CheckDefinition.from_dict(
        {"id": "br", "type": "container", "target": "guildscout-postgres", "interval": 600,
         "expect": {"network": "project_sicherheitsdienst-network"}}
    )
    runner = CheckRunner(base_url_resolver=lambda p, t: t)
    proc = Mock()
    proc.returncode = 0
    proc.communicate = AsyncMock(return_value=(b'{"other-network":{}}', b""))
    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        result = await runner.run(cd, project_name="zerodox")
    assert result.status is CheckStatus.FAIL
    assert "project_sicherheitsdienst-network" in result.message


@pytest.mark.asyncio
async def test_http_post_with_body_and_schema_ok():
    cd = CheckDefinition.from_dict(
        {"id": "syn", "type": "http", "target": "http://x/compose", "interval": 900,
         "method": "POST", "body": {"prospectId": "synthetic-check"},
         "expect": {"status": 200, "json_schema": ["hook", "finding_paragraph", "bridge_paragraph"]}}
    )
    assert cd.method == "POST"
    runner = CheckRunner(base_url_resolver=lambda p, t: t)
    resp = _FakeResp(200, json_data={"hook": "h", "finding_paragraph": "f", "bridge_paragraph": "b"})
    with patch("aiohttp.ClientSession", return_value=_fake_session(resp)):
        result = await runner.run(cd, project_name="zerodox")
    assert result.status is CheckStatus.OK


@pytest.mark.asyncio
async def test_http_schema_missing_field_fails():
    cd = CheckDefinition.from_dict(
        {"id": "syn", "type": "http", "target": "http://x/compose", "interval": 900,
         "method": "POST", "body": {"prospectId": "x"},
         "expect": {"status": 200, "json_schema": ["hook", "finding_paragraph", "bridge_paragraph"]}}
    )
    runner = CheckRunner(base_url_resolver=lambda p, t: t)
    resp = _FakeResp(200, json_data={"hook": "h", "finding_paragraph": ""})  # bridge fehlt, finding leer
    with patch("aiohttp.ClientSession", return_value=_fake_session(resp)):
        result = await runner.run(cd, project_name="zerodox")
    assert result.status is CheckStatus.FAIL
    assert "json_schema" in result.message


@pytest.mark.asyncio
async def test_container_missing_network_in_expect_errors():
    cd = CheckDefinition.from_dict(
        {"id": "br", "type": "container", "target": "x", "interval": 600}
    )
    runner = CheckRunner(base_url_resolver=lambda p, t: t)
    result = await runner.run(cd, project_name="zerodox")
    assert result.status is CheckStatus.ERROR
    assert "expect.network" in result.message
