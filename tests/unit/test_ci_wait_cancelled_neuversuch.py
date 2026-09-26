"""ZERODOX#2920: Ein "cancelled"-Lauf bei UNVERAENDERTEM Branch-Kopf ist kein
zwangslaeufiger Fehlschlag.

Hintergrund: `test_ci_wait_cancelled_ueberholt.py` deckt bereits den Fall ab,
in dem der Branch-Kopf beim Abbruch bereits weitergezogen ist (Sammel-Zug,
Ergebnis "superseded"). Dieser Testfall betrifft die Gegenprobe: Der Kopf
steht noch auf `merged_sha`, also KEIN Sammel-Zug — trotzdem wertete
`_wait_for_ci_completion` bisher jedes "cancelled" sofort als "FEHLGESCHLAGEN"
und schickte den Alert "CI FAILED (conclusion=cancelled)". Belegt: Runner-Last
kann Jobs abbrechen, ohne dass ein Test wirklich rot lief.

Neues Verhalten: Sind ALLE nicht-gruenen relevanten Laeufe "cancelled" (keiner
"failure"/"timed_out"/... darunter), wird fuer den betroffenen Lauf GENAU
EINMAL `rerun-failed-jobs` angestossen (`_rerun_cancelled_workflow_run`,
Markierung in `self._ci_cancelled_retry_versucht`) und danach normal
weitergepollt. Ein zweites "cancelled" NACH bereits erfolgtem Neuversuch
bleibt "failure" wie bisher, nur mit einem Hinweistext im Log. Schlaegt der
POST selbst fehl, bleibt es ebenfalls beim heutigen "failure"-Verhalten, ohne
dass eine Ausnahme nach aussen dringt.
"""
import logging
from collections import OrderedDict
from unittest.mock import AsyncMock, patch

import pytest

from src.integrations.github_integration.ci_mixin import CIMixin

MERGED_SHA = "a50e956aa9d6be6d540e2ef0c20d3ffca07323f1"


class _NeuversuchHarness(CIMixin):
    """Minimaler Harness fuer _wait_for_ci_completion mit einem
    unveraenderten Branch-Kopf (kein Sammel-Zug) und konfigurierbarer
    Run-Sequenz je Poll-Zyklus.

    `run_sequenzen` ist eine Liste von workflow_runs-Listen — ein Eintrag
    je Poll-Aufruf; nach dem letzten Eintrag wird der letzte wiederholt.
    `rerun_ergebnis` steuert, ob `_rerun_cancelled_workflow_run` Erfolg
    meldet (True) oder fehlschlaegt (False)."""

    def __init__(self, run_sequenzen, rerun_ergebnis=True, vorbelegte_retries=None):
        self.logger = logging.getLogger("test-ci-wait-cancelled-neuversuch")
        self._run_sequenzen = run_sequenzen
        self._poll_index = 0
        self._rerun_ergebnis = rerun_ergebnis
        self._rerun_aufrufe = []
        self._ci_cancelled_retry_versucht = vorbelegte_retries or OrderedDict()

    async def _fetch_workflow_runs_for_sha(self, repo_full_name: str, head_sha: str):
        idx = min(self._poll_index, len(self._run_sequenzen) - 1)
        runs = self._run_sequenzen[idx]
        self._poll_index += 1
        return {"workflow_runs": runs}

    async def _fetch_commit_files(self, repo_full_name: str, sha: str):
        return ["src/module.py"]

    async def _fetch_branch_head_sha(self, repo_full_name: str, branch: str):
        # Kopf bleibt unveraendert — kein Sammel-Zug-Fall.
        return MERGED_SHA

    async def _rerun_cancelled_workflow_run(self, repo_full_name: str, run_id) -> bool:
        self._rerun_aufrufe.append((repo_full_name, run_id))
        return self._rerun_ergebnis


def _run(name: str, conclusion: str, run_id: int = 1):
    return {
        "name": name,
        "path": f".github/workflows/{name.lower().replace(' ', '-')}.yml",
        "status": "completed",
        "conclusion": conclusion,
        "created_at": "2026-09-26T01:49:00Z",
        "id": run_id,
    }


async def _warte(h: _NeuversuchHarness, **kwargs):
    with patch("asyncio.sleep", new=AsyncMock(return_value=None)):
        return await h._wait_for_ci_completion(
            repo_full_name="Commandershadow9/ZERODOX",
            merged_sha=MERGED_SHA,
            workflow_names=["Web Quality"],
            max_wait_min=1,
            admin_merge_grace_min=0,
            branch="main",
            **kwargs,
        )


@pytest.mark.asyncio
async def test_cancelled_bei_unveraendertem_kopf_loest_neuversuch_aus_dann_success():
    """(a) cancelled + unveraenderter Kopf → POST feuert einmal, danach
    liefert der naechste Poll einen gruenen Lauf → Gesamtergebnis 'success'."""
    h = _NeuversuchHarness(
        run_sequenzen=[
            [_run("Web Quality", "cancelled", run_id=1)],
            [_run("Web Quality", "success", run_id=1)],
        ],
        rerun_ergebnis=True,
    )

    ergebnis = await _warte(h)

    assert ergebnis == "success", (
        f"Nach erfolgreichem Neuversuch und gruenem Folgelauf muss 'success' "
        f"stehen, gemessen: {ergebnis}"
    )
    assert len(h._rerun_aufrufe) == 1, (
        f"Der Neuversuch muss GENAU EINMAL ausgeloest werden, gemessen: "
        f"{len(h._rerun_aufrufe)}"
    )
    retry_key = f"Commandershadow9/ZERODOX:{MERGED_SHA}:1"
    assert retry_key in h._ci_cancelled_retry_versucht, (
        "Der erfolgreiche Neuversuch muss in der Markierung stehen."
    )


@pytest.mark.asyncio
async def test_zweites_cancelled_nach_bereits_erfolgtem_neuversuch_bleibt_failure():
    """(b) Ein zweites 'cancelled' fuer denselben Lauf, NACHDEM der Neuversuch
    bereits erfolgt ist → 'failure' mit Hinweistext auf den bereits erfolgten
    Neuversuch (kein zweiter POST)."""
    retry_key = f"Commandershadow9/ZERODOX:{MERGED_SHA}:1"
    h = _NeuversuchHarness(
        run_sequenzen=[[_run("Web Quality", "cancelled", run_id=1)]],
        rerun_ergebnis=True,
        vorbelegte_retries=OrderedDict({retry_key: True}),
    )

    with patch.object(h.logger, "warning") as mock_warning:
        ergebnis = await _warte(h)

    assert ergebnis == "failure", (
        f"Ein zweites 'cancelled' nach bereits erfolgtem Neuversuch muss "
        f"'failure' bleiben, gemessen: {ergebnis}"
    )
    assert len(h._rerun_aufrufe) == 0, (
        "Es darf KEIN zweiter Neuversuch fuer denselben Lauf ausgeloest werden."
    )
    hinweis_gefunden = any(
        "bereits erfolgt" in str(call.args[0]) if call.args else False
        for call in mock_warning.call_args_list
    )
    assert hinweis_gefunden, (
        "Der Log-Text muss erklaeren, dass ein automatischer Neuversuch "
        "bereits erfolgt ist, statt einfach nur 'FAILED' zu melden."
    )


@pytest.mark.asyncio
async def test_echter_fehlschlag_loest_keinen_neuversuch_aus():
    """(c) 'failure' (kein 'cancelled') → kein POST, 'failure' exakt wie
    bisher."""
    h = _NeuversuchHarness(
        run_sequenzen=[[_run("Web Quality", "failure", run_id=1)]],
        rerun_ergebnis=True,
    )

    ergebnis = await _warte(h)

    assert ergebnis == "failure", f"Ein echter Fehlschlag bleibt 'failure', gemessen: {ergebnis}"
    assert len(h._rerun_aufrufe) == 0, (
        "Ein echter Fehlschlag (conclusion=failure) darf NIE einen "
        "automatischen Neuversuch ausloesen."
    )


@pytest.mark.asyncio
async def test_fehlschlagender_neuversuch_post_bleibt_failure():
    """(d) Der POST fuer den Neuversuch selbst schlaegt fehl →
    'failure' exakt wie im bisherigen Verhalten, keine Ausnahme nach aussen."""
    h = _NeuversuchHarness(
        run_sequenzen=[[_run("Web Quality", "cancelled", run_id=1)]],
        rerun_ergebnis=False,
    )

    ergebnis = await _warte(h)

    assert ergebnis == "failure", (
        f"Schlaegt der Neuversuch-POST fehl, bleibt es beim heutigen "
        f"Verhalten 'failure', gemessen: {ergebnis}"
    )
    assert len(h._rerun_aufrufe) == 1, (
        "Der Neuversuch muss versucht worden sein (sonst waere dieser Test "
        "keine Probe auf den Fehlerfall)."
    )
    # Ein fehlgeschlagener POST darf NICHT als 'schon versucht' markiert
    # werden — sonst bekaeme ein spaeterer echter Neuversuch nie mehr eine
    # Chance.
    retry_key = f"Commandershadow9/ZERODOX:{MERGED_SHA}:1"
    assert retry_key not in h._ci_cancelled_retry_versucht, (
        "Ein fehlgeschlagener POST darf die Markierung nicht setzen."
    )


def _run_versuch(conclusion: str, versuch: int, status: str = "completed"):
    lauf = _run("Web Quality", conclusion, run_id=1)
    lauf["status"] = status
    lauf["run_attempt"] = versuch
    return lauf


@pytest.mark.asyncio
async def test_veraltete_listenantwort_direkt_nach_neuversuch_ist_kein_failure():
    """(e) Race: Direkt nach `rerun-failed-jobs` liefert die Listenabfrage noch
    den ALTEN Versuch (run_attempt=1, cancelled). Das ist kein endgueltiges
    'failure' — weiter pollen, bis Versuch 2 gruen durch ist."""
    h = _NeuversuchHarness(
        run_sequenzen=[
            [_run_versuch("cancelled", 1)],
            [_run_versuch("cancelled", 1)],  # veraltet
            [_run_versuch(None, 2, status="in_progress")],
            [_run_versuch("success", 2)],
        ],
        rerun_ergebnis=True,
    )

    ergebnis = await _warte(h)

    assert ergebnis == "success", f"Veraltete Antwort darf nicht FAILED sein, gemessen: {ergebnis}"
    assert len(h._rerun_aufrufe) == 1, "Genau ein Neuversuch erwartet."
    retry_key = f"Commandershadow9/ZERODOX:{MERGED_SHA}:1"
    assert h._ci_cancelled_retry_versucht[retry_key] == 1, (
        "Gemerkt werden muss der run_attempt zum Zeitpunkt des Neuversuchs."
    )


@pytest.mark.asyncio
async def test_hoeherer_versuch_erneut_cancelled_bleibt_failure():
    """(f) Versuch 2 (run_attempt > gemerkt) ist wieder 'cancelled' →
    endgueltig 'failure' mit dem Hinweis auf den bereits erfolgten Neuversuch."""
    h = _NeuversuchHarness(
        run_sequenzen=[
            [_run_versuch("cancelled", 1)],
            [_run_versuch("cancelled", 1)],  # veraltet
            [_run_versuch("cancelled", 2)],
        ],
        rerun_ergebnis=True,
    )

    with patch.object(h.logger, "warning") as mock_warning:
        ergebnis = await _warte(h)

    assert ergebnis == "failure", f"gemessen: {ergebnis}"
    assert len(h._rerun_aufrufe) == 1
    assert any(
        "bereits erfolgt" in str(c.args[0]) for c in mock_warning.call_args_list if c.args
    )


# --- Review-Nacharbeit (#2920): Kopf vor dem POST, Schalter, 403-Hinweis ---


class _KopfFolgeHarness(_NeuversuchHarness):
    """Wie `_NeuversuchHarness`, aber der Branch-Kopf folgt einer Liste —
    je Aufruf ein Eintrag, danach bleibt der letzte stehen."""

    def __init__(self, kopf_folge, **kwargs):
        super().__init__(**kwargs)
        self._kopf_folge = kopf_folge
        self._kopf_aufrufe = 0

    async def _fetch_branch_head_sha(self, repo_full_name: str, branch: str):
        idx = min(self._kopf_aufrufe, len(self._kopf_folge) - 1)
        self._kopf_aufrufe += 1
        return self._kopf_folge[idx]


NEUER_SHA = "f" * 40


@pytest.mark.asyncio
async def test_kopf_vor_post_weitergerueckt_ist_superseded_ohne_post():
    """Zwischen der ersten Kopf-Abfrage und dem POST rückt main weiter →
    'superseded', KEIN Neuversuch (er bräche den Lauf des neueren Merges ab)."""
    h = _KopfFolgeHarness(
        kopf_folge=[MERGED_SHA, NEUER_SHA],
        run_sequenzen=[[_run("Web Quality", "cancelled", run_id=1)]],
    )

    ergebnis = await _warte(h)

    assert ergebnis == "superseded", f"gemessen: {ergebnis}"
    assert h._rerun_aufrufe == [], "Bei weitergerücktem Kopf darf kein POST feuern."


@pytest.mark.asyncio
async def test_kopf_vor_post_unlesbar_kein_post_bleibt_failure():
    """Kopf vor dem POST nicht lesbar (None) → kein POST, heutiges 'failure'."""
    h = _KopfFolgeHarness(
        kopf_folge=[MERGED_SHA, None],
        run_sequenzen=[[_run("Web Quality", "cancelled", run_id=1)]],
    )

    ergebnis = await _warte(h)

    assert ergebnis == "failure", f"gemessen: {ergebnis}"
    assert h._rerun_aufrufe == [], "Ohne lesbaren Kopf darf kein POST feuern."


@pytest.mark.asyncio
async def test_gruener_neuversuch_bei_weitergeruecktem_kopf_ist_superseded():
    """Neuversuch erfolgt, Folgelauf grün — aber main ist inzwischen weiter →
    'superseded' statt 'success' (kein alter Stand wird ausgeliefert)."""
    h = _KopfFolgeHarness(
        kopf_folge=[MERGED_SHA, MERGED_SHA, NEUER_SHA],
        run_sequenzen=[
            [_run("Web Quality", "cancelled", run_id=1)],
            [_run("Web Quality", "success", run_id=1)],
        ],
    )

    ergebnis = await _warte(h)

    assert ergebnis == "superseded", f"gemessen: {ergebnis}"
    assert len(h._rerun_aufrufe) == 1


@pytest.mark.asyncio
async def test_schalter_aus_kein_neuversuch():
    """`ci_wait_cancelled_retry: false` → Verhalten vor #2920: kein POST, 'failure'."""
    h = _NeuversuchHarness(
        run_sequenzen=[[_run("Web Quality", "cancelled", run_id=1)]],
    )

    ergebnis = await _warte(h, cancelled_retry_enabled=False)

    assert ergebnis == "failure", f"gemessen: {ergebnis}"
    assert h._rerun_aufrufe == [], "Bei abgeschaltetem Schalter darf kein POST feuern."


class _FakeResp:
    def __init__(self, status, body=""):
        self.status = status
        self._body = body

    async def text(self):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakeSession:
    def __init__(self, status, *a, **kw):
        self._status = status

    def post(self, url, timeout=None):
        return _FakeResp(self._status, '{"message":"Resource not accessible by integration"}')

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 404])
async def test_403_warnung_nennt_actions_write(status, caplog):
    """403/404 beim POST → Warnung benennt das fehlende Recht actions:write."""

    class _Echt(CIMixin):
        def __init__(self):
            self.logger = logging.getLogger("test-ci-wait-403")

        def _get_github_token(self):
            return "x"

    h = _Echt()
    with patch(
        "src.integrations.github_integration.ci_mixin.aiohttp.ClientSession",
        new=lambda *a, **kw: _FakeSession(status),
    ), caplog.at_level(logging.WARNING, logger="test-ci-wait-403"):
        erfolg = await h._rerun_cancelled_workflow_run("Commandershadow9/ZERODOX", 42)

    assert erfolg is False
    assert "actions:write" in caplog.text, caplog.text
