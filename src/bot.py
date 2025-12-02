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
from utils.health_server import HealthCheckServer
from utils.state_manager import get_state_manager

from integrations.fail2ban import Fail2banMonitor
from integrations.crowdsec import CrowdSecMonitor
from integrations.docker import DockerSecurityMonitor
from integrations.aide import AIDEMonitor
from integrations.event_watcher import SecurityEventWatcher
from integrations.self_healing import SelfHealingCoordinator
from integrations.orchestrator import RemediationOrchestrator
from integrations.ai_service import AIService
from integrations.context_manager import ContextManager
from integrations.auto_fix_manager import AutoFixManager

# Phase 5: Multi-Project Management (v3.1)
from integrations.github_integration import GitHubIntegration
from integrations.project_monitor import ProjectMonitor
from integrations.deployment_manager import DeploymentManager
from integrations.incident_manager import IncidentManager
from integrations.customer_notifications import CustomerNotificationManager
from integrations.customer_server_setup import CustomerServerSetup
from integrations.guildscout_alerts import GuildScoutAlertsHandler

# AI Learning System
from integrations.ai_learning import ContinuousLearningAgent
from integrations.research_fetcher import ResearchFetcher

# Queue Management
from integrations.ollama_queue_manager import OllamaQueueManager
from integrations.queue_dashboard import QueueDashboard


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

        # Phase 5: Multi-Project Management (v3.1)
        self.github_integration = None
        self.guildscout_alerts = None
        self.project_monitor = None
        self.deployment_manager = None
        self.incident_manager = None
        self.customer_notifications = None
        self.customer_server_setup = None

        # AI Learning System
        self.continuous_learning = None

        # Queue Management
        self.queue_manager = None
        self.queue_dashboard = None

        # Health Check HTTP Server
        self.health_server = HealthCheckServer(bot=self, port=8766)

        # Discord Channel Logger (für kategorisierte Logs)
        self.discord_logger = DiscordChannelLogger(bot=None, config=self.config)
        # Research Fetcher (sicherer Allowlist-Fetch)
        self.research_fetcher = ResearchFetcher(config=self.config, discord_logger=self.discord_logger)
        # Auto-Fix Manager (Reaction-gesteuert)
        self.auto_fix_manager = AutoFixManager(config=self.config, ai_service=None)
        # Auto-Fix Manager (Proposal/Reaction Flow)
        self.auto_fix_manager = AutoFixManager(config=self.config, ai_service=None)
        
        # State Manager for dynamic data
        self.state_manager = get_state_manager()

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
        Erstellt automatisch ALLE benötigten Discord Channels und speichert die IDs zur Laufzeit
        und im StateManager.
        """
        try:
            self.logger.info("🔧 Prüfe und erstelle Discord Channels...")

            guild = self.get_guild(self.config.guild_id)
            if not guild:
                self.logger.error(f"❌ Guild {self.config.guild_id} nicht gefunden!")
                return

            bot_member = guild.get_member(self.user.id)
            if not bot_member or not bot_member.guild_permissions.manage_channels:
                self.logger.error("❌ Bot hat keine 'Manage Channels' Permission! Channel-Erstellung wird übersprungen.")
                return

            self.logger.info("✅ Bot hat 'Manage Channels' Permission")

            # Categories
            security_category = await self._get_or_create_category(guild, "🔐 Security Monitoring")
            auto_remediation_category = await self._get_or_create_category(guild, "🤖 Auto-Remediation")
            system_category = await self._get_or_create_category(guild, "⚙️ System Status")
            multi_project_category = await self._get_or_create_category(guild, "🌐 Multi-Project")
            project_updates_category = await self._get_or_create_category(guild, "📢 Project Updates")

            channels_created_or_updated_in_session = False 

            # Helper to find/create channels and update config/state
            async def _ensure_channel(state_key: str, channel_name: str, topic: str, category: discord.CategoryChannel, config_target_dict: dict, config_target_key: str, is_autorem_channel: bool = False):
                nonlocal channels_created_or_updated_in_session
                channel_id = None

                # 1. Try to get ID from state manager
                state_channel_id = self.state_manager.get_channel_id(guild.id, state_key)
                if state_channel_id:
                    dc_channel = guild.get_channel(state_channel_id)
                    if dc_channel:
                        # Channel exists and ID is in state manager
                        if dc_channel.category_id != category.id:
                            self.logger.info(f"📦 Verschiebe '{channel_name}' → {category.name}")
                            await dc_channel.edit(category=category)
                            channels_created_or_updated_in_session = True # Consider move as an update
                        config_target_dict[config_target_key] = state_channel_id
                        self.logger.info(f"✅ Channel '{channel_name}' existiert (ID aus State: {state_channel_id})")
                        return state_channel_id
                
                # 2. Try to find by name
                dc_channel = discord.utils.get(guild.text_channels, name=channel_name)
                if dc_channel:
                    if dc_channel.category_id != category.id:
                        self.logger.info(f"📦 Verschiebe '{channel_name}' → {category.name}")
                        await dc_channel.edit(category=category)
                        channels_created_or_updated_in_session = True # Consider move as an update
                    channel_id = dc_channel.id
                    config_target_dict[config_target_key] = channel_id
                    self.state_manager.set_channel_id(guild.id, state_key, channel_id) # Store in state for next time
                    channels_created_or_updated_in_session = True
                    self.logger.info(f"✅ Channel '{channel_name}' gefunden (ID: {channel_id})")
                    return channel_id

                # 3. Create new channel
                self.logger.info(f"📝 Erstelle Channel: {channel_name}")
                new_channel = await guild.create_text_channel(
                    name=channel_name, 
                    topic=topic, 
                    category=category, 
                    reason="ShadowOps Bot Setup" + (" - Auto-Remediation" if is_autorem_channel else "")
                )
                channel_id = new_channel.id
                config_target_dict[config_target_key] = channel_id
                self.state_manager.set_channel_id(guild.id, state_key, channel_id) # Store in state for next time
                channels_created_or_updated_in_session = True
                self.logger.info(f"✅ Channel '{channel_name}' erstellt (ID: {channel_id})")
                return channel_id


            # ============================================
            # TEIL 1: CORE CHANNELS
            # Channels, die direkt in self.config.channels liegen
            # ============================================
            # Ensure the channels dict exists in config for direct access
            if 'channels' not in self.config._config:
                self.config._config['channels'] = {}

            core_channels_to_manage = [
                ('critical', '🔴-critical', 'Kritische Security Alerts - Sofortige Reaktion erforderlich', security_category),
                ('bot_status', '🤖-bot-status', '⚙️ Bot Startup, Health-Checks und System-Status', system_category),
                ('deployment_log', '🚀-deployment-log', '🚀 Deployment-Benachrichtigungen und Auto-Deploy Logs', multi_project_category),
                # Add other standard top-level channels here if they should be auto-created
                ('sicherheitsdienst', '🛡️-security', 'Sicherheitsdienst Project Alerts', security_category),
                ('nexus', '⚡-nexus', 'Nexus Project Alerts', security_category),
                ('fail2ban', '🚫-fail2ban', 'Fail2ban Bans und Aktivitäten', security_category),
                ('crowdsec', '🛡️-crowdsec', 'CrowdSec Alerts', security_category),
                ('docker', '🐳-docker', 'Docker Security Scans (Trivy)', security_category),
                ('backups', '💾-backups', 'Backup Status und Logs', security_category),
                ('aide', '📁-aide', 'AIDE File Integrity Monitoring', security_category),
                ('ssh', '🔑-ssh', 'SSH Anomalien', security_category),
                ('performance', '📊-performance', 'Performance Monitoring', system_category),
                ('ollama_queue', '🔄-ollama-queue', 'Ollama Request Queue Status Dashboard', system_category),
                ('customer_alerts', '👥-customer-alerts', 'Kunden-sichtbare Alerts und Incidents', multi_project_category),
                ('customer_status', '📊-customer-status', 'Projekt-Status Updates und Dashboards', multi_project_category),
            ]

            for key, name, topic, category in core_channels_to_manage:
                await _ensure_channel(key, name, topic, category, self.config._config['channels'], key)

            # ============================================
            # TEIL 2: AUTO-REMEDIATION CHANNELS
            # Kanäle, die unter self.config.auto_remediation.notifications liegen
            # ============================================
            if self.config.auto_remediation.get('enabled', False):
                auto_remediation_config = self.config._config.get('auto_remediation', {})
                if 'notifications' not in auto_remediation_config:
                    auto_remediation_config['notifications'] = {}
                self.config._config['auto_remediation'] = auto_remediation_config # Ensure structure exists

                ar_channel_names = self.config.auto_remediation.get('channel_names', {})
                
                ar_channels_to_manage = [
                    ('alerts', ar_channel_names.get('alerts', '🤖-auto-remediation-alerts'), '🤖 Live-Updates aller Auto-Remediation Fixes'),
                    ('approvals', ar_channel_names.get('approvals', '✋-auto-remediation-approvals'), '✋ Human-Approval Requests für kritische Fixes'),
                    ('stats', ar_channel_names.get('stats', '📊-auto-remediation-stats'), '📊 Tägliche Auto-Remediation Statistiken'),
                    ('ai_learning', ar_channel_names.get('ai_learning', '🧠-ai-learning'), '🧠 AI Learning Logs: Code Analyzer, Git History, Knowledge Base'),
                    ('code_fixes', ar_channel_names.get('code_fixes', '🔧-code-fixes'), '🔧 Code Fixer: Vulnerability Processing & Fix Generation'),
                    ('orchestrator', ar_channel_names.get('orchestrator', '⚡-orchestrator'), '⚡ Orchestrator: Batch Event Coordination & Planning'),
                    ('ai_code_scans', ar_channel_names.get('ai_code_scans', '🔎-ai-code-scans'), '🔎 Auto-Fix Vorschläge & Status (Reaction-basiert)') # Assuming this is meant to be here
                ]

                for channel_type, name, topic in ar_channels_to_manage:
                    config_key_in_state = f'ar_{channel_type}' # Key for state manager
                    config_target_key_in_dict = f'{channel_type}_channel' # Key for auto_remediation.notifications dict
                    await _ensure_channel(config_key_in_state, name, topic, auto_remediation_category, self.config._config['auto_remediation']['notifications'], config_target_key_in_dict, is_autorem_channel=True)
            
            # ============================================
            # TEIL 4: PROJECT-SPECIFIC UPDATE CHANNELS
            # Kanäle, die pro Projekt in self.config.projects[proj_name]['update_channel_id'] liegen
            # ============================================
            if self.config.projects:
                # Ensure the projects dict exists in config for direct access
                if 'projects' not in self.config._config:
                    self.config._config['projects'] = {}

                for proj_name, proj_config in self.config.projects.items():
                    # Generate default channel name if not explicitly set in config
                    channel_name = proj_config.get("update_channel_name", f"updates-{proj_name}")
                    
                    self.logger.info(f"Prüfe Update-Channel für Projekt '{proj_name}' (Name: '{channel_name}')")
                    await _ensure_channel(
                        f"project_{proj_name}_updates", # Unique key for state manager
                        channel_name, 
                        f"Updates & Patch-Notes für das Projekt {proj_name}", 
                        project_updates_category,
                        self.config.projects[proj_name], # Update target is the project's config dict
                        'update_channel_id',             # Key in the project's config dict
                        is_autorem_channel=False         # Not an AR channel
                    )
                    self.logger.info(f"✅ Laufzeit-Config für '{proj_name}' aktualisiert mit Channel-ID: {self.config.projects[proj_name].get('update_channel_id')}")


            if channels_created_or_updated_in_session:
                self.logger.info("✅ Channel-Setup komplett! Alle Channel-IDs wurden aktualisiert.")
            else:
                self.logger.info("ℹ️ Alle benötigten Channels existieren bereits und sind korrekt konfiguriert.")

        except discord.Forbidden:
            self.logger.error("❌ FEHLER: Bot hat keine Berechtigung Channels zu erstellen! Bitte 'Manage Channels' Permission geben.")
        except Exception as e:
            self.logger.error(f"❌ Fehler beim Setup der Channels: {e}", exc_info=True)
        
        # Finalize initializations that depend on channels
        self.logger.info("🔄 Initialisiere Discord Channel Logger...")
        self.discord_logger.set_bot(self)
        await self.discord_logger.start()
        self.logger.info("✅ Discord Channel Logger bereit")

        # Initialisiere Auto-Fix Manager Channels
        try:
            await self.auto_fix_manager.ensure_channels(self)
            self.auto_fix_manager.register_persistent_view(self)
        except Exception as e:
            self.logger.warning(f"Auto-Fix Channel Setup fehlgeschlagen: {e}")


    async def _update_all_channel_ids(self, channel_ids: dict):
        """
        Schreibt alle neuen Channel-IDs in den StateManager für die aktuelle Guild.
        """
        try:
            guild_id = self.config.guild_id
            if not guild_id:
                self.logger.error("❌ Guild ID nicht in der Config gefunden. Kann Channel-IDs nicht speichern.")
                return

            self.logger.info(f"💾 Speichere Channel-IDs für Guild {guild_id} im State...")

            # Update standard and multi-project channels
            for key, channel_id in channel_ids.items():
                if not key.startswith('auto_remediation_'):
                    self.state_manager.set_channel_id(guild_id, key, channel_id)
                    self.logger.info(f"💾 State für Channel '{key}' aktualisiert: {channel_id}")

            # Update Auto-Remediation Channels
            for key, channel_id in channel_ids.items():
                if key.startswith('auto_remediation_'):
                    channel_type = key.replace('auto_remediation_', '')
                    # We store these with a more specific name in the state
                    state_key = f"ar_{channel_type}"
                    self.state_manager.set_channel_id(guild_id, state_key, channel_id)
                    self.logger.info(f"💾 State für Auto-Remediation Channel '{channel_type}' aktualisiert: {channel_id}")

            self.logger.info("✅ State-Datei aktualisiert mit allen neuen Channel-IDs")

        except Exception as e:
            self.logger.warning(f"⚠️ Konnte State-Datei nicht aktualisieren: {e}", exc_info=True)

    async def setup_hook(self):
        """Setup Hook - wird VOR Discord-Verbindung aufgerufen"""
        self.logger.info("🗡️ ShadowOps Bot startet...")
        await self.load_cogs()
        self.logger.info("⏳ Warte auf Discord-Verbindung...")

    async def load_cogs(self):
        """Loads all cogs from the cogs directory."""
        self.logger.info("🔄 Lade Cogs...")
        cog_dir = Path(__file__).parent / "cogs"
        for filename in os.listdir(cog_dir):
            if filename.endswith(".py") and not filename.startswith("__"):
                try:
                    cog_name = f"cogs.{filename[:-3]}"
                    await self.load_extension(cog_name)
                    self.logger.info(f"✅ Cog '{filename[:-3]}' geladen.")
                except Exception as e:
                    self.logger.error(f"❌ Fehler beim Laden von Cog '{filename[:-3]}': {e}", exc_info=True)

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

        # Validate Fail2ban permissions
        self.fail2ban.validate_permissions()

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
            # Set ai_service reference for Auto-Fix Manager
            self.auto_fix_manager.ai_service = self.ai_service
            self.logger.info("✅ [2/5] AI Service bereit")

            # Initialisiere Ollama Queue Manager (verhindert Resource Exhaustion)
            self.logger.info("🔄 [2.5/5] Initialisiere Ollama Queue Manager...")
            self.queue_manager = OllamaQueueManager(ai_service=self.ai_service)
            await self.queue_manager.start_worker()
            self.logger.info("✅ [2.5/5] Ollama Queue Manager bereit (Security-First Queuing aktiv)")

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
                config=self.config,
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
            # PHASE 5: MULTI-PROJECT MANAGEMENT (v3.1)
            # ============================================
            self.logger.info("=" * 60)
            self.logger.info("🌐 PHASE 5: Multi-Project Management Initialisierung (v3.1)")
            self.logger.info("=" * 60)

            await self._send_status_message(
                "⏳ **Phase 5/6:** Initialisiere Multi-Project Management...",
                0x3498DB
            )

            # Initialisiere Customer Notifications
            self.logger.info("🔄 [1/5] Initialisiere Customer Notification Manager...")
            self.customer_notifications = CustomerNotificationManager(self, self.config)
            self.logger.info("✅ [1/5] Customer Notifications bereit")

            # Initialisiere Incident Manager
            self.logger.info("🔄 [2/5] Initialisiere Incident Manager...")
            self.incident_manager = IncidentManager(self, self.config)
            self.logger.info("✅ [2/5] Incident Manager bereit")

            # Initialisiere Deployment Manager
            self.logger.info("🔄 [3/5] Initialisiere Deployment Manager...")
            self.deployment_manager = DeploymentManager(self, self.config)
            self.logger.info("✅ [3/5] Deployment Manager bereit")

            # Initialisiere Project Monitor
            self.logger.info("🔄 [4/5] Initialisiere Project Monitor...")
            self.project_monitor = ProjectMonitor(self, self.config)
            # Link IncidentManager to ProjectMonitor for proper incident tracking
            self.project_monitor.incident_manager = self.incident_manager
            await self.project_monitor.start_monitoring()
            self.logger.info("✅ [4/5] Project Monitor gestartet (mit Incident-Tracking)")

            # Initialisiere GitHub Integration
            self.logger.info("🔄 [5/6] Initialisiere GitHub Integration...")
            self.github_integration = GitHubIntegration(self, self.config)
            # Link Deployment Manager and AI Service to GitHub Integration
            if self.github_integration.enabled:
                self.github_integration.deployment_manager = self.deployment_manager
                self.github_integration.ai_service = self.ai_service  # For KI patch notes

                # Initialize Complete AI Learning System for Patch Notes
                try:
                    from integrations.patch_notes_trainer import get_patch_notes_trainer
                    from integrations.patch_notes_feedback import get_feedback_collector
                    from integrations.prompt_ab_testing import get_prompt_ab_testing
                    from integrations.prompt_auto_tuner import get_prompt_auto_tuner
                    from integrations.llm_fine_tuning import get_llm_fine_tuning

                    # 1. Core Trainer
                    self.patch_notes_trainer = get_patch_notes_trainer()
                    self.github_integration.patch_notes_trainer = self.patch_notes_trainer

                    # 2. Feedback Collector (Discord Reactions)
                    self.feedback_collector = get_feedback_collector(self, self.patch_notes_trainer)
                    self.github_integration.feedback_collector = self.feedback_collector

                    # 3. A/B Testing System
                    self.prompt_ab_testing = get_prompt_ab_testing()
                    self.github_integration.prompt_ab_testing = self.prompt_ab_testing

                    # 4. Auto-Tuner
                    self.prompt_auto_tuner = get_prompt_auto_tuner(
                        self.patch_notes_trainer.data_dir,
                        self.prompt_ab_testing,
                        self.patch_notes_trainer
                    )
                    self.github_integration.prompt_auto_tuner = self.prompt_auto_tuner

                    # 5. Fine-Tuning System
                    self.llm_fine_tuning = get_llm_fine_tuning(
                        self.patch_notes_trainer.data_dir,
                        self.patch_notes_trainer
                    )

                    # Log training stats
                    stats = self.patch_notes_trainer.get_statistics()
                    ab_stats = self.prompt_ab_testing.get_variant_statistics()

                    self.logger.info(f"✅ Complete AI Learning System initialized:")
                    self.logger.info(f"   📊 Training Examples: {stats['total_examples']}")
                    self.logger.info(f"   🌟 Good Examples: {stats['good_examples']}")
                    self.logger.info(f"   📈 Avg Quality Score: {stats['avg_quality_score']:.1f}/100")
                    self.logger.info(f"   🧪 A/B Test Variants: {len(self.prompt_ab_testing.variants)}")
                    self.logger.info(f"   🎯 Total A/B Tests Run: {sum(s['count'] for s in ab_stats.values())}")

                except Exception as e:
                    self.logger.warning(f"⚠️ AI Learning System konnte nicht vollständig initialisiert werden: {e}", exc_info=True)
                    self.patch_notes_trainer = None
                    self.feedback_collector = None
                    self.prompt_ab_testing = None
                    self.prompt_auto_tuner = None
                    self.llm_fine_tuning = None

                # Initialize Advanced Patch Notes Manager (optional, for approval system)
                try:
                    from integrations.patch_notes_manager import get_patch_notes_manager
                    self.patch_notes_manager = get_patch_notes_manager(self, self.ai_service)
                    self.github_integration.patch_notes_manager = self.patch_notes_manager
                    self.logger.info("✅ Advanced Patch Notes Manager initialisiert (optional)")
                except Exception as e:
                    self.logger.debug(f"Advanced Patch Notes Manager nicht verfügbar: {e}")
                    self.patch_notes_manager = None

                await self.github_integration.start_webhook_server()
                self.logger.info("✅ [5/6] GitHub Integration gestartet (Webhook Server läuft)")

                # Link Queue Manager to GitHub Integration for AI requests
                if self.queue_manager:
                    self.github_integration.queue_manager = self.queue_manager
                    self.logger.info("✅ Queue Manager mit GitHub Integration verknüpft")
            else:
                self.logger.info("ℹ️ [5/6] GitHub Integration deaktiviert (config: github.enabled=false)")

            # Initialize Queue Dashboard
            if self.queue_manager:
                self.logger.info("🔄 [5.5/6] Initialisiere Queue Dashboard...")
                queue_channel_id = self.config.channels.get('ollama_queue')
                if queue_channel_id:
                    self.queue_dashboard = QueueDashboard(
                        bot=self,
                        queue_manager=self.queue_manager,
                        channel_id=queue_channel_id
                    )
                    self.logger.info("✅ [5.5/6] Queue Dashboard gestartet")
                else:
                    self.logger.warning("⚠️ Queue Dashboard Channel nicht gefunden - Dashboard deaktiviert")

                # Load Queue Admin Commands
                try:
                    from commands.queue_admin import setup as setup_queue_admin
                    await setup_queue_admin(self, self.queue_manager, self.queue_dashboard, self.config)
                    self.logger.info("✅ Queue Admin Commands geladen")
                except Exception as e:
                    self.logger.error(f"❌ Fehler beim Laden der Queue Admin Commands: {e}", exc_info=True)

            # Initialisiere GuildScout Alerts Handler
            self.logger.info("🔄 [5.5/6] Initialisiere GuildScout Alerts Handler...")
            try:
                from integrations.guildscout_alerts import setup as setup_guildscout_alerts
                self.guildscout_alerts = await setup_guildscout_alerts(self, self.config)
                self.logger.info("✅ [5.5/6] GuildScout Alerts Handler gestartet (Port 9091)")
            except Exception as e:
                self.logger.warning(f"⚠️ GuildScout Alerts Handler konnte nicht gestartet werden: {e}")

            # Initialisiere Customer Server Setup (Auto-Channel Creation)
            self.logger.info("🔄 [6/6] Initialisiere Customer Server Setup...")
            self.customer_server_setup = CustomerServerSetup(self, self.config)
            # Check all guilds and setup missing channels
            await self.customer_server_setup.check_and_setup_all_guilds()
            self.logger.info("✅ [6/6] Customer Server Setup bereit (Auto-Channel Creation)")

            self.logger.info("=" * 60)
            self.logger.info("✅ PHASE 5 abgeschlossen - Multi-Project Management aktiv")
            self.logger.info("=" * 60)

            await self._send_status_message(
                "✅ **Multi-Project Management aktiv**\n"
                f"• Project Monitor: ✅ {len(self.project_monitor.projects)} Projekte überwacht\n"
                f"• Incident Manager: ✅ Automatisches Tracking\n"
                f"• Deployment Manager: ✅ CI/CD Pipeline bereit\n"
                f"• GitHub Webhook: {'✅ Aktiv' if self.github_integration.enabled else '⏸️ Deaktiviert'}\n"
                f"• Customer Notifications: ✅ Bereit",
                0x00FF00
            )

            # ============================================
            # PHASE 6: STARTE AI LEARNING (mit größerem Delay)
            # ============================================
            self.logger.info("=" * 60)
            self.logger.info("⏳ PHASE 6: AI Learning startet in 15 Sekunden...")
            self.logger.info("=" * 60)

            await self._send_status_message(
                "⏳ **Phase 6/6:** AI Learning startet in 15 Sekunden...\n"
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

            # ============================================
            # START CONTINUOUS LEARNING SYSTEM
            # ============================================
            self.logger.info("=" * 60)
            self.logger.info("🧠 Starting Continuous Learning System...")
            self.logger.info("=" * 60)

            try:
                self.continuous_learning = ContinuousLearningAgent(
                    bot=self,
                    config=self.config,
                    ai_service=self.ai_service,
                    context_manager=self.context_manager,
                    discord_logger=self.discord_logger
                )
                await self.continuous_learning.start()
                self.logger.info("✅ Continuous Learning System gestartet")
            except Exception as e:
                self.logger.error(f"❌ Continuous Learning System konnte nicht gestartet werden: {e}", exc_info=True)

        else:
            self.logger.info("ℹ️ Auto-Remediation deaktiviert (config: auto_remediation.enabled=false)")

        # ============================================
        # START HEALTH CHECK SERVER
        # ============================================
        self.logger.info("🔄 Starte Health Check Server...")
        try:
            await self.health_server.start()
            self.logger.info("✅ Health Check Server gestartet (Port 8766)")
        except Exception as e:
            self.logger.error(f"❌ Health Check Server konnte nicht gestartet werden: {e}")

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

    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        """Handle Reaktionen für Auto-Fix Vorschläge (Reaction-basiert)."""
        try:
            if payload.user_id == self.user.id:
                return
            await self.auto_fix_manager.handle_reaction(self, payload)
        except Exception as e:
            self.logger.error(f"Reaction handler error: {e}", exc_info=True)

    async def on_guild_join(self, guild: discord.Guild):
        """Bot wurde zu Server hinzugefügt"""
        self.logger.info(f"➕ Bot zu Server hinzugefügt: {guild.name} ({guild.id})")

        # Automatic channel setup for customer servers
        if self.customer_server_setup:
            try:
                await self.customer_server_setup.on_guild_join(guild)
            except Exception as e:
                self.logger.error(f"❌ Failed to setup customer server {guild.name}: {e}", exc_info=True)

    async def on_error(self, event: str, *args, **kwargs):
        """Error Handler"""
        self.logger.error(f"❌ Fehler in Event {event}", exc_info=True)

    async def close(self):
        """Clean shutdown of the bot"""
        self.logger.info("🛑 Shutting down ShadowOps Bot...")

        # Stop continuous learning system
        if self.continuous_learning:
            try:
                await self.continuous_learning.stop()
            except Exception as e:
                self.logger.error(f"Error stopping continuous learning: {e}")

        # Stop health check server
        try:
            await self.health_server.stop()
        except Exception as e:
            self.logger.error(f"Error stopping health server: {e}")

        # Stop project monitor
        if self.project_monitor:
            try:
                await self.project_monitor.stop_monitoring()
            except Exception as e:
                self.logger.error(f"Error stopping project monitor: {e}")

        # Stop GitHub webhook server
        if self.github_integration and self.github_integration.enabled:
            try:
                await self.github_integration.stop_webhook_server()
            except Exception as e:
                self.logger.error(f"Error stopping GitHub integration: {e}")

        # Close parent bot
        await super().close()

        self.logger.info("✅ ShadowOps Bot shutdown complete")

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
        bot = ShadowOpsBot()
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
