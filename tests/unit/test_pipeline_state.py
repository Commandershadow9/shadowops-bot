"""Tests für Pipeline State-Persistenz und Orchestrator."""
import pytest
from pathlib import Path
from patch_notes.state import PipelineStateStore
from patch_notes.context import PipelineContext, PipelineState


@pytest.fixture
def store(tmp_path):
    return PipelineStateStore(data_dir=tmp_path)


@pytest.fixture
def sample_ctx():
    return PipelineContext(
        project="shadowops-bot",
        project_config={"patch_notes": {"type": "devops"}},
        raw_commits=[{"message": "fix: test", "sha": "aaa111"}],
        trigger="manual",
    )


def test_persist_and_load(store, sample_ctx):
    sample_ctx.state = PipelineState.CLASSIFYING
    sample_ctx.version = "5.1.0"
    store.persist(sample_ctx)
    loaded = store.load("shadowops-bot")
    assert loaded is not None
    assert loaded.state == PipelineState.CLASSIFYING
    assert loaded.version == "5.1.0"


def test_load_nonexistent_returns_none(store):
    assert store.load("nonexistent") is None


def test_cleanup_completed(store, sample_ctx):
    sample_ctx.state = PipelineState.COMPLETED
    store.persist(sample_ctx)
    store.cleanup_completed()
    assert store.load("shadowops-bot") is None


def test_incomplete_runs(store, sample_ctx):
    sample_ctx.state = PipelineState.GENERATING
    store.persist(sample_ctx)
    runs = store.get_incomplete_runs()
    assert len(runs) == 1
    assert runs[0].project == "shadowops-bot"
