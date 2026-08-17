"""Tests fuer die Diagnose fehlgeschlagener Deploys (ZERODOX-Deploy-Haertung A).

Symptom (2026-08-17): Vier Deploy-Fehlschlaege an einem Tag, und im Bot-Log
stand jedes Mal ausschliesslich

    ⚠️ Deployment fehlgeschlagen: ZERODOX

Kein Exit-Code, kein Guard-Name, keine Ausgabe von deploy.sh. `deploy_project()`
liefert den Grund als `result['error']` zurueck — `_trigger_deployment` hat ihn
verworfen. Ohne Grund ist ein Fehlschlag nicht von einer Infrastrukturstoerung
unterscheidbar; genau das laesst wiederholte Abbrueche wie ein Systemversagen
aussehen, obwohl jeder einzelne eine konkrete Ursache hat.
"""
import logging

import pytest

from src.integrations.github_integration.ci_mixin import CIMixin
from src.integrations.github_integration.state_mixin import StateMixin


class _StubDeploymentManager:
    """Liefert ein vorgegebenes deploy_project()-Ergebnis zurueck."""

    def __init__(self, result: dict):
        self._result = result
        self.calls: list = []

    async def deploy_project(self, project_name: str, branch: str) -> dict:
        self.calls.append((project_name, branch))
        return self._result


class _StubConfig:
    def __init__(self, projects: dict):
        self.projects = projects


class _DeployHarness(CIMixin, StateMixin):
    """Minimaler Harness fuer _trigger_deployment.

    repo_full_name/full_sha werden von den Tests bewusst NICHT gesetzt, damit
    der Backward-Compat-Pfad greift und das CI-Warten uebersprungen wird — hier
    geht es ausschliesslich um die Diagnose nach dem Deploy-Aufruf.
    """

    def __init__(self, deploy_result: dict, projects: dict | None = None):
        self.logger = logging.getLogger("test-deploy-diagnostics")
        self.deployment_manager = _StubDeploymentManager(deploy_result)
        self.config = _StubConfig(projects if projects is not None else {})
        self.repoll_calls: list = []

    async def _repoll_after_deploy(self, **kwargs):
        self.repoll_calls.append(kwargs)


@pytest.mark.asyncio
async def test_failed_deploy_logs_the_reason(caplog):
    """Der von deploy_project gemeldete Grund MUSS im Log landen.

    Ohne ihn ist der Fehlschlag nicht diagnostizierbar (Original-Defekt).
    """
    reason = "Post-deploy command failed (exit=1): stdout: ✗ GitHub-CI rot: 1 Check(s) failed"
    h = _DeployHarness({'success': False, 'error': reason})

    with caplog.at_level(logging.WARNING, logger="test-deploy-diagnostics"):
        await h._trigger_deployment(
            repo_name="ZERODOX",
            branch="main",
            commit_sha="9683bbb",
        )

    assert h.deployment_manager.calls == [("ZERODOX", "main")]
    assert reason in caplog.text, (
        "Der Fehlschlag-Grund fehlt im Log — der Deploy scheitert stumm."
    )


@pytest.mark.asyncio
async def test_failed_deploy_without_reason_says_so_explicitly(caplog):
    """Liefert deploy_project keinen Grund, muss das Log das benennen.

    Sonst ist "Grund fehlt in der Antwort" von "Grund wurde verworfen" nicht
    unterscheidbar — und man sucht den Fehler an der falschen Stelle.
    """
    h = _DeployHarness({'success': False})

    with caplog.at_level(logging.WARNING, logger="test-deploy-diagnostics"):
        await h._trigger_deployment(
            repo_name="ZERODOX",
            branch="main",
            commit_sha="9683bbb",
        )

    assert "ohne Fehlergrund" in caplog.text, (
        "Ein Fehlschlag ohne Grund muss als solcher gekennzeichnet sein."
    )


@pytest.mark.asyncio
async def test_successful_deploy_logs_no_failure(caplog):
    """Gegenprobe: der Erfolgspfad darf keine Fehlermeldung erzeugen."""
    h = _DeployHarness({'success': True})

    with caplog.at_level(logging.WARNING, logger="test-deploy-diagnostics"):
        await h._trigger_deployment(
            repo_name="ZERODOX",
            branch="main",
            commit_sha="9683bbb",
        )

    assert "fehlgeschlagen" not in caplog.text
    assert len(h.repoll_calls) == 1
