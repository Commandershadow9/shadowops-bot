"""Eine leere PR-Zuordnung heisst "noch nicht indiziert", nicht "kein PR" (ZERODOX#3328).

Am 19.09.2026 wurde ZERODOX#3458 per Squash gemergt. Zwei Sekunden spaeter
fragte der Bot `GET /commits/ae34e13/pulls` und bekam eine LEERE Liste; wenige
Minuten spaeter lieferte derselbe Endpunkt korrekt PR #3458. GitHub indiziert
die Zuordnung Commit→PR verzoegert.

Der Bot las die leere Antwort als "Direkt-Push auf main", verwarf den
Kurzschluss aus Paket A endgueltig — die Pruefung liegt VOR der Polling-Schleife
und wird nie wiederholt — und wartete danach 30 Minuten auf einen Merge-Lauf,
den es seit Paket A gar nicht mehr gibt. Ergebnis: Abbruch, Merge unausgeliefert.

⚠️ Warum das erst jetzt auffiel: Bei einem Merge-Commit greift Weg 1
(Tree-SHA-Gleichheit auf `merge^2`) OHNE diese Abfrage. Die vorangegangenen
Merges waren Merge-Commits; der erste Squash-Merge nach Paket A lief sofort
hinein. Der Fall war also nicht selten, sondern nur ungetestet.

Die Unterscheidung, um die es geht, ist dieselbe wie beim
`mcp-drift-watchdog` (#2452) und beim Sanktionslisten-Alter (#2720): "ist
nicht da" und "ich habe noch nicht nachgesehen" sind zwei Zustaende. Wer sie
zusammenwirft, baut eine Aussage auf eine Messung, die nie stattgefunden hat.
"""
import logging

import pytest

from src.integrations.github_integration.ci_mixin import (
    CIMixin,
    _PR_ZUORDNUNG_VERSUCHE,
    _PR_ZUORDNUNG_WARTE_S,
)

_SQUASH_SHA = "ae34e1380000000000000000000000000000a1"
_PARENT1_SHA = "b5976cb400000000000000000000000000000b"
_PR_HEAD_SHA = "68b06f7200000000000000000000000000000c"


def _lauf(name: str, status: str, conclusion: str | None = None) -> dict:
    return {
        "name": name,
        "path": ".github/workflows/web-quality.yml",
        "status": status,
        "conclusion": conclusion,
        "created_at": "2026-09-19T12:47:00Z",
    }


class _VerzoegerteZuordnungHarness(CIMixin):
    """`pull_antworten` ist eine Warteschlange von Antworten auf
    `_fetch_pull_head_shas` — der letzte Wert wiederholt sich."""

    def __init__(self, pull_antworten: list, workflow_runs_by_sha: dict):
        self.logger = logging.getLogger("test-pr-zuordnung-verzoegert")
        self._pull_antworten = list(pull_antworten)
        self._workflow_runs_by_sha = {
            sha: list(a) for sha, a in workflow_runs_by_sha.items()
        }
        self.pull_aufrufe: list[str] = []
        self.workflow_aufrufe: list[str] = []
        self.sleep_dauern: list = []

    async def _fetch_pull_head_shas(self, repo_full_name: str, sha: str):
        self.pull_aufrufe.append(sha)
        if len(self._pull_antworten) > 1:
            return self._pull_antworten.pop(0)
        return self._pull_antworten[0] if self._pull_antworten else None

    async def _fetch_commit_files(self, repo_full_name: str, sha: str):
        return ["web/src/module.ts"]  # bewusst NICHT docs-only

    async def _fetch_commit_tree_info(self, repo_full_name: str, sha: str):
        # Squash-Commit: genau EIN Elternteil. Weg 1 (Tree-SHA auf merge^2)
        # kann damit nicht greifen — genau der Fall vom 19.09.2026.
        if sha == _SQUASH_SHA:
            return {"tree_sha": "TREE_SQUASH", "parent_shas": [_PARENT1_SHA]}
        return None

    async def _fetch_workflow_runs_for_sha(self, repo_full_name: str, sha: str):
        self.workflow_aufrufe.append(sha)
        warteschlange = self._workflow_runs_by_sha.get(sha)
        if not warteschlange:
            return None
        if len(warteschlange) > 1:
            return warteschlange.pop(0)
        return warteschlange[0]


class _VirtualClock:
    """Beide Uhren muessen zusammen gemockt werden, sonst laeuft die
    Polling-Schleife als echte Busyloop (Fund aus test_ci_wait_tree_sha_reuse)."""

    def __init__(self):
        self.jetzt = 0.0

    def monotonic(self):
        return self.jetzt

    async def sleep(self, dauer, *a, **kw):
        self.jetzt += dauer


async def _warte(h, monkeypatch, **kwargs):
    uhr = _VirtualClock()

    async def _fake_sleep(dauer, *a, **kw):
        h.sleep_dauern.append(dauer)
        await uhr.sleep(dauer)

    monkeypatch.setattr("asyncio.sleep", _fake_sleep)
    monkeypatch.setattr("time.monotonic", uhr.monotonic)
    return await h._wait_for_ci_completion(
        repo_full_name="Commandershadow9/ZERODOX",
        merged_sha=_SQUASH_SHA,
        workflow_names=["Web Quality"],
        admin_merge_grace_min=0,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_leere_zuordnung_wird_wiederholt_und_greift_dann(monkeypatch, caplog):
    """Der Fall vom 19.09.2026: erst leer, dann der PR — der Kurzschluss greift."""
    h = _VerzoegerteZuordnungHarness(
        pull_antworten=[[], [], [_PR_HEAD_SHA]],
        workflow_runs_by_sha={
            _PR_HEAD_SHA: [{"workflow_runs": [_lauf("Web Quality", "completed", "success")]}],
        },
    )

    with caplog.at_level(logging.INFO):
        ergebnis = await _warte(h, monkeypatch, max_wait_min=30)

    assert ergebnis == "success", (
        "Nach der verzoegerten Indizierung liegt ein gruener PR-HEAD vor — der "
        "Kurzschluss muss greifen, statt 30 Minuten auf einen Merge-Lauf zu "
        "warten, den es seit Paket A nicht mehr gibt."
    )
    assert len(h.pull_aufrufe) == 3, (
        f"Erwartet: drei Abfragen bis zur Antwort. Gemessen: {h.pull_aufrufe}"
    )
    assert _SQUASH_SHA not in h.workflow_aufrufe, (
        "Auf den Squash-Commit selbst darf gar nicht gepollt werden — "
        f"gemessen: {h.workflow_aufrufe}"
    )


@pytest.mark.asyncio
async def test_dauerhaft_leer_gilt_weiterhin_als_direkt_push(monkeypatch, caplog):
    """Gegenprobe: Ein echter Direkt-Push darf NICHT ewig wiederholt werden.

    Bleibt die Antwort ueber alle Versuche leer, ist sie glaubwuerdig — dann
    gilt weiterhin "kein PR" und es wird normal gepollt. Sonst verzoegerte die
    Wiederholung jeden echten Direkt-Push-Deploy.
    """
    h = _VerzoegerteZuordnungHarness(
        pull_antworten=[[]],
        workflow_runs_by_sha={
            _SQUASH_SHA: [{"workflow_runs": [_lauf("Web Quality", "completed", "success")]}],
        },
    )

    with caplog.at_level(logging.INFO):
        ergebnis = await _warte(h, monkeypatch, max_wait_min=30)

    assert ergebnis == "success"  # ueber den normalen Poll-Pfad
    assert len(h.pull_aufrufe) == _PR_ZUORDNUNG_VERSUCHE, (
        f"Es muss genau {_PR_ZUORDNUNG_VERSUCHE}-mal gefragt werden, nicht "
        f"unbegrenzt — gemessen: {len(h.pull_aufrufe)}"
    )
    assert _SQUASH_SHA in h.workflow_aufrufe, (
        "Ohne PR muss der normale Polling-Pfad auf den Commit selbst greifen."
    )
    log_text = " ".join(r.message for r in caplog.records).lower()
    assert "direkt-push" in log_text


@pytest.mark.asyncio
async def test_api_fehler_wird_nicht_wiederholt(monkeypatch):
    """None heisst "nicht ermittelbar" und ist bereits fail-closed.

    Eine Wiederholung braechte hier nichts: Der Aufrufer wartet ohnehin normal
    weiter, und jeder zusaetzliche Versuch verzoegerte das nur.
    """
    h = _VerzoegerteZuordnungHarness(
        pull_antworten=[None],
        workflow_runs_by_sha={
            _SQUASH_SHA: [{"workflow_runs": [_lauf("Web Quality", "completed", "success")]}],
        },
    )

    ergebnis = await _warte(h, monkeypatch, max_wait_min=30)

    assert ergebnis == "success"
    assert len(h.pull_aufrufe) == 1, (
        f"Ein API-Fehler darf NICHT wiederholt werden — gemessen: {h.pull_aufrufe}"
    )


def test_wartezeit_bleibt_klein_genug_fuer_einen_direkt_push():
    """Die Obergrenze ist eine Abwaegung und darf nicht unbemerkt wachsen.

    Sie verzoegert ausschliesslich den Fall "wirklich kein PR" — in 30 Tagen
    null Mal vorgekommen (gemessen 17.09.2026). Waere sie gross, kehrte sich
    das Verhaeltnis um: Der haeufige Fall (Squash-Merge) gewaenne Sekunden,
    der seltene verloere Minuten.
    """
    gesamt = _PR_ZUORDNUNG_VERSUCHE * _PR_ZUORDNUNG_WARTE_S
    assert gesamt <= 90, (
        f"Die Wiederholung dauert bis zu {gesamt}s. Mehr als 90s verschoebe "
        "einen echten Direkt-Push-Deploy spuerbar."
    )
    assert _PR_ZUORDNUNG_VERSUCHE >= 2, "Ohne Wiederholung ist der Fix wirkungslos."
