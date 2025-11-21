"""
Auto-Deployment System for ShadowOps Bot
Handles safe deployment with backups, tests, and rollback
"""

import asyncio
import logging
import subprocess
import shutil
import time
from typing import Dict, Optional, List
from datetime import datetime
from pathlib import Path
import discord

logger = logging.getLogger(__name__)


class DeploymentManager:
    """
    Automated deployment system with safety checks

    Features:
    - Git pull automation
    - Pre-deployment tests
    - Automatic backup creation
    - Health checks after deployment
    - Automatic rollback on failure
    - Discord notifications
    """

    def __init__(self, bot, config: Dict):
        """
        Initialize deployment manager

        Args:
            bot: Discord bot instance
            config: Configuration dictionary with projects and deployment settings
        """
        self.bot = bot
        self.config = config
        self.logger = logger

        # Project configurations
        self.projects = self._load_projects()

        # Deployment settings
        self.backup_dir = Path(config.get('deployment', {}).get('backup_dir', 'backups'))
        self.backup_dir.mkdir(parents=True, exist_ok=True)

        self.max_backups_per_project = config.get('deployment', {}).get('max_backups', 5)
        self.health_check_timeout = config.get('deployment', {}).get('health_check_timeout', 30)
        self.test_timeout = config.get('deployment', {}).get('test_timeout', 300)

        # Discord notification channel
        self.deployment_channel_id = config.get('channels', {}).get('deployment_log', 0)

        # Track active deployments
        self.active_deployments: Dict[str, bool] = {}

        self.logger.info(f"🔧 Deployment Manager initialized for {len(self.projects)} projects")

    def _load_projects(self) -> Dict[str, Dict]:
        """Load project configurations from config"""
        projects = {}
        projects_config = self.config.get('projects', {})

        for project_name, project_config in projects_config.items():
            if not project_config.get('enabled', False):
                continue

            projects[project_name] = {
                'name': project_name,
                'path': Path(project_config.get('path', '')),
                'branch': project_config.get('branch', 'main'),
                'run_tests': project_config.get('deploy', {}).get('run_tests', False),
                'test_command': project_config.get('deploy', {}).get('test_command', 'pytest'),
                'post_deploy_command': project_config.get('deploy', {}).get('post_deploy_command', None),
                'health_check_url': project_config.get('monitor', {}).get('url', ''),
                'service_name': project_config.get('deploy', {}).get('service_name', None)
            }

            self.logger.info(f"✅ Loaded deployment config for: {project_name}")

        return projects

    async def deploy_project(
        self, project_name: str, branch: Optional[str] = None
    ) -> Dict:
        """
        Deploy a project with full safety workflow

        Args:
            project_name: Name of the project to deploy
            branch: Git branch to deploy (defaults to project config)

        Returns:
            Deployment result dictionary with success status and details
        """
        start_time = time.time()

        # Check if project exists
        if project_name not in self.projects:
            error_msg = f"Project '{project_name}' not found in deployment config"
            self.logger.error(f"❌ {error_msg}")
            return {
                'success': False,
                'error': error_msg,
                'duration_seconds': 0
            }

        # Check if deployment is already in progress
        if self.active_deployments.get(project_name, False):
            error_msg = f"Deployment already in progress for '{project_name}'"
            self.logger.warning(f"⚠️ {error_msg}")
            return {
                'success': False,
                'error': error_msg,
                'duration_seconds': 0
            }

        # Mark deployment as active
        self.active_deployments[project_name] = True

        try:
            project = self.projects[project_name]
            deploy_branch = branch or project['branch']

            self.logger.info(f"🚀 Starting deployment: {project_name} @ {deploy_branch}")

            result = {
                'success': False,
                'project': project_name,
                'branch': deploy_branch,
                'duration_seconds': 0,
                'tests_passed': None,
                'backup_created': False,
                'deployed': False,
                'rolled_back': False,
                'error': None
            }

            # Step 1: Validate project path
            if not project['path'].exists():
                raise DeploymentError(f"Project path does not exist: {project['path']}")

            # Step 2: Create backup
            self.logger.info(f"📦 Creating backup for {project_name}")
            backup_path = await self._create_backup(project)
            result['backup_created'] = True
            self.logger.info(f"✅ Backup created: {backup_path}")

            # Step 3: Pull latest code
            self.logger.info(f"📥 Pulling latest code from {deploy_branch}")
            await self._git_pull(project, deploy_branch)

            # Step 4: Run tests (if configured)
            if project['run_tests']:
                self.logger.info(f"🧪 Running tests for {project_name}")
                tests_passed = await self._run_tests(project)
                result['tests_passed'] = tests_passed

                if not tests_passed:
                    raise DeploymentError("Tests failed")

                self.logger.info(f"✅ Tests passed")
            else:
                self.logger.info(f"⏭️ Skipping tests (not configured)")

            # Step 5: Execute post-deploy command (if configured)
            if project['post_deploy_command']:
                self.logger.info(f"⚙️ Running post-deploy command")
                await self._run_post_deploy_command(project)
                self.logger.info(f"✅ Post-deploy command completed")

            # Step 6: Restart service (if configured)
            if project['service_name']:
                self.logger.info(f"🔄 Restarting service: {project['service_name']}")
                await self._restart_service(project)
                self.logger.info(f"✅ Service restarted")

            result['deployed'] = True

            # Step 7: Health check
            if project['health_check_url']:
                self.logger.info(f"🏥 Running health check")
                health_ok = await self._health_check(project)

                if not health_ok:
                    raise DeploymentError("Health check failed after deployment")

                self.logger.info(f"✅ Health check passed")

            # Success!
            duration = time.time() - start_time
            result['success'] = True
            result['duration_seconds'] = duration

            self.logger.info(f"✅ Deployment successful: {project_name} ({duration:.1f}s)")

            return result

        except DeploymentError as e:
            # Deployment failed, attempt rollback
            self.logger.error(f"❌ Deployment failed: {e}")

            result['error'] = str(e)
            duration = time.time() - start_time
            result['duration_seconds'] = duration

            # Rollback if backup exists
            if result['backup_created']:
                try:
                    self.logger.warning(f"🔄 Attempting rollback for {project_name}")
                    await self._rollback(project, backup_path)
                    result['rolled_back'] = True
                    self.logger.info(f"✅ Rollback successful")

                    # Restart service after rollback
                    if project['service_name']:
                        await self._restart_service(project)

                except Exception as rollback_error:
                    self.logger.error(
                        f"❌ Rollback failed: {rollback_error}",
                        exc_info=True
                    )
                    result['error'] += f" | Rollback failed: {rollback_error}"

            return result

        except Exception as e:
            # Unexpected error
            self.logger.error(f"💥 Deployment exception: {e}", exc_info=True)

            result['error'] = str(e)
            duration = time.time() - start_time
            result['duration_seconds'] = duration

            return result

        finally:
            # Mark deployment as complete
            self.active_deployments[project_name] = False

    async def _create_backup(self, project: Dict) -> Path:
        """
        Create timestamped backup of project

        Args:
            project: Project configuration

        Returns:
            Path to backup directory
        """
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_name = f"{project['name']}_{timestamp}"
        backup_path = self.backup_dir / backup_name

        # Create backup using rsync for efficiency
        cmd = [
            'rsync', '-a',
            '--exclude=.git',
            '--exclude=__pycache__',
            '--exclude=*.pyc',
            '--exclude=node_modules',
            '--exclude=venv',
            str(project['path']) + '/',
            str(backup_path) + '/'
        ]

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            raise DeploymentError(f"Backup failed: {stderr.decode()}")

        # Clean up old backups
        await self._cleanup_old_backups(project['name'])

        return backup_path

    async def _cleanup_old_backups(self, project_name: str):
        """Remove old backups, keeping only the most recent N"""
        project_backups = sorted(
            [b for b in self.backup_dir.iterdir() if b.name.startswith(project_name)],
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )

        # Remove backups beyond max count
        for old_backup in project_backups[self.max_backups_per_project:]:
            self.logger.info(f"🗑️ Removing old backup: {old_backup.name}")
            shutil.rmtree(old_backup)

    async def _git_pull(self, project: Dict, branch: str):
        """
        Pull latest code from git

        Args:
            project: Project configuration
            branch: Branch to pull
        """
        # Fetch latest
        fetch_cmd = ['git', 'fetch', 'origin', branch]
        process = await asyncio.create_subprocess_exec(
            *fetch_cmd,
            cwd=str(project['path']),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await process.communicate()

        if process.returncode != 0:
            raise DeploymentError("Git fetch failed")

        # Checkout branch
        checkout_cmd = ['git', 'checkout', branch]
        process = await asyncio.create_subprocess_exec(
            *checkout_cmd,
            cwd=str(project['path']),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await process.communicate()

        if process.returncode != 0:
            raise DeploymentError(f"Git checkout {branch} failed")

        # Pull latest
        pull_cmd = ['git', 'pull', 'origin', branch]
        process = await asyncio.create_subprocess_exec(
            *pull_cmd,
            cwd=str(project['path']),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            raise DeploymentError(f"Git pull failed: {stderr.decode()}")

    async def _run_tests(self, project: Dict) -> bool:
        """
        Run project tests

        Args:
            project: Project configuration

        Returns:
            True if tests passed
        """
        test_cmd = project['test_command'].split()

        process = await asyncio.create_subprocess_exec(
            *test_cmd,
            cwd=str(project['path']),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=self.test_timeout
            )

            return process.returncode == 0

        except asyncio.TimeoutError:
            process.kill()
            raise DeploymentError(f"Tests timed out after {self.test_timeout}s")

    async def _run_post_deploy_command(self, project: Dict):
        """
        Run post-deployment command (e.g., npm install, pip install)

        Args:
            project: Project configuration
        """
        cmd = project['post_deploy_command'].split()

        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(project['path']),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            raise DeploymentError(f"Post-deploy command failed: {stderr.decode()}")

    async def _restart_service(self, project: Dict):
        """
        Restart systemd service

        Args:
            project: Project configuration
        """
        service_name = project['service_name']

        cmd = ['sudo', 'systemctl', 'restart', service_name]

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            raise DeploymentError(f"Service restart failed: {stderr.decode()}")

        # Wait a moment for service to start
        await asyncio.sleep(2)

    async def _health_check(self, project: Dict) -> bool:
        """
        Perform health check on deployed application

        Args:
            project: Project configuration

        Returns:
            True if health check passed
        """
        import aiohttp

        url = project['health_check_url']

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=self.health_check_timeout)
                ) as response:
                    return response.status == 200

        except Exception as e:
            self.logger.warning(f"⚠️ Health check failed: {e}")
            return False

    async def _rollback(self, project: Dict, backup_path: Path):
        """
        Rollback to backup

        Args:
            project: Project configuration
            backup_path: Path to backup directory
        """
        if not backup_path.exists():
            raise DeploymentError(f"Backup not found: {backup_path}")

        # Restore from backup using rsync
        cmd = [
            'rsync', '-a', '--delete',
            str(backup_path) + '/',
            str(project['path']) + '/'
        ]

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            raise DeploymentError(f"Rollback failed: {stderr.decode()}")


class DeploymentError(Exception):
    """Exception raised for deployment failures"""
    pass
