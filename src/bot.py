#!/usr/bin/env python3
"""
🗡️ ShadowOps - Security Operations Discord Bot
Monitort Security-Tools und sendet Echtzeit-Alerts
"""

import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
import sys
import os
import atexit
import signal
from pathlib import Path
from datetime import datetime, time
from typing import Optional

# Füge src/ zum Path hinzu
sys.path.insert(0, str(Path(__file__).parent))

from utils.config import get_config
from utils.logger import setup_logger
from utils.embeds import EmbedBuilder, Severity
from utils.discord_logger import DiscordChannelLogger

from integrations.fail2ban import Fail2banMonitor
from integrations.crowdsec import CrowdSecMonitor
from integrations.docker import DockerSecurityMonitor
from integrations.aide import AIDEMonitor
from integrations.event_watcher import SecurityEventWatcher
from integrations.self_healing import SelfHealingCoordinator
from integrations.orchestrator import RemediationOrchestrator
from integrations.ai_service import AIService
from integrations.context_manager import ContextManager


class ShadowOpsBot(commands.Bot):
    """ShadowOps Security Bot"""

    def __init__(self):
        # Load Config
        self.config = get_config()
        self.logger = setup_logger("shadowops", self.config.debug_mode)

        # Discord Intents (ohne Privileged Intents)
        intents = discord.Intents.default()
        # intents.message_content = True  # Nicht benötigt für Slash Commands
        # intents.guild_messages = True   # Nicht benötigt für Slash Commands

        super().__init__(
            command_prefix="!",  # Fallback, wir nutzen Slash Commands
            intents=intents,
            help_command=None
        )

        # Integrations
        self.fail2ban = Fail2banMonitor(self.config.log_paths.get('fail2ban', ''))
        self.crowdsec = CrowdSecMonitor()
        self.docker = DockerSecurityMonitor(self.config.log_paths.get('docker_scans', ''))
        self.aide = AIDEMonitor()

        # Auto-Remediation System
        self.event_watcher = None
        self.self_healing = None
        self.orchestrator = None

        # Discord Channel Logger (für kategorisierte Logs)
        self.discord_logger = DiscordChannelLogger(bot=None, config=self.config)

        # Rate Limiting für Alerts
        self.recent_alerts = {}

        # Flag für einmalige Initialisierung in on_ready
        self._ready_initialized = False

    async def _get_or_create_category(self, guild, category_name: str):
        """
        Findet oder erstellt eine Discord-Kategorie
        """
        # Suche existierende Kategorie
        category = discord.utils.get(guild.categories, name=category_name)
        if category:
            self.logger.info(f"✅ Kategorie '{category_name}' gefunden (ID: {category.id})")
            return category

        # Erstelle neue Kategorie
        self.logger.info(f"📝 Erstelle Kategorie: {category_name}")
        category = await guild.create_category(
            name=category_name,
            reason="ShadowOps Bot Setup"
        )
        self.logger.info(f"✅ Kategorie '{category_name}' erstellt (ID: {category.id})")
        return category

    async def _setup_auto_remediation_channels(self):
        """
        Erstellt automatisch ALLE benötigten Discord Channels
        und speichert die Channel-IDs in der Config
        """
        try:
            self.logger.info("🔧 Prüfe und erstelle Discord Channels...")

            # Hole Guild
            guild = self.get_guild(self.config.guild_id)
            if not guild:
                self.logger.error(f"❌ Guild {self.config.guild_id} nicht gefunden!")
                return

            # Prüfe Bot Permissions
            bot_member = guild.get_member(self.user.id)
            if not bot_member:
                self.logger.error("❌ Bot-Member nicht gefunden!")
                return

            if not bot_member.guild_permissions.manage_channels:
                self.logger.error("❌ Bot hat keine 'Manage Channels' Permission!")
                self.logger.error("   Bitte gebe dem Bot die 'Manage Channels' Berechtigung in Discord!")
                return

            self.logger.info("✅ Bot hat 'Manage Channels' Permission")

            # ============================================
            # KATEGORIEN ERSTELLEN/FINDEN
            # ============================================
            security_category = await self._get_or_create_category(guild, "🔐 Security Monitoring")
            auto_remediation_category = await self._get_or_create_category(guild, "🤖 Auto-Remediation")
            system_category = await self._get_or_create_category(guild, "⚙️ System Status")

            # ============================================
            # TEIL 1: ALLE STANDARD CHANNELS PRÜFEN
            # ============================================
            standard_channels = {
                'critical': ('🔴-critical', 'Kritische Security Alerts - Sofortige Reaktion erforderlich', security_category),
                'sicherheitsdienst': ('🛡️-security', 'Sicherheitsdienst Project Alerts', security_category),
                'nexus': ('⚡-nexus', 'Nexus Project Alerts', security_category),
                'fail2ban': ('🚫-fail2ban', 'Fail2ban Bans und Aktivitäten', security_category),
                'docker': ('🐳-docker', 'Docker Security Scans (Trivy)', security_category),
                'backups': ('💾-backups', 'Backup Status und Logs', security_category),
                'bot_status': ('🤖-bot-status', '⚙️ Bot Startup, Health-Checks und System-Status', system_category),
                'performance': ('📊-performance', '📊 Performance Monitor: CPU, RAM, Resource Anomalies', system_category),
            }

            channels_created = False
            updated_channel_ids = {}

            for channel_key, (channel_name, description, target_category) in standard_channels.items():
                current_id = self.config.channels.get(channel_key)

                # Prüfe ob Channel existiert (by ID)
                if current_id:
                    existing_channel = guild.get_channel(current_id)
                    if existing_channel:
                        # Verschiebe Channel in richtige Kategorie (falls nicht bereits dort)
                        if existing_channel.category_id != target_category.id:
                            self.logger.info(f"📦 Verschiebe '{channel_name}' → {target_category.name}")
                            await existing_channel.edit(category=target_category)
                        self.logger.info(f"✅ Channel '{channel_name}' existiert (ID: {current_id})")
                        continue

                # Prüfe ob Channel existiert (by name)
                existing_channel = discord.utils.get(guild.text_channels, name=channel_name)
                if existing_channel:
                    # Verschiebe Channel in richtige Kategorie
                    if existing_channel.category_id != target_category.id:
                        self.logger.info(f"📦 Verschiebe '{channel_name}' → {target_category.name}")
                        await existing_channel.edit(category=target_category)
                    self.logger.info(f"✅ Channel '{channel_name}' gefunden (ID: {existing_channel.id})")
                    updated_channel_ids[channel_key] = existing_channel.id
                    channels_created = True
                    continue

                # Channel existiert nicht → erstellen
                self.logger.info(f"📝 Erstelle Standard-Channel: {channel_name}")

                new_channel = await guild.create_text_channel(
                    name=channel_name,
                    topic=description,
                    category=target_category,
                    reason="ShadowOps Bot Setup - Standard Channels"
                )

                self.logger.info(f"✅ Channel '{channel_name}' erstellt (ID: {new_channel.id})")
                updated_channel_ids[channel_key] = new_channel.id
                channels_created = True

            # ============================================
            # TEIL 2: AUTO-REMEDIATION CHANNELS
            # ============================================
            channel_names = self.config.auto_remediation.get('channel_names', {})
            alerts_name = channel_names.get('alerts', '🤖-auto-remediation-alerts')
            approvals_name = channel_names.get('approvals', '✋-auto-remediation-approvals')
            stats_name = channel_names.get('stats', '📊-auto-remediation-stats')

            notifications = self.config.auto_remediation.get('notifications', {})

            auto_remediation_channels = [
                ('alerts', alerts_name, '🤖 Live-Updates aller Auto-Remediation Fixes'),
                ('approvals', approvals_name, '✋ Human-Approval Requests für kritische Fixes'),
                ('stats', stats_name, '📊 Tägliche Auto-Remediation Statistiken'),
                ('ai_learning', '🧠-ai-learning', '🧠 AI Learning Logs: Code Analyzer, Git History, Knowledge Base'),
                ('code_fixes', '🔧-code-fixes', '🔧 Code Fixer: Vulnerability Processing & Fix Generation'),
                ('orchestrator', '⚡-orchestrator', '⚡ Orchestrator: Batch Event Coordination & Planning'),
            ]

            for channel_type, channel_name, description in auto_remediation_channels:
                current_id = notifications.get(f'{channel_type}_channel')

                # Prüfe ob Channel existiert (by ID)
                if current_id:
                    existing_channel = guild.get_channel(current_id)
                    if existing_channel:
                        # Verschiebe Channel in richtige Kategorie (falls nicht bereits dort)
                        if existing_channel.category_id != auto_remediation_category.id:
                            self.logger.info(f"📦 Verschiebe '{channel_name}' → 🤖 Auto-Remediation")
                            await existing_channel.edit(category=auto_remediation_category)
                        self.logger.info(f"✅ Channel '{channel_name}' existiert (ID: {current_id})")
                        continue

                # Prüfe ob Channel existiert (by name)
                existing_channel = discord.utils.get(guild.text_channels, name=channel_name)
                if existing_channel:
                    # Verschiebe Channel in richtige Kategorie
                    if existing_channel.category_id != auto_remediation_category.id:
                        self.logger.info(f"📦 Verschiebe '{channel_name}' → 🤖 Auto-Remediation")
                        await existing_channel.edit(category=auto_remediation_category)
                    self.logger.info(f"✅ Channel '{channel_name}' gefunden (ID: {existing_channel.id})")
                    updated_channel_ids[f'auto_remediation_{channel_type}'] = existing_channel.id
                    channels_created = True
                    continue

                # Channel existiert nicht → erstellen
                self.logger.info(f"📝 Erstelle Auto-Remediation-Channel: {channel_name}")

                new_channel = await guild.create_text_channel(
                    name=channel_name,
                    topic=description,
                    category=auto_remediation_category,
                    reason="Auto-Remediation System Setup"
                )

                self.logger.info(f"✅ Channel '{channel_name}' erstellt (ID: {new_channel.id})")
                updated_channel_ids[f'auto_remediation_{channel_type}'] = new_channel.id
                channels_created = True

            # Update Config mit Channel-IDs
            if channels_created:
                self.logger.info("💾 Speichere Channel-IDs in Config...")
                await self._update_all_channel_ids(updated_channel_ids)
                self.logger.info("✅ Channel-Setup komplett!")
            else:
                self.logger.info("ℹ️ Alle Channels existieren bereits")

            # Initialisiere Discord Channel Logger
            self.logger.info("🔄 Initialisiere Discord Channel Logger...")
            self.discord_logger.set_bot(self)
            await self.discord_logger.start()
            self.logger.info("✅ Discord Channel Logger bereit")

        except discord.Forbidden:
            self.logger.error("❌ FEHLER: Bot hat keine Berechtigung Channels zu erstellen!")
            self.logger.error("   Lösung: Gehe zu Discord Server Settings → Roles → ShadowOps")
            self.logger.error("   Aktiviere: 'Manage Channels' Permission")
        except Exception as e:
            self.logger.error(f"❌ Fehler beim Setup der Channels: {e}", exc_info=True)

    async def _update_all_channel_ids(self, channel_ids: dict):
        """
        Schreibt ALLE Channel-IDs zurück in config.yaml
        Unterstützt sowohl Standard-Channels als auch Auto-Remediation Channels
        """
        try:
            import yaml
            from pathlib import Path

            config_path = Path(__file__).parent.parent / 'config' / 'config.yaml'

            # Lese aktuelle Config
            with open(config_path, 'r', encoding='utf-8') as f:
                config_data = yaml.safe_load(f)

            # Update Standard Channels
            standard_channel_keys = ['critical', 'sicherheitsdienst', 'nexus', 'fail2ban', 'docker', 'backups', 'bot_status', 'performance']
            for key in standard_channel_keys:
                if key in channel_ids:
                    if 'channels' not in config_data:
                        config_data['channels'] = {}
                    config_data['channels'][key] = channel_ids[key]
                    self.logger.info(f"💾 Channel '{key}' ID gespeichert: {channel_ids[key]}")

            # Update Auto-Remediation Channels
            if 'auto_remediation' in config_data:
                if 'notifications' not in config_data['auto_remediation']:
                    config_data['auto_remediation']['notifications'] = {}

                # Extract auto_remediation channel IDs
                for key, value in channel_ids.items():
                    if key.startswith('auto_remediation_'):
                        channel_type = key.replace('auto_remediation_', '')
                        config_data['auto_remediation']['notifications'][f'{channel_type}_channel'] = value
                        self.logger.info(f"💾 Auto-Remediation '{channel_type}' ID gespeichert: {value}")

                # Update runtime config
                if 'notifications' not in self.config.auto_remediation:
                    self.config.auto_remediation['notifications'] = {}

                for key, value in channel_ids.items():
                    if key.startswith('auto_remediation_'):
                        channel_type = key.replace('auto_remediation_', '')
                        self.config.auto_remediation['notifications'][f'{channel_type}_channel'] = value

            # Schreibe zurück
            with open(config_path, 'w', encoding='utf-8') as f:
                yaml.dump(config_data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

            self.logger.info("✅ Config-Datei aktualisiert mit allen Channel-IDs")

        except Exception as e:
            self.logger.warning(f"⚠️ Konnte Config-Datei nicht aktualisieren: {e}")

    async def _update_config_channel_ids(self, channel_ids: dict):
        """DEPRECATED: Use _update_all_channel_ids instead"""
        await self._update_all_channel_ids(channel_ids)

    async def setup_hook(self):
        """Setup Hook - wird VOR Discord-Verbindung aufgerufen"""
        self.logger.info("🗡️ ShadowOps Bot startet...")
        self.logger.info("⏳ Warte auf Discord-Verbindung...")

    async def _send_status_message(self, message: str, color: int = 0x00FF00):
        """Sendet eine Status-Nachricht an den Bot-Status Channel"""
        try:
            bot_status_channel_id = self.config.channels.get('bot_status')
            if bot_status_channel_id:
                channel = self.get_channel(bot_status_channel_id)
                if channel:
                    embed = discord.Embed(
                        description=message,
                        color=color,
                        timestamp=datetime.now()
                    )
                    await channel.send(embed=embed)
        except Exception as e:
            self.logger.error(f"Fehler beim Senden der Status-Nachricht: {e}")

    async def on_ready(self):
        """Bot ist bereit und mit Discord verbunden"""
        # Verhindere mehrfache Initialisierung (on_ready kann bei Reconnects mehrfach aufgerufen werden)
        if self._ready_initialized:
            self.logger.info("🔄 Bot reconnected")
            await self._send_status_message("🔄 **Bot Reconnected**\nVerbindung zu Discord wiederhergestellt.", 0xFFA500)
            return

        # ============================================
        # PHASE 1: CORE SERVICES
        # ============================================
        self.logger.info("=" * 60)
        self.logger.info("🚀 PHASE 1: Core Services Initialisierung")
        self.logger.info("=" * 60)

        self.logger.info(f"✅ Bot eingeloggt als {self.user}")
        self.logger.info(f"🖥️ Verbunden mit {len(self.guilds)} Server(n)")

        # Sende Startup-Message
        await self._send_status_message(
            f"🚀 **Bot gestartet - Phasenweise Initialisierung**\n"
            f"⏳ **Phase 1/5:** Core Services\n"
            f"• Eingeloggt als **{self.user}**\n"
            f"• Verbunden mit **{len(self.guilds)} Server(n)**",
            0x3498DB
        )

        # Sync Slash Commands mit Guild
        self.logger.info("🔄 Synchronisiere Slash Commands...")
        guild = discord.Object(id=self.config.guild_id)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        self.logger.info(f"✅ Slash Commands synchronisiert für Guild {self.config.guild_id}")

        self.logger.info("=" * 60)
        self.logger.info("✅ PHASE 1 abgeschlossen")
        self.logger.info("=" * 60)

        # ============================================
        # PHASE 2: AUTO-CREATE CHANNELS
        # ============================================
        if self.config.auto_remediation.get('enabled', False) and self.config.auto_remediation.get('auto_create_channels', False):
            self.logger.info("=" * 60)
            self.logger.info("🔄 PHASE 2: Channel Setup")
            self.logger.info("=" * 60)

            await self._send_status_message(
                "⏳ **Phase 2/5:** Erstelle/Prüfe Discord Channels...",
                0x3498DB
            )

            await self._setup_auto_remediation_channels()

            self.logger.info("=" * 60)
            self.logger.info("✅ PHASE 2 abgeschlossen - Alle Channels bereit")
            self.logger.info("=" * 60)

        # ============================================
        # PHASE 3: INITIALISIERE AUTO-REMEDIATION
        # ============================================
        if self.config.auto_remediation.get('enabled', False):
            self.logger.info("=" * 60)
            self.logger.info("🤖 PHASE 3: Auto-Remediation Initialisierung")
            self.logger.info("=" * 60)

            await self._send_status_message(
                "⏳ **Phase 3/5:** Initialisiere Auto-Remediation System...",
                0x3498DB
            )

            # Initialisiere Context Manager (RAG System)
            self.logger.info("🔄 [1/5] Initialisiere Context Manager (RAG)...")
            self.context_manager = ContextManager(config=self.config)
            self.context_manager.load_all_contexts()
            self.logger.info("✅ [1/5] Context Manager bereit")

            # Initialisiere AI Service mit Context Manager und Discord Logger
            self.logger.info("🔄 [2/5] Initialisiere AI Service...")
            self.ai_service = AIService(
                self.config,
                context_manager=self.context_manager,
                discord_logger=self.discord_logger
            )
            self.logger.info("✅ [2/5] AI Service bereit")

            # Initialisiere Self-Healing
            self.logger.info("🔄 [3/5] Initialisiere Self-Healing Coordinator...")
            self.self_healing = SelfHealingCoordinator(self, self.config, discord_logger=self.discord_logger)
            await self.self_healing.initialize(ai_service=self.ai_service)
            self.logger.info("✅ [3/5] Self-Healing Coordinator bereit")

            # Initialisiere Remediation Orchestrator
            self.logger.info("🔄 [4/5] Initialisiere Remediation Orchestrator...")
            self.orchestrator = RemediationOrchestrator(
                ai_service=self.ai_service,
                self_healing_coordinator=self.self_healing,
                approval_manager=self.self_healing.approval_manager,
                bot=self,
                discord_logger=self.discord_logger
            )
            self.logger.info("✅ [4/5] Remediation Orchestrator bereit")

            # Initialisiere Event Watcher
            self.logger.info("🔄 [5/5] Initialisiere Event Watcher...")
            self.event_watcher = SecurityEventWatcher(self, self.config)
            await self.event_watcher.initialize(
                trivy=self.docker,
                crowdsec=self.crowdsec,
                fail2ban=self.fail2ban,
                aide=self.aide
            )
            self.logger.info("✅ [5/5] Event Watcher bereit")

            self.logger.info("=" * 60)
            self.logger.info("✅ PHASE 3 abgeschlossen - Alle Komponenten initialisiert")
            self.logger.info("=" * 60)

            # ============================================
            # PHASE 4: STARTE AUTO-REMEDIATION (mit Delay)
            # ============================================
            self.logger.info("=" * 60)
            self.logger.info("🔄 PHASE 4: Starte Auto-Remediation Services...")
            self.logger.info("=" * 60)

            await self._send_status_message(
                "⏳ **Phase 4/5:** Starte Auto-Remediation...\n"
                "Warte 5 Sekunden bis Core Services vollständig hochgefahren sind...",
                0x3498DB
            )

            # Warte 5 Sekunden damit alle Core Services vollständig initialisiert sind
            await asyncio.sleep(5)

            self.logger.info("🚀 Starte Self-Healing Coordinator...")
            await self.self_healing.start()
            self.logger.info("✅ Self-Healing Coordinator gestartet")

            # Warte 3 Sekunden bevor Event Watcher startet
            await asyncio.sleep(3)

            self.logger.info("🚀 Starte Event Watcher...")
            await self.event_watcher.start()
            self.logger.info("✅ Event Watcher gestartet")

            self.logger.info("=" * 60)
            self.logger.info("✅ Auto-Remediation System vollständig aktiv")
            self.logger.info("=" * 60)

            await self._send_status_message(
                "✅ **Auto-Remediation System aktiv**\n"
                f"• Remediation Orchestrator: ✅ Koordination aktiv\n"
                f"• Self-Healing Coordinator: ✅ Gestartet\n"
                f"• Event Watcher: ✅ Gestartet\n"
                f"• Scan Intervals: Trivy=6h, CrowdSec/Fail2ban=60s, AIDE=15min",
                0x00FF00
            )

            # ============================================
            # PHASE 5: STARTE AI LEARNING (mit größerem Delay)
            # ============================================
            self.logger.info("=" * 60)
            self.logger.info("⏳ PHASE 5: AI Learning startet in 15 Sekunden...")
            self.logger.info("=" * 60)

            await self._send_status_message(
                "⏳ **Phase 5/5:** AI Learning startet in 15 Sekunden...\n"
                "Warte bis Monitoring & Auto-Remediation stabil laufen...",
                0x3498DB
            )

            # Warte 15 Sekunden bevor AI Learning startet
            # Damit hat Monitoring Zeit, erste Scans durchzuführen
            await asyncio.sleep(15)

            # AI Learning wird vom Event Watcher automatisch gestartet
            # Sende nur Status-Update
            self.logger.info("=" * 60)
            self.logger.info("✅ System vollständig hochgefahren - AI Learning kann starten")
            self.logger.info("=" * 60)

            await self._send_status_message(
                "✅ **AI Learning bereit**\n"
                "• Code Analyzer: Bereit für Vulnerability Scans\n"
                "• Git History Learner: Bereit für Pattern Learning\n"
                "• Knowledge Base: Aktiv",
                0x00FF00
            )
        else:
            self.logger.info("ℹ️ Auto-Remediation deaktiviert (config: auto_remediation.enabled=false)")

        # Starte Background Tasks
        # DISABLED: Old monitor_security replaced by Event Watcher System
        # Event Watcher now handles all alerts + auto-remediation with persistence
        # if not self.monitor_security.is_running():
        #     self.monitor_security.start()
        if not self.daily_health_check.is_running():
            self.daily_health_check.start()

        # Setze Status
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name=self.config.bot_status
            )
        )

        # Markiere als initialisiert
        self._ready_initialized = True
        self.logger.info("🚀 ShadowOps Bot vollständig einsatzbereit!")

        # Finale Status-Nachricht
        await self._send_status_message(
            "🚀 **ShadowOps Bot vollständig einsatzbereit!**\n"
            f"• Event Watcher: ✅ Läuft (mit Persistenz)\n"
            f"• Auto-Remediation: ✅ Aktiv (AI-powered)\n"
            f"• Daily Health-Check: ✅ Geplant (06:00 Uhr)\n\n"
            f"*Alle Systeme bereit für Security Monitoring*",
            0x2ECC71
        )

    async def on_guild_join(self, guild: discord.Guild):
        """Bot wurde zu Server hinzugefügt"""
        self.logger.info(f"➕ Bot zu Server hinzugefügt: {guild.name} ({guild.id})")

    async def on_error(self, event: str, *args, **kwargs):
        """Error Handler"""
        self.logger.error(f"❌ Fehler in Event {event}", exc_info=True)

    def is_rate_limited(self, alert_key: str, limit_seconds: Optional[int] = None) -> bool:
        """Prüft ob Alert rate-limited ist"""
        now = datetime.now()
        limit = limit_seconds if limit_seconds else self.config.rate_limit_seconds

        if alert_key in self.recent_alerts:
            last_time = self.recent_alerts[alert_key]
            if (now - last_time).seconds < limit:
                return True
        self.recent_alerts[alert_key] = now
        return False

    async def send_alert(self, channel_id: int, embed: discord.Embed, mention_role: Optional[int] = None):
        """Sendet Alert in einen Channel"""
        try:
            channel = self.get_channel(channel_id)
            if not channel:
                self.logger.warning(f"⚠️ Channel {channel_id} nicht gefunden")
                return

            content = f"<@&{mention_role}>" if mention_role else None

            await channel.send(content=content, embed=embed)
            self.logger.info(f"✉️ Alert gesendet an Channel {channel_id}")

        except discord.Forbidden:
            self.logger.error(f"❌ Keine Berechtigung für Channel {channel_id}")
        except Exception as e:
            self.logger.error(f"❌ Fehler beim Senden: {e}")

    @tasks.loop(seconds=30)
    async def monitor_security(self):
        """Background Task - Monitort Security-Tools alle 30 Sekunden"""
        try:
            await self.monitor_fail2ban()
            await self.monitor_crowdsec()
            await self.monitor_docker()
            await self.monitor_aide()

        except Exception as e:
            self.logger.error(f"❌ Fehler im Security Monitor: {e}", exc_info=True)

    @monitor_security.before_loop
    async def before_monitor(self):
        """Warte bis Bot bereit ist"""
        await self.wait_until_ready()
        self.logger.info("🔍 Security Monitor gestartet")

    @tasks.loop(time=time(hour=6, minute=0))
    async def daily_health_check(self):
        """Daily Health-Check um 06:00 Uhr - zeigt Status aller Systeme"""
        try:
            self.logger.info("📊 Führe Daily Health-Check durch...")

            # Prüfe alle Systeme
            fail2ban_ok = True
            fail2ban_bans_today = 0
            try:
                stats = self.fail2ban.get_jail_stats()
                fail2ban_bans_today = sum(s.get('currently_banned', 0) for s in stats.values())
            except:
                fail2ban_ok = False

            crowdsec_ok = True
            crowdsec_decisions = 0
            try:
                crowdsec_ok = self.crowdsec.is_running()
                decisions = self.crowdsec.get_active_decisions(limit=100)
                crowdsec_decisions = len(decisions)
            except:
                crowdsec_ok = False

            docker_ok = True
            docker_last_scan = None
            docker_vulnerabilities = 0
            try:
                results = self.docker.get_latest_scan_results()
                if results:
                    docker_last_scan = results.get('date', 'Unbekannt')
                    docker_vulnerabilities = results.get('critical', 0)
            except:
                docker_ok = False

            aide_ok = True
            aide_last_check = None
            try:
                aide_ok = self.aide.is_timer_active()
                aide_last_check = self.aide.get_last_check_date()
            except:
                aide_ok = False

            # Erstelle Health-Check Report
            embed = EmbedBuilder.health_check_report(
                fail2ban_ok=fail2ban_ok,
                fail2ban_bans_today=fail2ban_bans_today,
                crowdsec_ok=crowdsec_ok,
                crowdsec_decisions=crowdsec_decisions,
                docker_ok=docker_ok,
                docker_last_scan=docker_last_scan,
                docker_vulnerabilities=docker_vulnerabilities,
                aide_ok=aide_ok,
                aide_last_check=aide_last_check
            )

            # Sende Report - nutze Critical Channel bei Fehlern, sonst Security Channel
            if not (fail2ban_ok and crowdsec_ok and docker_ok and aide_ok):
                channel_id = self.config.get_channel_for_alert('critical')
                await self.send_alert(channel_id, embed, self.config.mention_role_critical)
            else:
                channel_id = self.config.get_channel_for_alert('sicherheitsdienst')
                await self.send_alert(channel_id, embed)

            self.logger.info("✅ Daily Health-Check abgeschlossen")

        except Exception as e:
            self.logger.error(f"❌ Fehler beim Daily Health-Check: {e}", exc_info=True)

    @daily_health_check.before_loop
    async def before_health_check(self):
        """Warte bis Bot bereit ist"""
        await self.wait_until_ready()
        self.logger.info("⏰ Daily Health-Check Task gestartet (läuft täglich um 06:00 Uhr)")

    async def monitor_fail2ban(self):
        """Monitort Fail2ban für neue Bans"""
        try:
            new_bans = self.fail2ban.get_new_bans()

            for ban in new_bans:
                ip = ban["ip"]
                jail = ban["jail"]

                # Rate Limiting: Nur 10 Sekunden pro IP+Jail (verhindert Duplikate, erlaubt Live-Tracking)
                alert_key = f"fail2ban_{ip}_{jail}"
                if self.is_rate_limited(alert_key, limit_seconds=10):
                    continue

                # Erstelle Embed
                embed = EmbedBuilder.fail2ban_ban(ip, jail)

                # Sende Alert
                channel_id = self.config.get_channel_for_alert('fail2ban')
                await self.send_alert(channel_id, embed, self.config.mention_role_high)

                self.logger.info(f"🚫 Fail2ban Ban: {ip} (Jail: {jail})")

        except Exception as e:
            # Error-Alert nur alle 30 Minuten senden (verhindert Spam bei anhaltendem Problem)
            error_key = "fail2ban_error"
            if not self.is_rate_limited(error_key, limit_seconds=1800):
                error_embed = EmbedBuilder.error_alert(
                    "Fail2ban Monitoring",
                    f"Fehler beim Lesen der Fail2ban Logs: {str(e)}"
                )
                critical_channel = self.config.get_channel_for_alert('critical')
                await self.send_alert(critical_channel, error_embed, self.config.mention_role_critical)
                self.logger.error(f"❌ Fail2ban Monitoring Error: {e}", exc_info=True)

    async def monitor_crowdsec(self):
        """Monitort CrowdSec für neue Threats"""
        try:
            # Hole neueste Alerts
            alerts = self.crowdsec.get_recent_alerts(limit=10)

            if not alerts:
                return

            # Prüfe jeden Alert
            for alert in alerts:
                alert_id = alert.get('id', '')
                source_ip = alert.get('source_ip', 'Unknown')
                scenario = alert.get('scenario', 'Unknown')
                country = alert.get('source_country', '')

                # Rate Limiting pro Alert-ID: 5 Minuten (erlaubt Live-Tracking verschiedener Threats)
                alert_key = f"crowdsec_{alert_id}"
                if self.is_rate_limited(alert_key, limit_seconds=300):  # 5 Minuten
                    continue

                # Prüfe ob Scenario kritisch ist (AI-basierte oder kritische Szenarien)
                is_critical = any(keyword in scenario.lower() for keyword in [
                    'exploit', 'vulnerability', 'cve', 'attack', 'injection',
                    'bruteforce', 'scan', 'probe', 'dos', 'ddos'
                ])

                if is_critical:
                    # Erstelle Embed
                    embed = EmbedBuilder.crowdsec_alert(source_ip, scenario, country)

                    # Sende zu Critical Channel
                    critical_channel_id = self.config.get_channel_for_alert('critical')
                    await self.send_alert(critical_channel_id, embed, self.config.mention_role_critical)

                    self.logger.info(f"🛡️ CrowdSec Alert: {source_ip} ({scenario})")

        except Exception as e:
            # Error-Alert nur alle 30 Minuten
            error_key = "crowdsec_error"
            if not self.is_rate_limited(error_key, limit_seconds=1800):
                error_embed = EmbedBuilder.error_alert(
                    "CrowdSec Monitoring",
                    f"Fehler beim Abrufen von CrowdSec Alerts: {str(e)}"
                )
                critical_channel = self.config.get_channel_for_alert('critical')
                await self.send_alert(critical_channel, error_embed, self.config.mention_role_critical)
                self.logger.error(f"❌ CrowdSec Monitoring Error: {e}", exc_info=True)

    async def monitor_docker(self):
        """Monitort Docker Security Scans für neue Ergebnisse"""
        try:
            # Hole neueste Scan-Ergebnisse
            results = self.docker.get_latest_scan_results()

            if not results:
                return

            # Rate Limiting - nur alle 5 Minuten für denselben Scan
            alert_key = f"docker_scan_{results.get('date', '')}"
            if self.is_rate_limited(alert_key, limit_seconds=300):  # 5 Minuten
                return

            critical = results.get('critical', 0)
            high = results.get('high', 0)

            # Nur Alert senden wenn CRITICAL oder HIGH gefunden
            if critical > 0 or high > 0:
                # Erstelle Embed
                embed = EmbedBuilder.docker_scan_result(
                    total_images=results.get('images', 0),
                    critical=critical,
                    high=high,
                    medium=results.get('medium', 0),
                    low=results.get('low', 0)
                )

                # Sende zu Docker Channel
                docker_channel_id = self.config.get_channel_for_alert('docker')
                await self.send_alert(docker_channel_id, embed, self.config.mention_role_critical if critical > 0 else None)

                # Wenn CRITICAL: auch zu Critical Channel
                if critical > 0:
                    critical_channel_id = self.config.get_channel_for_alert('critical')
                    if critical_channel_id != docker_channel_id:
                        await self.send_alert(critical_channel_id, embed, self.config.mention_role_critical)

                self.logger.info(f"🐳 Docker Scan Alert: {critical} CRITICAL, {high} HIGH")

        except Exception as e:
            # Error-Alert nur alle 30 Minuten
            error_key = "docker_error"
            if not self.is_rate_limited(error_key, limit_seconds=1800):
                error_embed = EmbedBuilder.error_alert(
                    "Docker Scan Monitoring",
                    f"Fehler beim Lesen der Docker Scan-Ergebnisse: {str(e)}"
                )
                critical_channel = self.config.get_channel_for_alert('critical')
                await self.send_alert(critical_channel, error_embed, self.config.mention_role_critical)
                self.logger.error(f"❌ Docker Monitoring Error: {e}", exc_info=True)

    async def monitor_aide(self):
        """Monitort AIDE File Integrity Checks"""
        try:
            # Hole letzte Check-Ergebnisse
            results = self.aide.get_last_check_results()

            if not results:
                return

            timestamp = results.get('timestamp', '')
            files_changed = results.get('files_changed', 0)
            files_added = results.get('files_added', 0)
            files_removed = results.get('files_removed', 0)

            # Rate Limiting - nur 1 Stunde für denselben Check (erlaubt schnellere Updates)
            alert_key = f"aide_check_{timestamp}"
            if self.is_rate_limited(alert_key, limit_seconds=3600):  # 1 Stunde
                return

            # Alert nur bei Änderungen
            total_changes = files_changed + files_added + files_removed
            if total_changes > 0:
                # Erstelle Embed
                embed = EmbedBuilder.aide_check(
                    files_changed=files_changed,
                    files_added=files_added,
                    files_removed=files_removed
                )

                # Sende zu Critical Channel (File Integrity ist kritisch!)
                critical_channel_id = self.config.get_channel_for_alert('critical')
                await self.send_alert(critical_channel_id, embed, self.config.mention_role_critical)

                self.logger.info(f"🔒 AIDE Alert: {total_changes} Datei-Änderungen erkannt")

        except Exception as e:
            # Error-Alert nur alle 30 Minuten
            error_key = "aide_error"
            if not self.is_rate_limited(error_key, limit_seconds=1800):
                error_embed = EmbedBuilder.error_alert(
                    "AIDE File Integrity Monitoring",
                    f"Fehler beim Lesen der AIDE Check-Ergebnisse: {str(e)}"
                )
                critical_channel = self.config.get_channel_for_alert('critical')
                await self.send_alert(critical_channel, error_embed, self.config.mention_role_critical)
                self.logger.error(f"❌ AIDE Monitoring Error: {e}", exc_info=True)


# ========================
# SLASH COMMANDS
# ========================

bot = ShadowOpsBot()


@bot.tree.command(name="status", description="Zeige Security-Status-Übersicht")
async def status_command(interaction: discord.Interaction):
    """Slash Command: /status"""
    await interaction.response.defer()

    try:
        # Fail2ban Status
        jail_stats = bot.fail2ban.get_jail_stats()
        total_bans = sum(s["currently_banned"] for s in jail_stats.values())

        # CrowdSec Status
        cs_active = bot.crowdsec.is_running()
        cs_metrics = bot.crowdsec.get_metrics()

        # Docker Scans
        docker_scan = bot.docker.get_scan_date()

        # AIDE
        aide_check = bot.aide.get_last_check_date()

        # Erstelle Status-Embed
        embed = EmbedBuilder.status_overview(
            fail2ban_active=len(jail_stats) > 0,
            fail2ban_bans=total_bans,
            crowdsec_active=cs_active,
            crowdsec_alerts=cs_metrics.get("alerts_total", 0),
            docker_last_scan=docker_scan,
            aide_last_check=aide_check
        )

        await interaction.followup.send(embed=embed)

    except Exception as e:
        bot.logger.error(f"❌ Fehler in /status: {e}", exc_info=True)
        await interaction.followup.send("❌ Fehler beim Abrufen des Status", ephemeral=True)


@bot.tree.command(name="scan", description="Trigger manuellen Docker Security Scan")
@app_commands.checks.has_permissions(administrator=True)
async def scan_command(interaction: discord.Interaction):
    """Slash Command: /scan"""
    await interaction.response.defer()

    try:
        success = bot.docker.trigger_scan()

        if success:
            embed = discord.Embed(
                title="🐳 Docker Security Scan gestartet",
                description="Der Scan läuft im Hintergrund und dauert einige Minuten.\nErgebnisse werden automatisch gepostet.",
                color=0x3498DB
            )
            await interaction.followup.send(embed=embed)
            bot.logger.info(f"🔍 Docker Scan manuell getriggert von {interaction.user}")
        else:
            await interaction.followup.send("❌ Scan konnte nicht gestartet werden", ephemeral=True)

    except Exception as e:
        bot.logger.error(f"❌ Fehler in /scan: {e}", exc_info=True)
        await interaction.followup.send("❌ Fehler beim Starten des Scans", ephemeral=True)


@bot.tree.command(name="bans", description="Zeige aktuell gebannte IPs")
@app_commands.describe(limit="Maximale Anzahl (Standard: 10)")
async def bans_command(interaction: discord.Interaction, limit: int = 10):
    """Slash Command: /bans"""
    await interaction.response.defer()

    try:
        # Fail2ban Bans
        f2b_bans = bot.fail2ban.get_banned_ips()

        # CrowdSec Decisions
        cs_decisions = bot.crowdsec.get_active_decisions(limit=limit)

        # Erstelle Embed
        embed = discord.Embed(
            title="🚫 Aktuell gebannte IP-Adressen",
            description=f"Zeige bis zu {limit} gebannte IPs",
            color=0xE74C3C,
            timestamp=datetime.utcnow()
        )

        # Fail2ban Bans
        if f2b_bans:
            f2b_text = ""
            for jail, ips in list(f2b_bans.items())[:5]:
                f2b_text += f"**{jail}:** {len(ips)} IPs\n"
                f2b_text += "```\n" + "\n".join(ips[:3]) + "\n```\n"
            embed.add_field(name="🛡️ Fail2ban", value=f2b_text or "Keine Bans", inline=False)

        # CrowdSec Decisions
        if cs_decisions:
            cs_text = ""
            for dec in cs_decisions[:5]:
                cs_text += f"`{dec['ip']}` - {dec['reason'][:50]}\n"
            embed.add_field(name="🤖 CrowdSec", value=cs_text, inline=False)

        embed.set_footer(text=f"Angefordert von {interaction.user}")

        await interaction.followup.send(embed=embed)

    except Exception as e:
        bot.logger.error(f"❌ Fehler in /bans: {e}", exc_info=True)
        await interaction.followup.send("❌ Fehler beim Abrufen der Bans", ephemeral=True)


@bot.tree.command(name="threats", description="Zeige letzte erkannte Bedrohungen")
@app_commands.describe(hours="Zeitraum in Stunden (Standard: 24)")
async def threats_command(interaction: discord.Interaction, hours: int = 24):
    """Slash Command: /threats"""
    await interaction.response.defer()

    try:
        # CrowdSec Alerts
        alerts = bot.crowdsec.get_recent_alerts(limit=20)

        embed = discord.Embed(
            title=f"⚠️ Bedrohungen der letzten {hours}h",
            description=f"Zeige neueste CrowdSec Alerts",
            color=0xE67E22,
            timestamp=datetime.utcnow()
        )

        if alerts:
            for alert in alerts[:10]:
                scenario = alert.get("scenario", "Unknown")
                ip = alert.get("source_ip", "Unknown")
                country = alert.get("source_country", "")
                events = alert.get("events_count", "0")

                flag = f":flag_{country.lower()}:" if country else ""

                embed.add_field(
                    name=f"{flag} {scenario}",
                    value=f"IP: `{ip}` | Events: {events}",
                    inline=False
                )
        else:
            embed.description = "✅ Keine Bedrohungen im angegebenen Zeitraum"

        embed.set_footer(text=f"Angefordert von {interaction.user}")

        await interaction.followup.send(embed=embed)

    except Exception as e:
        bot.logger.error(f"❌ Fehler in /threats: {e}", exc_info=True)
        await interaction.followup.send("❌ Fehler beim Abrufen der Bedrohungen", ephemeral=True)


@bot.tree.command(name="docker", description="Zeige letzte Docker Scan Ergebnisse")
async def docker_command(interaction: discord.Interaction):
    """Slash Command: /docker"""
    await interaction.response.defer()

    try:
        results = bot.docker.get_latest_scan_results()

        if not results:
            await interaction.followup.send("⚠️ Noch kein Scan durchgeführt", ephemeral=True)
            return

        embed = EmbedBuilder.docker_scan_result(
            total_images=results.get("images", 0),
            critical=results.get("critical", 0),
            high=results.get("high", 0),
            medium=results.get("medium", 0),
            low=results.get("low", 0)
        )

        embed.add_field(
            name="📅 Letzter Scan",
            value=results.get("date", "Unbekannt"),
            inline=False
        )

        await interaction.followup.send(embed=embed)

    except Exception as e:
        bot.logger.error(f"❌ Fehler in /docker: {e}", exc_info=True)
        await interaction.followup.send("❌ Fehler beim Abrufen der Scan-Ergebnisse", ephemeral=True)


@bot.tree.command(name="aide", description="Zeige AIDE Integrity Check Status")
async def aide_command(interaction: discord.Interaction):
    """Slash Command: /aide"""
    await interaction.response.defer()

    try:
        results = bot.aide.get_last_check_results()
        last_check = bot.aide.get_last_check_date()

        if not results:
            await interaction.followup.send("⚠️ Noch kein AIDE Check durchgeführt", ephemeral=True)
            return

        embed = EmbedBuilder.aide_check(
            files_changed=results.get("files_changed", 0),
            files_added=results.get("files_added", 0),
            files_removed=results.get("files_removed", 0)
        )

        if last_check:
            embed.add_field(name="📅 Letzter Check", value=last_check, inline=False)

        await interaction.followup.send(embed=embed)

    except Exception as e:
        bot.logger.error(f"❌ Fehler in /aide: {e}", exc_info=True)
        await interaction.followup.send("❌ Fehler beim Abrufen des AIDE Status", ephemeral=True)


# ========================
# AUTO-REMEDIATION COMMANDS
# ========================

@bot.tree.command(name="stop-all-fixes", description="🛑 EMERGENCY: Stoppt alle laufenden Auto-Fixes sofort")
@app_commands.describe()
async def stop_all_fixes(interaction: discord.Interaction):
    """Emergency stop für Auto-Remediation"""
    try:
        await interaction.response.defer(ephemeral=True)

        if not bot.self_healing:
            await interaction.followup.send("ℹ️ Auto-Remediation ist nicht aktiv", ephemeral=True)
            return

        # Stoppe alle Jobs
        stopped_count = await bot.self_healing.stop_all_jobs()

        # Stoppe Event Watcher temporär (kann mit Bot-Neustart reaktiviert werden)
        if bot.event_watcher:
            await bot.event_watcher.stop()

        bot.logger.warning(f"🛑 EMERGENCY STOP ausgeführt von {interaction.user} - {stopped_count} Jobs gestoppt")

        embed = discord.Embed(
            title="🛑 Emergency Stop Executed",
            description=f"Alle Auto-Remediation Prozesse wurden gestoppt.",
            color=discord.Color.red()
        )
        embed.add_field(name="👤 Ausgeführt von", value=interaction.user.mention, inline=True)
        embed.add_field(name="📊 Gestoppte Jobs", value=str(stopped_count), inline=True)
        embed.add_field(
            name="🔄 Reaktivierung",
            value="Bot-Neustart erforderlich: `sudo systemctl restart shadowops-bot`",
            inline=False
        )
        embed.timestamp = datetime.now()

        await interaction.followup.send(embed=embed, ephemeral=True)

        # Sende auch Public Notification
        if hasattr(bot.config, 'auto_remediation_alerts_channel'):
            channel = bot.get_channel(bot.config.auto_remediation_alerts_channel)
            if channel:
                await channel.send(embed=embed)

    except Exception as e:
        bot.logger.error(f"❌ Fehler in /stop-all-fixes: {e}", exc_info=True)
        await interaction.followup.send("❌ Fehler beim Stoppen der Auto-Fixes", ephemeral=True)


@bot.tree.command(name="remediation-stats", description="📊 Zeigt Auto-Remediation Statistiken an")
@app_commands.describe()
async def remediation_stats(interaction: discord.Interaction):
    """Zeigt Auto-Remediation Statistiken"""
    try:
        await interaction.response.defer(ephemeral=False)

        if not bot.self_healing or not bot.event_watcher:
            await interaction.followup.send("ℹ️ Auto-Remediation ist nicht aktiv", ephemeral=True)
            return

        # Hole Statistiken
        healing_stats = bot.self_healing.get_statistics()
        watcher_stats = bot.event_watcher.get_statistics()

        # Event Watcher Stats
        embed = discord.Embed(
            title="📊 Auto-Remediation Statistics",
            description="Aktuelle Statistiken des Event-Driven Auto-Remediation Systems",
            color=discord.Color.blue()
        )

        # Event Watcher
        embed.add_field(
            name="🔍 Event Watcher",
            value=f"Status: {'🟢 Running' if watcher_stats['running'] else '🔴 Stopped'}\n"
                  f"Total Scans: {watcher_stats['total_scans']}\n"
                  f"Total Events: {watcher_stats['total_events']}\n"
                  f"Events in History: {watcher_stats['events_in_history']}",
            inline=False
        )

        # Self-Healing Stats
        success_rate = 0
        if healing_stats['successful'] + healing_stats['failed'] > 0:
            success_rate = (healing_stats['successful'] / (healing_stats['successful'] + healing_stats['failed'])) * 100

        embed.add_field(
            name="🔧 Self-Healing Coordinator",
            value=f"Total Jobs: {healing_stats['total_jobs']}\n"
                  f"✅ Successful: {healing_stats['successful']}\n"
                  f"❌ Failed: {healing_stats['failed']}\n"
                  f"✋ Requires Approval: {healing_stats['requires_approval']}\n"
                  f"📈 Success Rate: {success_rate:.1f}%\n"
                  f"🔄 Avg Attempts: {healing_stats['avg_attempts_per_job']:.1f}",
            inline=False
        )

        # Queue Status
        embed.add_field(
            name="📋 Queue Status",
            value=f"Pending: {healing_stats['pending_jobs']}\n"
                  f"Active: {healing_stats['active_jobs']}\n"
                  f"Completed: {healing_stats['completed_jobs']}",
            inline=True
        )

        # Circuit Breaker
        cb_status = healing_stats['circuit_breaker']
        cb_emoji = {'CLOSED': '🟢', 'OPEN': '🔴', 'HALF_OPEN': '🟡'}.get(cb_status['state'], '⚪')

        embed.add_field(
            name="⚡ Circuit Breaker",
            value=f"{cb_emoji} {cb_status['state']}\n"
                  f"Failures: {cb_status['failure_count']}",
            inline=True
        )

        # Approval Mode
        embed.add_field(
            name="🎯 Approval Mode",
            value=healing_stats['approval_mode'].upper(),
            inline=True
        )

        # Scan Intervals
        intervals = watcher_stats['intervals']
        embed.add_field(
            name="⏱️ Scan Intervals",
            value=f"Trivy: {intervals['trivy']}s\n"
                  f"CrowdSec: {intervals['crowdsec']}s\n"
                  f"Fail2ban: {intervals['fail2ban']}s\n"
                  f"AIDE: {intervals['aide']}s",
            inline=False
        )

        embed.timestamp = datetime.now()
        embed.set_footer(text="Auto-Remediation System")

        await interaction.followup.send(embed=embed)

    except Exception as e:
        bot.logger.error(f"❌ Fehler in /remediation-stats: {e}", exc_info=True)
        await interaction.followup.send("❌ Fehler beim Abrufen der Statistiken", ephemeral=True)


@bot.tree.command(name="set-approval-mode", description="⚙️ Ändere Auto-Remediation Approval Mode")
@app_commands.describe(mode="paranoid (Frage immer) | auto (Nur bei CRITICAL) | dry-run (Nur Logs)")
@app_commands.checks.has_permissions(administrator=True)
async def set_approval_mode_command(interaction: discord.Interaction, mode: str):
    """
    Ändert den Approval Mode für Auto-Remediation

    Modes:
    - paranoid: Frage bei JEDEM Event (höchste Sicherheit, default)
    - auto: Nur bei CRITICAL fragen, andere automatisch
    - dry-run: Keine Execution, nur Logs (Test-Modus)
    """
    try:
        await interaction.response.defer(ephemeral=False)

        # Validate mode
        valid_modes = ['paranoid', 'auto', 'dry-run']
        if mode not in valid_modes:
            await interaction.followup.send(
                f"❌ Ungültiger Modus: `{mode}`\n"
                f"Erlaubte Modi: `{'`, `'.join(valid_modes)}`",
                ephemeral=True
            )
            return

        # Update config in memory
        bot.config.auto_remediation['approval_mode'] = mode

        # Create response embed
        embed = discord.Embed(
            title="⚙️ Approval Mode geändert",
            color=0x00FF00,
            timestamp=datetime.now()
        )

        mode_descriptions = {
            'paranoid': '🔒 Paranoid - Frage bei JEDEM Event (höchste Sicherheit)',
            'auto': '⚡ Auto - Nur bei CRITICAL fragen, andere automatisch',
            'dry-run': '🧪 Dry-Run - Keine Execution, nur Logs (Test-Modus)'
        }

        embed.add_field(
            name="Neuer Modus",
            value=mode_descriptions[mode],
            inline=False
        )

        embed.add_field(
            name="⚠️ Hinweis",
            value="Änderung gilt ab sofort für neue Events.\n"
                  "Config-File wird nicht automatisch gespeichert.",
            inline=False
        )

        embed.set_footer(text=f"Geändert von {interaction.user.name}")

        bot.logger.info(f"✅ Approval Mode geändert: {mode} (von {interaction.user.name})")

        await interaction.followup.send(embed=embed)

    except Exception as e:
        bot.logger.error(f"❌ Fehler in /set-approval-mode: {e}", exc_info=True)
        await interaction.followup.send("❌ Fehler beim Ändern des Approval Mode", ephemeral=True)


@bot.tree.command(name="get-ai-stats", description="🤖 Zeige AI-Provider Status und Statistiken")
async def get_ai_stats_command(interaction: discord.Interaction):
    """Zeigt AI-Provider Status und Performance-Statistiken"""
    try:
        await interaction.response.defer(ephemeral=False)

        # Create embed
        embed = discord.Embed(
            title="🤖 AI Provider Status",
            description="Übersicht über alle konfigurierten AI-Provider",
            color=0x5865F2,
            timestamp=datetime.now()
        )

        # Check Ollama status
        ollama_enabled = bot.ai_service.ollama_enabled
        ollama_status = "🟢 Enabled" if ollama_enabled else "🔴 Disabled"
        ollama_info = (
            f"Status: {ollama_status}\n"
            f"URL: `{bot.ai_service.ollama_url}`\n"
            f"Model: `{bot.ai_service.ollama_model}`\n"
            f"Critical: `{bot.ai_service.ollama_model_critical}`"
        )
        if bot.ai_service.use_hybrid_models:
            ollama_info += "\n⚡ Hybrid Mode: Enabled"

        embed.add_field(
            name="🦙 Ollama (Local)",
            value=ollama_info,
            inline=False
        )

        # Check Claude status
        claude_enabled = bot.ai_service.anthropic_enabled
        claude_status = "🟢 Enabled" if claude_enabled else "🔴 Disabled"
        claude_info = (
            f"Status: {claude_status}\n"
            f"Model: `{bot.ai_service.anthropic_model}`\n"
            f"API Key: {'✅ Configured' if bot.ai_service.anthropic_api_key else '❌ Missing'}"
        )

        embed.add_field(
            name="🧠 Claude (Anthropic)",
            value=claude_info,
            inline=False
        )

        # Check OpenAI status
        openai_enabled = bot.ai_service.openai_enabled
        openai_status = "🟢 Enabled" if openai_enabled else "🔴 Disabled"
        openai_info = (
            f"Status: {openai_status}\n"
            f"Model: `{bot.ai_service.openai_model}`\n"
            f"API Key: {'✅ Configured' if bot.ai_service.openai_api_key else '❌ Missing'}"
        )

        embed.add_field(
            name="🤖 OpenAI (GPT)",
            value=openai_info,
            inline=False
        )

        # Rate limiting info
        rate_limit_info = (
            f"Request Delay: `{bot.ai_service.request_delay}s`\n"
            f"Last Request: `{bot.ai_service.last_request_time:.1f}` (timestamp)"
        )

        embed.add_field(
            name="⏱️ Rate Limiting",
            value=rate_limit_info,
            inline=False
        )

        # Fallback chain
        fallback_chain = []
        if ollama_enabled:
            fallback_chain.append("Ollama")
        if claude_enabled:
            fallback_chain.append("Claude")
        if openai_enabled:
            fallback_chain.append("OpenAI")

        if fallback_chain:
            embed.add_field(
                name="🔄 Fallback Chain",
                value=" → ".join(fallback_chain),
                inline=False
            )

        embed.set_footer(text="AI Service Status")

        await interaction.followup.send(embed=embed)

    except Exception as e:
        bot.logger.error(f"❌ Fehler in /get-ai-stats: {e}", exc_info=True)
        await interaction.followup.send("❌ Fehler beim Abrufen der AI-Statistiken", ephemeral=True)


@bot.tree.command(name="reload-context", description="🔄 Lade Project-Context neu")
@app_commands.checks.has_permissions(administrator=True)
async def reload_context_command(interaction: discord.Interaction):
    """Lädt alle Context-Files neu"""
    try:
        await interaction.response.defer(ephemeral=False)

        # Reload context
        if hasattr(bot, 'context_manager') and bot.context_manager:
            # Context Manager is initialized
            project_count = len(bot.context_manager.project_paths) if hasattr(bot.context_manager, 'project_paths') else 0

            # Get DO-NOT-TOUCH rules if available
            try:
                do_not_touch = bot.context_manager.get_do_not_touch_list()
                do_not_touch_count = len(do_not_touch)
            except:
                do_not_touch_count = 0

            embed = discord.Embed(
                title="🔄 Context Reloaded",
                description="Project-Context wurde erfolgreich neu geladen",
                color=0x00FF00,
                timestamp=datetime.now()
            )

            embed.add_field(
                name="📁 Projects",
                value=f"{project_count} Projekte geladen",
                inline=True
            )

            embed.add_field(
                name="🚫 DO-NOT-TOUCH Rules",
                value=f"{do_not_touch_count} Regeln aktiv",
                inline=True
            )

            embed.add_field(
                name="🏗️ Infrastructure Context",
                value="✅ Geladen",
                inline=True
            )

            embed.set_footer(text=f"Neu geladen von {interaction.user.name}")

            bot.logger.info(f"✅ Context neu geladen (von {interaction.user.name})")

            await interaction.followup.send(embed=embed)

        else:
            # Context Manager not initialized
            await interaction.followup.send(
                "⚠️ Context Manager nicht initialisiert",
                ephemeral=True
            )

    except Exception as e:
        bot.logger.error(f"❌ Fehler in /reload-context: {e}", exc_info=True)
        await interaction.followup.send("❌ Fehler beim Neu-Laden des Context", ephemeral=True)


@bot.tree.command(name="projekt-status", description="📊 Zeige Status für ein bestimmtes Projekt")
@app_commands.describe(name="Name des Projekts (z.B. shadowops-bot, guildscout)")
async def projekt_status_command(interaction: discord.Interaction, name: str):
    """Zeigt detaillierten Status für ein spezifisches Projekt"""
    try:
        await interaction.response.defer(ephemeral=False)

        # Check if project monitor is available
        if not hasattr(bot, 'project_monitor') or not bot.project_monitor:
            await interaction.followup.send(
                "⚠️ Project Monitor nicht verfügbar",
                ephemeral=True
            )
            return

        # Get project status
        status = bot.project_monitor.get_project_status(name)

        if not status:
            await interaction.followup.send(
                f"❌ Projekt '{name}' nicht gefunden.\n"
                f"Verwende `/alle-projekte` um alle überwachten Projekte zu sehen.",
                ephemeral=True
            )
            return

        # Create detailed status embed
        is_online = status['is_online']
        status_emoji = "🟢" if is_online else "🔴"
        status_text = "Online" if is_online else "Offline"
        color = discord.Color.green() if is_online else discord.Color.red()

        embed = discord.Embed(
            title=f"{status_emoji} {status['name']} - Status",
            description=f"Aktueller Status: **{status_text}**",
            color=color,
            timestamp=datetime.now()
        )

        # Status
        embed.add_field(
            name="🔌 Status",
            value=f"{status_emoji} {status_text}",
            inline=True
        )

        # Uptime
        embed.add_field(
            name="📈 Uptime",
            value=f"{status['uptime_percentage']:.2f}%",
            inline=True
        )

        # Response Time
        if is_online:
            embed.add_field(
                name="⚡ Avg Response",
                value=f"{status['average_response_time_ms']:.0f}ms",
                inline=True
            )
        else:
            embed.add_field(
                name="⚡ Response",
                value="N/A",
                inline=True
            )

        # Health Checks
        embed.add_field(
            name="🔍 Total Checks",
            value=str(status['total_checks']),
            inline=True
        )

        embed.add_field(
            name="✅ Successful",
            value=str(status['successful_checks']),
            inline=True
        )

        embed.add_field(
            name="❌ Failed",
            value=str(status['failed_checks']),
            inline=True
        )

        # Last Check Time
        if status['last_check_time']:
            last_check = datetime.fromisoformat(status['last_check_time'])
            time_ago = datetime.utcnow() - last_check
            minutes_ago = int(time_ago.total_seconds() / 60)
            embed.add_field(
                name="🕐 Last Check",
                value=f"{minutes_ago}m ago",
                inline=True
            )

        # Downtime Info (if offline)
        if not is_online:
            if status['current_downtime_minutes']:
                embed.add_field(
                    name="⏱️ Current Downtime",
                    value=f"{status['current_downtime_minutes']} minutes",
                    inline=True
                )

            if status['consecutive_failures']:
                embed.add_field(
                    name="🔁 Consecutive Failures",
                    value=str(status['consecutive_failures']),
                    inline=True
                )

            if status['last_error']:
                error = status['last_error']
                if len(error) > 200:
                    error = error[:197] + "..."
                embed.add_field(
                    name="⚠️ Last Error",
                    value=f"```{error}```",
                    inline=False
                )

        embed.set_footer(text=f"Angefragt von {interaction.user.name}")

        await interaction.followup.send(embed=embed)

        bot.logger.info(f"📊 /projekt-status {name} von {interaction.user.name}")

    except Exception as e:
        bot.logger.error(f"❌ Fehler in /projekt-status: {e}", exc_info=True)
        await interaction.followup.send("❌ Fehler beim Abrufen des Projekt-Status", ephemeral=True)


@bot.tree.command(name="alle-projekte", description="📋 Zeige Übersicht aller überwachten Projekte")
async def alle_projekte_command(interaction: discord.Interaction):
    """Zeigt Status-Übersicht für alle Projekte"""
    try:
        await interaction.response.defer(ephemeral=False)

        # Check if project monitor is available
        if not hasattr(bot, 'project_monitor') or not bot.project_monitor:
            await interaction.followup.send(
                "⚠️ Project Monitor nicht verfügbar",
                ephemeral=True
            )
            return

        # Get all project statuses
        all_statuses = bot.project_monitor.get_all_projects_status()

        if not all_statuses:
            await interaction.followup.send(
                "ℹ️ Keine Projekte werden derzeit überwacht",
                ephemeral=True
            )
            return

        # Count online/offline
        online_count = sum(1 for s in all_statuses if s['is_online'])
        total_count = len(all_statuses)
        offline_count = total_count - online_count

        # Overall color based on status
        if offline_count == 0:
            color = discord.Color.green()
        elif online_count == 0:
            color = discord.Color.red()
        else:
            color = discord.Color.orange()

        embed = discord.Embed(
            title="📋 Alle Projekte - Status-Übersicht",
            description=f"🟢 **{online_count}** Online | 🔴 **{offline_count}** Offline | 📊 **{total_count}** Gesamt",
            color=color,
            timestamp=datetime.now()
        )

        # Sort projects: online first, then alphabetically
        sorted_statuses = sorted(
            all_statuses,
            key=lambda s: (not s['is_online'], s['name'].lower())
        )

        # Add field for each project
        for status in sorted_statuses:
            is_online = status['is_online']
            status_emoji = "🟢" if is_online else "🔴"

            value_parts = [
                f"Status: {status_emoji} {'Online' if is_online else 'Offline'}",
                f"Uptime: {status['uptime_percentage']:.1f}%"
            ]

            if is_online:
                value_parts.append(f"Response: {status['average_response_time_ms']:.0f}ms")
            else:
                if status['current_downtime_minutes']:
                    value_parts.append(f"Downtime: {status['current_downtime_minutes']}m")
                if status['consecutive_failures']:
                    value_parts.append(f"Failures: {status['consecutive_failures']}")

            embed.add_field(
                name=f"{status_emoji} **{status['name']}**",
                value="\n".join(value_parts),
                inline=True
            )

        embed.set_footer(text=f"Angefragt von {interaction.user.name} • Verwende /projekt-status [name] für Details")

        await interaction.followup.send(embed=embed)

        bot.logger.info(f"📋 /alle-projekte von {interaction.user.name}")

    except Exception as e:
        bot.logger.error(f"❌ Fehler in /alle-projekte: {e}", exc_info=True)
        await interaction.followup.send("❌ Fehler beim Abrufen der Projekt-Übersicht", ephemeral=True)


# ========================
# BOT START
# ========================

def main():
    """Hauptfunktion"""
    try:
        config = get_config()
        logger = setup_logger("shadowops", config.debug_mode)

        logger.info("=" * 60)
        logger.info("🗡️  ShadowOps Security Bot")
        logger.info("=" * 60)

        # Starte Bot
        bot.run(config.discord_token, log_handler=None)

    except FileNotFoundError as e:
        print(f"❌ Config-Fehler: {e}")
        sys.exit(1)
    except ValueError as e:
        print(f"❌ Config-Fehler: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n👋 Bot wird beendet...")
        sys.exit(0)
    except Exception as e:
        print(f"❌ Kritischer Fehler: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def ensure_single_instance():
    """
    Ensures only one instance of the bot is running using PID file.
    Prevents multiple instances from running simultaneously.
    """
    pid_file = Path(__file__).parent.parent / ".bot.pid"
    current_pid = os.getpid()

    # Check if PID file exists
    if pid_file.exists():
        try:
            old_pid = int(pid_file.read_text().strip())

            # Check if process with that PID still exists
            try:
                os.kill(old_pid, 0)  # Signal 0 = check if process exists
                print(f"❌ FEHLER: Bot läuft bereits (PID: {old_pid})")
                print(f"   PID-Datei: {pid_file}")
                print(f"   Zum Stoppen: kill {old_pid}")
                sys.exit(1)
            except OSError:
                # Process doesn't exist anymore, PID file is stale
                print(f"⚠️  Stale PID file gefunden (alter PID: {old_pid}), wird entfernt...")
                pid_file.unlink()
        except (ValueError, FileNotFoundError):
            # Invalid or missing PID file
            pid_file.unlink(missing_ok=True)

    # Write current PID
    pid_file.write_text(str(current_pid))
    print(f"✅ Single Instance Lock erstellt (PID: {current_pid})")

    # Register cleanup on exit
    def cleanup_pid_file():
        if pid_file.exists():
            try:
                stored_pid = int(pid_file.read_text().strip())
                if stored_pid == current_pid:
                    pid_file.unlink()
                    print(f"🧹 PID-Datei entfernt")
            except:
                pass

    atexit.register(cleanup_pid_file)

    # Handle SIGTERM and SIGINT
    def signal_handler(signum, frame):
        print(f"\n🛑 Signal {signum} empfangen, beende Bot...")
        cleanup_pid_file()
        sys.exit(0)

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)


if __name__ == "__main__":
    # Ensure only one instance is running
    ensure_single_instance()

    main()
