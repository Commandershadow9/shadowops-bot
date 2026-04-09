"""
Cross-process lock to keep singleton services single-instance.
"""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
from typing import Optional, TextIO


class ProcessLock:
    """Advisory file lock shared across processes."""

    def __init__(self, lock_path: str | Path):
        self.lock_path = Path(lock_path)
        self._handle: Optional[TextIO] = None

    def acquire(self) -> bool:
        """Try to acquire the lock without blocking."""
        if self._handle is not None:
            return True

        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.lock_path.open("a+", encoding="utf-8")

        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            return False

        handle.seek(0)
        handle.truncate()
        handle.write(f"{os.getpid()}\n")
        handle.flush()

        self._handle = handle
        return True

    def release(self) -> None:
        """Release the lock if we currently hold it."""
        if self._handle is None:
            return

        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None

    def read_owner_pid(self) -> Optional[int]:
        """Best-effort read of the PID written by the lock holder."""
        try:
            text = self.lock_path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return None
        except OSError:
            return None

        if not text:
            return None

        try:
            return int(text.splitlines()[0].strip())
        except ValueError:
            return None
