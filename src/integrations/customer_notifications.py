"""
Customer-Facing Notifications for ShadowOps Bot
Filters and formats messages for customer visibility
"""

import logging
from typing import Dict, Optional
from datetime import datetime
import discord

logger = logging.getLogger(__name__)


class CustomerNotificationManager:
    """
    Manages customer-facing notifications

    Features:
    - Filters technical details from alerts
    - Creates user-friendly embeds
    - Routes important events to customer channels
    - Maintains professional communication style
    """

    def __init__(self, bot, config: Dict):
        """
        Initialize customer notification manager

        Args:
            bot: Discord bot instance
            config: Configuration dictionary with channel settings
        """
        self.bot = bot
        self.config = config
        self.logger = logger

        # Discord channels
        self.customer_alerts_id = config.channels.get('customer_alerts', 0)
        self.customer_status_id = config.channels.get('customer_status', 0)
        self.deployment_log_id = config.channels.get('deployment_log', 0)

        # Severity thresholds for customer notifications
        customer_notif_config = getattr(config, 'customer_notifications', {})
        if isinstance(customer_notif_config, dict):
            self.min_severity_for_alert = customer_notif_config.get('min_severity', 'HIGH')
        else:
            self.min_severity_for_alert = getattr(customer_notif_config, 'min_severity', 'HIGH')

        # Severity levels (ordered)
        self.severity_order = ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL']

        self.logger.info("🔧 Customer Notification Manager initialized")

    def should_notify_customer(self, severity: str, event_type: str) -> bool:
        """
        Determine if an event should be sent to customers

        Args:
            severity: Event severity level
            event_type: Type of event

        Returns:
            True if customer should be notified
        """
        # Always notify about incidents and deployments
        if event_type in ['incident', 'deployment', 'recovery']:
            return True

        # Check severity threshold
        try:
            event_severity_level = self.severity_order.index(severity.upper())
            min_severity_level = self.severity_order.index(self.min_severity_for_alert.upper())

            return event_severity_level >= min_severity_level

        except (ValueError, AttributeError):
            # Unknown severity, don't notify
            return False

    async def send_security_alert(
        self, title: str, description: str, severity: str,
        details: Optional[Dict] = None
    ):
        """
        Send customer-friendly security alert

        Args:
            title: Alert title
            description: User-friendly description
            severity: Severity level
            details: Optional additional details
        """
        if not self.should_notify_customer(severity, 'security'):
            return

        channel = self.bot.get_channel(self.customer_alerts_id)
        if not channel:
            return

        # Create customer-friendly embed
        embed = self._create_security_embed(title, description, severity, details)

        try:
            await channel.send(embed=embed)
            self.logger.info(f"📢 Customer security alert sent: {title}")

        except Exception as e:
            self.logger.error(f"❌ Failed to send customer alert: {e}", exc_info=True)

    async def send_incident_alert(
        self, project: str, issue: str, impact: str,
        status: str = "Investigating"
    ):
        """
        Send customer-facing incident alert

        Args:
            project: Affected project name
            issue: Description of the issue
            impact: Customer impact description
            status: Current incident status
        """
        channel = self.bot.get_channel(self.customer_alerts_id)
        if not channel:
            return

        embed = discord.Embed(
            title=f"🚨 Incident Report: {project}",
            description=issue,
            color=discord.Color.red(),
            timestamp=datetime.utcnow()
        )

        embed.add_field(
            name="📊 Status",
            value=f"🔍 {status}",
            inline=True
        )

        embed.add_field(
            name="🎯 Affected Service",
            value=project,
            inline=True
        )

        embed.add_field(
            name="💼 Impact",
            value=impact,
            inline=False
        )

        embed.add_field(
            name="ℹ️ What we're doing",
            value="Our team is investigating and working to resolve this issue as quickly as possible. Updates will be posted here.",
            inline=False
        )

        embed.set_footer(text="ShadowOps Security Team")

        try:
            await channel.send(embed=embed)
            self.logger.info(f"📢 Customer incident alert sent: {project}")

        except Exception as e:
            self.logger.error(f"❌ Failed to send incident alert: {e}", exc_info=True)

    async def send_recovery_notification(
        self, project: str, issue: str, resolution: str,
        downtime_minutes: Optional[int] = None
    ):
        """
        Send recovery notification to customers

        Args:
            project: Recovered project name
            issue: What was the issue
            resolution: How it was resolved
            downtime_minutes: Optional downtime duration
        """
        channel = self.bot.get_channel(self.customer_alerts_id)
        if not channel:
            return

        embed = discord.Embed(
            title=f"✅ Resolved: {project}",
            description=f"The issue affecting **{project}** has been resolved.",
            color=discord.Color.green(),
            timestamp=datetime.utcnow()
        )

        embed.add_field(
            name="📊 Status",
            value="✅ Resolved",
            inline=True
        )

        embed.add_field(
            name="🎯 Service",
            value=project,
            inline=True
        )

        if downtime_minutes is not None:
            if downtime_minutes < 60:
                downtime_str = f"{downtime_minutes} minutes"
            else:
                hours = downtime_minutes // 60
                minutes = downtime_minutes % 60
                downtime_str = f"{hours}h {minutes}m"

            embed.add_field(
                name="⏱️ Downtime",
                value=downtime_str,
                inline=True
            )

        embed.add_field(
            name="🔧 Issue",
            value=issue,
            inline=False
        )

        embed.add_field(
            name="✅ Resolution",
            value=resolution,
            inline=False
        )

        embed.set_footer(text="Thank you for your patience • ShadowOps Team")

        try:
            await channel.send(embed=embed)
            self.logger.info(f"📢 Customer recovery notification sent: {project}")

        except Exception as e:
            self.logger.error(f"❌ Failed to send recovery notification: {e}", exc_info=True)

    async def send_deployment_notification(
        self, project: str, version: Optional[str] = None,
        changes: Optional[str] = None, downtime_expected: bool = False
    ):
        """
        Send deployment notification to customers

        Args:
            project: Project being deployed
            version: Optional version number
            changes: Customer-facing change description
            downtime_expected: Whether downtime is expected
        """
        channel = self.bot.get_channel(self.deployment_log_id)
        if not channel:
            # Fallback to customer status channel
            channel = self.bot.get_channel(self.customer_status_id)

        if not channel:
            return

        title = f"🚀 Deployment: {project}"
        if version:
            title += f" v{version}"

        embed = discord.Embed(
            title=title,
            description=f"We're deploying an update to **{project}**",
            color=discord.Color.blue(),
            timestamp=datetime.utcnow()
        )

        embed.add_field(
            name="📊 Status",
            value="🚀 Deploying",
            inline=True
        )

        embed.add_field(
            name="🎯 Service",
            value=project,
            inline=True
        )

        if version:
            embed.add_field(
                name="📦 Version",
                value=f"v{version}",
                inline=True
            )

        if downtime_expected:
            embed.add_field(
                name="⚠️ Service Impact",
                value="Brief service interruption expected during deployment",
                inline=False
            )
        else:
            embed.add_field(
                name="✅ Service Impact",
                value="No downtime expected - rolling deployment",
                inline=False
            )

        if changes:
            embed.add_field(
                name="📋 What's New",
                value=changes,
                inline=False
            )

        embed.set_footer(text="ShadowOps Deployment Team")

        try:
            await channel.send(embed=embed)
            self.logger.info(f"📢 Customer deployment notification sent: {project}")

        except Exception as e:
            self.logger.error(f"❌ Failed to send deployment notification: {e}", exc_info=True)

    async def send_status_update(self, message: str, status_type: str = "info"):
        """
        Send general status update to customers

        Args:
            message: Status message
            status_type: Type of status (info, warning, success)
        """
        channel = self.bot.get_channel(self.customer_status_id)
        if not channel:
            return

        # Color based on type
        color_map = {
            'info': discord.Color.blue(),
            'warning': discord.Color.orange(),
            'success': discord.Color.green(),
            'error': discord.Color.red()
        }

        color = color_map.get(status_type, discord.Color.blue())

        # Emoji based on type
        emoji_map = {
            'info': 'ℹ️',
            'warning': '⚠️',
            'success': '✅',
            'error': '❌'
        }

        emoji = emoji_map.get(status_type, 'ℹ️')

        embed = discord.Embed(
            title=f"{emoji} Status Update",
            description=message,
            color=color,
            timestamp=datetime.utcnow()
        )

        embed.set_footer(text="ShadowOps Team")

        try:
            await channel.send(embed=embed)
            self.logger.info(f"📢 Customer status update sent ({status_type})")

        except Exception as e:
            self.logger.error(f"❌ Failed to send status update: {e}", exc_info=True)

    def _create_security_embed(
        self, title: str, description: str, severity: str,
        details: Optional[Dict] = None
    ) -> discord.Embed:
        """
        Create customer-friendly security alert embed

        Args:
            title: Alert title
            description: User-friendly description
            severity: Severity level
            details: Optional additional details

        Returns:
            Discord Embed
        """
        # Color based on severity
        severity_colors = {
            'CRITICAL': discord.Color.dark_red(),
            'HIGH': discord.Color.red(),
            'MEDIUM': discord.Color.orange(),
            'LOW': discord.Color.yellow()
        }

        color = severity_colors.get(severity.upper(), discord.Color.blue())

        # Severity emoji
        severity_emojis = {
            'CRITICAL': '🔴',
            'HIGH': '🟠',
            'MEDIUM': '🟡',
            'LOW': '🟢'
        }

        emoji = severity_emojis.get(severity.upper(), 'ℹ️')

        embed = discord.Embed(
            title=f"{emoji} Security Alert: {title}",
            description=description,
            color=color,
            timestamp=datetime.utcnow()
        )

        embed.add_field(
            name="📊 Severity",
            value=f"{emoji} {severity.upper()}",
            inline=True
        )

        embed.add_field(
            name="📅 Detected",
            value=f"<t:{int(datetime.utcnow().timestamp())}:R>",
            inline=True
        )

        if details:
            # Filter and add customer-relevant details
            if 'affected_systems' in details:
                embed.add_field(
                    name="🎯 Affected Systems",
                    value=details['affected_systems'],
                    inline=False
                )

            if 'action_taken' in details:
                embed.add_field(
                    name="✅ Action Taken",
                    value=details['action_taken'],
                    inline=False
                )

            if 'customer_impact' in details:
                embed.add_field(
                    name="💼 Impact",
                    value=details['customer_impact'],
                    inline=False
                )

        embed.add_field(
            name="ℹ️ What's happening",
            value="Our automated security system detected and is addressing this issue. No action required from you.",
            inline=False
        )

        embed.set_footer(text="ShadowOps Security Team • 24/7 Monitoring")

        return embed

    async def send_maintenance_notification(
        self, project: str, start_time: datetime, duration_minutes: int,
        reason: str
    ):
        """
        Send planned maintenance notification

        Args:
            project: Project undergoing maintenance
            start_time: Scheduled start time
            duration_minutes: Expected duration
            reason: Reason for maintenance
        """
        channel = self.bot.get_channel(self.customer_status_id)
        if not channel:
            return

        embed = discord.Embed(
            title=f"🔧 Scheduled Maintenance: {project}",
            description=f"Planned maintenance window for **{project}**",
            color=discord.Color.blue(),
            timestamp=datetime.utcnow()
        )

        embed.add_field(
            name="🎯 Service",
            value=project,
            inline=True
        )

        embed.add_field(
            name="📅 Start Time",
            value=f"<t:{int(start_time.timestamp())}:F>",
            inline=True
        )

        if duration_minutes < 60:
            duration_str = f"{duration_minutes} minutes"
        else:
            hours = duration_minutes // 60
            minutes = duration_minutes % 60
            duration_str = f"{hours}h {minutes}m"

        embed.add_field(
            name="⏱️ Duration",
            value=duration_str,
            inline=True
        )

        embed.add_field(
            name="📋 Reason",
            value=reason,
            inline=False
        )

        embed.add_field(
            name="💼 Expected Impact",
            value="The service may be unavailable during this maintenance window.",
            inline=False
        )

        embed.set_footer(text="Thank you for your understanding • ShadowOps Team")

        try:
            await channel.send(embed=embed)
            self.logger.info(f"📢 Maintenance notification sent: {project}")

        except Exception as e:
            self.logger.error(f"❌ Failed to send maintenance notification: {e}", exc_info=True)
