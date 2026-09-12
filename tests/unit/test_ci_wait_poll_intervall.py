"""Konstantes Poll-Intervall statt Exponential-Backoff (ZERODOX#2891-Kontext, Welle 9.10-Folge).

Messung (12.09.2026): Ein ZERODOX-Merge-zu-Live-Deploy dauert ~21min, davon
~12min "CI-Wait" im Bot. Der gemessene CI-Lauf selbst brauchte nur 9,4min —
die Differenz ist blinde Zeit in der Poll-Logik.

Die Laufzeit der Web-Quality-CI ist gut bekannt (8-17min, Median 16min) —
für einen derart vorhersagbaren, immer-ähnlich-langen Vorgang ist
Exponential-Backoff (60s -> 120s -> 240s -> Deckel 300s) das falsche Muster:
Es vergrößert die Blindzeit zwischen den Polls genau dann, wenn der Lauf
typischerweise fertig wird. Ein konstantes Intervall (Default 20s) hält die
Blindzeit klein und kostet bei einem 16min-Lauf nur ~48 Requests gegen
GitHubs 5000/h-Limit.

Betroffen ist NUR die Schleife in `_wait_for_ci_completion` (der kritische
Merge-zu-Deploy-Pfad). Die zweite Warteschleife im Reconcile-Codepfad
(ca. Zeilen 60-267, `ci_success_reconcile_*`-Konfig) ist ein nachträglicher
Backstop, kein kritischer Pfad, und bleibt bewusst unverändert.
"""
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.integrations.github_integration.ci_mixin import CIMixin


class _SleepSpurHarness(CIMixin):
    """Wie _WaitHarness aus test_ci_wait_api_stoerung.py, zeichnet aber jede
    an asyncio.sleep() uebergebene Dauer auf, um das Poll-Muster zu belegen."""

    def __init__(self, antworten: list):
        self.logger = logging.getLogger("test-ci-wait-poll")
        self._antworten = list(antworten)
        self.fetch_aufrufe = 0
        self.sleep_dauern: list = []

    async def _fetch_workflow_runs_for_sha(self, repo_full_name: str, head_sha: str):
        self.fetch_aufrufe += 1
        if len(self._antworten) > 1:
            return self._antworten.pop(0)
        return self._antworten[0] if self._antworten else None

    async def _fetch_commit_files(self, repo_full_name: str, sha: str):
        # ZERODOX#3230: Der pre-loop Docs-only-Check in _wait_for_ci_completion
        # ruft diese Methode jetzt IMMER vor der Schleife auf. Fixe, nicht-docs-only
        # Pfadliste, damit dieser Harness (Fokus: Poll-Intervall, nicht
        # Docs-only-Erkennung) nicht auf die echte, tokenbasierte Implementierung
        # zurückfällt (die einen GitHub-Token via _get_github_token() braucht).
        return ["src/module.py"]

    async def _fetch_commit_tree_info(self, repo_full_name: str, sha: str):
        # ZERODOX#3328 Task 4: Der pre-loop Tree-SHA-Reuse-Check ruft diese
        # Methode jetzt IMMER vor der Schleife auf. None simuliert "keine
        # Tree-Info verfügbar" (fail-closed) — dieser Harness (Fokus:
        # Poll-Intervall, nicht Tree-Reuse) soll den Kurzschluss nie greifen
        # lassen und nicht auf die echte, tokenbasierte Implementierung
        # zurückfallen (die einen GitHub-Token via _get_github_token() braucht).
        return None


def _lauf(name: str, status: str, conclusion: str | None = None) -> dict:
    return {
        "name": name,
        "path": ".github/workflows/web-quality.yml",
        "status": status,
        "conclusion": conclusion,
        "created_at": "2026-09-12T13:00:00Z",
    }


async def _warte_und_zeichne_sleeps_auf(h: _SleepSpurHarness, **kwargs):
    """Ruft die Warteschleife auf, fängt jeden asyncio.sleep()-Aufruf ab und
    haengt die (gemockte) Dauer an h.sleep_dauern, statt wirklich zu schlafen."""

    async def _fake_sleep(dauer, *a, **kw):
        h.sleep_dauern.append(dauer)

    with patch("asyncio.sleep", new=AsyncMock(side_effect=_fake_sleep)):
        return await h._wait_for_ci_completion(
            repo_full_name="Commandershadow9/ZERODOX",
            merged_sha="9683bbb2e0c67deac8e70b2ed74c96c1f56a552d",
            workflow_names=["Web Quality"],
            admin_merge_grace_min=0,
            **kwargs,
        )


@pytest.mark.asyncio
async def test_poll_intervall_bleibt_konstant_statt_zu_verdoppeln():
    """Vier aufeinanderfolgende "noch pending"-Antworten dürfen NICHT zu
    60s -> 120s -> 240s -> 300s führen, sondern zu einem gleichbleibenden
    Intervall (Default 20s)."""
    pending = _lauf("Web Quality", "in_progress")
    h = _SleepSpurHarness(
        antworten=[
            {"workflow_runs": [pending]},
            {"workflow_runs": [pending]},
            {"workflow_runs": [pending]},
            {"workflow_runs": [pending]},
            {"workflow_runs": [_lauf("Web Quality", "completed", "success")]},
        ]
    )

    ergebnis = await _warte_und_zeichne_sleeps_auf(h, max_wait_min=30)

    assert ergebnis == "success"
    assert len(h.sleep_dauern) == 4, (
        f"Erwartet 4 Polls bis zum gruenen Ergebnis, gemessen: {h.sleep_dauern}"
    )
    assert h.sleep_dauern == [20, 20, 20, 20], (
        "Das Poll-Intervall darf sich nicht verdoppeln (alter Backoff "
        f"60->120->240->300) — gemessen: {h.sleep_dauern}. Die CI-Laufzeit ist "
        "gut bekannt (8-17min), Backoff ist hier das falsche Muster."
    )


@pytest.mark.asyncio
async def test_poll_intervall_ist_ueber_parameter_konfigurierbar():
    """Der Aufrufer (`_trigger_deployment`, über `ci_wait_poll_interval_sec`
    aus project_config) muss das Intervall verändern können."""
    pending = _lauf("Web Quality", "in_progress")
    h = _SleepSpurHarness(
        antworten=[
            {"workflow_runs": [pending]},
            {"workflow_runs": [_lauf("Web Quality", "completed", "success")]},
        ]
    )

    ergebnis = await _warte_und_zeichne_sleeps_auf(
        h, max_wait_min=30, poll_interval_sec=5
    )

    assert ergebnis == "success"
    assert h.sleep_dauern == [5], (
        f"poll_interval_sec=5 hätte zu einem 5s-Poll führen müssen, "
        f"gemessen: {h.sleep_dauern}"
    )


class _ConfigWiringHarness(CIMixin):
    """Prüft den Weg project_config -> _trigger_deployment -> _wait_for_ci_completion
    (ZERODOX#3230-Nachtrag, von team-lead angefordert): Die beiden Tests oben
    belegen nur den DIREKTEN poll_interval_sec-Parameter — nicht, dass der
    Config-Schlüssel `ci_wait_poll_interval_sec` aus project_config auch
    tatsächlich an der Aufrufstelle in `_trigger_deployment` (ca. Zeile 965)
    ankommt. `self.config`/`self.deployment_manager` sind hier bewusst
    schlanke SimpleNamespace-Stellvertreter statt der echten (schwereren)
    Config-/Deployment-Manager-Klassen, weil für diesen Test nur `.projects`
    bzw. `.deploy_project` gebraucht werden."""

    def __init__(self, poll_interval_sec_config: int):
        self.logger = logging.getLogger("test-ci-wait-config-wiring")
        self.sleep_dauern: list = []
        self._fetch_aufrufe = 0
        self.config = SimpleNamespace(
            projects={
                "shadowops-bot": {
                    "ci_workflows": ["Web Quality"],
                    "ci_wait_poll_interval_sec": poll_interval_sec_config,
                }
            }
        )
        self.deployment_manager = SimpleNamespace(
            deploy_project=AsyncMock(return_value={"success": True})
        )

    async def _fetch_workflow_runs_for_sha(self, repo_full_name: str, head_sha: str):
        self._fetch_aufrufe += 1
        if self._fetch_aufrufe == 1:
            # Erster Poll: noch "in_progress", damit die Schleife tatsächlich
            # einmal asyncio.sleep(poll_interval_s) aufruft — nur so lässt
            # sich der konfigurierte Wert überhaupt beobachten. Ein sofortiges
            # "success" hätte null Sleeps und würde nichts belegen.
            return {"workflow_runs": [_lauf("Web Quality", "in_progress")]}
        return {"workflow_runs": [_lauf("Web Quality", "completed", "success")]}

    async def _fetch_commit_files(self, repo_full_name: str, sha: str):
        # Wie _SleepSpurHarness: fixe, nicht-docs-only Pfadliste, damit der
        # pre-loop Docs-only-Check (ZERODOX#3230) nicht vorzeitig "docs_only"
        # liefert und die zu prüfende Schleife samt sleep()-Aufruf entfällt.
        return ["src/module.py"]

    async def _fetch_commit_tree_info(self, repo_full_name: str, sha: str):
        # ZERODOX#3328 Task 4: Wie bei _SleepSpurHarness -- None simuliert
        # "keine Tree-Info verfügbar" (fail-closed), damit der Tree-Reuse-
        # Kurzschluss diesen (Config-Wiring-fokussierten) Test nicht vorzeitig
        # beendet und der zu prüfende sleep()-Aufruf ausbleibt.
        return None

    def _release_deploy(self, repo_name: str, full_sha: str) -> None:
        # Auf dem hier geprüfte Erfolgspfad nicht erwartet — nur defensiv
        # gestellt, falls sich das je ändert.
        pass


@pytest.mark.asyncio
async def test_project_config_key_erreicht_die_warteschleife():
    """ZERODOX#3230-Nachtrag (team-lead-Review): Belegt, dass
    `ci_wait_poll_interval_sec` aus `project_config` über `_trigger_deployment`
    tatsächlich bis zum asyncio.sleep()-Aufruf in der Warteschleife
    durchgereicht wird — nicht nur der direkte Parameter (siehe
    test_poll_intervall_ist_ueber_parameter_konfigurierbar oben, der
    poll_interval_sec direkt an _wait_for_ci_completion übergibt und damit
    die Config-Verdrahtung selbst NICHT prüft).

    Namensgebung nach Verhalten, nicht nach Ort im Quelltext (team-lead-Review,
    12.09.2026): Der Test hieß zuvor
    test_project_config_key_erreicht_den_aufrufort_bei_945 — die Zeile 945 war
    zum Zeitpunkt der Benennung der Ort des durchgereichten Werts in
    ci_mixin.py, wird aber bei der nächsten Code-Einfügung oberhalb davon
    veralten, ohne dass der Testname das anzeigt. Die Zeilennummer bleibt hier
    im Docstring als Momentaufnahme; im Testnamen zählt nur das geprüfte
    Verhalten."""
    h = _ConfigWiringHarness(poll_interval_sec_config=7)

    async def _fake_sleep(dauer, *a, **kw):
        h.sleep_dauern.append(dauer)

    with patch("asyncio.sleep", new=AsyncMock(side_effect=_fake_sleep)):
        ergebnis = await h._trigger_deployment(
            repo_name="shadowops-bot",
            branch="main",
            commit_sha="9683bbb",
            repo_full_name="Commandershadow9/ZERODOX",
            full_sha="9683bbb2e0c67deac8e70b2ed74c96c1f56a552d",
        )

    assert ergebnis == "deployed"
    assert h.sleep_dauern == [7], (
        "ci_wait_poll_interval_sec=7 aus project_config hätte bis zum "
        f"asyncio.sleep()-Aufruf durchgereicht werden müssen, gemessen: "
        f"{h.sleep_dauern}"
    )
    h.deployment_manager.deploy_project.assert_awaited_once_with(
        "shadowops-bot", "main"
    )
