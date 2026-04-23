import pytest
from pathlib import Path
from unittest.mock import patch
from src.integrations.prompt_ab_testing import get_prompt_ab_testing, PromptABTesting


@patch('src.integrations.prompt_ab_testing.PromptABTesting.__init__')
@patch('src.integrations.prompt_ab_testing.Path.home')
def test_get_prompt_ab_testing_default_dir(mock_home, mock_init):
    mock_init.return_value = None
    mock_home.return_value = Path('/mocked/home')
    ab_testing = get_prompt_ab_testing()
    mock_init.assert_called_once_with(Path('/mocked/home/.shadowops/patch_notes_training'))
    assert isinstance(ab_testing, PromptABTesting)


@patch('src.integrations.prompt_ab_testing.PromptABTesting.__init__')
@patch('src.integrations.prompt_ab_testing.Path.home')
def test_get_prompt_ab_testing_explicit_none(mock_home, mock_init):
    mock_init.return_value = None
    mock_home.return_value = Path('/mocked/home')
    ab_testing = get_prompt_ab_testing(data_dir=None)
    mock_init.assert_called_once_with(Path('/mocked/home/.shadowops/patch_notes_training'))
    assert isinstance(ab_testing, PromptABTesting)


@patch('src.integrations.prompt_ab_testing.PromptABTesting.__init__')
def test_get_prompt_ab_testing_custom_dir(mock_init):
    mock_init.return_value = None
    custom_dir = Path('/custom/dir')
    ab_testing = get_prompt_ab_testing(data_dir=custom_dir)
    mock_init.assert_called_once_with(custom_dir)
    assert isinstance(ab_testing, PromptABTesting)
