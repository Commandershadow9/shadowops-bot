"""
Customer Server Setup for ShadowOps Bot
Automatically creates channels on customer servers with proper permissions
"""

import discord
import logging
from typing import Dict, Optional, List

logger = logging.getLogger('shadowops')


class CustomerServerSetup:
    """
    Handles automatic channel setup for customer Discord servers

    Creates channels in admin categories with proper permissions
    """

    def __init__(self, bot, config):
        """
        Initialize customer server setup

        Args:
            bot: Discord bot instance
            config: Configuration object
        """
        self.bot = bot
        self.config = config
        self.logger = logger

        # Channel setup config per project
        self.project_channels = {
            'guildscout': {
                'category_id': 1398982574923321494,  # 🚨 | ADMIN AREA
                'channels': [
                    {
                        'name': 'guildscout-updates',
                        'emoji': '📢',
                        'description': 'Git push updates and patch notes',
                        'notify_on': {
                            'git_push': True,
                            'offline': False,
                            'online': False,
                            'errors': False
                        }
                    },
                    {
                        'name': 'guildscout-status',
                        'emoji': '🔴',
                        'description': 'Bot status monitoring (online/offline)',
                        'notify_on': {
                            'git_push': False,
                            'offline': True,
                            'online': True,
                            'errors': True
                        }
                    }
                ]
            }
        }

    async def setup_customer_server(self, guild: discord.Guild, project_name: str) -> Dict[str, int]:
        """
        Setup channels on a customer server for a specific project

        Args:
            guild: Discord guild (server)
            project_name: Project to setup (e.g., 'guildscout')

        Returns:
            Dictionary mapping channel purpose to channel ID
        """
        if project_name not in self.project_channels:
            self.logger.warning(f"⚠️ No channel setup config for project: {project_name}")
            return {}

        setup_config = self.project_channels[project_name]
        category_id = setup_config['category_id']

        # Get category
        category = guild.get_channel(category_id)
        if not category or not isinstance(category, discord.CategoryChannel):
            self.logger.error(f"❌ Category {category_id} not found on {guild.name}")
            return {}

        self.logger.info(f"🔧 Setting up channels for {project_name} on {guild.name}")

        created_channels = {}

        for channel_config in setup_config['channels']:
            channel_name = f"{channel_config['emoji']}{channel_config['name']}"

            # Check if channel already exists
            existing_channel = discord.utils.get(guild.text_channels, name=channel_name)
            if existing_channel:
                self.logger.info(f"✅ Channel {channel_name} already exists, skipping")
                created_channels[channel_config['name']] = {
                    'channel_id': existing_channel.id,
                    'notify_on': channel_config['notify_on']
                }
                continue

            # Create channel with admin-only permissions
            try:
                # Get @everyone role
                everyone_role = guild.default_role

                # Find admin role (highest non-bot role with admin permissions)
                admin_role = None
                for role in sorted(guild.roles, key=lambda r: r.position, reverse=True):
                    if role.permissions.administrator and not role.is_bot_managed():
                        admin_role = role
                        break

                # Set permissions
                overwrites = {
                    everyone_role: discord.PermissionOverwrite(
                        read_messages=False,
                        send_messages=False
                    ),
                    guild.me: discord.PermissionOverwrite(
                        read_messages=True,
                        send_messages=True,
                        embed_links=True,
                        read_message_history=True
                    )
                }

                # Add admin role if found
                if admin_role:
                    overwrites[admin_role] = discord.PermissionOverwrite(
                        read_messages=True,
                        send_messages=True
                    )
                    self.logger.info(f"🔒 Admin role found: {admin_role.name}")
                else:
                    self.logger.warning(f"⚠️ No admin role found on {guild.name}, using @everyone deny")

                # Create channel
                channel = await category.create_text_channel(
                    name=channel_name,
                    topic=channel_config['description'],
                    overwrites=overwrites,
                    reason=f"ShadowOps Bot: Auto-setup for {project_name}"
                )

                self.logger.info(f"✅ Created channel: {channel_name} (ID: {channel.id})")

                # Send welcome message
                embed = discord.Embed(
                    title=f"{channel_config['emoji']} Channel Setup Complete",
                    description=channel_config['description'],
                    color=discord.Color.blue()
                )

                embed.add_field(
                    name="Notifications Enabled",
                    value=self._format_notify_config(channel_config['notify_on']),
                    inline=False
                )

                embed.set_footer(text="ShadowOps Bot - Multi-Guild Support")

                await channel.send(embed=embed)

                created_channels[channel_config['name']] = {
                    'channel_id': channel.id,
                    'notify_on': channel_config['notify_on']
                }

            except discord.Forbidden:
                self.logger.error(f"❌ Missing permissions to create channels on {guild.name}")
            except Exception as e:
                self.logger.error(f"❌ Failed to create channel {channel_name}: {e}", exc_info=True)

        # Log summary
        if created_channels:
            self.logger.info(f"🎉 Setup complete for {project_name} on {guild.name}:")
            for name, data in created_channels.items():
                self.logger.info(f"   - {name}: {data['channel_id']}")

        return created_channels

    def _format_notify_config(self, notify_on: Dict[str, bool]) -> str:
        """Format notify_on config for display"""
        enabled = []
        if notify_on.get('git_push'):
            enabled.append("📢 Git Push Updates")
        if notify_on.get('offline'):
            enabled.append("🔴 Offline Alerts")
        if notify_on.get('online'):
            enabled.append("🟢 Online Alerts")
        if notify_on.get('errors'):
            enabled.append("❌ Error Alerts")

        return "\n".join(f"• {item}" for item in enabled) if enabled else "None"

    async def update_config_with_channels(self, guild_id: int, project_name: str, channels: Dict[str, dict]):
        """
        Update project config with created channel IDs

        Args:
            guild_id: Discord guild ID
            project_name: Project name
            channels: Dictionary of created channels
        """
        # This will print the config to be added manually
        # (Automatic config file editing is risky)

        self.logger.info("=" * 60)
        self.logger.info("📋 ADD THIS TO config/config.yaml:")
        self.logger.info("=" * 60)
        self.logger.info(f"projects:")
        self.logger.info(f"  {project_name}:")
        self.logger.info(f"    external_notifications:")

        for channel_name, data in channels.items():
            self.logger.info(f"      - guild_id: {guild_id}")
            self.logger.info(f"        channel_id: {data['channel_id']}")
            self.logger.info(f"        enabled: true")
            self.logger.info(f"        notify_on:")
            for key, value in data['notify_on'].items():
                self.logger.info(f"          {key}: {'true' if value else 'false'}")
            self.logger.info("")

        self.logger.info("=" * 60)

    async def on_guild_join(self, guild: discord.Guild):
        """
        Called when bot joins a new guild

        Args:
            guild: Discord guild that was joined
        """
        self.logger.info(f"🎉 Joined new guild: {guild.name} (ID: {guild.id})")

        # Check if this is a customer server (not your dev server)
        # You can configure your dev server ID to skip auto-setup
        dev_server_ids = []  # Add your dev server ID here if needed

        if guild.id in dev_server_ids:
            self.logger.info(f"ℹ️ Skipping auto-setup for dev server: {guild.name}")
            return

        # For now, we'll setup GuildScout channels automatically
        # You can expand this to detect which project based on guild name, etc.

        self.logger.info(f"🔧 Starting automatic channel setup for {guild.name}")

        # Setup GuildScout channels (can be made configurable)
        channels = await self.setup_customer_server(guild, 'guildscout')

        if channels:
            await self.update_config_with_channels(guild.id, 'guildscout', channels)

            # Try to find system channel or first text channel to send setup message
            target_channel = guild.system_channel
            if not target_channel:
                target_channel = next((c for c in guild.text_channels if c.permissions_for(guild.me).send_messages), None)

            if target_channel:
                embed = discord.Embed(
                    title="✅ ShadowOps Bot Setup Complete",
                    description=f"Created {len(channels)} channel(s) for **GuildScout** monitoring",
                    color=discord.Color.green()
                )

                embed.add_field(
                    name="Channels Created",
                    value="\n".join(f"• <#{data['channel_id']}>" for data in channels.values()),
                    inline=False
                )

                embed.add_field(
                    name="Next Steps",
                    value="1. Check the new channels in 🚨 | ADMIN AREA\n"
                          "2. Notifications are now active\n"
                          "3. Admins have full access",
                    inline=False
                )

                embed.set_footer(text="ShadowOps Bot - Multi-Guild Support")

                try:
                    await target_channel.send(embed=embed)
                except:
                    pass
