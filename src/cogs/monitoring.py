"""
Cog for general monitoring commands.
"""

import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime

from utils.embeds import EmbedBuilder

class MonitoringCog(commands.Cog):
    """
    Contains slash commands for monitoring security status and tools.
    """

    def __init__(self, bot):
        self.bot = bot
        self.logger = bot.logger

    @app_commands.command(name="status", description="Zeige Security-Status-Übersicht")
    async def status_command(self, interaction: discord.Interaction):
        """Slash Command: /status"""
        await interaction.response.defer()
        try:
            # Fail2ban Status
            jail_stats = self.bot.fail2ban.get_jail_stats()
            total_bans = sum(s["currently_banned"] for s in jail_stats.values())

            # CrowdSec Status
            cs_active = self.bot.crowdsec.is_running()
            cs_metrics = self.bot.crowdsec.get_metrics()

            # Docker Scans
            docker_scan = self.bot.docker.get_scan_date()

            # AIDE
            aide_check = self.bot.aide.get_last_check_date()

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
            self.logger.error(f"❌ Fehler in /status: {e}", exc_info=True)
            await interaction.followup.send("❌ Fehler beim Abrufen des Status", ephemeral=True)

    @app_commands.command(name="bans", description="Zeige aktuell gebannte IPs")
    @app_commands.describe(limit="Maximale Anzahl (Standard: 10)")
    async def bans_command(self, interaction: discord.Interaction, limit: int = 10):
        """Slash Command: /bans"""
        await interaction.response.defer()
        try:
            # Fail2ban Bans
            f2b_bans = self.bot.fail2ban.get_banned_ips()

            # CrowdSec Decisions
            cs_decisions = self.bot.crowdsec.get_active_decisions(limit=limit)

            embed = discord.Embed(
                title="🚫 Aktuell gebannte IP-Adressen",
                description=f"Zeige bis zu {limit} gebannte IPs",
                color=0xE74C3C,
                timestamp=datetime.utcnow()
            )

            if f2b_bans:
                f2b_text = ""
                for jail, ips in list(f2b_bans.items())[:5]:
                    f2b_text += f"**{jail}:** {len(ips)} IPs\n"
                    f2b_text += "```\n" + "\n".join(ips[:3]) + "\n```\n"
                embed.add_field(name="🛡️ Fail2ban", value=f2b_text or "Keine Bans", inline=False)

            if cs_decisions:
                cs_text = ""
                for dec in cs_decisions[:5]:
                    cs_text += f"`{dec['ip']}` - {dec['reason'][:50]}\n"
                embed.add_field(name="🤖 CrowdSec", value=cs_text, inline=False)

            embed.set_footer(text=f"Angefordert von {interaction.user}")
            await interaction.followup.send(embed=embed)
        except Exception as e:
            self.logger.error(f"❌ Fehler in /bans: {e}", exc_info=True)
            await interaction.followup.send("❌ Fehler beim Abrufen der Bans", ephemeral=True)

    @app_commands.command(name="threats", description="Zeige letzte erkannte Bedrohungen")
    @app_commands.describe(hours="Zeitraum in Stunden (Standard: 24)")
    async def threats_command(self, interaction: discord.Interaction, hours: int = 24):
        """Slash Command: /threats"""
        await interaction.response.defer()
        try:
            alerts = self.bot.crowdsec.get_recent_alerts(limit=20)
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
            self.logger.error(f"❌ Fehler in /threats: {e}", exc_info=True)
            await interaction.followup.send("❌ Fehler beim Abrufen der Bedrohungen", ephemeral=True)

    @app_commands.command(name="docker", description="Zeige letzte Docker Scan Ergebnisse")
    async def docker_command(self, interaction: discord.Interaction):
        """Slash Command: /docker"""
        await interaction.response.defer()
        try:
            results = self.bot.docker.get_latest_scan_results()
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
            self.logger.error(f"❌ Fehler in /docker: {e}", exc_info=True)
            await interaction.followup.send("❌ Fehler beim Abrufen der Scan-Ergebnisse", ephemeral=True)

    @app_commands.command(name="aide", description="Zeige AIDE Integrity Check Status")
    async def aide_command(self, interaction: discord.Interaction):
        """Slash Command: /aide"""
        await interaction.response.defer()
        try:
            results = self.bot.aide.get_last_check_results()
            last_check = self.bot.aide.get_last_check_date()
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
            self.logger.error(f"❌ Fehler in /aide: {e}", exc_info=True)
            await interaction.followup.send("❌ Fehler beim Abrufen des AIDE Status", ephemeral=True)


async def setup(bot):
    await bot.add_cog(MonitoringCog(bot))
