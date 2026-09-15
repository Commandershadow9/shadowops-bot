"""Tests für die humanisierten Deploy-Embeds (Klartext-Zusammenfassung).

Verifiziert: Success-/Failure-Embed fassen die Step-Liste zu Klartext zusammen
(X/Y Schritten ok), heben den fehlgeschlagenen Schritt hervor und nutzen die
zentrale Dauer-Formatierung — statt einer rohen 10er-Step-Liste als einzigem
Signal.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.integrations.deployment_manager import (
    DeploymentManager,
    _concise_deploy_error,
    _format_deploy_trigger,
    _summarize_steps,
    _format_deploy_duration,
)


def _mgr_with_channel():
    """DeploymentManager-Stub mit gemocktem Channel; gibt (mgr, channel) zurück."""
    mgr = DeploymentManager.__new__(DeploymentManager)
    mgr.logger = MagicMock()
    mgr.deployment_channel_id = 12345
    # __new__ umgeht __init__, deshalb fehlt self.config. _kanal_fuer() liest
    # daraus die lebende Projektkonfiguration; ohne das Attribut bricht jeder
    # Embed-Test mit AttributeError ab. Leere projects-Abbildung bedeutet:
    # kein projekteigener Kanal, es greift der Rückfall auf
    # deployment_channel_id — genau der Pfad, den diese Tests prüfen.
    mgr.config = SimpleNamespace(projects={})
    channel = MagicMock()
    channel.send = AsyncMock()
    mgr.bot = MagicMock()
    mgr.bot.get_channel = MagicMock(return_value=channel)
    # _forward_deploy_to_external überspringen (kein externer Versand im Test)
    mgr._forward_deploy_to_external = AsyncMock()
    return mgr, channel


def _sent_embed(channel):
    return channel.send.call_args.kwargs["embed"]


# ---------- Helper-Funktionen ----------

def test_summarize_steps_all_ok():
    steps = ["`10:00:00` Build ok", "`10:00:05` Migrate ok", "`10:00:10` Restart ok"]
    ok, total, failed = _summarize_steps(steps)
    assert ok == 3
    assert total == 3
    assert failed is None


def test_summarize_steps_detects_failure():
    steps = [
        "`10:00:00` Build ok",
        "`10:00:05` ❌ Migrate fehlgeschlagen",
        "`10:00:10` Restart ok",
    ]
    ok, total, failed = _summarize_steps(steps)
    assert total == 3
    assert ok == 2
    assert failed is not None
    assert "Migrate" in failed


def test_format_deploy_duration_short_vs_long():
    # kurz -> Sekunden
    assert _format_deploy_duration(12.3) == "12.3s"
    # lang -> deutscher Klartext via format_downtime
    long = _format_deploy_duration(150)
    assert "Min" in long


def test_format_deploy_trigger_links_pr_issue_and_commit():
    text = _format_deploy_trigger({
        "pr_number": 102,
        "pr_title": "Demo-Zähler aktivieren",
        "pr_url": "https://github.com/acme/app/pull/102",
        "issues": [101, 103],
        "repo_url": "https://github.com/acme/app",
        "commit_sha": "abcdef123456",
        "commit_url": "https://github.com/acme/app/commit/abcdef123456",
    })
    assert "PR #102" in text
    assert "Issue #103" in text
    assert "`abcdef1`" in text


def test_concise_deploy_error_prefers_specific_last_error():
    error = """Post-deploy command failed (exit=1):
stdout: Quality läuft noch
stderr: FEHLER: Quality für 91e19d0 ist durch — Ergebnis: cancelled."""
    assert _concise_deploy_error(error) == (
        "FEHLER: Quality für 91e19d0 ist durch — Ergebnis: cancelled."
    )


@pytest.mark.asyncio
async def test_progress_message_is_sent_and_edited():
    mgr, channel = _mgr_with_channel()
    mgr.projects = {"ZERODOX": {"branch": "main"}}
    progress_message = MagicMock()
    progress_message.edit = AsyncMock()
    channel.send.return_value = progress_message

    await mgr._send_deployment_started(
        "ZERODOX",
        "main",
        deploy_context={"commit_sha": "abcdef123456", "branch": "main"},
    )
    await mgr._send_deployment_update("ZERODOX", "📦 Backup wird erstellt …")

    channel.send.assert_awaited_once()
    progress_message.edit.assert_awaited_once()
    embed = progress_message.edit.call_args.kwargs["embed"]
    assert "läuft" in embed.title
    assert any(field.name == "Aktueller Schritt" for field in embed.fields)


# ---------- Success-Embed ----------

@pytest.mark.asyncio
async def test_success_embed_summarizes_steps():
    mgr, channel = _mgr_with_channel()
    mgr._deploy_steps = {
        "ZERODOX": ["`10:00:00` Build ok", "`10:00:05` Migrate ok", "`10:00:10` Restart ok"]
    }

    await mgr._send_deployment_success("ZERODOX", "main", 42.0, {})

    embed = _sent_embed(channel)
    assert "erfolgreich" in embed.title.lower()
    # Klartext-Zusammenfassung statt blossem "deployed successfully"
    assert "3 Schritte" in embed.description
    assert "successfully" not in embed.description


# ---------- Failure-Embed ----------

@pytest.mark.asyncio
async def test_failure_embed_highlights_failed_step():
    mgr, channel = _mgr_with_channel()
    mgr._deploy_steps = {
        "ZERODOX": [
            "`10:00:00` Build ok",
            "`10:00:05` Migrate ok",
            "`10:00:10` ❌ Health-Check fehlgeschlagen",
        ]
    }
    result = {"error": "health endpoint returned 503", "rolled_back": True}

    await mgr._send_deployment_failure("ZERODOX", "main", 30.0, result)

    embed = _sent_embed(channel)
    assert "fehlgeschlagen" in embed.title.lower()
    # Zusammenfassung nennt Fortschritt; der konkrete Schritt bleibt separat sichtbar.
    assert "2 erfolgreichen Statusmeldungen" in embed.description
    # Eigenes Hervorhebungs-Feld
    field_names = [f.name for f in embed.fields]
    assert any("Fehlgeschlagen bei" in n for n in field_names)


@pytest.mark.asyncio
async def test_failure_embed_without_steps_does_not_crash():
    mgr, channel = _mgr_with_channel()
    mgr._deploy_steps = {}
    result = {"error": "boom", "rolled_back": False}

    await mgr._send_deployment_failure("ZERODOX", "main", 5.0, result)

    embed = _sent_embed(channel)
    assert "fehlgeschlagen" in embed.title.lower()
    assert embed.description  # nie leer
