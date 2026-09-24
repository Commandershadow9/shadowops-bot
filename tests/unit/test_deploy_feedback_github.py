"""Tests fuer die GitHub-Deploy-Rueckmeldung (ZERODOX#3638).

Deckt ab: Zustandstexte je Phase, Payload-Form des Commit-Status, PR-Kommentar
nur bei bekannter `pr_number`, PATCH auf dieselbe Kommentar-ID bei
Wiederholung, Default-AN nur fuer 'zerodox' sowie Fail-Soft-Verhalten bei
GitHub-API-Fehlern (fehlendes Token, HTTP 500, Exception im HTTP-Aufruf).

Alle HTTP-Aufrufe sind gemockt — kein echter Netzwerkzugriff.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.integrations.github_integration import deploy_feedback as df


@pytest.fixture(autouse=True)
def _reset_comment_state():
    """`_COMMENT_IDS` ist Modul-globaler Prozess-Speicher — je Test frisch."""
    df._COMMENT_IDS.clear()
    yield
    df._COMMENT_IDS.clear()


def _response(status: int, json_body: dict | None = None, text_body: str = ""):
    resp = MagicMock(status=status)
    resp.json = AsyncMock(return_value=json_body or {})
    resp.text = AsyncMock(return_value=text_body)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=resp)
    cm.__aexit__ = AsyncMock(return_value=None)
    return cm


class _FakeSession:
    """Minimaler aiohttp.ClientSession-Stub: post/patch liefern vorgegebene Antworten."""

    def __init__(self, post_responses=None, patch_responses=None):
        self._post_responses = list(post_responses or [])
        self._patch_responses = list(patch_responses or [])
        self.post_calls: list = []
        self.patch_calls: list = []

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        if self._post_responses:
            return self._post_responses.pop(0)
        return _response(201, {"id": 999})

    def patch(self, url, **kwargs):
        self.patch_calls.append((url, kwargs))
        if self._patch_responses:
            return self._patch_responses.pop(0)
        return _response(200)


def _patch_client_session(monkeypatch, session: _FakeSession):
    outer = MagicMock()
    outer.__aenter__ = AsyncMock(return_value=session)
    outer.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr(df, "aiohttp", MagicMock(
        ClientSession=lambda **_kwargs: outer,
        ClientTimeout=lambda **kwargs: kwargs,
    ))
    return outer


def _bot_with_token(token: str = "ghp_test123"):
    bot = MagicMock()
    bot.config = MagicMock()
    bot.config.github_token = token
    return bot


def _zerodox_project(**overrides):
    project = {
        "name": "zerodox",
        "repo_url": "https://github.com/Commandershadow9/ZERODOX.git",
        "health_check_url": "https://zerodox.de/api/health",
    }
    project.update(overrides)
    return project


# ---------------------------------------------------------------------------
# ist_aktiviert — Default-AN nur fuer 'zerodox'
# ---------------------------------------------------------------------------

class TestIstAktiviert:
    def test_zerodox_ist_ohne_flag_aktiviert(self):
        assert df.ist_aktiviert({"name": "zerodox"}) is True

    def test_anderes_projekt_ist_ohne_flag_deaktiviert(self):
        assert df.ist_aktiviert({"name": "mayday_sim"}) is False

    def test_explizites_flag_schaltet_anderes_projekt_an(self):
        assert df.ist_aktiviert({"name": "mayday_sim", "github_deploy_feedback": True}) is True

    def test_explizites_flag_schaltet_zerodox_aus(self):
        assert df.ist_aktiviert({"name": "zerodox", "github_deploy_feedback": False}) is False

    def test_kein_projekt_ist_deaktiviert(self):
        assert df.ist_aktiviert(None) is False

    def test_gross_klein_schreibung_und_leerraum_werden_toleriert(self):
        assert df.ist_aktiviert({"name": " ZeroDox "}) is True


# ---------------------------------------------------------------------------
# _parse_repo_slug
# ---------------------------------------------------------------------------

class TestParseRepoSlug:
    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("https://github.com/Commandershadow9/ZERODOX.git", "Commandershadow9/ZERODOX"),
            ("https://github.com/Commandershadow9/ZERODOX", "Commandershadow9/ZERODOX"),
            ("git@github.com:Commandershadow9/ZERODOX.git", "Commandershadow9/ZERODOX"),
            ("https://github.com/Commandershadow9/ZERODOX/", "Commandershadow9/ZERODOX"),
            (None, None),
            ("", None),
            ("https://gitlab.com/foo/bar", None),
        ],
    )
    def test_verschiedene_url_formen(self, url, expected):
        assert df._parse_repo_slug(url) == expected


# ---------------------------------------------------------------------------
# _rollback_zeile
# ---------------------------------------------------------------------------

class TestRollbackZeile:
    def test_exception_pfad_ist_immer_unklar_und_erwaehnt(self):
        zeile, mention = df._rollback_zeile({"backup_created": True, "rolled_back": True}, True)
        assert "unklar" not in zeile  # Text ist der generische Exception-Text
        assert mention is True

    def test_kein_result_ist_unklar_und_erwaehnt(self):
        zeile, mention = df._rollback_zeile(None, False)
        assert mention is True

    def test_rollback_erfolgreich_keine_erwaehnung(self):
        zeile, mention = df._rollback_zeile({"backup_created": True, "rolled_back": True}, False)
        assert "läuft weiter" in zeile
        assert mention is False

    def test_kein_backup_erstellt_keine_erwaehnung(self):
        zeile, mention = df._rollback_zeile({"backup_created": False, "rolled_back": False}, False)
        assert "nichts ausgeliefert" in zeile
        assert mention is False

    def test_backup_aber_rollback_gescheitert_erwaehnt(self):
        zeile, mention = df._rollback_zeile({"backup_created": True, "rolled_back": False}, False)
        assert "fehlgeschlagen" in zeile
        assert mention is True


# ---------------------------------------------------------------------------
# Zustandstexte
# ---------------------------------------------------------------------------

class TestTexte:
    def test_text_started_nennt_kurzen_sha(self):
        text = df._text_started("abc1234")
        assert "abc1234" in text
        assert "Deploy läuft" in text

    def test_text_success_nennt_dauer_und_health_check(self):
        text = df._text_success("abc1234", 12.4, "https://zerodox.de/api/health")
        assert "abc1234" in text
        assert "12 min" in text
        assert "https://zerodox.de/api/health" in text

    def test_text_success_ohne_health_check_url(self):
        text = df._text_success("abc1234", 5.0, None)
        assert "Nachprüfen" not in text

    def test_text_failure_enthaelt_erwaehnung_wenn_noetig(self):
        text = df._text_failure(
            sha7="abc1234",
            phase="build",
            ursache="Build fehlgeschlagen",
            rollback_zeile="❌ Rollback fehlgeschlagen — Betreiber informieren",
            mention_noetig=True,
            technische_details="Traceback ...",
        )
        assert "@Commandershadow9" in text
        assert "abc1234" in text
        assert "<details>" in text

    def test_text_failure_ohne_erwaehnung_wenn_nicht_noetig(self):
        text = df._text_failure(
            sha7="abc1234",
            phase="build",
            ursache="Build fehlgeschlagen",
            rollback_zeile="✅ alte Version läuft weiter",
            mention_noetig=False,
            technische_details=None,
        )
        assert "@Commandershadow9" not in text
        assert "<details>" not in text


# ---------------------------------------------------------------------------
# report() — Commit-Status-Payload
# ---------------------------------------------------------------------------

class TestReportCommitStatus:
    @pytest.mark.asyncio
    async def test_started_setzt_pending_status_ohne_pr_kommentar(self, monkeypatch):
        session = _FakeSession()
        _patch_client_session(monkeypatch, session)
        bot = _bot_with_token()

        await df.report(
            bot,
            _zerodox_project(),
            phase="started",
            deploy_context={"commit_sha": "a" * 40},
        )

        assert len(session.post_calls) == 1
        url, kwargs = session.post_calls[0]
        assert url.endswith(f"/statuses/{'a' * 40}")
        payload = kwargs["json"]
        assert payload["state"] == "pending"
        assert payload["context"] == "zerodox/deploy"
        assert len(payload["description"]) <= 140

    @pytest.mark.asyncio
    async def test_ohne_pr_number_gibt_es_keinen_kommentar_aufruf(self, monkeypatch):
        session = _FakeSession()
        _patch_client_session(monkeypatch, session)
        bot = _bot_with_token()

        await df.report(
            bot,
            _zerodox_project(),
            phase="started",
            deploy_context={"commit_sha": "b" * 40},
        )

        # Nur der Commit-Status-POST an /statuses/..., kein POST an /issues/.../comments
        assert len(session.post_calls) == 1
        assert "/comments" not in session.post_calls[0][0]

    @pytest.mark.asyncio
    async def test_erfolg_setzt_success_status_mit_dauer_in_beschreibung(self, monkeypatch):
        session = _FakeSession()
        _patch_client_session(monkeypatch, session)
        bot = _bot_with_token()

        await df.report(
            bot,
            _zerodox_project(),
            phase="success",
            deploy_context={"commit_sha": "c" * 40},
            duration=754.0,
        )

        payload = session.post_calls[0][1]["json"]
        assert payload["state"] == "success"
        assert "min" in payload["description"]

    @pytest.mark.asyncio
    async def test_fehlschlag_setzt_failure_status(self, monkeypatch):
        session = _FakeSession()
        _patch_client_session(monkeypatch, session)
        bot = _bot_with_token()

        await df.report(
            bot,
            _zerodox_project(),
            phase="failure",
            deploy_context={"commit_sha": "d" * 40},
            result={"error": "Build fehlgeschlagen", "backup_created": False, "rolled_back": False},
            duration=30.0,
        )

        payload = session.post_calls[0][1]["json"]
        assert payload["state"] == "failure"
        assert len(payload["description"]) <= 140

    @pytest.mark.asyncio
    async def test_deaktiviertes_projekt_ruft_keine_api_auf(self, monkeypatch):
        session = _FakeSession()
        _patch_client_session(monkeypatch, session)
        bot = _bot_with_token()

        await df.report(
            bot,
            {"name": "mayday_sim", "repo_url": "https://github.com/Commandershadow9/mayday-sim.git"},
            phase="started",
            deploy_context={"commit_sha": "e" * 40},
        )

        assert session.post_calls == []

    @pytest.mark.asyncio
    async def test_ohne_sha_gibt_es_keinen_aufruf(self, monkeypatch):
        session = _FakeSession()
        _patch_client_session(monkeypatch, session)
        bot = _bot_with_token()

        await df.report(bot, _zerodox_project(), phase="started", deploy_context={})

        assert session.post_calls == []


# ---------------------------------------------------------------------------
# report() — PR-Kommentar: Anlage + PATCH auf dieselbe ID
# ---------------------------------------------------------------------------

class TestReportPrComment:
    @pytest.mark.asyncio
    async def test_mit_pr_number_wird_kommentar_angelegt_mit_marker(self, monkeypatch):
        session = _FakeSession(post_responses=[
            _response(201, {"id": 555}),  # commit status
            _response(201, {"id": 555}),  # issue comment
        ])
        _patch_client_session(monkeypatch, session)
        bot = _bot_with_token()

        await df.report(
            bot,
            _zerodox_project(),
            phase="started",
            deploy_context={"commit_sha": "f" * 40, "pr_number": 3638},
        )

        assert len(session.post_calls) == 2
        comment_url, comment_kwargs = session.post_calls[1]
        assert comment_url.endswith("/issues/3638/comments")
        body = comment_kwargs["json"]["body"]
        assert body.startswith(f"<!-- zerodox-deploy-status sha={'f' * 40} -->")
        assert df._COMMENT_IDS[("Commandershadow9/ZERODOX", "f" * 40)] == 555

    @pytest.mark.asyncio
    async def test_folgeaufruf_patcht_dieselbe_kommentar_id(self, monkeypatch):
        sha = "1" * 40
        df._COMMENT_IDS[("Commandershadow9/ZERODOX", sha)] = 777
        session = _FakeSession(patch_responses=[_response(200)])
        _patch_client_session(monkeypatch, session)
        bot = _bot_with_token()

        await df.report(
            bot,
            _zerodox_project(),
            phase="success",
            deploy_context={"commit_sha": sha, "pr_number": 42},
            duration=120.0,
        )

        assert len(session.patch_calls) == 1
        patch_url, patch_kwargs = session.patch_calls[0]
        assert patch_url.endswith("/issues/comments/777")
        assert patch_kwargs["json"]["body"].startswith(f"<!-- zerodox-deploy-status sha={sha} -->")
        # PATCH erfolgreich → kein Fallback-POST an /issues/.../comments
        assert not any("/comments" in url and "/issues/comments/" not in url for url, _ in session.post_calls)

    @pytest.mark.asyncio
    async def test_gescheiterter_patch_faellt_zurueck_auf_neuen_kommentar(self, monkeypatch):
        sha = "2" * 40
        df._COMMENT_IDS[("Commandershadow9/ZERODOX", sha)] = 888
        session = _FakeSession(
            patch_responses=[_response(404, text_body="Not Found")],
            post_responses=[
                _response(201, {"id": 555}),  # commit status
                _response(201, {"id": 999}),  # neuer Kommentar
            ],
        )
        _patch_client_session(monkeypatch, session)
        bot = _bot_with_token()

        await df.report(
            bot,
            _zerodox_project(),
            phase="success",
            deploy_context={"commit_sha": sha, "pr_number": 42},
            duration=60.0,
        )

        assert len(session.patch_calls) == 1
        assert len(session.post_calls) == 2
        assert df._COMMENT_IDS[("Commandershadow9/ZERODOX", sha)] == 999


# ---------------------------------------------------------------------------
# Fail-Soft — darf NIE eine Exception nach aussen werfen
# ---------------------------------------------------------------------------

class TestFailSoft:
    @pytest.mark.asyncio
    async def test_fehlendes_token_bricht_nicht_ab(self, monkeypatch):
        session = _FakeSession()
        _patch_client_session(monkeypatch, session)
        bot = MagicMock()
        bot.config = MagicMock()
        bot.config.github_token = None
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("GH_TOKEN", raising=False)

        await df.report(
            bot,
            _zerodox_project(),
            phase="started",
            deploy_context={"commit_sha": "3" * 40},
        )

        assert session.post_calls == []  # kein Aufruf, aber auch keine Exception

    @pytest.mark.asyncio
    async def test_http_500_bricht_report_nicht_ab(self, monkeypatch):
        session = _FakeSession(post_responses=[_response(500, text_body="Internal Server Error")])
        _patch_client_session(monkeypatch, session)
        bot = _bot_with_token()

        # Darf keine Exception werfen, obwohl GitHub 500 liefert.
        await df.report(
            bot,
            _zerodox_project(),
            phase="started",
            deploy_context={"commit_sha": "4" * 40},
        )

    @pytest.mark.asyncio
    async def test_exception_im_http_aufruf_bricht_report_nicht_ab(self, monkeypatch):
        session = MagicMock()
        session.post = MagicMock(side_effect=RuntimeError("Verbindung abgebrochen"))
        _patch_client_session(monkeypatch, session)
        bot = _bot_with_token()

        # `_http_call` faengt das ab — report() darf trotzdem sauber durchlaufen.
        await df.report(
            bot,
            _zerodox_project(),
            phase="started",
            deploy_context={"commit_sha": "5" * 40},
        )

    @pytest.mark.asyncio
    async def test_fehlende_repo_url_bricht_nicht_ab(self, monkeypatch):
        session = _FakeSession()
        _patch_client_session(monkeypatch, session)
        bot = _bot_with_token()

        await df.report(
            bot,
            {"name": "zerodox"},  # kein repo_url
            phase="started",
            deploy_context={"commit_sha": "6" * 40},
        )

        assert session.post_calls == []
