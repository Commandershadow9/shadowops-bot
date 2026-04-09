"""
Tests for the cross-process singleton lock.
"""

from __future__ import annotations

import multiprocessing as mp
import os

from src.utils.process_lock import ProcessLock


def _try_acquire_lock(lock_path: str, queue) -> None:
    lock = ProcessLock(lock_path)
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
