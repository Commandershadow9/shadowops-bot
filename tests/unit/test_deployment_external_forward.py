"""Tests für _forward_deploy_to_external (Issue #504).

Der Deploy-Post an den externen Kunden-Channel (`🚀-deploy-log`) blieb aus,
weil `_forward_deploy_to_external` den rohen GitHub-Repo-Namen ("mayday-sim",
Bindestrich) direkt für `config.projects.get(...)` nutzte — der Config-Key ist
aber "mayday_sim" (Underscore). Ergebnis: leere `external_notifications`, kein
Post. `deploy_project`/`_trigger_deployment` normalisieren bereits; diese
Funktion wurde übersehen (gleicher Bug-Typ wie Vorfall 2026-05-25, PR #449/#450).
"""
from __future__ import annotations

import discord
import pytest
from unittest.mock import AsyncMock, MagicMock

from src.integrations.deployment_manager import DeploymentManager


def _mgr_with_external(projects: dict):
    """DeploymentManager-Stub mit gemocktem externen Channel + roher config.projects."""
    mgr = DeploymentManager.__new__(DeploymentManager)
    mgr.logger = MagicMock()
    ext_channel = MagicMock()
    ext_channel.send = AsyncMock()
    mgr.bot = MagicMock()
    mgr.bot.get_channel = MagicMock(return_value=ext_channel)
    mgr.bot.config = MagicMock()
    mgr.bot.config.projects = projects
    return mgr, ext_channel


def _deploy_embed():
    embed = discord.Embed(
        title="✅ Deployment erfolgreich: mayday-sim", description="alle Schritte ok"
    )
    embed.add_field(name="Projekt", value="`mayday-sim`", inline=True)
    embed.add_field(name="Branch", value="`main`", inline=True)
    embed.add_field(name="Dauer", value="42.0s", inline=True)
    return embed


def _mayday_projects():
    return {
        "mayday_sim": {
            "external_notifications": [
                {
                    "enabled": True,
                    "notify_on": {"deployments": True},
                    "deploy_channel_id": 1486899717362421840,
                }
            ]
        }
    }


@pytest.mark.asyncio
async def test_forward_resolves_dashed_repo_name_to_underscore_config_key():
    """Repo 'mayday-sim' (Bindestrich) muss Config-Key 'mayday_sim' (Underscore) treffen."""
    mgr, ext_channel = _mgr_with_external(_mayday_projects())

    await mgr._forward_deploy_to_external("mayday-sim", _deploy_embed())

    # Vor dem Fix: config.projects.get("mayday-sim") → {} → kein Versand.
    ext_channel.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_forward_exact_config_key_still_posts():
    """Regressionsschutz: exakter Key-Match (Underscore == Underscore) postet weiterhin."""
    mgr, ext_channel = _mgr_with_external(_mayday_projects())

    await mgr._forward_deploy_to_external("mayday_sim", _deploy_embed())

    ext_channel.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_forward_skips_when_deployments_flag_off():
    """notify_on.deployments=False → kein Post, auch bei korrekt aufgelöstem Projekt."""
    projects = _mayday_projects()
    projects["mayday_sim"]["external_notifications"][0]["notify_on"]["deployments"] = False
    mgr, ext_channel = _mgr_with_external(projects)

    await mgr._forward_deploy_to_external("mayday-sim", _deploy_embed())

    ext_channel.send.assert_not_called()
