"""Tests für den Orchestrator-Entrypoint (sec:trigger-Loop, W1)."""
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.integrations.security_engine.team import orchestrator_main


@pytest.mark.asyncio
async def test_handle_trigger_message_daily():
    """Gültige Payload → handle_trigger mit trigger='daily'."""
    orch = MagicMock()
    orch.handle_trigger = AsyncMock(return_value=[MagicMock(), MagicMock()])
    config = MagicMock()
    config.security_team_projects = {"zerodox": {"npm_audit_path": "/tmp/x"}}
    config.security_team_active_workers = ["npm_audit"]

    jobs = await orchestrator_main.handle_trigger_message(
        orch, config, json.dumps({"trigger": "daily"})
    )

    assert len(jobs) == 2
    orch.handle_trigger.assert_awaited_once_with(
        projects=config.security_team_projects,
        active_workers=["npm_audit"],
        trigger="daily",
    )


@pytest.mark.asyncio
async def test_handle_trigger_message_kaputte_payload_faellt_auf_manual():
    """Ungültiges JSON darf nicht crashen — Fallback trigger='manual'."""
    orch = MagicMock()
    orch.handle_trigger = AsyncMock(return_value=[])
    config = MagicMock()
    config.security_team_projects = {}
    config.security_team_active_workers = []

    await orchestrator_main.handle_trigger_message(orch, config, "{kaputt")

    orch.handle_trigger.assert_awaited_once_with(
        projects={}, active_workers=[], trigger="manual",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [None, ""])
async def test_handle_trigger_message_leere_payload_faellt_auf_manual(raw):
    """raw=None/"" (kein Redis-Payload-Body) darf nicht crashen — Fallback trigger='manual'."""
    orch = MagicMock()
    orch.handle_trigger = AsyncMock(return_value=[])
    config = MagicMock()
    config.security_team_projects = {}
    config.security_team_active_workers = []

    await orchestrator_main.handle_trigger_message(orch, config, raw)

    orch.handle_trigger.assert_awaited_once_with(
        projects={}, active_workers=[], trigger="manual",
    )


@pytest.mark.asyncio
async def test_handle_trigger_message_trigger_null_faellt_auf_manual():
    """{"trigger": null} darf NICHT zu trigger='None' fuehren — Fallback trigger='manual'."""
    orch = MagicMock()
    orch.handle_trigger = AsyncMock(return_value=[])
    config = MagicMock()
    config.security_team_projects = {}
    config.security_team_active_workers = []

    await orchestrator_main.handle_trigger_message(
        orch, config, json.dumps({"trigger": None})
    )

    orch.handle_trigger.assert_awaited_once_with(
        projects={}, active_workers=[], trigger="manual",
    )
