"""
Cog for bot administration and management commands.
"""

import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime

class AdminCog(commands.Cog):
    """
    Contains slash commands for managing the bot and its systems.
    """

    def __init__(self, bot):
        self.bot = bot
        self.logger = bot.logger

    @app_commands.command(name="scan", description="Trigger manuellen Docker Security Scan")
    @app_commands.checks.has_permissions(administrator=True)
    async def scan_command(self, interaction: discord.Interaction):
        """Slash Command: /scan"""
        await interaction.response.defer()
        try:
            success = self.bot.docker.trigger_scan()
            if success:
                embed = discord.Embed(
                    title="🐳 Docker Security Scan gestartet",
                    description=("Der Scan läuft im Hintergrund und dauert einige Minuten.\n"
                                 "Ergebnisse werden automatisch gepostet."),
                    color=0x3498DB
                )
                await interaction.followup.send(embed=embed)
                self.logger.info(f"🔍 Docker Scan manuell getriggert von {interaction.user}")
            else:
                await interaction.followup.send("❌ Scan konnte nicht gestartet werden", ephemeral=True)
        except Exception as e:
            self.logger.error(f"❌ Fehler in /scan: {e}", exc_info=True)
            await interaction.followup.send("❌ Fehler beim Starten des Scans", ephemeral=True)

    @app_commands.command(name="stop-all-fixes", description="🛑 EMERGENCY: Stoppt alle laufenden Auto-Fixes sofort")
    @app_commands.checks.has_permissions(administrator=True)
    async def stop_all_fixes(self, interaction: discord.Interaction):
        """Emergency stop für Auto-Remediation"""
        try:
            await interaction.response.defer(ephemeral=True)

            if not self.bot.self_healing:
                await interaction.followup.send("ℹ️ Auto-Remediation ist nicht aktiv", ephemeral=True)
                return

            stopped_count = await self.bot.self_healing.stop_all_jobs()
            if self.bot.event_watcher:
                await self.bot.event_watcher.stop()

            self.logger.warning(f"🛑 EMERGENCY STOP ausgeführt von {interaction.user} - {stopped_count} Jobs gestoppt")
            embed = discord.Embed(
                title="🛑 Emergency Stop Executed",
                description=f"Alle Auto-Remediation Prozesse wurden gestoppt.",
                color=discord.Color.red()
            )
            embed.add_field(name="👤 Ausgeführt von", value=interaction.user.mention, inline=True)
            embed.add_field(name="📊 Gestoppte Jobs", value=str(stopped_count), inline=True)
            embed.add_field(name="🔄 Reaktivierung", value="Bot-Neustart erforderlich: `sudo systemctl restart shadowops-bot`", inline=False)
            embed.timestamp = datetime.now()
            await interaction.followup.send(embed=embed, ephemeral=True)
            
            alerts_channel_id = self.bot.config.alerts_channel
            if alerts_channel_id:
                channel = self.bot.get_channel(alerts_channel_id)
                if channel:
                    await channel.send(embed=embed)
        except Exception as e:
            self.logger.error(f"❌ Fehler in /stop-all-fixes: {e}", exc_info=True)
            await interaction.followup.send("❌ Fehler beim Stoppen der Auto-Fixes", ephemeral=True)

    @app_commands.command(name="remediation-stats", description="📊 Zeigt Auto-Remediation Statistiken an")
    @app_commands.checks.has_permissions(administrator=True)
    async def remediation_stats(self, interaction: discord.Interaction):
        """Zeigt Auto-Remediation Statistiken"""
        try:
            await interaction.response.defer(ephemeral=False)
            if not self.bot.self_healing or not self.bot.event_watcher:
                await interaction.followup.send("ℹ️ Auto-Remediation ist nicht aktiv", ephemeral=True)
                return

            healing_stats = self.bot.self_healing.get_statistics()
            watcher_stats = self.bot.event_watcher.get_statistics()

            embed = discord.Embed(
                title="📊 Auto-Remediation Statistics",
                description="Aktuelle Statistiken des Event-Driven Auto-Remediation Systems",
                color=discord.Color.blue()
            )
            embed.add_field(name="🔍 Event Watcher", value=f"Status: {'🟢 Running' if watcher_stats['running'] else '🔴 Stopped'}\nTotal Scans: {watcher_stats['total_scans']}\nTotal Events: {watcher_stats['total_events']}", inline=False)
            
            success_rate = 0
            if healing_stats['successful'] + healing_stats['failed'] > 0:
                success_rate = (healing_stats['successful'] / (healing_stats['successful'] + healing_stats['failed'])) * 100
            
            embed.add_field(name="🔧 Self-Healing Coordinator", value=f"Total Jobs: {healing_stats['total_jobs']}\n✅ Successful: {healing_stats['successful']}\n❌ Failed: {healing_stats['failed']}\n📈 Success Rate: {success_rate:.1f}%", inline=False)
            embed.timestamp = datetime.now()
            embed.set_footer(text="Auto-Remediation System")
            await interaction.followup.send(embed=embed)
        except Exception as e:
            self.logger.error(f"❌ Fehler in /remediation-stats: {e}", exc_info=True)
            await interaction.followup.send("❌ Fehler beim Abrufen der Statistiken", ephemeral=True)

    @app_commands.command(name="set-approval-mode", description="⚙️ Ändere Auto-Remediation Approval Mode")
    @app_commands.describe(mode="paranoid (Frage immer) | auto (Nur bei CRITICAL) | dry-run (Nur Logs)")
    @app_commands.checks.has_permissions(administrator=True)
    async def set_approval_mode_command(self, interaction: discord.Interaction, mode: str):
        """Ändert den Approval Mode für Auto-Remediation"""
        try:
            await interaction.response.defer(ephemeral=False)
            valid_modes = ['paranoid', 'auto', 'dry-run']
            if mode not in valid_modes:
                await interaction.followup.send(f"❌ Ungültiger Modus: `{mode}`\nErlaubte Modi: `{'`, `'.join(valid_modes)}`", ephemeral=True)
                return

            self.bot.config.auto_remediation['approval_mode'] = mode
            embed = discord.Embed(title="⚙️ Approval Mode geändert", color=0x00FF00, timestamp=datetime.now())
            mode_descriptions = {
                'paranoid': '🔒 Paranoid - Frage bei JEDEM Event (höchste Sicherheit)',
                'auto': '⚡ Auto - Nur bei CRITICAL fragen, andere automatisch',
                'dry-run': '🧪 Dry-Run - Keine Execution, nur Logs (Test-Modus)'
            }
            embed.add_field(name="Neuer Modus", value=mode_descriptions[mode], inline=False)
            embed.add_field(
                name="⚠️ Hinweis",
                value=("Änderung gilt ab sofort für neue Events.\n"
                       "Config-File wird nicht automatisch gespeichert."),
                inline=False
            )
            embed.set_footer(text=f"Geändert von {interaction.user.name}")
            self.logger.info(f"✅ Approval Mode geändert: {mode} (von {interaction.user.name})")
            await interaction.followup.send(embed=embed)
        except Exception as e:
            self.logger.error(f"❌ Fehler in /set-approval-mode: {e}", exc_info=True)
            await interaction.followup.send("❌ Fehler beim Ändern des Approval Mode", ephemeral=True)

    @app_commands.command(name="reload-context", description="🔄 Lade Project-Context neu")
    @app_commands.checks.has_permissions(administrator=True)
    async def reload_context_command(self, interaction: discord.Interaction):
        """Lädt alle Context-Files neu"""
        try:
            await interaction.response.defer(ephemeral=False)
            if hasattr(self.bot, 'context_manager') and self.bot.context_manager:
                self.bot.context_manager.load_all_contexts()
                project_count = len(self.bot.context_manager.project_paths)
                embed = discord.Embed(title="🔄 Context Reloaded", description="Project-Context wurde erfolgreich neu geladen", color=0x00FF00, timestamp=datetime.now())
                embed.add_field(name="📁 Projects", value=f"{project_count} Projekte geladen", inline=True)
                embed.set_footer(text=f"Neu geladen von {interaction.user.name}")
                self.logger.info(f"✅ Context neu geladen (von {interaction.user.name})")
                await interaction.followup.send(embed=embed)
            else:
                await interaction.followup.send("⚠️ Context Manager nicht initialisiert", ephemeral=True)
        except Exception as e:
            self.logger.error(f"❌ Fehler in /reload-context: {e}", exc_info=True)
            await interaction.followup.send("❌ Fehler beim Neu-Laden des Context", ephemeral=True)


async def setup(bot):
    await bot.add_cog(AdminCog(bot))