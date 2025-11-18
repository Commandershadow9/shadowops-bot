"""
Docker Security Scanner Integration
Parsed Trivy Scan Reports und triggert neue Scans
Enhanced with image-level detail extraction from JSON files.
"""

import subprocess
import re
import json
from pathlib import Path
from typing import Dict, Optional, List
from datetime import datetime

from .docker_image_analyzer import DockerImageAnalyzer


class DockerSecurityMonitor:
    """Monitort Docker Security Scans"""

    def __init__(self, scan_dir: str = "/var/log/trivy-scans"):
        self.scan_dir = Path(scan_dir)
        self.scan_script = Path("/home/cmdshadow/docker-security-scan.sh")
        self.analyzer = DockerImageAnalyzer()

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

    def get_detailed_scan_results(self) -> Optional[Dict[str, any]]:
        """
        Enhanced scan results with image-level details from JSON files

        Returns:
            Dict with detailed scan results including per-image vulnerabilities
        """
        try:
            # First get summary results
            summary = self.get_latest_scan_results()

            if not summary:
                return None

            # Find latest JSON file (more detailed than summary)
            json_files = sorted(self.scan_dir.glob("scan_*.json"), reverse=True)

            if not json_files:
                # No JSON files, return summary only (fallback mode)
                return {
                    'images': {},  # No details available
                    'total_critical': summary.get('critical', 0),
                    'total_high': summary.get('high', 0),
                    'total_medium': summary.get('medium', 0),
                    'total_low': summary.get('low', 0),
                    'affected_projects': [],
                    'summary_mode': True,
                    'date': summary.get('date')
                }

            # Parse JSON file for detailed image info
            latest_json = json_files[0]
            image_details = self.analyzer.analyze_trivy_scan(str(latest_json))

            # Analyze each image
            analyzed_images = {}
            affected_projects = []

            for img_vuln in image_details:
                image_name = img_vuln['image']
                image_info = self.analyzer.analyze_image(image_name)

                # Get remediation strategy
                strategy = self.analyzer.get_remediation_strategy(
                    image_info,
                    img_vuln['total']
                )

                analyzed_images[image_name] = {
                    'vulnerabilities': img_vuln,
                    'image_info': {
                        'name': image_info.name,
                        'tag': image_info.tag,
                        'is_external': image_info.is_external,
                        'has_dockerfile': image_info.has_dockerfile,
                        'dockerfile_path': image_info.dockerfile_path,
                        'update_available': image_info.update_available,
                        'latest_version': image_info.latest_version
                    },
                    'recommended_action': strategy['action'],
                    'strategy': strategy
                }

                # Track affected projects
                if not image_info.is_external and image_info.dockerfile_path:
                    # Extract project name from path
                    project_path = str(Path(image_info.dockerfile_path).parent)
                    if project_path not in affected_projects:
                        affected_projects.append(project_path)

            return {
                'images': analyzed_images,
                'total_critical': summary.get('critical', 0),
                'total_high': summary.get('high', 0),
                'total_medium': summary.get('medium', 0),
                'total_low': summary.get('low', 0),
                'affected_projects': affected_projects,
                'summary_mode': False,
                'date': summary.get('date'),
                'json_file': str(latest_json)
            }

        except Exception as e:
            # Fallback to summary if detailed analysis fails
            summary = self.get_latest_scan_results()
            if summary:
                return {
                    'images': {},
                    'total_critical': summary.get('critical', 0),
                    'total_high': summary.get('high', 0),
                    'affected_projects': [],
                    'summary_mode': True,
                    'error': str(e)
                }
            return None
