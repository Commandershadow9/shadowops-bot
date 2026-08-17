"""Eine unerreichbare GitHub-API ist nicht dasselbe wie eine fehlende CI.

Vorfall 17.08.2026: GitHub hatte einen Major Outage (API Requests:
major_outage). Der Bot protokollierte minutenlang

    ⚠️ Branch-HEAD fuer Commandershadow9/ZERODOX/main nicht lesbar (HTTP 404)
    ⚠️ Workflow Runs fuer Commandershadow9/ZERODOX@9683bbb ... (404)

und schloss um 16:09 Uhr mit

    🛑 _wait_for_ci_completion: CI FEHLT nach 30min fuer ...@9683bbb

Die CI war zu diesem Zeitpunkt laengst gelaufen — der Bot konnte sie nur nicht
sehen. Die Poll-Schleife behandelt einen API-Fehler bereits richtig (sie wartet
weiter), aber der Ausgang danach unterschlaegt den Unterschied: `missing`
bedeutet "fuer diesen Commit existiert kein Workflow" und loest einen Alert mit
genau dieser Aussage aus. Bei einer Stoerung ist das schlicht falsch, und da
`missing` als endgueltiges Urteil gilt, versucht es niemand erneut.

Das ist dieselbe Fehlerklasse wie in `deploy.sh` (dort: `jq`-Fehler beendete die
Wartezeit) und wie vor #2316 (dort: `pending` galt wie `failure`) — ein
voruebergehender Zustand wird als endgueltiger gewertet.
"""
import logging
from unittest.mock import AsyncMock, patch

import pytest

from src.integrations.github_integration.ci_mixin import CIMixin


class _WaitHarness(CIMixin):
    """Minimaler Harness fuer _wait_for_ci_completion.

    `antworten` ist die Folge der Rueckgaben von _fetch_workflow_runs_for_sha;
    der letzte Eintrag wiederholt sich, bis die Frist ablaeuft.
    """

    def __init__(self, antworten: list, commit_files=None):
        self.logger = logging.getLogger("test-ci-wait")
        self._antworten = list(antworten)
        self._commit_files = commit_files
        self.fetch_aufrufe = 0

    async def _fetch_workflow_runs_for_sha(self, repo_full_name: str, head_sha: str):
        self.fetch_aufrufe += 1
        if len(self._antworten) > 1:
            return self._antworten.pop(0)
        return self._antworten[0] if self._antworten else None

    async def _fetch_commit_files(self, repo_full_name: str, sha: str):
        return self._commit_files


def _lauf(name: str, status: str, conclusion: str | None = None) -> dict:
    return {
        "name": name,
        "path": ".github/workflows/web-quality.yml",
        "status": status,
        "conclusion": conclusion,
        "created_at": "2026-08-17T13:00:00Z",
    }


async def _warte(h: _WaitHarness, max_wait_min: float = 0.05):
    """Ruft die Warteschleife mit gekappter Frist und ohne echtes Schlafen."""
    with patch("asyncio.sleep", new=AsyncMock(return_value=None)):
        return await h._wait_for_ci_completion(
            repo_full_name="Commandershadow9/ZERODOX",
            merged_sha="9683bbb2e0c67deac8e70b2ed74c96c1f56a552d",
            workflow_names=["Web Quality"],
            max_wait_min=max_wait_min,
            admin_merge_grace_min=0,
        )


@pytest.mark.asyncio
async def test_dauerhafte_api_stoerung_meldet_nicht_missing():
    """Antwortet die API waehrend der gesamten Frist nicht, ist die CI-Lage
    unbekannt — nicht "es gibt keine CI"."""
    h = _WaitHarness(antworten=[None])

    ergebnis = await _warte(h)

    assert h.fetch_aufrufe > 0, "Die Schleife hat gar nicht abgefragt."
    assert ergebnis != "missing", (
        "Eine reine API-Stoerung darf nicht als fehlende CI gemeldet werden — "
        "der Alert behauptet sonst etwas Falsches und niemand versucht es erneut."
    )
    assert ergebnis == "api_unavailable"


@pytest.mark.asyncio
async def test_erreichbare_api_ohne_workflows_bleibt_missing():
    """Gegenprobe: Antwortet die API und kennt wirklich keinen Lauf, bleibt es
    beim bisherigen Urteil. Sonst waere der Unterschied nur verschoben."""
    h = _WaitHarness(antworten=[{"workflow_runs": []}])

    ergebnis = await _warte(h)

    assert ergebnis == "missing"


@pytest.mark.asyncio
async def test_stoerung_am_anfang_verhindert_kein_erfolgreiches_ergebnis():
    """Vereinzelte Aussetzer duerfen das Ergebnis nicht faerben: Kommt nach
    zwei Fehlversuchen eine gruene Antwort, gilt die."""
    h = _WaitHarness(
        antworten=[
            None,
            None,
            {"workflow_runs": [_lauf("Web Quality", "completed", "success")]},
        ]
    )

    ergebnis = await _warte(h, max_wait_min=0.2)

    assert ergebnis == "success"


@pytest.mark.asyncio
async def test_stoerung_faerbt_kein_rotes_ergebnis_um():
    """Und umgekehrt: ein roter Lauf bleibt rot, auch wenn davor die API hakte."""
    h = _WaitHarness(
        antworten=[
            None,
            {"workflow_runs": [_lauf("Web Quality", "completed", "failure")]},
        ]
    )

    ergebnis = await _warte(h, max_wait_min=0.2)

    assert ergebnis == "failure"


# ---------------------------------------------------------------------------
# Der neue Ausgang muss beim Aufrufer ankommen. Ein Rueckgabewert, den niemand
# auswertet, faellt in Python stillschweigend in den Erfolgszweig — hier waere
# das ein Deploy ohne jede CI-Pruefung, also schlimmer als der Ausgangszustand.
# ---------------------------------------------------------------------------


class _StubDeploymentManager:
    def __init__(self):
        self.calls: list = []

    async def deploy_project(self, project_name: str, branch: str) -> dict:
        self.calls.append((project_name, branch))
        return {'success': True}


class _StubConfig:
    def __init__(self, projects: dict):
        self.projects = projects


class _TriggerHarness(CIMixin):
    """Harness fuer _trigger_deployment mit vorgegebenem CI-Ausgang."""

    def __init__(self, ci_outcome: str):
        self.logger = logging.getLogger("test-ci-trigger")
        self.deployment_manager = _StubDeploymentManager()
        self.config = _StubConfig({
            "ZERODOX": {"deploy": {"enabled": True}, "ci_workflows": ["Web Quality"]},
        })
        self._ci_outcome = ci_outcome
        self.alerts: list = []
        self.freigaben: list = []

    async def _wait_for_ci_completion(self, **kwargs):
        return self._ci_outcome

    async def _send_ci_wait_alert(self, **kwargs):
        self.alerts.append(kwargs)

    def _release_deploy(self, repo_name: str, sha: str):
        self.freigaben.append((repo_name, sha))

    async def _repoll_after_deploy(self, **kwargs):
        pass


async def _ausloesen(h: _TriggerHarness):
    await h._trigger_deployment(
        repo_name="ZERODOX",
        branch="main",
        commit_sha="9683bbb",
        repo_full_name="Commandershadow9/ZERODOX",
        full_sha="9683bbb2e0c67deac8e70b2ed74c96c1f56a552d",
    )


@pytest.mark.asyncio
async def test_api_unavailable_loest_keinen_deploy_aus():
    """Ist die CI-Lage unbekannt, wird nicht deployt — fail-closed."""
    h = _TriggerHarness("api_unavailable")

    await _ausloesen(h)

    assert h.deployment_manager.calls == [], (
        "Bei unbekannter CI-Lage darf kein Deploy starten. Faellt der neue "
        "Rueckgabewert durch die Fallunterscheidung, landet er im Erfolgszweig."
    )
    assert h.freigaben, "Die Deploy-Reservierung muss freigegeben werden."


@pytest.mark.asyncio
async def test_api_unavailable_meldet_sich_als_stoerung():
    """Der Alert muss die Stoerung benennen, nicht eine fehlende CI."""
    h = _TriggerHarness("api_unavailable")

    await _ausloesen(h)

    assert len(h.alerts) == 1, "Eine unbekannte CI-Lage braucht eine Meldung."
    assert h.alerts[0].get("outcome") == "api_unavailable"


@pytest.mark.asyncio
async def test_gruene_ci_deployt_weiterhin():
    """Gegenprobe: der Erfolgspfad bleibt unberuehrt."""
    h = _TriggerHarness("success")

    await _ausloesen(h)

    assert h.deployment_manager.calls == [("ZERODOX", "main")]
    assert h.alerts == []
