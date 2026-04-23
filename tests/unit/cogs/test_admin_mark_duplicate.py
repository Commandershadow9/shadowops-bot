"""Tests fuer /mark-duplicate Slash-Command."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from cogs.admin import AdminCog


@pytest.mark.asyncio
async def test_mark_duplicate_merges_findings():
    cog = AdminCog.__new__(AdminCog)
    cog.bot = MagicMock()
    cog.bot.security_engine = MagicMock()
    cog.bot.security_engine.scan_agent = MagicMock()
    cog.bot.security_engine.scan_agent.db = MagicMock()
    cog.bot.security_engine.scan_agent.db.pool = MagicMock()
    cog.bot.security_engine.scan_agent.db.pool.execute = AsyncMock()
    cog.bot.security_engine.scan_agent.db.pool.fetchrow = AsyncMock(
        return_value={
            "id": 101,
            "title": "child",
            "status": "open",
            "affected_project": "guildscout",
        }
    )
    cog.bot.security_engine.scan_agent.learning_bridge = MagicMock()
    cog.bot.security_engine.scan_agent.learning_bridge.is_connected = True
    cog.bot.security_engine.scan_agent.learning_bridge.record_manual_merge = AsyncMock()

    interaction = MagicMock()
    interaction.user.id = 42
    interaction.user.name = "christian"
    interaction.response.send_message = AsyncMock()

    await cog.mark_duplicate.callback(cog, interaction, parent_id=100, child_id=101)

    # Child wird als duplicate_of markiert, nicht geloescht
    cog.bot.security_engine.scan_agent.db.pool.execute.assert_any_await(
        "UPDATE findings SET status='duplicate_of', fixed_at=NOW() WHERE id=$1", 101
    )
    # Learning-Feedback wurde geschrieben
    cog.bot.security_engine.scan_agent.learning_bridge.record_manual_merge.assert_awaited_once()
    interaction.response.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_mark_duplicate_rejects_same_id():
    cog = AdminCog.__new__(AdminCog)
    cog.bot = MagicMock()

    interaction = MagicMock()
    interaction.response.send_message = AsyncMock()

    await cog.mark_duplicate.callback(cog, interaction, parent_id=100, child_id=100)

    interaction.response.send_message.assert_awaited_once()
    args, kwargs = interaction.response.send_message.call_args
    assert "unterschiedlich" in args[0]
    assert kwargs.get("ephemeral") is True


@pytest.mark.asyncio
async def test_mark_duplicate_missing_engine():
    cog = AdminCog.__new__(AdminCog)
    cog.bot = MagicMock(spec=[])  # kein security_engine Attribut

    interaction = MagicMock()
    interaction.response.send_message = AsyncMock()

    await cog.mark_duplicate.callback(cog, interaction, parent_id=100, child_id=101)

    interaction.response.send_message.assert_awaited_once()
    args, kwargs = interaction.response.send_message.call_args
    assert "nicht verfuegbar" in args[0]


@pytest.mark.asyncio
async def test_mark_duplicate_child_not_found():
    cog = AdminCog.__new__(AdminCog)
    cog.bot = MagicMock()
    cog.bot.security_engine = MagicMock()
    cog.bot.security_engine.scan_agent = MagicMock()
    cog.bot.security_engine.scan_agent.db = MagicMock()
    cog.bot.security_engine.scan_agent.db.pool = MagicMock()
    cog.bot.security_engine.scan_agent.db.pool.execute = AsyncMock()
    cog.bot.security_engine.scan_agent.db.pool.fetchrow = AsyncMock(return_value=None)

    interaction = MagicMock()
    interaction.response.send_message = AsyncMock()

    await cog.mark_duplicate.callback(cog, interaction, parent_id=100, child_id=999)

    interaction.response.send_message.assert_awaited_once()
    args, kwargs = interaction.response.send_message.call_args
    assert "999" in args[0]
    # Kein UPDATE darf gefeuert worden sein
    cog.bot.security_engine.scan_agent.db.pool.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_mark_duplicate_no_learning_bridge_still_succeeds():
    """Wenn Learning-Bridge nicht verbunden ist, Update trotzdem ausfuehren."""
    cog = AdminCog.__new__(AdminCog)
    cog.bot = MagicMock()
    cog.bot.security_engine = MagicMock()
    cog.bot.security_engine.scan_agent = MagicMock()
    cog.bot.security_engine.scan_agent.db = MagicMock()
    cog.bot.security_engine.scan_agent.db.pool = MagicMock()
    cog.bot.security_engine.scan_agent.db.pool.execute = AsyncMock()
    cog.bot.security_engine.scan_agent.db.pool.fetchrow = AsyncMock(
        return_value={
            "id": 101,
            "title": "child",
            "status": "open",
            "affected_project": "guildscout",
        }
    )
    cog.bot.security_engine.scan_agent.learning_bridge = MagicMock()
    cog.bot.security_engine.scan_agent.learning_bridge.is_connected = False
    cog.bot.security_engine.scan_agent.learning_bridge.record_manual_merge = AsyncMock()

    interaction = MagicMock()
    interaction.user.id = 42
    interaction.user.name = "christian"
    interaction.response.send_message = AsyncMock()

    await cog.mark_duplicate.callback(cog, interaction, parent_id=100, child_id=101)

    cog.bot.security_engine.scan_agent.db.pool.execute.assert_any_await(
        "UPDATE findings SET status='duplicate_of', fixed_at=NOW() WHERE id=$1", 101
    )
    cog.bot.security_engine.scan_agent.learning_bridge.record_manual_merge.assert_not_awaited()
    interaction.response.send_message.assert_awaited_once()
