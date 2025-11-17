"""
Service Manager - Project Service Control

Manages starting, stopping, and restarting services:
- Project-aware service management
- Graceful shutdown with timeouts
- Health check monitoring
- Dependency-aware ordering
- Discord notifications for downtime
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Callable

from .command_executor import CommandExecutor, CommandResult

logger = logging.getLogger('shadowops.service_manager')


class ServiceState(Enum):
    """Service state"""
    RUNNING = "running"
    STOPPED = "stopped"
    STARTING = "starting"
    STOPPING = "stopping"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass
class ServiceInfo:
    """Information about a service"""
    name: str
    project: str
    command: str  # Command to check if running
    start_command: Optional[str] = None
    stop_command: Optional[str] = None
    health_check: Optional[str] = None
    graceful_shutdown_timeout: int = 30
    state: ServiceState = ServiceState.UNKNOWN


class ServiceManager:
    """
    Manages project services (start, stop, health checks)

    Features:
    - Graceful shutdown with timeout
    - Health check monitoring
    - Dependency-aware service ordering
    - Discord notifications for downtime
    - Process state tracking
    """

    def __init__(
        self,
        executor: Optional[CommandExecutor] = None,
        discord_notify_callback: Optional[Callable] = None
    ):
        """
        Initialize service manager

        Args:
            executor: Command executor
            discord_notify_callback: Callback function for Discord notifications
        """
        self.executor = executor or CommandExecutor()
        self.discord_notify = discord_notify_callback

        # Define services for each project
        self.services = {
            'shadowops-bot': ServiceInfo(
                name='shadowops-bot',
                project='shadowops-bot',
                command="pgrep -f 'python.*shadowops'",
                start_command="cd /home/cmdshadow/shadowops-bot && python src/bot.py &",
                stop_command="pkill -f 'python.*shadowops'",
                health_check="pgrep -f 'python.*shadowops'",
                graceful_shutdown_timeout=30
            ),
            'guildscout': ServiceInfo(
                name='guildscout',
                project='guildscout',
                command="pgrep -f 'python.*guildscout|python.*run.py.*GuildScout'",
                start_command="cd /home/cmdshadow/GuildScout && python run.py &",
                stop_command="pkill -f 'python.*guildscout|python.*run.py.*GuildScout'",
                health_check="pgrep -f 'python.*guildscout|python.*run.py'",
                graceful_shutdown_timeout=15
            ),
            'sicherheitstool': ServiceInfo(
                name='sicherheitstool',
                project='sicherheitstool',
                command="pgrep -f 'node.*sicherheitstool|npm.*start.*project'",
                # SECURITY: Database credentials must be set in environment (.env file or systemd service)
                # Never hardcode passwords in code! Use: DATABASE_URL="postgresql://admin:PASSWORD@..."
                start_command='cd /home/cmdshadow/project && PORT=3001 npm start &',
                stop_command="pkill -f 'node.*sicherheitstool|npm.*start.*project'",
                health_check="curl -f http://localhost:3001/api/health || exit 1",
                graceful_shutdown_timeout=60  # Longer for production
            ),
            'nexus': ServiceInfo(
                name='nexus',
                project='nexus',
                command="pgrep -f 'nexus|sonatype'",
                start_command="systemctl start nexus",
                stop_command="systemctl stop nexus",
                health_check="systemctl is-active nexus",
                graceful_shutdown_timeout=120  # Nexus needs time
            ),
            'postgresql': ServiceInfo(
                name='postgresql',
                project='system',
                command="pgrep -f postgres",
                start_command="systemctl start postgresql",
                stop_command="systemctl stop postgresql",
                health_check="systemctl is-active postgresql",
                graceful_shutdown_timeout=30
            )
        }

        logger.info(f"🔧 Service Manager initialized ({len(self.services)} services)")

    async def get_service_state(self, service_name: str) -> ServiceState:
        """
        Get current state of a service

        Args:
            service_name: Name of service

        Returns:
            ServiceState
        """
        if service_name not in self.services:
            return ServiceState.UNKNOWN

        service = self.services[service_name]

        try:
            result = await self.executor.execute(
                service.command,
                timeout=10
            )

            if result.success:
                service.state = ServiceState.RUNNING
                return ServiceState.RUNNING
            else:
                service.state = ServiceState.STOPPED
                return ServiceState.STOPPED

        except Exception as e:
            logger.error(f"❌ Failed to check service {service_name}: {e}")
            service.state = ServiceState.UNKNOWN
            return ServiceState.UNKNOWN

    async def stop_service(
        self,
        service_name: str,
        graceful: bool = True,
        notify: bool = True
    ) -> bool:
        """
        Stop a service

        Args:
            service_name: Name of service to stop
            graceful: Use graceful shutdown (wait for timeout)
            notify: Send Discord notification

        Returns:
            True if stopped successfully
        """
        if service_name not in self.services:
            logger.error(f"❌ Unknown service: {service_name}")
            return False

        service = self.services[service_name]

        logger.info(f"⏸️ Stopping service: {service_name}")

        # Send Discord notification
        if notify and self.discord_notify:
            await self.discord_notify(
                f"⏸️ Stopping {service.project} for maintenance...",
                level='warning'
            )

        service.state = ServiceState.STOPPING

        try:
            # Check if service is running
            current_state = await self.get_service_state(service_name)

            if current_state == ServiceState.STOPPED:
                logger.info(f"   ℹ️ Service already stopped: {service_name}")
                return True

            # Execute stop command
            if service.stop_command:
                result = await self.executor.execute(
                    service.stop_command,
                    timeout=service.graceful_shutdown_timeout if graceful else 10,
                    sudo=True
                )

                if not result.success:
                    logger.warning(f"⚠️ Stop command failed for {service_name}, trying force kill")

                    # Force kill if stop command failed
                    await self.executor.execute(
                        f"pkill -9 -f '{service.name}'",
                        timeout=10,
                        sudo=True
                    )

            # Wait for service to stop (if graceful)
            if graceful:
                logger.info(f"   ⏳ Waiting for graceful shutdown ({service.graceful_shutdown_timeout}s)...")

                for i in range(service.graceful_shutdown_timeout):
                    await asyncio.sleep(1)

                    state = await self.get_service_state(service_name)

                    if state == ServiceState.STOPPED:
                        logger.info(f"   ✅ Service stopped gracefully after {i+1}s")
                        break
                else:
                    # Timeout reached, force kill
                    logger.warning(f"   ⚠️ Graceful shutdown timeout, force killing...")

                    await self.executor.execute(
                        f"pkill -9 -f '{service.name}'",
                        timeout=10,
                        sudo=True
                    )

            # Verify stopped
            final_state = await self.get_service_state(service_name)

            if final_state == ServiceState.STOPPED:
                service.state = ServiceState.STOPPED
                logger.info(f"✅ Service stopped: {service_name}")
                return True
            else:
                service.state = ServiceState.FAILED
                logger.error(f"❌ Failed to stop service: {service_name}")
                return False

        except Exception as e:
            logger.error(f"❌ Error stopping service {service_name}: {e}", exc_info=True)
            service.state = ServiceState.FAILED
            return False

    async def start_service(
        self,
        service_name: str,
        notify: bool = True,
        wait_for_healthy: bool = True
    ) -> bool:
        """
        Start a service

        Args:
            service_name: Name of service to start
            notify: Send Discord notification
            wait_for_healthy: Wait for health check to pass

        Returns:
            True if started successfully
        """
        if service_name not in self.services:
            logger.error(f"❌ Unknown service: {service_name}")
            return False

        service = self.services[service_name]

        logger.info(f"▶️ Starting service: {service_name}")

        service.state = ServiceState.STARTING

        try:
            # Check if already running
            current_state = await self.get_service_state(service_name)

            if current_state == ServiceState.RUNNING:
                logger.info(f"   ℹ️ Service already running: {service_name}")
                return True

            # Execute start command
            if service.start_command:
                result = await self.executor.execute(
                    service.start_command,
                    timeout=60,
                    sudo=True
                )

                if not result.success:
                    logger.error(f"❌ Start command failed for {service_name}: {result.error_message}")
                    service.state = ServiceState.FAILED
                    return False

            # Wait for service to start
            logger.info(f"   ⏳ Waiting for service to start...")

            max_wait = 30  # 30 seconds max wait
            for i in range(max_wait):
                await asyncio.sleep(1)

                state = await self.get_service_state(service_name)

                if state == ServiceState.RUNNING:
                    logger.info(f"   ✅ Service started after {i+1}s")
                    break
            else:
                logger.error(f"❌ Service did not start within {max_wait}s")
                service.state = ServiceState.FAILED
                return False

            # Wait for health check if enabled
            if wait_for_healthy and service.health_check:
                healthy = await self._wait_for_healthy(service_name, max_wait=60)

                if not healthy:
                    logger.error(f"❌ Service started but health check failed: {service_name}")
                    service.state = ServiceState.FAILED
                    return False

            service.state = ServiceState.RUNNING
            logger.info(f"✅ Service started: {service_name}")

            # Send Discord notification
            if notify and self.discord_notify:
                await self.discord_notify(
                    f"▶️ {service.project} is back online",
                    level='success'
                )

            return True

        except Exception as e:
            logger.error(f"❌ Error starting service {service_name}: {e}", exc_info=True)
            service.state = ServiceState.FAILED
            return False

    async def restart_service(
        self,
        service_name: str,
        notify: bool = True
    ) -> bool:
        """
        Restart a service

        Args:
            service_name: Name of service to restart
            notify: Send Discord notification

        Returns:
            True if restarted successfully
        """
        logger.info(f"🔄 Restarting service: {service_name}")

        # Stop
        stopped = await self.stop_service(service_name, graceful=True, notify=notify)

        if not stopped:
            logger.error(f"❌ Failed to stop service for restart: {service_name}")
            return False

        # Wait a bit between stop and start
        await asyncio.sleep(2)

        # Start
        started = await self.start_service(service_name, notify=notify, wait_for_healthy=True)

        if started:
            logger.info(f"✅ Service restarted: {service_name}")
            return True
        else:
            logger.error(f"❌ Failed to restart service: {service_name}")
            return False

    async def stop_services_batch(
        self,
        service_names: List[str],
        reverse_order: bool = True
    ) -> Dict[str, bool]:
        """
        Stop multiple services in order

        Args:
            service_names: List of services to stop
            reverse_order: Stop in reverse order (default: True)

        Returns:
            Dict mapping service name to success status
        """
        logger.info(f"⏸️ Stopping {len(service_names)} services...")

        services_to_stop = list(reversed(service_names)) if reverse_order else service_names

        results = {}

        for service_name in services_to_stop:
            success = await self.stop_service(service_name, graceful=True, notify=False)
            results[service_name] = success

        # Send summary notification
        if self.discord_notify:
            success_count = sum(1 for s in results.values() if s)
            await self.discord_notify(
                f"⏸️ Stopped {success_count}/{len(service_names)} services",
                level='warning'
            )

        return results

    async def start_services_batch(
        self,
        service_names: List[str],
        forward_order: bool = True
    ) -> Dict[str, bool]:
        """
        Start multiple services in order

        Args:
            service_names: List of services to start
            forward_order: Start in forward order (default: True)

        Returns:
            Dict mapping service name to success status
        """
        logger.info(f"▶️ Starting {len(service_names)} services...")

        services_to_start = service_names if forward_order else list(reversed(service_names))

        results = {}

        for service_name in services_to_start:
            success = await self.start_service(service_name, notify=False, wait_for_healthy=True)
            results[service_name] = success

            if not success:
                # If a service fails to start, stop here
                logger.error(f"❌ Service start failed, stopping batch: {service_name}")
                break

        # Send summary notification
        if self.discord_notify:
            success_count = sum(1 for s in results.values() if s)
            await self.discord_notify(
                f"▶️ Started {success_count}/{len(service_names)} services",
                level='success' if success_count == len(service_names) else 'warning'
            )

        return results

    async def health_check(self, service_name: str) -> bool:
        """
        Run health check for a service

        Args:
            service_name: Name of service

        Returns:
            True if healthy
        """
        if service_name not in self.services:
            return False

        service = self.services[service_name]

        if not service.health_check:
            # No health check defined, just check if running
            state = await self.get_service_state(service_name)
            return state == ServiceState.RUNNING

        try:
            result = await self.executor.execute(
                service.health_check,
                timeout=10
            )

            return result.success

        except Exception as e:
            logger.error(f"❌ Health check failed for {service_name}: {e}")
            return False

    async def _wait_for_healthy(
        self,
        service_name: str,
        max_wait: int = 60
    ) -> bool:
        """Wait for service to become healthy"""

        logger.info(f"   🏥 Waiting for health check to pass...")

        for i in range(max_wait):
            await asyncio.sleep(1)

            healthy = await self.health_check(service_name)

            if healthy:
                logger.info(f"   ✅ Health check passed after {i+1}s")
                return True

        logger.error(f"   ❌ Health check timeout after {max_wait}s")
        return False

    def get_service_info(self, service_name: str) -> Optional[ServiceInfo]:
        """Get information about a service"""
        return self.services.get(service_name)

    def list_services(self) -> List[str]:
        """List all managed services"""
        return list(self.services.keys())

    async def get_all_states(self) -> Dict[str, ServiceState]:
        """Get states of all services"""

        states = {}

        for service_name in self.services.keys():
            state = await self.get_service_state(service_name)
            states[service_name] = state

        return states

    def get_stats(self) -> Dict:
        """Get service manager statistics"""

        states = {
            ServiceState.RUNNING: 0,
            ServiceState.STOPPED: 0,
            ServiceState.FAILED: 0,
            ServiceState.UNKNOWN: 0
        }

        for service in self.services.values():
            states[service.state] = states.get(service.state, 0) + 1

        return {
            'total_services': len(self.services),
            'states': {state.value: count for state, count in states.items()},
            'services': [
                {
                    'name': s.name,
                    'project': s.project,
                    'state': s.state.value
                }
                for s in self.services.values()
            ]
        }
