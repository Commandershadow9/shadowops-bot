"""Unit-Tests für den CheckRunner (Plan 1, Task 2+3).

http-Checks werden mit einem robusten async-Context-Manager-Fake getestet.
script-Checks mocken ``asyncio.create_subprocess_shell`` (Unit-Tests starten
keine echten Prozesse — das koppelt an asyncios globalen Child-Watcher und
verschmutzt nachfolgende async-Tests). Die echte Subprocess-Ausführung wird
in der Real-Chaos-Verifikation (Plan-Task 8) gegen echte Targets getestet.
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, Mock, patch

from src.integrations.check_runner import CheckRunner
from src.integrations.check_definitions import CheckDefinition, CheckStatus


class _FakeResp:
    def __init__(self, status: int):
        self.status = status


def _fake_session(resp: _FakeResp):
    """Fake-aiohttp.ClientSession als async-Context-Manager; .get() liefert
    einen async-CM mit der Fake-Response."""

    class _GetCM:
        async def __aenter__(self):
            return resp

        async def __aexit__(self, *exc):
            return False

    class _Session:
        def get(self, url):
            return _GetCM()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    return _Session()


def _fake_proc(returncode, stderr: bytes = b""):
    """Gemockter asyncio-Subprocess mit definiertem Exit-Code."""
    proc = Mock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(b"", stderr))
    proc.kill = Mock()
    return proc


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
async def test_script_check_exit0_ok():
    cd = CheckDefinition.from_dict(
        {"id": "smoke", "type": "script", "target": "echo hi", "interval": 900}
    )
    runner = CheckRunner(base_url_resolver=lambda p, t: t)
    with patch("asyncio.create_subprocess_shell", AsyncMock(return_value=_fake_proc(0))):
        result = await runner.run(cd, project_name="zerodox")
    assert result.status is CheckStatus.OK


@pytest.mark.asyncio
async def test_script_check_nonzero_fails():
    cd = CheckDefinition.from_dict(
        {"id": "smoke", "type": "script", "target": "exit 1", "interval": 900}
    )
    runner = CheckRunner(base_url_resolver=lambda p, t: t)
    with patch(
        "asyncio.create_subprocess_shell",
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
        await asyncio.sleep(5)  # überschreitet timeout=1 → TimeoutError
        return (b"", b"")

    proc = Mock()
    proc.returncode = None
    proc.communicate = _slow_communicate
    proc.kill = Mock()
    with patch("asyncio.create_subprocess_shell", AsyncMock(return_value=proc)):
        result = await runner.run(cd, project_name="zerodox")
    assert result.status is CheckStatus.FAIL
    assert "Timeout" in result.message
    proc.kill.assert_called_once()
