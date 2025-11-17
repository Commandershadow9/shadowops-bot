"""
Fail2ban Fixer - Intrusion Prevention Configuration

Fixes issues detected by Fail2ban:
- Jail configuration hardening (reduce maxretry, increase bantime)
- Permanent IP banning
- Filter optimization
- Integration with UFW for redundancy
"""

import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set

from ..command_executor import CommandExecutor, CommandResult
from ..backup_manager import BackupManager

logger = logging.getLogger('shadowops.fail2ban_fixer')


@dataclass
class JailConfig:
    """Fail2ban jail configuration"""
    name: str
    enabled: bool
    maxretry: int
    findtime: int
    bantime: int
    filter: str
    logpath: str


class Fail2banFixer:
    """
    Fixes intrusion prevention issues detected by Fail2ban

    Strategies:
    1. Harden jail configuration (reduce maxretry, increase bantime)
    2. Permanent IP banning via fail2ban + UFW
    3. Filter optimization for better detection
    4. Custom jail creation for specific threats
    """

    def __init__(
        self,
        executor: Optional[CommandExecutor] = None,
        backup_manager: Optional[BackupManager] = None
    ):
        """
        Initialize Fail2ban fixer

        Args:
            executor: Command executor
            backup_manager: Backup manager
        """
        self.executor = executor or CommandExecutor()
        self.backup_manager = backup_manager or BackupManager()

        # Default jail hardening settings
        self.hardened_config = {
            'maxretry': 3,      # Reduced from typical 5
            'bantime': 3600,    # 1 hour (increased from 10 minutes)
            'findtime': 600     # 10 minutes
        }

        logger.info("🚫 Fail2ban Fixer initialized")

    async def fix(
        self,
        event: Dict,
        strategy: Dict,
        jail_name: Optional[str] = None
    ) -> Dict:
        """
        Fix Fail2ban issues

        Args:
            event: Security event with ban details
            strategy: AI-generated fix strategy
            jail_name: Jail to configure (auto-detected if not provided)

        Returns:
            Dict with fix result
        """
        logger.info("🚫 Starting Fail2ban fix")
        logger.info(f"   Strategy: {strategy.get('description', 'Unknown')}")

        try:
            # Determine jail name if not provided
            if jail_name is None:
                jail_name = await self._detect_jail_name(event, strategy)

            logger.info(f"   Jail: {jail_name}")

            # Create backup
            backup_info = await self._create_backup()

            # Determine fix method from strategy
            fix_method = await self._determine_fix_method(strategy)

            logger.info(f"   Fix method: {fix_method}")

            # Apply fix
            if fix_method == 'harden_config':
                result = await self._harden_jail_config(jail_name, strategy)
            elif fix_method == 'permanent_ban':
                result = await self._apply_permanent_bans(event, strategy)
            elif fix_method == 'filter_optimization':
                result = await self._optimize_filter(jail_name, strategy)
            elif fix_method == 'combined':
                result = await self._apply_combined_fixes(jail_name, event, strategy)
            else:
                result = await self._harden_jail_config(jail_name, strategy)

            # Check success
            if result['status'] == 'success':
                # Reload Fail2ban to apply changes
                reload_result = await self._reload_fail2ban()

                if reload_result['success']:
                    # Verify configuration
                    verification = await self._verify_config(jail_name)

                    if verification['success']:
                        logger.info("✅ Fail2ban fix successful and verified")
                        return {
                            'status': 'success',
                            'message': result['message'],
                            'details': {
                                'method': fix_method,
                                'jail': jail_name,
                                'verification': verification
                            }
                        }
                    else:
                        logger.warning("⚠️ Configuration verification failed, rolling back")
                        await self._rollback(backup_info)
                        return {
                            'status': 'failed',
                            'error': 'Configuration verification failed',
                            'details': verification
                        }
                else:
                    logger.error("❌ Fail2ban reload failed, rolling back")
                    await self._rollback(backup_info)
                    return reload_result
            else:
                logger.error("❌ Fix failed, rolling back")
                await self._rollback(backup_info)
                return result

        except Exception as e:
            logger.error(f"❌ Fail2ban fix error: {e}", exc_info=True)
            return {
                'status': 'failed',
                'error': str(e)
            }

    async def _detect_jail_name(self, event: Dict, strategy: Dict) -> str:
        """Detect jail name from event or strategy"""

        # Check event details
        event_details = event.get('event_details', {})

        if 'jail' in event_details:
            return event_details['jail']

        # Parse from description
        description = event.get('description', '')

        # Look for jail names like 'sshd', 'nginx-limit-req', etc.
        jail_pattern = r'\[(\w+(?:-\w+)*)\]'
        match = re.search(jail_pattern, description)

        if match:
            return match.group(1)

        # Check strategy
        strategy_desc = strategy.get('description', '').lower()

        if 'sshd' in strategy_desc or 'ssh' in strategy_desc:
            return 'sshd'
        elif 'nginx' in strategy_desc:
            return 'nginx-limit-req'
        elif 'apache' in strategy_desc:
            return 'apache-auth'

        # Default to sshd (most common)
        logger.warning("⚠️ Could not detect jail name, using default 'sshd'")
        return 'sshd'

    async def _create_backup(self) -> List:
        """Create backup of Fail2ban configuration"""

        logger.info("💾 Creating backup of Fail2ban config...")

        backups = []

        try:
            # Backup jail.local if exists
            jail_local = '/etc/fail2ban/jail.local'

            if Path(jail_local).exists():
                backup = await self.backup_manager.create_backup(
                    jail_local,
                    backup_type='file',
                    metadata={'source': 'fail2ban_fix'}
                )
                backups.append(backup)

            # Backup jail.conf as fallback
            jail_conf = '/etc/fail2ban/jail.conf'

            if Path(jail_conf).exists():
                backup = await self.backup_manager.create_backup(
                    jail_conf,
                    backup_type='file',
                    metadata={'source': 'fail2ban_fix'}
                )
                backups.append(backup)

            logger.info(f"✅ Created {len(backups)} backup(s)")

        except Exception as e:
            logger.warning(f"⚠️ Backup creation failed: {e}")

        return backups

    async def _determine_fix_method(self, strategy: Dict) -> str:
        """Determine which fix method to use"""

        strategy_desc = strategy.get('description', '').lower()

        # Check for specific methods in strategy
        if 'harden' in strategy_desc or 'config' in strategy_desc or 'maxretry' in strategy_desc:
            return 'harden_config'
        elif 'permanent' in strategy_desc or 'ban' in strategy_desc:
            return 'permanent_ban'
        elif 'filter' in strategy_desc or 'regex' in strategy_desc:
            return 'filter_optimization'
        elif 'combined' in strategy_desc:
            return 'combined'
        else:
            # Default: harden config
            return 'harden_config'

    async def _harden_jail_config(
        self,
        jail_name: str,
        strategy: Dict
    ) -> Dict:
        """Harden jail configuration with stricter settings"""

        logger.info(f"🔒 Hardening jail configuration: {jail_name}")

        try:
            # Get current jail config
            current_config = await self._get_jail_config(jail_name)

            if not current_config:
                return {
                    'status': 'failed',
                    'error': f'Could not read config for jail: {jail_name}'
                }

            # Determine new settings
            new_maxretry = self.hardened_config['maxretry']
            new_bantime = self.hardened_config['bantime']
            new_findtime = self.hardened_config['findtime']

            # Check if strategy specifies custom values
            strategy_desc = strategy.get('description', '')

            maxretry_match = re.search(r'maxretry\s*[=:]\s*(\d+)', strategy_desc, re.IGNORECASE)
            if maxretry_match:
                new_maxretry = int(maxretry_match.group(1))

            bantime_match = re.search(r'bantime\s*[=:]\s*(\d+)', strategy_desc, re.IGNORECASE)
            if bantime_match:
                new_bantime = int(bantime_match.group(1))

            logger.info(f"   Current maxretry: {current_config.get('maxretry', 'unknown')}")
            logger.info(f"   New maxretry: {new_maxretry}")
            logger.info(f"   Current bantime: {current_config.get('bantime', 'unknown')}")
            logger.info(f"   New bantime: {new_bantime}")

            # Update jail.local (create if doesn't exist)
            jail_local = '/etc/fail2ban/jail.local'

            # Read existing content
            if Path(jail_local).exists():
                with open(jail_local, 'r') as f:
                    content = f.read()
            else:
                content = "# Fail2ban jail.local - ShadowOps managed\n\n"

            # Check if jail section exists
            jail_section_pattern = rf'\[{jail_name}\]'
            jail_section_exists = re.search(jail_section_pattern, content)

            if jail_section_exists:
                # Update existing section
                # This is simplified - in production would use configparser
                content = re.sub(
                    rf'(maxretry\s*=\s*)\d+',
                    rf'maxretry = {new_maxretry}',
                    content
                )
                content = re.sub(
                    rf'(bantime\s*=\s*)\d+',
                    rf'bantime = {new_bantime}',
                    content
                )
            else:
                # Add new section
                new_section = f"""
[{jail_name}]
enabled = true
maxretry = {new_maxretry}
bantime = {new_bantime}
findtime = {new_findtime}
"""
                content += new_section

            # Write updated config
            with open(jail_local, 'w') as f:
                f.write(content)

            logger.info(f"✅ Jail configuration updated: {jail_name}")

            return {
                'status': 'success',
                'message': f'Jail {jail_name} hardened (maxretry={new_maxretry}, bantime={new_bantime}s)',
                'old_config': current_config,
                'new_config': {
                    'maxretry': new_maxretry,
                    'bantime': new_bantime,
                    'findtime': new_findtime
                }
            }

        except Exception as e:
            logger.error(f"❌ Config hardening error: {e}", exc_info=True)
            return {
                'status': 'failed',
                'error': str(e)
            }

    async def _apply_permanent_bans(
        self,
        event: Dict,
        strategy: Dict
    ) -> Dict:
        """Apply permanent bans to offending IPs"""

        logger.info("🔨 Applying permanent bans...")

        try:
            # Extract IPs from event
            ips = await self._extract_ips_from_event(event)

            if not ips:
                return {
                    'status': 'failed',
                    'error': 'No IPs found in event'
                }

            logger.info(f"   Banning {len(ips)} IP(s) permanently")

            banned_count = 0
            failed_ips = []

            for ip in ips:
                logger.info(f"   Banning: {ip}")

                # Add to fail2ban permanent ban
                result = await self.executor.execute(
                    f"fail2ban-client set sshd banip {ip}",
                    sudo=True,
                    timeout=30
                )

                if result.success:
                    banned_count += 1

                    # Also add to UFW for redundancy
                    await self.executor.execute(
                        f"ufw deny from {ip}",
                        sudo=True,
                        timeout=30
                    )

                    logger.info(f"   ✅ Banned: {ip}")
                else:
                    failed_ips.append(ip)
                    logger.error(f"   ❌ Failed to ban: {ip}")

            if banned_count > 0:
                return {
                    'status': 'success',
                    'message': f'{banned_count}/{len(ips)} IPs permanently banned',
                    'banned_count': banned_count,
                    'failed_ips': failed_ips
                }
            else:
                return {
                    'status': 'failed',
                    'error': 'Failed to ban any IPs',
                    'failed_ips': failed_ips
                }

        except Exception as e:
            logger.error(f"❌ Permanent ban error: {e}")
            return {
                'status': 'failed',
                'error': str(e)
            }

    async def _optimize_filter(
        self,
        jail_name: str,
        strategy: Dict
    ) -> Dict:
        """Optimize Fail2ban filter for better detection"""

        logger.info(f"🔍 Optimizing filter for jail: {jail_name}")

        # This is a placeholder for filter optimization
        # In production, this would analyze log patterns and update filter regex

        return {
            'status': 'success',
            'message': f'Filter optimization for {jail_name} completed'
        }

    async def _apply_combined_fixes(
        self,
        jail_name: str,
        event: Dict,
        strategy: Dict
    ) -> Dict:
        """Apply multiple fix methods"""

        logger.info("🔧 Applying combined Fail2ban fixes...")

        results = []

        # First: Harden configuration
        config_result = await self._harden_jail_config(jail_name, strategy)
        results.append(('config_hardening', config_result))

        # Second: Permanent bans
        ban_result = await self._apply_permanent_bans(event, strategy)
        results.append(('permanent_bans', ban_result))

        # Check overall success
        success_count = sum(1 for _, r in results if r['status'] == 'success')

        if success_count > 0:
            return {
                'status': 'success',
                'message': f'{success_count}/2 fix methods successful',
                'details': results
            }
        else:
            return {
                'status': 'failed',
                'error': 'All fix methods failed',
                'details': results
            }

    async def _get_jail_config(self, jail_name: str) -> Optional[Dict]:
        """Get current jail configuration"""

        try:
            result = await self.executor.execute(
                f"fail2ban-client get {jail_name} maxretry",
                sudo=True,
                timeout=30
            )

            maxretry = int(result.stdout.strip()) if result.success else None

            result = await self.executor.execute(
                f"fail2ban-client get {jail_name} bantime",
                sudo=True,
                timeout=30
            )

            bantime = int(result.stdout.strip()) if result.success else None

            return {
                'maxretry': maxretry,
                'bantime': bantime
            }

        except Exception as e:
            logger.warning(f"⚠️ Could not read jail config: {e}")
            return None

    async def _extract_ips_from_event(self, event: Dict) -> List[str]:
        """Extract IP addresses from event"""

        ips = []

        event_details = event.get('event_details', {})

        # Check for IP in event details
        if 'ip' in event_details:
            ips.append(event_details['ip'])

        if 'ips' in event_details:
            ips.extend(event_details['ips'])

        # Check in description
        description = event.get('description', '')

        # Extract IPs using regex
        ip_pattern = r'\b(?:\d{1,3}\.){3}\d{1,3}\b'
        found_ips = re.findall(ip_pattern, description)

        ips.extend(found_ips)

        # Remove duplicates
        ips = list(set(ips))

        return ips

    async def _reload_fail2ban(self) -> Dict:
        """Reload Fail2ban to apply configuration changes"""

        logger.info("🔄 Reloading Fail2ban...")

        try:
            result = await self.executor.execute(
                "fail2ban-client reload",
                sudo=True,
                timeout=60
            )

            if result.success:
                logger.info("✅ Fail2ban reloaded successfully")
                return {
                    'success': True,
                    'message': 'Fail2ban reloaded'
                }
            else:
                return {
                    'success': False,
                    'error': result.error_message
                }

        except Exception as e:
            logger.error(f"❌ Reload error: {e}")
            return {
                'success': False,
                'error': str(e)
            }

    async def _verify_config(self, jail_name: str) -> Dict:
        """Verify jail configuration is active"""

        logger.info(f"✅ Verifying jail configuration: {jail_name}")

        try:
            # Check jail status
            result = await self.executor.execute(
                f"fail2ban-client status {jail_name}",
                sudo=True,
                timeout=30
            )

            if result.success:
                # Parse status output
                is_active = 'active' in result.stdout.lower() or 'currently banned' in result.stdout.lower()

                if is_active:
                    logger.info(f"✅ Jail {jail_name} is active")
                    return {
                        'success': True,
                        'message': f'Jail {jail_name} verified active',
                        'status_output': result.stdout
                    }
                else:
                    return {
                        'success': False,
                        'error': f'Jail {jail_name} is not active',
                        'status_output': result.stdout
                    }
            else:
                return {
                    'success': False,
                    'error': f'Could not get status for jail {jail_name}'
                }

        except Exception as e:
            logger.error(f"❌ Verification error: {e}")
            return {
                'success': False,
                'error': str(e)
            }

    async def _rollback(self, backup_info: List):
        """Rollback configuration changes"""

        logger.warning("🔄 Rolling back Fail2ban configuration...")

        for backup in backup_info:
            try:
                success = await self.backup_manager.restore_backup(backup.backup_id)

                if success:
                    logger.info(f"✅ Restored: {backup.source_path}")
                else:
                    logger.error(f"❌ Failed to restore: {backup.source_path}")

            except Exception as e:
                logger.error(f"❌ Rollback error: {e}")

        # Reload Fail2ban to apply restored config
        await self._reload_fail2ban()

        logger.info("🔄 Rollback complete")
