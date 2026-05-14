"""
Tests fuer Bot's Subprocess-Error-Capture im _run_post_deploy_command.

Hintergrund:
Bash-Scripts schreiben Errors typischerweise nach stdout (via echo/print_fail),
nicht nach stderr. Plus: `gh api ... 2>&1` redirects stderr-zu-stdout. Wenn
deploy.sh failt, ist stderr leer und der echte Fehler-Reason landet in stdout.

Original Bug (2026-05-14):
ZERODOX-Auto-Deploy failed 2x in 11s mit Bot-Log:
  "Post-deploy command failed:"
(mit Doppelpunkt, ohne Reason — weil stderr leer und Bot nur stderr captured)

Fix: Error-Message inkludiert stdout + stderr fuer vollstaendige Diagnose.
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.integrations.deployment_manager import DeploymentManager, DeploymentError


class _MockProcess:
    """Mock fuer asyncio.subprocess.Process."""

    def __init__(self, returncode: int, stdout: bytes = b"", stderr: bytes = b""):
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self):
        return self._stdout, self._stderr


@pytest.fixture
def mgr():
    """
    Stub-Instance ohne __init__ — `_run_post_deploy_command` nutzt nur das
    project-Argument, keinen Instanz-State. Bypass spart komplexes Bot/Config-Setup.
    """
    return DeploymentManager.__new__(DeploymentManager)


@pytest.mark.asyncio
async def test_post_deploy_failure_with_stdout_only_includes_stdout_in_error(mgr):
    """
    Wenn deploy.sh nach stdout schreibt (typisch fuer Bash mit print_fail / set -e
    nach gh api 2>&1) und stderr leer ist, MUSS die Error-Message den stdout-
    Inhalt enthalten — sonst geht die Diagnose verloren (Original-Bug).
    """
    project = {
        'post_deploy_command': 'bash /tmp/fake-deploy.sh',
        'path': '/tmp/fake-project',
    }

    fake_process = _MockProcess(
        returncode=1,
        stdout=b"\xe2\x9c\x97 Keine Required-Checks fuer Commit abc123 gefunden\n",
        stderr=b"",
    )

    with patch('asyncio.create_subprocess_exec', AsyncMock(return_value=fake_process)):
        with pytest.raises(DeploymentError) as exc_info:
            await mgr._run_post_deploy_command(project)

    msg = str(exc_info.value)
    assert "Keine Required-Checks" in msg, (
        f"Stdout-Diagnose muss in Error-Message sein, aber: {msg!r}"
    )


@pytest.mark.asyncio
async def test_post_deploy_failure_with_stderr_only_includes_stderr_in_error(mgr):
    """Standard-Fall: stderr-Output muss weiterhin sichtbar sein."""
    project = {
        'post_deploy_command': 'bash /tmp/fake-deploy.sh',
        'path': '/tmp/fake-project',
    }

    fake_process = _MockProcess(
        returncode=1,
        stdout=b"",
        stderr=b"npm ERR! ENOENT package.json\n",
    )

    with patch('asyncio.create_subprocess_exec', AsyncMock(return_value=fake_process)):
        with pytest.raises(DeploymentError) as exc_info:
            await mgr._run_post_deploy_command(project)

    msg = str(exc_info.value)
    assert "npm ERR" in msg, f"Stderr muss erhalten bleiben, aber: {msg!r}"


@pytest.mark.asyncio
async def test_post_deploy_failure_with_both_includes_both(mgr):
    """Wenn beides da ist: beide Streams in Error-Message."""
    project = {
        'post_deploy_command': 'bash /tmp/fake-deploy.sh',
        'path': '/tmp/fake-project',
    }

    fake_process = _MockProcess(
        returncode=2,
        stdout=b"\xe2\x9c\x97 GitHub-CI rot: 1 Check(s) failed\n",
        stderr=b"warning: gh api rate-limit hit\n",
    )

    with patch('asyncio.create_subprocess_exec', AsyncMock(return_value=fake_process)):
        with pytest.raises(DeploymentError) as exc_info:
            await mgr._run_post_deploy_command(project)

    msg = str(exc_info.value)
    assert "GitHub-CI rot" in msg, f"Stdout-Diagnose fehlt: {msg!r}"
    assert "rate-limit" in msg, f"Stderr-Diagnose fehlt: {msg!r}"


@pytest.mark.asyncio
async def test_post_deploy_success_does_not_raise(mgr):
    """Regression-Schutz: Erfolgsfall darf KEINE Exception werfen."""
    project = {
        'post_deploy_command': 'bash /tmp/fake-deploy.sh',
        'path': '/tmp/fake-project',
    }

    fake_process = _MockProcess(returncode=0, stdout=b"Deploy OK", stderr=b"")

    with patch('asyncio.create_subprocess_exec', AsyncMock(return_value=fake_process)):
        # Darf NICHT throw'n
        await mgr._run_post_deploy_command(project)
