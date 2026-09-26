"""Start-Abgleich: verlorene Merge-Aufträge nach einem Bot-Neustart (ZERODOX#3447).

Trifft ein Merge-Webhook in der Startphase ein, stellt GitHub ihn nicht erneut
zu. Der Abgleich vergleicht einmal nach dem Start den Deploy-Baum mit dem
Remote-HEAD von `main` und stösst bei Abweichung denselben Deploy an wie ein
Push-Webhook — über die vorhandene Per-SHA-Sperre, ohne eigene Sperre.
"""
import logging
from types import SimpleNamespace

import pytest

from src.integrations.github_integration.start_abgleich_mixin import StartAbgleichMixin

REMOTE = "b" * 40
DEPLOYED = "a" * 40


class _Harness(StartAbgleichMixin):
    def __init__(self, tmp_path, *, remote=REMOTE, deployed=DEPLOYED, aktiv=False,
                 remote_fehler=False, git_fehler=False, projekte=None):
        self.logger = logging.getLogger("test-start-abgleich")
        self.auto_deploy_enabled = True
        self.deploy_branches = ["main", "master"]
        self.start_abgleich_delay_sec = 0
        self._start_abgleich_task = None
        self._remote = remote
        self._deployed = deployed
        self._aktiv = aktiv
        self._remote_fehler = remote_fehler
        self._git_fehler = git_fehler
        self._reserviert: set = set()
        self.trigger_calls: list = []
        deploy_baum = tmp_path / "deploy"
        deploy_baum.mkdir(exist_ok=True)
        if projekte is None:
            projekte = {
                "zerodox": {
                    "enabled": True,
                    "path": str(tmp_path / "arbeitsbaum"),
                    "deploy_path": str(deploy_baum),
                    "repo_url": "https://github.com/Commandershadow9/ZERODOX",
                },
            }
        self.config = SimpleNamespace(projects=projekte)

    def _normalize_repo_name(self, repo_name):
        for key in self.config.projects:
            if key.lower() == repo_name.lower():
                return key
        return repo_name.lower()

    async def _fetch_branch_head_sha(self, repo_full_name, branch):
        if self._remote_fehler:
            raise RuntimeError("GitHub-API kaputt")
        return self._remote

    def _get_commit_sha(self, repo_path, ref):
        if self._git_fehler:
            return None
        return self._deployed

    def _deployment_is_active(self, repo_name):
        return self._aktiv

    def _reserve_deploy(self, repo_name, full_sha, ttl_sec=3600):
        key = (repo_name.lower(), full_sha)
        if key in self._reserviert:
            return False
        self._reserviert.add(key)
        return True

    async def _trigger_deployment(self, **kwargs):
        self.trigger_calls.append(kwargs)
        return "deployed"


@pytest.mark.asyncio
async def test_gleicher_stand_kein_trigger(tmp_path):
    h = _Harness(tmp_path, deployed=REMOTE)
    await h._start_abgleich(0)
    assert h.trigger_calls == []


@pytest.mark.asyncio
async def test_abweichung_ohne_aktiven_deploy_genau_ein_trigger(tmp_path):
    h = _Harness(tmp_path)
    await h._start_abgleich(0)
    assert len(h.trigger_calls) == 1
    call = h.trigger_calls[0]
    assert call["full_sha"] == REMOTE
    assert call["commit_sha"] == REMOTE[:7]
    assert call["branch"] == "main"
    assert call["repo_name"] == "ZERODOX"
    assert call["repo_full_name"] == "Commandershadow9/ZERODOX"


@pytest.mark.asyncio
async def test_abweichung_bei_aktivem_deploy_kein_trigger(tmp_path):
    h = _Harness(tmp_path, aktiv=True)
    await h._start_abgleich(0)
    assert h.trigger_calls == []


@pytest.mark.asyncio
async def test_webhook_hat_sha_schon_reserviert_kein_zweiter_deploy(tmp_path):
    h = _Harness(tmp_path)
    h._reserve_deploy("ZERODOX", REMOTE)  # Webhook war schneller
    await h._start_abgleich(0)
    assert h.trigger_calls == []


@pytest.mark.asyncio
async def test_api_fehler_kein_trigger_keine_exception(tmp_path):
    h = _Harness(tmp_path, remote_fehler=True)
    await h._start_abgleich(0)
    assert h.trigger_calls == []


@pytest.mark.asyncio
async def test_remote_unlesbar_kein_trigger(tmp_path):
    h = _Harness(tmp_path, remote=None)
    await h._start_abgleich(0)
    assert h.trigger_calls == []


@pytest.mark.asyncio
async def test_git_fehler_kein_trigger_keine_exception(tmp_path):
    h = _Harness(tmp_path, git_fehler=True)
    await h._start_abgleich(0)
    assert h.trigger_calls == []


@pytest.mark.asyncio
async def test_shadowops_bot_wird_nicht_angestossen(tmp_path):
    deploy_baum = tmp_path / "bot-deploy"
    deploy_baum.mkdir()
    projekte = {
        "shadowops-bot": {
            "enabled": True,
            "deploy_path": str(deploy_baum),
            "repo_url": "https://github.com/Commandershadow9/shadowops-bot",
        },
    }
    h = _Harness(tmp_path, projekte=projekte)
    await h._start_abgleich(0)
    assert h.trigger_calls == []


@pytest.mark.asyncio
async def test_projekt_ohne_deploy_path_wird_nicht_verglichen(tmp_path):
    # Der Arbeitsbaum `path` zeigt, was gerade ausgecheckt ist — kein Deploy-Stand.
    projekte = {
        "guildscout": {
            "enabled": True,
            "path": str(tmp_path),
            "repo_url": "https://github.com/Commandershadow9/GuildScout",
        },
    }
    h = _Harness(tmp_path, projekte=projekte)
    await h._start_abgleich(0)
    assert h.trigger_calls == []


@pytest.mark.asyncio
async def test_schedule_nur_einmal(tmp_path):
    h = _Harness(tmp_path)
    assert h.schedule_start_abgleich() is True
    assert h.schedule_start_abgleich() is False
    await h._start_abgleich_task
    assert len(h.trigger_calls) == 1


@pytest.mark.asyncio
async def test_schedule_ohne_auto_deploy_plant_nichts(tmp_path):
    h = _Harness(tmp_path)
    h.auto_deploy_enabled = False
    assert h.schedule_start_abgleich() is False
    assert h._start_abgleich_task is None
