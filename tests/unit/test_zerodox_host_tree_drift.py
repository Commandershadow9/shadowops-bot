"""Tests für scripts/zerodox-host-tree-drift-check.py — Host-Baum-Drift (ZERODOX#2801).

Der Zwilling `zerodox-build-drift-check.py` misst, ob die laufende **Produktion**
von `origin/main` abweicht. Dieses Skript misst die andere Hälfte: ob die
**Dateien auf der Platte** abweichen, die vom Host aus ausgeführt werden.

Der Unterschied ist der ganze Punkt. Am 29.08.2026 war `scripts/backup-files.sh`
gemergt, grün und deployt — und der stündliche Cron las trotzdem die alte
Fassung, weil er sie aus `/home/cmdshadow/ZERODOX` liest und diesen Baum nichts
aktualisiert. Kein Container-Check kann das sehen.

Geladen wird das Skript (Dateiname mit Bindestrich) per importlib — Vorbild
test_zerodox_build_drift_check.py.
"""

import importlib.util
from pathlib import Path

_SCRIPT = (
    Path(__file__).resolve().parents[2] / "scripts" / "zerodox-host-tree-drift-check.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("zerodox_host_tree_drift_check", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ─── scharfe_pfade (reine Logik, kein Monkeypatch nötig) ────────────────────

def test_scharfe_pfade_behaelt_cron_skript_und_hook():
    """Die zwei Klassen, die der Host tatsächlich ausführt."""
    mod = _load()
    geaendert = [
        "scripts/backup-files.sh",
        ".claude/hooks/wachhund.sh",
    ]
    assert mod.scharfe_pfade(geaendert) == [
        "scripts/backup-files.sh",
        ".claude/hooks/wachhund.sh",
    ]


def test_scharfe_pfade_verwirft_doku_und_container_code():
    """Ein veralteter Blogeintrag oder eine veraltete Regel-Datei richtet auf
    dem Host nichts an; `web/src/**` läuft im Container, nicht aus diesem Baum."""
    mod = _load()
    geaendert = [
        "scripts/README.md",
        ".claude/rules/safety.md",
        "web/src/app/page.tsx",
        "docs/PROJECT_TIMELINE.md",
        "CLAUDE.md",
    ]
    assert mod.scharfe_pfade(geaendert) == []


def test_scharfe_pfade_zaehlt_settings_json_als_scharf():
    """`.claude/settings.json` registriert die Hooks. Weicht sie ab, laufen sie
    nicht — genau der Wachhund-Befund vom 30.08.2026: Der Stop-Hook war auf
    origin/main registriert, im Arbeitsbaum fehlte die Zeile, und aufgezeichnet
    wurde nichts."""
    mod = _load()
    assert mod.scharfe_pfade([".claude/settings.json"]) == [".claude/settings.json"]


# ─── pruefe() — Git-Schicht über run_git-Stub ───────────────────────────────

def _stub_run_git(fetch_ok=True, diff_out="", diff_ok=True, branch="main", branch_ok=True):
    """Baut einen run_git-Stub, der je nach Subkommando (args[0]) antwortet."""

    def _run_git(args, timeout):
        cmd = args[0]
        if cmd == "fetch":
            return fetch_ok, "" if fetch_ok else "could not read from remote"
        if cmd == "diff":
            return diff_ok, diff_out if diff_ok else "bad object (force-push?)"
        if cmd == "rev-parse":
            return branch_ok, branch if branch_ok else "not a git repository"
        raise AssertionError(f"unerwartetes Git-Subkommando im Stub: {cmd}")

    return _run_git


def test_pruefe_schweigt_wenn_nichts_abweicht(monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod, "run_git", _stub_run_git(diff_out=""))
    ok, meldung = mod.pruefe()
    assert ok is True
    assert meldung == ""


def test_pruefe_schweigt_wenn_nur_doku_abweicht(monkeypatch):
    """Der Normalfall eines Arbeitsbaums: Er liegt zurück, aber an Stellen, die
    der Host nie ausführt. Eine Prüfung, die hier meldet, wird Tapete."""
    mod = _load()
    diff = "docs/PROJECT_TIMELINE.md\n.claude/rules/safety.md\nweb/src/app/page.tsx\n"
    monkeypatch.setattr(mod, "run_git", _stub_run_git(diff_out=diff))
    ok, meldung = mod.pruefe()
    assert ok is True
    assert meldung == ""


def test_pruefe_meldet_abweichendes_cron_skript_und_nennt_es(monkeypatch):
    """Der Vorfall vom 29.08.2026 — die Meldung muss die Datei benennen,
    sonst weiss niemand, welcher Cron mit welcher Fassung lief."""
    mod = _load()
    diff = "docs/PROJECT_TIMELINE.md\nscripts/backup-files.sh\n"
    monkeypatch.setattr(mod, "run_git", _stub_run_git(diff_out=diff))
    ok, meldung = mod.pruefe()
    assert ok is False
    assert "scripts/backup-files.sh" in meldung
    assert "docs/PROJECT_TIMELINE.md" not in meldung


def test_pruefe_ist_fail_safe_wenn_fetch_scheitert(monkeypatch):
    """Fail-SAFE wie beim Zwilling: Ein Git-Fehler ist nicht anderweitig
    abgedeckt. Im Zweifel melden statt stillschweigend OK sagen."""
    mod = _load()
    monkeypatch.setattr(mod, "run_git", _stub_run_git(fetch_ok=False))
    ok, meldung = mod.pruefe()
    assert ok is False
    assert "fetch" in meldung.lower()


def test_pruefe_meldet_detached_head_nicht_fuer_sich_allein(monkeypatch):
    """Detached HEAD ist ein Risiko, kein Schaden: Solange keine scharfe Datei
    abweicht, ist nichts kaputt. Wer das allein meldet, meldet dauerhaft."""
    mod = _load()
    monkeypatch.setattr(mod, "run_git", _stub_run_git(diff_out="", branch="HEAD"))
    ok, meldung = mod.pruefe()
    assert ok is True
    assert meldung == ""


def test_pruefe_erwaehnt_detached_head_wenn_ohnehin_ein_befund_vorliegt(monkeypatch):
    """Dann erklärt es nämlich, warum der Rückstand von selbst weiterwächst:
    In einem detached HEAD ist `git pull` gar nicht möglich."""
    mod = _load()
    monkeypatch.setattr(
        mod, "run_git", _stub_run_git(diff_out="scripts/backup-files.sh\n", branch="HEAD")
    )
    ok, meldung = mod.pruefe()
    assert ok is False
    assert "detached" in meldung.lower()


def test_meldung_bleibt_unter_dem_200_zeichen_limit_der_engine(monkeypatch):
    """Die Check-Engine kappt stderr auf 200 Zeichen. Der Echtlauf am 30.08.2026
    lieferte 278 — der Diagnose-Hinweis am Ende wäre also genau dann verloren
    gegangen, wenn es viele Treffer gibt, also im schlimmsten Fall.

    Deshalb steht die Diagnose VOR der Dateiliste: Gekappt werden sollen
    Beispiele, nicht der Befund.
    """
    mod = _load()
    viele = "\n".join(
        f".claude/hooks/bereichspruefer/modul_mit_langem_namen_{i}.py" for i in range(18)
    )
    monkeypatch.setattr(mod, "run_git", _stub_run_git(diff_out=viele, branch="HEAD"))
    ok, meldung = mod.pruefe()
    assert ok is False
    assert len(meldung) <= 200, f"Meldung ist {len(meldung)} Zeichen lang"
    assert "18" in meldung
    assert "detached" in meldung.lower()
