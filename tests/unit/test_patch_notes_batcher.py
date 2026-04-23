"""
Unit Tests für PatchNotesBatcher — Sammelt Commits und gibt sie kontrolliert frei.
Stellt sicher, dass Batching, Release und die verschiedenen Freigabe-Methoden korrekt funktionieren.
"""

import pytest
from pathlib import Path

from src.integrations.patch_notes_batcher import PatchNotesBatcher, get_patch_notes_batcher


# ============================================================================
# FIXTURES
# ============================================================================

@pytest.fixture
def batcher(tmp_path):
    """Standard-Batcher mit temporärem Verzeichnis."""
    return PatchNotesBatcher(
        data_dir=tmp_path,
        batch_threshold=8,
        emergency_threshold=20,
        cron_day='sunday',
        cron_hour=20,
        cron_min_commits=3,
    )


def _make_commits(count: int, prefix: str = 'abc') -> list:
    """Erzeugt eine Liste von Test-Commits."""
    return [
        {'id': f'{prefix}{i}', 'message': f'feat: feature {i}', 'author': {'name': 'dev'}}
        for i in range(count)
    ]


# ============================================================================
# ADD COMMITS
# ============================================================================

class TestAddCommits:
    def test_adds_commits_to_batch(self, batcher):
        result = batcher.add_commits('test_project', _make_commits(3))
        assert result['batched'] is True
        assert result['total_pending'] == 3

    def test_deduplicates_commits(self, batcher):
        commits = _make_commits(3)
        batcher.add_commits('test_project', commits)
        result = batcher.add_commits('test_project', commits)
        assert result['total_pending'] == 3  # Keine Duplikate

    def test_emergency_threshold(self, batcher):
        result = batcher.add_commits('test_project', _make_commits(20))
        assert result['ready'] is True

    def test_below_emergency_threshold(self, batcher):
        result = batcher.add_commits('test_project', _make_commits(5))
        assert result['ready'] is False


# ============================================================================
# RELEASE BATCH
# ============================================================================

class TestReleaseBatch:
    def test_release_returns_commits(self, batcher):
        batcher.add_commits('test_project', _make_commits(5))
        commits = batcher.release_batch('test_project')
        assert len(commits) == 5

    def test_release_clears_batch(self, batcher):
        batcher.add_commits('test_project', _make_commits(3))
        batcher.release_batch('test_project')
        assert not batcher.has_pending('test_project')

    def test_release_nonexistent_returns_none(self, batcher):
        result = batcher.release_batch('nonexistent')
        assert result is None


# ============================================================================
# PENDING SUMMARY
# ============================================================================

class TestPendingSummary:
    def test_summary_counts(self, batcher):
        batcher.add_commits('project_a', _make_commits(3, 'aaa'))
        batcher.add_commits('project_b', _make_commits(7, 'bbb'))
        summary = batcher.get_pending_summary()
        assert summary['project_a']['count'] == 3
        assert summary['project_b']['count'] == 7


# ============================================================================
# CRON RELEASABLE
# ============================================================================

class TestCronReleasable:
    def test_returns_projects_above_min(self, batcher):
        batcher.add_commits('big_project', _make_commits(5))
        batcher.add_commits('small_project', _make_commits(1))
        result = batcher.get_cron_releasable_projects()
        assert 'big_project' in result
        assert 'small_project' not in result


# ============================================================================
# DAILY RELEASABLE
# ============================================================================

class TestDailyReleasable:
    def test_returns_projects_above_min(self, batcher):
        """Projekte mit >= daily_min_commits werden returned."""
        batcher.add_commits('mayday_sim', _make_commits(5))
        batcher.add_commits('other_project', _make_commits(1))
        result = batcher.get_daily_releasable_projects(daily_min_commits=3)
        assert 'mayday_sim' in result
        assert 'other_project' not in result

    def test_respects_custom_min(self, batcher):
        """Custom daily_min_commits wird beachtet."""
        batcher.add_commits('mayday_sim', _make_commits(2))
        result = batcher.get_daily_releasable_projects(daily_min_commits=2)
        assert 'mayday_sim' in result
        result = batcher.get_daily_releasable_projects(daily_min_commits=3)
        assert 'mayday_sim' not in result

    def test_default_min_is_three(self, batcher):
        """Default daily_min_commits ist 3."""
        batcher.add_commits('project_a', _make_commits(3))
        batcher.add_commits('project_b', _make_commits(2))
        result = batcher.get_daily_releasable_projects()
        assert 'project_a' in result
        assert 'project_b' not in result

    def test_empty_batcher_returns_empty(self, batcher):
        """Leerer Batcher gibt leere Liste zurück."""
        result = batcher.get_daily_releasable_projects()
        assert result == []

    def test_multiple_projects_releasable(self, batcher):
        """Mehrere Projekte können gleichzeitig releasable sein."""
        batcher.add_commits('project_a', _make_commits(5, 'aaa'))
        batcher.add_commits('project_b', _make_commits(4, 'bbb'))
        batcher.add_commits('project_c', _make_commits(1, 'ccc'))
        result = batcher.get_daily_releasable_projects(daily_min_commits=3)
        assert 'project_a' in result
        assert 'project_b' in result
        assert 'project_c' not in result


# ============================================================================
# PERSISTENCE
# ============================================================================

class TestPersistence:
    def test_data_survives_reload(self, tmp_path):
        """Daten überleben einen Batcher-Neustart."""
        batcher1 = PatchNotesBatcher(data_dir=tmp_path)
        batcher1.add_commits('test_project', _make_commits(4))

        batcher2 = PatchNotesBatcher(data_dir=tmp_path)
        assert batcher2.has_pending('test_project')
        summary = batcher2.get_pending_summary()
        assert summary['test_project']['count'] == 4


# ============================================================================
# FACTORY FUNCTION
# ============================================================================

class TestFactoryFunction:
    def test_get_patch_notes_batcher_default(self, monkeypatch, tmp_path):
        """Standard-Verhalten der Factory-Funktion."""
        monkeypatch.setattr(Path, 'home', lambda: tmp_path)

        batcher = get_patch_notes_batcher()
        expected_dir = tmp_path / '.shadowops' / 'patch_notes_training'
        assert batcher.data_dir == expected_dir
        assert batcher.batch_threshold == 8
        assert expected_dir.exists()

    def test_get_patch_notes_batcher_custom(self, tmp_path):
        """Factory-Funktion mit eigenen Parametern."""
        custom_dir = tmp_path / 'custom_dir'
        batcher = get_patch_notes_batcher(data_dir=custom_dir, batch_threshold=15)
        assert batcher.data_dir == custom_dir
        assert batcher.batch_threshold == 15
        assert custom_dir.exists()
