"""
Multi-Project Monitoring for ShadowOps Bot
Health checks, uptime tracking, and Discord dashboards
"""

import asyncio
import aiohttp
import logging
import json
import shutil
import ssl
import time
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta, timezone
from pathlib import Path
import discord

# EmbedBuilder + Severity fuer die Enterprise-Health-Check-Erweiterung (Phase 5b, Issue #278).
# Wird verwendet von _check_disk_space, _check_memory_usage, _check_container_restart_count,
# _check_ssl_cert_expiry und _check_backup_freshness.
try:  # pragma: no cover - Import-Pfad haengt von pythonpath ab (src/ vs. shadowops_bot/)
    from utils.embeds import EmbedBuilder, Severity
except ImportError:  # pragma: no cover
    from src.utils.embeds import EmbedBuilder, Severity  # type: ignore[no-redef]

logger = logging.getLogger('shadowops.project_monitor')


# Default-Schwellen fuer die Enterprise-Health-Checks (Phase 5b).
# Pro Projekt ueberschreibbar via projects.<name>.monitor.thresholds.* in config.yaml.
HEALTH_CHECK_DEFAULTS: Dict[str, Any] = {
    'disk_warn_percent': 15,      # < 15% frei -> Alert
    'memory_warn_percent': 90,    # > 90% Container-Memory -> Alert
    'restart_count_warn': 3,      # > 3 Restarts in 24h -> Alert
    'ssl_cert_warn_days': 30,     # < 30 Tage -> Alert
    'backup_max_age_hours': 25,   # > 25h alt -> Alert
    # Phase 5c: App-Health-Checks (DB-Pool, Failed-Login)
    'db_pool_saturation_warn': 80,        # > 80% Pool-Sat -> Alert
    'failed_login_per_5min_warn': 100,    # > 100 Fehlversuche/5 Min -> Brute-Force-Verdacht
}

# Min-Intervall pro Check-Typ (Sekunden). Verhindert dass teure Checks
# (SSL via openssl, Disk via shutil) bei jedem Health-Loop-Tick laufen.
HEALTH_CHECK_MIN_INTERVAL_SECONDS: Dict[str, int] = {
    'disk_space': 5 * 60,           # 5 Min
    'memory_usage': 60,             # 60s (zeitnah, da kritisch)
    'restart_count': 60 * 60,       # 1h (Trend)
    'ssl_cert_expiry': 6 * 60 * 60, # 6h (langsam-bewegender Wert)
    'backup_freshness': 30 * 60,    # 30 Min (taeglicher Cron)
    # Phase 5c: App-Health-Checks via /api/internal/health-stats
    'db_pool_saturation': 5 * 60,   # 5 Min (Pool-Sat ist Trend, nicht Spike)
    'failed_login_rate': 60,        # 60s (Brute-Force kann schnell hochlaufen)
}

# Anti-Spam: pro Check-Typ wie lange ein bereits ausgeloester Alert
# unterdrueckt wird, bevor neu alarmiert wird.
HEALTH_CHECK_ALERT_COOLDOWNS: Dict[str, timedelta] = {
    'disk_space': timedelta(minutes=60),
    'memory_usage': timedelta(minutes=60),
    'restart_count': timedelta(hours=6),
    'ssl_cert_expiry': timedelta(hours=24),
    'backup_freshness': timedelta(minutes=60),
    # Phase 5c
    'db_pool_saturation': timedelta(minutes=30),  # Sat-Trend braucht keine sofortige Re-Alarmierung
    'failed_login_rate': timedelta(minutes=15),   # Brute-Force-Burst kann mehrfach kommen
}


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

        # Erweiterte Health-Daten (Latenz, Memory, Version)
        self.health_details: dict = {}

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

        # ── Enterprise-Health-Check-Erweiterung (Phase 5b, Issue #278) ─────────
        # Pro Check + Project: Wann zuletzt alarmiert? (Anti-Spam-Cooldown)
        # Key: f"{project.name}:{check_type}" -> datetime
        self._health_check_alerts: Dict[str, datetime] = {}
        # Pro Check + Project: Wann zuletzt ausgefuehrt? (Min-Intervall-Filter)
        self._health_check_last_run: Dict[str, datetime] = {}

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
            self._ext_alert_ids = state.get('ext_alert_ids', {})

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
                'ext_alert_ids': getattr(self, '_ext_alert_ids', {}),
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

                # Health-Check-Erweiterung (Phase 5b + 5c, Issue #278).
                # Jede Methode hat ihren eigenen Min-Intervall-Filter — die werden
                # bei jedem Loop-Tick aufgerufen, fuehren aber nur dann tatsaechlich
                # die Pruefung aus, wenn der Filter es erlaubt.
                await self._check_disk_space(project)
                await self._check_memory_usage(project)
                await self._check_container_restart_count(project)
                await self._check_ssl_cert_expiry(project)
                await self._check_backup_freshness(project)
                # Phase 5c: App-Health-Checks via internal API (skip wenn nicht konfiguriert)
                await self._check_db_pool_saturation(project)
                await self._check_failed_login_rate(project)

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
            # Wenn AUCH eine URL vorhanden ist → HTTP-Health-Check zusätzlich machen
            # (liefert erweiterte Daten: Latenz, Memory, Version)
            if not project.url:
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
                        # Parse health details wenn verfügbar
                        try:
                            health_data = await response.json()
                            project.health_details = health_data
                        except Exception:
                            project.health_details = {}

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

                # Persistente Alert-Nachricht: Edit statt neu senden (Anti-Flooding)
                alert_key = f"ext_alert_{project.name}_{channel_id}"
                if not hasattr(self, '_ext_alert_ids'):
                    self._ext_alert_ids = {}

                existing_alert_id = self._ext_alert_ids.get(alert_key)
                if existing_alert_id:
                    try:
                        msg = await channel.fetch_message(existing_alert_id)
                        await msg.edit(embed=embed)
                    except discord.NotFound:
                        msg = await channel.send(embed=embed)
                        self._ext_alert_ids[alert_key] = msg.id
                else:
                    msg = await channel.send(embed=embed)
                    self._ext_alert_ids[alert_key] = msg.id

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

            # Erweiterte Health-Daten (wenn verfügbar)
            hd = project.health_details
            if hd:
                latency = hd.get('latency', {})
                memory = hd.get('memory', {})
                version = hd.get('version')

                if latency:
                    lat_parts = []
                    if latency.get('db_ms') is not None:
                        lat_parts.append(f"DB:{latency['db_ms']}ms")
                    if latency.get('redis_ms') is not None:
                        lat_parts.append(f"Redis:{latency['redis_ms']}ms")
                    if latency.get('osrm_ms') is not None:
                        lat_parts.append(f"OSRM:{latency['osrm_ms']}ms")
                    if lat_parts:
                        main_line += f"\n⚡ {' · '.join(lat_parts)}"

                if memory:
                    main_line += f"\n💾 RAM: {memory.get('rss_mb', '?')} MB · Heap: {memory.get('heap_used_mb', '?')} MB"

                if version:
                    main_line += f"\n📦 v{version}"

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

        # Erweiterte Health-Daten (wenn verfügbar)
        hd = project.health_details
        if hd:
            latency = hd.get('latency', {})
            memory = hd.get('memory', {})
            version = hd.get('version')

            if latency:
                latency_lines = []
                if latency.get('db_ms') is not None:
                    latency_lines.append(f"DB: {latency['db_ms']}ms")
                if latency.get('redis_ms') is not None:
                    latency_lines.append(f"Redis: {latency['redis_ms']}ms")
                if latency.get('osrm_ms') is not None:
                    latency_lines.append(f"OSRM: {latency['osrm_ms']}ms")
                if latency_lines:
                    embed.add_field(name="⚡ Latenz", value=" · ".join(latency_lines), inline=False)

            if memory:
                mem_text = f"RAM: {memory.get('rss_mb', '?')} MB · Heap: {memory.get('heap_used_mb', '?')} MB"
                embed.add_field(name="💾 Memory", value=mem_text, inline=True)

            if version:
                embed.add_field(name="📦 Version", value=f"v{version}", inline=True)

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

    # ════════════════════════════════════════════════════════════════════════
    # Enterprise-Health-Check-Erweiterung (Phase 5b, Issue #278)
    # ════════════════════════════════════════════════════════════════════════

    def _get_project_config(self, project_name: str) -> Dict[str, Any]:
        """Liefert die volle Projekt-Config (config.yaml -> projects.<name>)."""
        projects_cfg = self._get_config_section('projects', {})
        if isinstance(projects_cfg, dict):
            cfg = projects_cfg.get(project_name)
            if isinstance(cfg, dict):
                return cfg
        return {}

    def _get_health_threshold(self, project: ProjectStatus, key: str) -> Any:
        """Schwellenwert holen — projekt-spezifisch (monitor.thresholds.<key>) oder default."""
        proj_cfg = self._get_project_config(project.name)
        thresholds = proj_cfg.get('monitor', {}).get('thresholds', {}) if isinstance(proj_cfg, dict) else {}
        if isinstance(thresholds, dict) and key in thresholds:
            return thresholds[key]
        return HEALTH_CHECK_DEFAULTS.get(key)

    def _get_project_container(self, project: ProjectStatus) -> Optional[str]:
        """Container-Name aus monitor.container holen (z.B. 'zerodox-web'). None = skip."""
        proj_cfg = self._get_project_config(project.name)
        monitor_cfg = proj_cfg.get('monitor', {}) if isinstance(proj_cfg, dict) else {}
        container = monitor_cfg.get('container')
        if isinstance(container, str) and container.strip():
            return container.strip()
        return None

    def _get_project_domain(self, project: ProjectStatus) -> Optional[str]:
        """Domain fuer SSL-Check aus project.url ableiten."""
        if not project.url:
            return None
        try:
            from urllib.parse import urlparse
            parsed = urlparse(project.url)
            host = parsed.hostname
            if host and not host.startswith('127.') and host != 'localhost':
                return host
        except Exception:
            pass
        return None

    def _should_run_health_check(self, project: ProjectStatus, check_type: str) -> bool:
        """
        Min-Intervall-Filter: Hat dieser Check fuer dieses Projekt schon
        kuerzlich gelaufen? Wenn ja -> skip.
        """
        key = f"{project.name}:{check_type}"
        last = self._health_check_last_run.get(key)
        if last is None:
            return True
        min_interval = HEALTH_CHECK_MIN_INTERVAL_SECONDS.get(check_type, 60)
        elapsed = (datetime.now(timezone.utc) - last).total_seconds()
        return elapsed >= min_interval

    def _mark_health_check_ran(self, project: ProjectStatus, check_type: str) -> None:
        """Min-Intervall-State aktualisieren."""
        self._health_check_last_run[f"{project.name}:{check_type}"] = datetime.now(timezone.utc)

    def _resolve_health_alert_channel(self, channel_key: str, fallback_id: int) -> Optional[int]:
        """
        Channel-ID aus bot.config.channels.<channel_key> holen, mit Hardcoded-Fallback.

        Args:
            channel_key: 'critical', 'bot_status', 'backups', etc.
            fallback_id: Hardcoded Channel-ID falls config nichts liefert.
        """
        channels_cfg = self._get_config_section('channels', {})
        if isinstance(channels_cfg, dict):
            val = channels_cfg.get(channel_key)
            if isinstance(val, int) and val > 0:
                return val
        # Config-Object mit Attributen?
        cfg = getattr(self.config, 'channels', None)
        if cfg is not None:
            val = getattr(cfg, channel_key, None)
            if isinstance(val, int) and val > 0:
                return val
            if isinstance(cfg, dict):
                val = cfg.get(channel_key)
                if isinstance(val, int) and val > 0:
                    return val
        return fallback_id if fallback_id > 0 else None

    def _project_tag(self, project: ProjectStatus) -> str:
        """Tag fuer Embed-Header (z.B. '📘 [ZERODOX]')."""
        proj_cfg = self._get_project_config(project.name)
        tag = proj_cfg.get('tag') if isinstance(proj_cfg, dict) else None
        return str(tag) if tag else f"[{project.name.upper()}]"

    async def _send_health_alert(
        self,
        project: ProjectStatus,
        check_type: str,
        title: str,
        description: str,
        severity: Severity,
        fields: List[Dict[str, Any]],
        channel_key: str,
        fallback_channel_id: int,
    ) -> None:
        """
        Discord-Alert senden mit Cooldown-Check.

        Anti-Spam: Wenn der gleiche Alert (project + check_type) innerhalb
        des Cooldown-Fensters bereits gesendet wurde -> unterdruecken.
        """
        cooldown_key = f"{project.name}:{check_type}"
        cooldown = HEALTH_CHECK_ALERT_COOLDOWNS.get(check_type, timedelta(minutes=60))
        last_alert = self._health_check_alerts.get(cooldown_key)
        now = datetime.now(timezone.utc)
        if last_alert is not None and (now - last_alert) < cooldown:
            self.logger.debug(
                f"🔇 {project.name} {check_type}: Cooldown aktiv ({last_alert.isoformat()}), Alert unterdrueckt"
            )
            return

        channel_id = self._resolve_health_alert_channel(channel_key, fallback_channel_id)
        if not channel_id:
            self.logger.warning(
                f"⚠️ Kein Channel fuer {check_type}-Alert (key={channel_key}) konfiguriert — uebersprungen"
            )
            return

        channel = self.bot.get_channel(channel_id) if hasattr(self.bot, 'get_channel') else None
        if not channel:
            self.logger.warning(f"⚠️ Channel {channel_id} fuer {check_type}-Alert nicht gefunden")
            return

        embed = EmbedBuilder.create_alert(
            title=title,
            description=description,
            severity=severity,
            fields=fields,
            project_tag=self._project_tag(project),
            footer="ShadowOps Enterprise Health-Checks",
        )

        try:
            await channel.send(embed=embed)
            self._health_check_alerts[cooldown_key] = now
            self.logger.warning(
                f"🚨 Health-Alert {check_type} fuer {project.name} -> Channel {channel_id}"
            )
        except discord.HTTPException as exc:
            self.logger.error(f"❌ Discord-Send fuer {project.name} {check_type} fehlgeschlagen: {exc}")

    def _clear_health_alert_cooldown(self, project: ProjectStatus, check_type: str) -> None:
        """
        Recovery-Logik: Wenn ein Wert wieder unterhalb der Schwelle ist,
        Cooldown loeschen — damit ein erneuter Spike sofort alarmiert.
        """
        key = f"{project.name}:{check_type}"
        if key in self._health_check_alerts:
            del self._health_check_alerts[key]
            self.logger.info(
                f"✅ {project.name} {check_type}: Recovery — Cooldown zurueckgesetzt"
            )

    async def _check_disk_space(self, project: ProjectStatus) -> None:
        """
        Check 1: Disk-Space am Projekt-Pfad.

        Schwelle: < disk_warn_percent (default 15) % freier Speicher -> Alert.
        Channel:  🚨-critical (1441655480840617994), key='critical'.
        Severity: CRITICAL.
        Cooldown: 60 Min.
        Frequenz: alle 5 Min (Disk-Full = potenzieller Service-Crash).
        """
        if not self._should_run_health_check(project, 'disk_space'):
            return
        self._mark_health_check_ran(project, 'disk_space')

        proj_cfg = self._get_project_config(project.name)
        path = proj_cfg.get('path') if isinstance(proj_cfg, dict) else None
        if not path:
            return

        threshold_percent = float(self._get_health_threshold(project, 'disk_warn_percent'))

        try:
            # shutil.disk_usage ist blocking → in Thread-Pool offloaden.
            usage = await asyncio.to_thread(shutil.disk_usage, path)
        except FileNotFoundError:
            self.logger.debug(f"ℹ️ Disk-Check {project.name}: Pfad {path} existiert nicht")
            return
        except Exception as exc:
            self.logger.error(f"❌ Disk-Check {project.name} fehlgeschlagen: {exc}")
            return

        free_percent = (usage.free / usage.total) * 100 if usage.total > 0 else 100.0
        free_gb = usage.free / (1024 ** 3)
        total_gb = usage.total / (1024 ** 3)

        if free_percent < threshold_percent:
            await self._send_health_alert(
                project=project,
                check_type='disk_space',
                title=f"Disk-Space niedrig — {project.name}",
                description=(
                    f"Nur noch **{free_percent:.1f}%** freier Speicher auf `{path}`.\n"
                    f"Schwelle: **< {threshold_percent:.0f}%** frei.\n\n"
                    f"Disk-Full kann den Service zum Crash bringen — bitte zeitnah aufraeumen."
                ),
                severity=Severity.CRITICAL,
                fields=[
                    {"name": "Frei", "value": f"{free_gb:.1f} GB ({free_percent:.1f}%)", "inline": True},
                    {"name": "Gesamt", "value": f"{total_gb:.1f} GB", "inline": True},
                    {"name": "Pfad", "value": f"`{path}`", "inline": False},
                ],
                channel_key='critical',
                fallback_channel_id=1441655480840617994,
            )
        else:
            self._clear_health_alert_cooldown(project, 'disk_space')

    async def _check_memory_usage(self, project: ProjectStatus) -> None:
        """
        Check 2: Container-Memory-Auslastung.

        Schwelle: > memory_warn_percent (default 90) % -> Alert.
        Channel:  🚨-critical, key='critical'.
        Severity: CRITICAL.
        Cooldown: 60 Min.
        Frequenz: alle 60s (zeitnah, da kritisch).

        Container-Name: aus projects.<name>.monitor.container in config.yaml.
        Wenn nicht gesetzt -> skip.
        """
        if not self._should_run_health_check(project, 'memory_usage'):
            return
        self._mark_health_check_ran(project, 'memory_usage')

        container = self._get_project_container(project)
        if not container:
            return  # Kein Container konfiguriert -> skip

        threshold_percent = float(self._get_health_threshold(project, 'memory_warn_percent'))

        try:
            proc = await asyncio.create_subprocess_exec(
                'docker', 'stats', '--no-stream', '--format', '{{.MemPerc}}', container,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
        except (asyncio.TimeoutError, FileNotFoundError) as exc:
            self.logger.debug(f"ℹ️ Memory-Check {project.name}: docker stats nicht verfuegbar ({exc})")
            return
        except Exception as exc:
            self.logger.error(f"❌ Memory-Check {project.name} fehlgeschlagen: {exc}")
            return

        if proc.returncode != 0:
            self.logger.debug(
                f"ℹ️ Memory-Check {project.name}: docker stats exit {proc.returncode} "
                f"(Container '{container}' evtl. nicht laufend)"
            )
            return

        raw = stdout.decode().strip()
        # Format: "12.34%" -> 12.34
        try:
            mem_percent = float(raw.rstrip('%'))
        except ValueError:
            self.logger.debug(f"ℹ️ Memory-Check {project.name}: Konnte '{raw}' nicht parsen")
            return

        if mem_percent > threshold_percent:
            await self._send_health_alert(
                project=project,
                check_type='memory_usage',
                title=f"Memory hoch — {project.name}",
                description=(
                    f"Container `{container}` nutzt **{mem_percent:.1f}%** des Memory-Limits.\n"
                    f"Schwelle: **> {threshold_percent:.0f}%**.\n\n"
                    f"Bei anhaltend hoher Auslastung droht OOM-Kill — bitte pruefen."
                ),
                severity=Severity.CRITICAL,
                fields=[
                    {"name": "Container", "value": f"`{container}`", "inline": True},
                    {"name": "Memory", "value": f"{mem_percent:.1f}%", "inline": True},
                    {"name": "Schwelle", "value": f"> {threshold_percent:.0f}%", "inline": True},
                ],
                channel_key='critical',
                fallback_channel_id=1441655480840617994,
            )
        else:
            self._clear_health_alert_cooldown(project, 'memory_usage')

    async def _check_container_restart_count(self, project: ProjectStatus) -> None:
        """
        Check 3: Container-Restart-Count.

        Schwelle: > restart_count_warn (default 3) Restarts in 24h -> Alert.
        Channel:  🤖-bot-status (1441655486981214309), key='bot_status'.
        Severity: MEDIUM (informativ — Trend-Erkennung, nicht kritisch).
        Cooldown: 6 Stunden.
        Frequenz: alle 1h (langsamer Trend, kein Sub-Minute-Sampling noetig).

        Quelle: docker inspect --format '{{.RestartCount}}' <container>.
        Hinweis: Docker liefert nur den Total-RestartCount seit Container-Erstellung.
        Fuer "in 24h" mappen wir auf einen Delta-Vergleich gegen den letzten
        beobachteten Wert (gespeichert in self._health_check_alerts via Helfer).
        """
        if not self._should_run_health_check(project, 'restart_count'):
            return
        self._mark_health_check_ran(project, 'restart_count')

        container = self._get_project_container(project)
        if not container:
            return

        threshold = int(self._get_health_threshold(project, 'restart_count_warn'))

        try:
            proc = await asyncio.create_subprocess_exec(
                'docker', 'inspect', '--format', '{{.RestartCount}}', container,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        except (asyncio.TimeoutError, FileNotFoundError):
            return
        except Exception as exc:
            self.logger.error(f"❌ Restart-Count-Check {project.name} fehlgeschlagen: {exc}")
            return

        if proc.returncode != 0:
            return

        raw = stdout.decode().strip()
        try:
            current_count = int(raw)
        except ValueError:
            return

        # Rolling 24h-Snapshot: state-key haelt {timestamp: int(count)} der letzten 24h.
        # Vereinfachte Loesung: Wir merken uns den Count vor 24h und vergleichen.
        snapshot_key = f"{project.name}:restart_snapshot"
        snapshots = getattr(self, '_restart_count_snapshots', None)
        if snapshots is None:
            self._restart_count_snapshots: Dict[str, List[Any]] = {}
            snapshots = self._restart_count_snapshots

        history = snapshots.setdefault(snapshot_key, [])
        now = datetime.now(timezone.utc)
        history.append((now, current_count))
        # Alles aelter als 24h verwerfen.
        cutoff = now - timedelta(hours=24)
        snapshots[snapshot_key] = [(t, c) for (t, c) in history if t >= cutoff]
        history = snapshots[snapshot_key]

        # Restart-Count vor 24h (= aeltester Snapshot innerhalb des Fensters).
        baseline = history[0][1] if history else current_count
        delta = current_count - baseline

        if delta > threshold:
            await self._send_health_alert(
                project=project,
                check_type='restart_count',
                title=f"Container-Restarts — {project.name}",
                description=(
                    f"Container `{container}` ist in den letzten 24h **{delta}×** neugestartet.\n"
                    f"Schwelle: **> {threshold}** Restarts.\n\n"
                    f"Hinweis: Crash-Loops oder OOM-Kills koennten die Ursache sein. "
                    f"`docker logs {container}` und `journalctl` pruefen."
                ),
                severity=Severity.MEDIUM,
                fields=[
                    {"name": "Container", "value": f"`{container}`", "inline": True},
                    {"name": "Restarts (24h)", "value": str(delta), "inline": True},
                    {"name": "Total seit Erstellung", "value": str(current_count), "inline": True},
                ],
                channel_key='bot_status',
                fallback_channel_id=1441655486981214309,
            )
        else:
            self._clear_health_alert_cooldown(project, 'restart_count')

    async def _check_ssl_cert_expiry(self, project: ProjectStatus) -> None:
        """
        Check 4: SSL-Zertifikats-Ablauf.

        Schwelle: < ssl_cert_warn_days (default 30) Tage -> Alert.
        Channel:  🤖-bot-status, key='bot_status'.
        Severity: HIGH wenn < 7 Tage, sonst MEDIUM.
        Cooldown: 24 Stunden.
        Frequenz: alle 6h (langsam-bewegender Wert).

        Domain: aus project.url (URL-Parsing).
        Methode: TLS-Handshake via asyncio.open_connection + SSLContext —
        liefert das Server-Zertifikat ohne externes openssl-Subprocess.
        """
        if not self._should_run_health_check(project, 'ssl_cert_expiry'):
            return
        self._mark_health_check_ran(project, 'ssl_cert_expiry')

        domain = self._get_project_domain(project)
        if not domain:
            return  # Lokale URL oder kein URL -> skip

        warn_days = int(self._get_health_threshold(project, 'ssl_cert_warn_days'))

        try:
            cert = await asyncio.wait_for(
                self._fetch_peer_cert(domain, 443),
                timeout=10,
            )
        except (asyncio.TimeoutError, OSError, ssl.SSLError) as exc:
            self.logger.debug(f"ℹ️ SSL-Check {project.name} ({domain}) fehlgeschlagen: {exc}")
            return
        except Exception as exc:
            self.logger.error(f"❌ SSL-Check {project.name} ({domain}) Fehler: {exc}")
            return

        not_after_str = cert.get('notAfter') if cert else None
        if not not_after_str:
            return

        try:
            # Format: "Apr 30 12:00:00 2026 GMT"
            not_after = datetime.strptime(not_after_str, '%b %d %H:%M:%S %Y %Z').replace(tzinfo=timezone.utc)
        except ValueError:
            self.logger.debug(f"ℹ️ SSL-Check {project.name}: notAfter-Format unbekannt: {not_after_str}")
            return

        days_remaining = (not_after - datetime.now(timezone.utc)).days

        if days_remaining < warn_days:
            severity = Severity.HIGH if days_remaining < 7 else Severity.MEDIUM
            await self._send_health_alert(
                project=project,
                check_type='ssl_cert_expiry',
                title=f"SSL-Zertifikat laeuft bald ab — {project.name}",
                description=(
                    f"Das Zertifikat fuer **{domain}** laeuft in **{days_remaining} Tagen** ab "
                    f"({not_after.strftime('%Y-%m-%d %H:%M UTC')}).\n"
                    f"Schwelle: **< {warn_days} Tage**.\n\n"
                    f"Let's Encrypt sollte automatisch renewen — falls nicht, "
                    f"`certbot renew` oder Traefik-Logs pruefen."
                ),
                severity=severity,
                fields=[
                    {"name": "Domain", "value": domain, "inline": True},
                    {"name": "Verbleibend", "value": f"{days_remaining} Tage", "inline": True},
                    {"name": "Ablauf", "value": not_after.strftime('%Y-%m-%d'), "inline": True},
                ],
                channel_key='bot_status',
                fallback_channel_id=1441655486981214309,
            )
        else:
            self._clear_health_alert_cooldown(project, 'ssl_cert_expiry')

    async def _fetch_peer_cert(self, host: str, port: int) -> Dict[str, Any]:
        """TLS-Handshake durchfuehren und Server-Zertifikat (parsed) liefern."""
        ctx = ssl.create_default_context()
        loop = asyncio.get_running_loop()
        # asyncio.open_connection liefert einen StreamWriter, ueber den wir den
        # ssl-Transport abgreifen koennen, um das Peer-Cert auszulesen.
        reader, writer = await asyncio.open_connection(host, port, ssl=ctx, server_hostname=host)
        try:
            ssl_obj = writer.get_extra_info('ssl_object')
            if not ssl_obj:
                raise ssl.SSLError(f"Kein ssl_object fuer {host}:{port}")
            # binary_form=False -> dict {'subject': ..., 'notAfter': ..., ...}
            cert = ssl_obj.getpeercert()
            return cert or {}
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def _check_backup_freshness(self, project: ProjectStatus) -> None:
        """
        Check 5: Backup-Freshness.

        Schwelle: > backup_max_age_hours (default 25) -> Alert.
        Channel:  backup-dashboard (1486479593602023486), key='backups'.
        Severity: HIGH.
        Cooldown: 60 Min.
        Frequenz: alle 30 Min (taeglicher Cron, kein Sub-Hour-Sampling noetig).

        Pfad: <project.path>/backups/daily/ — wenn der Pfad nicht existiert,
        wird der Check uebersprungen (z.B. fuer Projekte ohne Backup-Strategie).
        Aktuell nur ZERODOX hat einen daily-Backup-Pfad.
        """
        if not self._should_run_health_check(project, 'backup_freshness'):
            return
        self._mark_health_check_ran(project, 'backup_freshness')

        proj_cfg = self._get_project_config(project.name)
        path = proj_cfg.get('path') if isinstance(proj_cfg, dict) else None
        if not path:
            return

        backup_dir = Path(path) / 'backups' / 'daily'
        if not backup_dir.exists() or not backup_dir.is_dir():
            return  # Projekt hat keinen Backup-Pfad -> skip

        max_age_hours = float(self._get_health_threshold(project, 'backup_max_age_hours'))

        try:
            files = [p for p in backup_dir.iterdir() if p.is_file()]
        except OSError as exc:
            self.logger.error(f"❌ Backup-Check {project.name}: Verzeichnis-Lesefehler: {exc}")
            return

        if not files:
            await self._send_health_alert(
                project=project,
                check_type='backup_freshness',
                title=f"Keine Backups gefunden — {project.name}",
                description=(
                    f"Im Verzeichnis `{backup_dir}` liegen keine Backup-Dateien.\n"
                    f"Backup-Cron wahrscheinlich tot — sofortige Pruefung erforderlich!"
                ),
                severity=Severity.HIGH,
                fields=[
                    {"name": "Pfad", "value": f"`{backup_dir}`", "inline": False},
                ],
                channel_key='backups',
                fallback_channel_id=1486479593602023486,
            )
            return

        # Neueste Datei finden.
        latest = max(files, key=lambda p: p.stat().st_mtime)
        latest_mtime = datetime.fromtimestamp(latest.stat().st_mtime, tz=timezone.utc)
        age = datetime.now(timezone.utc) - latest_mtime
        age_hours = age.total_seconds() / 3600

        if age_hours > max_age_hours:
            await self._send_health_alert(
                project=project,
                check_type='backup_freshness',
                title=f"Backup veraltet — {project.name}",
                description=(
                    f"Letztes Backup ist **{age_hours:.1f} Stunden** alt.\n"
                    f"Schwelle: **> {max_age_hours:.0f}h**.\n\n"
                    f"Backup-Cron pruefen: `crontab -l`, `journalctl -u <backup-service>`."
                ),
                severity=Severity.HIGH,
                fields=[
                    {"name": "Letzte Datei", "value": f"`{latest.name}`", "inline": False},
                    {"name": "Alter", "value": f"{age_hours:.1f}h", "inline": True},
                    {"name": "Schwelle", "value": f"> {max_age_hours:.0f}h", "inline": True},
                    {"name": "Pfad", "value": f"`{backup_dir}`", "inline": False},
                ],
                channel_key='backups',
                fallback_channel_id=1486479593602023486,
            )
        else:
            self._clear_health_alert_cooldown(project, 'backup_freshness')

    # === Phase 5c — App-Health-Checks via internal API ===
    # Diese Checks pollen einen ZERODOX-API-Endpoint der DB-Pool-Saturation und
    # Failed-Login-Rate exposed. Nur Projekte mit `monitor.internal_health_endpoint`
    # in config.yaml werden geprüft (graceful skip bei fehlender Konfiguration).
    #
    # Auth: X-Agent-Key Header mit ZERODOX_AGENT_API_KEY (env-var).
    # Endpoint: ZERODOX/api/internal/health-stats (Phase 5a, PR #279)

    async def _fetch_app_health_stats(self, project: ProjectStatus) -> Optional[Dict[str, Any]]:
        """HTTP-Call zur internal Health-Stats-API des Projekts.

        Returnt None wenn:
          - Kein internal_health_endpoint konfiguriert
          - Keine API-Key env-var gesetzt
          - HTTP-Fehler (Timeout, 4xx, 5xx)
          - JSON-Parse-Fehler
        """
        proj_cfg = self._get_project_config(project.name)
        if not isinstance(proj_cfg, dict):
            return None

        monitor_cfg = proj_cfg.get('monitor', {})
        if not isinstance(monitor_cfg, dict):
            return None

        endpoint = monitor_cfg.get('internal_health_endpoint')
        if not endpoint:
            return None

        api_key_env = monitor_cfg.get('health_api_key_env', 'ZERODOX_AGENT_API_KEY')
        import os
        api_key = os.environ.get(api_key_env)
        if not api_key:
            self.logger.debug(
                f"ℹ️ App-Health-Check {project.name}: env-var {api_key_env} nicht gesetzt — skip"
            )
            return None

        timeout = aiohttp.ClientTimeout(total=10)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    endpoint,
                    headers={'X-Agent-Key': api_key, 'Accept': 'application/json'},
                ) as resp:
                    if resp.status != 200:
                        self.logger.warning(
                            f"⚠️ App-Health-Stats {project.name}: HTTP {resp.status} von {endpoint}"
                        )
                        return None
                    return await resp.json()
        except asyncio.TimeoutError:
            self.logger.warning(f"⚠️ App-Health-Stats {project.name}: Timeout nach 10s")
            return None
        except aiohttp.ClientError as exc:
            self.logger.warning(f"⚠️ App-Health-Stats {project.name}: ClientError — {exc}")
            return None
        except (ValueError, KeyError) as exc:
            self.logger.warning(f"⚠️ App-Health-Stats {project.name}: JSON-Fehler — {exc}")
            return None

    async def _check_db_pool_saturation(self, project: ProjectStatus) -> None:
        """
        Check 6 (Phase 5c): DB-Connection-Pool-Saturation.

        Schwelle: > db_pool_saturation_warn (default 80) % -> Alert.
        Channel:  🧪-ci-zerodox (1463512208083521577), key='ci_zerodox'.
        Severity: HIGH.
        Cooldown: 30 Min.
        Frequenz: alle 5 Min.

        Datenquelle: ZERODOX /api/internal/health-stats Response.dbPool.saturationPercent.
        """
        if not self._should_run_health_check(project, 'db_pool_saturation'):
            return
        self._mark_health_check_ran(project, 'db_pool_saturation')

        stats = await self._fetch_app_health_stats(project)
        if not stats:
            return

        db_pool = stats.get('dbPool')
        if not isinstance(db_pool, dict):
            return

        threshold = float(self._get_health_threshold(project, 'db_pool_saturation_warn'))
        try:
            saturation = float(db_pool.get('saturationPercent', 0))
        except (TypeError, ValueError):
            return

        if saturation > threshold:
            await self._send_health_alert(
                project=project,
                check_type='db_pool_saturation',
                title=f"DB-Pool-Saturation hoch — {project.name}",
                description=(
                    f"Connection-Pool ist zu **{saturation:.0f}%** ausgelastet "
                    f"(Schwelle: > {threshold:.0f}%).\n\n"
                    f"Bei voll laufendem Pool kommen Auth-Logins und API-Requests zum Erliegen. "
                    f"Mögliche Ursachen: Long-running Queries, Pool-Limit zu niedrig, Verbindungs-Leak."
                ),
                severity=Severity.HIGH,
                fields=[
                    {"name": "Saturation", "value": f"{saturation:.0f}%", "inline": True},
                    {"name": "Schwelle", "value": f"> {threshold:.0f}%", "inline": True},
                    {"name": "Active", "value": str(db_pool.get('active', '?')), "inline": True},
                    {"name": "Idle", "value": str(db_pool.get('idle', '?')), "inline": True},
                    {"name": "Total / Max", "value": f"{db_pool.get('total', '?')} / {db_pool.get('max', '?')}", "inline": True},
                ],
                channel_key='ci_zerodox',
                fallback_channel_id=1463512208083521577,
            )
        else:
            self._clear_health_alert_cooldown(project, 'db_pool_saturation')

    async def _check_failed_login_rate(self, project: ProjectStatus) -> None:
        """
        Check 7 (Phase 5c): Failed-Login-Rate (5-Min-Fenster).

        Schwelle: > failed_login_per_5min_warn (default 100) -> Alert (Brute-Force-Verdacht).
        Channel:  🚨-critical (1441655480840617994), key='critical'.
        Severity: HIGH (CRITICAL bei > 500).
        Cooldown: 15 Min.
        Frequenz: alle 60s (Brute-Force kann schnell hochlaufen).

        Datenquelle: ZERODOX /api/internal/health-stats Response.failedLogins.count.
        DSGVO-konform: nur Counts, keine Email-Adressen oder User-IDs.
        """
        if not self._should_run_health_check(project, 'failed_login_rate'):
            return
        self._mark_health_check_ran(project, 'failed_login_rate')

        stats = await self._fetch_app_health_stats(project)
        if not stats:
            return

        failed = stats.get('failedLogins')
        if not isinstance(failed, dict):
            return

        threshold = int(self._get_health_threshold(project, 'failed_login_per_5min_warn'))
        try:
            count = int(failed.get('count', 0))
        except (TypeError, ValueError):
            return

        unique_emails = failed.get('uniqueEmails', 0)
        window_minutes = failed.get('windowMinutes', 5)

        if count > threshold:
            # Severity skaliert mit Volumen — > 500 ist klarer Brute-Force-Angriff
            severity = Severity.CRITICAL if count > threshold * 5 else Severity.HIGH

            # Verteilung gibt Hinweise: viele Versuche auf wenige Accounts = Credential-Stuffing
            distribution_hint = ""
            if unique_emails > 0:
                ratio = count / max(unique_emails, 1)
                if ratio > 10:
                    distribution_hint = " (Credential-Stuffing-Verdacht: viele Versuche pro Account)"
                elif ratio < 2:
                    distribution_hint = " (verteilt über viele Accounts: möglicher Broad-Sweep)"

            await self._send_health_alert(
                project=project,
                check_type='failed_login_rate',
                title=f"Failed-Login-Rate hoch — {project.name}",
                description=(
                    f"**{count} fehlgeschlagene Logins** in den letzten {window_minutes} Min "
                    f"(Schwelle: > {threshold}){distribution_hint}.\n\n"
                    f"Möglicherweise Brute-Force oder Credential-Stuffing. "
                    f"Bei Bedarf Account-Lockout-Schwellen prüfen oder IP-Sperren via Fail2ban."
                ),
                severity=severity,
                fields=[
                    {"name": "Versuche", "value": str(count), "inline": True},
                    {"name": "Schwelle", "value": f"> {threshold} / {window_minutes} Min", "inline": True},
                    {"name": "Unique Emails", "value": str(unique_emails), "inline": True},
                ],
                channel_key='critical',
                fallback_channel_id=1441655480840617994,
            )
        else:
            self._clear_health_alert_cooldown(project, 'failed_login_rate')
