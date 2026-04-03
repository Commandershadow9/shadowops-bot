"""
Unit Tests für Team-Credits — Mapping von Git-Autoren zu Team-Mitgliedern,
Credits-Aufbereitung und Prompt-Integration.
"""

import pytest

from src.integrations.github_integration.ai_patch_notes_mixin import AIPatchNotesMixin


# ============================================================================
# FIXTURES
# ============================================================================

@pytest.fixture
def mixin():
    """AIPatchNotesMixin-Instanz für Tests."""
    return AIPatchNotesMixin()


def _make_commit(author_name: str, message: str = "feat: test") -> dict:
    """Erzeugt einen Test-Commit."""
    return {
        'author': {'name': author_name},
        'message': message,
        'id': 'abc123',
    }


# ============================================================================
# TEAM-MAPPING
# ============================================================================

class TestResolveTeamMember:
    """Tests für _resolve_team_member — Git-Autor → Display-Name."""

    def test_commandershadow9_wird_zu_shadow(self, mixin):
        result = mixin._resolve_team_member('Commandershadow9')
        assert result == ('Shadow', 'Backend & Infrastruktur')

    def test_cmdshadow_wird_zu_shadow(self, mixin):
        result = mixin._resolve_team_member('cmdshadow')
        assert result == ('Shadow', 'Backend & Infrastruktur')

    def test_renjihoshida_wird_zu_mapu(self, mixin):
        result = mixin._resolve_team_member('RenjiHoshida')
        assert result == ('Mapu', 'Frontend & Design')

    def test_mapu_wird_zu_mapu(self, mixin):
        result = mixin._resolve_team_member('mapu')
        assert result == ('Mapu', 'Frontend & Design')

    def test_claude_wird_gefiltert(self, mixin):
        result = mixin._resolve_team_member('Claude')
        assert result is None

    def test_claude_opus_wird_gefiltert(self, mixin):
        result = mixin._resolve_team_member('Claude Opus')
        assert result is None

    def test_dependabot_wird_gefiltert(self, mixin):
        result = mixin._resolve_team_member('dependabot[bot]')
        assert result is None

    def test_github_actions_wird_gefiltert(self, mixin):
        result = mixin._resolve_team_member('github-actions[bot]')
        assert result is None

    def test_unbekannter_autor_wird_contributor(self, mixin):
        result = mixin._resolve_team_member('NeuerGameDesigner')
        assert result == ('NeuerGameDesigner', 'Contributor')

    def test_case_insensitive(self, mixin):
        result = mixin._resolve_team_member('COMMANDERSHADOW9')
        assert result == ('Shadow', 'Backend & Infrastruktur')


# ============================================================================
# CREDITS-AUFBEREITUNG
# ============================================================================

class TestBuildTeamCredits:
    """Tests für _build_team_credits — Commits nach Team-Member gruppieren."""

    def test_single_author(self, mixin):
        commits = [
            _make_commit('cmdshadow', 'feat: neues Feature'),
            _make_commit('cmdshadow', 'fix: Bugfix'),
        ]
        credits = mixin._build_team_credits(commits)
        assert 'Shadow' in credits
        assert credits['Shadow']['commits'] == 2
        assert credits['Shadow']['rolle'] == 'Backend & Infrastruktur'

    def test_multiple_authors(self, mixin):
        commits = [
            _make_commit('cmdshadow', 'feat: Backend'),
            _make_commit('RenjiHoshida', 'feat: Frontend'),
            _make_commit('cmdshadow', 'fix: Bugfix'),
        ]
        credits = mixin._build_team_credits(commits)
        assert credits['Shadow']['commits'] == 2
        assert credits['Mapu']['commits'] == 1

    def test_claude_co_author_nicht_in_credits(self, mixin):
        """Claude als Co-Author soll NICHT als eigener Credit erscheinen."""
        commits = [
            _make_commit('cmdshadow', 'feat: mit AI\n\nCo-Authored-By: Claude'),
            _make_commit('Claude', 'chore: auto-generated'),
        ]
        credits = mixin._build_team_credits(commits)
        assert 'Claude' not in credits
        assert 'Shadow' in credits

    def test_auto_commits_separat(self, mixin):
        """SEO-AUTO und DEPS-AUTO Commits werden als __autonomous__ gruppiert."""
        commits = [
            _make_commit('cmdshadow', 'feat: normales Feature'),
            _make_commit('github-actions', 'SEO: Automatische Optimierungen für GuildScout'),
            _make_commit('dependabot', 'chore(deps): bump express from 4.18.2 to 4.19.0'),
        ]
        credits = mixin._build_team_credits(commits)
        assert '__autonomous__' in credits
        assert credits['__autonomous__']['commits'] == 2

    def test_merge_commits_werden_uebersprungen(self, mixin):
        commits = [
            _make_commit('cmdshadow', 'Merge branch feat/test'),
            _make_commit('cmdshadow', 'feat: echtes Feature'),
        ]
        credits = mixin._build_team_credits(commits)
        assert credits['Shadow']['commits'] == 1

    def test_features_werden_gesammelt(self, mixin):
        commits = [
            _make_commit('cmdshadow', 'feat: Verschleiß-System'),
            _make_commit('cmdshadow', 'fix: Timer-Bug behoben'),
            _make_commit('cmdshadow', 'chore: Dependencies aktualisiert'),
        ]
        credits = mixin._build_team_credits(commits)
        assert len(credits['Shadow']['features']) == 2  # feat + fix, nicht chore

    def test_max_5_features(self, mixin):
        commits = [_make_commit('cmdshadow', f'feat: Feature {i}') for i in range(10)]
        credits = mixin._build_team_credits(commits)
        assert len(credits['Shadow']['features']) <= 5


# ============================================================================
# CREDITS-FORMATIERUNG
# ============================================================================

class TestFormatCreditsSection:
    """Tests für _format_credits_section — Credits als Prompt-Kontext."""

    def test_leere_credits(self, mixin):
        result = mixin._format_credits_section({})
        assert result == ""

    def test_single_member(self, mixin):
        credits = {
            'Shadow': {'rolle': 'Backend', 'commits': 5, 'features': []},
        }
        result = mixin._format_credits_section(credits, 'de')
        assert 'Shadow (Backend)' in result
        assert 'Dieses Update' in result

    def test_mit_autonomous(self, mixin):
        credits = {
            'Shadow': {'rolle': 'Backend', 'commits': 3, 'features': []},
            '__autonomous__': {'commits': 2, 'types': ['SEO-AUTO']},
        }
        result = mixin._format_credits_section(credits, 'de')
        assert 'Automatisiert' in result
        assert 'SEO-Optimierungen' in result
