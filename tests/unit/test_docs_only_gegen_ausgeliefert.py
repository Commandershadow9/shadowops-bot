"""Docs-only misst gegen den ausgelieferten Stand, nicht gegen den Push (ZERODOX#3391).

Am 15.09.2026 blieb Code unausgeliefert, ohne dass irgendetwas rot wurde:

    18:07-18:16  Deploy fuer 96deb876 laeuft
    18:16        Merge #3386 (CSS + Test) -> faellt in den laufenden Deploy,
                 wird vom active_deployments-Guard verworfen
    18:36        Merge #3389 (docs-only) -> Push traegt nur Dokumentation,
                 Docs-only-Kurzschluss greift, KEIN Runtime-Deploy

Der Code von 18:16 war in keinem spaeteren Push enthalten. Eine Pruefung der
Push-Commits — auch die vollstaendige Liste — kann ihn deshalb nicht finden.
Maßgeblich ist der Diff zwischen dem AUSGELIEFERTEN Stand und dem neuen HEAD.

⚠️ Der Fehler sah wie ein Erfolg aus: eine INFO-Zeile, kein Alarm.
"""
import logging

import pytest

from src.integrations.github_integration.ci_mixin import CIMixin

_LIVE_SHA = "96deb8760000000000000000000000000000aa"   # was ausgeliefert ist
_HEAD_SHA = "bc69206000000000000000000000000000000b"   # Docs-Merge als HEAD
_DOCS_COMMIT = "bc69206000000000000000000000000000000b"


class _DocsOnlyHarness(CIMixin):
    """`compare` bildet (base, head) -> Pfadliste oder None ab."""

    def __init__(self, compare, commit_files, workflow_runs=None):
        self.logger = logging.getLogger("test-docs-only-ausgeliefert")
        self._compare = compare
        self._commit_files = commit_files
        self._workflow_runs = workflow_runs or {}
        self.compare_aufrufe: list[tuple] = []
        self.commit_aufrufe: list[str] = []

    async def _fetch_compare_files(self, repo_full_name, base_sha, head_sha):
        self.compare_aufrufe.append((base_sha, head_sha))
        if callable(self._compare):
            return self._compare(base_sha, head_sha)
        return self._compare

    async def _fetch_commit_files(self, repo_full_name, sha):
        self.commit_aufrufe.append(sha)
        return self._commit_files

    async def _fetch_commit_tree_info(self, repo_full_name, sha):
        return None  # keine Tree-/PR-Wiederverwendung in diesem Test

    async def _fetch_pull_head_shas(self, repo_full_name, sha):
        return None  # fail-closed, lenkt hier nicht ab

    async def _fetch_workflow_runs_for_sha(self, repo_full_name, sha):
        return self._workflow_runs.get(sha)


class _Uhr:
    def __init__(self):
        self.jetzt = 0.0

    def monotonic(self):
        return self.jetzt

    async def sleep(self, dauer, *a, **kw):
        self.jetzt += dauer


async def _warte(h, monkeypatch, **kwargs):
    uhr = _Uhr()
    monkeypatch.setattr("asyncio.sleep", uhr.sleep)
    monkeypatch.setattr("time.monotonic", uhr.monotonic)
    return await h._wait_for_ci_completion(
        repo_full_name="Commandershadow9/ZERODOX",
        merged_sha=_HEAD_SHA,
        workflow_names=["Web Quality"],
        admin_merge_grace_min=0,
        max_wait_min=1,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_verworfener_code_verhindert_den_kurzschluss(monkeypatch, caplog):
    """Der Fall vom 15.09.: Push ist docs-only, der Diff zu live ist es nicht."""
    h = _DocsOnlyHarness(
        compare=["web/src/styles/zs-basis.css", "docs/etwas.md"],
        commit_files=["docs/etwas.md"],  # der Push allein sieht harmlos aus
        workflow_runs={_HEAD_SHA: {"workflow_runs": []}},
    )

    with caplog.at_level(logging.INFO):
        ergebnis = await _warte(
            h,
            monkeypatch,
            push_commit_shas=[_DOCS_COMMIT],
            ausgelieferter_sha=_LIVE_SHA,
        )

    assert ergebnis != "docs_only", (
        "Zwischen dem ausgelieferten Stand und HEAD liegt CSS-Code. Wird hier "
        "'docs_only' gemeldet, unterbleibt das Runtime-Deployment — genau der "
        "Vorfall vom 15.09.2026."
    )
    assert h.compare_aufrufe == [(_LIVE_SHA, _HEAD_SHA)]
    assert h.commit_aufrufe == [], (
        "Bei nutzbarem Vergleich darf die Commit-Schleife nicht mehr laufen — "
        f"sie sieht weniger, nicht mehr. Gemessen: {h.commit_aufrufe}"
    )


@pytest.mark.asyncio
async def test_echter_docs_diff_wird_weiterhin_erkannt(monkeypatch):
    """Gegenprobe: Ist auch der Diff zu live nur Doku, bleibt der Kurzschluss."""
    h = _DocsOnlyHarness(
        compare=["docs/a.md", "CLAUDE.md"],
        commit_files=["docs/a.md"],
    )

    ergebnis = await _warte(
        h, monkeypatch, push_commit_shas=[_DOCS_COMMIT], ausgelieferter_sha=_LIVE_SHA
    )

    assert ergebnis == "docs_only", (
        "Ein reiner Doku-Diff muss den Deploy weiterhin sparen — sonst kostet "
        "die Änderung bei jedem Doku-Merge einen vollen Lauf."
    )


@pytest.mark.asyncio
async def test_unlesbarer_vergleich_faellt_auf_die_push_commits_zurueck(monkeypatch):
    """Fail-safe: API-Störung darf den bisherigen Weg nicht abschneiden."""
    h = _DocsOnlyHarness(
        compare=None,  # Vergleich nicht ermittelbar
        commit_files=["docs/a.md"],
    )

    ergebnis = await _warte(
        h, monkeypatch, push_commit_shas=[_DOCS_COMMIT], ausgelieferter_sha=_LIVE_SHA
    )

    assert ergebnis == "docs_only"
    assert h.commit_aufrufe == [_DOCS_COMMIT], (
        "Ohne nutzbaren Vergleich muss die alte Commit-Prüfung greifen."
    )


@pytest.mark.asyncio
async def test_ohne_ausgelieferten_stand_bleibt_alles_wie_bisher(monkeypatch):
    """Andere Projekte liefern keinen ausgelieferten Stand — kein Verhaltenswechsel."""
    h = _DocsOnlyHarness(compare=["web/src/app/page.tsx"], commit_files=["docs/a.md"])

    ergebnis = await _warte(h, monkeypatch, push_commit_shas=[_DOCS_COMMIT])

    assert ergebnis == "docs_only"
    assert h.compare_aufrufe == [], (
        "Ohne ausgelieferten Stand darf der Vergleich gar nicht erst angefragt "
        f"werden. Gemessen: {h.compare_aufrufe}"
    )


@pytest.mark.asyncio
async def test_identischer_stand_fragt_nicht_vergeblich(monkeypatch):
    """Ist live == HEAD, gibt es nichts zu vergleichen."""
    h = _DocsOnlyHarness(compare=["egal.md"], commit_files=["docs/a.md"])

    await _warte(
        h, monkeypatch, push_commit_shas=[_DOCS_COMMIT], ausgelieferter_sha=_HEAD_SHA
    )

    assert h.compare_aufrufe == [], (
        "base == head braucht keinen API-Aufruf."
    )
