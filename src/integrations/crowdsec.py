"""
CrowdSec Integration
Monitort CrowdSec für Bedrohungen und Decisions
"""

import subprocess
import json
import time
from typing import List, Dict, Optional, Callable
from datetime import datetime


class CrowdSecMonitor:
    """Monitort CrowdSec für Bedrohungen"""

    def __init__(self):
        pass

    def _call_with_retry(self, func: Callable, max_retries: int = 3):
        """
        Retry wrapper for CrowdSec calls with exponential backoff

        Args:
            func: Function to call (should return subprocess result)
            max_retries: Maximum retry attempts

        Returns:
            Result from func or None on failure
        """
        for attempt in range(1, max_retries + 1):
            try:
                result = func()

                # If subprocess timed out or failed, retry
                if result.returncode != 0 and attempt < max_retries:
                    delay = 2 ** (attempt - 1)  # Exponential: 1s, 2s, 4s
                    print(f"⚠️ CrowdSec call failed (attempt {attempt}/{max_retries}), return code: {result.returncode}, retrying in {delay}s...")
                    if result.stderr:
                        print(f"   Error: {result.stderr[:200]}")
                    time.sleep(delay)
                    continue

                return result

            except subprocess.TimeoutExpired as e:
                if attempt < max_retries:
                    delay = 2 ** (attempt - 1)
                    print(f"⚠️ CrowdSec timeout (attempt {attempt}/{max_retries}), retrying in {delay}s...")
                    time.sleep(delay)
                else:
                    raise
            except Exception as e:
                # Other errors - don't retry
                raise

        return None

    def get_active_decisions(self, limit: int = 50) -> List[Dict[str, str]]:
        """
        Holt aktive Decisions (gebannte IPs) von CrowdSec with retry logic

        Args:
            limit: Maximale Anzahl

        Returns:
            Liste von Decisions
        """
        decisions = []

        try:
            # Use retry wrapper for subprocess call
            result = self._call_with_retry(
                lambda: subprocess.run(
                    ['sudo', 'cscli', 'decisions', 'list', '-o', 'json'],
                    capture_output=True,
                    text=True,
                    timeout=15
                )
            )

            if not result or result.returncode != 0:
                return decisions

            # Parse JSON Output
            data = json.loads(result.stdout)

            # CrowdSec returns alerts/events with nested decisions
            for alert in data:
                # Each alert can have multiple decisions
                for decision in alert.get("decisions", []):
                    if len(decisions) >= limit:
                        break

                    decisions.append({
                        "ip": decision.get("value", "Unknown"),
                        "reason": alert.get("message", "Unknown"),
                        "scenario": decision.get("scenario", "Unknown"),
                        "duration": decision.get("duration", "Unknown"),
                        "scope": decision.get("scope", "Unknown"),
                        "type": decision.get("type", "ban"),
                        "origin": decision.get("origin", "Unknown"),
                    })

                if len(decisions) >= limit:
                    break

        except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError, json.JSONDecodeError):
            pass

        return decisions

    def get_recent_alerts(self, limit: int = 20) -> List[Dict[str, str]]:
        """
        Holt neueste Alerts von CrowdSec

        Args:
            limit: Maximale Anzahl

        Returns:
            Liste von Alerts
        """
        alerts = []

        try:
            result = subprocess.run(
                ['sudo', 'cscli', 'alerts', 'list', '-o', 'json', '--limit', str(limit)],
                capture_output=True,
                text=True,
                timeout=15
            )

            if result.returncode != 0:
                return alerts

            data = json.loads(result.stdout)

            for alert in data:
                alerts.append({
                    "id": str(alert.get("id", "")),
                    "scenario": alert.get("scenario", "Unknown"),
                    "message": alert.get("message", ""),
                    "source_ip": alert.get("source", {}).get("ip", "Unknown"),
                    "source_country": alert.get("source", {}).get("cn", ""),
                    "events_count": str(alert.get("events_count", 0)),
                    "created_at": alert.get("created_at", ""),
                })

        except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError, json.JSONDecodeError):
            pass

        return alerts

    def get_metrics(self) -> Dict[str, int]:
        """
        Holt CrowdSec Metriken

        Returns:
            Dict mit Metriken: {"active_decisions": 5, "alerts_total": 123}
        """
        metrics = {
            "active_decisions": 0,
            "alerts_total": 0,
        }

        try:
            # Aktive Decisions
            result = subprocess.run(
                ['sudo', 'cscli', 'metrics', 'show', 'local-api'],
                capture_output=True,
                text=True,
                timeout=15
            )

            if result.returncode == 0:
                # Parse Metrics Output (text-based)
                # Suche nach "Active Decisions"
                for line in result.stdout.split('\n'):
                    if 'active decisions' in line.lower():
                        # Extrahiere Zahl
                        import re
                        match = re.search(r'(\d+)', line)
                        if match:
                            metrics["active_decisions"] = int(match.group(1))

        except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError):
            pass

        # Alerts zählen
        try:
            result = subprocess.run(
                ['sudo', 'cscli', 'alerts', 'list', '-o', 'json'],
                capture_output=True,
                text=True,
                timeout=15
            )

            if result.returncode == 0:
                data = json.loads(result.stdout)
                metrics["alerts_total"] = len(data)

        except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError, json.JSONDecodeError):
            pass

        return metrics

    def is_running(self) -> bool:
        """
        Prüft ob CrowdSec läuft

        Returns:
            True wenn aktiv
        """
        try:
            result = subprocess.run(
                ['sudo', 'systemctl', 'is-active', 'crowdsec'],
                capture_output=True,
                text=True,
                timeout=5
            )
            return result.stdout.strip() == 'active'
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def get_decisions(self, limit: int = 50) -> List[Dict[str, str]]:
        """
        Alias für get_active_decisions() für Event Watcher Kompatibilität

        Args:
            limit: Maximale Anzahl

        Returns:
            Liste von Decisions
        """
        return self.get_active_decisions(limit)
