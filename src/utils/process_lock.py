"""
Cross-process lock to keep singleton services single-instance.

Layered protection (Issue #259):
1. fcntl.flock (advisory): Kernel-managed, robust against clean crashes.
2. cmdline-match stale-detection: Falls fcntl belegt ist obwohl niemand mehr
   einen Bot-Prozess fährt (recycelte PID, exotische FS-Pfade), erkennen wir
   das Lockfile als stale anhand der gespeicherten PID + /proc/PID/cmdline.
3. atexit + release-truncate: Saubere Shutdowns hinterlassen kein
   irreführendes PID-File.
"""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
from typing import Optional, TextIO


def _read_proc_cmdline(pid: int) -> Optional[str]:
    """Liest /proc/{pid}/cmdline. Returns None wenn der Prozess nicht (mehr) existiert."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            raw = fh.read()
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return None
    except OSError:
        return None
    return raw.replace(b"\x00", b" ").decode("utf-8", errors="ignore").strip()


def _pid_matches_cmdline(pid: int, needle: str) -> bool:
    """True wenn /proc/PID/cmdline den needle-Substring enthält."""
    if not needle:
        return False
    cmdline = _read_proc_cmdline(pid)
    if cmdline is None:
        return False
    return needle in cmdline


class ProcessLock:
    """Advisory file lock shared across processes.

    Args:
        lock_path: Pfad des Lockfiles.
        cmdline_match: Optionaler Substring, der in /proc/PID/cmdline der
            gespeicherten Owner-PID auftauchen muss, damit das Lockfile als
            "nicht stale" gewertet wird. Wenn None: alte fcntl-only-Semantik.
    """

    def __init__(self, lock_path: str | Path, cmdline_match: Optional[str] = None):
        self.lock_path = Path(lock_path)
        self.cmdline_match = cmdline_match
        self._handle: Optional[TextIO] = None

    def acquire(self) -> bool:
        """Try to acquire the lock without blocking.

        Wenn fcntl.flock fehlschlägt und cmdline_match gesetzt ist, prüfen
        wir die gespeicherte Owner-PID. Wenn sie nicht zu einem laufenden
        Bot-Prozess gehört, ist das Lockfile stale → wir überschreiben es
        und versuchen das Lock erneut.
        """
        if self._handle is not None:
            return True

        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.lock_path.open("a+", encoding="utf-8")

        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            # Lock ist belegt. Prüfen ob es ein echter Bot-Prozess hält.
            stored_pid = self._read_pid_from_handle(handle)
            handle.close()

            if self.cmdline_match is None or stored_pid is None:
                return False

            # Wenn die gespeicherte PID kein lebendiger Bot-Prozess ist,
            # ist das Lockfile stale → wir können es übernehmen.
            if _pid_matches_cmdline(stored_pid, self.cmdline_match):
                return False

            # Stale: File neu öffnen + Lock erneut versuchen.
            return self._acquire_after_stale()

        # Lock erfolgreich erhalten → PID schreiben.
        handle.seek(0)
        handle.truncate()
        handle.write(f"{os.getpid()}\n")
        handle.flush()

        self._handle = handle
        return True

    def _acquire_after_stale(self) -> bool:
        """Wird gerufen wenn eine stale Lockfile erkannt wurde. Versucht den
        Lock erneut zu acquiren. Die fcntl-Sperre des stale-Owners ist
        garantiert weg (sonst wäre der Owner-Prozess noch ein Bot), also
        sollte das jetzt klappen."""
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

    @staticmethod
    def _read_pid_from_handle(handle: TextIO) -> Optional[int]:
        try:
            handle.seek(0)
            text = handle.read().strip()
        except (OSError, ValueError):
            return None

        if not text:
            return None
        try:
            return int(text.splitlines()[0].strip())
        except ValueError:
            return None

    def release(self) -> None:
        """Release the lock if we currently hold it.

        Truncated die Lockfile auf 0 Bytes — die nächste Instanz sieht
        sofort dass niemand mehr Owner ist (auch ohne fcntl-Probe), und
        Diagnose-Tools verwirren sich nicht an einer alten PID.
        """
        if self._handle is None:
            return

        try:
            # Inhalt löschen BEVOR wir das fcntl-Lock freigeben, damit
            # andere Prozesse beim Lesen keine stale PID sehen.
            try:
                self._handle.seek(0)
                self._handle.truncate()
                self._handle.flush()
            except OSError:
                pass

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

    def is_owner_alive(self) -> bool:
        """True wenn die im Lockfile gespeicherte PID zu einem
        Prozess gehört, dessen cmdline den configured `cmdline_match`
        enthält. False sonst (auch wenn cmdline_match nicht gesetzt ist —
        dann ist die Frage nicht beantwortbar, also Default False)."""
        if self.cmdline_match is None:
            return False
        pid = self.read_owner_pid()
        if pid is None:
            return False
        return _pid_matches_cmdline(pid, self.cmdline_match)
