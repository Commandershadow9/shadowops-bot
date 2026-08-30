"""Tests für scripts/zerodox-hauptbaum-sync.sh — der Halter aus ZERODOX#2801.

Das Problem: `/home/cmdshadow/ZERODOX` liefert Cron-Skripte und Claude-Hooks an
den laufenden Betrieb, aber nichts hält ihn aktuell. Am 29.08. lief eine gemergte
Backup-Korrektur deshalb nie; am 30.08. zeichnete der Session-Wachhund nichts auf.

Warum das bisher ungelöst blieb: Ein automatischer `fetch + reset --hard` auf
einem Baum, in dem Sessions arbeiten könnten, ist gefährlicher als das Problem.
Dieses Skript nimmt deshalb den einzigen Weg, der NICHTS zerstören kann:
`merge --ff-only`, und nur bei sauberem Baum auf einem Branch.

Getestet wird gegen ECHTE temporäre Git-Repos, nicht gegen Stubs — die Zusage
lautet „kann keine Arbeit vernichten", und die muss man an echtem git messen.
"""

import subprocess
from pathlib import Path

import pytest

_SKRIPT = Path(__file__).resolve().parents[2] / "scripts" / "zerodox-hauptbaum-sync.sh"


def _git(repo, *args, check=True):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=check,
    )


def _lauf(baum):
    """Ruft das Skript auf und liefert (exit, stdout+stderr)."""
    p = subprocess.run(
        ["bash", str(_SKRIPT)],
        capture_output=True, text=True, check=False,  # Exit-Code IST das Prüfobjekt
        env={"PATH": "/usr/bin:/bin", "HOME": str(baum), "ZERODOX_BAUM": str(baum)},
    )
    return p.returncode, p.stdout + p.stderr


@pytest.fixture
def repos(tmp_path):
    """Ein 'origin' mit zwei Commits und ein Klon, der auf dem ersten steht."""
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "main")
    _git(origin, "config", "user.email", "t@t")
    _git(origin, "config", "user.name", "T")
    (origin / "datei.txt").write_text("eins\n")
    _git(origin, "add", "-A")
    _git(origin, "commit", "-qm", "eins")

    klon = tmp_path / "klon"
    subprocess.run(["git", "clone", "-q", str(origin), str(klon)], check=True)
    _git(klon, "config", "user.email", "t@t")
    _git(klon, "config", "user.name", "T")

    # origin bekommt einen zweiten Commit — der Klon hinkt jetzt hinterher
    (origin / "datei.txt").write_text("zwei\n")
    _git(origin, "commit", "-qam", "zwei")
    return origin, klon


def test_sauberer_baum_wird_nachgezogen(repos):
    _, klon = repos
    code, aus = _lauf(klon)
    assert code == 0, aus
    assert (klon / "datei.txt").read_text() == "zwei\n", "Baum wurde nicht aktualisiert"


def test_aktueller_baum_bleibt_still(repos):
    _, klon = repos
    _lauf(klon)
    code, aus = _lauf(klon)
    assert code == 0
    assert "aktualisiert" not in aus.lower(), f"zweiter Lauf sollte still sein: {aus}"


def test_uncommittete_arbeit_wird_nicht_angefasst(repos):
    """Der wichtigste Fall: Jemand arbeitet gerade im Baum."""
    _, klon = repos
    (klon / "datei.txt").write_text("MEINE UNGESICHERTE ARBEIT\n")
    code, aus = _lauf(klon)
    assert (klon / "datei.txt").read_text() == "MEINE UNGESICHERTE ARBEIT\n", \
        "Arbeit wurde überschrieben — genau das darf NIE passieren"
    assert code != 0
    assert "nicht sauber" in aus.lower() or "uncommitted" in aus.lower()


def test_detached_head_wird_nicht_angefasst(repos):
    _, klon = repos
    sha = _git(klon, "rev-parse", "HEAD").stdout.strip()
    _git(klon, "checkout", "-q", sha)
    code, aus = _lauf(klon)
    assert code != 0
    assert "detached" in aus.lower()


def test_lokale_commits_werden_nicht_verworfen(repos):
    """Divergenz: ff-only muss scheitern, statt den Commit wegzuwerfen."""
    _, klon = repos
    (klon / "eigen.txt").write_text("lokal\n")
    _git(klon, "add", "-A")
    _git(klon, "commit", "-qm", "lokaler commit")
    vorher = _git(klon, "rev-parse", "HEAD").stdout.strip()

    code, aus = _lauf(klon)

    nachher = _git(klon, "rev-parse", "HEAD").stdout.strip()
    assert nachher == vorher, "lokaler Commit wurde verworfen — Datenverlust"
    assert (klon / "eigen.txt").exists()
    assert code != 0
    assert "ff-only" in aus.lower() or "divergiert" in aus.lower()
