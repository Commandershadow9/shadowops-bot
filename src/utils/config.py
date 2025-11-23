"""
Configuration loader for ShadowOps Bot.
Provides safe dictionary and attribute style access to the YAML configuration.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger('shadowops')


class Config:
    """Type-safe configuration helper with convenient access patterns."""

    def __init__(self, config_path: str = "config/config.yaml"):
        self.config_path = Path(config_path)
        self._config: Dict[str, Any] = {}
        self.load()

    def __getitem__(self, key: str) -> Any:
        """Allow dictionary-style access (e.g. config['discord'])."""
        return self._config[key]

    def __repr__(self) -> str:
        return f"Config(path='{self.config_path}')"

    def __str__(self) -> str:
        sanitized = dict(self._config)
        if 'discord' in sanitized and isinstance(sanitized['discord'], dict):
            sanitized['discord'] = {**sanitized['discord'], 'token': '***redacted***'}
        return f"Config({sanitized})"

    # ---- Core sections -------------------------------------------------
    @property
    def discord(self) -> Dict[str, Any]:
        return self._config.get('discord', {})

    @property
    def channels(self) -> Dict[str, Any]:
        return self._config.get('channels', {})

    @property
    def github(self) -> Dict[str, Any]:
        return self._config.get('github', {})

    @property
    def incidents(self) -> Dict[str, Any]:
        return self._config.get('incidents', {})

    @property
    def ai(self) -> Dict[str, Any]:
        return self._config.get('ai', {})

    @property
    def projects(self) -> Any:
        return self._config.get('projects', [])

    @property
    def auto_remediation(self) -> Dict[str, Any]:
        return self._config.get('auto_remediation', {})

    @property
    def auto_remediation_notifications(self) -> Dict[str, Any]:
        return self.auto_remediation.get('notifications', {})

    # ---- Loading & validation -----------------------------------------
    def load(self) -> None:
        """Load configuration from YAML file with helpful error messages."""
        if not self.config_path.exists():
            raise FileNotFoundError(f"Config file not found: {self.config_path}")

        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                self._config = yaml.safe_load(f) or {}
        except yaml.YAMLError as exc:
            # Re-raise YAML parsing issues so callers can surface the exact error
            raise exc

        self._validate()

        if hasattr(logger, 'info'):
            logger.info(f"✅ Config geladen: {self.config_path}")

    def _validate(self) -> None:
        """Validate minimal required fields and log warnings for optional parts."""
        missing_fields = []

        discord_cfg = self.discord or {}
        if 'token' not in discord_cfg:
            missing_fields.append('discord.token')
        if 'guild_id' not in discord_cfg:
            missing_fields.append('discord.guild_id')

        if missing_fields:
            raise KeyError(f"Missing required config fields: {', '.join(missing_fields)}")

        warnings: List[str] = []

        if not self.auto_remediation.get('enabled'):
            warnings.append("auto_remediation.enabled ist false - Bot ist im passiven Modus")

        ai_cfg = self.ai
        ai_ollama = ai_cfg.get('ollama', {}).get('enabled', False)
        ai_claude = ai_cfg.get('anthropic', {}).get('enabled', False) and ai_cfg.get('anthropic', {}).get('api_key')
        ai_openai = ai_cfg.get('openai', {}).get('enabled', False) and ai_cfg.get('openai', {}).get('api_key')

        if not (ai_ollama or ai_claude or ai_openai):
            warnings.append("Keine AI-Services konfiguriert! Mindestens einer sollte aktiviert sein")

        if warnings and hasattr(logger, 'warning'):
            logger.warning("⚠️ Config-Warnings:")
            for warn in warnings:
                logger.warning(f"   - {warn}")

    # ---- Convenience accessors ---------------------------------------
    @property
    def discord_token(self) -> str:
        return self.discord.get('token', '')

    @property
    def guild_id(self) -> int:
        return int(self.discord.get('guild_id', 0))

    @property
    def critical_channel(self) -> int:
        channel = self.channels.get('critical') or self.channels.get('security_alerts')
        return int(channel) if channel is not None else 0

    @property
    def sicherheitsdienst_channel(self) -> int:
        channel = self.channels.get('sicherheitsdienst')
        return int(channel) if channel is not None else self.fallback_channel

    @property
    def nexus_channel(self) -> int:
        channel = self.channels.get('nexus')
        return int(channel) if channel is not None else self.fallback_channel

    @property
    def fail2ban_channel(self) -> int:
        channel = self.channels.get('fail2ban')
        return int(channel) if channel is not None else self.critical_channel

    @property
    def crowdsec_channel(self) -> int:
        channel = self.channels.get('crowdsec')
        return int(channel) if channel is not None else self.critical_channel

    @property
    def docker_channel(self) -> int:
        channel = self.channels.get('docker')
        return int(channel) if channel is not None else self.critical_channel

    @property
    def backups_channel(self) -> int:
        channel = self.channels.get('backups')
        return int(channel) if channel is not None else self.fallback_channel

    @property
    def aide_channel(self) -> int:
        channel = self.channels.get('aide')
        return int(channel) if channel is not None else self.critical_channel

    @property
    def ssh_channel(self) -> int:
        channel = self.channels.get('ssh')
        return int(channel) if channel is not None else self.critical_channel

    @property
    def fallback_channel(self) -> int:
        channel = self.channels.get('security_alerts', self.channels.get('critical', 0))
        return int(channel) if channel is not None else 0

    def _resolve_notification_channel(self, key: str, fallback_key: Optional[str] = None) -> int:
        notifications = self.auto_remediation_notifications
        fallback = self.channels.get(fallback_key) if fallback_key else None
        channel = notifications.get(key, fallback)
        return int(channel) if channel is not None else self.fallback_channel

    @property
    def alerts_channel(self) -> int:
        return self._resolve_notification_channel('alerts_channel', 'critical')

    @property
    def approvals_channel(self) -> int:
        return self._resolve_notification_channel('approvals_channel')

    @property
    def stats_channel(self) -> int:
        return self._resolve_notification_channel('stats_channel', 'performance')

    @property
    def ai_learning_channel(self) -> int:
        return self._resolve_notification_channel('ai_learning_channel', 'ai_learning')

    @property
    def code_fixes_channel(self) -> int:
        return self._resolve_notification_channel('code_fixes_channel', 'code_fixes')

    @property
    def orchestrator_channel(self) -> int:
        return self._resolve_notification_channel('orchestrator_channel', 'orchestrator')

    def get_channel_for_alert(self, alert_type: str, project: Optional[str] = None) -> int:
        """Return a channel ID for the given alert type or project."""
        if project == 'sicherheitsdienst':
            return self.sicherheitsdienst_channel
        if project == 'nexus':
            return self.nexus_channel

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

    def get_project_config(self, project_name: str) -> Optional[Dict[str, Any]]:
        return self.projects.get(project_name) if isinstance(self.projects, dict) else None

    def is_project_enabled(self, project_name: str) -> bool:
        project = self.get_project_config(project_name)
        return bool(project.get('enabled')) if project else False

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

    @property
    def log_paths(self) -> Dict[str, str]:
        return self._config.get('log_paths', {})

    @property
    def admin_user_ids(self) -> List[int]:
        admins = self._config.get('permissions', {}).get('admins', [])
        return [int(uid) for uid in admins]

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admin_user_ids

    @property
    def bot_status(self) -> str:
        return self._config.get('bot', {}).get('status', '🔒 Monitoring Security')

    @property
    def debug_mode(self) -> bool:
        return self._config.get('bot', {}).get('debug', False)

    @property
    def auto_reconnect(self) -> bool:
        return self._config.get('bot', {}).get('auto_reconnect', True)


# Global singleton helper
config: Optional[Config] = None


def get_config() -> Config:
    global config
    if config is None:
        config = Config()
    return config
