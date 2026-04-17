"""Unit-Tests fuer Token-Tracking in Scan-Agent-Sessions.

Verifiziert dass _end_session tokens_used mit echten Zahlen aus
ai_engine._last_token_usage befuellt (nicht mehr nur die Prompt-Laenge-Schaetzung).
"""
from unittest.mock import AsyncMock, MagicMock
import pytest


def _make_agent_with_ai(usage_total):
    """Baut einen Scan-Agent mit minimalem Mock-State."""
    from src.integrations.security_engine import scan_agent as sa
    agent = sa.SecurityScanAgent.__new__(sa.SecurityScanAgent)
    agent.ai_engine = MagicMock()
    agent.ai_engine._daily_tokens_used = 0
    agent.ai_engine._last_token_usage = {
        "input_tokens": 0, "output_tokens": 0, "total_tokens": usage_total,
    }
    agent._session_tokens_start = 0
    agent._session_tokens_accumulated = 0
    return agent


def test_mark_token_start_resets_accumulator():
    """_mark_token_start() setzt accumulator auf 0 zurueck."""
    agent = _make_agent_with_ai(usage_total=0)
    agent._session_tokens_accumulated = 999
    agent._mark_token_start()
    assert agent._session_tokens_accumulated == 0


def test_accumulate_ai_usage_adds_real_tokens():
    """_accumulate_ai_usage() addiert den echten ai-engine Verbrauch."""
    agent = _make_agent_with_ai(usage_total=5000)
    agent._accumulate_ai_usage()
    assert agent._session_tokens_accumulated == 5000
    # Zweiter Aufruf: neuer CLI-Call mit anderem Verbrauch
    agent.ai_engine._last_token_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 3000}
    agent._accumulate_ai_usage()
    assert agent._session_tokens_accumulated == 8000


def test_get_session_tokens_returns_real_accumulated_tokens():
    """_get_session_tokens() liefert den akkumulierten Real-Verbrauch."""
    agent = _make_agent_with_ai(usage_total=0)
    agent._session_tokens_accumulated = 12345
    assert agent._get_session_tokens() == 12345


def test_get_session_tokens_defaults_to_zero_when_no_ai_engine():
    from src.integrations.security_engine import scan_agent as sa
    agent = sa.SecurityScanAgent.__new__(sa.SecurityScanAgent)
    agent.ai_engine = None
    agent._session_tokens_start = 0
    agent._session_tokens_accumulated = 0
    assert agent._get_session_tokens() == 0


def test_accumulate_safe_without_last_token_usage_attr():
    """Falls _last_token_usage nicht existiert (alter AI-Engine-Stand), kein Crash."""
    from src.integrations.security_engine import scan_agent as sa
    agent = sa.SecurityScanAgent.__new__(sa.SecurityScanAgent)
    agent.ai_engine = MagicMock(spec=[])  # keine Attribute
    agent._session_tokens_accumulated = 0
    # Muss nicht crashen
    agent._accumulate_ai_usage()
    assert agent._session_tokens_accumulated == 0
