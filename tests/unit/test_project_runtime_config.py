"""
Regressionstests fuer projektuebergreifende Runtime-Konfiguration.
"""

from pathlib import Path

import yaml


def _load_config() -> dict:
    return yaml.safe_load(Path("config/config.example.yaml").read_text(encoding="utf-8"))


def test_mayday_sim_monitor_uses_reachable_public_health_url():
    cfg = _load_config()
    monitor = cfg["projects"]["mayday_sim"]["monitor"]

    assert monitor["url"] == "https://maydaysim.de/api/health"
    assert all(port.get("port") != 3200 for port in monitor.get("tcp_ports", []))


def test_ai_agent_framework_has_explicit_github_repo_url():
    cfg = _load_config()
    project = cfg["projects"]["ai-agent-framework"]

    assert project["repo_url"] == "https://github.com/Commandershadow9/ai-agent-framework"


def test_guildscout_has_explicit_github_repo_url():
    cfg = _load_config()
    project = cfg["projects"]["guildscout"]

    assert project["repo_url"] == "https://github.com/Commandershadow9/GuildScout"
