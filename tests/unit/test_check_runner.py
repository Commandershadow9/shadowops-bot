"""Unit-Tests für den CheckRunner (Plan 1, Task 2+3).

http-Checks werden mit einem robusten async-Context-Manager-Fake getestet
(zuverlässiger als tiefes aiohttp-Mocking). script-Checks nutzen echte
/bin/true bzw. /bin/false Subprocesses (deterministisch, kein Mock).
"""
import pytest
from unittest.mock import patch

from src.integrations.check_runner import CheckRunner
from src.integrations.check_definitions import CheckDefinition, CheckStatus


class _FakeResp:
    def __init__(self, status: int):
        self.status = status


def _fake_session(resp: _FakeResp):
    """Baut ein Fake-aiohttp.ClientSession, das als async-Context-Manager
    funktioniert und bei .get() einen async-CM mit der Fake-Response liefert."""

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
        {"id": "smoke", "type": "script", "target": "/bin/true", "interval": 900}
    )
    runner = CheckRunner(base_url_resolver=lambda p, t: t)
    result = await runner.run(cd, project_name="zerodox")
    assert result.status is CheckStatus.OK


@pytest.mark.asyncio
async def test_script_check_nonzero_fails():
    cd = CheckDefinition.from_dict(
        {"id": "smoke", "type": "script", "target": "/bin/false", "interval": 900}
    )
    runner = CheckRunner(base_url_resolver=lambda p, t: t)
    result = await runner.run(cd, project_name="zerodox")
    assert result.status is CheckStatus.FAIL


@pytest.mark.asyncio
async def test_script_check_timeout_fails():
    cd = CheckDefinition.from_dict(
        {"id": "slow", "type": "script", "target": "sleep 5", "interval": 900, "timeout": 1}
    )
    runner = CheckRunner(base_url_resolver=lambda p, t: t)
    result = await runner.run(cd, project_name="zerodox")
    assert result.status is CheckStatus.FAIL
    assert "Timeout" in result.message
