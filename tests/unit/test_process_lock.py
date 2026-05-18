"""
Tests for the cross-process singleton lock.
"""

from __future__ import annotations

import multiprocessing as mp
import os

from src.utils.process_lock import (
    ProcessLock,
    _pid_matches_cmdline,
    _read_proc_cmdline,
)


def _try_acquire_lock(lock_path: str, queue) -> None:
    lock = ProcessLock(lock_path)
    acquired = lock.acquire()
    queue.put(acquired)
    if acquired:
        lock.release()


def _try_acquire_lock_with_cmdline(
    lock_path: str, cmdline_match: str, queue
) -> None:
    lock = ProcessLock(lock_path, cmdline_match=cmdline_match)
    acquired = lock.acquire()
    queue.put(acquired)
    if acquired:
        lock.release()


def test_process_lock_records_owner_pid(tmp_path):
    lock = ProcessLock(tmp_path / "shadowops.lock")

    assert lock.acquire() is True
    assert lock.read_owner_pid() == os.getpid()

    lock.release()


def test_process_lock_blocks_second_process(tmp_path):
    lock_path = tmp_path / "shadowops.lock"
    lock = ProcessLock(lock_path)
    assert lock.acquire() is True

    ctx = mp.get_context("spawn")
    queue = ctx.Queue()
    process = ctx.Process(target=_try_acquire_lock, args=(str(lock_path), queue))
    process.start()
    process.join(timeout=10)

    assert process.exitcode == 0
    assert queue.get(timeout=1) is False

    lock.release()


def test_process_lock_releases_for_next_process(tmp_path):
    lock_path = tmp_path / "shadowops.lock"
    lock = ProcessLock(lock_path)
    assert lock.acquire() is True
    lock.release()

    ctx = mp.get_context("spawn")
    queue = ctx.Queue()
    process = ctx.Process(target=_try_acquire_lock, args=(str(lock_path), queue))
    process.start()
    process.join(timeout=10)

    assert process.exitcode == 0
    assert queue.get(timeout=1) is True


# -----------------------------------------------------------------------------
# Issue #259: cmdline-based stale-detection
# -----------------------------------------------------------------------------


def test_release_truncates_lockfile(tmp_path):
    """Nach release() darf das Lockfile keine PID mehr enthalten —
    sonst zeigen Diagnose-Tools (`/cogs/admin`, `diagnose-bot.sh`) eine
    falsche Owner-PID auf eine längst gestoppte Instanz."""
    lock_path = tmp_path / "shadowops.lock"
    lock = ProcessLock(lock_path)

    assert lock.acquire() is True
    assert lock.read_owner_pid() == os.getpid()
    lock.release()

    assert lock.read_owner_pid() is None


def test_acquire_takes_over_stale_file_when_cmdline_does_not_match(tmp_path):
    """Wenn das Lockfile eine PID enthält, die NICHT zu unserem Bot-cmdline
    gehört (z.B. recycelt nach einem Crash), soll acquire() die Lockfile
    als stale erkennen und übernehmen.

    Wir simulieren das, indem wir eine PID einschreiben, die garantiert
    nicht zu einem `src/bot.py`-Prozess gehört (PID 1 = systemd, bzw.
    der Test-Runner-Prozess hat keinen `src/bot.py` im cmdline).
    """
    lock_path = tmp_path / "shadowops.lock"

    # Stale PID hineinschreiben — aber NICHT fcntl-locken (simuliert
    # crashed previous instance, deren FD bereits zu ist).
    lock_path.write_text("1\n", encoding="utf-8")

    lock = ProcessLock(lock_path, cmdline_match="src/bot.py")
    assert lock.acquire() is True
    assert lock.read_owner_pid() == os.getpid()

    lock.release()


def test_acquire_respects_live_owner_when_cmdline_matches(tmp_path):
    """Wenn ein anderer Prozess das Lock noch hält UND seine cmdline
    matched, soll acquire() korrekt blockieren (kein false-positive stale)."""
    lock_path = tmp_path / "shadowops.lock"

    # Erster ProcessLock erhält das Lock — unser eigener Test-Prozess.
    # Mit cmdline_match=str(__file__) matcht der Test selbst (pytest läuft
    # mit der Test-Datei im cmdline).
    own_cmdline = _read_proc_cmdline(os.getpid()) or ""
    needle = "pytest" if "pytest" in own_cmdline else "python"

    lock = ProcessLock(lock_path, cmdline_match=needle)
    assert lock.acquire() is True

    # Zweiter Prozess versucht zu acquiren — soll fehlschlagen weil
    # der Owner ein lebendiger pytest/python-Prozess ist.
    ctx = mp.get_context("spawn")
    queue = ctx.Queue()
    process = ctx.Process(
        target=_try_acquire_lock_with_cmdline,
        args=(str(lock_path), needle, queue),
    )
    process.start()
    process.join(timeout=10)

    assert process.exitcode == 0
    assert queue.get(timeout=1) is False

    lock.release()


def test_is_owner_alive_returns_false_for_stale_pid(tmp_path):
    """is_owner_alive() ist False für stale Lockfiles."""
    lock_path = tmp_path / "shadowops.lock"
    # PID 1 (systemd/init) existiert immer, aber sein cmdline enthält
    # niemals 'src/bot.py'.
    lock_path.write_text("1\n", encoding="utf-8")

    lock = ProcessLock(lock_path, cmdline_match="src/bot.py")
    assert lock.is_owner_alive() is False


def test_is_owner_alive_returns_false_when_cmdline_match_disabled(tmp_path):
    """Ohne cmdline_match ist is_owner_alive() konservativ False —
    Aufrufer hat keine zuverlässige Antwort."""
    lock_path = tmp_path / "shadowops.lock"
    lock_path.write_text(f"{os.getpid()}\n", encoding="utf-8")

    lock = ProcessLock(lock_path)
    assert lock.is_owner_alive() is False


def test_pid_matches_cmdline_handles_dead_pid():
    """_pid_matches_cmdline auf einer garantiert toten PID returnt False
    statt zu crashen."""
    # PID 999999 ist auf einem normalen System frei (max default 32k).
    assert _pid_matches_cmdline(999_999, "anything") is False


def test_pid_matches_cmdline_handles_empty_needle():
    """Leerer Match-String wird als ungültig gewertet."""
    assert _pid_matches_cmdline(os.getpid(), "") is False
