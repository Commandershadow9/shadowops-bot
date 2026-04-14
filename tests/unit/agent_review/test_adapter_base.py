"""Tests fuer AgentAdapter ABC + AgentDetection + MergeDecision."""
import pytest

from src.integrations.github_integration.agent_review.adapters.base import (
    AgentAdapter,
    AgentDetection,
    MergeDecision,
)


class TestMergeDecision:
    def test_enum_values(self):
        assert MergeDecision.AUTO.value == "auto"
        assert MergeDecision.MANUAL.value == "manual"
        assert MergeDecision.BLOCKED.value == "blocked"

    def test_enum_count(self):
        assert len(MergeDecision) == 3


class TestAgentDetection:
    def test_defaults_no_metadata(self):
        d = AgentDetection(matched=False, confidence=0.0)
        assert d.matched is False
        assert d.confidence == 0.0
        assert d.metadata is None

    def test_with_metadata(self):
        d = AgentDetection(matched=True, confidence=0.95, metadata={"src": "label"})
        assert d.matched is True
        assert d.confidence == 0.95
        assert d.metadata == {"src": "label"}

    def test_confidence_can_be_float(self):
        d = AgentDetection(matched=True, confidence=0.85)
        assert isinstance(d.confidence, float)


class TestAgentAdapterABC:
    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            AgentAdapter()  # ABC

    def test_subclass_must_implement_all_abstract_methods(self):
        # Definiere unvollstaendige Subklasse — sollte TypeError werfen
        class IncompleteAdapter(AgentAdapter):
            agent_name = "incomplete"
            # detect, build_prompt, etc. fehlen

        with pytest.raises(TypeError):
            IncompleteAdapter()

    def test_complete_subclass_works(self):
        class MinimalAdapter(AgentAdapter):
            agent_name = "minimal"

            def detect(self, pr_payload):
                return AgentDetection(matched=False, confidence=0.0)

            def build_prompt(self, **kwargs):
                return ""

            def model_preference(self, pr_payload, diff_len):
                return ("standard", "thinking")

            def merge_policy(self, review, pr_payload, project):
                return MergeDecision.MANUAL

            def discord_channel(self, verdict):
                return "test"

        # Sollte instanziierbar sein
        adapter = MinimalAdapter()
        assert adapter.agent_name == "minimal"
        assert adapter.iteration_mention() is None  # default None

    def test_iteration_mention_default_none(self):
        class MinimalAdapter(AgentAdapter):
            agent_name = "x"
            def detect(self, p): return AgentDetection(False, 0.0)
            def build_prompt(self, **k): return ""
            def model_preference(self, p, d): return ("standard", "thinking")
            def merge_policy(self, r, p, pr): return MergeDecision.MANUAL
            def discord_channel(self, v): return "x"

        assert MinimalAdapter().iteration_mention() is None
