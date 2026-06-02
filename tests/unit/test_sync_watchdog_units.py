"""
Tests für scripts/sync-watchdog-units.sh — IaC-Sync für Watchdog-Units (#294).

Das Skript spiegelt `deploy/*-watchdog.{service,timer}` als Symlinks ins
user-systemd-Verzeichnis. Diese Tests laufen das Skript als subprocess gegen
TEMP-Verzeichnisse (WATCHDOG_UNIT_DIR auf tmp_path) und nutzen ausschließlich
--dry-run für systemd-berührende Pfade, damit NIEMALS echtes systemd oder das
reale ~/.config/systemd/user angefasst wird.

Stil-Vorbild: tests/unit/test_service_watchdog_jq_filter.py (subprocess gegen
Shell-Skript, Assertions auf Exit-Code + stdout).
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "sync-watchdog-units.sh"
DEPLOY_DIR = REPO_ROOT / "deploy"


def _run(unit_dir: Path, *args: str) -> subprocess.CompletedProcess:
    """Run sync-watchdog-units.sh mit isoliertem Ziel-Verzeichnis."""
    env = {**os.environ, "WATCHDOG_UNIT_DIR": str(unit_dir)}
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _real_unit_name() -> str:
    """Liefert den Namen einer echten Watchdog-Timer-Unit aus deploy/."""
    timers = sorted(DEPLOY_DIR.glob("*-watchdog.timer"))
    assert timers, "keine *-watchdog.timer in deploy/ gefunden"
    return timers[0].name


# ---------- Fixtures ----------

@pytest.fixture(scope="module")
def script_exists():
    assert SCRIPT.exists(), f"sync-watchdog-units.sh nicht gefunden: {SCRIPT}"
    assert shutil.which("bash"), "bash nicht installiert"
    # Syntax-Check schützt vor kaputtem Skript in den restlichen Tests.
    proc = subprocess.run(
        ["bash", "-n", str(SCRIPT)], capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stderr


# ---------- Tests ----------

def test_help_exits_zero(script_exists, tmp_path):
    """--help gibt Usage aus und exit 0."""
    result = _run(tmp_path, "--help")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Usage:" in result.stdout


def test_dry_run_plans_symlinks_without_touching_fs(script_exists, tmp_path):
    """Dry-Run gegen leeres Ziel: plant Symlinks, legt aber NICHTS an."""
    result = _run(tmp_path, "--dry-run")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "DRY-RUN" in result.stdout
    assert "Symlink anlegen" in result.stdout
    # systemctl darf nur als geplante (dry-run) Aktion erscheinen, nie real.
    assert "würde ausführen: systemctl --user daemon-reload" in result.stdout
    # FS bleibt leer.
    assert list(tmp_path.iterdir()) == []


def test_dry_run_does_not_emit_real_systemctl_marker(script_exists, tmp_path):
    """Im Dry-Run trägt JEDE Aktion das (dry-run)-Präfix (Gate greift)."""
    result = _run(tmp_path, "--dry-run")
    # Es darf keine systemctl-Zeile OHNE (dry-run)-Marker geben.
    for line in result.stdout.splitlines():
        if "systemctl --user" in line:
            assert "(dry-run)" in line, f"echtes systemctl im Dry-Run: {line}"


def test_real_run_creates_symlinks(script_exists, tmp_path):
    """Echt-Run legt Symlinks an, die auf die deploy/-Dateien zeigen.

    systemctl wird über einen PATH-Shim neutralisiert (exit 0), damit kein
    echtes user-systemd berührt wird — das Skript ruft NUR `systemctl`.
    """
    shim = tmp_path / "shim"
    shim.mkdir()
    (shim / "systemctl").write_text("#!/usr/bin/env bash\nexit 0\n")
    (shim / "systemctl").chmod(0o755)

    unit_dir = tmp_path / "units"
    env = {
        **os.environ,
        "WATCHDOG_UNIT_DIR": str(unit_dir),
        "PATH": f"{shim}:{os.environ['PATH']}",
    }
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    name = _real_unit_name()
    link = unit_dir / name
    assert link.is_symlink(), f"{name} sollte ein Symlink sein"
    assert link.resolve() == (DEPLOY_DIR / name).resolve()
    # Alle deploy-Units gespiegelt.
    deploy_units = {p.name for p in DEPLOY_DIR.glob("*-watchdog.service")}
    deploy_units |= {p.name for p in DEPLOY_DIR.glob("*-watchdog.timer")}
    target_units = {p.name for p in unit_dir.iterdir()}
    assert deploy_units == target_units


def test_idempotent_second_run_skips(script_exists, tmp_path):
    """Zweiter Lauf gegen bereits korrekt verlinktes Ziel → alles skipped."""
    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    # Korrekte Symlinks vorab anlegen (simuliert vorherigen erfolgreichen Sync).
    for src in list(DEPLOY_DIR.glob("*-watchdog.service")) + list(
        DEPLOY_DIR.glob("*-watchdog.timer")
    ):
        (unit_dir / src.name).symlink_to(src)

    result = _run(unit_dir, "--dry-run")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Synced:        0" in result.stdout
    assert "Keine Symlink-Änderungen" in result.stdout
    assert "Symlink anlegen" not in result.stdout


def test_wrong_symlink_gets_corrected(script_exists, tmp_path):
    """Falsch zeigender Symlink → wird als Korrektur geplant."""
    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    name = _real_unit_name()
    (unit_dir / name).symlink_to("/nonexistent/wrong-target")

    result = _run(unit_dir, "--dry-run")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Symlink korrigieren" in result.stdout


def test_orphan_regular_file_detected(script_exists, tmp_path):
    """Reguläre Datei im Ziel ohne deploy-Pendant → als ORPHAN gemeldet."""
    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    (unit_dir / "audit-watchdog.service").write_text("[Unit]\n")
    (unit_dir / "audit-watchdog.timer").write_text("[Timer]\n")

    result = _run(unit_dir, "--dry-run")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ORPHAN: audit-watchdog.service" in result.stdout
    assert "ORPHAN: audit-watchdog.timer" in result.stdout
    assert "REGULÄRE Datei" in result.stdout
    assert "Orphans:       2" in result.stdout


def test_orphan_symlink_detected_with_kind(script_exists, tmp_path):
    """Verwaister Symlink → als ORPHAN mit Symlink-Hinweis gemeldet."""
    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    (unit_dir / "foo-watchdog.timer").symlink_to("/nonexistent/foo-watchdog.timer")

    result = _run(unit_dir, "--dry-run")
    assert "ORPHAN: foo-watchdog.timer" in result.stdout
    assert "Symlink" in result.stdout


def test_strict_exits_nonzero_on_orphan(script_exists, tmp_path):
    """--strict → Exit 1 wenn Orphans gefunden (für CI/Drift-Gate)."""
    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    (unit_dir / "audit-watchdog.timer").write_text("[Timer]\n")

    result = _run(unit_dir, "--dry-run", "--strict")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "STRICT-Modus: Orphans gefunden" in result.stdout


def test_strict_without_orphan_exits_zero(script_exists, tmp_path):
    """--strict ohne Orphans → Exit 0."""
    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    for src in list(DEPLOY_DIR.glob("*-watchdog.service")) + list(
        DEPLOY_DIR.glob("*-watchdog.timer")
    ):
        (unit_dir / src.name).symlink_to(src)

    result = _run(unit_dir, "--dry-run", "--strict")
    assert result.returncode == 0, result.stdout + result.stderr


def test_prune_removes_orphan_symlink_only(script_exists, tmp_path):
    """--prune entfernt verwaisten Symlink, lässt reguläre Datei stehen."""
    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    (unit_dir / "foo-watchdog.timer").symlink_to("/nonexistent/foo-watchdog.timer")
    (unit_dir / "audit-watchdog.timer").write_text("[Timer]\n")

    result = _run(unit_dir, "--dry-run", "--prune")
    assert result.returncode == 0, result.stdout + result.stderr
    # Symlink wird zum Prune geplant …
    assert "prune Symlink:    foo-watchdog.timer" in result.stdout
    # … reguläre Datei NICHT (braucht --force).
    assert "reguläre Datei NICHT entfernt" in result.stdout
    assert "Prune:       aktiv (nur Symlinks)" in result.stdout


def test_prune_real_run_deletes_symlink_keeps_regular(script_exists, tmp_path):
    """Echter --prune-Lauf: Symlink weg, reguläre Datei bleibt.

    systemctl via PATH-Shim neutralisiert.
    """
    shim = tmp_path / "shim"
    shim.mkdir()
    (shim / "systemctl").write_text("#!/usr/bin/env bash\nexit 0\n")
    (shim / "systemctl").chmod(0o755)

    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    orphan_link = unit_dir / "foo-watchdog.timer"
    orphan_link.symlink_to("/nonexistent/foo-watchdog.timer")
    orphan_file = unit_dir / "audit-watchdog.timer"
    orphan_file.write_text("[Timer]\n")

    env = {
        **os.environ,
        "WATCHDOG_UNIT_DIR": str(unit_dir),
        "PATH": f"{shim}:{os.environ['PATH']}",
    }
    result = subprocess.run(
        ["bash", str(SCRIPT), "--prune"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not orphan_link.exists() and not orphan_link.is_symlink(), (
        "verwaister Symlink hätte entfernt werden müssen"
    )
    assert orphan_file.exists(), "reguläre Datei darf ohne --force NICHT gelöscht werden"


def test_prune_force_plans_regular_file_removal(script_exists, tmp_path):
    """--prune --force plant auch Entfernung verwaister regulärer Dateien (laut)."""
    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    (unit_dir / "audit-watchdog.timer").write_text("[Timer]\n")

    result = _run(unit_dir, "--dry-run", "--prune", "--force")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Entferne verwaiste REGULÄRE Datei" in result.stdout
    assert "prune Datei:      audit-watchdog.timer" in result.stdout


def test_regular_file_at_managed_path_not_overwritten(script_exists, tmp_path):
    """Reguläre Datei am Pfad einer GEMANAGTEN Unit → nicht durch Symlink ersetzt."""
    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    name = _real_unit_name()  # Name existiert in deploy/ → ist KEIN Orphan
    (unit_dir / name).write_text("[Timer]\n# handgepflegt\n")

    result = _run(unit_dir, "--dry-run")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "existiert als REGULÄRE Datei im Ziel" in result.stdout
    # Datei bleibt unverändert.
    assert (unit_dir / name).read_text().endswith("# handgepflegt\n")
