"""
Trivy Fixer - Docker Vulnerability Remediation

Fixes vulnerabilities detected by Trivy:
- NPM package updates
- APT package updates
- Base image updates
- Docker image rebuilds
- Verification with re-scan
"""

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from ..command_executor import CommandExecutor, CommandResult
from ..backup_manager import BackupManager

logger = logging.getLogger('shadowops.trivy_fixer')


@dataclass
class VulnerabilityFix:
    """Information about a vulnerability fix"""
    cve_id: str
    package_name: str
    current_version: str
    fixed_version: str
    severity: str
    fix_method: str  # 'npm_audit', 'apt_upgrade', 'base_image'


class TrivyFixer:
    """
    Fixes Docker vulnerabilities detected by Trivy

    Strategies:
    1. NPM Package Updates (npm audit fix)
    2. APT Package Updates (apt-get upgrade)
    3. Base Image Updates (update Dockerfile FROM)
    4. Full rebuild with verification
    """

    def __init__(
        self,
        executor: Optional[CommandExecutor] = None,
        backup_manager: Optional[BackupManager] = None
    ):
        """
        Initialize Trivy fixer

        Args:
            executor: Command executor
            backup_manager: Backup manager
        """
        self.executor = executor or CommandExecutor()
        self.backup_manager = backup_manager or BackupManager()

        logger.info("🐳 Trivy Fixer initialized")

    async def fix(
        self,
        event: Dict,
        strategy: Dict,
        project_path: Optional[str] = None
    ) -> Dict:
        """
        Fix Docker vulnerabilities

        Args:
            event: Security event with vulnerability details
            strategy: AI-generated fix strategy
            project_path: Path to project (auto-detected if not provided)

        Returns:
            Dict with fix result
        """
        logger.info("🐳 Starting Trivy fix")
        logger.info(f"   Strategy: {strategy.get('description', 'Unknown')}")

        try:
            # Parse event details
            event_details = event.get('event_details', {})
            vulnerabilities = event_details.get('vulnerabilities', {})

            # Detect project path if not provided
            if not project_path:
                project_path = await self._detect_project_path(event, strategy)

            logger.info(f"   Project: {project_path}")

            # Determine fix method
            fix_method = await self._determine_fix_method(
                vulnerabilities,
                strategy,
                project_path
            )

            logger.info(f"   Fix method: {fix_method}")

            # Create backup
            backup_info = await self._create_backup(project_path, fix_method)

            # Apply fix based on method
            if fix_method == 'npm_audit':
                result = await self._fix_npm_vulnerabilities(
                    project_path,
                    vulnerabilities,
                    strategy
                )
            elif fix_method == 'apt_upgrade':
                result = await self._fix_apt_vulnerabilities(
                    project_path,
                    vulnerabilities,
                    strategy
                )
            elif fix_method == 'base_image':
                result = await self._fix_base_image(
                    project_path,
                    vulnerabilities,
                    strategy
                )
            elif fix_method == 'combined':
                # Multiple fix methods needed
                result = await self._fix_combined(
                    project_path,
                    vulnerabilities,
                    strategy
                )
            else:
                logger.error(f"❌ Unknown fix method: {fix_method}")
                return {
                    'status': 'failed',
                    'error': f'Unknown fix method: {fix_method}'
                }

            # Check if fix was successful
            if result['status'] == 'success':
                # Rebuild Docker image
                rebuild_result = await self._rebuild_docker_image(
                    project_path,
                    strategy
                )

                if rebuild_result['status'] == 'success':
                    # Verify fix with Trivy re-scan (skip if no Docker rebuild happened)
                    if rebuild_result.get('skipped'):
                        # No Docker image, skip verification (Python project running directly)
                        logger.info("✅ Trivy fix successful (non-Docker project, verification skipped)")
                        return {
                            'status': 'success',
                            'message': 'NPM vulnerabilities fixed (no Docker verification needed)',
                            'details': {
                                'method': fix_method,
                                'note': 'Python project without Dockerfile - running directly'
                            }
                        }

                    verification = await self._verify_fix(
                        project_path,
                        vulnerabilities
                    )

                    if verification['success']:
                        logger.info("✅ Trivy fix successful and verified")
                        return {
                            'status': 'success',
                            'message': 'Vulnerabilities fixed and verified',
                            'details': {
                                'method': fix_method,
                                'fixed_count': verification.get('fixed_count', 0),
                                'remaining_count': verification.get('remaining_count', 0)
                            }
                        }
                    else:
                        # Fix didn't work, rollback
                        logger.warning("⚠️ Fix verification failed, rolling back")
                        await self._rollback(backup_info)
                        return {
                            'status': 'failed',
                            'error': 'Fix verification failed',
                            'details': verification
                        }
                else:
                    # Rebuild failed, rollback
                    logger.error("❌ Docker rebuild failed, rolling back")
                    await self._rollback(backup_info)
                    return rebuild_result
            else:
                # Fix failed, rollback
                logger.error("❌ Fix failed, rolling back")
                await self._rollback(backup_info)
                return result

        except Exception as e:
            logger.error(f"❌ Trivy fix error: {e}", exc_info=True)
            return {
                'status': 'failed',
                'error': str(e)
            }

    async def _detect_project_path(self, event: Dict, strategy: Dict) -> str:
        """Detect project path from event or strategy"""

        # Check event for image/container name
        event_details = event.get('event_details', {})
        source = event_details.get('source', '')

        # Try to extract project from source
        if 'shadowops' in source.lower():
            return '/home/cmdshadow/shadowops-bot'
        elif 'guildscout' in source.lower():
            return '/home/cmdshadow/GuildScout'
        elif 'sicherheitstool' in source.lower() or 'project' in source.lower():
            return '/home/cmdshadow/project'

        # Check strategy description
        strategy_desc = strategy.get('description', '').lower()

        if 'shadowops' in strategy_desc:
            return '/home/cmdshadow/shadowops-bot'
        elif 'guildscout' in strategy_desc:
            return '/home/cmdshadow/GuildScout'
        elif 'sicherheitstool' in strategy_desc:
            return '/home/cmdshadow/project'

        # Default to shadowops-bot
        logger.warning("⚠️ Could not detect project path, using default")
        return '/home/cmdshadow/shadowops-bot'

    async def _determine_fix_method(
        self,
        vulnerabilities: Dict,
        strategy: Dict,
        project_path: str
    ) -> str:
        """Determine which fix method to use"""

        strategy_desc = strategy.get('description', '').lower()

        # Check for NPM vulnerabilities
        has_npm = 'npm' in strategy_desc or 'package.json' in strategy_desc

        # Check for APT vulnerabilities
        has_apt = 'apt' in strategy_desc or 'debian' in strategy_desc or 'ubuntu' in strategy_desc

        # Check for base image updates
        has_base = 'base image' in strategy_desc or 'from' in strategy_desc

        # Determine method
        if has_npm and has_apt:
            return 'combined'
        elif has_npm:
            return 'npm_audit'
        elif has_apt:
            return 'apt_upgrade'
        elif has_base:
            return 'base_image'
        else:
            # Default to npm_audit for Node.js projects
            return 'npm_audit'

    async def _create_backup(self, project_path: str, fix_method: str) -> List:
        """Create backups before making changes"""

        backups = []

        logger.info("💾 Creating backups...")

        try:
            # Always backup package files
            if fix_method in ['npm_audit', 'combined']:
                package_json = os.path.join(project_path, 'package.json')
                package_lock = os.path.join(project_path, 'package-lock.json')

                if os.path.exists(package_json):
                    backup = await self.backup_manager.create_backup(
                        package_json,
                        backup_type='file',
                        metadata={'fix_method': fix_method}
                    )
                    backups.append(backup)

                if os.path.exists(package_lock):
                    backup = await self.backup_manager.create_backup(
                        package_lock,
                        backup_type='file',
                        metadata={'fix_method': fix_method}
                    )
                    backups.append(backup)

            # Backup Dockerfile if base image update
            if fix_method in ['base_image', 'combined']:
                dockerfile = os.path.join(project_path, 'Dockerfile')

                if os.path.exists(dockerfile):
                    backup = await self.backup_manager.create_backup(
                        dockerfile,
                        backup_type='file',
                        metadata={'fix_method': fix_method}
                    )
                    backups.append(backup)

            logger.info(f"✅ Created {len(backups)} backups")

        except Exception as e:
            logger.error(f"❌ Backup creation failed: {e}")
            raise

        return backups

    async def _fix_npm_vulnerabilities(
        self,
        project_path: str,
        vulnerabilities: Dict,
        strategy: Dict
    ) -> Dict:
        """Fix NPM package vulnerabilities"""

        logger.info("📦 Fixing NPM vulnerabilities...")

        try:
            # Check if package.json exists
            package_json = os.path.join(project_path, 'package.json')
            if not os.path.exists(package_json):
                return {
                    'status': 'failed',
                    'error': 'No package.json found'
                }

            # Run npm audit fix
            logger.info("   Running: npm audit fix")

            result = await self.executor.execute(
                "npm audit fix",
                working_dir=project_path,
                timeout=300
            )

            if not result.success:
                # Try with --force if regular fix failed
                logger.warning("⚠️ npm audit fix failed, trying --force")

                result = await self.executor.execute(
                    "npm audit fix --force",
                    working_dir=project_path,
                    timeout=300
                )

            if result.success:
                logger.info("✅ NPM audit fix completed")

                # Run npm install to ensure consistency
                install_result = await self.executor.execute(
                    "npm install",
                    working_dir=project_path,
                    timeout=300
                )

                if not install_result.success:
                    return {
                        'status': 'failed',
                        'error': 'npm install failed after audit fix'
                    }

                return {
                    'status': 'success',
                    'message': 'NPM vulnerabilities fixed',
                    'output': result.stdout
                }
            else:
                return {
                    'status': 'failed',
                    'error': result.error_message
                }

        except Exception as e:
            logger.error(f"❌ NPM fix error: {e}")
            return {
                'status': 'failed',
                'error': str(e)
            }

    async def _fix_apt_vulnerabilities(
        self,
        project_path: str,
        vulnerabilities: Dict,
        strategy: Dict
    ) -> Dict:
        """Fix APT package vulnerabilities"""

        logger.info("📦 Fixing APT vulnerabilities...")

        try:
            # Extract package names from vulnerabilities
            packages_to_upgrade = []

            # Parse vulnerability details
            for cve, details in vulnerabilities.items():
                if isinstance(details, dict):
                    package_name = details.get('package', '')
                    if package_name:
                        packages_to_upgrade.append(package_name)

            if not packages_to_upgrade:
                # Upgrade all packages
                logger.info("   Upgrading all packages")

                # Update package list
                update_result = await self.executor.execute(
                    "apt-get update",
                    sudo=True,
                    timeout=120
                )

                if not update_result.success:
                    return {
                        'status': 'failed',
                        'error': 'apt-get update failed'
                    }

                # Upgrade packages
                upgrade_result = await self.executor.execute(
                    "apt-get upgrade -y",
                    sudo=True,
                    timeout=600
                )

                if upgrade_result.success:
                    return {
                        'status': 'success',
                        'message': 'APT packages upgraded'
                    }
                else:
                    return {
                        'status': 'failed',
                        'error': upgrade_result.error_message
                    }
            else:
                # Upgrade specific packages
                logger.info(f"   Upgrading packages: {', '.join(packages_to_upgrade)}")

                # Update package list
                await self.executor.execute("apt-get update", sudo=True, timeout=120)

                # Upgrade specific packages
                for package in packages_to_upgrade:
                    result = await self.executor.execute(
                        f"apt-get install --only-upgrade -y {package}",
                        sudo=True,
                        timeout=300
                    )

                    if not result.success:
                        logger.warning(f"⚠️ Failed to upgrade {package}")

                return {
                    'status': 'success',
                    'message': f'Upgraded {len(packages_to_upgrade)} packages'
                }

        except Exception as e:
            logger.error(f"❌ APT fix error: {e}")
            return {
                'status': 'failed',
                'error': str(e)
            }

    async def _fix_base_image(
        self,
        project_path: str,
        vulnerabilities: Dict,
        strategy: Dict
    ) -> Dict:
        """Fix by updating base Docker image"""

        logger.info("🐳 Updating base Docker image...")

        try:
            dockerfile = os.path.join(project_path, 'Dockerfile')

            if not os.path.exists(dockerfile):
                return {
                    'status': 'failed',
                    'error': 'No Dockerfile found'
                }

            # Read Dockerfile
            with open(dockerfile, 'r') as f:
                content = f.read()

            # Extract current base image
            from_match = re.search(r'^FROM\s+([^\s]+)', content, re.MULTILINE)

            if not from_match:
                return {
                    'status': 'failed',
                    'error': 'Could not find FROM instruction in Dockerfile'
                }

            current_image = from_match.group(1)
            logger.info(f"   Current base image: {current_image}")

            # Determine new base image from strategy
            strategy_desc = strategy.get('description', '')

            # Extract suggested image from strategy (if present)
            new_image_match = re.search(r'update.*to\s+([^\s,]+)', strategy_desc, re.IGNORECASE)

            if new_image_match:
                new_image = new_image_match.group(1)
            else:
                # Default: update to latest minor version
                if ':' in current_image:
                    base, tag = current_image.split(':', 1)

                    # Try to increment version
                    version_match = re.match(r'(\d+)\.(\d+)', tag)
                    if version_match:
                        major = version_match.group(1)
                        # Update to latest in same major version
                        new_image = f"{base}:{major}-alpine"
                    else:
                        new_image = f"{base}:latest"
                else:
                    new_image = f"{current_image}:latest"

            logger.info(f"   New base image: {new_image}")

            # Update Dockerfile
            new_content = content.replace(
                f"FROM {current_image}",
                f"FROM {new_image}"
            )

            with open(dockerfile, 'w') as f:
                f.write(new_content)

            logger.info("✅ Dockerfile updated")

            return {
                'status': 'success',
                'message': f'Base image updated: {current_image} → {new_image}',
                'old_image': current_image,
                'new_image': new_image
            }

        except Exception as e:
            logger.error(f"❌ Base image update error: {e}")
            return {
                'status': 'failed',
                'error': str(e)
            }

    async def _fix_combined(
        self,
        project_path: str,
        vulnerabilities: Dict,
        strategy: Dict
    ) -> Dict:
        """Apply multiple fix methods"""

        logger.info("🔧 Applying combined fixes...")

        results = []

        # Try NPM fix first
        npm_result = await self._fix_npm_vulnerabilities(
            project_path,
            vulnerabilities,
            strategy
        )
        results.append(('npm', npm_result))

        # Then APT fix
        apt_result = await self._fix_apt_vulnerabilities(
            project_path,
            vulnerabilities,
            strategy
        )
        results.append(('apt', apt_result))

        # Check overall success
        success_count = sum(1 for _, r in results if r['status'] == 'success')

        if success_count > 0:
            return {
                'status': 'success',
                'message': f'{success_count}/{len(results)} fix methods successful',
                'details': results
            }
        else:
            return {
                'status': 'failed',
                'error': 'All fix methods failed',
                'details': results
            }

    async def _rebuild_docker_image(
        self,
        project_path: str,
        strategy: Dict
    ) -> Dict:
        """Rebuild Docker image after fixes"""

        logger.info("🐳 Rebuilding Docker image...")

        try:
            dockerfile = os.path.join(project_path, 'Dockerfile')

            if not os.path.exists(dockerfile):
                logger.warning("⚠️ No Dockerfile found, skipping rebuild")
                return {
                    'status': 'success',
                    'skipped': True,
                    'message': 'No Docker rebuild needed (Python project without Dockerfile)'
                }

            # Determine image name from project
            project_name = os.path.basename(project_path).lower()
            image_name = f"{project_name}:latest"

            logger.info(f"   Building: {image_name}")

            # Build image
            result = await self.executor.execute(
                f"docker build -t {image_name} .",
                working_dir=project_path,
                timeout=600
            )

            if result.success:
                logger.info("✅ Docker image rebuilt successfully")
                return {
                    'status': 'success',
                    'message': f'Image rebuilt: {image_name}'
                }
            else:
                return {
                    'status': 'failed',
                    'error': f'Docker build failed: {result.error_message}'
                }

        except Exception as e:
            logger.error(f"❌ Docker rebuild error: {e}")
            return {
                'status': 'failed',
                'error': str(e)
            }

    async def _verify_fix(
        self,
        project_path: str,
        original_vulnerabilities: Dict
    ) -> Dict:
        """Verify fix by running Trivy scan"""

        logger.info("✅ Verifying fix with Trivy re-scan...")

        try:
            # Determine image name
            project_name = os.path.basename(project_path).lower()
            image_name = f"{project_name}:latest"

            # Run Trivy scan
            result = await self.executor.execute(
                f"trivy image --format json --quiet {image_name}",
                timeout=300
            )

            if not result.success:
                return {
                    'success': False,
                    'error': 'Trivy scan failed'
                }

            # Parse results
            try:
                scan_results = json.loads(result.stdout)

                # Count vulnerabilities
                total_vulns = 0
                for result_item in scan_results.get('Results', []):
                    vulns = result_item.get('Vulnerabilities', [])
                    if vulns:
                        total_vulns += len(vulns)

                original_count = len(original_vulnerabilities)

                logger.info(f"   Original vulnerabilities: {original_count}")
                logger.info(f"   Remaining vulnerabilities: {total_vulns}")

                # Check if we reduced vulnerabilities
                if total_vulns < original_count:
                    fixed_count = original_count - total_vulns

                    logger.info(f"✅ Fixed {fixed_count} vulnerabilities")

                    return {
                        'success': True,
                        'fixed_count': fixed_count,
                        'remaining_count': total_vulns,
                        'original_count': original_count
                    }
                elif total_vulns == 0:
                    logger.info("✅ All vulnerabilities fixed!")
                    return {
                        'success': True,
                        'fixed_count': original_count,
                        'remaining_count': 0,
                        'original_count': original_count
                    }
                else:
                    logger.warning("⚠️ No reduction in vulnerabilities")
                    return {
                        'success': False,
                        'error': 'No vulnerabilities were fixed',
                        'remaining_count': total_vulns,
                        'original_count': original_count
                    }

            except json.JSONDecodeError:
                return {
                    'success': False,
                    'error': 'Could not parse Trivy output'
                }

        except Exception as e:
            logger.error(f"❌ Verification error: {e}")
            return {
                'success': False,
                'error': str(e)
            }

    async def _rollback(self, backup_info: List):
        """Rollback changes using backups"""

        logger.warning("🔄 Rolling back changes...")

        for backup in backup_info:
            try:
                success = await self.backup_manager.restore_backup(backup.backup_id)

                if success:
                    logger.info(f"✅ Restored: {backup.source_path}")
                else:
                    logger.error(f"❌ Failed to restore: {backup.source_path}")

            except Exception as e:
                logger.error(f"❌ Rollback error for {backup.source_path}: {e}")

        logger.info("🔄 Rollback complete")
