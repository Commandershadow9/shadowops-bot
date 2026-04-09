"""
Tests for bot startup guards.
"""

from unittest.mock import Mock

from src import bot as bot_module


def test_main_skips_second_instance(monkeypatch):
    config = Mock(discord_token="test-token", debug_mode=False)
    logger = Mock()
    lock = Mock()
    lock.acquire.return_value = False
    lock.read_owner_pid.return_value = 4242

    monkeypatch.setattr(bot_module, "get_config", lambda: config)
    monkeypatch.setattr(bot_module, "setup_logger", lambda *args, **kwargs: logger)
    monkeypatch.setattr(bot_module, "ProcessLock", lambda path: lock)
    shadowops_bot = Mock()
    monkeypatch.setattr(bot_module, "ShadowOpsBot", shadowops_bot)

    assert bot_module.main() == 0
    shadowops_bot.assert_not_called()
    lock.release.assert_called_once()


def test_main_runs_bot_when_lock_is_free(monkeypatch):
    config = Mock(discord_token="test-token", debug_mode=False)
    logger = Mock()
    lock = Mock()
    lock.acquire.return_value = True

    bot_instance = Mock()
    shadowops_bot = Mock(return_value=bot_instance)

    monkeypatch.setattr(bot_module, "get_config", lambda: config)
    monkeypatch.setattr(bot_module, "setup_logger", lambda *args, **kwargs: logger)
    monkeypatch.setattr(bot_module, "ProcessLock", lambda path: lock)
    monkeypatch.setattr(bot_module, "ShadowOpsBot", shadowops_bot)

    assert bot_module.main() == 0
    shadowops_bot.assert_called_once()
    bot_instance.run.assert_called_once_with("test-token", log_handler=None)
    lock.release.assert_called_once()
