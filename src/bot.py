#!/usr/bin/env python3
"""
🗡️ ShadowOps - Security Operations Discord Bot
Monitort Security-Tools und sendet Echtzeit-Alerts
"""

import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
import logging
import sys
import os
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
from integrations.ai_engine import AIEngine
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

# AI Learning System (Legacy, nur noch fuer Config-Kompatibilitaet)
from integrations.ai_learning import ContinuousLearningAgent
from integrations.research_fetcher import ResearchFetcher

# Server Assistant (ersetzt Learning System)
from integrations.server_assistant import ServerAssistant

# Security Analyst (autonome AI Security Sessions)
from integrations.analyst import SecurityAnalyst

# Queue Management
from integrations.smart_queue import SmartQueue


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
        self.ai_service = None
        self.context_manager = None

        # Phase 5: Multi-Project Management (v3.1)
        self.github_integration = None
        self.guildscout_alerts = None
        self.project_monitor = None
        self.deployment_manager = None
        self.incident_manager = None
        self.customer_notifications = None
        self.customer_server_setup = None

        # AI Learning System (Legacy)
        self.continuous_learning = None

        # Server Assistant (ersetzt Learning)
        self.server_assistant = None
        # Security Analyst (autonome AI Security Sessions)
        self.security_analyst = None

        # Changelog-DB (Patch Notes v3)
        self.changelog_db = None

        # Queue Management
        self.smart_queue = None

        # Health Check HTTP Server
        self.health_server = HealthCheckServer(bot=self, port=8766)

        # Discord Channel Logger (für kategorisierte Logs)
        self.discord_logger = DiscordChannelLogger(bot=None, config=self.config)
        # Research Fetcher (sicherer Allowlist-Fetch)
        self.research_fetcher = ResearchFetcher(config=self.config, discord_logger=self.discord_logger)
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
            auto_remediation_category = await self._get_or_create_category(guild, "🤖 AI Engine")
            system_category = await self._get_or_create_category(guild, "📦 System & Projekte")
            project_updates_category = await self._get_or_create_category(guild, "📢 Updates & CI")

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
                # 🔐 Security Monitoring
                ('critical', '🚨-critical', 'Kritische Security Alerts - Sofortige Reaktion erforderlich', security_category),
                ('fail2ban', '🚫-fail2ban', 'Fail2ban Bans und Aktivitäten', security_category),
                ('crowdsec', '🛡️-crowdsec', 'CrowdSec Alerts', security_category),
                ('docker', '🐳-docker', 'Docker Security Scans (Trivy)', security_category),
                ('guildscout', '⚡-guildscout', 'GuildScout Verification Alerts & Performance Monitoring', security_category),
                # 📦 System & Projekte
                ('bot_status', '🤖-bot-status', 'Bot Startup, Health-Checks und System-Status', system_category),
                ('customer_alerts', '👥-customer-alerts', 'Kunden-sichtbare Alerts und Incidents', system_category),
                ('deployment_log', '🚀-deployment-log', 'Deployment-Benachrichtigungen und Auto-Deploy Logs', system_category),
                # 📊 Dashboard
                ('dashboard', '📊-dashboard', 'Live-Übersicht aller Projekte und deren Status', system_category),
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
                    ('approvals', ar_channel_names.get('approvals', '✋-approvals'), '✋ Human-Approval Requests für kritische Fixes'),
                    ('ai_learning', ar_channel_names.get('ai_learning', '🧠-ai-learning'), '🧠 AI Learning Logs: Code Analyzer, Git History, Knowledge Base'),
                    ('code_fixes', ar_channel_names.get('code_fixes', '🔧-code-fixes'), '🔧 Code Fixer: Vulnerability Processing & Fix Generation'),
                    ('orchestrator', ar_channel_names.get('orchestrator', '⚡-orchestrator'), '⚡ Orchestrator: Batch Event Coordination & Planning'),
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
                    if not proj_config.get('enabled', True):
                        self.logger.info(f"⏭️ Projekt '{proj_name}' deaktiviert, überspringe Channel-Setup")
                        continue
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

        # SIGTERM-Handler im Event-Loop registrieren für graceful shutdown.
        # bot.run() → asyncio.run() fängt nur SIGINT. SIGTERM (systemd stop/restart)
        # muss explizit behandelt werden, damit bot.close() die HTTP-Server-Sockets freigibt.
        self._sigterm_received = False
        def _handle_sigterm():
            if self._sigterm_received:
                return  # Doppelten SIGTERM ignorieren
            self._sigterm_received = True
            self.logger.info("🛑 SIGTERM empfangen — starte graceful shutdown...")
            asyncio.ensure_future(self.close())

        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGTERM, _handle_sigterm)

        # SIGUSR1-Handler für Logrotate: Log-Dateien neu öffnen statt Bot zu killen.
        # Logrotate sendet SIGUSR1 nach Rotation, damit der Bot in die neue Datei schreibt.
        def _handle_sigusr1():
            try:
                self.logger.info("🔄 SIGUSR1 empfangen — öffne Log-Dateien neu...")
                root_logger = logging.getLogger('shadowops')
                for handler in root_logger.handlers[:]:
                    if isinstance(handler, logging.FileHandler):
                        handler.close()
                        root_logger.removeHandler(handler)
                # Neuen FileHandler mit aktuellem Datum erstellen
                from datetime import datetime as _dt
                log_file = Path("logs") / f"shadowops_{_dt.now().strftime('%Y%m%d')}.log"
                new_handler = logging.FileHandler(log_file, encoding='utf-8')
                new_handler.setLevel(logging.DEBUG)
                new_handler.setFormatter(logging.Formatter(
                    '%(asctime)s [%(levelname)s] %(name)s (%(filename)s:%(lineno)d): %(message)s'
                ))
                root_logger.addHandler(new_handler)
                self.logger.info(f"✅ Log-Datei neu geöffnet: {log_file}")
            except Exception as e:
                # Exception NIEMALS propagieren — sonst wird der Prozess durch SIGUSR1 gekillt
                print(f"[SIGUSR1] Fehler beim Log-Reopening: {e}", flush=True)

        loop.add_signal_handler(signal.SIGUSR1, _handle_sigusr1)

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

            ai_enabled = self.config.ai_enabled
            if ai_enabled:
                # Initialisiere Context Manager (RAG System)
                self.logger.info("🔄 [1/5] Initialisiere Context Manager (RAG)...")
                self.context_manager = ContextManager(config=self.config)
                self.context_manager.load_all_contexts()
                self.logger.info("✅ [1/5] Context Manager bereit")

                # Initialisiere AI Service mit Context Manager und Discord Logger
                self.logger.info("🔄 [2/5] Initialisiere AI Engine (Codex + Claude)...")
                self.ai_service = AIEngine(
                    self.config,
                    context_manager=self.context_manager,
                    discord_logger=self.discord_logger
                )
                # Set ai_service reference for Auto-Fix Manager
                self.auto_fix_manager.ai_service = self.ai_service
                self.logger.info("✅ [2/5] AI Engine bereit (Dual-Engine: Codex Primary + Claude Fallback)")

                # Initialisiere SmartQueue (Analyse-Pool + Fix-Lock)
                self.logger.info("🔄 [2.5/5] Initialisiere SmartQueue...")
                queue_config = self.config.ai.get('queue', {})
                self.smart_queue = SmartQueue(queue_config)
                self.smart_queue.start()
                self.logger.info("✅ [2.5/5] SmartQueue bereit (3 Analyse-Slots, Fix-Lock aktiv)")

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
            else:
                self.logger.info("⏸️ AI-Funktionen deaktiviert - Remediation läuft im Monitoring-Modus")
                self.context_manager = None
                self.ai_service = None
                self.auto_fix_manager.ai_service = None
                self.smart_queue = None
                self.self_healing = None
                self.orchestrator = None

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

            if self.self_healing:
                self.logger.info("🚀 Starte Self-Healing Coordinator...")
                await self.self_healing.start()
                self.logger.info("✅ Self-Healing Coordinator gestartet")
            else:
                self.logger.info("⏸️ Self-Healing Coordinator deaktiviert")

            # Warte 3 Sekunden bevor Event Watcher startet
            await asyncio.sleep(3)

            if self.event_watcher:
                self.logger.info("🚀 Starte Event Watcher...")
                await self.event_watcher.start()
                self.logger.info("✅ Event Watcher gestartet")
            else:
                self.logger.info("⏸️ Event Watcher deaktiviert")

            self.logger.info("=" * 60)
            self.logger.info("✅ Auto-Remediation System vollständig aktiv")
            self.logger.info("=" * 60)

            status_title = "✅ **Auto-Remediation System aktiv**"
            if not self.orchestrator and not self.self_healing:
                status_title = "✅ **Auto-Remediation Monitoring aktiv**"

            orchestrator_status = "✅ Koordination aktiv" if self.orchestrator else "⏸️ deaktiviert"
            healing_status = "✅ Gestartet" if self.self_healing else "⏸️ deaktiviert"
            watcher_status = "✅ Gestartet" if self.event_watcher else "⏸️ deaktiviert"

            await self._send_status_message(
                f"{status_title}\n"
                f"• Remediation Orchestrator: {orchestrator_status}\n"
                f"• Self-Healing Coordinator: {healing_status}\n"
                f"• Event Watcher: {watcher_status}\n"
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

            # Changelog-DB initialisieren (vor Health-Server)
            try:
                from integrations.changelog_db import ChangelogDB
                self.changelog_db = ChangelogDB()
                await self.changelog_db.initialize()
                self.logger.info("✅ Changelog-DB initialisiert")
            except Exception as e:
                self.logger.warning(f"⚠️ Changelog-DB konnte nicht initialisiert werden: {e}")
                self.changelog_db = None

            # Changelog-DB und API-Key an Health-Server weitergeben
            self.health_server.changelog_db = self.changelog_db
            changelog_config = self.config._config.get('changelog_api', {})
            api_key = changelog_config.get('api_key', '')
            self.health_server.api_key = api_key

            # Health-Server frueh starten damit Project-Monitor sich selbst pruefen kann
            try:
                await self.health_server.start()
                self.logger.info("✅ Health Check Server gestartet (Port 8766)")
            except Exception as e:
                self.logger.error(f"❌ Health Check Server konnte nicht gestartet werden: {e}")

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
                if self.config.ai_learning_enabled and self.config.ai_enabled:
                    try:
                        from integrations.patch_notes_trainer import get_patch_notes_trainer
                        from integrations.patch_notes_feedback import get_feedback_collector
                        from integrations.prompt_ab_testing import get_prompt_ab_testing
                        from integrations.prompt_auto_tuner import get_prompt_auto_tuner
                        from integrations.llm_fine_tuning import get_llm_fine_tuning

                        # 1. Core Trainer
                        self.patch_notes_trainer = get_patch_notes_trainer()
                        self.github_integration.patch_notes_trainer = self.patch_notes_trainer

                        # 2. Feedback Collector (Discord Buttons, Persistent Views)
                        self.feedback_collector = get_feedback_collector(self, self.patch_notes_trainer)
                        self.github_integration.feedback_collector = self.feedback_collector
                        self.feedback_collector.register_persistent_view()

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
                else:
                    self.logger.info("⏸️ AI Learning deaktiviert - Patch Notes laufen ohne Training/A-B-Tests")
                    self.patch_notes_trainer = None
                    self.github_integration.patch_notes_trainer = None
                    self.feedback_collector = None
                    self.github_integration.feedback_collector = None
                    self.prompt_ab_testing = None
                    self.github_integration.prompt_ab_testing = None
                    self.prompt_auto_tuner = None
                    self.github_integration.prompt_auto_tuner = None
                    self.llm_fine_tuning = None

                # Feedback-Collector unabhängig vom AI Learning initialisieren
                if not self.feedback_collector:
                    try:
                        from integrations.patch_notes_feedback import get_feedback_collector
                        trainer = getattr(self, 'patch_notes_trainer', None)
                        self.feedback_collector = get_feedback_collector(self, trainer)
                        self.github_integration.feedback_collector = self.feedback_collector
                        self.feedback_collector.register_persistent_view()
                        self.logger.info("✅ Feedback Collector initialisiert (standalone, ohne AI Learning)")
                    except Exception as e:
                        self.logger.warning(f"⚠️ Feedback Collector konnte nicht initialisiert werden: {e}")
                        self.feedback_collector = None
                        self.github_integration.feedback_collector = None

                # Initialize Advanced Patch Notes Manager (optional, for approval system)
                if self.ai_service:
                    try:
                        from integrations.patch_notes_manager import get_patch_notes_manager
                        self.patch_notes_manager = get_patch_notes_manager(self, self.ai_service)
                        self.github_integration.patch_notes_manager = self.patch_notes_manager
                        self.logger.info("✅ Advanced Patch Notes Manager initialisiert (optional)")
                    except Exception as e:
                        self.logger.debug(f"Advanced Patch Notes Manager nicht verfügbar: {e}")
                        self.patch_notes_manager = None
                else:
                    self.patch_notes_manager = None

                # Initialize Patch Notes v2: Web Exporter + Batcher
                try:
                    from pathlib import Path
                    from integrations.patch_notes_web_exporter import PatchNotesWebExporter
                    from integrations.patch_notes_batcher import PatchNotesBatcher

                    # API-Endpoints aus Config laden
                    api_endpoints = {}
                    projects = self.config.projects
                    if isinstance(projects, dict):
                        for proj_name, proj_config in projects.items():
                            if not isinstance(proj_config, dict):
                                continue
                            pn_config = proj_config.get('patch_notes', {})
                            api_config = pn_config.get('api_endpoint', {})
                            if isinstance(api_config, dict) and api_config.get('url') and api_config.get('api_key', '').strip():
                                api_endpoints[proj_name] = api_config

                    # Web Exporter: Default-Verzeichnis + API-Endpoints
                    default_output = Path.home() / '.shadowops' / 'changelogs'
                    self.web_exporter = PatchNotesWebExporter(
                        default_output, api_endpoints,
                        changelog_db=self.changelog_db
                    )
                    self.github_integration.web_exporter = self.web_exporter

                    # Batcher (braucht data_dir für pending_batch.json)
                    data_dir = Path.home() / '.shadowops' / 'data'
                    self.patch_notes_batcher = PatchNotesBatcher(data_dir)
                    self.github_integration.patch_notes_batcher = self.patch_notes_batcher

                    self.logger.info("✅ Patch Notes v2: Web Exporter + Batcher initialisiert")
                except Exception as e:
                    self.logger.warning(f"⚠️ Patch Notes v2 Komponenten nicht verfügbar: {e}")
                    self.web_exporter = None
                    self.patch_notes_batcher = None

                await self.github_integration.start_webhook_server()
                await self.github_integration.start_local_polling()
                await self.github_integration.ensure_project_webhooks()
                if self.github_integration.enabled:
                    self.logger.info("✅ [5/6] GitHub Integration gestartet (Webhook Server läuft)")
                else:
                    self.logger.warning("⚠️ [5/6] GitHub Webhook Server nicht aktiv - prüfe Port/Config")

                # Link SmartQueue to GitHub Integration for AI requests
                if self.smart_queue:
                    self.github_integration.smart_queue = self.smart_queue
                    self.logger.info("✅ SmartQueue mit GitHub Integration verknüpft")
            else:
                self.logger.info("ℹ️ [5/6] GitHub Integration deaktiviert (config: github.enabled=false)")

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
            # PHASE 6: SERVER ASSISTANT (ersetzt altes Learning)
            # ============================================
            self.logger.info("=" * 60)
            self.logger.info("PHASE 6: Server Assistant starten...")
            self.logger.info("=" * 60)

            try:
                self.server_assistant = ServerAssistant(
                    bot=self,
                    config=self.config,
                    ai_service=self.ai_service,
                )
                await self.server_assistant.start()
                self.logger.info("Server Assistant gestartet")

                await self._send_status_message(
                    "**Phase 6/6: Server Assistant aktiv**\n"
                    "- Daily Housekeeping: 06:00 (lokal, 0 Token)\n"
                    "- Weekly Intelligence Report: Mo 07:00 (1 AI-Call)\n"
                    "- Git Push Security Review: event-getrieben",
                    0x00FF00
                )
            except Exception as e:
                self.logger.error(
                    f"Server Assistant konnte nicht gestartet werden: {e}",
                    exc_info=True
                )

            # Security Analyst starten (wenn aktiviert)
            analyst_config = self.config._config.get('security_analyst', {})
            if analyst_config.get('enabled', False):
                try:
                    self.security_analyst = SecurityAnalyst(
                        bot=self,
                        config=self.config,
                        ai_engine=self.ai_service,
                    )
                    await self.security_analyst.start()
                    self.logger.info("Security Analyst gestartet")

                    await self._send_status_message(
                        "**Security Analyst aktiv**\n"
                        "- Autonome Sessions bei User-Idle\n"
                        "- Discord-Briefing bei Online-Rueckkehr\n"
                        "- GitHub Issues fuer Code-Findings",
                        0x00FF00
                    )
                except Exception as e:
                    self.logger.error(
                        f"Security Analyst konnte nicht gestartet werden: {e}",
                        exc_info=True
                    )

            # Legacy: AI Learning System (falls in Config noch aktiviert)
            if self.config.ai_learning_enabled and self.config.ai_enabled:
                self.logger.info(
                    "AI Learning ist in Config aktiviert, "
                    "wird aber durch Server Assistant ersetzt"
                )

        else:
            self.logger.info("ℹ️ Auto-Remediation deaktiviert (config: auto_remediation.enabled=false)")

        # Health Check Server wurde bereits in Phase 5 gestartet (vor Project Monitor)

        # Starte Background Tasks
        # DISABLED: Old monitor_security replaced by Event Watcher System
        # Event Watcher now handles all alerts + auto-remediation with persistence
        # if not self.monitor_security.is_running():
        #     self.monitor_security.start()
        if not self.daily_health_check.is_running():
            self.daily_health_check.start()
        if not self.update_dashboard.is_running():
            self.update_dashboard.start()

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

        # Process any pending GitHub webhooks that arrived during startup
        if self.github_integration:
            try:
                await self.github_integration.mark_bot_ready_and_process_queue()
            except Exception as e:
                self.logger.error(f"❌ Error processing pending webhooks: {e}")

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
        import traceback
        self.logger.info("🛑 Shutting down ShadowOps Bot...")
        self.logger.info(f"   Close() aufgerufen von: {''.join(traceback.format_stack()[-3:-1])}")

        # Stop Security Analyst
        if self.security_analyst:
            try:
                await self.security_analyst.stop()
            except Exception as e:
                self.logger.error(f"Error stopping security analyst: {e}")

        # Stop Server Assistant
        if self.server_assistant:
            try:
                await self.server_assistant.stop()
            except Exception as e:
                self.logger.error(f"Error stopping server assistant: {e}")

        # Stop continuous learning system (Legacy)
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

        # Stop GuildScout alerts webhook server
        if hasattr(self, 'guildscout_alerts') and self.guildscout_alerts:
            try:
                await self.guildscout_alerts.stop_webhook_server()
            except Exception as e:
                self.logger.error(f"Error stopping GuildScout alerts: {e}")

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
            except Exception:
                fail2ban_ok = False

            crowdsec_ok = True
            crowdsec_decisions = 0
            try:
                crowdsec_ok = self.crowdsec.is_running()
                decisions = self.crowdsec.get_active_decisions(limit=100)
                crowdsec_decisions = len(decisions)
            except Exception:
                crowdsec_ok = False

            docker_ok = True
            docker_last_scan = None
            docker_vulnerabilities = 0
            try:
                results = self.docker.get_latest_scan_results()
                if results:
                    docker_last_scan = results.get('date', 'Unbekannt')
                    docker_vulnerabilities = results.get('critical', 0)
            except Exception:
                docker_ok = False

            aide_ok = True
            aide_last_check = None
            try:
                aide_ok = self.aide.is_timer_active()
                aide_last_check = self.aide.get_last_check_date()
            except Exception:
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

    @tasks.loop(minutes=5)
    async def update_dashboard(self):
        """Aktualisiert das Dashboard-Embed mit aktuellem Projekt-Status alle 5 Minuten"""
        try:
            dashboard_channel_id = self.config.channels.get('dashboard')
            if not dashboard_channel_id:
                return

            channel = self.get_channel(dashboard_channel_id)
            if not channel:
                return

            # Baue Dashboard-Embed
            embed = discord.Embed(
                title="📊 ShadowOps — Projekt-Dashboard",
                description="Live-Status aller überwachten Projekte",
                color=0x2ECC71,
                timestamp=datetime.now()
            )

            if self.project_monitor and self.project_monitor.projects:
                online_count = sum(1 for p in self.project_monitor.projects.values() if p.is_online)
                total_count = len(self.project_monitor.projects)
                all_online = online_count == total_count

                # Gesamtstatus
                if all_online:
                    embed.color = 0x2ECC71  # Grün
                    embed.description = f"✅ **Alle {total_count} Projekte online**"
                else:
                    embed.color = 0xE74C3C  # Rot
                    embed.description = f"⚠️ **{online_count}/{total_count} Projekte online**"

                # Pro Projekt ein Feld
                for project in sorted(self.project_monitor.projects.values(), key=lambda p: p.name):
                    tag = self.config.projects.get(project.name, {}).get('tag', '')
                    status_emoji = "🟢" if project.is_online else "🔴"
                    status_text = "Online" if project.is_online else "Offline"

                    # Details
                    details = []
                    if project.is_online:
                        details.append(f"Antwortzeit: {project.average_response_time:.0f}ms")
                    else:
                        if project.current_downtime_duration:
                            mins = int(project.current_downtime_duration.total_seconds() / 60)
                            if mins < 60:
                                details.append(f"Downtime: {mins}min")
                            else:
                                details.append(f"Downtime: {mins // 60}h {mins % 60}min")
                        if project.last_error:
                            details.append(f"Fehler: {project.last_error[:80]}")

                    uptime = f"{project.uptime_percentage:.1f}%" if project.total_checks > 0 else "—"
                    details.append(f"Uptime: {uptime}")

                    if project.last_check_time:
                        details.append(f"Letzter Check: <t:{int(project.last_check_time.timestamp())}:R>")

                    field_name = f"{status_emoji} {tag} {project.name}" if tag else f"{status_emoji} {project.name}"
                    embed.add_field(
                        name=field_name,
                        value=f"**{status_text}**\n" + "\n".join(details),
                        inline=True
                    )
                # Health-Snapshots in Knowledge DB speichern
                try:
                    from integrations.ai_learning.knowledge_db import get_knowledge_db
                    db = get_knowledge_db()
                    for project in self.project_monitor.projects.values():
                        db.add_health_snapshot(
                            project_name=project.name,
                            is_online=project.is_online,
                            response_time_ms=project.average_response_time if project.is_online else None,
                            uptime_pct=project.uptime_percentage if project.total_checks > 0 else None,
                            error=project.last_error if not project.is_online else None
                        )
                except Exception:
                    pass  # KB nicht verfügbar — kein Problem

            else:
                embed.description = "⏳ Project Monitor noch nicht initialisiert..."

            embed.set_footer(text="Aktualisiert alle 5 Minuten")

            # Suche nach existierendem Bot-Embed zum Editieren
            dashboard_message = None

            # 1. Prüfe gepinnte Nachrichten
            try:
                async for pin in channel.pins():
                    if pin.author.id == self.user.id and pin.embeds and pin.embeds[0].title and "Projekt-Dashboard" in pin.embeds[0].title:
                        dashboard_message = pin
                        break
            except Exception:
                pass

            # 2. Fallback: Letzte Bot-Nachricht im Channel suchen
            if not dashboard_message:
                try:
                    async for msg in channel.history(limit=10):
                        if msg.author.id == self.user.id and msg.embeds and msg.embeds[0].title and "Projekt-Dashboard" in msg.embeds[0].title:
                            dashboard_message = msg
                            break
                except Exception:
                    pass

            if dashboard_message:
                await dashboard_message.edit(embed=embed)
            else:
                msg = await channel.send(embed=embed)
                try:
                    await msg.pin()
                except discord.Forbidden:
                    pass  # Kein Pin möglich, Embed wird trotzdem editiert beim nächsten Lauf

        except Exception as e:
            self.logger.error(f"❌ Fehler beim Dashboard-Update: {e}", exc_info=True)

    @update_dashboard.before_loop
    async def before_dashboard(self):
        """Warte bis Bot bereit ist"""
        await self.wait_until_ready()
        # Warte bis Project Monitor initialisiert ist
        await asyncio.sleep(30)
        self.logger.info("📊 Dashboard-Task gestartet (aktualisiert alle 5 Minuten)")

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

        # bot.run() nutzt intern asyncio.run() mit async with (garantiertes close()).
        # SIGTERM wird im setup_hook() via Event-Loop-Signal-Handler behandelt,
        # damit bot.close() die HTTP-Server-Sockets sauber freigibt.
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


if __name__ == "__main__":
    main()
