"""Ein abgebrochener CI-Lauf ist kein Fehlschlag, wenn der Merge ihn ueberholt hat.

Hintergrund (ZERODOX#3328, Sammel-Zug seit 22.09.2026): Der Workflow "Web
Quality" laeuft auf `main` jetzt mit `cancel-in-progress: true` — ein neuerer
Merge bricht den CI-Lauf des aelteren Merges ab. Geprueft und ausgeliefert
wird nur noch der neueste Stand.

`_klassifiziere_workflow_runs` wertet `conclusion == "cancelled"` bisher IMMER
als Fehlschlag (`_CI_FAILURE_CONCLUSIONS`), egal warum abgebrochen wurde.
Belegt am 23.09.2026 01:49: zwei ueberholte Merges (a50e956, ae31de6) loesten
je einen Fehlalarm aus, obwohl der jeweils naechste Merge den Stand ohnehin
gesammelt auslieferte.

Die Unterscheidung: Steht der Branch-Kopf (`_fetch_branch_head_sha`) beim
Abbruch bereits auf einem ANDEREN SHA als dem gewarteten — der Merge wurde
ueberholt, Ergebnis "superseded", kein Alarm. Steht der Kopf noch auf
demselben SHA, war der Abbruch ein echter (z.B. manueller) Cancel und bleibt
"failure" wie bisher. Ist der Kopf nicht ermittelbar (API-Fehler), bleibt es
fail-closed bei "failure" — lieber ein Alarm zu viel als ein verschluckter
echter Fehlschlag.
"""
import logging
from unittest.mock import AsyncMock, patch

import pytest

from src.integrations.github_integration.ci_mixin import CIMixin

MERGED_SHA = "a50e956aa9d6be6d540e2ef0c20d3ffca07323f1"
NEUER_HEAD = "ae31de6cca050cf9a059273e34ea89068edfc644"


class _WaitCancelledHarness(CIMixin):
    """Minimaler Harness fuer _wait_for_ci_completion mit einem
    "cancelled"-Ergebnis und einem konfigurierbaren Branch-Kopf.

    `branch_head` ist entweder ein SHA-String (Kopf lesbar) oder `None`
    (API-Fehler beim Ermitteln des Kopfs — fail-closed-Pfad)."""

    def __init__(self, branch_head):
        self.logger = logging.getLogger("test-ci-wait-cancelled")
        self._branch_head = branch_head
        self.branch_head_aufrufe = 0

    async def _fetch_workflow_runs_for_sha(self, repo_full_name: str, head_sha: str):
        return {
            "workflow_runs": [
                {
                    "name": "Web Quality",
                    "path": ".github/workflows/web-quality.yml",
                    "status": "completed",
                    "conclusion": "cancelled",
                    "created_at": "2026-09-23T01:49:00Z",
                }
            ]
        }

    async def _fetch_commit_files(self, repo_full_name: str, sha: str):
        # Fixe, nicht-docs-only Pfadliste — dieser Harness prueft die
        # cancelled/superseded-Weiche, nicht die Docs-only-Erkennung.
        return ["src/module.py"]

    async def _fetch_branch_head_sha(self, repo_full_name: str, branch: str):
        self.branch_head_aufrufe += 1
        return self._branch_head


async def _warte(h: _WaitCancelledHarness, **kwargs):
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
async def test_cancelled_und_kopf_weitergerueckt_ist_superseded():
    """Sammel-Zug-Fall: main steht inzwischen auf einem neueren Commit —
    kein Fehler, sondern ueberholt."""
    h = _WaitCancelledHarness(branch_head=NEUER_HEAD)

    ergebnis = await _warte(h)

    assert ergebnis == "superseded", (
        f"Ein ueberholter Merge (Kopf {NEUER_HEAD[:7]} != gewarteter "
        f"{MERGED_SHA[:7]}) darf keinen Fehlalarm ausloesen, gemessen: {ergebnis}"
    )
    assert h.branch_head_aufrufe > 0, "Der Branch-Kopf wurde nie geprueft."


@pytest.mark.asyncio
async def test_cancelled_und_kopf_unveraendert_bleibt_failure():
    """Gegenprobe: main steht noch auf demselben Commit — echter Abbruch,
    bisheriges Verhalten bleibt."""
    h = _WaitCancelledHarness(branch_head=MERGED_SHA)

    ergebnis = await _warte(h)

    assert ergebnis == "failure", (
        "Ohne HEAD-Wechsel ist der Abbruch kein Sammel-Zug-Fall, sondern ein "
        f"echter Cancel — muss wie bisher 'failure' bleiben, gemessen: {ergebnis}"
    )


@pytest.mark.asyncio
async def test_cancelled_und_kopf_abfrage_schlaegt_fehl_bleibt_failure():
    """Fail-closed: Ist der Branch-Kopf nicht ermittelbar (API-Fehler),
    gilt der Abbruch weiterhin als Fehlschlag — lieber ein Alarm zu viel."""
    h = _WaitCancelledHarness(branch_head=None)

    ergebnis = await _warte(h)

    assert ergebnis == "failure", (
        "Eine unlesbare Kopf-Abfrage darf NICHT als 'superseded' gelten — "
        f"das waere ein verschluckter echter Fehlschlag, gemessen: {ergebnis}"
    )
