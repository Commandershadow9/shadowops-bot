"""Kein 30-Minuten-Warten auf einen Workflow, der nie startet (ZERODOX#3230).

`Web Quality` hat einen `paths-ignore`-Filter. Bei einem Docs-Merge legt GitHub
den Lauf nie an. Der Bot wartete trotzdem bis zum 30-Minuten-Limit und meldete
danach "Deployment fehlgeschlagen" — fuer einen Merge, der gar keinen Deploy
brauchte.

Am 09.09.2026 kostete das ueber eine Stunde Auslieferung: Waehrend der Bot
wartete, wurden nachfolgende Merges mit "already in progress" verworfen.

"Noch nicht angelegt" und "wird nie angelegt" sehen an einem einzelnen
Check-Run gleich aus. Unterscheidbar werden sie erst ueber den GESAMTEN
Lauf-Bestand des Commits: Ist dort nichts mehr offen, hat GitHub die Events
verarbeitet — was dann fehlt, kommt nicht mehr.

⚠️ Der Ausstieg ist KEIN "darf ohne CI deployen": `deploy.sh` hat ein eigenes
Gate und entscheidet erneut.
"""
import logging

import pytest

from src.integrations.github_integration.ci_mixin import CIMixin

_SHA = "498d220000000000000000000000000000000a"


class _Harness(CIMixin):
    def __init__(self, laeuft_noch):
        self.logger = logging.getLogger("test-uebersprungener-workflow")
        self._laeuft_noch = laeuft_noch
        self.bestand_aufrufe = 0
        self.poll_runden = 0

    async def _laeuft_noch_ein_workflow(self, repo_full_name, sha):
        self.bestand_aufrufe += 1
        return self._laeuft_noch

    async def _fetch_commit_files(self, repo_full_name, sha):
        return ["web/src/app/page.tsx"]  # bewusst NICHT docs-only

    async def _fetch_compare_files(self, repo_full_name, base, head):
        return None

    async def _fetch_commit_tree_info(self, repo_full_name, sha):
        return None

    async def _fetch_pull_head_shas(self, repo_full_name, sha):
        return None

    async def _fetch_workflow_runs_for_sha(self, repo_full_name, sha):
        self.poll_runden += 1
        return {"workflow_runs": []}  # nie ein relevanter Lauf


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
        merged_sha=_SHA,
        workflow_names=["Web Quality"],
        admin_merge_grace_min=0,
        max_wait_min=30,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_kein_lauf_mehr_offen_beendet_das_warten(monkeypatch, caplog):
    """Der Fall vom 09.09.: nichts mehr unterwegs, Workflow fehlt → kein Warten."""
    h = _Harness(laeuft_noch=False)

    with caplog.at_level(logging.INFO):
        ergebnis = await _warte(h, monkeypatch)

    assert ergebnis == "no_workflows", (
        "Ist kein Lauf mehr offen und der erwartete Workflow fehlt weiterhin, "
        "wird er nicht mehr starten. Weiterzuwarten kostet 30 Minuten und "
        "endet in einem Fehlschlag, der keiner ist."
    )
    assert h.poll_runden <= 2, (
        f"Der Ausstieg muss frueh greifen — gemessen: {h.poll_runden} Poll-Runden."
    )
    text = " ".join(r.message for r in caplog.records)
    assert "Path-Filter" in text, "Die Log-Zeile muss den Grund benennen, nicht nur das Ergebnis."


@pytest.mark.asyncio
async def test_laufender_workflow_wird_weiter_abgewartet(monkeypatch):
    """Gegenprobe: Solange etwas offen ist, bleibt es beim Warten."""
    h = _Harness(laeuft_noch=True)

    ergebnis = await _warte(h, monkeypatch)

    assert ergebnis == "missing", (
        "Ein noch laufender Workflow darf NICHT als uebersprungen gelten — "
        "sonst deployt der Bot an der CI vorbei."
    )
    assert h.poll_runden > 2, "Es muss tatsaechlich gepollt worden sein."


@pytest.mark.asyncio
async def test_unklarer_bestand_wartet_weiter(monkeypatch):
    """Fail-closed: None heisst 'nicht ermittelbar', nicht 'nichts laeuft'."""
    h = _Harness(laeuft_noch=None)

    ergebnis = await _warte(h, monkeypatch)

    assert ergebnis == "missing", (
        "Eine API-Stoerung darf den Ausstieg nicht ausloesen. 'Ich kann nicht "
        "nachsehen' und 'da ist nichts' sind zwei Zustaende."
    )
