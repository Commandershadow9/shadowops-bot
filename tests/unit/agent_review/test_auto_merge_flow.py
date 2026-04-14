"""Tests fuer Auto-Merge-Flow im Mixin.

Testet die neuen Methoden:
- _auto_merge_enabled(project)
- _handle_approval_with_adapter()
- _summarize_rule() Helper
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.integrations.github_integration.jules_workflow_mixin import (
    JulesWorkflowMixin, _summarize_rule,
)
from src.integrations.github_integration.agent_review.adapters.base import (
    AgentAdapter, AgentDetection, MergeDecision,
)

pytestmark = pytest.mark.asyncio


class _StubAdapter(AgentAdapter):
    """Adapter mit deterministischer Merge-Entscheidung fuer Tests."""

    def __init__(self, decision: MergeDecision, name: str = "stub"):
        self._decision = decision
        self._name = name

    @property
    def agent_name(self): return self._name

    def detect(self, pr): return AgentDetection(matched=True, confidence=1.0)
    def build_prompt(self, **kw): return ""
    def model_preference(self, p, d): return ("standard", "thinking")
    def merge_policy(self, r, p, project): return self._decision
    def discord_channel(self, v): return "test"


class _FakeMixin(JulesWorkflowMixin):
    """Test-Subclass mit gestubten Abhaengigkeiten."""

    def __init__(self, *, adapter=None, auto_merge_enabled=True,
                 merge_succeeds=True, project="ZERODOX"):
        self._stub_adapter = adapter
        self._agent_detector = None  # lazy-init-Flag umgehen
        self._cached_detector = _FakeDetector(adapter)
        self.config = SimpleNamespace(
            agent_review=SimpleNamespace(
                auto_merge=SimpleNamespace(
                    enabled=auto_merge_enabled,
                    projects={project: {"allowed": True}},
                ),
            ),
        )
        self.outcome_tracker = AsyncMock()
        self.outcome_tracker.record_auto_merge = AsyncMock(return_value=1)
        self._apply_approval_calls = []
        self._merge_result = merge_succeeds

    def _get_agent_detector(self):
        return self._cached_detector

    async def _jules_apply_approval(self, owner, repo, pr_number, row):
        self._apply_approval_calls.append((owner, repo, pr_number))

    async def _gh_auto_merge_squash(self, owner, repo, pr_number):
        return self._merge_result


class _FakeDetector:
    def __init__(self, adapter):
        self._adapter = adapter

    def detect(self, pr):
        return self._adapter


# ─────────── _summarize_rule ───────────

class TestSummarizeRule:
    def test_format_includes_agent_verdict_blockers(self):
        review = {"verdict": "approved", "blockers": [{"t": "x"}, {"t": "y"}]}
        adapter = _StubAdapter(MergeDecision.AUTO, name="jules")
        out = _summarize_rule(review, adapter)
        assert out == "jules_approved_2b"

    def test_handles_missing_blockers(self):
        review = {"verdict": "approved"}
        out = _summarize_rule(review, _StubAdapter(MergeDecision.MANUAL, name="seo"))
        assert out == "seo_approved_0b"


# ─────────── _auto_merge_enabled ───────────

class TestAutoMergeEnabled:
    def test_enabled_and_project_allowed(self):
        m = _FakeMixin(adapter=_StubAdapter(MergeDecision.AUTO), project="ZERODOX")
        assert m._auto_merge_enabled("ZERODOX") is True

    def test_disabled_globally(self):
        m = _FakeMixin(adapter=_StubAdapter(MergeDecision.AUTO), auto_merge_enabled=False)
        assert m._auto_merge_enabled("ZERODOX") is False

    def test_unknown_project_false(self):
        m = _FakeMixin(adapter=_StubAdapter(MergeDecision.AUTO), project="ZERODOX")
        assert m._auto_merge_enabled("RandomProject") is False

    def test_project_not_allowed(self):
        m = _FakeMixin(adapter=_StubAdapter(MergeDecision.AUTO), project="ZERODOX")
        m.config.agent_review.auto_merge.projects = {"ZERODOX": {"allowed": False}}
        assert m._auto_merge_enabled("ZERODOX") is False

    def test_no_agent_review_config(self):
        m = _FakeMixin(adapter=_StubAdapter(MergeDecision.AUTO))
        m.config.agent_review = None
        assert m._auto_merge_enabled("ZERODOX") is False

    def test_accepts_namespace_projects(self):
        m = _FakeMixin(adapter=_StubAdapter(MergeDecision.AUTO), project="ZERODOX")
        m.config.agent_review.auto_merge.projects = SimpleNamespace(
            ZERODOX=SimpleNamespace(allowed=True),
        )
        assert m._auto_merge_enabled("ZERODOX") is True


# ─────────── _handle_approval_with_adapter ───────────

class TestHandleApproval:
    async def test_auto_decision_triggers_merge(self):
        m = _FakeMixin(adapter=_StubAdapter(MergeDecision.AUTO))
        review = {"verdict": "approved", "blockers": []}
        pr = {"title": "x"}
        row = MagicMock(id=1)
        await m._handle_approval_with_adapter(
            owner="Commandershadow9", repo="ZERODOX", pr_number=42,
            pr_payload=pr, review=review, row=row,
        )
        # Auto-Merge erfolgt, KEIN Label
        assert len(m._apply_approval_calls) == 0
        m.outcome_tracker.record_auto_merge.assert_awaited_once()

    async def test_manual_decision_falls_back_to_label(self):
        m = _FakeMixin(adapter=_StubAdapter(MergeDecision.MANUAL))
        review = {"verdict": "approved", "blockers": []}
        pr = {"title": "x"}
        await m._handle_approval_with_adapter(
            owner="o", repo="ZERODOX", pr_number=42,
            pr_payload=pr, review=review, row=MagicMock(id=1),
        )
        # Label gesetzt, keine Auto-Merge Outcome
        assert len(m._apply_approval_calls) == 1
        m.outcome_tracker.record_auto_merge.assert_not_awaited()

    async def test_no_adapter_falls_back_to_label(self):
        m = _FakeMixin(adapter=None)
        await m._handle_approval_with_adapter(
            owner="o", repo="ZERODOX", pr_number=1,
            pr_payload={}, review={"verdict": "approved"}, row=MagicMock(id=1),
        )
        assert len(m._apply_approval_calls) == 1

    async def test_auto_disabled_project_falls_back_to_label(self):
        m = _FakeMixin(
            adapter=_StubAdapter(MergeDecision.AUTO),
            auto_merge_enabled=False,
        )
        await m._handle_approval_with_adapter(
            owner="o", repo="ZERODOX", pr_number=1,
            pr_payload={}, review={"verdict": "approved"}, row=MagicMock(id=1),
        )
        # Kein Auto-Merge weil globally disabled -> Label
        assert len(m._apply_approval_calls) == 1
        m.outcome_tracker.record_auto_merge.assert_not_awaited()

    async def test_auto_merge_fail_falls_back_to_label(self):
        m = _FakeMixin(
            adapter=_StubAdapter(MergeDecision.AUTO),
            merge_succeeds=False,
        )
        await m._handle_approval_with_adapter(
            owner="o", repo="ZERODOX", pr_number=1,
            pr_payload={}, review={"verdict": "approved"}, row=MagicMock(id=1),
        )
        # Merge gescheitert -> Label-Pfad als Fallback
        assert len(m._apply_approval_calls) == 1
        m.outcome_tracker.record_auto_merge.assert_not_awaited()

    async def test_adapter_crash_falls_back_to_label(self):
        class _CrashingAdapter(_StubAdapter):
            def merge_policy(self, r, p, project):
                raise RuntimeError("oops")

        m = _FakeMixin(adapter=_CrashingAdapter(MergeDecision.AUTO))
        await m._handle_approval_with_adapter(
            owner="o", repo="ZERODOX", pr_number=1,
            pr_payload={}, review={"verdict": "approved"}, row=MagicMock(id=1),
        )
        assert len(m._apply_approval_calls) == 1
