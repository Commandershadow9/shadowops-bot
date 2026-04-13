"""Tests für PipelineContext — Dataclass + Serialisierung."""
import json
import pytest
from patch_notes.context import PipelineContext, PipelineState


def test_context_creation_with_defaults():
    ctx = PipelineContext(
        project="mayday_sim",
        project_config={"patch_notes": {"type": "gaming"}},
        raw_commits=[{"message": "feat: test", "sha": "abc123"}],
        trigger="webhook",
    )
    assert ctx.project == "mayday_sim"
    assert ctx.state == PipelineState.PENDING
    assert ctx.version == ""
    assert ctx.groups == []
    assert ctx.error is None


def test_context_serialization_roundtrip():
    ctx = PipelineContext(
        project="guildscout",
        project_config={"patch_notes": {"type": "saas"}},
        raw_commits=[{"message": "fix: bug", "sha": "def456"}],
        trigger="cron",
    )
    ctx.version = "2.5.1"
    ctx.state = PipelineState.CLASSIFYING

    data = ctx.to_dict()
    assert isinstance(data, dict)
    assert data["project"] == "guildscout"
    assert data["version"] == "2.5.1"
    assert data["state"] == PipelineState.CLASSIFYING.value

    restored = PipelineContext.from_dict(data)
    assert restored.project == "guildscout"
    assert restored.version == "2.5.1"
    assert restored.state == PipelineState.CLASSIFYING


def test_context_json_serializable():
    ctx = PipelineContext(
        project="zerodox",
        project_config={},
        raw_commits=[],
        trigger="manual",
    )
    json_str = json.dumps(ctx.to_dict())
    assert isinstance(json_str, str)
    assert "zerodox" in json_str


def test_pipeline_state_ordering():
    assert PipelineState.PENDING.value < PipelineState.COLLECTING.value
    assert PipelineState.COLLECTING.value < PipelineState.GENERATING.value
    assert PipelineState.COMPLETED.value > PipelineState.DISTRIBUTING.value
