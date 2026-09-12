"""Docs-only-Check vor die Polling-Schleife ziehen (ZERODOX#3230).

Bisher läuft der Docs-only-Check erst, NACHDEM `admin_merge_grace_min`
(Default 5min) verstrichen ist -- und nur einmal, gated durch
`commit_paths_checked`. Bei einem docs-only Merge wartet der Bot dadurch
sinnlos auf einen Workflow, der wegen `paths-ignore` in web-quality.yml
strukturell nie startet (er wird erst nach 30min als "missing" gemeldet,
siehe test_ci_wait_api_stoerung.py).

Die geänderten Pfade eines Commits sind aber sofort über
`_fetch_commit_files` abfragbar und ändern sich während des Wartens nicht.
Der Check gehört deshalb VOR den Beginn der Polling-Schleife: Ist der
Commit nachweislich docs-only, sofort "docs_only" zurückgeben, ohne
überhaupt einen Workflow-Poll abzusetzen.

Zwei Dinge müssen dabei exakt erhalten bleiben (separate Tests unten):
1. Fail-closed bei API-Fehler (`_fetch_commit_files` -> None) -- siehe
   den 2026-08-17 api_unavailable-Vorfall im Docstring von
   `_wait_for_ci_completion`. None darf NIE als docs-only gelten.
2. Die 5-Minuten-Gnadenfrist für den unklaren Fall (Code-Commit ohne
   bisher sichtbaren Workflow) bleibt unverändert -- nur der
   Docs-only-Zweig wird vorgezogen.
"""
import logging
from unittest.mock import patch

import pytest

from src.integrations.github_integration.ci_mixin import CIMixin


class _DocsOnlyHarness(CIMixin):
    """Harness, der NIE einen relevanten Workflow liefert -- wie bei einem
    Commit, der von `paths-ignore` übersprungen wird."""

    def __init__(self, commit_files):
        self.logger = logging.getLogger("test-ci-wait-docs-only")
        self._commit_files = commit_files
        self.fetch_workflow_aufrufe = 0
        self.fetch_commit_files_aufrufe = 0

    async def _fetch_workflow_runs_for_sha(self, repo_full_name: str, head_sha: str):
        self.fetch_workflow_aufrufe += 1
        return {"workflow_runs": []}

    async def _fetch_commit_files(self, repo_full_name: str, sha: str):
        self.fetch_commit_files_aufrufe += 1
        return self._commit_files

    async def _fetch_commit_tree_info(self, repo_full_name: str, sha: str):
        # ZERODOX#3328 Task 4: Der pre-loop Tree-SHA-Reuse-Check (läuft NACH
        # dem hier getesteten Docs-only-Check, aber noch vor der Schleife)
        # ruft diese Methode jetzt ebenfalls unbedingt auf. None simuliert
        # "keine Tree-Info verfügbar" (fail-closed) -- dieser Harness prüft
        # den Docs-only-Vorzug, nicht Tree-Reuse, und soll den Kurzschluss
        # nie greifen lassen.
        return None


class _VirtualClock:
    """Monoton fortschreitende Test-Uhr: springt nur vor, wenn der Code
    tatsächlich (gemockt) schläft -- ohne echte Wartezeit im Testlauf.
    So lässt sich eine 5-Minuten-Gnadenfrist deterministisch simulieren,
    statt 300 echte Sekunden zu warten oder die Schleife CPU-gebunden
    leerlaufen zu lassen."""

    def __init__(self):
        self.jetzt = 0.0

    def monotonic(self):
        return self.jetzt

    async def sleep(self, dauer, *a, **kw):
        self.jetzt += dauer


@pytest.mark.asyncio
async def test_docs_only_wird_nicht_erst_nach_der_gnadenfrist_erkannt():
    """Ein nachweislich docs-only Commit darf nicht erst gepollt werden, bis
    die 5-Minuten-Gnadenfrist um ist -- der Check muss VOR der Schleife
    laufen und sofort "docs_only" liefern, ohne einen einzigen
    Workflow-Poll."""
    uhr = _VirtualClock()
    h = _DocsOnlyHarness(commit_files=["docs/foo.md", ".claude/bar.md"])

    with patch("time.monotonic", new=uhr.monotonic), \
         patch("asyncio.sleep", new=uhr.sleep):
        ergebnis = await h._wait_for_ci_completion(
            repo_full_name="Commandershadow9/ZERODOX",
            merged_sha="9683bbb2e0c67deac8e70b2ed74c96c1f56a552d",
            workflow_names=["Web Quality"],
            max_wait_min=30,
            admin_merge_grace_min=5,
            poll_interval_sec=20,
        )

    assert ergebnis == "docs_only"
    assert h.fetch_workflow_aufrufe == 0, (
        "Ein docs-only Commit darf gar nicht erst auf einen Workflow gepollt "
        f"werden -- gemessen: {h.fetch_workflow_aufrufe} Poll(s) VOR der "
        "Docs-only-Erkennung. Der Check muss vor der Schleife laufen, nicht "
        "erst nach admin_merge_grace_min."
    )


@pytest.mark.asyncio
async def test_docs_only_check_bleibt_fail_closed_bei_api_fehler():
    """Liefert _fetch_commit_files None (API-Störung), darf das NICHT als
    docs-only gelten -- sonst wiederholt sich der 2026-08-17-Vorfall in neuer
    Form. Der Code muss normal weiterwarten (hier bis zum missing-Timeout)."""
    uhr = _VirtualClock()
    h = _DocsOnlyHarness(commit_files=None)

    with patch("time.monotonic", new=uhr.monotonic), \
         patch("asyncio.sleep", new=uhr.sleep):
        ergebnis = await h._wait_for_ci_completion(
            repo_full_name="Commandershadow9/ZERODOX",
            merged_sha="9683bbb2e0c67deac8e70b2ed74c96c1f56a552d",
            workflow_names=["Web Quality"],
            max_wait_min=6,
            admin_merge_grace_min=5,
            poll_interval_sec=20,
        )

    assert ergebnis != "docs_only", (
        "_fetch_commit_files() == None ist eine API-Störung, kein Beleg für "
        "docs-only -- darf nie zu 'docs_only' führen (fail-closed)."
    )
    assert ergebnis == "missing"
    assert h.fetch_commit_files_aufrufe >= 1, (
        "Der Docs-only-Check muss trotzdem ausgeführt werden (und fail-closed "
        "erkennen, dass er nichts weiss) -- nicht komplett übersprungen werden."
    )


@pytest.mark.asyncio
async def test_unklarer_code_commit_wartet_weiterhin_die_gnadenfrist_ab():
    """Ein Code-Commit (nicht docs-only) ohne bisher sichtbaren Workflow muss
    weiterhin die volle admin_merge_grace_min abwarten, bevor er als
    'missing' gilt -- das Vorziehen betrifft NUR den Docs-only-Zweig."""
    uhr = _VirtualClock()
    h = _DocsOnlyHarness(commit_files=["src/app.py"])

    with patch("time.monotonic", new=uhr.monotonic), \
         patch("asyncio.sleep", new=uhr.sleep):
        ergebnis = await h._wait_for_ci_completion(
            repo_full_name="Commandershadow9/ZERODOX",
            merged_sha="9683bbb2e0c67deac8e70b2ed74c96c1f56a552d",
            workflow_names=["Web Quality"],
            max_wait_min=6,
            admin_merge_grace_min=5,
            poll_interval_sec=20,
        )

    assert ergebnis == "missing"
    # 5min Gnadenfrist / 20s Poll-Intervall = mindestens 15 Polls, bevor der
    # Docs-only-Check überhaupt greift -- die Gnadenfrist darf NICHT verkürzt
    # werden, nur weil der Docs-only-Zweig jetzt früher im Ablauf sitzt.
    assert h.fetch_workflow_aufrufe >= 15, (
        f"Erwartet mind. 15 Workflow-Polls während der 5min-Gnadenfrist, "
        f"gemessen: {h.fetch_workflow_aufrufe}. Die Gnadenfrist für den "
        "unklaren Commit-Fall darf sich durch das Vorziehen nicht ändern."
    )
