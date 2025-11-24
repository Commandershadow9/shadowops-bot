"""
AIDE (Advanced Intrusion Detection Environment) Integration
Monitort AIDE File Integrity Checks
"""

import subprocess
import re
from typing import Dict, Optional
from datetime import datetime
from pathlib import Path


class AIDEMonitor:
    """Monitort AIDE File Integrity Checks"""

    def __init__(self, log_dir: str = "/var/log/aide"):
        self.log_dir = Path(log_dir)
        self.check_log = self.log_dir / "aide_check.log"

    def get_last_check_results(self) -> Optional[Dict[str, any]]:
        """
        Liest Ergebnisse des letzten AIDE Checks

        Returns:
            Dict mit Check-Ergebnissen oder None
        """
        try:
            if not self.check_log.exists():
                return None

            with open(self.check_log, 'r') as f:
                content = f.read()

            results = {
                "timestamp": None,
                "files_changed": 0,
                "files_added": 0,
                "files_removed": 0,
                "errors": [],
            }

            # Extrahiere Timestamp
            timestamp_match = re.search(r'Start timestamp: (.+)', content)
            if timestamp_match:
                results["timestamp"] = timestamp_match.group(1).strip()

            # Suche nach Changed/Added/Removed Files
            # AIDE Output-Format kann variieren, hier generische Patterns

            # Changed files
            changed_match = re.search(r'Changed entries: (\d+)', content)
            if changed_match:
                results["files_changed"] = int(changed_match.group(1))
            else:
                # Alternative: Zähle Zeilen mit "changed"
                results["files_changed"] = len(re.findall(r'\bchanged\b', content, re.IGNORECASE))

            # Added files
            added_match = re.search(r'Added entries: (\d+)', content)
            if added_match:
                results["files_added"] = int(added_match.group(1))
            else:
                results["files_added"] = len(re.findall(r'\badded\b', content, re.IGNORECASE))

            # Removed files
            removed_match = re.search(r'Removed entries: (\d+)', content)
            if removed_match:
                results["files_removed"] = int(removed_match.group(1))
            else:
                results["files_removed"] = len(re.findall(r'\bremoved\b', content, re.IGNORECASE))

            # Errors
            error_lines = re.findall(r'ERROR:.*', content)
            results["errors"] = error_lines[:10]  # Maximal 10 Errors

            return results

        except (FileNotFoundError, PermissionError, IOError):
            return None

    def get_last_check_date(self) -> Optional[str]:
        """
        Gibt Datum des letzten Checks zurück

        Returns:
            Datum-String, "Pending first run" oder None
        """
        try:
            result = subprocess.run(
                ['sudo', 'systemctl', 'show', 'dailyaidecheck.timer', '-p', 'LastTriggerUSecRealtime', '--value'],
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode == 0 and result.stdout.strip():
                timestamp_str = result.stdout.strip()
                if timestamp_str == 'n/a' or int(timestamp_str) == 0:
                    if self.is_timer_active():
                        return "Pending first run"
                    else:
                        return None

                timestamp = int(timestamp_str)
                if timestamp > 0:
                    dt = datetime.fromtimestamp(timestamp / 1000000)  # Mikrosekunden → Sekunden
                    return dt.strftime('%Y-%m-%d %H:%M:%S')

        except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
            pass

        return None

    def is_timer_active(self) -> bool:
        """
        Prüft ob AIDE Timer aktiv ist

        Returns:
            True wenn Timer läuft
        """
        try:
            result = subprocess.run(
                ['sudo', 'systemctl', 'is-active', 'dailyaidecheck.timer'],
                capture_output=True,
                text=True,
                timeout=5
            )
            return result.stdout.strip() == 'active'
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def trigger_check(self) -> bool:
        """
        Triggert einen manuellen AIDE Check

        Returns:
            True wenn erfolgreich gestartet
        """
        try:
            subprocess.Popen(
                ['sudo', 'aide', '--check'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True
            )
            return True
        except (FileNotFoundError, PermissionError):
            return False

    def get_changes(self) -> Optional[Dict[str, any]]:
        """
        Alias für get_last_check_results() für Event Watcher Kompatibilität

        Returns:
            Dict mit Check-Ergebnissen oder None
        """
        return self.get_last_check_results()
