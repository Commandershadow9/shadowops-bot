"""Tests für Security-Agent-Team Config-Properties (P1, #290)."""
from src.utils.config import Config


def _cfg(d):
    c = Config.__new__(Config)
    c._config = d
    return c


def test_security_team_disabled_by_default():
    assert _cfg({})._security_team_enabled_value() is False


def test_security_team_enabled_via_config():
    c = _cfg({"security_team": {"enabled": True}})
    assert c._security_team_enabled_value() is True


def test_env_override_wins(monkeypatch):
    monkeypatch.setenv("SECURITY_TEAM_ENABLED", "true")
    c = _cfg({"security_team": {"enabled": False}})
    assert c._security_team_enabled_value() is True


def test_projects_and_workers():
    c = _cfg({"security_team": {
        "projects": {"guildscout": {"npm_audit_path": "/g/web"}},
        "active_workers": ["npm_audit"],
    }})
    assert c.security_team_projects == {"guildscout": {"npm_audit_path": "/g/web"}}
    assert c.security_team_active_workers == ["npm_audit"]
