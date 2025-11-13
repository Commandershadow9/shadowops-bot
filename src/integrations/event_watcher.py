"""
Event-Driven Security Watcher
Monitors all security integrations for new threats/vulnerabilities and triggers auto-remediation.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set
from dataclasses import dataclass, field
import json

logger = logging.getLogger(__name__)


@dataclass
class SecurityEvent:
    """Represents a security event from any integration"""
    source: str  # 'trivy', 'crowdsec', 'fail2ban', 'aide'
    event_type: str  # 'vulnerability', 'threat', 'ban', 'integrity_violation'
    severity: str  # 'CRITICAL', 'HIGH', 'MEDIUM', 'LOW'
    details: Dict
    timestamp: datetime = field(default_factory=datetime.now)
    event_id: str = ""

    def __post_init__(self):
        if not self.event_id:
            self.event_id = f"{self.source}_{self.event_type}_{self.timestamp.timestamp()}"

    def to_dict(self) -> Dict:
        return {
            'source': self.source,
            'event_type': self.event_type,
            'severity': self.severity,
            'details': self.details,
            'timestamp': self.timestamp.isoformat(),
            'event_id': self.event_id
        }


class SecurityEventWatcher:
    """
    Event-Driven Security Monitoring System

    Watches all security integrations and triggers auto-remediation on new events.
    Uses efficient polling intervals based on event type urgency.
    """

    def __init__(self, bot, config: Dict):
        self.bot = bot
        self.config = config

        # Event tracking
        self.seen_events: Set[str] = set()
        self.event_history: List[SecurityEvent] = []
        self.max_history = 1000

        # Integration references
        self.trivy = None
        self.crowdsec = None
        self.fail2ban = None
        self.aide = None

        # Watcher tasks
        self.watcher_tasks: List[asyncio.Task] = []
        self.running = False

        # Scan intervals (in seconds) - EFFICIENT mode
        self.intervals = {
            'trivy': 21600,      # 6 hours (Docker scans are slow)
            'crowdsec': 30,      # 30 seconds (active threats)
            'fail2ban': 30,      # 30 seconds (active bans)
            'aide': 900,         # 15 minutes (file integrity)
        }

        # Override from config if present
        if 'auto_remediation' in config:
            scan_intervals = config['auto_remediation'].get('scan_intervals', {})
            self.intervals.update(scan_intervals)

        # Statistics
        self.stats = {
            'trivy': {'scans': 0, 'events': 0, 'last_scan': None},
            'crowdsec': {'scans': 0, 'events': 0, 'last_scan': None},
            'fail2ban': {'scans': 0, 'events': 0, 'last_scan': None},
            'aide': {'scans': 0, 'events': 0, 'last_scan': None},
        }

    async def initialize(self, trivy, crowdsec, fail2ban, aide):
        """Initialize with integration instances"""
        self.trivy = trivy
        self.crowdsec = crowdsec
        self.fail2ban = fail2ban
        self.aide = aide

        logger.info("✅ Security Event Watcher initialized")

    async def start(self):
        """Start all event watchers"""
        if self.running:
            logger.warning("Event Watcher already running")
            return

        self.running = True
        logger.info("🔍 Starting Security Event Watcher (EFFICIENT Mode)...")

        # Start individual watchers
        self.watcher_tasks = [
            asyncio.create_task(self._watch_trivy()),
            asyncio.create_task(self._watch_crowdsec()),
            asyncio.create_task(self._watch_fail2ban()),
            asyncio.create_task(self._watch_aide()),
        ]

        logger.info("✅ Event-Driven Auto-Remediation aktiv!")
        logger.info(f"📊 Scan Intervals: Trivy={self.intervals['trivy']}s, "
                   f"CrowdSec={self.intervals['crowdsec']}s, "
                   f"Fail2ban={self.intervals['fail2ban']}s, "
                   f"AIDE={self.intervals['aide']}s")

    async def stop(self):
        """Stop all event watchers"""
        logger.info("🛑 Stopping Security Event Watcher...")
        self.running = False

        for task in self.watcher_tasks:
            task.cancel()

        await asyncio.gather(*self.watcher_tasks, return_exceptions=True)
        self.watcher_tasks.clear()

        logger.info("✅ Event Watcher stopped")

    async def _watch_trivy(self):
        """Watch for Docker vulnerabilities"""
        logger.info(f"🔍 Starting Trivy watcher ({self.intervals['trivy']}s intervals)")

        while self.running:
            try:
                self.stats['trivy']['scans'] += 1
                self.stats['trivy']['last_scan'] = datetime.now()

                # Get latest scan results
                results = await self._get_trivy_results()

                # Process new vulnerabilities
                new_events = 0
                for vuln in results:
                    event = SecurityEvent(
                        source='trivy',
                        event_type='vulnerability',
                        severity=vuln.get('Severity', 'UNKNOWN'),
                        details=vuln
                    )

                    if await self._is_new_event(event):
                        await self._handle_new_event(event)
                        new_events += 1

                if new_events > 0:
                    self.stats['trivy']['events'] += new_events
                    logger.info(f"🐳 Trivy: {new_events} neue Vulnerabilities erkannt")

            except Exception as e:
                logger.error(f"❌ Trivy watcher error: {e}", exc_info=True)

            # Wait for next scan
            await asyncio.sleep(self.intervals['trivy'])

    async def _watch_crowdsec(self):
        """Watch for CrowdSec threats"""
        logger.info(f"🔍 Starting CrowdSec watcher ({self.intervals['crowdsec']}s intervals)")

        while self.running:
            try:
                self.stats['crowdsec']['scans'] += 1
                self.stats['crowdsec']['last_scan'] = datetime.now()

                # Get active decisions
                decisions = await self._get_crowdsec_decisions()

                new_events = 0
                for decision in decisions:
                    event = SecurityEvent(
                        source='crowdsec',
                        event_type='threat',
                        severity='HIGH',  # All CrowdSec decisions are high priority
                        details=decision
                    )

                    if await self._is_new_event(event):
                        await self._handle_new_event(event)
                        new_events += 1

                if new_events > 0:
                    self.stats['crowdsec']['events'] += new_events
                    logger.info(f"🛡️ CrowdSec: {new_events} neue Threats erkannt")

            except Exception as e:
                logger.error(f"❌ CrowdSec watcher error: {e}", exc_info=True)

            await asyncio.sleep(self.intervals['crowdsec'])

    async def _watch_fail2ban(self):
        """Watch for Fail2ban bans"""
        logger.info(f"🔍 Starting Fail2ban watcher ({self.intervals['fail2ban']}s intervals)")

        while self.running:
            try:
                self.stats['fail2ban']['scans'] += 1
                self.stats['fail2ban']['last_scan'] = datetime.now()

                # Get new bans
                bans = await self._get_fail2ban_bans()

                new_events = 0
                for ban in bans:
                    event = SecurityEvent(
                        source='fail2ban',
                        event_type='ban',
                        severity='MEDIUM',
                        details=ban
                    )

                    if await self._is_new_event(event):
                        await self._handle_new_event(event)
                        new_events += 1

                if new_events > 0:
                    self.stats['fail2ban']['events'] += new_events
                    logger.info(f"🚫 Fail2ban: {new_events} neue Bans erkannt")

            except Exception as e:
                logger.error(f"❌ Fail2ban watcher error: {e}", exc_info=True)

            await asyncio.sleep(self.intervals['fail2ban'])

    async def _watch_aide(self):
        """Watch for file integrity violations"""
        logger.info(f"🔍 Starting AIDE watcher ({self.intervals['aide']}s intervals)")

        while self.running:
            try:
                self.stats['aide']['scans'] += 1
                self.stats['aide']['last_scan'] = datetime.now()

                # Get file changes
                changes = await self._get_aide_changes()

                new_events = 0
                for change in changes:
                    # Determine severity based on file path
                    severity = 'CRITICAL' if self._is_critical_file(change.get('file', '')) else 'HIGH'

                    event = SecurityEvent(
                        source='aide',
                        event_type='integrity_violation',
                        severity=severity,
                        details=change
                    )

                    if await self._is_new_event(event):
                        await self._handle_new_event(event)
                        new_events += 1

                if new_events > 0:
                    self.stats['aide']['events'] += new_events
                    logger.info(f"📁 AIDE: {new_events} File Integrity Violations erkannt")

            except Exception as e:
                logger.error(f"❌ AIDE watcher error: {e}", exc_info=True)

            await asyncio.sleep(self.intervals['aide'])

    async def _get_trivy_results(self) -> List[Dict]:
        """Get latest Trivy scan results"""
        if not self.trivy:
            return []

        try:
            # Get scan results from last scan
            results = self.trivy.get_scan_results()

            if not results:
                return []

            # Extract vulnerabilities
            vulnerabilities = []
            for result in results:
                if 'Vulnerabilities' in result:
                    vulnerabilities.extend(result['Vulnerabilities'])

            return vulnerabilities
        except Exception as e:
            logger.error(f"Error getting Trivy results: {e}")
            return []

    async def _get_crowdsec_decisions(self) -> List[Dict]:
        """Get active CrowdSec decisions"""
        if not self.crowdsec:
            return []

        try:
            decisions = self.crowdsec.get_decisions()
            return decisions if decisions else []
        except Exception as e:
            logger.error(f"Error getting CrowdSec decisions: {e}")
            return []

    async def _get_fail2ban_bans(self) -> List[Dict]:
        """Get new Fail2ban bans"""
        if not self.fail2ban:
            return []

        try:
            bans = self.fail2ban.get_new_bans()
            return bans if bans else []
        except Exception as e:
            logger.error(f"Error getting Fail2ban bans: {e}")
            return []

    async def _get_aide_changes(self) -> List[Dict]:
        """Get AIDE file changes"""
        if not self.aide:
            return []

        try:
            changes = self.aide.get_changes()
            return changes if changes else []
        except Exception as e:
            logger.error(f"Error getting AIDE changes: {e}")
            return []

    async def _is_new_event(self, event: SecurityEvent) -> bool:
        """Check if event is new (not seen before)"""
        event_signature = self._generate_event_signature(event)

        if event_signature in self.seen_events:
            return False

        self.seen_events.add(event_signature)
        return True

    def _generate_event_signature(self, event: SecurityEvent) -> str:
        """Generate unique signature for event deduplication"""
        if event.source == 'trivy':
            # For Docker: CVE ID + Package + Version
            details = event.details
            return f"trivy_{details.get('VulnerabilityID')}_{details.get('PkgName')}_{details.get('InstalledVersion')}"

        elif event.source == 'crowdsec':
            # For CrowdSec: IP + Scenario
            details = event.details
            return f"crowdsec_{details.get('value')}_{details.get('scenario')}"

        elif event.source == 'fail2ban':
            # For Fail2ban: IP + Jail
            details = event.details
            return f"fail2ban_{details.get('ip')}_{details.get('jail')}"

        elif event.source == 'aide':
            # For AIDE: File path + Change type
            details = event.details
            return f"aide_{details.get('file')}_{details.get('change_type')}"

        # Fallback
        return event.event_id

    async def _handle_new_event(self, event: SecurityEvent):
        """Handle newly detected security event"""
        # Add to history
        self.event_history.append(event)
        if len(self.event_history) > self.max_history:
            self.event_history.pop(0)

        logger.info(f"🚨 New {event.severity} event from {event.source}: {event.event_type}")

        # Trigger auto-remediation via self-healing coordinator
        if hasattr(self.bot, 'self_healing') and self.bot.self_healing:
            await self.bot.self_healing.handle_event(event)
        else:
            logger.warning("Self-healing coordinator not available, event logged only")

    def _is_critical_file(self, filepath: str) -> bool:
        """Determine if file is critical system file"""
        critical_paths = [
            '/etc/passwd',
            '/etc/shadow',
            '/etc/ssh/',
            '/etc/sudoers',
            '/boot/',
            '/root/.ssh/',
        ]

        return any(filepath.startswith(path) for path in critical_paths)

    def get_statistics(self) -> Dict:
        """Get event watcher statistics"""
        total_scans = sum(s['scans'] for s in self.stats.values())
        total_events = sum(s['events'] for s in self.stats.values())

        return {
            'running': self.running,
            'total_scans': total_scans,
            'total_events': total_events,
            'events_in_history': len(self.event_history),
            'seen_events': len(self.seen_events),
            'by_source': self.stats,
            'intervals': self.intervals,
        }

    def get_recent_events(self, limit: int = 50) -> List[Dict]:
        """Get recent security events"""
        recent = self.event_history[-limit:] if len(self.event_history) > limit else self.event_history
        return [event.to_dict() for event in reversed(recent)]

    async def force_scan_all(self):
        """Force immediate scan of all sources (for testing)"""
        logger.info("🔄 Forcing immediate scan of all sources...")

        tasks = [
            self._get_trivy_results(),
            self._get_crowdsec_decisions(),
            self._get_fail2ban_bans(),
            self._get_aide_changes(),
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        total_events = sum(len(r) if isinstance(r, list) else 0 for r in results)
        logger.info(f"✅ Force scan completed: {total_events} total items found")

        return {
            'trivy': len(results[0]) if isinstance(results[0], list) else 0,
            'crowdsec': len(results[1]) if isinstance(results[1], list) else 0,
            'fail2ban': len(results[2]) if isinstance(results[2], list) else 0,
            'aide': len(results[3]) if isinstance(results[3], list) else 0,
        }
