import pytest
from pathlib import Path
from utils.changelog_parser import get_changelog_parser, ChangelogParser

def test_get_changelog_parser():
    """Test get_changelog_parser returns an instance with the correct path."""
    project_path = Path("/mock/project")
    parser = get_changelog_parser(project_path)

    assert isinstance(parser, ChangelogParser)
    assert parser.changelog_path == project_path / 'CHANGELOG.md'
