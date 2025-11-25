"""
Customer Server Setup Commands
Manual channel setup for customer servers
"""

import discord
from discord import app_commands
from discord.ext import commands
import logging

logger = logging.getLogger('shadowops')


class CustomerSetupCommands(commands.Cog):
    """Commands for customer server setup"""

    def __init__(self, bot):
        self.bot = bot
        self.logger = logger

    @app_commands.command(
        name="setup-customer-server",
        description="🔧 Setup monitoring channels for GuildScout (Admin only)"
    )
    @app_commands.default_permissions(administrator=True)
    async def setup_customer_server(self, interaction: discord.Interaction):
        """Manually trigger customer server setup"""

        # Defer response (setup can take a few seconds)
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild

        if not guild:
            await interaction.followup.send("❌ This command must be used in a server!", ephemeral=True)
            return

        self.logger.info(f"🔧 Manual setup triggered by {interaction.user} on {guild.name}")

        # Check if customer_server_setup is available
        if not hasattr(self.bot, 'customer_server_setup') or not self.bot.customer_server_setup:
            await interaction.followup.send(
                "❌ Customer server setup not initialized!\nPlease contact bot administrator.",
                ephemeral=True
            )
            return

        try:
            # Run setup for GuildScout
            channels = await self.bot.customer_server_setup.setup_customer_server(guild, 'guildscout')

            if not channels:
                await interaction.followup.send(
                    "❌ Setup failed! Check bot logs for details.\n"
                    "Possible issues:\n"
                    "• Bot missing Manage Channels permission\n"
                    "• Unable to create category\n"
                    "• Channels already exist",
                    ephemeral=True
                )
                return

            # Generate config snippet
            config_lines = [
                "```yaml",
                "projects:",
                "  guildscout:",
                "    external_notifications:"
            ]

            for channel_name, data in channels.items():
                config_lines.extend([
                    f"      - guild_id: {guild.id}",
                    f"        channel_id: {data['channel_id']}",
                    "        enabled: true",
                    "        notify_on:"
                ])
                for key, value in data['notify_on'].items():
                    config_lines.append(f"          {key}: {'true' if value else 'false'}")
                config_lines.append("")

            config_lines.append("```")

            # Success message
            embed = discord.Embed(
                title="✅ Setup Complete!",
                description=f"Created {len(channels)} channel(s) for GuildScout monitoring",
                color=discord.Color.green()
            )

            embed.add_field(
                name="Channels Created",
                value="\n".join(f"• <#{data['channel_id']}>" for data in channels.values()),
                inline=False
            )

            embed.add_field(
                name="Next Steps",
                value="1. Copy config snippet below\n"
                      "2. Add to `/home/cmdshadow/shadowops-bot/config/config.yaml`\n"
                      "3. Restart bot: `sudo systemctl restart shadowops-bot.service`",
                inline=False
            )

            await interaction.followup.send(embed=embed, ephemeral=True)
            await interaction.followup.send("\n".join(config_lines), ephemeral=True)

            self.logger.info(f"✅ Manual setup completed for {guild.name}")

        except Exception as e:
            self.logger.error(f"❌ Manual setup failed for {guild.name}: {e}", exc_info=True)
            await interaction.followup.send(
                f"❌ Setup failed with error:\n```{str(e)}```\nCheck bot logs for details.",
                ephemeral=True
            )


async def setup(bot):
    await bot.add_cog(CustomerSetupCommands(bot))
