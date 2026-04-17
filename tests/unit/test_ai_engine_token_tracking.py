"""Unit-Tests fuer Token-Usage-Parser in der AI-Engine.

Deckt ab:
- _parse_token_usage() auf Claude-JSON-Output (input_tokens + output_tokens)
- _parse_token_usage() auf Codex-Text-Output (tokens used \n 12,345)
- _parse_token_usage() Fallback-Werte (leerer Output, unbekanntes Format)
- AIEngine._last_token_usage wird nach review_pr gesetzt
- ClaudeProvider.query_raw_with_usage() liefert (text, usage) Tupel
"""
import json
import logging
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.integrations import ai_engine as aie


# ─── Pure Parser Tests ───────────────────────────────────────────────

class TestParseTokenUsage:
    """_parse_token_usage() — Pure Funktion, keine Seiteneffekte."""

    def test_claude_json_stdout_usage_block_without_cache(self):
        """Claude --output-format json: usage.input_tokens + usage.output_tokens."""
        stdout = json.dumps({
            "type": "result",
            "result": "OK",
            "usage": {
                "input_tokens": 123,
                "output_tokens": 45,
            },
        })
        usage = aie._parse_token_usage(stdout, "")
        assert usage["input_tokens"] == 123
        assert usage["output_tokens"] == 45
        assert usage["total_tokens"] == 168

    def test_claude_json_includes_cache_in_total_when_present(self):
        """Cache-Tokens werden zum input-Betrag gezaehlt (echte Verrechnung)."""
        stdout = json.dumps({
            "result": "ok",
            "usage": {
                "input_tokens": 10,
                "output_tokens": 5,
                "cache_creation_input_tokens": 500,
                "cache_read_input_tokens": 200,
            },
        })
        usage = aie._parse_token_usage(stdout, "")
        # input_tokens enthaelt Cache-Anteile, damit Kostenkurve stimmt
        assert usage["input_tokens"] == 10 + 500 + 200
        assert usage["output_tokens"] == 5
        assert usage["total_tokens"] == 715

    def test_codex_text_tokens_used_block(self):
        """Codex text mode: 'tokens used\\n28,077' am Ende der Ausgabe."""
        stdout = (
            "codex\n"
            "OK\n"
            "tokens used\n"
            "28,077\n"
        )
        usage = aie._parse_token_usage(stdout, "")
        # Codex gibt nur Gesamt-Tokens — input/output nicht aufgeteilt
        assert usage["total_tokens"] == 28077
        # input/output sind 0, weil Codex sie nicht aufteilt
        assert usage["input_tokens"] == 0
        assert usage["output_tokens"] == 0

    def test_codex_small_number(self):
        stdout = "tokens used\n42"
        usage = aie._parse_token_usage(stdout, "")
        assert usage["total_tokens"] == 42

    def test_empty_output_returns_zeros(self):
        usage = aie._parse_token_usage("", "")
        assert usage == {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    def test_malformed_json_returns_zeros(self):
        usage = aie._parse_token_usage("not json but no tokens used", "")
        assert usage == {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    def test_plain_text_no_usage_returns_zeros(self):
        """Claude --output-format text hat keine Token-Info."""
        usage = aie._parse_token_usage("OK\n", "")
        assert usage == {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    def test_checks_stderr_as_fallback_for_codex(self):
        """Codex schreibt gelegentlich auch nach stderr bei Fehler — Parser checkt beide."""
        usage = aie._parse_token_usage("", "tokens used\n99\n")
        assert usage["total_tokens"] == 99

    def test_claude_json_missing_usage_block(self):
        stdout = json.dumps({"result": "OK"})
        usage = aie._parse_token_usage(stdout, "")
        assert usage == {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


# ─── ClaudeProvider.query_raw_with_usage() ───────────────────────────

class TestClaudeQueryRawWithUsage:
    """query_raw_with_usage: liefert (text, usage) aus JSON-Output."""

    @pytest.mark.asyncio
    async def test_returns_text_and_usage_on_success(self):
        provider = aie.ClaudeProvider({
            "cli_path": "/home/cmdshadow/.local/bin/claude",
            "models": {"standard": "claude-sonnet-4-6", "thinking": "claude-opus-4-6"},
            "timeout": 60,
        })
        fake_json = json.dumps({
            "result": '{"verdict": "approved"}',
            "usage": {"input_tokens": 100, "output_tokens": 50},
        })
        fake_proc = AsyncMock()
        fake_proc.communicate = AsyncMock(return_value=(fake_json.encode(), b""))
        fake_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=fake_proc):
            text, usage = await provider.query_raw_with_usage("hello", model="standard")

        assert text == '{"verdict": "approved"}'
        assert usage["input_tokens"] == 100
        assert usage["output_tokens"] == 50
        assert usage["total_tokens"] == 150

    @pytest.mark.asyncio
    async def test_returns_none_text_on_cli_error(self):
        provider = aie.ClaudeProvider({
            "cli_path": "/home/cmdshadow/.local/bin/claude",
            "models": {"standard": "claude-sonnet-4-6"},
            "timeout": 60,
        })
        fake_proc = AsyncMock()
        fake_proc.communicate = AsyncMock(return_value=(b"", b"error"))
        fake_proc.returncode = 1

        with patch("asyncio.create_subprocess_exec", return_value=fake_proc):
            text, usage = await provider.query_raw_with_usage("hi", model="standard")
        assert text is None
        # Usage immer als Dict zurueck, nie None
        assert usage == {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    @pytest.mark.asyncio
    async def test_returns_text_even_if_usage_missing(self):
        """Kein 'usage'-Block im JSON: text kommt trotzdem zurueck, usage=0."""
        provider = aie.ClaudeProvider({
            "cli_path": "/home/cmdshadow/.local/bin/claude",
            "models": {"standard": "claude-sonnet-4-6"},
            "timeout": 60,
        })
        fake_json = json.dumps({"result": "OK"})
        fake_proc = AsyncMock()
        fake_proc.communicate = AsyncMock(return_value=(fake_json.encode(), b""))
        fake_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=fake_proc):
            text, usage = await provider.query_raw_with_usage("hi", model="standard")
        assert text == "OK"
        assert usage["total_tokens"] == 0


# ─── AIEngine._last_token_usage nach review_pr ───────────────────────

def _valid_review():
    return {
        "verdict": "approved",
        "summary": "LGTM",
        "blockers": [],
        "suggestions": [],
        "nits": [],
        "scope_check": {"in_scope": True, "explanation": "matches"},
    }


def _make_engine_with_claude(raw_return=None, usage_return=None):
    """Baut ein AIEngine-Mock-Objekt mit Claude-Provider."""
    engine = aie.AIEngine.__new__(aie.AIEngine)
    engine.logger = logging.getLogger("test_tokens")
    engine.claude = MagicMock()
    if usage_return is not None:
        engine.claude.query_raw_with_usage = AsyncMock(
            return_value=(raw_return, usage_return)
        )
    else:
        engine.claude.query_raw = AsyncMock(return_value=raw_return)
    engine._last_token_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    return engine


class TestReviewPrCapturesTokens:
    """Nach review_pr() MUSS _last_token_usage den Verbrauch enthalten."""

    @pytest.mark.asyncio
    async def test_review_pr_sets_last_token_usage_from_claude_json(self):
        engine = _make_engine_with_claude(
            raw_return=json.dumps(_valid_review()),
            usage_return={"input_tokens": 200, "output_tokens": 80, "total_tokens": 280},
        )
        result = await engine.review_pr(
            diff="d", finding_context={"severity": "high", "title": "t"},
            project="p", iteration=1, project_knowledge=[], few_shot_examples=[],
        )
        assert result is not None
        assert engine._last_token_usage["total_tokens"] == 280
        assert engine._last_token_usage["input_tokens"] == 200
        assert engine._last_token_usage["output_tokens"] == 80

    @pytest.mark.asyncio
    async def test_review_pr_resets_usage_to_zero_when_both_models_fail(self):
        """Wenn beide Claude-Modelle fehlschlagen, ist usage 0 (keine Tokens verbraucht)."""
        engine = _make_engine_with_claude(
            raw_return=None,
            usage_return={"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        )
        result = await engine.review_pr(
            diff="d", finding_context={}, project="p", iteration=1,
            project_knowledge=[], few_shot_examples=[],
        )
        assert result is None
        assert engine._last_token_usage["total_tokens"] == 0
