"""Wächter für shadowops-bot#674: CI-Reconcile bei fremder Deploy-Sperre.

Vorfall 09.11.2026: Der Host-Läufer des ZERODOX-MCP (`deploy_mit_migration`)
hielt die Deploy-Sperre `~/.zerodox-deploy.lock` und deployte `f61ffa8`. Der
Bot sah die Sperre nicht und startete alle ~40 s einen Nachhol-Deploy. Jeder
Versuch setzte vor `deploy.sh` den Commit-Status `zerodox/deploy` auf
`pending` und scheiterte dann mit Exit 75. Weil „transient“ keinen Versuch
verbraucht, stand im Log elfmal „Nachhol-Deploy 1/2“. Nach dem fremden Deploy
meldete der Reconcile „live bereits aktuell“ und kehrte zurück, ohne GitHub
etwas zu melden. Ergebnis: 12× `pending`, 0× `success` für einen pünktlich
ausgelieferten Stand.

Geprüft wird hier:
- Fremde Sperre gehalten → kein Deploy-Versuch, kein weiterer `pending`,
  genau EINE Logzeile „wartet auf fremden Deploy“.
- Fremder Deploy fertig, live == sha, Status `pending` → genau EIN `success`
  mit Hinweis auf den fremden Deploy, PR-Kommentar nachgezogen.
- live ≠ sha → kein `success`. Status nicht lesbar → kein `success`.
- Die Sperre wird nur lesend über /proc/locks erkannt, nie per eigener Probe.

Alle GitHub-Aufrufe sind gemockt. Die /proc/locks-Tests arbeiten mit einer
eigenen Sperrdatei im tmp-Verzeichnis, nie mit der echten Deploy-Sperre.
"""
from __future__ import annotations

import fcntl
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.integrations.github_integration import ci_mixin
from src.integrations.github_integration import deploy_feedback as df
from src.integrations.github_integration.ci_mixin import CIMixin, _flock_gehalten

REPO_URL = "https://github.com/Commandershadow9/ZERODOX"
SLUG = "Commandershadow9/ZERODOX"
SHA = "f61ffa8" + "0" * 33
LIVE_ALT = "d136f0a" + "0" * 33
PR = 5479
LOGGER = "test-674"

PROJEKT = {
    "name": "zerodox",
    "repo_url": REPO_URL,
    "health_check_url": "https://zerodox.de/api/health",
}


@pytest.fixture(autouse=True)
def _frischer_prozess_speicher():
    df._COMMENT_IDS.clear()
    df._PR_NUMMERN.clear()
    yield
    df._COMMENT_IDS.clear()
    df._PR_NUMMERN.clear()


# ---------------------------------------------------------------------------
# GitHub-Attrappe für deploy_feedback
# ---------------------------------------------------------------------------

def _antwort(status: int, json_body: dict | None = None, text_body: str = ""):
    resp = MagicMock(status=status)
    resp.json = AsyncMock(return_value=json_body or {})
    resp.text = AsyncMock(return_value=text_body)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=resp)
    cm.__aexit__ = AsyncMock(return_value=None)
    return cm


class _FakeGitHub:
    """Merkt sich je SHA den Zustand von `zerodox/deploy` wie GitHub selbst."""

    def __init__(self, status: dict | None = None, status_http: int = 200):
        self.status = dict(status or {})
        self.status_http = status_http
        self.get_calls: list = []
        self.post_calls: list = []
        self.patch_calls: list = []

    def get(self, url, **_kwargs):
        self.get_calls.append(url)
        if self.status_http != 200:
            return _antwort(self.status_http, text_body="kaputt")
        sha = url.split("/commits/")[1].split("/")[0]
        statuses = [{"context": "andere/ci", "state": "failure"}]
        if sha in self.status:
            statuses.append({"context": df.STATUS_CONTEXT, "state": self.status[sha]})
        return _antwort(200, {"statuses": statuses})

    def post(self, url, json=None, **_kwargs):
        self.post_calls.append((url, json))
        if "/statuses/" in url:
            self.status[url.rsplit("/", 1)[1]] = json["state"]
            return _antwort(201, {})
        return _antwort(201, {"id": 4711})

    def patch(self, url, json=None, **_kwargs):
        self.patch_calls.append((url, json))
        return _antwort(200)

    def status_posts(self, state: str | None = None) -> list:
        return [
            j for u, j in self.post_calls
            if "/statuses/" in u and (state is None or j["state"] == state)
        ]


@pytest.fixture
def github(monkeypatch):
    fake = _FakeGitHub()
    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=fake)
    session_cm.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr(df, "aiohttp", MagicMock(
        ClientSession=lambda **_kwargs: session_cm,
        ClientTimeout=lambda **kwargs: kwargs,
    ))
    return fake


def _bot():
    bot = MagicMock()
    bot.config = MagicMock()
    bot.config.github_token = "ghp_test"
    return bot


async def _bot_versuch_gestartet(sha: str = SHA, pr_number: int | None = PR) -> None:
    """Wie `deployment_manager._send_deployment_started`: pending + Kommentar."""
    await df.report(
        _bot(), PROJEKT, phase="started",
        deploy_context={"commit_sha": sha, "repo_url": REPO_URL, "pr_number": pr_number},
    )


# ---------------------------------------------------------------------------
# Reconcile-Harness
# ---------------------------------------------------------------------------

class _Harness(CIMixin):
    """`sperre` ist eine Folge von Ergebnissen für `_flock_gehalten` (letzter
    Eintrag wiederholt sich), `live` ebenso für den Health-Endpoint."""

    def __init__(self, *, sperre: list, live: list, branch_sha: str = SHA,
                 eigener_deploy: bool = False, trigger_ergebnis: str = "transient"):
        self.logger = logging.getLogger(LOGGER)
        self._sperre = list(sperre)
        self._live = list(live)
        self._branch_sha = branch_sha
        self._eigener_deploy = eigener_deploy
        self._trigger_ergebnis = trigger_ergebnis
        self.trigger_calls: list = []
        self.sperr_abfragen = 0
        self._reserviert: set = set()
        self.deployment_manager = SimpleNamespace(
            projects={"zerodox": dict(PROJEKT)}, bot=_bot(), active_deployments={},
        )

    @staticmethod
    def _naechster(folge: list):
        return folge.pop(0) if len(folge) > 1 else folge[0]

    def sperre_lesen(self, _pfad):
        self.sperr_abfragen += 1
        return self._naechster(self._sperre)

    async def _fetch_branch_head_sha(self, repo_full_name, branch):
        return self._branch_sha

    async def _fetch_live_build_sha(self, health_url):
        return self._naechster(self._live)

    def _deployment_is_active(self, repo_name):
        return self._eigener_deploy

    def _reserve_deploy(self, repo_name, sha):
        if (repo_name, sha) in self._reserviert:
            return False
        self._reserviert.add((repo_name, sha))
        return True

    def _release_deploy(self, repo_name, sha):
        self._reserviert.discard((repo_name, sha))

    async def _trigger_deployment(self, **kwargs):
        """Bildet die echte Kette nach: `deploy_project` meldet `pending`
        VOR `deploy.sh`, `deploy.sh` steigt an der Sperre mit Exit 75 aus."""
        self.trigger_calls.append(kwargs)
        await _bot_versuch_gestartet(kwargs["full_sha"], pr_number=None)
        return self._trigger_ergebnis


class _Uhr:
    """Ersetzt `time` in ci_mixin: jede Abfrage rückt eine Sekunde vor. So endet
    die Schleife nach `timeout_sec` Runden, ohne dass der Test echte Zeit braucht."""

    def __init__(self):
        self.jetzt = 0.0

    def monotonic(self):
        self.jetzt += 1.0
        return self.jetzt

    def time(self):
        return self.monotonic()


def _config(tmp_path: Path, **deploy) -> dict:
    basis = {
        "ci_success_reconcile_delay_sec": 0,
        "ci_success_reconcile_poll_sec": 30,
        "ci_success_reconcile_timeout_sec": 12,
        "ci_success_reconcile_max_attempts": 2,
        "lock_file": str(tmp_path / "zerodox-deploy.lock"),
    }
    basis.update(deploy)
    return {"deploy": basis, "monitor": {"url": PROJEKT["health_check_url"]}}


async def _reconcile(h: _Harness, monkeypatch, tmp_path, *, sleep=None, **deploy):
    monkeypatch.setattr(ci_mixin, "time", _Uhr())
    monkeypatch.setattr(ci_mixin, "_flock_gehalten", h.sperre_lesen)
    with patch("asyncio.sleep", new=sleep or AsyncMock(return_value=None)):
        await h._reconcile_ci_success_deployment(
            repo_name="ZERODOX",
            branch="main",
            successful_sha=SHA,
            repo_full_name=SLUG,
            project_config=_config(tmp_path, **deploy),
        )


def _warte_zeilen(caplog) -> list:
    return [r for r in caplog.records if "wartet auf fremden Deploy" in r.getMessage()]


# ---------------------------------------------------------------------------
# Kriterium 2 + 4a: fremde Sperre gehalten
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fremde_sperre_kein_versuch_kein_pending_eine_logzeile(
    github, monkeypatch, tmp_path, caplog,
):
    """Sperre über die ganze Laufzeit gehalten: nichts versuchen, nichts melden."""
    caplog.set_level(logging.INFO, logger=LOGGER)
    await _bot_versuch_gestartet()  # der eine pending des Push-Deploys
    vorher = len(github.status_posts())
    h = _Harness(sperre=[True], live=[LIVE_ALT])

    await _reconcile(h, monkeypatch, tmp_path)

    assert h.trigger_calls == [], (
        "Während ein fremder Lauf die Sperre hält, darf kein Nachhol-Deploy starten — "
        "jeder Versuch setzt vor deploy.sh einen neuen pending-Status (#674)."
    )
    assert len(github.status_posts()) == vorher, "Kein weiterer Commit-Status."
    assert len(_warte_zeilen(caplog)) == 1, "Genau EINE Logzeile, keine Schleife im Log."
    assert h.sperr_abfragen >= 3, "Der Test muss mehrere Runden abdecken."


@pytest.mark.asyncio
async def test_gegenprobe_ohne_erkannte_sperre_haeuft_sich_pending(
    github, monkeypatch, tmp_path,
):
    """Gegenprobe zum Wächter oben: Sieht der Bot die Sperre nicht, entsteht das
    Bild vom 09.11. (jeder Versuch ein pending, Zähler verbraucht nichts). Ist
    die Sperre nicht feststellbar (None), bleibt es bewusst beim alten Verhalten —
    deploy.sh schützt sich dann selbst."""
    for zustand in (False, None):
        github.post_calls.clear()
        h = _Harness(sperre=[zustand], live=[LIVE_ALT])
        await _reconcile(h, monkeypatch, tmp_path)
        assert len(h.trigger_calls) > 2
        assert len(github.status_posts("pending")) == len(h.trigger_calls)


# ---------------------------------------------------------------------------
# Kriterium 3 + 4b: fremder Deploy fertig, live == sha
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fremder_deploy_fertig_live_gleich_sha_genau_ein_success(
    github, monkeypatch, tmp_path, caplog,
):
    caplog.set_level(logging.INFO, logger=LOGGER)
    await _bot_versuch_gestartet()  # pending + Kommentar „Deploy läuft“ an PR 5479
    assert github.status == {SHA: "pending"}
    h = _Harness(sperre=[True, True, True, False], live=[SHA])

    await _reconcile(h, monkeypatch, tmp_path)

    assert h.trigger_calls == []
    erfolge = github.status_posts("success")
    assert len(erfolge) == 1, f"Genau ein success erwartet, waren {len(erfolge)}."
    assert "fremden Deploy" in erfolge[0]["description"]
    assert len(erfolge[0]["description"]) <= 140
    assert len(github.status_posts()) == 2, "pending (Push) + success — nicht mehr."
    # PR-Kommentar: derselbe Kommentar wird überschrieben, kein zweiter angelegt.
    kommentar_posts = [u for u, _ in github.post_calls if "/comments" in u]
    assert kommentar_posts == [f"https://api.github.com/repos/{SLUG}/issues/{PR}/comments"]
    assert len(github.patch_calls) == 1
    url, body = github.patch_calls[0]
    assert url.endswith("/issues/comments/4711")
    assert "✅ **Live**" in body["body"] and "fremden Deploy-Lauf" in body["body"]
    assert f"sha={SHA}" in body["body"], "Marker bleibt erhalten."
    assert len(_warte_zeilen(caplog)) == 1


@pytest.mark.asyncio
async def test_zweiter_nachzug_setzt_kein_weiteres_success(github, monkeypatch, tmp_path):
    """Ein zweiter Reconcile für denselben Stand findet `success` vor → nichts."""
    await _bot_versuch_gestartet()
    for _ in range(2):
        await _reconcile(_Harness(sperre=[False], live=[SHA]), monkeypatch, tmp_path)

    assert len(github.status_posts("success")) == 1


@pytest.mark.asyncio
async def test_eigener_deploy_aktiv_kein_nachzug(github, monkeypatch, tmp_path):
    """Läuft ein Bot-eigener Deploy, meldet der sein Ergebnis selbst."""
    await _bot_versuch_gestartet()
    h = _Harness(sperre=[True], live=[SHA], eigener_deploy=True)

    await _reconcile(h, monkeypatch, tmp_path)

    assert github.status_posts("success") == []
    assert h.sperr_abfragen == 0, "Bei eigenem Deploy ist die Sperre nicht fremd."


# ---------------------------------------------------------------------------
# Kriterium 4c: live ≠ sha → kein success (fail-closed)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_live_ungleich_sha_kein_success(github, monkeypatch, tmp_path):
    await _bot_versuch_gestartet()
    h = _Harness(
        sperre=[True, False], live=[LIVE_ALT], trigger_ergebnis="failed",
    )

    await _reconcile(h, monkeypatch, tmp_path, ci_success_reconcile_max_attempts=1)

    assert github.status_posts("success") == []
    assert len(h.trigger_calls) == 1, "live hinkt nach → ein echter Nachhol-Deploy."


@pytest.mark.asyncio
async def test_melde_fremd_ausgeliefert_ohne_gleichstand_ruft_github_nicht(github):
    await _bot_versuch_gestartet()
    github.get_calls.clear()

    for live in (LIVE_ALT, None, "", SHA[:7]):
        assert await df.melde_fremd_ausgeliefert(_bot(), PROJEKT, sha=SHA, live_sha=live) is False

    assert github.get_calls == []
    assert github.status_posts("success") == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("vorher", "status_http"),
    [
        ({SHA: "pending"}, 500),   # nicht lesbar → fail-closed
        ({}, 200),                 # kein Status → nie einen anlegen
        ({SHA: "success"}, 200),   # schon erledigt
        ({SHA: "failure"}, 200),   # Bot-eigener Fehlschlag bleibt stehen
    ],
)
async def test_kein_success_ausser_bei_lesbarem_pending(github, vorher, status_http):
    github.status = dict(vorher)
    github.status_http = status_http

    ergebnis = await df.melde_fremd_ausgeliefert(_bot(), PROJEKT, sha=SHA, live_sha=SHA)

    assert ergebnis is False
    assert github.status_posts() == []
    assert github.patch_calls == []


@pytest.mark.asyncio
async def test_ohne_bekannten_pr_nur_status(github):
    """Nach einem Bot-Neustart fehlt die PR-Nummer: Status ja, Kommentar nein."""
    github.status = {SHA: "pending"}

    assert await df.melde_fremd_ausgeliefert(_bot(), PROJEKT, sha=SHA, live_sha=SHA) is True

    assert len(github.status_posts("success")) == 1
    assert [u for u, _ in github.post_calls if "/comments" in u] == []


@pytest.mark.asyncio
async def test_anderes_projekt_ohne_feedback_bleibt_unberuehrt(github):
    github.status = {SHA: "pending"}

    ergebnis = await df.melde_fremd_ausgeliefert(
        _bot(), {**PROJEKT, "name": "mayday_sim"}, sha=SHA, live_sha=SHA,
    )

    assert ergebnis is False
    assert github.get_calls == []


# ---------------------------------------------------------------------------
# Sperre erkennen: nur lesend über /proc/locks
# ---------------------------------------------------------------------------

def test_echte_flock_sperre_wird_erkannt(tmp_path):
    datei = tmp_path / "zerodox-deploy.lock"
    datei.touch()
    with open(datei, "w") as halter:
        fcntl.flock(halter, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert _flock_gehalten(datei) is True
        fcntl.flock(halter, fcntl.LOCK_UN)
        assert _flock_gehalten(datei) is False


def test_probe_greift_nicht_selbst_nach_der_sperre(tmp_path, monkeypatch):
    """Eine Probe per flock könnte einen gerade startenden Deploy mit Exit 75
    abbrechen. `_flock_gehalten` darf flock deshalb nie aufrufen."""
    datei = tmp_path / "zerodox-deploy.lock"
    datei.touch()
    gerufen = []
    monkeypatch.setattr(fcntl, "flock", lambda *a, **k: gerufen.append(a))
    monkeypatch.setattr(fcntl, "lockf", lambda *a, **k: gerufen.append(a))

    _flock_gehalten(datei)

    assert gerufen == []


def test_fehlende_sperrdatei_heisst_frei(tmp_path):
    assert _flock_gehalten(tmp_path / "gibt-es-nicht.lock") is False


def test_unlesbares_proc_locks_heisst_unbekannt(tmp_path):
    datei = tmp_path / "zerodox-deploy.lock"
    datei.touch()
    assert _flock_gehalten(datei, proc_locks=tmp_path / "keine-locks") is None


def test_wartende_anforderung_haelt_nichts(tmp_path):
    import os

    datei = tmp_path / "zerodox-deploy.lock"
    datei.touch()
    st = os.stat(datei)
    kennung = f"{os.major(st.st_dev):02x}:{os.minor(st.st_dev):02x}:{st.st_ino}"

    fremde_inode = f"{os.major(st.st_dev):02x}:{os.minor(st.st_dev):02x}:{st.st_ino + 1}"
    tabelle = tmp_path / "locks"

    tabelle.write_text(f"1: -> FLOCK  ADVISORY  WRITE 4242 {kennung} 0 EOF\n")
    assert _flock_gehalten(datei, proc_locks=tabelle) is False

    tabelle.write_text(f"1: POSIX  ADVISORY  WRITE 17 {fremde_inode} 0 EOF\n")
    assert _flock_gehalten(datei, proc_locks=tabelle) is False

    tabelle.write_text(
        f"1: POSIX  ADVISORY  READ 17 {fremde_inode} 0 EOF\n"
        f"2: FLOCK  ADVISORY  WRITE 4242 {kennung} 0 EOF\n"
    )
    assert _flock_gehalten(datei, proc_locks=tabelle) is True


@pytest.mark.asyncio
async def test_reconcile_mit_echter_sperre_ende_zu_ende(github, monkeypatch, tmp_path):
    """Ohne Attrappe für die Sperre: echter flock, echtes /proc/locks. Die Sperre
    fällt nach drei Warterunden, dann ist live == sha."""
    await _bot_versuch_gestartet()
    datei = tmp_path / "zerodox-deploy.lock"
    datei.touch()
    halter = open(datei, "w")
    fcntl.flock(halter, fcntl.LOCK_EX | fcntl.LOCK_NB)
    runden = []

    async def _schlafen(_sek):
        runden.append(_sek)
        if len(runden) == 3:
            halter.close()  # fremder Deploy fertig, Kernel gibt die Sperre frei

    h = _Harness(sperre=[None], live=[SHA])
    monkeypatch.setattr(ci_mixin, "time", _Uhr())
    try:
        with patch("asyncio.sleep", new=AsyncMock(side_effect=_schlafen)):
            await h._reconcile_ci_success_deployment(
                repo_name="ZERODOX", branch="main", successful_sha=SHA,
                repo_full_name=SLUG, project_config=_config(tmp_path),
            )
    finally:
        halter.close()

    assert len(runden) == 3, "Drei Warterunden während der Sperre, danach kein Schlaf mehr."
    assert h.trigger_calls == []
    assert len(github.status_posts("success")) == 1


# ---------------------------------------------------------------------------
# Welche Sperrdatei?
# ---------------------------------------------------------------------------

class TestSperrDatei:
    def _h(self):
        return _Harness(sperre=[False], live=[SHA])

    def test_config_gewinnt(self, tmp_path):
        pfad = self._h()._deploy_sperr_datei(
            "mayday-sim", {"deploy": {"lock_file": str(tmp_path / "x.lock")}}
        )
        assert pfad == tmp_path / "x.lock"

    def test_leerer_eintrag_schaltet_ab(self):
        assert self._h()._deploy_sperr_datei("ZERODOX", {"deploy": {"lock_file": ""}}) is None

    def test_zerodox_default_wie_deploy_sh(self, monkeypatch, tmp_path):
        monkeypatch.delenv("ZERODOX_DEPLOY_LOCK_FILE", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        assert self._h()._deploy_sperr_datei("ZERODOX", {}) == tmp_path / ".zerodox-deploy.lock"

        monkeypatch.setenv("ZERODOX_DEPLOY_LOCK_FILE", str(tmp_path / "anders.lock"))
        assert self._h()._deploy_sperr_datei("zerodox", {"deploy": {}}) == tmp_path / "anders.lock"

    def test_andere_projekte_ohne_eintrag_unveraendert(self):
        assert self._h()._deploy_sperr_datei("mayday-sim", {"deploy": {}}) is None
