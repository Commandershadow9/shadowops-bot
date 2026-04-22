import pytest
from pathlib import Path
from utils.changelog_parser import get_changelog_parser, ChangelogParser


@pytest.mark.parametrize('project_path', [Path('/mock/project'), Path('relative/project')])
def test_get_changelog_parser(project_path):
    """Test get_changelog_parser returns an instance with the correct path."""
    parser = get_changelog_parser(project_path)

    assert isinstance(parser, ChangelogParser)
    assert parser.changelog_path == project_path / 'CHANGELOG.md'
