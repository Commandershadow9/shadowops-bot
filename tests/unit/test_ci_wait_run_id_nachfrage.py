"""ZERODOX#2891: Ein bereits gesehener Lauf darf nicht aus dem Wartefenster
verschwinden.

Am 31.08.2026 sah `_wait_for_ci_completion` einen gruenen Web-Quality-Lauf
32 Minuten lang nicht und lief in den Timeout. Die Listenabfrage nach
`head_sha` ist eventually consistent — ein gesehener Lauf kann darin wieder
fehlen, und `saw_any_relevant` war nur ein Einweg-Latch ohne Gedaechtnis,
WELCHER Lauf gesehen wurde.

Neues Verhalten: Die gesehenen relevanten Laeufe werden je Wartevorgang
gemerkt (id, API-URL, zuletzt gesehener Status). Fehlt ein gemerkter Lauf in
einer spaeteren Listenantwort, wird er per `_fetch_workflow_run(url)` direkt
nachgefragt und in die bestehende Klassifikation eingespeist. Wartelog und
Timeout-Alarm nennen den zuletzt gesehenen Zustand (queued/in_progress).
"""
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.integrations.github_integration.ci_mixin import CIMixin

SHA = "b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0"
RUN_URL = "https://api.github.com/repos/Commandershadow9/ZERODOX/actions/runs/42"


def _lauf(status: str, conclusion=None):
    return {
        "name": "Web Quality",
        "path": ".github/workflows/web-quality.yml",
        "status": status,
        "conclusion": conclusion,
        "created_at": "2026-08-31T10:00:00Z",
        "id": 42,
        "url": RUN_URL,
    }


class _Harness(CIMixin):
    def __init__(self, run_sequenzen, einzel_ergebnis=None):
        self.logger = logging.getLogger("test-ci-wait-run-id-nachfrage")
        self._run_sequenzen = run_sequenzen
        self._poll_index = 0
        self._einzel_ergebnis = einzel_ergebnis
        self.einzel_aufrufe = []

    async def _fetch_workflow_runs_for_sha(self, repo_full_name: str, head_sha: str):
        idx = min(self._poll_index, len(self._run_sequenzen) - 1)
        self._poll_index += 1
        return {"workflow_runs": self._run_sequenzen[idx]}

    async def _fetch_workflow_run(self, run_api_url):
        self.einzel_aufrufe.append(run_api_url)
        return self._einzel_ergebnis

    async def _fetch_commit_files(self, repo_full_name: str, sha: str):
        return ["web/src/app/page.tsx"]

    async def _fetch_branch_head_sha(self, repo_full_name: str, branch: str):
        return SHA

    async def _laeuft_noch_ein_workflow(self, repo_full_name: str, sha: str):
        return None  # nicht ermittelbar → weiter warten


class _Uhr:
    def __init__(self):
        self.jetzt = 1000.0

    def monotonic(self):
        return self.jetzt

    async def sleep(self, dauer, *a, **kw):
        self.jetzt += dauer


async def _warte(h: _Harness):
    uhr = _Uhr()
    with patch("time.monotonic", new=uhr.monotonic), \
         patch("asyncio.sleep", new=uhr.sleep):
        return await h._wait_for_ci_completion(
            repo_full_name="Commandershadow9/ZERODOX",
            merged_sha=SHA,
            workflow_names=["Web Quality"],
            max_wait_min=5,
            admin_merge_grace_min=0,
            branch="main",
        )


@pytest.mark.asyncio
async def test_verschwundener_lauf_einzeln_gruen_ergibt_success():
    """(a) Einmal in_progress gesehen, dann aus der Liste verschwunden,
    Einzelabfrage liefert completed/success → success."""
    h = _Harness(
        run_sequenzen=[[_lauf("in_progress")], []],
        einzel_ergebnis=_lauf("completed", "success"),
    )
    ergebnis = await _warte(h)
    assert ergebnis == "success", f"gemessen: {ergebnis}"
    assert h.einzel_aufrufe == [RUN_URL]


@pytest.mark.asyncio
async def test_verschwundener_lauf_einzeln_rot_ergibt_failure():
    """(b) Einzelabfrage liefert completed/failure → failure."""
    h = _Harness(
        run_sequenzen=[[_lauf("in_progress")], []],
        einzel_ergebnis=_lauf("completed", "failure"),
    )
    ergebnis = await _warte(h)
    assert ergebnis == "failure", f"gemessen: {ergebnis}"


@pytest.mark.asyncio
async def test_einzelabfrage_ohne_ergebnis_bleibt_timeout():
    """(c) Einzelabfrage liefert None → weiter pollen, Timeout wie bisher."""
    h = _Harness(run_sequenzen=[[_lauf("in_progress")], []], einzel_ergebnis=None)
    ergebnis = await _warte(h)
    assert ergebnis == "timeout", f"gemessen: {ergebnis}"
    assert len(h.einzel_aufrufe) >= 1


@pytest.mark.asyncio
async def test_einzelabfrage_wirft_bleibt_timeout():
    """(c') Einzelabfrage wirft → fail-soft, Timeout wie bisher."""
    h = _Harness(run_sequenzen=[[_lauf("in_progress")], []])
    h._fetch_workflow_run = AsyncMock(side_effect=RuntimeError("kaputt"))
    ergebnis = await _warte(h)
    assert ergebnis == "timeout", f"gemessen: {ergebnis}"


@pytest.mark.asyncio
async def test_timeout_nennt_zuletzt_queued_in_log_und_alarm():
    """(d) Timeout mit zuletzt gesehenem 'queued' → Log und Alarmtext nennen es."""
    h = _Harness(run_sequenzen=[[_lauf("queued")]])
    with patch.object(h.logger, "warning") as mock_warning:
        ergebnis = await _warte(h)
    assert ergebnis == "timeout", f"gemessen: {ergebnis}"
    timeout_logs = [
        str(c.args[0]) for c in mock_warning.call_args_list if c.args and "TIMEOUT" in str(c.args[0])
    ]
    assert timeout_logs and "queued" in timeout_logs[0], timeout_logs
    assert "wartete zuletzt auf einen Runner" in timeout_logs[0]

    # Alarmtext: derselbe Zustand muss in der Discord-Beschreibung stehen.
    kanal = MagicMock()
    kanal.send = AsyncMock()
    h.config = MagicMock()
    h.config.projects = {}
    h.bot = MagicMock()
    h.bot.get_channel = MagicMock(return_value=kanal)
    h.deployment_channel_id = 1
    await h._send_ci_wait_alert(
        outcome="timeout",
        repo_name="ZERODOX",
        repo_full_name="Commandershadow9/ZERODOX",
        branch="main",
        merged_sha=SHA,
        workflow_names=["Web Quality"],
        max_wait_min=5,
    )
    embed = kanal.send.call_args.kwargs["embed"]
    assert "queued" in embed.description, embed.description
