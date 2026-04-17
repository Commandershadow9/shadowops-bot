"""Tests für Stufe 2: Classify."""
import pytest
from patch_notes.stages.classify import classify, _extract_credits, TEAM_MAPPING
from patch_notes.context import PipelineContext


def _make_commits(n: int, prefix: str = "feat", scope: str = "") -> list[dict]:
    scope_str = f"({scope})" if scope else ""
    return [
        {"message": f"{prefix}{scope_str}: change {i}", "sha": f"sha{i}",
         "author": {"name": "cmdshadow"}}
        for i in range(n)
    ]


@pytest.fixture
def ctx_small():
    return PipelineContext(
        project="test-project",
        project_config={"patch_notes": {"type": "devops"}},
        raw_commits=_make_commits(3, "fix"),
        trigger="manual",
    )


@pytest.fixture
def ctx_major():
    commits = (
        _make_commits(40, "feat", "events") +
        _make_commits(10, "feat", "auth") +
        _make_commits(5, "fix") +
        _make_commits(10, "docs")
    )
    return PipelineContext(
        project="mayday_sim",
        project_config={"patch_notes": {"type": "gaming"}},
        raw_commits=commits,
        trigger="cron",
    )


@pytest.mark.asyncio
async def test_classify_groups_all_commits(ctx_major):
    await classify(ctx_major)
    total = sum(len(g["commits"]) for g in ctx_major.groups)
    assert total == 65


@pytest.mark.asyncio
async def test_classify_sets_version(ctx_small):
    await classify(ctx_small)
    assert ctx_small.version != ""
    assert ctx_small.version_source in ("semver", "fallback")


@pytest.mark.asyncio
async def test_classify_update_size_major(ctx_major):
    await classify(ctx_major)
    assert ctx_major.update_size == "major"


@pytest.mark.asyncio
async def test_classify_update_size_small(ctx_small):
    await classify(ctx_small)
    assert ctx_small.update_size == "small"


@pytest.mark.asyncio
async def test_classify_update_size_normal():
    ctx = PipelineContext(
        project="test", project_config={},
        raw_commits=_make_commits(10, "feat"),
        trigger="manual",
    )
    await classify(ctx)
    assert ctx.update_size == "normal"


@pytest.mark.asyncio
async def test_classify_update_size_big():
    """15-39 Commits → big."""
    ctx = PipelineContext(
        project="test", project_config={},
        raw_commits=_make_commits(20, "fix"),
        trigger="cron",
    )
    await classify(ctx)
    assert ctx.update_size == "big"


@pytest.mark.asyncio
async def test_classify_update_size_mega_by_volume():
    """≥80 Commits → mega."""
    ctx = PipelineContext(
        project="test", project_config={},
        raw_commits=_make_commits(85, "fix"),
        trigger="cron",
    )
    await classify(ctx)
    assert ctx.update_size == "mega"


@pytest.mark.asyncio
async def test_classify_update_size_mega_by_feature_groups():
    """≥5 FEATURE-Gruppen → mega auch bei wenig Commits."""
    commits = (
        _make_commits(3, "feat", "lagebild") +
        _make_commits(3, "feat", "einsatz") +
        _make_commits(3, "feat", "fahrzeug") +
        _make_commits(3, "feat", "krankenhaus") +
        _make_commits(3, "feat", "verkettung") +
        _make_commits(2, "fix")
    )
    ctx = PipelineContext(
        project="mayday_sim",
        project_config={"patch_notes": {"type": "gaming"}},
        raw_commits=commits,
        trigger="cron",
    )
    await classify(ctx)
    feature_groups = [g for g in ctx.groups if g.get('tag') == 'FEATURE']
    assert len(feature_groups) >= 5
    assert ctx.update_size == "mega"


def test_extract_credits_known_author():
    commits = [
        {"author": {"name": "cmdshadow"}},
        {"author": {"name": "cmdshadow"}},
        {"author": {"name": "renjihoshida"}},
    ]
    credits = _extract_credits(commits)
    assert len(credits) == 2
    shadow = next(c for c in credits if c["name"] == "Shadow")
    assert shadow["commits"] == 2
    assert shadow["role"] == "Founder & Lead Dev"


def test_extract_credits_filters_ai():
    commits = [
        {"author": {"name": "Claude"}},
        {"author": {"name": "github-actions[bot]"}},
        {"author": {"name": "cmdshadow"}},
    ]
    credits = _extract_credits(commits)
    assert len(credits) == 1
    assert credits[0]["name"] == "Shadow"


def test_extract_credits_unknown_author():
    commits = [{"author": {"name": "neuer-dev"}}]
    credits = _extract_credits(commits)
    assert credits[0]["name"] == "neuer-dev"
    assert credits[0]["role"] == "Contributor"


@pytest.mark.asyncio
async def test_classify_player_facing_detected(ctx_major):
    await classify(ctx_major)
    pf = [g for g in ctx_major.groups if g.get("is_player_facing")]
    assert len(pf) > 0
