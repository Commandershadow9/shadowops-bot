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

    def get_detailed_vulnerabilities(self) -> Optional[Dict[str, any]]:
        """
        Liest detaillierte Vulnerabilities aus JSON Scan-Reports

        Returns:
            Dict mit Image-Namen und deren Vulnerabilities
        """
        try:
            import json

            # Finde neueste JSON-Dateien (Trivy speichert pro Image ein JSON)
            json_files = sorted(self.scan_dir.glob("*.json"), reverse=True)

            if not json_files:
                return None

            detailed_results = {
                'images': {},
                'total_critical': 0,
                'total_high': 0,
                'affected_projects': set()
            }

            # Parse alle JSON files
            for json_file in json_files:
                try:
                    with open(json_file, 'r') as f:
                        scan_data = json.load(f)

                    # Trivy JSON Format: {"Results": [...], "ArtifactName": "image:tag"}
                    image_name = scan_data.get('ArtifactName', str(json_file.name))

                    # Extrahiere Projekt aus Image-Namen
                    project_name = self._extract_project_from_image(image_name)

                    if project_name:
                        detailed_results['affected_projects'].add(project_name)

                    # Zähle Vulnerabilities
                    critical_count = 0
                    high_count = 0
                    vulnerabilities = []

                    for result in scan_data.get('Results', []):
                        for vuln in result.get('Vulnerabilities', []):
                            severity = vuln.get('Severity', 'UNKNOWN')
                            if severity == 'CRITICAL':
                                critical_count += 1
                            elif severity == 'HIGH':
                                high_count += 1

                            vulnerabilities.append({
                                'cve': vuln.get('VulnerabilityID', 'N/A'),
                                'package': vuln.get('PkgName', 'N/A'),
                                'severity': severity,
                                'installed_version': vuln.get('InstalledVersion', 'N/A'),
                                'fixed_version': vuln.get('FixedVersion', 'N/A')
                            })

                    if critical_count > 0 or high_count > 0:
                        detailed_results['images'][image_name] = {
                            'project': project_name,
                            'critical': critical_count,
                            'high': high_count,
                            'vulnerabilities': vulnerabilities,
                            'scan_file': str(json_file)
                        }

                        detailed_results['total_critical'] += critical_count
                        detailed_results['total_high'] += high_count

                except (json.JSONDecodeError, KeyError) as e:
                    # Skip invalid JSON files
                    continue

            # Convert set to list for JSON serialization
            detailed_results['affected_projects'] = list(detailed_results['affected_projects'])

            return detailed_results if detailed_results['images'] else None

        except Exception as e:
            return None

    def _extract_project_from_image(self, image_name: str) -> Optional[str]:
        """
        Extrahiert Projekt-Namen aus Docker Image Namen

        Args:
            image_name: Docker image name (z.B. "guildscout:latest", "ghcr.io/user/sicherheitstool:v1")

        Returns:
            Projekt-Name oder None
        """
        image_lower = image_name.lower()

        # Entferne Registry-Prefix (ghcr.io/, docker.io/, etc.)
        if '/' in image_name:
            parts = image_name.split('/')
            image_lower = parts[-1].lower()

        # Entferne Tag (:latest, :v1, etc.)
        if ':' in image_lower:
            image_lower = image_lower.split(':')[0]

        # Match gegen bekannte Projekte
        project_mappings = {
            'guildscout': '/home/cmdshadow/GuildScout',
            'sicherheitstool': '/home/cmdshadow/project',
            'sicherheitsdienst': '/home/cmdshadow/project',
            'shadowops': '/home/cmdshadow/shadowops-bot',
            'bot': '/home/cmdshadow/shadowops-bot'
        }

        for keyword, path in project_mappings.items():
            if keyword in image_lower:
                return path

        return None
