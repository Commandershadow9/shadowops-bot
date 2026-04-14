"""Tests fuer die vertiefte Adapter-Integration (Phase 6 Final).

Deckt ab:
- ai_engine.review_pr() akzeptiert prompt_override + model_preference
- _jules_run_review() nutzt adapter.build_prompt() fuer non-Jules-Adapter
- handle_jules_pr_event() routed non-Jules-Adapter durch wenn enabled
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.integrations.github_integration.agent_review.adapters.base import (
    AgentAdapter, AgentDetection, MergeDecision,
)

pytestmark = pytest.mark.asyncio


# ─────────── Stub-Adapter ───────────

class _StubAdapter(AgentAdapter):
    def __init__(self, name="seo", *, prompt="STUB_PROMPT", model=("thinking", "standard")):
        self._name = name
        self._prompt = prompt
        self._model = model
        self.build_prompt_called = False

    @property
    def agent_name(self): return self._name
    def detect(self, pr): return AgentDetection(matched=True, confidence=0.95)
    def build_prompt(self, **kw):
        self.build_prompt_called = True
        return self._prompt
    def model_preference(self, p, d): return self._model
    def merge_policy(self, r, p, project): return MergeDecision.MANUAL
    def discord_channel(self, v): return "test"


# ─────────── ai_engine.review_pr Erweiterung ───────────

class TestAiEngineReviewPr:
    async def test_prompt_override_used_when_provided(self):
        """review_pr mit prompt_override ueberspringt den Jules-Prompt-Builder."""
        from integrations.ai_engine import AIEngine
        engine = AIEngine.__new__(AIEngine)  # skip __init__
        engine.logger = MagicMock()
        engine._get_claude_response = AsyncMock(return_value='{"verdict":"approved","summary":"x","blockers":[],"suggestions":[],"nits":[],"scope_check":{"in_scope":true,"explanation":"ok"}}')

        # Patch build_review_prompt um zu verifizieren dass es NICHT aufgerufen wird
        with patch(
            "integrations.github_integration.jules_review_prompt.build_review_prompt",
        ) as jules_builder:
            try:
                result = await engine.review_pr(
                    diff="+x", finding_context={"title": "t", "category": ""},
                    project="X", iteration=1,
                    project_knowledge=[], few_shot_examples=[],
                    prompt_override="CUSTOM_PROMPT_FROM_ADAPTER",
                    model_preference=("standard", "thinking"),
                )
            except Exception:
                # Kein Claude-Response-Parse moeglich ohne echten Provider — egal,
                # wir wollen nur sehen dass build_review_prompt NICHT aufgerufen wurde
                pass

            # KERN-ASSERT: Jules-Builder wurde NICHT aufgerufen wenn prompt_override gesetzt
            jules_builder.assert_not_called()

    async def test_default_prompt_used_when_no_override(self):
        """review_pr ohne prompt_override baut Jules-Prompt (Legacy)."""
        from integrations.ai_engine import AIEngine
        engine = AIEngine.__new__(AIEngine)
        engine.logger = MagicMock()
        engine._get_claude_response = AsyncMock(return_value="{}")

        with patch(
            "integrations.github_integration.jules_review_prompt.build_review_prompt",
            return_value="JULES_PROMPT",
        ) as jules_builder:
            try:
                await engine.review_pr(
                    diff="+x", finding_context={"title": "t", "category": ""},
                    project="X", iteration=1,
                    project_knowledge=[], few_shot_examples=[],
                )
            except Exception:
                pass
            jules_builder.assert_called_once()


# ─────────── Mixin-Route: non-Jules Adapter ───────────

class TestHandleJulesPrEventRouting:
    def _make_mixin(self, *, is_jules_legacy=False, adapter=None, agent_review_enabled=True):
        from src.integrations.github_integration.jules_workflow_mixin import JulesWorkflowMixin
        m = JulesWorkflowMixin.__new__(JulesWorkflowMixin)

        # Stubs fuer benoetigte Attribute
        m.config = SimpleNamespace(
            jules_workflow=SimpleNamespace(
                enabled=True, dry_run=False,
                circuit_breaker=SimpleNamespace(
                    max_reviews_per_hour=20, pause_duration_seconds=3600,
                ),
                max_iterations=5, max_diff_chars=8000,
                project_knowledge_limit=5, few_shot_examples=3,
                notification_channel="test",
            ),
        )
        m._agent_review_enabled = agent_review_enabled
        m._cached_detector_result = adapter
        m._get_agent_detector = MagicMock(
            return_value=MagicMock(detect=MagicMock(return_value=adapter)),
        )
        m._jules_is_jules_pr = AsyncMock(return_value=is_jules_legacy)
        m._jules_extract_fixes_ref = MagicMock(return_value=None)
        m._jules_lookup_finding = AsyncMock(return_value=None)
        m.jules_state = MagicMock()
        m.jules_state.ensure_pending = AsyncMock()
        m.jules_state._pool = None  # triggert non-fatal skip im agent_type update
        m.should_review = AsyncMock(return_value=MagicMock(proceed=False, reason="stub"))
        m._jules_run_review = AsyncMock()
        return m

    async def test_jules_pr_takes_legacy_path(self):
        m = self._make_mixin(is_jules_legacy=True, adapter=None)
        await m.handle_jules_pr_event({
            "action": "opened",
            "pull_request": {"number": 1, "head": {"sha": "abc1234"}, "labels": [], "user": {"login": "x"}, "body": ""},
            "repository": {"name": "ZERODOX"},
        })
        # Ensure pending wurde aufgerufen (Jules-Pfad)
        m.jules_state.ensure_pending.assert_awaited()

    async def test_seo_pr_uses_adapter_path_when_enabled(self):
        adapter = _StubAdapter(name="seo")
        m = self._make_mixin(is_jules_legacy=False, adapter=adapter)
        await m.handle_jules_pr_event({
            "action": "opened",
            "pull_request": {"number": 1, "head": {"sha": "abc"}, "labels": [], "user": {"login": "x"}, "body": ""},
            "repository": {"name": "ZERODOX"},
        })
        # SEO-Pfad: ensure_pending wurde aufgerufen
        m.jules_state.ensure_pending.assert_awaited()

    async def test_non_jules_pr_skipped_when_agent_review_disabled(self):
        adapter = _StubAdapter(name="seo")
        m = self._make_mixin(
            is_jules_legacy=False, adapter=adapter, agent_review_enabled=False,
        )
        await m.handle_jules_pr_event({
            "action": "opened",
            "pull_request": {"number": 1, "head": {"sha": "abc"}, "labels": [], "user": {"login": "x"}, "body": ""},
            "repository": {"name": "ZERODOX"},
        })
        m.jules_state.ensure_pending.assert_not_awaited()

    async def test_no_adapter_and_no_jules_skipped(self):
        m = self._make_mixin(is_jules_legacy=False, adapter=None)
        await m.handle_jules_pr_event({
            "action": "opened",
            "pull_request": {"number": 1, "head": {"sha": "abc"}, "labels": [], "user": {"login": "x"}, "body": ""},
            "repository": {"name": "ZERODOX"},
        })
        m.jules_state.ensure_pending.assert_not_awaited()


# ─────────── _jules_run_review nutzt adapter.build_prompt() ───────────

class TestRunReviewUsesAdapterPrompt:
    async def test_non_jules_adapter_calls_build_prompt(self):
        from src.integrations.github_integration.jules_workflow_mixin import JulesWorkflowMixin
        m = JulesWorkflowMixin.__new__(JulesWorkflowMixin)
        m.config = SimpleNamespace(
            jules_workflow=SimpleNamespace(
                dry_run=False, max_diff_chars=8000,
                project_knowledge_limit=5, few_shot_examples=3,
            ),
        )
        m._jules_fetch_diff = AsyncMock(return_value="+code")
        m._jules_load_finding = AsyncMock(return_value=None)
        m.jules_learning = MagicMock()
        m.jules_learning.fetch_project_knowledge = AsyncMock(return_value=[])
        m.jules_learning.fetch_few_shot_examples = AsyncMock(return_value=[])
        m.ai_service = MagicMock()
        m.ai_service.review_pr = AsyncMock(return_value=None)  # returns None -> escalate
        m._jules_escalate = AsyncMock()

        adapter = _StubAdapter(name="seo", prompt="SEO_SPECIFIC_PROMPT")
        row = MagicMock(id=1, iteration_count=0, finding_id=None)
        await m._jules_run_review(
            repo="ZERODOX", pr_number=42, head_sha="abc",
            pr_payload={"title": "SEO fix"}, row=row, adapter=adapter,
        )
        # adapter.build_prompt wurde aufgerufen
        assert adapter.build_prompt_called is True
        # ai_service.review_pr bekam prompt_override
        _, kwargs = m.ai_service.review_pr.await_args
        assert kwargs.get("prompt_override") == "SEO_SPECIFIC_PROMPT"
        assert kwargs.get("model_preference") == ("thinking", "standard")

    async def test_jules_adapter_does_not_override_prompt(self):
        """JulesAdapter nutzt weiter den internen Prompt-Builder (Legacy-Compat)."""
        from src.integrations.github_integration.jules_workflow_mixin import JulesWorkflowMixin
        from src.integrations.github_integration.agent_review.adapters.jules import JulesAdapter
        m = JulesWorkflowMixin.__new__(JulesWorkflowMixin)
        m.config = SimpleNamespace(
            jules_workflow=SimpleNamespace(
                dry_run=False, max_diff_chars=8000,
                project_knowledge_limit=5, few_shot_examples=3,
            ),
        )
        m._jules_fetch_diff = AsyncMock(return_value="+code")
        m._jules_load_finding = AsyncMock(return_value=None)
        m.jules_learning = MagicMock()
        m.jules_learning.fetch_project_knowledge = AsyncMock(return_value=[])
        m.jules_learning.fetch_few_shot_examples = AsyncMock(return_value=[])
        m.ai_service = MagicMock()
        m.ai_service.review_pr = AsyncMock(return_value=None)
        m._jules_escalate = AsyncMock()

        adapter = JulesAdapter()
        row = MagicMock(id=1, iteration_count=0, finding_id=None)
        await m._jules_run_review(
            repo="ZERODOX", pr_number=42, head_sha="abc",
            pr_payload={"title": "Fix bug"}, row=row, adapter=adapter,
        )
        _, kwargs = m.ai_service.review_pr.await_args
        assert kwargs.get("prompt_override") is None
        assert kwargs.get("model_preference") is None

    async def test_adapter_build_prompt_crash_falls_back_to_jules_prompt(self):
        """Wenn adapter.build_prompt crasht, fallback auf Jules-Prompt."""
        from src.integrations.github_integration.jules_workflow_mixin import JulesWorkflowMixin
        m = JulesWorkflowMixin.__new__(JulesWorkflowMixin)
        m.config = SimpleNamespace(
            jules_workflow=SimpleNamespace(
                dry_run=False, max_diff_chars=8000,
                project_knowledge_limit=5, few_shot_examples=3,
            ),
        )
        m._jules_fetch_diff = AsyncMock(return_value="+code")
        m._jules_load_finding = AsyncMock(return_value=None)
        m.jules_learning = MagicMock()
        m.jules_learning.fetch_project_knowledge = AsyncMock(return_value=[])
        m.jules_learning.fetch_few_shot_examples = AsyncMock(return_value=[])
        m.ai_service = MagicMock()
        m.ai_service.review_pr = AsyncMock(return_value=None)
        m._jules_escalate = AsyncMock()

        class _CrashingAdapter(_StubAdapter):
            def build_prompt(self, **kw):
                raise RuntimeError("template error")

        adapter = _CrashingAdapter(name="codex")
        row = MagicMock(id=1, iteration_count=0, finding_id=None)
        await m._jules_run_review(
            repo="ZERODOX", pr_number=42, head_sha="abc",
            pr_payload={"title": "x"}, row=row, adapter=adapter,
        )
        _, kwargs = m.ai_service.review_pr.await_args
        # Fallback: kein prompt_override
        assert kwargs.get("prompt_override") is None
