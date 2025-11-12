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
        if not self.security_alerts_channel:
            raise ValueError("security_alerts Channel-ID fehlt in config.yaml!")

    # Discord Settings
    @property
    def discord_token(self) -> str:
        return self._config.get('discord', {}).get('token', '')

    @property
    def guild_id(self) -> int:
        return int(self._config.get('discord', {}).get('guild_id', 0))

    # Channels
    @property
    def security_alerts_channel(self) -> int:
        return int(self._config.get('channels', {}).get('security_alerts', 0))

    @property
    def fail2ban_channel(self) -> Optional[int]:
        channel = self._config.get('channels', {}).get('fail2ban')
        return int(channel) if channel else None

    @property
    def crowdsec_channel(self) -> Optional[int]:
        channel = self._config.get('channels', {}).get('crowdsec')
        return int(channel) if channel else None

    @property
    def docker_scans_channel(self) -> Optional[int]:
        channel = self._config.get('channels', {}).get('docker_scans')
        return int(channel) if channel else None

    @property
    def backups_channel(self) -> Optional[int]:
        channel = self._config.get('channels', {}).get('backups')
        return int(channel) if channel else None

    @property
    def aide_channel(self) -> Optional[int]:
        channel = self._config.get('channels', {}).get('aide')
        return int(channel) if channel else None

    @property
    def ssh_channel(self) -> Optional[int]:
        channel = self._config.get('channels', {}).get('ssh')
        return int(channel) if channel else None

    def get_channel_for_alert(self, alert_type: str) -> int:
        """Gibt die richtige Channel-ID für einen Alert-Typ zurück"""
        channel_map = {
            'fail2ban': self.fail2ban_channel,
            'crowdsec': self.crowdsec_channel,
            'docker': self.docker_scans_channel,
            'backup': self.backups_channel,
            'aide': self.aide_channel,
            'ssh': self.ssh_channel,
        }

        # Nutze spezifischen Channel oder fallback zu security_alerts
        return channel_map.get(alert_type) or self.security_alerts_channel

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
