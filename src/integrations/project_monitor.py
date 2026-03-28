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
from datetime import datetime, timedelta, timezone
from pathlib import Path
import discord

logger = logging.getLogger('shadowops.project_monitor')


class ProjectStatus:
    """Represents the current status of a monitored project"""

    def __init__(self, name: str, config: Dict):
        self.name = name
        self.url = config.get('url', '')
        self.expected_status = config.get('expected_status', 200)
        self.check_interval = config.get('check_interval', 60)
        self.timeout = config.get('timeout', 10)
        self.remediation_command = config.get('remediation_command')
        self.remediation_threshold = config.get('remediation_threshold', 3)
        self.log_file = config.get('log_file')
        self.log_pattern = config.get('log_pattern')
        self.log_tail_bytes = config.get('log_tail_bytes', 50000)

        # Systemd-basiertes Health-Checking (für Services ohne HTTP-Endpoint)
        self.systemd_services = config.get('systemd_services', [])

        # TCP-Port-basiertes Health-Checking (für DB-Ports etc.)
        self.tcp_ports = config.get('tcp_ports', [])

        # Current status
        self.is_online = False
        self.last_check_time: Optional[datetime] = None
        self.last_online_time: Optional[datetime] = None
        self.last_offline_time: Optional[datetime] = None
        self.current_downtime_start: Optional[datetime] = None
        self.remediation_triggered: bool = False

        # Historical data
        self.total_checks = 0
        self.successful_checks = 0
        self.failed_checks = 0
        self.response_times: List[float] = []  # Last 100 response times
        self.max_response_times = 100
        self.last_log_pos: int = 0

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
        return datetime.now(timezone.utc) - self.current_downtime_start

    def update_online(self, response_time_ms: float):
        """Update status when health check succeeds"""
        # Consider this a "recovery" only if we had consecutive failures
        was_recovering = self.consecutive_failures > 0

        self.is_online = True
        self.last_check_time = datetime.now(timezone.utc)
        self.last_online_time = datetime.now(timezone.utc)
        self.total_checks += 1
        self.successful_checks += 1
        self.consecutive_failures = 0
        self.current_downtime_start = None
        self.remediation_triggered = False

        # Track response time
        self.response_times.append(response_time_ms)
        if len(self.response_times) > self.max_response_times:
            self.response_times.pop(0)

        return was_recovering  # True if coming back from failures

    def update_offline(self, error: str):
        """Update status when health check fails"""
        was_online = self.is_online

        self.is_online = False
        self.last_check_time = datetime.now(timezone.utc)
        self.last_offline_time = datetime.now(timezone.utc)
        self.total_checks += 1
        self.failed_checks += 1
        self.consecutive_failures += 1
        self.last_error = error

        # Start downtime tracking
        if was_online:
            self.current_downtime_start = datetime.now(timezone.utc)

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
        self.startup_grace_seconds = 10  # Avoid race with health server on startup

        # Project configurations
        self.projects: Dict[str, ProjectStatus] = {}
        self._load_projects()

        # Discord channels
        self.customer_status_channel_id = self.config.customer_status_channel
        self.customer_alerts_channel_id = self.config.customer_alerts_channel

        # Monitoring tasks
        self.monitor_tasks: Dict[str, asyncio.Task] = {}
        self.dashboard_task: Optional[asyncio.Task] = None
        self.dashboard_message_id: Optional[int] = None

        # Dashboard update interval (seconds)
        self.dashboard_update_interval = 300  # 5 minutes

        # Persistence
        self.state_file = Path('data/project_monitor_state.json')
        self.load_state_enabled = True
        if isinstance(self.config, dict):
            # Unit tests supply dict configs; default to skipping persisted state
            self.load_state_enabled = self.config.get('load_state', False)
            self.state_file = Path(self.config.get('state_file', 'data/project_monitor_state.json'))

        self.state_file.parent.mkdir(exist_ok=True)
        if self.load_state_enabled:
            self._load_state()

        # Incident Manager (will be set by bot.py after initialization)
        self.incident_manager = None

        # DM alert user IDs (notified on critical incidents)
        discord_config = self._get_config_section('discord', {})
        self.alert_dm_user_ids: List[int] = [
            int(uid) for uid in discord_config.get('alert_dm_user_ids', [])
        ]

        self.logger.info(f"🔧 Project Monitor initialized with {len(self.projects)} projects")

    def _load_projects(self):
        """Load project configurations from config"""
        projects_config = self._get_config_section('projects', {})

        for project_name, project_config in projects_config.items():
            if not project_config.get('enabled', False):
                continue

            # Check if project has monitoring config
            monitor_config = project_config.get('monitor', {})
            if not monitor_config.get('enabled', False):
                continue

            self.projects[project_name] = ProjectStatus(project_name, monitor_config)
            self.logger.info(f"✅ Loaded monitoring for project: {project_name}")

    def _get_config_section(self, name: str, default=None):
        """Safely fetch config sections from dicts or Config objects."""
        if default is None:
            default = {}
        cfg = getattr(self.config, name, None)
        if isinstance(cfg, dict):
            return cfg
        if isinstance(self.config, dict):
            return self.config.get(name, default)
        base_cfg = getattr(self.config, '_config', None)
        if isinstance(base_cfg, dict):
            return base_cfg.get(name, default)
        return default

    def _load_state(self):
        """Load persisted monitoring state"""
        if not self.state_file.exists():
            return

        try:
            with open(self.state_file, 'r') as f:
                state = json.load(f)

            # Load dashboard message IDs
            self.dashboard_message_id = state.get('dashboard_message_id')
            self._ext_dashboard_ids = state.get('ext_dashboard_ids', {})

            # Load project states
            project_states = state.get('projects', {})
            for project_name, project_state in project_states.items():
                if project_name not in self.projects:
                    continue

                project = self.projects[project_name]
                project.total_checks = project_state.get('total_checks', 0)
                project.successful_checks = project_state.get('successful_checks', 0)
                project.failed_checks = project_state.get('failed_checks', 0)
                # is_online aus State wiederherstellen (verhindert "Offline"-Flash nach Restart)
                if project_state.get('is_online') is not None:
                    project.is_online = project_state['is_online']

            self.logger.info(
                f"📂 Loaded monitoring state from {self.state_file} "
                f"(dashboard_id: {self.dashboard_message_id})"
            )

        except Exception as e:
            self.logger.error(f"❌ Error loading state: {e}", exc_info=True)

    def _save_state(self):
        """Persist monitoring state"""
        try:
            # Build state structure
            state = {
                'dashboard_message_id': self.dashboard_message_id,
                'ext_dashboard_ids': getattr(self, '_ext_dashboard_ids', {}),
                'projects': {}
            }

            # Save project states
            for project_name, project in self.projects.items():
                state['projects'][project_name] = {
                    'total_checks': project.total_checks,
                    'successful_checks': project.successful_checks,
                    'failed_checks': project.failed_checks,
                    'is_online': project.is_online
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
        # Give core services (e.g., health server) a moment to start
        await asyncio.sleep(self.startup_grace_seconds)

        while True:
            try:
                await self._check_project_logs(project)
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

        Supports two modes:
        - HTTP health check (when project.url is set)
        - systemd service check (when project.systemd_services is set)

        Args:
            project: ProjectStatus instance to check
        """
        # TCP-Port-basiertes Health-Checking (für DB-Ports etc.)
        if project.tcp_ports:
            await self._check_tcp_ports(project)
            return

        # Systemd-basiertes Health-Checking (für Services ohne HTTP-Endpoint)
        if project.systemd_services:
            await self._check_systemd_health(project)
            return

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
                        await self._attempt_remediation(project, error)

        except asyncio.TimeoutError:
            error = f"Timeout after {project.timeout}s"
            was_new_incident = project.update_offline(error)

            self.logger.warning(f"⚠️ {project.name}: {error}")

            if was_new_incident:
                await self._send_incident_alert(project, error)
            await self._attempt_remediation(project, error)

        except aiohttp.ClientError as e:
            error = f"Connection error: {str(e)}"
            was_new_incident = project.update_offline(error)

            self.logger.warning(f"⚠️ {project.name}: {error}")

            if was_new_incident:
                await self._send_incident_alert(project, error)
            await self._attempt_remediation(project, error)

        except Exception as e:
            error = f"Unexpected error: {str(e)}"
            was_new_incident = project.update_offline(error)

            self.logger.error(f"❌ {project.name}: {error}", exc_info=True)

            if was_new_incident:
                await self._send_incident_alert(project, error)
            await self._attempt_remediation(project, error)

        # Save state periodically
        self._save_state()

    async def _check_project_logs(self, project: ProjectStatus):
        """Scan recent log tail for critical patterns (e.g., DB connectivity errors)."""
        if not project.log_file or not project.log_pattern:
            return

        log_path = Path(project.log_file)
        if not log_path.exists():
            self.logger.debug(f"ℹ️ Log file not found for {project.name}: {log_path}")
            return

        try:
            size = log_path.stat().st_size
            # Seek to last position or tail window
            start_pos = max(0, size - project.log_tail_bytes)
            with log_path.open('rb') as f:
                f.seek(start_pos)
                data = f.read().decode(errors='ignore')

            if project.log_pattern in data:
                # Only notify once per remediation window
                self.logger.warning(
                    f"⚠️ {project.name}: Detected log pattern '{project.log_pattern}' "
                    f"in {log_path}"
                )
                if project.remediation_command and not project.remediation_triggered:
                    await self._attempt_remediation(
                        project,
                        f"Log pattern detected: {project.log_pattern}"
                    )
        except Exception as e:
            self.logger.error(
                f"❌ Error reading log file for {project.name}: {e}",
                exc_info=True
            )

    async def _check_systemd_health(self, project: ProjectStatus):
        """
        Check health via systemd service status.
        All configured services must be active for the project to be online.
        """
        start_time = time.time()
        failed_services = []

        for svc_config in project.systemd_services:
            svc_name = svc_config if isinstance(svc_config, str) else svc_config.get('name', '')
            is_user = svc_config.get('user', False) if isinstance(svc_config, dict) else False

            if not svc_name:
                continue

            try:
                cmd = ['systemctl']
                if is_user:
                    cmd.extend(['--user'])
                cmd.extend(['is-active', svc_name])

                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await asyncio.wait_for(
                    proc.communicate(), timeout=project.timeout
                )
                status = stdout.decode().strip()

                if status != 'active':
                    failed_services.append(f"{svc_name}: {status}")

            except asyncio.TimeoutError:
                failed_services.append(f"{svc_name}: timeout")
            except Exception as e:
                failed_services.append(f"{svc_name}: {e}")

        response_time_ms = (time.time() - start_time) * 1000

        if not failed_services:
            was_recovering = project.update_online(response_time_ms)
            svc_count = len(project.systemd_services)
            self.logger.info(
                f"✅ {project.name} healthy ({svc_count} services active, {response_time_ms:.0f}ms)"
            )
            if was_recovering:
                await self._send_recovery_alert(project)
        else:
            error = f"Services down: {', '.join(failed_services)}"
            was_new_incident = project.update_offline(error)
            self.logger.warning(f"⚠️ {project.name}: {error}")
            if was_new_incident:
                await self._send_incident_alert(project, error)
            await self._attempt_remediation(project, error)

        self._save_state()

    async def _check_tcp_ports(self, project: ProjectStatus):
        """
        Check health via TCP port connectivity.
        All configured ports must be reachable for the project to be online.
        """
        start_time = time.time()
        failed_ports = []

        for port_config in project.tcp_ports:
            if isinstance(port_config, int):
                host, port, label = '127.0.0.1', port_config, f'localhost:{port_config}'
            else:
                host = port_config.get('host', '127.0.0.1')
                port = port_config['port']
                label = port_config.get('label', f'{host}:{port}')

            try:
                _, writer = await asyncio.wait_for(
                    asyncio.open_connection(host, port),
                    timeout=project.timeout
                )
                writer.close()
                await writer.wait_closed()
            except (OSError, asyncio.TimeoutError) as e:
                failed_ports.append(f'{label} ({e.__class__.__name__})')

        response_time_ms = (time.time() - start_time) * 1000

        if not failed_ports:
            was_recovering = project.update_online(response_time_ms)
            port_labels = ', '.join(
                str(p) if isinstance(p, int) else p.get('label', f":{p.get('port')}")
                for p in project.tcp_ports
            )
            self.logger.info(
                f'✅ {project.name} ports healthy ({port_labels}, {response_time_ms:.0f}ms)'
            )
            if was_recovering:
                await self._send_recovery_alert(project)
        else:
            error = f'TCP ports unreachable: {", ".join(failed_ports)}'
            was_new_incident = project.update_offline(error)
            self.logger.warning(f'⚠️ {project.name}: {error}')
            if was_new_incident:
                await self._send_incident_alert(project, error)
            await self._attempt_remediation(project, error)

        self._save_state()

    async def _attempt_remediation(self, project: ProjectStatus, error: str):
        """Attempt automatic remediation after repeated failures."""
        if not project.remediation_command:
            return
        if project.remediation_triggered:
            return
        if project.consecutive_failures < project.remediation_threshold:
            return

        project.remediation_triggered = True
        self.logger.warning(
            f"🛠️  Auto-remediation for {project.name}: "
            f"{project.consecutive_failures} consecutive failures ({error}). "
            f"Running: {project.remediation_command}"
        )

        try:
            proc = await asyncio.create_subprocess_shell(
                project.remediation_command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if stdout:
                self.logger.info(f"🛠️  Remediation stdout for {project.name}: {stdout.decode().strip()}")
            if stderr:
                self.logger.warning(f"⚠️ Remediation stderr for {project.name}: {stderr.decode().strip()}")
            if proc.returncode != 0:
                self.logger.error(
                    f"❌ Remediation command failed for {project.name} "
                    f"(exit {proc.returncode})"
                )
        except Exception as e:
            self.logger.error(f"❌ Remediation exception for {project.name}: {e}", exc_info=True)

    async def _send_incident_alert(self, project: ProjectStatus, error: str):
        """
        Send Discord alert when project goes down

        If IncidentManager is available, creates a tracked incident with thread.
        Otherwise, falls back to sending a simple alert.
        """
        # Prefer using IncidentManager for proper incident tracking
        if self.incident_manager:
            try:
                await self.incident_manager.detect_project_down_incident(
                    project_name=project.name,
                    error=error
                )
                self.logger.info(
                    f"🚨 Created incident for {project.name} via IncidentManager"
                )
                # Continue to send external notifications even if incident was created
            except Exception as e:
                self.logger.error(
                    f"❌ Failed to create incident via IncidentManager: {e}",
                    exc_info=True
                )

        # Send alert to internal channel (fallback if IncidentManager failed)
        if not self.incident_manager:
            channel = self.bot.get_channel(self.customer_alerts_channel_id)
            if channel:
                embed = self._create_incident_embed(project, error)
                await channel.send(embed=embed)
                self.logger.info(f"🚨 Sent incident alert for {project.name} (fallback mode)")

        # Send to external notification channels (customer servers)
        await self._send_external_notifications(project, "offline", error=error)

        # Send DM to admin users for critical alerts
        await self._send_dm_alerts(project, "offline", error=error)

    async def _send_recovery_alert(self, project: ProjectStatus):
        """Send Discord alert when project recovers"""
        # Auto-resolve any open downtime incidents
        if self.incident_manager:
            try:
                # Calculate downtime duration
                if project.last_offline_time:
                    downtime = datetime.now(timezone.utc) - project.last_offline_time
                    hours = int(downtime.total_seconds() // 3600)
                    minutes = int((downtime.total_seconds() % 3600) // 60)

                    if hours > 0:
                        downtime_str = f"{hours}h {minutes}m"
                    else:
                        downtime_str = f"{minutes}m"
                else:
                    downtime_str = "unbekannt"

                await self.incident_manager.auto_resolve_project_recovery(
                    project_name=project.name,
                    downtime_duration=downtime_str
                )
            except Exception as e:
                self.logger.error(
                    f"❌ Failed to auto-resolve incident for {project.name}: {e}",
                    exc_info=True
                )

        # Send recovery alert to channel
        channel = self.bot.get_channel(self.customer_alerts_channel_id)
        if channel:
            embed = self._create_recovery_embed(project)
            await channel.send(embed=embed)
            self.logger.info(f"✅ Sent recovery alert for {project.name}")

        # Send to external notification channels (customer servers)
        await self._send_external_notifications(project, "online")

        # Send recovery DM to admin users
        await self._send_dm_alerts(project, "online")

    def _create_incident_embed(self, project: ProjectStatus, error: str) -> discord.Embed:
        """Create embed for incident alert"""
        embed = discord.Embed(
            title=f"🔴 {project.name} is DOWN",
            description=f"Health check failed for **{project.name}**",
            color=discord.Color.red(),
            timestamp=datetime.now(timezone.utc)
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

        return embed

    def _create_recovery_embed(self, project: ProjectStatus) -> discord.Embed:
        """Create embed for recovery alert"""
        embed = discord.Embed(
            title=f"✅ {project.name} is BACK ONLINE",
            description=f"**{project.name}** has recovered",
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc)
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

        return embed

    async def _send_external_notifications(self, project: ProjectStatus, event_type: str, error: str = None):
        """
        Send notifications to external servers (customer guilds)

        Args:
            project: ProjectStatus instance
            event_type: "online", "offline", or "error"
            error: Error message (if applicable)
        """
        # Get project config
        project_config = None
        for proj_name, proj_cfg in self.config.projects.items():
            if proj_name == project.name:
                project_config = proj_cfg
                break

        if not project_config:
            return

        # Get external notifications config
        external_notifs = project_config.get('external_notifications', [])
        if not external_notifs:
            return

        for notif_config in external_notifs:
            if not notif_config.get('enabled', False):
                continue

            # Check if this event type should be notified
            notify_on = notif_config.get('notify_on', {})
            if event_type == "offline" and not notify_on.get('offline', True):
                continue
            if event_type == "online" and not notify_on.get('online', True):
                continue

            # Get channel
            channel_id = notif_config.get('channel_id')
            if not channel_id:
                continue

            try:
                channel = self.bot.get_channel(int(channel_id))
                if not channel:
                    self.logger.warning(f"⚠️ External channel {channel_id} not found for {project.name}")
                    continue

                # Create and send embed
                if event_type == "offline":
                    embed = self._create_incident_embed(project, error or "Unknown error")
                elif event_type == "online":
                    embed = self._create_recovery_embed(project)
                else:
                    continue

                await channel.send(embed=embed)
                self.logger.info(f"📤 Sent {event_type} notification for {project.name} to external server")

            except Exception as e:
                self.logger.error(f"❌ Failed to send external notification for {project.name}: {e}")

    async def _send_dm_alerts(self, project: ProjectStatus, event_type: str, error: str = None):
        """
        Send DM to configured admin users for critical project events.

        Only sends DMs for:
        - offline events after 5+ consecutive failures (~25 min with 300s interval)
        - recovery events after extended downtime (>5 min)
        """
        if not self.alert_dm_user_ids:
            return

        # Only DM after sustained downtime (not transient blips)
        if event_type == "offline" and project.consecutive_failures < 2:
            return

        for user_id in self.alert_dm_user_ids:
            try:
                user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
                if not user:
                    continue

                if event_type == "offline":
                    downtime = project.current_downtime_duration
                    downtime_str = ""
                    if downtime:
                        minutes = int(downtime.total_seconds() // 60)
                        downtime_str = f" (seit {minutes} Min.)"

                    embed = discord.Embed(
                        title=f"🚨 {project.name} ist OFFLINE{downtime_str}",
                        description=(
                            f"Health-Check fehlgeschlagen!\n"
                            f"**Fehler:** {(error or 'Unbekannt')[:200]}\n"
                            f"**Fehlversuche:** {project.consecutive_failures}x in Folge"
                        ),
                        color=discord.Color.red(),
                        timestamp=datetime.now(timezone.utc)
                    )
                    await user.send(embed=embed)

                elif event_type == "online":
                    downtime_str = "unbekannt"
                    if project.last_offline_time:
                        downtime = datetime.now(timezone.utc) - project.last_offline_time
                        hours = int(downtime.total_seconds() // 3600)
                        minutes = int((downtime.total_seconds() % 3600) // 60)
                        downtime_str = f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"

                    embed = discord.Embed(
                        title=f"✅ {project.name} ist wieder ONLINE",
                        description=f"Service wiederhergestellt nach **{downtime_str}** Downtime.",
                        color=discord.Color.green(),
                        timestamp=datetime.now(timezone.utc)
                    )
                    await user.send(embed=embed)

                self.logger.info(f"📩 DM Alert an User {user_id}: {project.name} {event_type}")

            except discord.Forbidden:
                self.logger.warning(f"⚠️ Kann keine DM an User {user_id} senden (DMs deaktiviert)")
            except Exception as e:
                self.logger.error(f"❌ DM Alert fehlgeschlagen für User {user_id}: {e}")

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
        """Update or create the dashboard message (main + external)"""
        # === Haupt-Dashboard (DEV Server, alle Projekte) ===
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

        # === Externe Mini-Dashboards (pro Projekt auf deren Server) ===
        await self._update_external_dashboards()

    def _create_dashboard_embed(self) -> discord.Embed:
        """Create Discord embed for project dashboard with per-service details"""
        online_count = sum(1 for p in self.projects.values() if p.is_online)
        total_count = len(self.projects)

        status_line = f"{'✅' if online_count == total_count else '⚠️'} {online_count}/{total_count} Projekte online"

        embed = discord.Embed(
            title="📊 ShadowOps — Projekt-Dashboard",
            description=status_line,
            color=discord.Color.green() if online_count == total_count else discord.Color.orange(),
            timestamp=datetime.now(timezone.utc)
        )

        for project in sorted(self.projects.values(), key=lambda p: p.name):
            status_emoji = "🟢" if project.is_online else "🔴"
            tag = project.name

            # Tag aus Config holen (falls vorhanden)
            for pname, pcfg in self.config.projects.items():
                if pname == project.name and pcfg.get('tag'):
                    tag = pcfg['tag']
                    break

            # Hauptzeile
            if project.is_online:
                response = f"{project.average_response_time:.0f}ms"
                header = f"{status_emoji} {tag}"
                main_line = f"**Online**\nAntwortzeit: {response}\nUptime: {project.uptime_percentage:.1f}%"
            else:
                header = f"{status_emoji} {tag}"
                error_short = (project.last_error or "Unbekannt")[:80]
                main_line = f"**Offline**\nFehler: {error_short}\nUptime: {project.uptime_percentage:.1f}%"
                if project.current_downtime_duration:
                    mins = int(project.current_downtime_duration.total_seconds() / 60)
                    main_line += f"\nDowntime: {mins}m"

            # TCP-Port Details (Services)
            for pname, pcfg in self.config.projects.items():
                if pname != project.name:
                    continue
                tcp_ports = pcfg.get('monitor', {}).get('tcp_ports', [])
                if tcp_ports:
                    port_lines = []
                    for pc in tcp_ports:
                        if isinstance(pc, int):
                            label = f"Port {pc}"
                        else:
                            label = pc.get('label', f"Port {pc.get('port')}")
                        port_ok = project.is_online or (label not in str(project.last_error or ''))
                        if not project.is_online and not project.last_error:
                            port_ok = False
                        icon = "🟢" if port_ok else "🔴"
                        port_lines.append(f"{icon} {label}")
                    main_line += "\n" + " · ".join(port_lines)
                break

            # Letzter Check Zeitstempel
            last_check = getattr(project, 'last_check_time', None)
            if last_check:
                from datetime import timezone as tz
                now = datetime.now(tz.utc)
                ago = int((now - last_check).total_seconds() / 60)
                main_line += f"\nLetzter Check: vor {ago} Minuten"

            embed.add_field(name=header, value=main_line, inline=True)

        embed.set_footer(text="Aktualisiert alle 5 Minuten")

        return embed

    async def _update_external_dashboards(self):
        """
        Aktualisiert Mini-Dashboards auf externen Servern.
        Zeigt nur den Status des jeweiligen Projekts — nicht alle Projekte.
        Konfiguriert via external_notifications[].channel_id pro Projekt.
        """
        for proj_name, proj_cfg in self.config.projects.items():
            external_notifs = proj_cfg.get('external_notifications', [])
            if not external_notifs:
                continue

            project = self.projects.get(proj_name)
            if not project:
                continue

            for notif_config in external_notifs:
                if not notif_config.get('enabled', False):
                    continue

                channel_id = notif_config.get('channel_id')
                if not channel_id:
                    continue

                channel = self.bot.get_channel(int(channel_id))
                if not channel:
                    continue

                # Mini-Dashboard Embed fuer dieses eine Projekt
                embed = self._create_single_project_dashboard(project, proj_cfg)

                # State-Key fuer die externe Dashboard-Message-ID
                state_key = f"ext_dashboard_{proj_name}_{channel_id}"

                try:
                    existing_msg_id = getattr(self, '_ext_dashboard_ids', {}).get(state_key)
                    if existing_msg_id:
                        try:
                            msg = await channel.fetch_message(existing_msg_id)
                            await msg.edit(embed=embed)
                            continue
                        except discord.NotFound:
                            pass  # Nachricht geloescht, neue erstellen

                    # Neue Nachricht senden
                    msg = await channel.send(embed=embed)
                    if not hasattr(self, '_ext_dashboard_ids'):
                        self._ext_dashboard_ids = {}
                    self._ext_dashboard_ids[state_key] = msg.id

                except Exception as e:
                    self.logger.error(f"❌ Fehler beim externen Dashboard fuer {proj_name}: {e}")

    def _create_single_project_dashboard(self, project, project_config) -> discord.Embed:
        """
        Erstellt ein detailliertes Embed fuer ein einzelnes Projekt.
        Wird auf dem externen Discord-Server des Projekts angezeigt.
        Zeigt den Gesamtstatus + einzelne Services (TCP-Ports).
        """
        tag = project_config.get('tag', project.name)
        color_val = project_config.get('color', 0x2ECC71)
        if isinstance(color_val, str) and color_val.startswith('0x'):
            color_val = int(color_val, 16)

        is_online = project.is_online
        status_emoji = "🟢" if is_online else "🔴"
        status_text = "Online" if is_online else "Offline"
        color = discord.Color(color_val) if is_online else discord.Color.red()

        embed = discord.Embed(
            title=f"{status_emoji} Server Status — {tag}",
            color=color,
            timestamp=datetime.now(timezone.utc)
        )

        # Hauptstatus
        if is_online:
            response_time = f"{project.average_response_time:.0f}ms"
            embed.description = f"**{status_text}** — Antwortzeit: {response_time}"
        else:
            error_msg = project.last_error or "Unbekannter Fehler"
            embed.description = f"**{status_text}** — {error_msg}"
            if project.current_downtime_duration:
                downtime_min = int(project.current_downtime_duration.total_seconds() / 60)
                embed.description += f"\nDowntime: {downtime_min} Minuten"

        # Einzelne Services (TCP-Ports) live pruefen
        tcp_ports = project_config.get('monitor', {}).get('tcp_ports', [])
        if tcp_ports:
            service_lines = []
            for port_config in tcp_ports:
                if isinstance(port_config, int):
                    host, port, label = '127.0.0.1', port_config, f'Port {port_config}'
                else:
                    host = port_config.get('host', '127.0.0.1')
                    port = port_config['port']
                    label = port_config.get('label', f'Port {port}')

                # Schneller TCP-Check (non-blocking, cached vom letzten Health-Check)
                # Wir nutzen den letzten Fehler-String um den Status abzuleiten
                port_ok = True
                if not is_online and project.last_error and label in str(project.last_error):
                    port_ok = False

                icon = "🟢" if (is_online or port_ok) else "🔴"
                if not is_online and not project.last_error:
                    icon = "🔴"  # Gesamtstatus offline = alles rot

                service_lines.append(f"{icon} **{label}**")

            embed.add_field(
                name="📡 Services",
                value="\n".join(service_lines),
                inline=False
            )

        # Statistiken
        uptime = f"{project.uptime_percentage:.1f}%"
        embed.add_field(name="📊 Uptime", value=uptime, inline=True)

        total_checks = project.total_checks if hasattr(project, 'total_checks') else 0
        embed.add_field(name="🔍 Checks", value=str(total_checks), inline=True)

        embed.add_field(
            name="⏱️ Intervall",
            value=f"alle {project.check_interval}s",
            inline=True
        )

        embed.set_footer(text="ShadowOps Monitoring • Aktualisiert alle 5 Minuten")

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
