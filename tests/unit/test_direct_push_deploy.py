"""Tests fuer den Direct-Push-Auto-Deploy (Akquise-Feinschliff-Folge).

Zwei Bausteine:
  - _reserve_deploy (StateMixin): per-SHA-Dedup, damit ein PR-Merge (push +
    pull_request, beide gleicher Merge-SHA) nicht doppelt deployt.
  - _project_allows_direct_push (CIMixin): per-Projekt opt-in fuer Auto-Deploy
    bei direktem Push (default aus -> nur PR-Merge deployt).
"""
import time as _time

from src.integrations.github_integration.state_mixin import StateMixin
from src.integrations.github_integration.ci_mixin import CIMixin


class _ReserveHarness(StateMixin):
    """Minimaler Harness — _reserve_deploy braucht nur _normalize_repo_name."""

    def _normalize_repo_name(self, name):
        return name.lower().replace("-", "_")


class _ConfigStub:
    def __init__(self, projects):
        self.projects = projects


class _CIHarness(CIMixin):
    def __init__(self, projects):
        self.config = _ConfigStub(projects)


def test_reserve_deploy_first_wins_then_dedup():
    h = _ReserveHarness()
    assert h._reserve_deploy("ZERODOX", "abc1234") is True   # erster gewinnt
    assert h._reserve_deploy("ZERODOX", "abc1234") is False  # Duplikat
    # andere Schreibweise desselben Repos -> normalisiert gleich -> Duplikat
    assert h._reserve_deploy("zerodox", "abc1234") is False


def test_reserve_deploy_distinct_sha_and_repo():
    h = _ReserveHarness()
    assert h._reserve_deploy("ZERODOX", "aaa") is True
    assert h._reserve_deploy("ZERODOX", "bbb") is True       # anderer SHA
    assert h._reserve_deploy("GuildScout", "aaa") is True    # anderes Repo


def test_reserve_deploy_without_sha_passes_through():
    h = _ReserveHarness()
    assert h._reserve_deploy("ZERODOX", "") is True
    assert h._reserve_deploy("ZERODOX", "") is True          # ohne SHA kein Dedup


def test_reserve_deploy_ttl_expiry(monkeypatch):
    h = _ReserveHarness()
    clock = {"t": 1000.0}
    monkeypatch.setattr(_time, "monotonic", lambda: clock["t"])
    assert h._reserve_deploy("ZERODOX", "sha", ttl_sec=10) is True
    clock["t"] = 1005.0
    assert h._reserve_deploy("ZERODOX", "sha", ttl_sec=10) is False  # innerhalb TTL
    clock["t"] = 1020.0
    assert h._reserve_deploy("ZERODOX", "sha", ttl_sec=10) is True   # TTL abgelaufen


def test_project_allows_direct_push_opt_in():
    ci = _CIHarness({
        "zerodox": {"deploy": {"allow_direct_push": True}},
        "guildscout": {"deploy": {"enabled": True}},
        "mayday-sim": {"deploy": {}},
    })
    assert ci._project_allows_direct_push("ZERODOX") is True
    assert ci._project_allows_direct_push("zerodox") is True
    assert ci._project_allows_direct_push("GuildScout") is False   # kein Flag -> aus
    assert ci._project_allows_direct_push("mayday-sim") is False
    assert ci._project_allows_direct_push("mayday_sim") is False   # dash/underscore
    assert ci._project_allows_direct_push("unknown") is False      # nicht in Config
