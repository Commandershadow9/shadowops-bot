"""
Multi-Project Monitoring for ShadowOps Bot
Health checks, uptime tracking, and Discord dashboards
"""

import asyncio
import aiohttp
import logging
import json
import time
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from pathlib import Path
import discord

logger = logging.getLogger(__name__)


class ProjectStatus:
    """Represents the current status of a monitored project"""

    def __init__(self, name: str, config: Dict):
        self.name = name
        self.url = config.get('url', '')
        self.expected_status = config.get('expected_status', 200)
        self.check_interval = config.get('check_interval', 60)
        self.timeout = config.get('timeout', 10)

        # Current status
        self.is_online = False
        self.last_check_time: Optional[datetime] = None
        self.last_online_time: Optional[datetime] = None
        self.last_offline_time: Optional[datetime] = None
        self.current_downtime_start: Optional[datetime] = None

        # Historical data
        self.total_checks = 0
        self.successful_checks = 0
        self.failed_checks = 0
        self.response_times: List[float] = []  # Last 100 response times
        self.max_response_times = 100

        # Incident tracking
        self.consecutive_failures = 0
        self.last_error: Optional[str] = None

    @property
    def uptime_percentage(self) -> float:
        """Calculate uptime percentage"""
        if self.total_checks == 0:
            return 0.0
        return (self.successful_checks / self.total_checks) * 100

    @property
    def average_response_time(self) -> float:
        """Calculate average response time (ms)"""
        if not self.response_times:
            return 0.0
        return sum(self.response_times) / len(self.response_times)

    @property
    def current_downtime_duration(self) -> Optional[timedelta]:
        """Get current downtime duration if project is down"""
        if self.is_online or not self.current_downtime_start:
            return None
        return datetime.utcnow() - self.current_downtime_start

    def update_online(self, response_time_ms: float):
        """Update status when health check succeeds"""
        was_offline = not self.is_online

        self.is_online = True
        self.last_check_time = datetime.utcnow()
        self.last_online_time = datetime.utcnow()
        self.total_checks += 1
        self.successful_checks += 1
        self.consecutive_failures = 0
        self.current_downtime_start = None

        # Track response time
        self.response_times.append(response_time_ms)
        if len(self.response_times) > self.max_response_times:
            self.response_times.pop(0)

        return was_offline  # Return True if this was a recovery

    def update_offline(self, error: str):
        """Update status when health check fails"""
        was_online = self.is_online

        self.is_online = False
        self.last_check_time = datetime.utcnow()
        self.last_offline_time = datetime.utcnow()
        self.total_checks += 1
        self.failed_checks += 1
        self.consecutive_failures += 1
        self.last_error = error

        # Start downtime tracking
        if was_online:
            self.current_downtime_start = datetime.utcnow()

        return was_online  # Return True if this is a new incident

    def to_dict(self) -> Dict:
        """Serialize to dictionary"""
        return {
            'name': self.name,
            'is_online': self.is_online,
            'uptime_percentage': self.uptime_percentage,
            'total_checks': self.total_checks,
            'successful_checks': self.successful_checks,
            'failed_checks': self.failed_checks,
            'average_response_time_ms': self.average_response_time,
            'consecutive_failures': self.consecutive_failures,
            'last_check_time': self.last_check_time.isoformat() if self.last_check_time else None,
            'last_online_time': self.last_online_time.isoformat() if self.last_online_time else None,
            'last_offline_time': self.last_offline_time.isoformat() if self.last_offline_time else None,
            'current_downtime_minutes': int(self.current_downtime_duration.total_seconds() / 60) if self.current_downtime_duration else None,
            'last_error': self.last_error
        }


class ProjectMonitor:
    """
    Multi-project monitoring system

    Features:
    - Health checks for all configured projects
    - Uptime tracking and SLA calculation
    - Discord dashboard updates
    - Incident detection and alerting
    """

    def __init__(self, bot, config: Dict):
        """
        Initialize project monitor

        Args:
            bot: Discord bot instance
            config: Configuration dictionary with projects and channels
        """
        self.bot = bot
        self.config = config
        self.logger = logger

        # Project configurations
        self.projects: Dict[str, ProjectStatus] = {}
        self._load_projects()

        # Discord channels
        self.customer_status_channel_id = config.get('channels', {}).get('customer_status', 0)
        self.customer_alerts_channel_id = config.get('channels', {}).get('customer_alerts', 0)

        # Monitoring tasks
        self.monitor_tasks: Dict[str, asyncio.Task] = {}
        self.dashboard_task: Optional[asyncio.Task] = None
        self.dashboard_message_id: Optional[int] = None

        # Dashboard update interval (seconds)
        self.dashboard_update_interval = 300  # 5 minutes

        # Persistence
        self.state_file = Path('data/project_monitor_state.json')
        self.state_file.parent.mkdir(exist_ok=True)
        self._load_state()

        self.logger.info(f"🔧 Project Monitor initialized with {len(self.projects)} projects")

    def _load_projects(self):
        """Load project configurations from config"""
        projects_config = self.config.get('projects', {})

        for project_name, project_config in projects_config.items():
            if not project_config.get('enabled', False):
                continue

            # Check if project has monitoring config
            monitor_config = project_config.get('monitor', {})
            if not monitor_config.get('enabled', False):
                continue

            self.projects[project_name] = ProjectStatus(project_name, monitor_config)
            self.logger.info(f"✅ Loaded monitoring for project: {project_name}")

    def _load_state(self):
        """Load persisted monitoring state"""
        if not self.state_file.exists():
            return

        try:
            with open(self.state_file, 'r') as f:
                state = json.load(f)

            for project_name, project_state in state.items():
                if project_name not in self.projects:
                    continue

                project = self.projects[project_name]
                project.total_checks = project_state.get('total_checks', 0)
                project.successful_checks = project_state.get('successful_checks', 0)
                project.failed_checks = project_state.get('failed_checks', 0)

            self.logger.info(f"📂 Loaded monitoring state from {self.state_file}")

        except Exception as e:
            self.logger.error(f"❌ Error loading state: {e}", exc_info=True)

    def _save_state(self):
        """Persist monitoring state"""
        try:
            state = {}
            for project_name, project in self.projects.items():
                state[project_name] = {
                    'total_checks': project.total_checks,
                    'successful_checks': project.successful_checks,
                    'failed_checks': project.failed_checks
                }

            with open(self.state_file, 'w') as f:
                json.dump(state, f, indent=2)

        except Exception as e:
            self.logger.error(f"❌ Error saving state: {e}", exc_info=True)

    async def start_monitoring(self):
        """Start monitoring all configured projects"""
        for project_name, project in self.projects.items():
            task = asyncio.create_task(self._monitor_project(project))
            self.monitor_tasks[project_name] = task
            self.logger.info(f"🚀 Started monitoring: {project_name}")

        # Start dashboard updater
        self.dashboard_task = asyncio.create_task(self._update_dashboard_loop())

        self.logger.info(f"✅ Monitoring started for {len(self.projects)} projects")

    async def stop_monitoring(self):
        """Stop all monitoring tasks"""
        # Stop project monitors
        for project_name, task in self.monitor_tasks.items():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            self.logger.info(f"🛑 Stopped monitoring: {project_name}")

        # Stop dashboard updater
        if self.dashboard_task:
            self.dashboard_task.cancel()
            try:
                await self.dashboard_task
            except asyncio.CancelledError:
                pass

        # Save final state
        self._save_state()

        self.logger.info("🛑 All monitoring stopped")

    async def _monitor_project(self, project: ProjectStatus):
        """
        Monitor a single project continuously

        Args:
            project: ProjectStatus instance to monitor
        """
        while True:
            try:
                await self._check_project_health(project)
                await asyncio.sleep(project.check_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(
                    f"❌ Error monitoring {project.name}: {e}",
                    exc_info=True
                )
                await asyncio.sleep(project.check_interval)

    async def _check_project_health(self, project: ProjectStatus):
        """
        Perform health check for a project

        Args:
            project: ProjectStatus instance to check
        """
        if not project.url:
            self.logger.debug(f"ℹ️ No health check URL for {project.name}")
            return

        start_time = time.time()

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    project.url,
                    timeout=aiohttp.ClientTimeout(total=project.timeout)
                ) as response:
                    response_time_ms = (time.time() - start_time) * 1000

                    if response.status == project.expected_status:
                        # Health check succeeded
                        was_recovering = project.update_online(response_time_ms)

                        self.logger.info(
                            f"✅ {project.name} healthy "
                            f"({response.status}, {response_time_ms:.0f}ms)"
                        )

                        # Alert on recovery
                        if was_recovering:
                            await self._send_recovery_alert(project)

                    else:
                        # Unexpected status code
                        error = f"Status {response.status} (expected {project.expected_status})"
                        was_new_incident = project.update_offline(error)

                        self.logger.warning(f"⚠️ {project.name}: {error}")

                        # Alert on new incident
                        if was_new_incident:
                            await self._send_incident_alert(project, error)

        except asyncio.TimeoutError:
            error = f"Timeout after {project.timeout}s"
            was_new_incident = project.update_offline(error)

            self.logger.warning(f"⚠️ {project.name}: {error}")

            if was_new_incident:
                await self._send_incident_alert(project, error)

        except aiohttp.ClientError as e:
            error = f"Connection error: {str(e)}"
            was_new_incident = project.update_offline(error)

            self.logger.warning(f"⚠️ {project.name}: {error}")

            if was_new_incident:
                await self._send_incident_alert(project, error)

        except Exception as e:
            error = f"Unexpected error: {str(e)}"
            was_new_incident = project.update_offline(error)

            self.logger.error(f"❌ {project.name}: {error}", exc_info=True)

            if was_new_incident:
                await self._send_incident_alert(project, error)

        # Save state periodically
        self._save_state()

    async def _send_incident_alert(self, project: ProjectStatus, error: str):
        """Send Discord alert when project goes down"""
        channel = self.bot.get_channel(self.customer_alerts_channel_id)
        if not channel:
            return

        embed = discord.Embed(
            title=f"🔴 {project.name} is DOWN",
            description=f"Health check failed for **{project.name}**",
            color=discord.Color.red(),
            timestamp=datetime.utcnow()
        )

        embed.add_field(name="Project", value=project.name, inline=True)
        embed.add_field(name="Status", value="🔴 Offline", inline=True)
        embed.add_field(name="Consecutive Failures", value=str(project.consecutive_failures), inline=True)

        # Truncate error if too long
        if len(error) > 500:
            error = error[:497] + "..."
        embed.add_field(name="Error", value=f"```{error}```", inline=False)

        embed.add_field(
            name="Uptime (before incident)",
            value=f"{project.uptime_percentage:.2f}%",
            inline=True
        )

        await channel.send(embed=embed)
        self.logger.info(f"🚨 Sent incident alert for {project.name}")

    async def _send_recovery_alert(self, project: ProjectStatus):
        """Send Discord alert when project recovers"""
        channel = self.bot.get_channel(self.customer_alerts_channel_id)
        if not channel:
            return

        embed = discord.Embed(
            title=f"✅ {project.name} is BACK ONLINE",
            description=f"**{project.name}** has recovered",
            color=discord.Color.green(),
            timestamp=datetime.utcnow()
        )

        embed.add_field(name="Project", value=project.name, inline=True)
        embed.add_field(name="Status", value="🟢 Online", inline=True)
        embed.add_field(
            name="Response Time",
            value=f"{project.average_response_time:.0f}ms",
            inline=True
        )

        embed.add_field(
            name="Current Uptime",
            value=f"{project.uptime_percentage:.2f}%",
            inline=True
        )

        await channel.send(embed=embed)
        self.logger.info(f"✅ Sent recovery alert for {project.name}")

    async def _update_dashboard_loop(self):
        """Periodically update the dashboard message"""
        while True:
            try:
                await self._update_dashboard()
                await asyncio.sleep(self.dashboard_update_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"❌ Error updating dashboard: {e}", exc_info=True)
                await asyncio.sleep(self.dashboard_update_interval)

    async def _update_dashboard(self):
        """Update or create the dashboard message"""
        channel = self.bot.get_channel(self.customer_status_channel_id)
        if not channel:
            return

        embed = self._create_dashboard_embed()

        try:
            if self.dashboard_message_id:
                # Try to edit existing message
                message = await channel.fetch_message(self.dashboard_message_id)
                await message.edit(embed=embed)
            else:
                # Create new dashboard message
                message = await channel.send(embed=embed)
                self.dashboard_message_id = message.id

            self.logger.debug("📊 Dashboard updated")

        except discord.NotFound:
            # Message was deleted, create new one
            message = await channel.send(embed=embed)
            self.dashboard_message_id = message.id
            self.logger.info("📊 Created new dashboard message")

        except Exception as e:
            self.logger.error(f"❌ Error updating dashboard: {e}", exc_info=True)

    def _create_dashboard_embed(self) -> discord.Embed:
        """Create Discord embed for project dashboard"""
        embed = discord.Embed(
            title="📊 Project Status Dashboard",
            description="Real-time status of all monitored projects",
            color=discord.Color.blue(),
            timestamp=datetime.utcnow()
        )

        # Count online/offline projects
        online_count = sum(1 for p in self.projects.values() if p.is_online)
        total_count = len(self.projects)

        embed.add_field(
            name="Overview",
            value=f"🟢 {online_count}/{total_count} projects online",
            inline=False
        )

        # List each project
        for project in sorted(self.projects.values(), key=lambda p: p.name):
            status_emoji = "🟢" if project.is_online else "🔴"
            uptime = f"{project.uptime_percentage:.1f}%"
            response_time = f"{project.average_response_time:.0f}ms" if project.is_online else "N/A"

            value_parts = [
                f"Status: {status_emoji} {'Online' if project.is_online else 'Offline'}",
                f"Uptime: {uptime}",
                f"Avg Response: {response_time}"
            ]

            if not project.is_online and project.current_downtime_duration:
                downtime_minutes = int(project.current_downtime_duration.total_seconds() / 60)
                value_parts.append(f"Downtime: {downtime_minutes}m")

            embed.add_field(
                name=f"**{project.name}**",
                value="\n".join(value_parts),
                inline=True
            )

        embed.set_footer(text=f"Last updated")

        return embed

    def get_project_status(self, project_name: str) -> Optional[Dict]:
        """
        Get current status for a specific project

        Args:
            project_name: Name of the project

        Returns:
            Status dictionary or None if project not found
        """
        project = self.projects.get(project_name)
        if not project:
            return None

        return project.to_dict()

    def get_all_projects_status(self) -> List[Dict]:
        """
        Get status for all projects

        Returns:
            List of status dictionaries
        """
        return [project.to_dict() for project in self.projects.values()]
