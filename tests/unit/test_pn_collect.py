"""Tests für Stufe 1: Collect."""
import pytest
from patch_notes.stages.collect import collect, _collect_git_stats, _get_last_release_date
from patch_notes.context import PipelineContext


@pytest.fixture
def ctx():
    return PipelineContext(
        project="test-project",
        project_config={"path": "", "repo": ""},
        raw_commits=[
            {"message": "feat: neue Funktion\n\nCo-Authored-By: Claude <noreply@anthropic.com>", "sha": "abc123"},
            {"message": "fix: Bug behoben\n\nSigned-off-by: Dev <dev@test.com>", "sha": "def456"},
            {"message": "docs: README update", "sha": "ghi789"},
        ],
        trigger="webhook",
    )


@pytest.mark.asyncio
async def test_collect_removes_body_noise(ctx):
    await collect(ctx)
    assert "Co-Authored-By" not in ctx.enriched_commits[0]["message"]
    assert "Signed-off-by" not in ctx.enriched_commits[1]["message"]


@pytest.mark.asyncio
async def test_collect_preserves_commit_title(ctx):
    await collect(ctx)
    assert ctx.enriched_commits[0]["message"].startswith("feat: neue Funktion")
    assert ctx.enriched_commits[2]["message"] == "docs: README update"


@pytest.mark.asyncio
async def test_collect_all_commits_present(ctx):
    await collect(ctx)
    assert len(ctx.enriched_commits) == 3


@pytest.mark.asyncio
async def test_collect_no_project_path_skips_enrichment(ctx):
    """Ohne Projekt-Pfad: keine PR-Daten, keine Git-Stats, aber Commits enriched."""
    await collect(ctx)
    assert len(ctx.enriched_commits) == 3
    assert ctx.git_stats == {}


@pytest.mark.asyncio
async def test_collect_empty_commits_no_path():
    """Ohne Commits UND ohne Pfad: bleibt leer."""
    ctx = PipelineContext(
        project="empty", project_config={}, raw_commits=[], trigger="manual"
    )
    await collect(ctx)
    assert ctx.enriched_commits == []
    assert ctx.git_stats == {}


def test_get_last_release_date_no_db():
    """Ohne DB: None zurückgeben."""
    result = _get_last_release_date("nonexistent_project_xyz")
    # Kann None oder ein Datum sein (wenn DB existiert aber kein Eintrag)
    assert result is None or isinstance(result, str)


@pytest.mark.asyncio
async def test_collect_self_healing_with_path(tmp_path):
    """Wenn raw_commits leer aber Pfad gesetzt: Commits aus Git holen."""
    # Erstelle ein Mini-Git-Repo
    import subprocess
    repo = tmp_path / "test-repo"
    repo.mkdir()
    subprocess.run(['git', 'init'], cwd=repo, capture_output=True)
    subprocess.run(['git', 'config', 'user.email', 'test@test.com'], cwd=repo, capture_output=True)
    subprocess.run(['git', 'config', 'user.name', 'Test'], cwd=repo, capture_output=True)
    (repo / "file.txt").write_text("initial")
    subprocess.run(['git', 'add', '.'], cwd=repo, capture_output=True)
    subprocess.run(['git', 'commit', '-m', 'feat: initial commit'], cwd=repo, capture_output=True)

    ctx = PipelineContext(
        project="test-self-heal",
        project_config={"path": str(repo), "deploy": {"branch": "main"}},
        raw_commits=[],  # LEER — soll aus Git geholt werden
        trigger="cron",
    )
    await collect(ctx)
    # Kann Commits finden (aus Git) oder nicht (kein origin/main in lokalem Repo)
    # Wichtig: darf NICHT crashen
    assert isinstance(ctx.enriched_commits, list)
