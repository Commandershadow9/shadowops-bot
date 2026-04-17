"""Tests fuer AgentDetector."""
import pytest

from src.integrations.github_integration.agent_review.detector import AgentDetector
from src.integrations.github_integration.agent_review.adapters.base import (
    AgentAdapter,
    AgentDetection,
    MergeDecision,
)
from src.integrations.github_integration.agent_review.adapters.jules import JulesAdapter


# ──────── Mock-Adapter fuer kontrollierte Tests ────────

class _FixedAdapter(AgentAdapter):
    """Adapter mit fixer Confidence — fuer Tests."""

    def __init__(self, name: str, confidence: float, matched: bool = True):
        self._name = name
        self._confidence = confidence
        self._matched = matched

    @property
    def agent_name(self):
        return self._name

    def detect(self, pr):
        return AgentDetection(matched=self._matched, confidence=self._confidence)

    def build_prompt(self, **kwargs): return ""
    def model_preference(self, p, d): return ("standard", "thinking")
    def merge_policy(self, r, p, pr): return MergeDecision.MANUAL
    def discord_channel(self, v): return "test"


# ──────── Tests ────────

class TestDetect:
    def test_returns_jules_for_jules_pr(self):
        d = AgentDetector([JulesAdapter()])
        pr = {"labels": [{"name": "jules"}], "user": {"login": "x"}, "body": ""}
        adapter = d.detect(pr)
        assert adapter is not None
        assert adapter.agent_name == "jules"

    def test_returns_none_when_no_match(self):
        d = AgentDetector([JulesAdapter()])
        pr = {
            "labels": [],
            "user": {"login": "dependabot[bot]"},
            "body": "Bumps deps",
        }
        assert d.detect(pr) is None

    def test_returns_none_when_below_threshold(self):
        d = AgentDetector([_FixedAdapter("low", 0.5)])
        assert d.detect({}) is None  # 0.5 < 0.8

    def test_returns_match_at_exact_threshold(self):
        d = AgentDetector([_FixedAdapter("borderline", 0.8)])
        adapter = d.detect({})
        assert adapter is not None
        assert adapter.agent_name == "borderline"

    def test_highest_confidence_wins(self):
        d = AgentDetector([
            _FixedAdapter("medium", 0.85),
            _FixedAdapter("high", 0.95),
            _FixedAdapter("borderline", 0.80),
        ])
        adapter = d.detect({})
        assert adapter is not None
        assert adapter.agent_name == "high"

    def test_unmatched_ignored_even_with_high_confidence(self):
        d = AgentDetector([
            _FixedAdapter("matched_low", 0.85, matched=True),
            _FixedAdapter("unmatched_high", 0.99, matched=False),
        ])
        adapter = d.detect({})
        assert adapter.agent_name == "matched_low"

    def test_empty_adapter_list(self):
        d = AgentDetector([])
        assert d.detect({}) is None


class TestDetectAll:
    def test_returns_all_adapters_with_confidence(self):
        d = AgentDetector([
            _FixedAdapter("a", 0.9, True),
            _FixedAdapter("b", 0.5, True),
            _FixedAdapter("c", 0.0, False),
        ])
        results = d.detect_all({})
        assert len(results) == 3
        names = [r[0] for r in results]
        assert "a" in names and "b" in names and "c" in names


class TestThresholdConstant:
    def test_threshold_is_0_8(self):
        # Sicherstellen dass die Konstante nicht versehentlich verschoben wird
        assert AgentDetector.CONFIDENCE_THRESHOLD == 0.8
