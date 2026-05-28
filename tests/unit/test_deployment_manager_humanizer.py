"""Tests für die humanisierten Deploy-Embeds (Klartext-Zusammenfassung).

Verifiziert: Success-/Failure-Embed fassen die Step-Liste zu Klartext zusammen
(X/Y Schritten ok), heben den fehlgeschlagenen Schritt hervor und nutzen die
zentrale Dauer-Formatierung — statt einer rohen 10er-Step-Liste als einzigem
Signal.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.integrations.deployment_manager import (
    DeploymentManager,
    _summarize_steps,
    _format_deploy_duration,
)


def _mgr_with_channel():
    """DeploymentManager-Stub mit gemocktem Channel; gibt (mgr, channel) zurück."""
    mgr = DeploymentManager.__new__(DeploymentManager)
    mgr.logger = MagicMock()
    mgr.deployment_channel_id = 12345
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
    # Zusammenfassung: 2/3 ok + fehlgeschlagener Schritt benannt
    assert "2/3" in embed.description
    assert "Health-Check" in embed.description
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
