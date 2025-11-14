"""
Docker Security Scanner Integration
Parsed Trivy Scan Reports und triggert neue Scans
"""

import subprocess
import re
from pathlib import Path
from typing import Dict, Optional
from datetime import datetime


class DockerSecurityMonitor:
    """Monitort Docker Security Scans"""

    def __init__(self, scan_dir: str = "/var/log/trivy-scans"):
        self.scan_dir = Path(scan_dir)
        self.scan_script = Path("/home/cmdshadow/docker-security-scan.sh")

    def get_latest_scan_results(self) -> Optional[Dict[str, any]]:
        """
        Liest das neueste Scan-Ergebnis

        Returns:
            Dict mit Scan-Ergebnissen oder None
        """
        try:
            # Finde neueste Summary-Datei
            summary_files = sorted(self.scan_dir.glob("summary_*.txt"), reverse=True)

            if not summary_files:
                return None

            latest_summary = summary_files[0]

            with open(latest_summary, 'r') as f:
                content = f.read()

            # Parse Summary
            results = {
                "date": None,
                "images": 0,
                "critical": 0,
                "high": 0,
                "medium": 0,
                "low": 0,
                "summary_file": str(latest_summary),
            }

            # Extrahiere Werte
            date_match = re.search(r'Datum: (.+)', content)
            if date_match:
                results["date"] = date_match.group(1).strip()

            images_match = re.search(r'Gescannte Images: (\d+)', content)
            if images_match:
                results["images"] = int(images_match.group(1))

            critical_match = re.search(r'CRITICAL: (\d+)', content)
            if critical_match:
                results["critical"] = int(critical_match.group(1))

            high_match = re.search(r'HIGH:\s+(\d+)', content)
            if high_match:
                results["high"] = int(high_match.group(1))

            medium_match = re.search(r'MEDIUM:\s+(\d+)', content)
            if medium_match:
                results["medium"] = int(medium_match.group(1))

            low_match = re.search(r'LOW:\s+(\d+)', content)
            if low_match:
                results["low"] = int(low_match.group(1))

            return results

        except (FileNotFoundError, PermissionError, IOError):
            return None

    def trigger_scan(self) -> bool:
        """
        Triggert einen manuellen Security-Scan

        Returns:
            True wenn erfolgreich gestartet
        """
        try:
            if not self.scan_script.exists():
                return False

            # Führe Scan-Script aus (async im Hintergrund)
            subprocess.Popen(
                [str(self.scan_script)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True
            )

            return True

        except (FileNotFoundError, PermissionError):
            return False

    def get_scan_date(self) -> Optional[str]:
        """
        Gibt Datum des letzten Scans zurück

        Returns:
            Datum-String oder None
        """
        results = self.get_latest_scan_results()
        return results["date"] if results else None

    def get_scan_results(self) -> Optional[Dict[str, any]]:
        """
        Alias für get_latest_scan_results() für Event Watcher Kompatibilität

        Returns:
            Dict mit Scan-Ergebnissen oder None
        """
        return self.get_latest_scan_results()
