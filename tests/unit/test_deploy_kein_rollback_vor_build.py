"""Kein Rollback vor dem Build, und nie auf den Arbeitsbaum (ZERODOX#3515).

## Der Vorfall (29.09.2026, 17:33–17:36 und 20:24–20:28)

Auf ZERODOX-main stand eine nicht-additive Migration aus (Float → Decimal).
Jeder weitere Merge löste einen Deploy aus, `deploy.sh` stieg im Pre-Flight
aus (`Migration-Drift`, exit 1) — VOR dem Build, die Seite lief unverändert.
Der Bot behandelte das wie jeden anderen Fehlschlag: „Attempting rollback“
(`deployment_manager.py:552`), `rsync --delete` vom Backup in
`project['path']`. Bei ZERODOX ist das der Arbeitsbaum `~/ZERODOX`, den der
Deploy seit ZERODOX#2344 gar nicht mehr verändert (Git-Schritt, Tests und
post-deploy laufen in `~/ZERODOX-deploy`). In einem laufenden Agent-Worktree
wurde `web/src` dabei dreimal geleert.

## Zwei Riegel, beide hier geprüft

1. `deploy.sh` meldet einen Pre-Flight-Abbruch mit Exitcode 78 → eigene
   Ausnahme, Fehlermeldung ja, Rollback nein.
2. Backup und Rollback arbeiten auf dem Deploy-Baum (`deploy_path`), nie auf
   dem Arbeitsbaum. Das greift auch, solange `deploy.sh` den Code 78 noch
   nicht setzt und ein Pre-Flight-Abbruch weiter mit 1 endet.
"""
import logging
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.integrations.deployment_manager import (
    DeploymentError,
    DeploymentManager,
    PostDeployPreflightAbortError,
    PostDeployTempfailError,
    _POST_DEPLOY_PREFLIGHT_EXIT_CODE,
)


class _MockProcess:
    def __init__(self, returncode: int, stdout: bytes = b"", stderr: bytes = b""):
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self):
        return self._stdout, self._stderr


def _projekt(tmp_path: Path, mit_deploy_baum: bool = True) -> dict:
    arbeitsbaum = tmp_path / "ZERODOX"
    arbeitsbaum.mkdir(exist_ok=True)
    deploy_baum = tmp_path / "ZERODOX-deploy"
    deploy_baum.mkdir(exist_ok=True)
    return {
        'name': 'zerodox',
        'path': arbeitsbaum,
        'deploy_path': str(deploy_baum) if mit_deploy_baum else None,
        'branch': 'main',
        'deploy_enabled': True,
        'run_tests': False,
        'test_command': 'pytest',
        'post_deploy_command': 'bash scripts/deploy.sh --yes',
        'health_check_url': '',
        'service_name': None,
        'repo_url': None,
        'github_deploy_feedback': None,
    }


@pytest.fixture
def mgr(tmp_path):
    """Stub ohne __init__; nur der State, den deploy_project/_rollback lesen."""
    m = DeploymentManager.__new__(DeploymentManager)
    m.logger = logging.getLogger("test_deploy_kein_rollback_vor_build")
    m.backup_dir = tmp_path / "backups"
    m.backup_dir.mkdir()
    m.max_backups_per_project = 5
    m.projects = {'zerodox': _projekt(tmp_path)}
    m.active_deployments = {}
    m._send_deployment_started = AsyncMock()
    m._send_deployment_update = AsyncMock()
    m._send_deployment_success = AsyncMock()
    m._send_deployment_failure = AsyncMock()
    m._git_pull = AsyncMock()
    m._restart_service = AsyncMock()
    m._rollback = AsyncMock()
    m.wartenden_auftrag_entnehmen = lambda _key: None
    backup = tmp_path / "backups" / "zerodox_x"
    backup.mkdir()
    m._create_backup = AsyncMock(return_value=backup)
    return m


# ── 1. Exitcode → Ausnahme ─────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exitcode, erwartet, nicht_erwartet",
    [
        (_POST_DEPLOY_PREFLIGHT_EXIT_CODE, PostDeployPreflightAbortError, None),
        (75, PostDeployTempfailError, PostDeployPreflightAbortError),
        (1, DeploymentError, PostDeployPreflightAbortError),
    ],
)
async def test_exitcode_bestimmt_die_ausnahme(mgr, tmp_path, exitcode, erwartet, nicht_erwartet):
    projekt = _projekt(tmp_path)
    with patch(
        "asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=_MockProcess(exitcode, b"Migration-Drift")),
    ):
        with pytest.raises(erwartet) as info:
            await mgr._run_post_deploy_command(projekt)
    if nicht_erwartet is not None:
        assert not isinstance(info.value, nicht_erwartet)
    assert f"exit={exitcode}" in str(info.value)


# ── 2. Pre-Flight-Abbruch: melden ja, Rollback nein ────────────────────────


@pytest.mark.asyncio
async def test_preflight_abbruch_loest_keinen_rollback_aus(mgr):
    mgr._run_post_deploy_command = AsyncMock(
        side_effect=PostDeployPreflightAbortError(
            "Post-deploy command failed (exit=78):\nstdout: ✗ Migration-Drift"
        )
    )

    ergebnis = await mgr.deploy_project("zerodox", branch="main")

    mgr._rollback.assert_not_awaited()
    mgr._restart_service.assert_not_awaited()
    assert ergebnis['success'] is False
    assert ergebnis['rolled_back'] is False
    assert "Migration-Drift" in ergebnis['error']
    # Ein echter Fehlschlag, der eine Handlung braucht — er wird gemeldet.
    mgr._send_deployment_failure.assert_awaited_once()
    assert mgr.active_deployments['zerodox'] is False


@pytest.mark.asyncio
async def test_gegenprobe_fehler_nach_dem_build_rollt_weiter_zurueck(mgr):
    """Der Riegel darf den Rollback nicht generell abschalten."""
    mgr._run_post_deploy_command = AsyncMock(
        side_effect=DeploymentError("Post-deploy command failed (exit=1): Health rot")
    )

    ergebnis = await mgr.deploy_project("zerodox", branch="main")

    mgr._rollback.assert_awaited_once()
    assert ergebnis['rolled_back'] is True
    mgr._send_deployment_failure.assert_awaited_once()


# ── 3. Backup und Rollback: Deploy-Baum, nie Arbeitsbaum ───────────────────


def _rsync_ziel(cmd: tuple) -> str:
    return cmd[-1].rstrip('/')


def _rsync_quelle(cmd: tuple) -> str:
    return cmd[-2].rstrip('/')


@pytest.mark.asyncio
async def test_rollback_zielt_auf_den_deploy_baum(mgr, tmp_path):
    projekt = _projekt(tmp_path)
    backup = tmp_path / "backups" / "zerodox_x"
    mock_exec = AsyncMock(return_value=_MockProcess(0))

    with patch("shutil.which", return_value="/usr/bin/rsync"), \
         patch("asyncio.create_subprocess_exec", new=mock_exec):
        await DeploymentManager._rollback(mgr, projekt, backup)

    cmd = mock_exec.call_args.args
    assert '--delete' in cmd
    assert _rsync_ziel(cmd) == str(tmp_path / "ZERODOX-deploy")
    assert _rsync_ziel(cmd) != str(projekt['path']), (
        "Rollback mit --delete auf den Arbeitsbaum leert die Claude-Worktrees "
        "(ZERODOX#3515)."
    )


@pytest.mark.asyncio
async def test_backup_sichert_den_deploy_baum(mgr, tmp_path):
    projekt = _projekt(tmp_path)
    mock_exec = AsyncMock(return_value=_MockProcess(0))

    with patch("shutil.which", return_value="/usr/bin/rsync"), \
         patch("asyncio.create_subprocess_exec", new=mock_exec):
        await DeploymentManager._create_backup(mgr, projekt)

    cmd = mock_exec.call_args.args
    assert _rsync_quelle(cmd) == str(tmp_path / "ZERODOX-deploy")


@pytest.mark.asyncio
async def test_ohne_deploy_baum_bleibt_alles_wie_bisher(mgr, tmp_path):
    """No-op für Projekte ohne `deploy_path`."""
    projekt = _projekt(tmp_path, mit_deploy_baum=False)
    backup = tmp_path / "backups" / "zerodox_x"
    mock_exec = AsyncMock(return_value=_MockProcess(0))

    with patch("shutil.which", return_value="/usr/bin/rsync"), \
         patch("asyncio.create_subprocess_exec", new=mock_exec):
        await DeploymentManager._rollback(mgr, projekt, backup)

    assert _rsync_ziel(mock_exec.call_args.args) == str(projekt['path'])


@pytest.mark.asyncio
async def test_python_rollback_ohne_rsync_verschont_den_arbeitsbaum(mgr, tmp_path):
    """Der Fallback ohne rsync löscht per `_purge_project_path` — dasselbe Ziel."""
    projekt = _projekt(tmp_path)
    worktree_datei = projekt['path'] / ".claude" / "worktrees" / "agent-x" / "web" / "src" / "a.ts"
    worktree_datei.parent.mkdir(parents=True)
    worktree_datei.write_text("export const a = 1;\n")
    backup = tmp_path / "backups" / "zerodox_x"
    (backup / "README.md").write_text("alt\n")
    mgr._send_deployment_update = AsyncMock()

    with patch("shutil.which", return_value=None):
        await DeploymentManager._rollback(mgr, projekt, backup)

    assert worktree_datei.exists(), "Arbeitsbaum darf der Rollback nie anfassen."
    assert (tmp_path / "ZERODOX-deploy" / "README.md").read_text() == "alt\n"
