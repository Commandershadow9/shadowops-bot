"""
Configuration Loader für ShadowOps Bot
Lädt YAML-Config und bietet Type-Safe Zugriff
"""

import yaml
import os
from pathlib import Path
from typing import Optional, Dict, Any, List


class Config:
    """Config-Klasse mit Type-Safe Zugriff"""

    def __init__(self, config_path: str = "config/config.yaml"):
        self.config_path = Path(config_path)
        self._config: Dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        """Lädt Config aus YAML-Datei"""
        if not self.config_path.exists():
            raise FileNotFoundError(
                f"Config-Datei nicht gefunden: {self.config_path}\n"
                f"Erstelle config/config.yaml aus config.example.yaml!"
            )

        with open(self.config_path, 'r', encoding='utf-8') as f:
            self._config = yaml.safe_load(f)

        self._validate()

    def _validate(self) -> None:
        """Validiert Config-Werte"""
        # Discord Token erforderlich
        if not self.discord_token or self.discord_token == "YOUR_BOT_TOKEN_HERE":
            raise ValueError("Discord Token fehlt in config.yaml!")

        # Guild ID erforderlich
        if not self.guild_id:
            raise ValueError("Guild ID fehlt in config.yaml!")

        # Mindestens ein Channel erforderlich
        if not self.fallback_channel:
            raise ValueError("Mindestens ein Channel muss konfiguriert sein!")

    # Discord Settings
    @property
    def discord_token(self) -> str:
        return self._config.get('discord', {}).get('token', '')

    @property
    def guild_id(self) -> int:
        return int(self._config.get('discord', {}).get('guild_id', 0))

    # Channels
    @property
    def critical_channel(self) -> int:
        """Kanal für CRITICAL Alerts"""
        ch = self._config.get('channels', {}).get('critical')
        return int(ch) if ch else self.fallback_channel

    @property
    def sicherheitsdienst_channel(self) -> int:
        """Kanal für Sicherheitsdienst-Projekt"""
        ch = self._config.get('channels', {}).get('sicherheitsdienst')
        return int(ch) if ch else self.fallback_channel

    @property
    def nexus_channel(self) -> int:
        """Kanal für NEXUS-Projekt"""
        ch = self._config.get('channels', {}).get('nexus')
        return int(ch) if ch else self.fallback_channel

    @property
    def fail2ban_channel(self) -> int:
        ch = self._config.get('channels', {}).get('fail2ban')
        return int(ch) if ch else self.critical_channel

    @property
    def crowdsec_channel(self) -> int:
        ch = self._config.get('channels', {}).get('crowdsec')
        return int(ch) if ch else self.critical_channel

    @property
    def docker_channel(self) -> int:
        ch = self._config.get('channels', {}).get('docker')
        return int(ch) if ch else self.critical_channel

    @property
    def backups_channel(self) -> int:
        ch = self._config.get('channels', {}).get('backups')
        return int(ch) if ch else self.fallback_channel

    @property
    def aide_channel(self) -> int:
        ch = self._config.get('channels', {}).get('aide')
        return int(ch) if ch else self.critical_channel

    @property
    def ssh_channel(self) -> int:
        ch = self._config.get('channels', {}).get('ssh')
        return int(ch) if ch else self.critical_channel

    @property
    def fallback_channel(self) -> int:
        """Fallback wenn kein spezifischer Channel definiert"""
        return int(self._config.get('channels', {}).get('security_alerts',
                   self._config.get('channels', {}).get('critical', 0)))

    def get_channel_for_alert(self, alert_type: str, project: Optional[str] = None) -> int:
        """
        Gibt die richtige Channel-ID für einen Alert-Typ zurück

        Args:
            alert_type: fail2ban, crowdsec, docker, backup, aide, ssh
            project: sicherheitsdienst, nexus (optional)

        Returns:
            Channel ID
        """
        # Projekt-spezifische Channels
        if project == 'sicherheitsdienst':
            return self.sicherheitsdienst_channel
        elif project == 'nexus':
            return self.nexus_channel

        # Alert-Typ-spezifische Channels
        channel_map = {
            'fail2ban': self.fail2ban_channel,
            'crowdsec': self.crowdsec_channel,
            'docker': self.docker_channel,
            'backup': self.backups_channel,
            'aide': self.aide_channel,
            'ssh': self.ssh_channel,
            'critical': self.critical_channel,
        }

        return channel_map.get(alert_type, self.fallback_channel)

    # Projects
    @property
    def projects(self) -> Dict[str, Dict[str, Any]]:
        return self._config.get('projects', {})

    def get_project_config(self, project_name: str) -> Optional[Dict[str, Any]]:
        """Gibt Project-Config zurück"""
        return self.projects.get(project_name)

    def is_project_enabled(self, project_name: str) -> bool:
        """Prüft ob Projekt aktiviert ist"""
        project = self.get_project_config(project_name)
        return project.get('enabled', False) if project else False

    # Alerts
    @property
    def min_severity(self) -> str:
        return self._config.get('alerts', {}).get('min_severity', 'HIGH').upper()

    @property
    def rate_limit_seconds(self) -> int:
        return self._config.get('alerts', {}).get('rate_limit_seconds', 60)

    @property
    def mention_role_critical(self) -> Optional[int]:
        role = self._config.get('alerts', {}).get('mention_roles', {}).get('critical')
        return int(role) if role else None

    @property
    def mention_role_high(self) -> Optional[int]:
        role = self._config.get('alerts', {}).get('mention_roles', {}).get('high')
        return int(role) if role else None

    # Log Paths
    @property
    def log_paths(self) -> Dict[str, str]:
        return self._config.get('log_paths', {})

    # Permissions
    @property
    def admin_user_ids(self) -> List[int]:
        admins = self._config.get('permissions', {}).get('admins', [])
        return [int(uid) for uid in admins]

    def is_admin(self, user_id: int) -> bool:
        """Prüft ob User Admin ist"""
        return user_id in self.admin_user_ids

    # Bot Settings
    @property
    def bot_status(self) -> str:
        return self._config.get('bot', {}).get('status', '🔒 Monitoring Security')

    @property
    def debug_mode(self) -> bool:
        return self._config.get('bot', {}).get('debug', False)

    @property
    def auto_reconnect(self) -> bool:
        return self._config.get('bot', {}).get('auto_reconnect', True)


# Globale Config-Instanz
config: Optional[Config] = None


def get_config() -> Config:
    """Singleton Pattern für Config"""
    global config
    if config is None:
        config = Config()
    return config
