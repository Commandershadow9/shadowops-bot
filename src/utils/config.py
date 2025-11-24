"""
Configuration loader for ShadowOps Bot.
Provides safe dictionary and attribute style access to the YAML configuration.
"""

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from utils.state_manager import get_state_manager

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
        if 'ai' in sanitized and isinstance(sanitized['ai'], dict):
            if 'anthropic' in sanitized['ai']:
                sanitized['ai']['anthropic'] = {**sanitized['ai'].get('anthropic', {}), 'api_key': '***redacted***'}
            if 'openai' in sanitized['ai']:
                sanitized['ai']['openai'] = {**sanitized['ai'].get('openai', {}), 'api_key': '***redacted***'}
        return f"Config({sanitized})"

    def _get_secret(self, env_var: str, config_path: List[str], warn: bool = True) -> Optional[str]:
        """
        Get a secret from environment variables first, then fallback to config file.
        """
        secret = os.getenv(env_var)
        if secret:
            return secret

        # Fallback to config file
        value = self._config
        try:
            for key in config_path:
                value = value[key]
            secret = value
        except (KeyError, TypeError):
            secret = None

        if secret and warn:
            logger.warning(f"⚠️ Loading secret '{'.'.join(config_path)}' from config file. "
                           f"For better security, set the {env_var} environment variable.")
        
        return secret

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

        if not self.discord_token:
            missing_fields.append('discord.token (or DISCORD_BOT_TOKEN env var)')
        if 'guild_id' not in self.discord:
            missing_fields.append('discord.guild_id')

        if missing_fields:
            raise KeyError(f"Missing required config fields: {', '.join(missing_fields)}")

        warnings: List[str] = []

        if not self.auto_remediation.get('enabled'):
            warnings.append("auto_remediation.enabled ist false - Bot ist im passiven Modus")

        ai_cfg = self.ai
        ai_ollama = ai_cfg.get('ollama', {}).get('enabled', False)
        ai_claude = ai_cfg.get('anthropic', {}).get('enabled', False) and self.anthropic_api_key
        ai_openai = ai_cfg.get('openai', {}).get('enabled', False) and self.openai_api_key

        if not (ai_ollama or ai_claude or ai_openai):
            warnings.append("Keine AI-Services konfiguriert! Mindestens einer sollte aktiviert sein")

        if warnings and hasattr(logger, 'warning'):
            logger.warning("⚠️ Config-Warnings:")
            for warn in warnings:
                logger.warning(f"   - {warn}")

    # ---- Convenience accessors ---------------------------------------
    @property
    def discord_token(self) -> Optional[str]:
        return self._get_secret('DISCORD_BOT_TOKEN', ['discord', 'token'])

    @property
    def github_token(self) -> Optional[str]:
        return self._get_secret('GITHUB_TOKEN', ['github', 'token'], warn=False)

    @property
    def anthropic_api_key(self) -> Optional[str]:
        return self._get_secret('ANTHROPIC_API_KEY', ['ai', 'anthropic', 'api_key'])

    @property
    def openai_api_key(self) -> Optional[str]:
        return self._get_secret('OPENAI_API_KEY', ['ai', 'openai', 'api_key'])

    @property
    def guild_id(self) -> int:
        return int(self.discord.get('guild_id', 0))

    def _get_channel_id(self, name: str, fallback_names: Optional[List[str]] = None) -> int:
        """
        Gets a channel ID, prioritizing state over config.
        Supports multiple fallback names.
        """
        state_manager = get_state_manager()
        guild_id = self.guild_id

        # 1. Check state manager for the primary name
        channel_id = state_manager.get_channel_id(guild_id, name)
        if channel_id:
            return channel_id
        
        # Check for auto-remediation channels with 'ar_' prefix in state
        ar_name = f"ar_{name.replace('_channel', '')}"
        channel_id = state_manager.get_channel_id(guild_id, ar_name)
        if channel_id:
            return channel_id

        # 2. Check config for the primary name (from channels and notifications sections)
        primary_sources = [
            self.channels.get(name),
            self.auto_remediation_notifications.get(f"{name}_channel")
        ]
        for source in primary_sources:
            if source:
                return int(source)

        # 3. Check config for fallback names
        if fallback_names:
            for fallback_name in fallback_names:
                fallback_id = self.channels.get(fallback_name)
                if fallback_id:
                    return int(fallback_id)

        return 0 # Final fallback

    @property
    def critical_channel(self) -> int:
        return self._get_channel_id('critical', fallback_names=['security_alerts'])

    @property
    def sicherheitsdienst_channel(self) -> int:
        return self._get_channel_id('sicherheitsdienst', fallback_names=['critical'])

    @property
    def nexus_channel(self) -> int:
        return self._get_channel_id('nexus', fallback_names=['critical'])

    @property
    def fail2ban_channel(self) -> int:
        return self._get_channel_id('fail2ban', fallback_names=['critical'])

    @property
    def crowdsec_channel(self) -> int:
        return self._get_channel_id('crowdsec', fallback_names=['critical'])

    @property
    def docker_channel(self) -> int:
        return self._get_channel_id('docker', fallback_names=['critical'])

    @property
    def backups_channel(self) -> int:
        return self._get_channel_id('backups', fallback_names=['critical'])

    @property
    def aide_channel(self) -> int:
        return self._get_channel_id('aide', fallback_names=['critical'])

    @property
    def ssh_channel(self) -> int:
        return self._get_channel_id('ssh', fallback_names=['critical'])

    @property
    def fallback_channel(self) -> int:
        return self._get_channel_id('critical', fallback_names=['security_alerts'])

    @property
    def alerts_channel(self) -> int:
        return self._get_channel_id('alerts', fallback_names=['critical'])

    @property
    def approvals_channel(self) -> int:
        return self._get_channel_id('approvals', fallback_names=['critical'])

    @property
    def stats_channel(self) -> int:
        return self._get_channel_id('stats', fallback_names=['performance', 'critical'])

    @property
    def ai_learning_channel(self) -> int:
        return self._get_channel_id('ai_learning', fallback_names=['critical'])

    @property
    def code_fixes_channel(self) -> int:
        return self._get_channel_id('code_fixes', fallback_names=['critical'])

    @property
    def orchestrator_channel(self) -> int:
        return self._get_channel_id('orchestrator', fallback_names=['critical'])

    @property
    def customer_alerts_channel(self) -> int:
        return self._get_channel_id('customer_alerts', fallback_names=['critical'])

    @property
    def customer_status_channel(self) -> int:
        return self._get_channel_id('customer_status', fallback_names=['critical'])

    @property
    def deployment_log_channel(self) -> int:
        return self._get_channel_id('deployment_log', fallback_names=['critical'])

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
