"""
Cog for commands that inspect the bot's internal state, like AI and project status.
"""

import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime

class InspectorCog(commands.Cog):
    """
    Contains slash commands for inspecting internal systems.
    """

    def __init__(self, bot):
        self.bot = bot
        self.logger = bot.logger

    @app_commands.command(name="get-ai-stats", description="🤖 Zeige AI-Provider Status und Statistiken")
    async def get_ai_stats_command(self, interaction: discord.Interaction):
        """Zeigt AI-Provider Status und Performance-Statistiken"""
        await interaction.response.defer(ephemeral=False)
        try:
            if not getattr(self.bot, 'ai_service', None):
                await interaction.followup.send("⏸️ AI ist deaktiviert", ephemeral=True)
                return

            embed = discord.Embed(
                title="🤖 AI Provider Status",
                description="Übersicht über alle konfigurierten AI-Provider",
                color=0x5865F2,
                timestamp=datetime.now()
            )
            # Codex (Primary)
            if hasattr(self.bot.ai_service, 'codex_provider'):
                codex = self.bot.ai_service.codex_provider
                codex_info = f"Status: 🟢 Primary\nModelle: `{codex.models}`"
                embed.add_field(name="⚡ Codex CLI (Primary)", value=codex_info, inline=False)
            # Claude (Fallback)
            if hasattr(self.bot.ai_service, 'claude_provider'):
                claude = self.bot.ai_service.claude_provider
                claude_info = f"Status: 🟡 Fallback\nModelle: `{claude.models}`\nCLI: `{claude.cli_path}`"
                embed.add_field(name="🧠 Claude CLI (Fallback)", value=claude_info, inline=False)
            # Stats
            if hasattr(self.bot.ai_service, 'stats'):
                stats = self.bot.ai_service.get_stats()
                stats_info = (
                    f"Codex: {stats.get('codex_success', 0)}/{stats.get('codex_calls', 0)} erfolgreich\n"
                    f"Claude: {stats.get('claude_success', 0)}/{stats.get('claude_calls', 0)} erfolgreich"
                )
                embed.add_field(name="📊 Engine Stats", value=stats_info, inline=False)

            await interaction.followup.send(embed=embed)
        except Exception as e:
            self.logger.error(f"❌ Fehler in /get-ai-stats: {e}", exc_info=True)
            await interaction.followup.send("❌ Fehler beim Abrufen der AI-Statistiken", ephemeral=True)

    @app_commands.command(name="projekt-status", description="📊 Zeige Status für ein bestimmtes Projekt")
    @app_commands.describe(name="Name des Projekts (z.B. shadowops-bot, guildscout)")
    async def projekt_status_command(self, interaction: discord.Interaction, name: str):
        """Zeigt detaillierten Status für ein spezifisches Projekt"""
        await interaction.response.defer(ephemeral=False)
        try:
            if not hasattr(self.bot, 'project_monitor') or not self.bot.project_monitor:
                await interaction.followup.send("⚠️ Project Monitor nicht verfügbar", ephemeral=True)
                return

            status = self.bot.project_monitor.get_project_status(name)
            if not status:
                await interaction.followup.send(f"❌ Projekt '{name}' nicht gefunden.", ephemeral=True)
                return

            is_online = status['is_online']
            status_emoji = "🟢" if is_online else "🔴"
            embed = discord.Embed(title=f"{status_emoji} {status['name']} - Status", color=discord.Color.green() if is_online else discord.Color.red(), timestamp=datetime.now())
            embed.add_field(name="🔌 Status", value=f"{status_emoji} {'Online' if is_online else 'Offline'}", inline=True)
            embed.add_field(name="📈 Uptime", value=f"{status['uptime_percentage']:.2f}%", inline=True)
            
            if is_online:
                embed.add_field(name="⚡ Avg Response", value=f"{status['average_response_time_ms']:.0f}ms", inline=True)
            
            if not is_online and status['last_error']:
                embed.add_field(name="⚠️ Last Error", value=f"```{{status['last_error'][:200]}}...```", inline=False)

            await interaction.followup.send(embed=embed)
        except Exception as e:
            self.logger.error(f"❌ Fehler in /projekt-status: {e}", exc_info=True)
            await interaction.followup.send("❌ Fehler beim Abrufen des Projekt-Status", ephemeral=True)

    @app_commands.command(name="alle-projekte", description="📋 Zeige Übersicht aller überwachten Projekte")
    async def alle_projekte_command(self, interaction: discord.Interaction):
        """Zeigt Status-Übersicht für alle Projekte"""
        await interaction.response.defer(ephemeral=False)
        try:
            if not hasattr(self.bot, 'project_monitor') or not self.bot.project_monitor:
                await interaction.followup.send("⚠️ Project Monitor nicht verfügbar", ephemeral=True)
                return

            all_statuses = self.bot.project_monitor.get_all_projects_status()
            if not all_statuses:
                await interaction.followup.send("ℹ️ Keine Projekte werden derzeit überwacht", ephemeral=True)
                return

            online_count = sum(1 for s in all_statuses if s['is_online'])
            total_count = len(all_statuses)
            color = discord.Color.green() if online_count == total_count else (discord.Color.red() if online_count == 0 else discord.Color.orange())
            
            embed = discord.Embed(title="📋 Alle Projekte - Status-Übersicht", description=f"🟢 **{online_count}** Online | 🔴 **{total_count - online_count}** Offline", color=color, timestamp=datetime.now())

            for status in sorted(all_statuses, key=lambda s: (not s['is_online'], s['name'].lower())):
                status_emoji = "🟢" if status['is_online'] else "🔴"
                value = f"Uptime: {status['uptime_percentage']:.1f}%"
                if status['is_online']:
                     value += f" | Avg Resp: {status['average_response_time_ms']:.0f}ms"
                embed.add_field(name=f"{status_emoji} **{status['name']}**", value=value, inline=True)
            
            await interaction.followup.send(embed=embed)
        except Exception as e:
            self.logger.error(f"❌ Fehler in /alle-projekte: {e}", exc_info=True)
            await interaction.followup.send("❌ Fehler beim Abrufen der Projekt-Übersicht", ephemeral=True)


async def setup(bot):
    await bot.add_cog(InspectorCog(bot))
