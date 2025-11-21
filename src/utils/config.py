"""
Configuration Loader für ShadowOps Bot
Lädt YAML-Config und bietet Type-Safe Zugriff
"""

import yaml
import os
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List

logger = logging.getLogger('shadowops')


class Config:
    """Config-Klasse mit Type-Safe Zugriff"""

    def __init__(self, config_path: str = "config/config.yaml"):
        self.config_path = Path(config_path)
        self._config: Dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        """Lädt Config aus YAML-Datei"""
        if not self.config_path.exists():
            error_msg = (
                f"\n{'='*70}\n"
                f"❌ CONFIG-DATEI NICHT GEFUNDEN: {self.config_path}\n"
                f"{'='*70}\n\n"
                f"LÖSUNG:\n"
                f"1. Kopiere die Example-Config:\n"
                f"   cp config/config.example.yaml config/config.yaml\n\n"
                f"2. Bearbeite die Config:\n"
                f"   nano config/config.yaml\n\n"
                f"3. Fülle mindestens diese Werte aus:\n"
                f"   - discord.token (von https://discord.com/developers/applications)\n"
                f"   - discord.guild_id (Discord Server ID)\n"
                f"   - channels.critical (Channel ID für Alerts)\n\n"
                f"TIP: Entwickler-Modus in Discord aktivieren für ID-Kopieren!\n"
                f"{'='*70}\n"
            )
            raise FileNotFoundError(error_msg)

        # Load YAML with error handling
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                self._config = yaml.safe_load(f)
        except yaml.YAMLError as e:
            error_msg = (
                f"\n{'='*70}\n"
                f"❌ YAML-SYNTAX-FEHLER in {self.config_path}\n"
                f"{'='*70}\n\n"
                f"Fehler: {e}\n\n"
                f"LÖSUNG:\n"
                f"- Prüfe YAML-Syntax (Einrückung, Doppelpunkte, Anführungszeichen)\n"
                f"- Vergleiche mit config.example.yaml\n"
                f"- Online YAML-Validator nutzen: https://www.yamllint.com/\n"
                f"{'='*70}\n"
            )
            raise ValueError(error_msg) from e

        # Validate config
        self._validate()

        # Log successful load
        if hasattr(logger, 'info'):
            logger.info(f"✅ Config geladen: {self.config_path}")

    def _validate(self) -> None:
        """Validiert Config-Werte mit detailliertem Feedback"""
        errors = []
        warnings = []

        # === PFLICHTFELDER ===

        # Discord Token
        if not self.discord_token or self.discord_token == "YOUR_BOT_TOKEN_HERE":
            errors.append(
                "discord.token fehlt!\n"
                "   → Hole Token von: https://discord.com/developers/applications\n"
                "   → Bot → Reset Token → Kopieren → In config.yaml einfügen"
            )

        # Guild ID
        if not self.guild_id or self.guild_id == 0:
            errors.append(
                "discord.guild_id fehlt!\n"
                "   → Discord Developer Mode aktivieren: Einstellungen → Erweitert\n"
                "   → Rechtsklick auf Server → 'ID kopieren'\n"
                "   → In config.yaml einfügen"
            )

        # Mindestens ein Channel
        if not self.fallback_channel or self.fallback_channel == 0:
            errors.append(
                "channels.critical fehlt!\n"
                "   → Rechtsklick auf Channel → 'ID kopieren'\n"
                "   → Mindestens channels.critical muss konfiguriert sein"
            )

        # === OPTIONALE ABER EMPFOHLENE FELDER ===

        # Auto-Remediation
        if not self.auto_remediation.get('enabled'):
            warnings.append("auto_remediation.enabled ist false - Bot ist im passiven Modus")

        # AI Service
        ai_ollama = self._config.get('ai', {}).get('ollama', {}).get('enabled', False)
        ai_claude_enabled = self._config.get('ai', {}).get('anthropic', {}).get('enabled', False)
        ai_claude_key = self._config.get('ai', {}).get('anthropic', {}).get('api_key', '')
        ai_openai_enabled = self._config.get('ai', {}).get('openai', {}).get('enabled', False)
        ai_openai_key = self._config.get('ai', {}).get('openai', {}).get('api_key', '')

        ai_claude = ai_claude_enabled and ai_claude_key
        ai_openai = ai_openai_enabled and ai_openai_key

        if not (ai_ollama or ai_claude or ai_openai):
            warnings.append(
                "Keine AI-Services konfiguriert!\n"
                "   → Mindestens Ollama, Claude oder OpenAI sollte aktiviert sein\n"
                "   → Auto-Remediation benötigt AI für Analyse"
            )

        # Projects
        if not self.projects:
            warnings.append("Keine Projekte konfiguriert - Bot überwacht nur System")

        # === FEHLERAUSGABE ===

        if errors:
            error_msg = (
                f"\n{'='*70}\n"
                f"❌ CONFIG-VALIDIERUNG FEHLGESCHLAGEN\n"
                f"{'='*70}\n\n"
                f"Folgende PFLICHTFELDER fehlen:\n\n"
            )
            for i, err in enumerate(errors, 1):
                error_msg += f"{i}. {err}\n\n"

            error_msg += (
                f"{'='*70}\n"
                f"LÖSUNG: Bearbeite config/config.yaml und fülle die Pflichtfelder!\n"
                f"{'='*70}\n"
            )
            raise ValueError(error_msg)

        # === WARNINGS AUSGEBEN ===

        if warnings and hasattr(logger, 'warning'):
            logger.warning("⚠️ Config-Warnings:")
            for warn in warnings:
                logger.warning(f"   - {warn}")

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

    # Auto-Remediation Settings
    @property
    def auto_remediation(self) -> Dict[str, Any]:
        """Auto-Remediation System Config"""
        return self._config.get('auto_remediation', {})

    # AI Settings
    @property
    def ai(self) -> Dict[str, Any]:
        """AI Service Config (OpenAI, Anthropic)"""
        return self._config.get('ai', {})

    # Direct Channels Access
    @property
    def channels(self) -> Dict[str, Any]:
        """Direct access to channels dict"""
        return self._config.get('channels', {})


# Globale Config-Instanz
config: Optional[Config] = None


def get_config() -> Config:
    """Singleton Pattern für Config"""
    global config
    if config is None:
        config = Config()
    return config
