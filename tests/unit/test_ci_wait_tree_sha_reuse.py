"""Tree-SHA-Reuse fuer Merge-Commits (ZERODOX#3230-Folge, team-lead-Auftrag
"der eigentliche Ertrag der ganzen Welle").

Messung (12.09.2026): Von den ~12min CI-Wait im Bot entfallen in 6 von 15
gemessenen Merges auf einen Merge-Commit, dessen Baum (Tree-SHA) BIT-IDENTISCH
mit dem Baum seines zweiten Elternteils (`merge^2`, die Spitze des gemergten
Branches) ist -- typischerweise ein reiner Fast-Forward-Merge ohne
Merge-Konflikt-Aufloesung. Die CI auf `merge^2` ist in diesem Fall bereits
gelaufen (PR-Pipeline) und ebenso gueltig fuer den Merge-Commit selbst -- ein
erneutes Warten auf eine neue CI-Runde des Merge-Commits ist dann reine
Blindzeit (bis zu die vollen ~12min).

Der Check sitzt VOR der Polling-Schleife, NACH dem Docs-only-Check
(ZERODOX#3230, siehe test_ci_wait_docs_only_vorzug.py) und VOR dem
`while`-Loop selbst -- analog zu dessen Vorzug-Prinzip: was schon vorliegt,
muss nicht erst erwartet werden.

Fail-closed in JEDER Richtung (gleiches Prinzip wie beim Docs-only-Check):
- kein zweiter Elternteil (kein Merge-Commit) -> normal warten
- Tree-Info nicht ladbar (API-Fehler) -> normal warten
- Trees unterschiedlich -> normal warten
- Trees gleich, aber `merge^2` NICHT vollstaendig gruen -> normal warten
  (der Shortcut liefert NIEMALS "failure" selbst -- nur ein zusaetzlicher
  Wahrheitsbeweis fuer "success", nie eine Abkuerzung fuer eine Ablehnung).
Nur wenn Trees nachweislich gleich UND alle relevanten Checks auf `merge^2`
nachweislich gruen sind, liefert der Check sofort "success" -- ohne jeden
Poll auf den Merge-Commit selbst.

Die Fetch-Quelle fuer Tree-SHA und Parent-SHAs ist hinter einer eigenen,
ueberschreibbaren Methode `_fetch_commit_tree_info` isoliert (Rueckgabe:
{"tree_sha": str, "parent_shas": list[str]} oder None bei Fehler) -- ob diese
intern einen eigenen API-Call macht oder den ohnehin schon fuer den
Docs-only-Check geholten Commit-Payload aus `_fetch_commit_files` mitnutzt,
ist eine noch offene Implementierungsentscheidung (an team-lead
zurueckgemeldet: Erweiterung von `_fetch_commit_files` wuerde dessen
Rueckgabetyp aendern und damit in den bereits abgenommenen Docs-only-Code
(Task 2, `precomputed_changed_paths`) eingreifen). Diese Tests pruefen nur
das Verhalten von `_wait_for_ci_completion`, nicht, welchen der beiden Wege
die Implementierung waehlt.
"""
import logging

import pytest

from src.integrations.github_integration.ci_mixin import CIMixin

_MERGE_SHA = "1111111111111111111111111111111111111a"
_PARENT1_SHA = "2222222222222222222222222222222222222b"
_PARENT2_SHA = "3333333333333333333333333333333333333c"
_PR_HEAD_SHA = "4444444444444444444444444444444444444d"

#: Sentinel: Der Test hat zum PR-Weg (ZERODOX#3328 Paket A) nichts gesagt.
#: Wirkt wie eine API-Unklarheit, also fail-closed.
_NICHT_GESETZT = object()


def _lauf(name: str, status: str, conclusion: str | None = None) -> dict:
    return {
        "name": name,
        "path": ".github/workflows/web-quality.yml",
        "status": status,
        "conclusion": conclusion,
        "created_at": "2026-09-12T13:00:00Z",
    }


class _TreeReuseHarness(CIMixin):
    """`tree_info` bildet sha -> {"tree_sha", "parent_shas"} (oder fehlender
    Key / None-Wert = API-Fehler) ab. `workflow_runs_by_sha` bildet sha -> Liste
    von Antworten ab (wird der Reihe nach abgearbeitet, letzter Wert wiederholt
    sich -- wie bei `_SleepSpurHarness` in test_ci_wait_poll_intervall.py)."""

    def __init__(self, tree_info: dict, workflow_runs_by_sha: dict, pull_heads=_NICHT_GESETZT):
        self.logger = logging.getLogger("test-ci-wait-tree-reuse")
        self._tree_info = tree_info
        self._workflow_runs_by_sha = {
            sha: list(antworten) for sha, antworten in workflow_runs_by_sha.items()
        }
        # ZERODOX#3328 Paket A: sha -> Liste von PR-HEAD-SHAs, [] = nachweislich
        # kein PR, None = API-Unklarheit (muss fail-closed wirken).
        # `_NICHT_GESETZT` bedeutet "dieser Test kennt den Weg nicht" und
        # verhaelt sich wie eine API-Unklarheit — so bleiben die aelteren
        # Testfaelle unveraendert gueltig, ohne sich auf eine AttributeError
        # zu verlassen.
        self._pull_heads = pull_heads
        self.tree_info_aufrufe: list[str] = []
        self.workflow_aufrufe: list[str] = []
        self.pull_aufrufe: list[str] = []
        self.sleep_dauern: list = []

    async def _fetch_pull_head_shas(self, repo_full_name: str, sha: str):
        self.pull_aufrufe.append(sha)
        if self._pull_heads is _NICHT_GESETZT:
            return None
        if isinstance(self._pull_heads, dict):
            return self._pull_heads.get(sha)
        return self._pull_heads

    async def _fetch_commit_files(self, repo_full_name: str, sha: str):
        # Fixe, nicht-docs-only Pfadliste -- dieser Testfokus ist die
        # Tree-Reuse-Logik, nicht die Docs-only-Erkennung (ZERODOX#3230).
        return ["src/module.py"]

    async def _fetch_commit_tree_info(self, repo_full_name: str, sha: str):
        self.tree_info_aufrufe.append(sha)
        return self._tree_info.get(sha)

    async def _fetch_workflow_runs_for_sha(self, repo_full_name: str, sha: str):
        self.workflow_aufrufe.append(sha)
        warteschlange = self._workflow_runs_by_sha.get(sha)
        if not warteschlange:
            return None
        if len(warteschlange) > 1:
            return warteschlange.pop(0)
        return warteschlange[0]


class _VirtualClock:
    """Monoton fortschreitende Test-Uhr: springt nur vor, wenn der Code
    tatsächlich (gemockt) schläft -- ohne echte Wartezeit im Testlauf.
    Identisches Muster wie in test_ci_wait_docs_only_vorzug.py.

    WICHTIG (dieser Testdatei eigener Fund): Wird NUR `asyncio.sleep`
    gemockt, aber `time.monotonic` bleibt echt, dann läuft die Schleife bei
    einem (hier absichtlich, weil noch unimplementiert) niemals endenden
    Poll-Ergebnis als reine CPU-Busyloop bis zu `max_wait_min` ECHTE
    Minuten lang -- beobachtet als hängender, RAM/CPU-fressender Testlauf
    (RED-Test, der nicht schnell fehlschlägt, sondern hängt). Beide Uhren
    müssen zusammen gemockt werden, sonst misst die Abbruchbedingung der
    Schleife (`time.monotonic() - start < max_wait_min * 60`) echte Zeit,
    während der Schlaf selbst keine echte Zeit kostet."""

    def __init__(self):
        self.jetzt = 0.0

    def monotonic(self):
        return self.jetzt

    async def sleep(self, dauer, *a, **kw):
        self.jetzt += dauer


async def _warte(h: _TreeReuseHarness, monkeypatch, **kwargs):
    uhr = _VirtualClock()

    async def _fake_sleep(dauer, *a, **kw):
        h.sleep_dauern.append(dauer)
        await uhr.sleep(dauer)

    monkeypatch.setattr("asyncio.sleep", _fake_sleep)
    monkeypatch.setattr("time.monotonic", uhr.monotonic)
    return await h._wait_for_ci_completion(
        repo_full_name="Commandershadow9/ZERODOX",
        merged_sha=_MERGE_SHA,
        workflow_names=["Web Quality"],
        admin_merge_grace_min=0,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_tree_gleich_und_gruen_liefert_success_ohne_poll(monkeypatch, caplog):
    """(a) Baum von merge und merge^2 identisch, merge^2 vollstaendig gruen
    -> sofortiges "success", KEIN einziger Poll auf den Merge-Commit selbst."""
    h = _TreeReuseHarness(
        tree_info={
            _MERGE_SHA: {"tree_sha": "TREEXYZ", "parent_shas": [_PARENT1_SHA, _PARENT2_SHA]},
            _PARENT2_SHA: {"tree_sha": "TREEXYZ", "parent_shas": [_PARENT1_SHA]},
        },
        workflow_runs_by_sha={
            _PARENT2_SHA: [{"workflow_runs": [_lauf("Web Quality", "completed", "success")]}],
        },
    )

    with caplog.at_level(logging.INFO):
        ergebnis = await _warte(h, monkeypatch, max_wait_min=30)

    assert ergebnis == "success"
    assert h.sleep_dauern == [], (
        "Der Tree-Reuse-Shortcut muss VOR der Polling-Schleife greifen -- "
        f"gemessen: {len(h.sleep_dauern)} Sleep(s)/Poll-Runde(n)."
    )
    assert _MERGE_SHA not in h.workflow_aufrufe, (
        "Bei identischem Tree darf der Merge-Commit selbst gar nicht erst auf "
        f"CI-Status abgefragt werden -- gemessen: {h.workflow_aufrufe}."
    )
    assert h.workflow_aufrufe == [_PARENT2_SHA], (
        "Der CI-Status muss auf merge^2 geprueft werden, nicht auf dem "
        f"Merge-Commit -- gemessen: {h.workflow_aufrufe}."
    )
    log_text = " ".join(r.message for r in caplog.records)
    assert "wiederverwend" in log_text.lower(), (
        "Anforderung 5 (team-lead): Logging muss als WIEDERVERWENDUNG "
        "formuliert sein, nicht als 'skip'/uebersprungen -- "
        f"gemessene Log-Zeilen: {log_text!r}"
    )
    assert "übersprung" not in log_text.lower() and "skip" not in log_text.lower()


@pytest.mark.asyncio
async def test_tree_gleich_aber_rot_wartet_normal(monkeypatch):
    """(b) Baum identisch, aber merge^2 ist NICHT gruen (failure) -> der
    Shortcut darf das NIEMALS als eigenes "failure" ausgeben (fail-closed
    heisst hier: nur ein Beweis fuer "success", nie einer fuer "failure").
    Es muss auf den normalen Polling-Pfad fuer den Merge-Commit selbst
    zurueckgefallen werden."""
    h = _TreeReuseHarness(
        tree_info={
            _MERGE_SHA: {"tree_sha": "TREEXYZ", "parent_shas": [_PARENT1_SHA, _PARENT2_SHA]},
            _PARENT2_SHA: {"tree_sha": "TREEXYZ", "parent_shas": [_PARENT1_SHA]},
        },
        workflow_runs_by_sha={
            _PARENT2_SHA: [{"workflow_runs": [_lauf("Web Quality", "completed", "failure")]}],
            _MERGE_SHA: [
                {"workflow_runs": [_lauf("Web Quality", "in_progress")]},
                {"workflow_runs": [_lauf("Web Quality", "completed", "failure")]},
            ],
        },
    )

    ergebnis = await _warte(h, monkeypatch, max_wait_min=30, poll_interval_sec=1)

    assert ergebnis == "failure"
    assert _MERGE_SHA in h.workflow_aufrufe, (
        "Bei rotem merge^2 muss normal auf den Merge-Commit selbst gepollt "
        f"werden -- gemessen: {h.workflow_aufrufe}."
    )
    assert len(h.sleep_dauern) >= 1, (
        "Der Fallback auf den normalen Polling-Pfad muss tatsaechlich "
        f"mindestens einmal pollen/schlafen -- gemessen: {h.sleep_dauern}."
    )


@pytest.mark.asyncio
async def test_tree_unterschiedlich_ohne_paket_a_wartet_normal(monkeypatch):
    """(c) Baeume unterschiedlich, Paket A ABGESCHALTET -> kein Shortcut,
    normaler Poll auf den Merge-Commit. merge^2 darf dafuer gar nicht erst auf
    CI-Status geprueft werden -- das waere verschwendete Arbeit, wenn der Tree
    ohnehin abweicht.

    Dieser Fall haelt den Zustand VOR ZERODOX#3328 Paket A fest. Er muss
    erhalten bleiben, weil `ci_wait_pr_head_reuse: false` genau dorthin
    zurueckschaltet -- und dieser Rueckweg ist die Absicherung fuer den Fall,
    dass Paket A sich als Fehlgriff erweist."""
    h = _TreeReuseHarness(
        tree_info={
            _MERGE_SHA: {"tree_sha": "TREE_MERGE", "parent_shas": [_PARENT1_SHA, _PARENT2_SHA]},
            _PARENT2_SHA: {"tree_sha": "TREE_ANDERS", "parent_shas": [_PARENT1_SHA]},
        },
        workflow_runs_by_sha={
            _MERGE_SHA: [{"workflow_runs": [_lauf("Web Quality", "completed", "success")]}],
        },
    )

    ergebnis = await _warte(
        h, monkeypatch, max_wait_min=30, poll_interval_sec=1, pr_head_reuse_enabled=False
    )

    assert ergebnis == "success"
    assert _MERGE_SHA in h.workflow_aufrufe
    assert _PARENT2_SHA not in h.workflow_aufrufe, (
        "Bei abweichendem Tree ist der CI-Status von merge^2 irrelevant -- "
        f"er darf gar nicht erst abgefragt werden. Gemessen: {h.workflow_aufrufe}."
    )


@pytest.mark.asyncio
async def test_tree_unterschiedlich_mit_paket_a_nutzt_pr_head(monkeypatch):
    """(c2) Baeume unterschiedlich, Paket A AN -> der gruene Stand des
    PR-HEAD wird trotzdem wiederverwendet.

    Das ist der Kern von ZERODOX#3328 Paket A. Der Baum weicht ab, sobald
    zwischen PR-Erstellung und Merge irgendein anderer Commit auf main landet
    -- belegt am 16.09.2026: PR #3404 wurde nach #3405 gemergt, der strenge
    Riegel schlug zu, der Merge hing. Nachdem `push: main` aus
    `web-quality.yml` entfernt ist, gibt es fuer den Merge-Commit ueberhaupt
    keinen Lauf mehr; ohne diesen Weg wartete der Bot die vollen
    `max_wait_min` und lieferte danach NICHT aus.

    Geprueft wird deshalb ausdruecklich auch, dass auf den Merge-Commit KEIN
    Poll mehr faellt -- genau die Wartezeit ist der Ertrag."""
    h = _TreeReuseHarness(
        tree_info={
            _MERGE_SHA: {"tree_sha": "TREE_MERGE", "parent_shas": [_PARENT1_SHA, _PARENT2_SHA]},
            _PARENT2_SHA: {"tree_sha": "TREE_ANDERS", "parent_shas": [_PARENT1_SHA]},
        },
        workflow_runs_by_sha={
            _PARENT2_SHA: [{"workflow_runs": [_lauf("Web Quality", "completed", "success")]}],
        },
    )

    ergebnis = await _warte(h, monkeypatch, max_wait_min=30, poll_interval_sec=1)

    assert ergebnis == "success"
    assert _PARENT2_SHA in h.workflow_aufrufe
    assert _MERGE_SHA not in h.workflow_aufrufe, (
        "Der Merge-Commit darf gar nicht mehr gepollt werden -- fuer ihn "
        "existiert nach Paket A kein Lauf, und das Warten darauf ist exakt "
        f"die Blindzeit, die entfaellt. Gemessen: {h.workflow_aufrufe}."
    )
    assert h.sleep_dauern == [], (
        "Kein einziger Schlaf erwartet -- der Kurzschluss sitzt VOR der "
        f"Polling-Schleife. Gemessen: {h.sleep_dauern}."
    )


@pytest.mark.asyncio
async def test_squash_merge_nutzt_den_zugeordneten_pr(monkeypatch):
    """(c3) Squash-Merge: EIN Elternteil, aber ein zugeordneter, gemergter PR
    mit gruenem HEAD -> Wiederverwendung.

    Gemessen auf der main-Linie von ZERODOX ueber 30 Tage (17.09.2026):
    387 Merge-Commits, dazu 68 Ein-Eltern-Commits, davon 51 mit Code. Alle 51
    hatten einen zugeordneten PR, keiner war ein echter Direkt-Push.

    Eine Wiederverwendung, die nur ueber `merge^2` geht, laesst diese 51
    durchfallen -- nach dem Entfernen von `push: main` haetten sie jeweils die
    vollen `max_wait_min` gewartet und waeren danach NICHT ausgeliefert worden.
    Rund 1,7-mal taeglich, also schlimmer als das Problem, das Paket A loest.
    """
    h = _TreeReuseHarness(
        tree_info={
            _MERGE_SHA: {"tree_sha": "TREE_SQUASH", "parent_shas": [_PARENT1_SHA]},
        },
        workflow_runs_by_sha={
            _PR_HEAD_SHA: [{"workflow_runs": [_lauf("Web Quality", "completed", "success")]}],
        },
        pull_heads=[_PR_HEAD_SHA],
    )

    ergebnis = await _warte(h, monkeypatch, max_wait_min=30, poll_interval_sec=1)

    assert ergebnis == "success"
    assert _PR_HEAD_SHA in h.workflow_aufrufe
    assert _MERGE_SHA not in h.workflow_aufrufe, (
        "Fuer den Squash-Commit existiert nach Paket A kein Lauf -- er darf "
        f"nicht gepollt werden. Gemessen: {h.workflow_aufrufe}."
    )
    assert h.sleep_dauern == []


@pytest.mark.asyncio
async def test_direkt_push_ohne_pr_bleibt_fail_closed(monkeypatch):
    """(c4) Ein-Eltern-Commit OHNE zugeordneten PR -> KEIN Kurzschluss.

    Das ist ein echter Direkt-Push auf main. In 30 Tagen kam er kein einziges
    Mal vor -- und genau deshalb darf er hier nicht durchrutschen: ungeprueft
    auf main geschobener Code soll NICHT ohne Lauf ausgeliefert werden. Eine
    leere PR-Liste ist ein nachweisliches "kein PR", kein Anlass zur Nachsicht.
    """
    h = _TreeReuseHarness(
        tree_info={
            _MERGE_SHA: {"tree_sha": "TREE_DIREKT", "parent_shas": [_PARENT1_SHA]},
        },
        workflow_runs_by_sha={
            _MERGE_SHA: [{"workflow_runs": [_lauf("Web Quality", "completed", "success")]}],
        },
        pull_heads=[],
    )

    ergebnis = await _warte(h, monkeypatch, max_wait_min=30, poll_interval_sec=1)

    # Der normale Poll auf den Commit selbst findet hier noch einen Lauf
    # (Testaufbau); entscheidend ist, dass der Kurzschluss NICHT gegriffen hat.
    assert ergebnis == "success"
    assert _MERGE_SHA in h.workflow_aufrufe, (
        "Ohne zugeordneten PR muss der Commit selbst gepollt werden -- "
        f"gemessen: {h.workflow_aufrufe}."
    )


@pytest.mark.asyncio
async def test_pr_zuordnung_unklar_bleibt_fail_closed(monkeypatch):
    """(c5) Die PR-Zuordnung ist nicht ermittelbar (API-Fehler) -> kein
    Kurzschluss.

    `None` heisst "ich konnte nicht nachsehen", niemals "es gibt keinen PR".
    Dieselbe Unterscheidung, an der der `mcp-drift-watchdog` einmal
    gescheitert ist: Zwischen "ist aus" und "ich kann nicht nachsehen" muss
    ein Wachposten unterscheiden."""
    h = _TreeReuseHarness(
        tree_info={
            _MERGE_SHA: {"tree_sha": "TREE_SQUASH", "parent_shas": [_PARENT1_SHA]},
        },
        workflow_runs_by_sha={
            _MERGE_SHA: [{"workflow_runs": [_lauf("Web Quality", "completed", "success")]}],
            _PR_HEAD_SHA: [{"workflow_runs": [_lauf("Web Quality", "completed", "success")]}],
        },
        pull_heads=None,
    )

    ergebnis = await _warte(h, monkeypatch, max_wait_min=30, poll_interval_sec=1)

    assert ergebnis == "success"
    assert _PR_HEAD_SHA not in h.workflow_aufrufe, (
        "Bei unklarer PR-Zuordnung darf kein fremder Commit als Beleg "
        f"herangezogen werden. Gemessen: {h.workflow_aufrufe}."
    )
    assert _MERGE_SHA in h.workflow_aufrufe


@pytest.mark.asyncio
async def test_kein_zweiter_parent_wartet_normal(monkeypatch):
    """(d) Der Commit ist gar kein Merge-Commit (nur ein Elternteil) ->
    Shortcut greift nicht, normaler Poll auf den Commit selbst."""
    h = _TreeReuseHarness(
        tree_info={
            _MERGE_SHA: {"tree_sha": "TREE_SOLO", "parent_shas": [_PARENT1_SHA]},
        },
        workflow_runs_by_sha={
            _MERGE_SHA: [{"workflow_runs": [_lauf("Web Quality", "completed", "success")]}],
        },
    )

    ergebnis = await _warte(h, monkeypatch, max_wait_min=30, poll_interval_sec=1)

    assert ergebnis == "success"
    assert _MERGE_SHA in h.workflow_aufrufe
    assert not any(sha != _MERGE_SHA for sha in h.workflow_aufrufe), (
        "Ohne zweiten Parent darf ueberhaupt keine andere SHA auf CI-Status "
        f"geprueft werden -- gemessen: {h.workflow_aufrufe}."
    )


@pytest.mark.asyncio
async def test_api_fehler_bei_tree_info_wartet_normal(monkeypatch):
    """(e) `_fetch_commit_tree_info` liefert None (API-Stoerung) -> fail-closed,
    normaler Poll auf den Merge-Commit -- wie beim 2026-08-17
    api_unavailable-Vorfall darf eine Unklarheit NIE als Erfolgsbeweis
    gewertet werden."""
    h = _TreeReuseHarness(
        tree_info={},  # jede Anfrage liefert None (Schluessel fehlt)
        workflow_runs_by_sha={
            _MERGE_SHA: [{"workflow_runs": [_lauf("Web Quality", "completed", "success")]}],
        },
    )

    ergebnis = await _warte(h, monkeypatch, max_wait_min=30, poll_interval_sec=1)

    assert ergebnis == "success"
    assert _MERGE_SHA in h.workflow_aufrufe, (
        "API-Fehler beim Tree-Lookup darf NIE zu einem uebersprungenen Poll "
        f"fuehren -- gemessen: {h.workflow_aufrufe}."
    )
