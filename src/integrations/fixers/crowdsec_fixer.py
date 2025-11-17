"""
CrowdSec Fixer - Network Threat Mitigation

Fixes threats detected by CrowdSec:
- Permanent IP blocking
- Firewall rule updates (UFW)
- CrowdSec decision management
- Whitelist management (prevent false positives)
"""

import asyncio
import ipaddress
import json
import logging
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Set

from ..command_executor import CommandExecutor, CommandResult
from ..backup_manager import BackupManager

logger = logging.getLogger('shadowops.crowdsec_fixer')


@dataclass
class ThreatInfo:
    """Information about a threat"""
    ip_address: str
    threat_type: str  # 'brute-force', 'scanner', 'exploit', etc.
    scenario: str
    confidence: float
    ban_duration: str
    source: str  # 'crowdsec', 'manual'


class CrowdSecFixer:
    """
    Fixes network threats detected by CrowdSec

    Strategies:
    1. Permanent IP blocking via UFW
    2. Extended CrowdSec decisions
    3. IP range blocking for coordinated attacks
    4. Whitelist management for false positives
    """

    def __init__(
        self,
        executor: Optional[CommandExecutor] = None,
        backup_manager: Optional[BackupManager] = None
    ):
        """
        Initialize CrowdSec fixer

        Args:
            executor: Command executor
            backup_manager: Backup manager
        """
        self.executor = executor or CommandExecutor()
        self.backup_manager = backup_manager or BackupManager()

        # Whitelist of IPs that should NEVER be blocked
        self.whitelist: Set[str] = {
            '127.0.0.1',
            '::1',
            # Add server's own IPs
            # Add office IPs
            # Add known good actors
        }

        logger.info("🛡️ CrowdSec Fixer initialized")

    async def fix(
        self,
        event: Dict,
        strategy: Dict,
        threat_ips: Optional[List[str]] = None
    ) -> Dict:
        """
        Fix CrowdSec threats

        Args:
            event: Security event with threat details
            strategy: AI-generated fix strategy
            threat_ips: List of IPs to block (extracted from event if not provided)

        Returns:
            Dict with fix result
        """
        logger.info("🛡️ Starting CrowdSec fix")
        logger.info(f"   Strategy: {strategy.get('description', 'Unknown')}")

        try:
            # Extract threat IPs if not provided
            if threat_ips is None:
                threat_ips = await self._extract_threat_ips(event)

            if not threat_ips:
                return {
                    'status': 'failed',
                    'error': 'No threat IPs found in event'
                }

            logger.info(f"   Blocking {len(threat_ips)} IP(s)")

            # Validate IPs (check whitelist)
            validated_ips = await self._validate_ips(threat_ips)

            if not validated_ips:
                return {
                    'status': 'failed',
                    'error': 'All IPs are whitelisted or invalid'
                }

            # Create backup of UFW rules
            backup_info = await self._create_backup()

            # Determine fix method from strategy
            fix_method = await self._determine_fix_method(strategy, validated_ips)

            logger.info(f"   Fix method: {fix_method}")

            # Apply fix
            if fix_method == 'ufw_permanent':
                result = await self._block_ips_ufw(validated_ips, strategy)
            elif fix_method == 'crowdsec_extended':
                result = await self._extend_crowdsec_decisions(validated_ips, strategy)
            elif fix_method == 'range_blocking':
                result = await self._block_ip_ranges(validated_ips, strategy)
            elif fix_method == 'combined':
                result = await self._apply_combined_blocking(validated_ips, strategy)
            else:
                result = await self._block_ips_ufw(validated_ips, strategy)

            # Check success
            if result['status'] == 'success':
                # Verify blocking
                verification = await self._verify_blocking(validated_ips)

                if verification['success']:
                    logger.info("✅ CrowdSec fix successful and verified")
                    return {
                        'status': 'success',
                        'message': f'{len(validated_ips)} IP(s) blocked',
                        'details': {
                            'method': fix_method,
                            'blocked_ips': validated_ips,
                            'verification': verification
                        }
                    }
                else:
                    logger.warning("⚠️ Blocking verification failed, rolling back")
                    await self._rollback(backup_info)
                    return {
                        'status': 'failed',
                        'error': 'Blocking verification failed',
                        'details': verification
                    }
            else:
                logger.error("❌ Fix failed, rolling back")
                await self._rollback(backup_info)
                return result

        except Exception as e:
            logger.error(f"❌ CrowdSec fix error: {e}", exc_info=True)
            return {
                'status': 'failed',
                'error': str(e)
            }

    async def _extract_threat_ips(self, event: Dict) -> List[str]:
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

        logger.info(f"   Extracted {len(ips)} IP(s) from event")

        return ips

    async def _validate_ips(self, ips: List[str]) -> List[str]:
        """Validate IPs and check against whitelist"""

        validated = []

        for ip in ips:
            try:
                # Validate IP format
                ip_obj = ipaddress.ip_address(ip)

                # Check whitelist
                if ip in self.whitelist:
                    logger.warning(f"⚠️ IP {ip} is whitelisted, skipping")
                    continue

                # Check if private IP (extra caution)
                if ip_obj.is_private:
                    logger.warning(f"⚠️ IP {ip} is private, requires extra caution")
                    # Still allow blocking private IPs if explicitly requested

                validated.append(ip)

            except ValueError:
                logger.warning(f"⚠️ Invalid IP address: {ip}")
                continue

        logger.info(f"   Validated {len(validated)}/{len(ips)} IP(s)")

        return validated

    async def _create_backup(self) -> List:
        """Create backup of UFW rules"""

        logger.info("💾 Creating backup of firewall rules...")

        backups = []

        try:
            # Save current UFW status
            result = await self.executor.execute(
                "ufw status numbered > /tmp/ufw_backup_before_crowdsec.txt",
                sudo=True,
                timeout=30
            )

            if result.success:
                backup = await self.backup_manager.create_backup(
                    '/tmp/ufw_backup_before_crowdsec.txt',
                    backup_type='file',
                    metadata={'source': 'crowdsec_fix'}
                )
                backups.append(backup)

            logger.info("✅ Backup created")

        except Exception as e:
            logger.warning(f"⚠️ Backup creation failed: {e}")

        return backups

    async def _determine_fix_method(
        self,
        strategy: Dict,
        ips: List[str]
    ) -> str:
        """Determine which fix method to use"""

        strategy_desc = strategy.get('description', '').lower()

        # Check for specific methods in strategy
        if 'ufw' in strategy_desc or 'firewall' in strategy_desc:
            return 'ufw_permanent'
        elif 'extended' in strategy_desc or 'duration' in strategy_desc:
            return 'crowdsec_extended'
        elif 'range' in strategy_desc or 'subnet' in strategy_desc:
            return 'range_blocking'
        elif 'combined' in strategy_desc or 'both' in strategy_desc:
            return 'combined'
        else:
            # Default: permanent UFW blocking
            return 'ufw_permanent'

    async def _block_ips_ufw(
        self,
        ips: List[str],
        strategy: Dict
    ) -> Dict:
        """Block IPs permanently using UFW"""

        logger.info("🔥 Blocking IPs with UFW...")

        blocked_count = 0
        failed_ips = []

        for ip in ips:
            logger.info(f"   Blocking: {ip}")

            # Add UFW deny rule
            result = await self.executor.execute(
                f"ufw deny from {ip}",
                sudo=True,
                timeout=30
            )

            if result.success:
                blocked_count += 1
                logger.info(f"   ✅ Blocked: {ip}")
            else:
                failed_ips.append(ip)
                logger.error(f"   ❌ Failed to block: {ip}")

        if blocked_count > 0:
            # Reload UFW to apply changes
            reload_result = await self.executor.execute(
                "ufw reload",
                sudo=True,
                timeout=30
            )

            if not reload_result.success:
                logger.warning("⚠️ UFW reload failed, rules may not be active")

            return {
                'status': 'success',
                'message': f'{blocked_count}/{len(ips)} IPs blocked via UFW',
                'blocked_count': blocked_count,
                'failed_ips': failed_ips
            }
        else:
            return {
                'status': 'failed',
                'error': 'Failed to block any IPs',
                'failed_ips': failed_ips
            }

    async def _extend_crowdsec_decisions(
        self,
        ips: List[str],
        strategy: Dict
    ) -> Dict:
        """Extend CrowdSec decisions to longer duration"""

        logger.info("⏰ Extending CrowdSec decisions...")

        # Extract duration from strategy (default 24h)
        duration = '24h'

        strategy_desc = strategy.get('description', '')
        duration_match = re.search(r'(\d+)\s*(h|hour|hours|d|day|days)', strategy_desc, re.IGNORECASE)

        if duration_match:
            value = duration_match.group(1)
            unit = duration_match.group(2).lower()

            if unit.startswith('h'):
                duration = f'{value}h'
            elif unit.startswith('d'):
                duration = f'{value}h'  # Convert days to hours
                hours = int(value) * 24
                duration = f'{hours}h'

        logger.info(f"   Duration: {duration}")

        blocked_count = 0
        failed_ips = []

        for ip in ips:
            logger.info(f"   Adding decision for: {ip}")

            # Add CrowdSec decision
            result = await self.executor.execute(
                f"cscli decisions add --ip {ip} --duration {duration} --type ban --reason 'Security fix by ShadowOps'",
                sudo=True,
                timeout=30
            )

            if result.success:
                blocked_count += 1
                logger.info(f"   ✅ Decision added: {ip}")
            else:
                failed_ips.append(ip)
                logger.error(f"   ❌ Failed to add decision: {ip}")

        if blocked_count > 0:
            return {
                'status': 'success',
                'message': f'{blocked_count}/{len(ips)} decisions added ({duration})',
                'blocked_count': blocked_count,
                'duration': duration,
                'failed_ips': failed_ips
            }
        else:
            return {
                'status': 'failed',
                'error': 'Failed to add any decisions',
                'failed_ips': failed_ips
            }

    async def _block_ip_ranges(
        self,
        ips: List[str],
        strategy: Dict
    ) -> Dict:
        """Block entire IP ranges for coordinated attacks"""

        logger.info("🌐 Blocking IP ranges...")

        # Group IPs by /24 subnet
        subnets = {}

        for ip in ips:
            try:
                ip_obj = ipaddress.ip_address(ip)
                # Get /24 network
                network = ipaddress.ip_network(f"{ip}/24", strict=False)
                subnet_str = str(network)

                if subnet_str not in subnets:
                    subnets[subnet_str] = []

                subnets[subnet_str].append(ip)

            except ValueError:
                logger.warning(f"⚠️ Could not determine subnet for: {ip}")

        # Block subnets that have multiple IPs (coordinated attack indicator)
        blocked_subnets = []

        for subnet, subnet_ips in subnets.items():
            if len(subnet_ips) >= 2:  # At least 2 IPs from same subnet
                logger.info(f"   Blocking subnet: {subnet} ({len(subnet_ips)} IPs)")

                result = await self.executor.execute(
                    f"ufw deny from {subnet}",
                    sudo=True,
                    timeout=30
                )

                if result.success:
                    blocked_subnets.append(subnet)
                    logger.info(f"   ✅ Blocked subnet: {subnet}")
                else:
                    logger.error(f"   ❌ Failed to block subnet: {subnet}")

        if blocked_subnets:
            # Reload UFW
            await self.executor.execute("ufw reload", sudo=True, timeout=30)

            return {
                'status': 'success',
                'message': f'{len(blocked_subnets)} subnet(s) blocked',
                'blocked_subnets': blocked_subnets
            }
        else:
            # Fall back to individual IP blocking
            return await self._block_ips_ufw(ips, strategy)

    async def _apply_combined_blocking(
        self,
        ips: List[str],
        strategy: Dict
    ) -> Dict:
        """Apply both UFW and CrowdSec blocking"""

        logger.info("🔧 Applying combined blocking...")

        results = []

        # First: UFW permanent blocking
        ufw_result = await self._block_ips_ufw(ips, strategy)
        results.append(('ufw', ufw_result))

        # Second: Extended CrowdSec decisions
        crowdsec_result = await self._extend_crowdsec_decisions(ips, strategy)
        results.append(('crowdsec', crowdsec_result))

        # Check overall success
        success_count = sum(1 for _, r in results if r['status'] == 'success')

        if success_count > 0:
            return {
                'status': 'success',
                'message': f'{success_count}/2 blocking methods successful',
                'details': results
            }
        else:
            return {
                'status': 'failed',
                'error': 'All blocking methods failed',
                'details': results
            }

    async def _verify_blocking(self, ips: List[str]) -> Dict:
        """Verify that IPs are actually blocked"""

        logger.info("✅ Verifying IP blocking...")

        blocked_count = 0
        not_blocked = []

        for ip in ips:
            # Check UFW status
            result = await self.executor.execute(
                f"ufw status | grep -i '{ip}'",
                sudo=True,
                timeout=30
            )

            if result.success and 'DENY' in result.stdout:
                blocked_count += 1
                logger.debug(f"   ✅ Verified blocked: {ip}")
            else:
                not_blocked.append(ip)
                logger.warning(f"   ⚠️ Not verified: {ip}")

        if blocked_count == len(ips):
            return {
                'success': True,
                'blocked_count': blocked_count,
                'message': f'All {blocked_count} IPs verified blocked'
            }
        elif blocked_count > 0:
            return {
                'success': True,
                'blocked_count': blocked_count,
                'not_blocked': not_blocked,
                'message': f'{blocked_count}/{len(ips)} IPs verified blocked'
            }
        else:
            return {
                'success': False,
                'error': 'No IPs verified as blocked',
                'not_blocked': not_blocked
            }

    async def _rollback(self, backup_info: List):
        """Rollback firewall changes"""

        logger.warning("🔄 Rolling back firewall changes...")

        for backup in backup_info:
            try:
                success = await self.backup_manager.restore_backup(backup.backup_id)

                if success:
                    logger.info(f"✅ Restored: {backup.source_path}")

                    # Reload UFW to apply restored rules
                    await self.executor.execute("ufw reload", sudo=True, timeout=30)
                else:
                    logger.error(f"❌ Failed to restore: {backup.source_path}")

            except Exception as e:
                logger.error(f"❌ Rollback error: {e}")

        logger.info("🔄 Rollback complete")

    async def add_to_whitelist(self, ip: str) -> bool:
        """Add IP to whitelist (prevent blocking)"""

        try:
            ip_obj = ipaddress.ip_address(ip)
            self.whitelist.add(ip)
            logger.info(f"✅ Added to whitelist: {ip}")
            return True
        except ValueError:
            logger.error(f"❌ Invalid IP address: {ip}")
            return False

    async def remove_from_whitelist(self, ip: str) -> bool:
        """Remove IP from whitelist"""

        if ip in self.whitelist:
            self.whitelist.remove(ip)
            logger.info(f"✅ Removed from whitelist: {ip}")
            return True
        else:
            logger.warning(f"⚠️ IP not in whitelist: {ip}")
            return False

    def get_whitelist(self) -> Set[str]:
        """Get current whitelist"""
        return self.whitelist.copy()
