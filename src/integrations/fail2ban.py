"""
Fail2ban Integration
Monitort Fail2ban Logs und erkennt IP-Bans
"""

import re
import subprocess
from typing import List, Dict, Optional
from datetime import datetime, timedelta


class Fail2banMonitor:
    """Monitort Fail2ban für IP-Bans"""

    BAN_PATTERN = re.compile(r'\[(\w+)\] Ban (\d+\.\d+\.\d+\.\d+)')
    UNBAN_PATTERN = re.compile(r'\[(\w+)\] Unban (\d+\.\d+\.\d+\.\d+)')

    def __init__(self, log_path: str = "/var/log/fail2ban/fail2ban.log"):
        self.log_path = log_path
        self._last_position = 0

    def validate_permissions(self) -> bool:
        """
        Check if bot has necessary permissions for fail2ban-client

        Returns:
            True if permissions are valid, False otherwise
        """
        try:
            result = subprocess.run(
                ['sudo', '-n', 'fail2ban-client', 'ping'],
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode != 0:
                print("❌ No permissions for fail2ban-client.")
                print("   Bot needs sudo access without password for fail2ban-client.")
                print("   Add to /etc/sudoers: 'username ALL=(ALL) NOPASSWD: /usr/bin/fail2ban-client'")
                return False

            print("✅ Fail2ban permissions validated successfully")
            return True

        except FileNotFoundError:
            print("❌ fail2ban-client not found. Is Fail2ban installed?")
            return False
        except Exception as e:
            print(f"❌ Failed to validate fail2ban permissions: {e}")
            return False

    def get_new_bans(self) -> List[Dict[str, str]]:
        """
        Liest neue Bans aus dem Log

        Returns:
            Liste von Bans: [{"ip": "1.2.3.4", "jail": "sshd", "timestamp": "..."}]
        """
        bans = []

        try:
            with open(self.log_path, 'r') as f:
                # Springe zur letzten Position
                f.seek(self._last_position)
                new_lines = f.readlines()
                self._last_position = f.tell()

            for line in new_lines:
                match = self.BAN_PATTERN.search(line)
                if match:
                    jail, ip = match.groups()
                    # Extrahiere Timestamp vom Anfang der Zeile
                    # Format: "2025-11-12 09:00:00,123"
                    timestamp_match = re.match(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
                    timestamp = timestamp_match.group(1) if timestamp_match else datetime.now().isoformat()

                    bans.append({
                        "ip": ip,
                        "jail": jail,
                        "timestamp": timestamp
                    })

        except FileNotFoundError:
            pass  # Log-Datei existiert noch nicht
        except PermissionError:
            pass  # Kein Zugriff (Bot muss mit entsprechenden Rechten laufen)

        return bans

    def get_banned_ips(self) -> Dict[str, List[str]]:
        """
        Holt aktuell gebannte IPs via fail2ban-client

        Returns:
            Dict mit Jail → IP-Liste: {"sshd": ["1.2.3.4", "5.6.7.8"]}
        """
        banned = {}

        try:
            # Liste alle Jails
            result = subprocess.run(
                ['sudo', 'fail2ban-client', 'status'],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode != 0:
                return banned

            # Extrahiere Jail-Namen
            jail_match = re.search(r'Jail list:\s+(.+)', result.stdout)
            if not jail_match:
                return banned

            jails = [j.strip() for j in jail_match.group(1).split(',')]

            # Für jeden Jail: hole gebannte IPs
            for jail in jails:
                result = subprocess.run(
                    ['sudo', 'fail2ban-client', 'status', jail],
                    capture_output=True,
                    text=True,
                    timeout=10
                )

                if result.returncode == 0:
                    # Extrahiere gebannte IPs
                    banned_match = re.search(r'Banned IP list:\s+(.+)', result.stdout)
                    if banned_match:
                        ips = banned_match.group(1).strip().split()
                        if ips:
                            banned[jail] = ips

        except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError):
            pass

        return banned

    def get_jail_stats(self) -> Dict[str, Dict[str, int]]:
        """
        Holt Statistiken für alle Jails

        Returns:
            Dict: {"sshd": {"currently_banned": 5, "total_banned": 123, "current_failed": 2}}
        """
        stats = {}

        try:
            result = subprocess.run(
                ['sudo', 'fail2ban-client', 'status'],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode != 0:
                return stats

            jail_match = re.search(r'Jail list:\s+(.+)', result.stdout)
            if not jail_match:
                return stats

            jails = [j.strip() for j in jail_match.group(1).split(',')]

            for jail in jails:
                result = subprocess.run(
                    ['sudo', 'fail2ban-client', 'status', jail],
                    capture_output=True,
                    text=True,
                    timeout=10
                )

                if result.returncode == 0:
                    # Parse Output
                    currently_banned = 0
                    total_banned = 0
                    current_failed = 0

                    for line in result.stdout.split('\n'):
                        if 'Currently banned:' in line:
                            currently_banned = int(re.search(r'(\d+)', line).group(1))
                        elif 'Total banned:' in line:
                            total_banned = int(re.search(r'(\d+)', line).group(1))
                        elif 'Currently failed:' in line:
                            current_failed = int(re.search(r'(\d+)', line).group(1))

                    stats[jail] = {
                        "currently_banned": currently_banned,
                        "total_banned": total_banned,
                        "current_failed": current_failed
                    }

        except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError, AttributeError):
            pass

        return stats
