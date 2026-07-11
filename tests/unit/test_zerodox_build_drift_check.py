"""Tests fuer scripts/zerodox-build-drift-check.py — buildSha-Drift-Backstop
(ZERODOX#1720, Teil 2).

Der Kernpunkt ist die Docs-only-Ausnahme aus dem Issue-Kommentar 2026-07-08:
ein buildSha-Unterschied ist NUR dann ein Alarm wert, wenn zwischen dem
deployten und dem origin/main-Commit auch deploy-relevante Pfade geaendert
wurden (Allowlist identisch zu ZERODOX/scripts/deploy.sh, Issue #1262).

Geladen wird das Script (Dateiname mit Bindestrich) per importlib — siehe
test_ki_cost_watchdog.py fuer das Vorbild. `main()` ruft die Modul-Funktionen
`fetch_live_build_sha`/`run_git` ueber die Modul-Globals auf, daher reicht
`monkeypatch.setattr(mod, "...", stub)`, ohne echtes Netzwerk/subprocess.
"""

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "zerodox-build-drift-check.py"


def _load():
    spec = importlib.util.spec_from_file_location("zerodox_build_drift_check", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _stub_run_git(fetch_ok=True, rev_ok=True, rev_out="origin1234567", diff_ok=True, diff_out=""):
    """Baut einen run_git-Stub, der je nach aufgerufenem Git-Subkommando
    (args[0]) eine unterschiedliche (ok, output)-Antwort liefert."""

    def _run_git(args, timeout):
        cmd = args[0]
        if cmd == "fetch":
            return fetch_ok, "" if fetch_ok else "fetch fehlgeschlagen"
        if cmd == "rev-parse":
            return rev_ok, rev_out if rev_ok else "unknown revision"
        if cmd == "diff":
            return diff_ok, diff_out if diff_ok else "bad object (force-push?)"
        raise AssertionError(f"unerwartetes Git-Subkommando in Test-Stub: {cmd}")

    return _run_git


# ─── deploy_relevant_paths (reine Logik, kein Monkeypatch noetig) ───────────

def test_deploy_relevant_paths_filters_docs_only():
    mod = _load()
    changed = [
        "docs/PROJECT_TIMELINE.md",
        ".claude/rules/safety.md",
        "CHANGELOG.md",
        "web/src/app/page.tsx",
    ]
    assert mod.deploy_relevant_paths(changed) == ["web/src/app/page.tsx"]


def test_deploy_relevant_paths_web_markdown_is_relevant():
    """Ein *.md UNTER web/ (z.B. ein Blog-Post-Content-File) ist NICHT die
    Top-Level-Doku-Ausnahme -> gilt als deploy-relevant."""
    mod = _load()
    changed = ["web/content/blog/neuer-post.md"]
    assert mod.deploy_relevant_paths(changed) == changed


def test_deploy_relevant_paths_all_docs_only_returns_empty():
    mod = _load()
    changed = ["docs/a.md", "docs/b/c.md", ".claude/rules/x.md", "README.md"]
    assert mod.deploy_relevant_paths(changed) == []


# ─── main() Kontrollfluss (mit gestubbtem fetch/run_git) ────────────────────

def test_main_ok_when_shas_equal(monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod, "fetch_live_build_sha", lambda url, timeout: "abc1234567")
    monkeypatch.setattr(mod, "run_git", _stub_run_git(rev_out="abc1234567"))
    assert mod.main() == 0


def test_main_ok_docs_only_diff(monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod, "fetch_live_build_sha", lambda url, timeout: "abc1234567")
    monkeypatch.setattr(
        mod,
        "run_git",
        _stub_run_git(rev_out="def7654321", diff_out="docs/PROJECT_TIMELINE.md\nREADME.md\n"),
    )
    assert mod.main() == 0


def test_main_fail_web_path_diff(monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod, "fetch_live_build_sha", lambda url, timeout: "abc1234567")
    monkeypatch.setattr(
        mod,
        "run_git",
        _stub_run_git(rev_out="def7654321", diff_out="docs/x.md\nweb/src/app/page.tsx\n"),
    )
    assert mod.main() == 1


def test_main_fail_open_health_unreachable(monkeypatch):
    """Health-Endpoint kaputt/unbekannt -> fail-open (bereits durch
    zerodox-watchdog abgedeckt, kein Doppel-Alarm)."""
    mod = _load()
    monkeypatch.setattr(mod, "fetch_live_build_sha", lambda url, timeout: None)
    assert mod.main() == 0


def test_main_fail_safe_rev_parse_fails(monkeypatch):
    """Lokales Repo nicht auswertbar (origin/main HEAD unbekannt) -> fail-safe,
    NICHT stillschweigend OK."""
    mod = _load()
    monkeypatch.setattr(mod, "fetch_live_build_sha", lambda url, timeout: "abc1234567")
    monkeypatch.setattr(mod, "run_git", _stub_run_git(rev_ok=False))
    assert mod.main() == 1


def test_main_fail_safe_when_diff_fails(monkeypatch):
    """git diff schlaegt fehl (z.B. live_sha nach force-push/rebase nicht mehr
    lokal bekannt) -> fail-safe, nicht als docs-only werten."""
    mod = _load()
    monkeypatch.setattr(mod, "fetch_live_build_sha", lambda url, timeout: "abc1234567")
    monkeypatch.setattr(mod, "run_git", _stub_run_git(rev_out="def7654321", diff_ok=False))
    assert mod.main() == 1


def test_main_tolerates_fetch_failure_if_rev_parse_still_works(monkeypatch):
    """git fetch schlaegt fehl, aber origin/main ist lokal noch von einem
    frueheren Fetch bekannt -> main() darf trotzdem auswerten (Warnung statt
    Abbruch), Fail-Safe greift erst wenn rev-parse selbst scheitert."""
    mod = _load()
    monkeypatch.setattr(mod, "fetch_live_build_sha", lambda url, timeout: "abc1234567")
    monkeypatch.setattr(mod, "run_git", _stub_run_git(fetch_ok=False, rev_out="abc1234567"))
    assert mod.main() == 0
