"""Docs-only-Erkennung über ALLE Commits eines Pushes (ZERODOX#3391).

Vorfall 15.09.2026: Ein direkter Push auf `main` trug zwei Commits — zuerst
den Merge eines Code-PRs (CSS mehrerer Produktivseiten), danach den Merge
eines reinen Docs-PRs. `_wait_for_ci_completion` prüfte nur `merged_sha`
(den HEAD), fand dort eine einzige `docs/`-Datei und meldete "docs_only".
Der Bot deployte daraufhin mit `--skip-e2e`: Layout-Code ging live, ohne
dass ein einziger End-to-End-Test lief.

Die Ursache ist keine falsche Logik, sondern eine **andere Kardinalität**:
Ein PR-Merge bringt genau einen Commit mit, ein Push beliebig viele. Die für
den PR-Pfad korrekte Prüfung wurde im Push-Pfad unvollständig.

`push_commit_shas` reicht die Commit-Liste des Push-Events durch. Drei
Eigenschaften müssen dabei erhalten bleiben (die ersten beiden sind in
test_ci_wait_docs_only_vorzug.py festgehalten und gelten unverändert):

1. Ohne `push_commit_shas` (PR-Pfad) verhält sich alles wie bisher.
2. Fail-closed: `_fetch_commit_files() -> None` gilt NIE als docs-only.
   Neu und hier geprüft: Das gilt für JEDEN Commit der Liste, nicht nur für
   den HEAD — ein einzelner API-Aussetzer darf einen Code-Push nicht in
   einen Docs-Push verwandeln.
3. Der Kurzschluss darf keinen Workflow-Poll kosten.
"""
import logging
from unittest.mock import patch

import pytest

from src.integrations.github_integration.ci_mixin import CIMixin


CODE_SHA = "aaaaaaa1111111111111111111111111111111111"
DOCS_SHA = "bbbbbbb2222222222222222222222222222222222"


class _PushHarness(CIMixin):
    """Liefert je SHA eine eigene Dateiliste — so wie GitHub es für die
    einzelnen Commits eines Pushes täte. `None` als Wert simuliert eine
    API-Störung für genau diesen Commit."""

    def __init__(self, dateien_je_sha: dict):
        self.logger = logging.getLogger("test-ci-wait-push")
        self._dateien_je_sha = dateien_je_sha
        self.fetch_workflow_aufrufe = 0
        self.abgefragte_shas: list[str] = []

    async def _fetch_workflow_runs_for_sha(self, repo_full_name: str, head_sha: str):
        self.fetch_workflow_aufrufe += 1
        return {"workflow_runs": []}

    async def _fetch_commit_files(self, repo_full_name: str, sha: str):
        self.abgefragte_shas.append(sha)
        return self._dateien_je_sha.get(sha)

    async def _fetch_commit_tree_info(self, repo_full_name: str, sha: str):
        # Der Tree-SHA-Reuse-Kurzschluss (#3328 Task 4) läuft nach dem
        # Docs-only-Check und ist hier nicht Prüfgegenstand — None hält ihn
        # fail-closed geschlossen.
        return None


class _VirtualClock:
    """Springt nur vor, wenn der Code (gemockt) schläft — sonst liefe die
    Gnadenfrist in Echtzeit ab."""

    def __init__(self):
        self.jetzt = 0.0

    def monotonic(self):
        return self.jetzt

    async def sleep(self, dauer, *a, **kw):
        self.jetzt += dauer


async def _lauf(harness, push_commit_shas, max_wait_min=6):
    uhr = _VirtualClock()
    with patch("time.monotonic", new=uhr.monotonic), \
         patch("asyncio.sleep", new=uhr.sleep):
        return await harness._wait_for_ci_completion(
            repo_full_name="Commandershadow9/ZERODOX",
            merged_sha=DOCS_SHA,
            workflow_names=["Web Quality"],
            max_wait_min=max_wait_min,
            admin_merge_grace_min=5,
            poll_interval_sec=20,
            push_commit_shas=push_commit_shas,
        )


@pytest.mark.asyncio
async def test_push_mit_code_und_docs_commit_ist_nicht_docs_only():
    """Der eigentliche Vorfall: HEAD ist docs-only, ein früherer Commit
    desselben Pushes trägt aber Code. Der Push darf NICHT als docs-only
    gelten — sonst wird Code ohne E2E ausgeliefert."""
    h = _PushHarness({
        CODE_SHA: ["web/src/styles/zs-basis.css"],
        DOCS_SHA: ["docs/PROJECT_TIMELINE.md"],
    })

    ergebnis = await _lauf(h, [CODE_SHA, DOCS_SHA])

    assert ergebnis != "docs_only", (
        "Ein Push mit einem Code-Commit darf nie als docs-only gelten, auch "
        "wenn der HEAD-Commit selbst nur Dokumentation ändert — genau das "
        "hat am 15.09.2026 CSS-Code mit --skip-e2e ausgeliefert."
    )
    assert CODE_SHA in h.abgefragte_shas, (
        "Der Code-Commit muss abgefragt worden sein — sonst prüft der Check "
        "weiterhin nur den HEAD."
    )


@pytest.mark.asyncio
async def test_push_nur_aus_docs_commits_bleibt_docs_only():
    """Die Gegenrichtung: Tragen ALLE Commits des Pushes nur Dokumentation,
    bleibt der Kurzschluss erhalten — ohne einen einzigen Workflow-Poll."""
    h = _PushHarness({
        CODE_SHA: ["docs/ADR-0031.md"],
        DOCS_SHA: ["docs/PROJECT_TIMELINE.md", ".claude/rules/safety.md"],
    })

    ergebnis = await _lauf(h, [CODE_SHA, DOCS_SHA])

    assert ergebnis == "docs_only"
    assert h.fetch_workflow_aufrufe == 0, (
        "Ein reiner Docs-Push darf weiterhin ohne Workflow-Poll "
        f"kurzgeschlossen werden — gemessen: {h.fetch_workflow_aufrufe} Poll(s)."
    )


@pytest.mark.asyncio
async def test_ein_unlesbarer_commit_verhindert_docs_only():
    """Fail-closed über die ganze Liste: Liefert die API für EINEN Commit
    None, ist der Push unbekannt — nicht 'die übrigen waren ja docs-only'."""
    h = _PushHarness({
        CODE_SHA: None,                              # API-Störung
        DOCS_SHA: ["docs/PROJECT_TIMELINE.md"],      # für sich genommen docs-only
    })

    ergebnis = await _lauf(h, [CODE_SHA, DOCS_SHA])

    assert ergebnis != "docs_only", (
        "Ein nicht lesbarer Commit ist kein Beleg für docs-only. Sonst "
        "verwandelt ein einzelner API-Aussetzer einen Code-Push in einen "
        "vermeintlichen Docs-Push (fail-closed, vgl. Vorfall 2026-08-17)."
    )


@pytest.mark.asyncio
async def test_ohne_push_liste_unveraendertes_verhalten():
    """PR-Pfad: Ohne `push_commit_shas` wird genau ein Commit geprüft — das
    bisherige Verhalten, auf das die bestehenden Tests aufbauen."""
    h = _PushHarness({DOCS_SHA: ["docs/PROJECT_TIMELINE.md"]})

    ergebnis = await _lauf(h, None)

    assert ergebnis == "docs_only"
    assert h.abgefragte_shas == [DOCS_SHA], (
        "Ohne Push-Liste darf ausschliesslich der übergebene merged_sha "
        f"abgefragt werden — gemessen: {h.abgefragte_shas}"
    )
