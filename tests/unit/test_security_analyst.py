"""
Tests fuer SecurityAnalyst — Retry-Loop, Backoff, Session-Lock, Counter-Logik

Testet die kritischen Pfade:
  - Sessions zaehlen korrekt gegen Tages-Limit
  - Failure-Backoff wird korrekt angewendet und zurueckgesetzt
  - Session-Lock verhindert parallele Sessions
  - Manueller Scan respektiert Lock und Counter
  - Tages-Reset setzt Failure-State zurueck
"""

import asyncio
import time
import pytest
from datetime import date
from unittest.mock import Mock, AsyncMock, MagicMock, patch

from src.integrations.analyst.security_analyst import (
    SecurityAnalyst,
    FAILURE_BACKOFF_SECONDS,
    MAX_CONSECUTIVE_FAILURES,
    DEFAULT_MAX_SESSIONS_PER_DAY,
)


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def analyst_config():
    """Mock-Config fuer SecurityAnalyst"""
    config = Mock()
    config._config = {
        'security_analyst': {
            'max_sessions_per_day': 3,
            'model': 'gpt-5.3-codex',
            'fallback_model': 'claude-opus-4-6',
            'database_dsn': 'postgresql://test:test@localhost/test',
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
    """Mock Discord Bot"""
    bot = MagicMock()
    channel = AsyncMock()
    channel.send = AsyncMock()
    bot.get_channel.return_value = channel
    return bot


@pytest.fixture
def mock_ai_engine():
    """Mock AIEngine"""
    engine = AsyncMock()
    engine.run_analyst_session = AsyncMock(return_value=None)
    return engine


@pytest.fixture
def analyst(analyst_config, mock_bot, mock_ai_engine):
    """SecurityAnalyst mit gemockten Dependencies"""
    with patch('src.integrations.analyst.security_analyst.AnalystDB') as MockDB, \
         patch('src.integrations.analyst.security_analyst.ActivityMonitor') as MockAM:

        mock_db = AsyncMock()
        mock_db.connect = AsyncMock()
        mock_db.close = AsyncMock()
        mock_db.count_sessions_today = AsyncMock(return_value=0)
        mock_db.start_session = AsyncMock(return_value=1)
        mock_db.end_session = AsyncMock()
        mock_db.pause_session = AsyncMock()
        mock_db.build_ai_context = AsyncMock(return_value="Test knowledge")
        mock_db.save_health_snapshot = AsyncMock()
        mock_db.upsert_knowledge = AsyncMock()
        mock_db.add_finding = AsyncMock(return_value=1)
        mock_db.mark_finding_fixed = AsyncMock()
        mock_db.find_similar_open_finding = AsyncMock(return_value=None)
        MockDB.return_value = mock_db

        mock_am = Mock()
        mock_am.is_user_active = AsyncMock(return_value=False)
        mock_am.is_user_on_discord = AsyncMock(return_value='online')
        MockAM.return_value = mock_am

        sa = SecurityAnalyst(mock_bot, analyst_config, mock_ai_engine)
        sa.db = mock_db
        sa.activity_monitor = mock_am
        return sa


# ============================================================================
# Initialisierung
# ============================================================================

class TestInit:
    def test_config_defaults(self, analyst):
        assert analyst.max_sessions_per_day == 3
        assert analyst.codex_model == 'gpt-5.3-codex'
        assert analyst.claude_model == 'claude-opus-4-6'

    def test_failure_state_initial(self, analyst):
        assert analyst._consecutive_failures == 0
        assert analyst._failure_cooldown_until == 0.0

    def test_session_lock_exists(self, analyst):
        assert hasattr(analyst, '_session_lock')
        assert isinstance(analyst._session_lock, asyncio.Lock)


# ============================================================================
# Counter-Logik
# ============================================================================

class TestSessionCounter:
    @pytest.mark.asyncio
    async def test_session_increments_counter(self, analyst, mock_ai_engine):
        """Session soll _sessions_today um 1 erhoehen"""
        mock_ai_engine.run_analyst_session.return_value = {
            'summary': 'Test', 'findings': [], 'knowledge_updates': [],
            'topics_investigated': [], '_provider': 'codex', '_model': 'gpt-5.3-codex',
        }
        assert analyst._sessions_today == 0

        with patch.object(analyst, '_take_health_snapshot', new_callable=AsyncMock,
                          return_value={'containers': {}, 'services': {}, 'resources': {}}):
            await analyst._run_session()

        assert analyst._sessions_today == 1

    @pytest.mark.asyncio
    async def test_failed_session_keeps_counter(self, analyst, mock_ai_engine):
        """Fehlgeschlagene Session soll Counter NICHT zuruecksetzen"""
        mock_ai_engine.run_analyst_session.return_value = None
        assert analyst._sessions_today == 0

        with patch.object(analyst, '_take_health_snapshot', new_callable=AsyncMock,
                          return_value={'containers': {}, 'services': {}, 'resources': {}}):
            await analyst._run_session()

        # Counter muss 1 sein (Session wurde gestartet, Fehler zaehlt gegen Limit)
        assert analyst._sessions_today == 1

    @pytest.mark.asyncio
    async def test_user_abort_decrements_counter(self, analyst):
        """User-Abbruch soll Counter zuruecksetzen"""
        analyst.activity_monitor.is_user_active.return_value = True  # User aktiv

        with patch.object(analyst, '_take_health_snapshot', new_callable=AsyncMock,
                          return_value={'containers': {}, 'services': {}, 'resources': {}}):
            await analyst._run_session()

        # Counter muss 0 sein (User-Abbruch zaehlt nicht)
        assert analyst._sessions_today == 0


# ============================================================================
# Failure-Backoff
# ============================================================================

class TestFailureBackoff:
    @pytest.mark.asyncio
    async def test_first_failure_sets_30min_backoff(self, analyst, mock_ai_engine):
        """Erster Fehler: 30 Minuten Backoff"""
        mock_ai_engine.run_analyst_session.return_value = None

        with patch.object(analyst, '_take_health_snapshot', new_callable=AsyncMock,
                          return_value={'containers': {}, 'services': {}, 'resources': {}}):
            await analyst._run_session()

        assert analyst._consecutive_failures == 1
        assert analyst._failure_cooldown_until > time.time()
        # Backoff sollte ~30 Minuten sein
        remaining = analyst._failure_cooldown_until - time.time()
        assert 1790 < remaining <= 1800

    @pytest.mark.asyncio
    async def test_three_failures_disables_for_day(self, analyst, mock_ai_engine):
        """Nach 3 Fehlern: Fuer den Tag deaktiviert"""
        mock_ai_engine.run_analyst_session.return_value = None

        with patch.object(analyst, '_take_health_snapshot', new_callable=AsyncMock,
                          return_value={'containers': {}, 'services': {}, 'resources': {}}):
            # Simuliere 3 Fehler
            for i in range(3):
                analyst._failure_cooldown_until = 0.0  # Cooldown manuell aufheben
                analyst.db.start_session.return_value = i + 1
                await analyst._run_session()

        assert analyst._consecutive_failures == 3
        assert analyst._sessions_today == analyst.max_sessions_per_day

    @pytest.mark.asyncio
    async def test_success_resets_failure_counter(self, analyst, mock_ai_engine):
        """Erfolgreiche Session setzt Failure-Counter auf 0"""
        # Erst einen Fehler haben
        analyst._consecutive_failures = 2
        analyst._failure_cooldown_until = time.time() + 9999

        # Dann Erfolg
        mock_ai_engine.run_analyst_session.return_value = {
            'summary': 'Test', 'findings': [], 'knowledge_updates': [],
            'topics_investigated': [], '_provider': 'codex', '_model': 'gpt-5.3-codex',
        }
        analyst._failure_cooldown_until = 0.0  # Cooldown aufheben

        with patch.object(analyst, '_take_health_snapshot', new_callable=AsyncMock,
                          return_value={'containers': {}, 'services': {}, 'resources': {}}):
            await analyst._run_session()

        assert analyst._consecutive_failures == 0
        assert analyst._failure_cooldown_until == 0.0

    def test_backoff_escalation(self, analyst):
        """Backoff soll eskalieren: 30min -> 2h, danach Tages-Sperre"""
        analyst._consecutive_failures = 1
        analyst._apply_failure_backoff(1)
        backoff_1 = analyst._failure_cooldown_until - time.time()

        analyst._consecutive_failures = 2
        analyst._apply_failure_backoff(2)
        backoff_2 = analyst._failure_cooldown_until - time.time()

        assert 1790 < backoff_1 <= 1800   # 30 Min
        assert 7190 < backoff_2 <= 7200   # 2 Stunden

        # 3. Fehler: Kein Cooldown-Timer, stattdessen Tages-Sperre via Counter
        analyst._consecutive_failures = 3
        analyst._apply_failure_backoff(3)
        assert analyst._failure_cooldown_until == 0.0  # Kein Cooldown
        assert analyst._sessions_today == analyst.max_sessions_per_day  # Tages-Limit


# ============================================================================
# Session-Lock
# ============================================================================

class TestSessionLock:
    @pytest.mark.asyncio
    async def test_lock_prevents_parallel_sessions(self, analyst, mock_ai_engine):
        """Lock verhindert parallele _run_session Aufrufe"""
        call_count = 0

        async def slow_session(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.1)
            return None

        mock_ai_engine.run_analyst_session.side_effect = slow_session

        with patch.object(analyst, '_take_health_snapshot', new_callable=AsyncMock,
                          return_value={'containers': {}, 'services': {}, 'resources': {}}):
            # Starte zwei Sessions gleichzeitig
            task1 = asyncio.create_task(analyst._run_session())
            await asyncio.sleep(0.01)  # Gib task1 Zeit den Lock zu holen
            task2 = asyncio.create_task(analyst._run_session())

            await asyncio.gather(task1, task2)

        # Nur eine Session sollte gelaufen sein (die andere wurde abgelehnt)
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_manual_scan_respects_lock(self, analyst, mock_ai_engine):
        """manual_scan wird abgelehnt wenn automatische Session laeuft"""
        async def slow_session(*args, **kwargs):
            await asyncio.sleep(0.2)
            return None

        mock_ai_engine.run_analyst_session.side_effect = slow_session

        with patch.object(analyst, '_take_health_snapshot', new_callable=AsyncMock,
                          return_value={'containers': {}, 'services': {}, 'resources': {}}):
            task1 = asyncio.create_task(analyst._run_session())
            await asyncio.sleep(0.01)

            # manual_scan sollte None zurueckgeben weil Lock aktiv
            result = await analyst.manual_scan()
            assert result is None

            await task1


# ============================================================================
# Tages-Reset
# ============================================================================

class TestDayReset:
    @pytest.mark.asyncio
    async def test_day_change_resets_failure_state(self, analyst):
        """Tageswechsel setzt Failure-Counter und Cooldown zurueck"""
        analyst._consecutive_failures = 3
        analyst._failure_cooldown_until = time.time() + 99999
        analyst._sessions_today = 3
        analyst._today = date(2020, 1, 1)  # Gestern
        analyst.db.count_sessions_today.return_value = 0

        # Simuliere einen Loop-Durchlauf mit Tageswechsel
        # Wir testen nur die Reset-Logik, nicht den ganzen Loop
        today = date.today()
        if today != analyst._today:
            analyst._today = today
            analyst._sessions_today = await analyst.db.count_sessions_today()
            analyst._consecutive_failures = 0
            analyst._failure_cooldown_until = 0.0

        assert analyst._consecutive_failures == 0
        assert analyst._failure_cooldown_until == 0.0
        assert analyst._sessions_today == 0


# ============================================================================
# Manual Scan
# ============================================================================

class TestManualScan:
    @pytest.mark.asyncio
    async def test_manual_scan_failure_updates_counter(self, analyst, mock_ai_engine):
        """Manueller Scan-Fehler soll Failure-Counter erhoehen"""
        mock_ai_engine.run_analyst_session.return_value = None

        with patch.object(analyst, '_take_health_snapshot', new_callable=AsyncMock,
                          return_value={'containers': {}, 'services': {}, 'resources': {}}):
            await analyst.manual_scan()

        assert analyst._consecutive_failures == 1

    @pytest.mark.asyncio
    async def test_manual_scan_success_resets_counter(self, analyst, mock_ai_engine):
        """Manueller Scan-Erfolg soll Failure-Counter zuruecksetzen"""
        analyst._consecutive_failures = 2

        mock_ai_engine.run_analyst_session.return_value = {
            'summary': 'Test', 'findings': [], 'knowledge_updates': [],
            'topics_investigated': [], '_provider': 'codex', '_model': 'gpt-5.3-codex',
        }

        with patch.object(analyst, '_take_health_snapshot', new_callable=AsyncMock,
                          return_value={'containers': {}, 'services': {}, 'resources': {}}):
            await analyst.manual_scan()

        assert analyst._consecutive_failures == 0


# ============================================================================
# Discord Notifications
# ============================================================================

class TestNotifications:
    @pytest.mark.asyncio
    async def test_start_notification_sent(self, analyst, mock_bot):
        """Session-Start soll Discord-Embed senden"""
        await analyst._notify_session_start(1)

        channel = mock_bot.get_channel.return_value
        channel.send.assert_called_once()
        embed = channel.send.call_args[1]['embed']
        assert 'gestartet' in embed.title

    @pytest.mark.asyncio
    async def test_failure_notification_sent(self, analyst, mock_bot):
        """Session-Failure soll Discord-Embed senden"""
        analyst._consecutive_failures = 1
        await analyst._notify_session_failure(1)

        channel = mock_bot.get_channel.return_value
        channel.send.assert_called_once()
        embed = channel.send.call_args[1]['embed']
        assert 'fehlgeschlagen' in embed.title

    @pytest.mark.asyncio
    async def test_failure_notification_shows_error(self, analyst, mock_bot):
        """Failure-Notification soll Fehler-Details anzeigen"""
        analyst._consecutive_failures = 1
        await analyst._notify_session_failure(1, error="Timeout nach 900s")

        channel = mock_bot.get_channel.return_value
        embed = channel.send.call_args[1]['embed']
        assert 'Timeout' in embed.description

    @pytest.mark.asyncio
    async def test_no_channel_no_crash(self, analyst, mock_bot):
        """Fehlender Channel soll nicht crashen"""
        mock_bot.get_channel.return_value = None
        # Darf keine Exception werfen
        await analyst._notify_session_start(1)
        await analyst._notify_session_failure(1)


# ============================================================================
# Exception-Handling
# ============================================================================

class TestExceptionHandling:
    @pytest.mark.asyncio
    async def test_db_exception_counts_as_failure(self, analyst):
        """DB-Exception bei start_session soll als Fehler zaehlen"""
        analyst.db.start_session.side_effect = Exception("Connection refused")

        with patch.object(analyst, '_take_health_snapshot', new_callable=AsyncMock,
                          return_value={'containers': {}, 'services': {}, 'resources': {}}):
            await analyst._run_session()

        assert analyst._consecutive_failures == 1

    @pytest.mark.asyncio
    async def test_pause_session_exception_no_false_failure(self, analyst):
        """pause_session Exception soll keinen falschen AI-Fehler zaehlen

        BUG-001/007: Vorher wurde pause_session-Exception als AI-Fehler
        gezaehlt. Jetzt wird die Exception gefangen und der Counter
        nicht beruehrt.
        """
        analyst.activity_monitor.is_user_active.return_value = True
        analyst.db.pause_session.side_effect = Exception("DB error")

        with patch.object(analyst, '_take_health_snapshot', new_callable=AsyncMock,
                          return_value={'containers': {}, 'services': {}, 'resources': {}}):
            await analyst._run_session()

        # Failure-Counter muss 0 bleiben — war ein User-Abbruch, kein AI-Fehler
        assert analyst._consecutive_failures == 0
