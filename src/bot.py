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
from pathlib import Path
from datetime import datetime, time
from typing import Optional

# Füge src/ zum Path hinzu
sys.path.insert(0, str(Path(__file__).parent))

from utils.config import get_config
from utils.logger import setup_logger
from utils.embeds import EmbedBuilder, Severity

from integrations.fail2ban import Fail2banMonitor
from integrations.crowdsec import CrowdSecMonitor
from integrations.docker import DockerSecurityMonitor
from integrations.aide import AIDEMonitor
from integrations.event_watcher import SecurityEventWatcher
from integrations.self_healing import SelfHealingCoordinator


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

        # Rate Limiting für Alerts
        self.recent_alerts = {}

    async def _setup_auto_remediation_channels(self):
        """
        Erstellt automatisch Discord Channels für Auto-Remediation
        und speichert die Channel-IDs in der Config
        """
        try:
            self.logger.info("🔧 Prüfe Auto-Remediation Channels...")

            # Hole Guild
            guild = self.get_guild(self.config.guild_id)
            if not guild:
                self.logger.error(f"❌ Guild {self.config.guild_id} nicht gefunden!")
                return

            # Channel-Namen aus Config
            channel_names = self.config.auto_remediation.get('channel_names', {})
            alerts_name = channel_names.get('alerts', '🤖-auto-remediation-alerts')
            approvals_name = channel_names.get('approvals', '✋-auto-remediation-approvals')
            stats_name = channel_names.get('stats', '📊-auto-remediation-stats')

            # Aktuelle Channel-IDs aus Config
            notifications = self.config.auto_remediation.get('notifications', {})

            channels_to_create = [
                ('alerts', alerts_name, notifications.get('alerts_channel')),
                ('approvals', approvals_name, notifications.get('approvals_channel')),
                ('stats', stats_name, notifications.get('stats_channel')),
            ]

            channels_created = False
            channel_ids = {}

            for channel_type, channel_name, current_id in channels_to_create:
                # Prüfe ob Channel bereits existiert (by ID)
                if current_id:
                    existing_channel = guild.get_channel(current_id)
                    if existing_channel:
                        self.logger.info(f"✅ Channel '{channel_name}' existiert bereits (ID: {current_id})")
                        channel_ids[f'{channel_type}_channel'] = current_id
                        continue

                # Prüfe ob Channel existiert (by name)
                existing_channel = discord.utils.get(guild.text_channels, name=channel_name)
                if existing_channel:
                    self.logger.info(f"✅ Channel '{channel_name}' gefunden (ID: {existing_channel.id})")
                    channel_ids[f'{channel_type}_channel'] = existing_channel.id
                    channels_created = True
                    continue

                # Channel existiert nicht → erstellen
                self.logger.info(f"📝 Erstelle Channel: {channel_name}")

                # Erstelle Channel mit passender Description
                descriptions = {
                    'alerts': '🤖 Live-Updates aller Auto-Remediation Fixes',
                    'approvals': '✋ Human-Approval Requests für kritische Fixes',
                    'stats': '📊 Tägliche Auto-Remediation Statistiken'
                }

                new_channel = await guild.create_text_channel(
                    name=channel_name,
                    topic=descriptions.get(channel_type, 'Auto-Remediation System'),
                    reason="Auto-Remediation System Setup"
                )

                self.logger.info(f"✅ Channel '{channel_name}' erstellt (ID: {new_channel.id})")
                channel_ids[f'{channel_type}_channel'] = new_channel.id
                channels_created = True

            # Update Config mit Channel-IDs
            if channels_created:
                self.logger.info("💾 Speichere Channel-IDs in Config...")
                await self._update_config_channel_ids(channel_ids)

                # Update runtime config
                if 'notifications' not in self.config.auto_remediation:
                    self.config.auto_remediation['notifications'] = {}
                self.config.auto_remediation['notifications'].update(channel_ids)

                self.logger.info("✅ Auto-Remediation Channels setup komplett!")
            else:
                self.logger.info("ℹ️ Alle Channels existieren bereits")

        except Exception as e:
            self.logger.error(f"❌ Fehler beim Setup der Auto-Remediation Channels: {e}", exc_info=True)

    async def _update_config_channel_ids(self, channel_ids: dict):
        """Schreibt Channel-IDs zurück in config.yaml"""
        try:
            import yaml
            from pathlib import Path

            config_path = Path(__file__).parent.parent / 'config' / 'config.yaml'

            # Lese aktuelle Config
            with open(config_path, 'r', encoding='utf-8') as f:
                config_data = yaml.safe_load(f)

            # Update notifications section
            if 'auto_remediation' in config_data:
                if 'notifications' not in config_data['auto_remediation']:
                    config_data['auto_remediation']['notifications'] = {}

                config_data['auto_remediation']['notifications'].update(channel_ids)

            # Schreibe zurück
            with open(config_path, 'w', encoding='utf-8') as f:
                yaml.dump(config_data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

            self.logger.info("✅ Config-Datei aktualisiert mit Channel-IDs")

        except Exception as e:
            self.logger.warning(f"⚠️ Konnte Config-Datei nicht aktualisieren: {e}")

    async def setup_hook(self):
        """Setup Hook - wird beim Start aufgerufen"""
        self.logger.info("🗡️ ShadowOps Bot startet...")

        # Sync Slash Commands mit Guild
        guild = discord.Object(id=self.config.guild_id)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)

        self.logger.info(f"✅ Slash Commands synchronisiert für Guild {self.config.guild_id}")

        # Auto-Create Channels für Auto-Remediation (falls aktiviert)
        if self.config.auto_remediation.get('enabled', False) and self.config.auto_remediation.get('auto_create_channels', False):
            await self._setup_auto_remediation_channels()

        # Initialisiere Auto-Remediation (falls aktiviert)
        if self.config.auto_remediation.get('enabled', False):
            self.logger.info("🤖 Auto-Remediation System wird initialisiert...")

            # Initialisiere Self-Healing
            self.self_healing = SelfHealingCoordinator(self, self.config)
            await self.self_healing.initialize(ai_service=None)  # AI Service später hinzufügen

            # Initialisiere Event Watcher
            self.event_watcher = SecurityEventWatcher(self, self.config)
            await self.event_watcher.initialize(
                trivy=self.docker,
                crowdsec=self.crowdsec,
                fail2ban=self.fail2ban,
                aide=self.aide
            )

            # Starte Auto-Remediation
            await self.self_healing.start()
            await self.event_watcher.start()

            self.logger.info("✅ Auto-Remediation System initialisiert")
        else:
            self.logger.info("ℹ️ Auto-Remediation deaktiviert (config: auto_remediation.enabled=false)")

        # Starte Background Tasks
        if not self.monitor_security.is_running():
            self.monitor_security.start()
            self.daily_health_check.start()

    async def on_ready(self):
        """Bot ist bereit"""
        self.logger.info(f"✅ Bot eingeloggt als {self.user}")
        self.logger.info(f"🖥️ Verbunden mit {len(self.guilds)} Server(n)")

        # Setze Status
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name=self.config.bot_status
            )
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


if __name__ == "__main__":
    main()
