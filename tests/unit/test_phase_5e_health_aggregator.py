"""Tests fuer Phase-5e Health-Aggregator Cog.

Regressionstest fuer Bug #565: discord.py 2.6+ hat `TextChannel.pins()` von
einem awaitable List-Returner zu einem AsyncIterator umgebaut. Die alte
`pins = await channel.pins()` Form wirft daher `TypeError`, der vom
`with suppress(discord.HTTPException)` nicht mehr gefangen wird und den
gesamten 5-Min-Status-Embed-Loop killt.
"""
from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

# Cog-Modul importieren — DB-Pfad in Temp umbiegen
SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture
def cog_module(tmp_path, monkeypatch):
    """Importiert Cog mit Temp-DB."""
    import importlib

    # DB-Pfad auf tmp umlenken vor Modul-Import-Init
    from cogs import phase_5e_health_aggregator as mod

    importlib.reload(mod)
    monkeypatch.setattr(mod, "DB_PATH", tmp_path / "health_history.db")
    return mod


@pytest.fixture
def mock_bot():
    bot = MagicMock()
    bot.user = MagicMock()
    bot.user.id = 999
    bot.wait_until_ready = AsyncMock()
    return bot


@pytest.fixture
def cog(cog_module, mock_bot):
    return cog_module.Phase5eHealthAggregator(mock_bot)


def _make_async_iterator(items):
    """Erzeugt einen Async-Iterator fuer Mock-Channels."""

    class _AIter:
        def __init__(self, data):
            self._data = list(data)

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self._data:
                raise StopAsyncIteration
            return self._data.pop(0)

    return _AIter(items)


def _make_poll_result(cog_module, name="Runner-VM", status="ok"):
    target = cog_module.HealthTarget(
        name=name, url="http://test.local/health", role_hint="ci-runner"
    )
    response = MagicMock()
    response.status = status
    response.host = name
    response.role = "ci-runner"
    response.uptime_seconds = 3600
    response.components = {"docker": {}, "redis": {}}
    response.critical_alerts = []
    response.warning_alerts = []
    response.alerts = []
    response.http_status = 200
    return cog_module.PollResult(
        target=target,
        polled_at=datetime.now(timezone.utc),
        response=response,
    )


# ────────────────────────────────────────────────────────────────────────
# Regressionstest #565: pins() ist AsyncIterator in discord.py 2.6+
# ────────────────────────────────────────────────────────────────────────


class TestEmbedLoopPinsCompat:
    """pins() darf nicht mehr awaited werden — nur async-for."""

    @pytest.mark.asyncio
    async def test_pins_is_consumed_via_async_for(self, cog, cog_module):
        """Smoke: embed_loop nutzt async-for fuer pins(), nicht await."""
        cog._latest_results = [_make_poll_result(cog_module)]

        channel = MagicMock()
        channel.pins = MagicMock(return_value=_make_async_iterator([]))
        channel.history = MagicMock(return_value=_make_async_iterator([]))
        channel.send = AsyncMock(return_value=MagicMock(spec=discord.Message))
        cog.bot.get_channel = MagicMock(return_value=channel)

        # Wenn der alte Code (`pins = await channel.pins()`) noch vorhanden
        # waere, wuerde ein TypeError fliegen (kann _AIter nicht awaiten).
        await cog.embed_loop.coro(cog)

        channel.pins.assert_called_once()
        channel.send.assert_called_once()
        assert cog._dashboard_message is not None

    @pytest.mark.asyncio
    async def test_pins_raises_forbidden_falls_back_to_history(self, cog, cog_module):
        """pins() Forbidden → history() wird trotzdem versucht."""
        cog._latest_results = [_make_poll_result(cog_module)]

        class _ForbiddenIter:
            def __aiter__(self):
                return self

            async def __anext__(self):
                raise discord.Forbidden(MagicMock(status=403, reason="Missing perms"), "no")

        existing_msg = MagicMock(spec=discord.Message)
        existing_msg.author.id = cog.bot.user.id
        existing_msg.embeds = [MagicMock(title="ShadowOps Phase 5e — Health-Aggregator")]
        existing_msg.edit = AsyncMock()

        channel = MagicMock()
        channel.pins = MagicMock(return_value=_ForbiddenIter())
        channel.history = MagicMock(return_value=_make_async_iterator([existing_msg]))
        channel.send = AsyncMock()
        cog.bot.get_channel = MagicMock(return_value=channel)

        await cog.embed_loop.coro(cog)

        # history-fallback fand existing message und editierte
        existing_msg.edit.assert_called_once()
        channel.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_pins_finds_existing_pinned_dashboard_embed(self, cog, cog_module):
        """Wenn ein Phase-5e Embed gepinnt ist, wird es geladen + editiert."""
        cog._latest_results = [_make_poll_result(cog_module)]

        pinned = MagicMock(spec=discord.Message)
        pinned.author.id = cog.bot.user.id
        pinned.embeds = [MagicMock(title="🟢 ShadowOps Phase 5e — Health-Aggregator")]
        pinned.edit = AsyncMock()

        channel = MagicMock()
        channel.pins = MagicMock(return_value=_make_async_iterator([pinned]))
        channel.history = MagicMock(return_value=_make_async_iterator([]))
        channel.send = AsyncMock()
        cog.bot.get_channel = MagicMock(return_value=channel)

        await cog.embed_loop.coro(cog)

        pinned.edit.assert_called_once()
        assert cog._dashboard_message is pinned
        channel.send.assert_not_called()


# ────────────────────────────────────────────────────────────────────────
# Generelle Resilienz: embed_loop darf nicht silent sterben
# ────────────────────────────────────────────────────────────────────────


class TestEmbedLoopResilience:
    @pytest.mark.asyncio
    async def test_no_results_returns_silently(self, cog):
        """Beim ersten Loop ohne Poll-Ergebnisse → silent return."""
        cog._latest_results = []
        channel = MagicMock()
        cog.bot.get_channel = MagicMock(return_value=channel)

        await cog.embed_loop.coro(cog)

        channel.send.assert_not_called() if hasattr(channel.send, "assert_not_called") else None

    @pytest.mark.asyncio
    async def test_channel_not_found_returns_silently(self, cog, cog_module):
        """get_channel returnt None → silent return, kein Crash."""
        cog._latest_results = [_make_poll_result(cog_module)]
        cog.bot.get_channel = MagicMock(return_value=None)

        await cog.embed_loop.coro(cog)  # darf nicht raisen

    @pytest.mark.asyncio
    async def test_edit_notfound_falls_back_to_send(self, cog, cog_module):
        """Wenn Cached-Message weg ist, wird neue Nachricht gesendet."""
        cog._latest_results = [_make_poll_result(cog_module)]

        stale_msg = MagicMock(spec=discord.Message)
        stale_msg.edit = AsyncMock(side_effect=discord.NotFound(MagicMock(status=404, reason="x"), "x"))
        cog._dashboard_message = stale_msg

        new_msg = MagicMock(spec=discord.Message)
        new_msg.pin = AsyncMock()

        channel = MagicMock()
        channel.send = AsyncMock(return_value=new_msg)
        cog.bot.get_channel = MagicMock(return_value=channel)

        await cog.embed_loop.coro(cog)

        stale_msg.edit.assert_called_once()
        channel.send.assert_called_once()
        assert cog._dashboard_message is new_msg

    @pytest.mark.asyncio
    async def test_outer_exception_is_caught_and_logged(self, cog, cog_module):
        """Unerwarteter Fehler killt den Loop nicht (logger.exception)."""
        cog._latest_results = [_make_poll_result(cog_module)]
        cog.logger = MagicMock()

        channel = MagicMock()
        # Provoziere TypeError durch kaputte pins()
        channel.pins = MagicMock(side_effect=TypeError("kaputt"))
        cog.bot.get_channel = MagicMock(return_value=channel)

        await cog.embed_loop.coro(cog)  # darf nicht raisen

        cog.logger.exception.assert_called_once()
        assert "[5e] embed_loop" in cog.logger.exception.call_args[0][0]


# ────────────────────────────────────────────────────────────────────────
# Drift-Embed: discord.NotFound darf nicht den Loop killen
# ────────────────────────────────────────────────────────────────────────


class TestDriftHandling:
    @pytest.mark.asyncio
    async def test_drift_first_run_no_alert(self, cog, cog_module):
        """Erster Lauf lernt nur, alertet nicht."""
        result = _make_poll_result(cog_module, status="ok")

        channel = MagicMock()
        channel.send = AsyncMock()
        cog.bot.get_channel = MagicMock(return_value=channel)

        await cog._handle_drifts([result])

        assert cog._last_status_per_host[result.target.name] == "ok"
        channel.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_drift_status_change_sends_embed(self, cog, cog_module):
        """Status-Wechsel ok → critical loest Embed aus."""
        cog._last_status_per_host = {"Runner-VM": "ok"}
        result = _make_poll_result(cog_module, name="Runner-VM", status="critical")

        channel = MagicMock()
        channel.send = AsyncMock()
        cog.bot.get_channel = MagicMock(return_value=channel)

        await cog._handle_drifts([result])

        channel.send.assert_called_once()
        assert cog._last_status_per_host["Runner-VM"] == "critical"

    @pytest.mark.asyncio
    async def test_drift_no_change_no_embed(self, cog, cog_module):
        """Identischer Status → kein Embed."""
        cog._last_status_per_host = {"Runner-VM": "degraded"}
        result = _make_poll_result(cog_module, name="Runner-VM", status="degraded")

        channel = MagicMock()
        channel.send = AsyncMock()
        cog.bot.get_channel = MagicMock(return_value=channel)

        await cog._handle_drifts([result])

        channel.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_drift_http_error_does_not_kill_loop(self, cog, cog_module):
        """HTTPException beim Senden wird gefangen + geloggt."""
        cog._last_status_per_host = {"Runner-VM": "ok"}
        cog.logger = MagicMock()
        result = _make_poll_result(cog_module, name="Runner-VM", status="critical")

        channel = MagicMock()
        channel.send = AsyncMock(
            side_effect=discord.HTTPException(MagicMock(status=500, reason="x"), "fail")
        )
        cog.bot.get_channel = MagicMock(return_value=channel)

        await cog._handle_drifts([result])  # darf nicht raisen

        cog.logger.error.assert_called_once()


# ────────────────────────────────────────────────────────────────────────
# Incident-Grouping (Modul 2): aufeinanderfolgende Drifts = 1 Message
# ────────────────────────────────────────────────────────────────────────


def _make_incident_channel():
    """Channel-Mock mit send() → Message(id=...) und edit-fähiger partial message."""
    sent_message = MagicMock(spec=discord.Message)
    sent_message.id = 555000111

    edited = MagicMock(spec=discord.Message)
    edited.edit = AsyncMock()

    channel = MagicMock()
    channel.send = AsyncMock(return_value=sent_message)
    channel.get_partial_message = MagicMock(return_value=edited)
    return channel, sent_message, edited


class TestIncidentGrouping:
    @pytest.mark.asyncio
    async def test_three_drifts_one_message_then_edits(self, cog, cog_module):
        """3 aufeinanderfolgende Drifts desselben Hosts → 1 send + N edits."""
        cog._last_status_per_host = {"Runner-VM": "ok"}
        channel, sent_msg, edited = _make_incident_channel()
        cog.bot.get_channel = MagicMock(return_value=channel)

        # Drift 1: ok → unreachable (öffnet Incident, postet Message)
        await cog._handle_drifts([_make_poll_result(cog_module, name="Runner-VM", status="unreachable")])
        # Drift 2: unreachable → critical (editiert bestehende Message)
        await cog._handle_drifts([_make_poll_result(cog_module, name="Runner-VM", status="critical")])
        # Drift 3: critical → degraded (editiert bestehende Message)
        await cog._handle_drifts([_make_poll_result(cog_module, name="Runner-VM", status="degraded")])

        # Genau EIN send, danach nur noch edits
        channel.send.assert_called_once()
        assert edited.edit.call_count == 2
        channel.get_partial_message.assert_called_with(sent_msg.id)

        incident = cog._open_incidents["Runner-VM"]
        assert incident.message_id == sent_msg.id
        assert len(incident.transitions) == 3
        assert incident.worst_status == "unreachable"

    @pytest.mark.asyncio
    async def test_recovery_edit_shows_downtime_and_closes(self, cog, cog_module):
        """Recovery → finale Edit mit Downtime, Incident wird geschlossen."""
        cog._last_status_per_host = {"Runner-VM": "ok"}
        channel, sent_msg, edited = _make_incident_channel()
        cog.bot.get_channel = MagicMock(return_value=channel)

        # Incident öffnen
        await cog._handle_drifts([_make_poll_result(cog_module, name="Runner-VM", status="critical")])
        # started_at manuell zurückdatieren für messbare Downtime
        cog._open_incidents["Runner-VM"].started_at = datetime.now(timezone.utc) - timedelta(minutes=7)

        # Recovery: critical → ok
        await cog._handle_drifts([_make_poll_result(cog_module, name="Runner-VM", status="ok")])

        # Recovery editiert die bestehende Message (kein zweiter send)
        channel.send.assert_called_once()
        edited.edit.assert_called_once()

        # Finale Embed enthält Downtime-Klartext + Selbst-Erholung
        final_embed = edited.edit.call_args.kwargs["embed"]
        assert "Ausfall" in final_embed.description
        assert "Min" in final_embed.description
        assert "Selbst-erholt" in final_embed.description

        # Incident geschlossen
        assert "Runner-VM" not in cog._open_incidents

    @pytest.mark.asyncio
    async def test_two_hosts_two_incidents(self, cog, cog_module):
        """Zwei verschiedene Hosts → zwei getrennte Incidents (2 sends)."""
        cog._last_status_per_host = {"Runner-VM": "ok", "ZERODOX Production": "ok"}
        channel, _sent, _edited = _make_incident_channel()
        # jeder send liefert eine eigene Message-id
        ids = iter([111, 222])

        def _send(*args, **kwargs):
            m = MagicMock(spec=discord.Message)
            m.id = next(ids)
            return m

        channel.send = AsyncMock(side_effect=_send)
        cog.bot.get_channel = MagicMock(return_value=channel)

        await cog._handle_drifts([
            _make_poll_result(cog_module, name="Runner-VM", status="critical"),
            _make_poll_result(cog_module, name="ZERODOX Production", status="degraded"),
        ])

        assert channel.send.call_count == 2
        assert set(cog._open_incidents.keys()) == {"Runner-VM", "ZERODOX Production"}
        assert cog._open_incidents["Runner-VM"].message_id == 111
        assert cog._open_incidents["ZERODOX Production"].message_id == 222

    @pytest.mark.asyncio
    async def test_edit_failure_falls_back_to_new_post(self, cog, cog_module):
        """Wenn edit() 404 wirft (Message gelöscht), wird neu gepostet + id aktualisiert."""
        cog._last_status_per_host = {"Runner-VM": "ok"}
        cog.logger = MagicMock()

        first_msg = MagicMock(spec=discord.Message)
        first_msg.id = 100
        repost_msg = MagicMock(spec=discord.Message)
        repost_msg.id = 200

        stale = MagicMock(spec=discord.Message)
        stale.edit = AsyncMock(side_effect=discord.NotFound(MagicMock(status=404, reason="x"), "gone"))

        channel = MagicMock()
        channel.send = AsyncMock(side_effect=[first_msg, repost_msg])
        channel.get_partial_message = MagicMock(return_value=stale)
        cog.bot.get_channel = MagicMock(return_value=channel)

        # Drift 1 öffnet Incident (send → id 100)
        await cog._handle_drifts([_make_poll_result(cog_module, name="Runner-VM", status="critical")])
        # Drift 2 versucht edit (404) → neu posten (send → id 200)
        await cog._handle_drifts([_make_poll_result(cog_module, name="Runner-VM", status="unreachable")])

        assert channel.send.call_count == 2
        stale.edit.assert_called_once()
        assert cog._open_incidents["Runner-VM"].message_id == 200

    @pytest.mark.asyncio
    async def test_recovery_without_open_incident_posts_simple(self, cog, cog_module):
        """Recovery ohne offenen Incident (z.B. nach Bot-Restart) → einfacher Post, kein Crash."""
        cog._last_status_per_host = {"Runner-VM": "critical"}
        channel, sent_msg, _edited = _make_incident_channel()
        cog.bot.get_channel = MagicMock(return_value=channel)

        await cog._handle_drifts([_make_poll_result(cog_module, name="Runner-VM", status="ok")])

        channel.send.assert_called_once()
        assert "Runner-VM" not in cog._open_incidents
