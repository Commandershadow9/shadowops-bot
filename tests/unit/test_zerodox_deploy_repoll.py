"""Tests fuer den Re-Poll-nach-Deploy-Backstop (ZERODOX#1720, Teil 1).

Symptom: der `active_deployments`-Guard in DeploymentManager.deploy_project()
verwirft einen zweiten Push/PR-Merge, der waehrend eines laufenden Deploys
eintrifft, still ({'success': False, 'error': 'Deployment already in
progress...'}) — kein Retry, kein Alert. `_repoll_after_deploy` (ci_mixin.py)
prueft nach jedem erfolgreichen Deploy, ob origin/<branch> inzwischen weiter
ist als der gerade deployte Commit, und stoesst in diesem Fall ueber
_trigger_deployment einen weiteren normalen Deploy an (Schleifen-Schutz via
repoll_max_rounds, Dedup via _reserve_deploy).
"""
import logging

import pytest

from src.integrations.github_integration.ci_mixin import CIMixin
from src.integrations.github_integration.state_mixin import StateMixin


class _RepollHarness(CIMixin, StateMixin):
    """Minimaler Harness: _repoll_after_deploy braucht Logger, die beiden
    Git-Methoden (hier gestubbt statt echtem subprocess) und _reserve_deploy
    (echt aus StateMixin). _trigger_deployment wird gespyt statt rekursiv
    echt auszufuehren."""

    def __init__(self, shas: dict, fetch_ok: bool = True):
        self.logger = logging.getLogger("test-repoll")
        self._shas = shas  # {"HEAD": sha, "origin/main": sha}
        self._fetch_ok = fetch_ok
        self.trigger_calls: list = []

    def _normalize_repo_name(self, name: str) -> str:
        return name.lower().replace("-", "_")

    def _safe_git_fetch(self, repo_path) -> bool:
        return self._fetch_ok

    def _get_commit_sha(self, repo_path, ref: str):
        return self._shas.get(ref)

    async def _trigger_deployment(self, **kwargs):
        self.trigger_calls.append(kwargs)


def _project_config(path, **deploy_overrides) -> dict:
    return {"path": str(path), "deploy": deploy_overrides}


@pytest.mark.asyncio
async def test_repoll_noop_when_shas_equal(tmp_path):
    h = _RepollHarness(shas={"HEAD": "aaa1111", "origin/main": "aaa1111"})
    await h._repoll_after_deploy(
        repo_name="ZERODOX",
        branch="main",
        project_config=_project_config(tmp_path),
        repo_full_name="Commandershadow9/ZERODOX",
        repoll_round=0,
    )
    assert h.trigger_calls == []


@pytest.mark.asyncio
async def test_repoll_triggers_when_remote_ahead(tmp_path):
    h = _RepollHarness(shas={"HEAD": "aaa1111", "origin/main": "bbb2222"})
    await h._repoll_after_deploy(
        repo_name="ZERODOX",
        branch="main",
        project_config=_project_config(tmp_path),
        repo_full_name="Commandershadow9/ZERODOX",
        repoll_round=0,
    )
    assert len(h.trigger_calls) == 1
    call = h.trigger_calls[0]
    assert call["repo_name"] == "ZERODOX"
    assert call["full_sha"] == "bbb2222"
    assert call["commit_sha"] == "bbb2222"[:7]
    assert call["_repoll_round"] == 1
    assert call["repo_full_name"] == "Commandershadow9/ZERODOX"


@pytest.mark.asyncio
async def test_repoll_respects_max_rounds(tmp_path):
    h = _RepollHarness(shas={"HEAD": "aaa1111", "origin/main": "bbb2222"})
    await h._repoll_after_deploy(
        repo_name="ZERODOX",
        branch="main",
        project_config=_project_config(tmp_path, repoll_max_rounds=2),
        repo_full_name="Commandershadow9/ZERODOX",
        repoll_round=2,  # bereits am Limit
    )
    assert h.trigger_calls == []


@pytest.mark.asyncio
async def test_repoll_disabled_via_config(tmp_path):
    h = _RepollHarness(shas={"HEAD": "aaa1111", "origin/main": "bbb2222"})
    await h._repoll_after_deploy(
        repo_name="ZERODOX",
        branch="main",
        project_config=_project_config(tmp_path, repoll_enabled=False),
        repo_full_name="Commandershadow9/ZERODOX",
        repoll_round=0,
    )
    assert h.trigger_calls == []


@pytest.mark.asyncio
async def test_repoll_noop_without_project_config():
    h = _RepollHarness(shas={"HEAD": "aaa1111", "origin/main": "bbb2222"})
    await h._repoll_after_deploy(
        repo_name="ZERODOX",
        branch="main",
        project_config=None,
        repo_full_name="Commandershadow9/ZERODOX",
        repoll_round=0,
    )
    assert h.trigger_calls == []


@pytest.mark.asyncio
async def test_repoll_noop_without_path():
    h = _RepollHarness(shas={"HEAD": "aaa1111", "origin/main": "bbb2222"})
    await h._repoll_after_deploy(
        repo_name="ZERODOX",
        branch="main",
        project_config={"deploy": {}},  # kein 'path'-Key
        repo_full_name="Commandershadow9/ZERODOX",
        repoll_round=0,
    )
    assert h.trigger_calls == []


@pytest.mark.asyncio
async def test_repoll_noop_when_path_missing(tmp_path):
    missing = tmp_path / "does-not-exist"
    h = _RepollHarness(shas={"HEAD": "aaa1111", "origin/main": "bbb2222"})
    await h._repoll_after_deploy(
        repo_name="ZERODOX",
        branch="main",
        project_config=_project_config(missing),
        repo_full_name="Commandershadow9/ZERODOX",
        repoll_round=0,
    )
    assert h.trigger_calls == []


@pytest.mark.asyncio
async def test_repoll_noop_when_git_fetch_fails(tmp_path):
    h = _RepollHarness(shas={"HEAD": "aaa1111", "origin/main": "bbb2222"}, fetch_ok=False)
    await h._repoll_after_deploy(
        repo_name="ZERODOX",
        branch="main",
        project_config=_project_config(tmp_path),
        repo_full_name="Commandershadow9/ZERODOX",
        repoll_round=0,
    )
    assert h.trigger_calls == []


@pytest.mark.asyncio
async def test_repoll_dedup_via_reserve_deploy(tmp_path):
    """Ein anderer Trigger (z.B. ein parallel eingetroffener normaler
    Webhook fuer denselben Ziel-Commit) hat den SHA schon reserviert -> der
    Re-Poll darf NICHT nochmal deployen (sonst Doppel-Deploy)."""
    h = _RepollHarness(shas={"HEAD": "aaa1111", "origin/main": "bbb2222"})
    assert h._reserve_deploy("ZERODOX", "bbb2222") is True  # simuliert Fremd-Reservierung
    await h._repoll_after_deploy(
        repo_name="ZERODOX",
        branch="main",
        project_config=_project_config(tmp_path),
        repo_full_name="Commandershadow9/ZERODOX",
        repoll_round=0,
    )
    assert h.trigger_calls == []
