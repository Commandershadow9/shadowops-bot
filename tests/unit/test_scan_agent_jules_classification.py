import pytest
from unittest.mock import MagicMock

from src.integrations.security_engine.scan_agent import (
    FIX_MODE_DECISION,
    classify_fix_mode,
    SKIP_ISSUE_PROJECTS,
)


def _finding(category, project="zerodox"):
    f = MagicMock()
    f.category = category
    f.project = project
    return f


def test_npm_audit_routes_to_jules():
    assert classify_fix_mode(_finding("npm_audit")) == "jules"

def test_pip_audit_routes_to_jules():
    assert classify_fix_mode(_finding("pip_audit")) == "jules"

def test_dockerfile_routes_to_jules():
    assert classify_fix_mode(_finding("dockerfile")) == "jules"

def test_code_vulnerability_routes_to_jules():
    assert classify_fix_mode(_finding("code_vulnerability")) == "jules"

def test_ufw_routes_to_self_fix():
    assert classify_fix_mode(_finding("ufw")) == "self_fix"

def test_fail2ban_routes_to_self_fix():
    assert classify_fix_mode(_finding("fail2ban")) == "self_fix"

def test_unknown_category_is_human_only():
    assert classify_fix_mode(_finding("mysterious_thing")) == "human_only"

def test_ssh_config_is_human_only():
    assert classify_fix_mode(_finding("ssh_config")) == "human_only"

def test_skipped_project_falls_back_to_human():
    if "test_skipped" not in SKIP_ISSUE_PROJECTS:
        SKIP_ISSUE_PROJECTS.add("test_skipped")
    assert classify_fix_mode(_finding("npm_audit", project="test_skipped")) == "human_only"
    SKIP_ISSUE_PROJECTS.discard("test_skipped")
