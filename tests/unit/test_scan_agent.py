import time
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from src.integrations.security_engine.scan_agent import (
    MAX_CONSECUTIVE_FAILURES,
    SecurityScanAgent,
)


@pytest.fixture
def scan_config():
    config = Mock()
    config._config = {
        'security_analyst': {
            'max_sessions_per_day': 3,
            'model': 'gpt-5.3-codex',
            'fallback_model': 'claude-opus-4-6',
        },
    }
    config.channels = {
        'security_briefing': 12345,
        'ai_learning': 67890,
    }
    config.critical_channel = 99999
    return config


@pytest.fixture
def mock_bot():
    bot = MagicMock()
    channel = AsyncMock()
    channel.send = AsyncMock()
    bot.get_channel.return_value = channel
    bot.learning_notifier = None
    return bot


@pytest.fixture
def mock_ai_engine():
    engine = AsyncMock()
    engine.run_analyst_session = AsyncMock(return_value=None)
    engine.is_claude_quota_exhausted = Mock(return_value=False)
    engine._daily_tokens_used = 0
    return engine


@pytest.fixture
def mock_db():
    db = Mock()
    db.pool = AsyncMock()
    db.pool.fetchval = AsyncMock(return_value=0)
    db.pool.fetchrow = AsyncMock(return_value={'id': 1})
    db.pool.fetch = AsyncMock(return_value=[])
    db.pool.execute = AsyncMock()
    return db


@pytest.fixture
def agent(scan_config, mock_bot, mock_ai_engine, mock_db):
    scan_agent = SecurityScanAgent(mock_bot, scan_config, mock_ai_engine, mock_db)
    scan_agent.activity_monitor = Mock()
    scan_agent.activity_monitor.is_user_active = AsyncMock(return_value=False)
    scan_agent.activity_monitor.is_user_on_discord = AsyncMock(return_value='online')
    return scan_agent


@pytest.mark.asyncio
async def test_disabled_failure_notification_sent_once_per_day(agent, mock_bot):
    agent._consecutive_failures = MAX_CONSECUTIVE_FAILURES

    await agent._notify_session_failure(1)
    await agent._notify_session_failure(2)

    channel = mock_bot.get_channel.return_value
    assert channel.send.await_count == 1
    embed = channel.send.await_args.kwargs['embed']
    assert "3/3" in embed.description


@pytest.mark.asyncio
async def test_weekly_deep_skips_when_disabled(agent):
    agent._consecutive_failures = MAX_CONSECUTIVE_FAILURES
    agent._sessions_today = agent.max_sessions_per_day
    agent._start_session = AsyncMock(return_value=42)

    await agent._run_weekly_deep(trigger='weekly_auto')

    agent._start_session.assert_not_awaited()
    assert agent._weekly_deep_done_this_week is True


@pytest.mark.asyncio
async def test_weekly_deep_failure_marks_week_done_and_failed_status(agent):
    agent._start_session = AsyncMock(return_value=42)
    agent._pre_session_maintenance = AsyncMock()
    agent._knowledge_maintenance = AsyncMock()
    agent._notify_session_start = AsyncMock()
    agent._take_health_snapshot = AsyncMock(
        return_value={'containers': {}, 'services': {}, 'resources': {}, 'port_bindings': {}}
    )
    agent._build_ai_context = AsyncMock(return_value="context")
    agent._get_open_findings_summary = AsyncMock(return_value=[])
    agent._build_scan_plan = AsyncMock(return_value="plan")
    agent._run_pre_checks = AsyncMock(return_value="")
    agent._notify_session_failure = AsyncMock()
    agent._end_session = AsyncMock()

    await agent._run_weekly_deep(trigger='weekly_auto')

    agent._end_session.assert_awaited_once()
    assert agent._end_session.await_args.kwargs['status'] == 'failed'
    assert agent._weekly_deep_done_this_week is True


@pytest.mark.asyncio
async def test_weekly_deep_retries_with_codex_when_claude_quota_is_exhausted(agent, mock_ai_engine):
    agent._start_session = AsyncMock(return_value=42)
    agent._pre_session_maintenance = AsyncMock()
    agent._knowledge_maintenance = AsyncMock()
    agent._notify_session_start = AsyncMock()
    agent._take_health_snapshot = AsyncMock(
        return_value={'containers': {}, 'services': {}, 'resources': {}, 'port_bindings': {}}
    )
    agent._build_ai_context = AsyncMock(return_value="context")
    agent._get_open_findings_summary = AsyncMock(return_value=[])
    agent._build_scan_plan = AsyncMock(return_value="plan")
    agent._run_pre_checks = AsyncMock(return_value="")
    agent._process_results = AsyncMock()
    agent._run_fix_phase = AsyncMock()
    agent._post_scan_reflection = AsyncMock()
    mock_ai_engine.is_claude_quota_exhausted = Mock(return_value=True)
    mock_ai_engine.run_analyst_session = AsyncMock(side_effect=[
        None,
        {
            '_provider': 'codex',
            '_model': 'gpt-5.3-codex',
            'summary': 'ok',
            'topics_investigated': [],
            'findings': [],
            'knowledge_updates': [],
            'health_check_passed': True,
            'next_priority': 'none',
            'areas_checked': [],
            'areas_deferred': [],
            'finding_assessments': [],
        },
    ])

    await agent._run_weekly_deep(trigger='weekly_auto')

    assert mock_ai_engine.run_analyst_session.await_count == 2
    first_call = mock_ai_engine.run_analyst_session.await_args_list[0].kwargs
    second_call = mock_ai_engine.run_analyst_session.await_args_list[1].kwargs
    assert first_call['codex_model'] is None
    assert second_call['codex_model'] == agent.codex_model
    agent._process_results.assert_awaited_once()
    assert agent._weekly_deep_done_this_week is True
    assert agent._consecutive_failures == 0


def test_apply_failure_backoff_sets_cooldown_until_next_day(agent):
    agent._consecutive_failures = MAX_CONSECUTIVE_FAILURES

    agent._apply_failure_backoff(99)

    assert agent._sessions_today == agent.max_sessions_per_day
    assert agent._failure_cooldown_until > time.time()


@pytest.mark.asyncio
async def test_can_start_session_returns_false_when_disabled(agent):
    agent._running = True
    agent._consecutive_failures = MAX_CONSECUTIVE_FAILURES

    assert await agent.can_start_session() is False
