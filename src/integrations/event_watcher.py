"""
Event-Driven Security Watcher
Monitors all security integrations for new threats/vulnerabilities and triggers auto-remediation.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set
from dataclasses import dataclass, field
from pathlib import Path
import json

logger = logging.getLogger('shadowops')


@dataclass
class SecurityEvent:
    """Represents a security event from any integration"""
    source: str  # 'trivy', 'crowdsec', 'fail2ban', 'aide'
    event_type: str  # 'vulnerability', 'threat', 'ban', 'integrity_violation'
    severity: str  # 'CRITICAL', 'HIGH', 'MEDIUM', 'LOW'
    details: Dict
    timestamp: datetime = field(default_factory=datetime.now)
    event_id: str = ""
    is_persistent: bool = False  # If True, event requires fix (Docker, AIDE). If False, self-resolving (Fail2ban, CrowdSec)

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
            'event_id': self.event_id,
            'is_persistent': self.is_persistent
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

        # Event tracking with persistence
        self.seen_events: Dict[str, float] = {}  # event_signature -> timestamp
        self.event_history: List[SecurityEvent] = []
        self.max_history = 1000
        self.event_cache_file = Path("/home/cmdshadow/shadowops-bot/logs/seen_events.json")
        self.event_cache_duration = 86400  # 24 hours in seconds

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
        if hasattr(config, 'auto_remediation') and config.auto_remediation:
            scan_intervals = config.auto_remediation.get('scan_intervals', {})
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

        # Load seen events from persistent storage
        self._load_seen_events()

        logger.info("✅ Security Event Watcher initialized")

    async def start(self):
        """Start all event watchers"""
        if self.running:
            logger.warning("Event Watcher already running")
            return

        self.running = True
        print("🔍 Starting Security Event Watcher (EFFICIENT Mode)...")
        logger.info("🔍 Starting Security Event Watcher (EFFICIENT Mode)...")

        # Start individual watchers
        self.watcher_tasks = [
            asyncio.create_task(self._watch_trivy()),
            asyncio.create_task(self._watch_crowdsec()),
            asyncio.create_task(self._watch_fail2ban()),
            asyncio.create_task(self._watch_aide()),
        ]

        print("✅ Event-Driven Auto-Remediation aktiv!")
        print(f"📊 Scan Intervals: Trivy={self.intervals['trivy']}s, "
              f"CrowdSec={self.intervals['crowdsec']}s, "
              f"Fail2ban={self.intervals['fail2ban']}s, "
              f"AIDE={self.intervals['aide']}s")

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
        print(f"🔍 Starting Trivy watcher ({self.intervals['trivy']}s intervals)")
        logger.info(f"🔍 Starting Trivy watcher ({self.intervals['trivy']}s intervals)")

        while self.running:
            try:
                print("🐳 Trivy: Scanning for vulnerabilities...")
                self.stats['trivy']['scans'] += 1
                self.stats['trivy']['last_scan'] = datetime.now()

                # Get latest scan results (returns max 1 summary event)
                results = await self._get_trivy_results()

                if not results:
                    print(f"🐳 Trivy: No vulnerabilities found or scan not available")
                    await asyncio.sleep(self.intervals['trivy'])
                    continue

                print(f"🐳 Trivy: Found {len(results)} summary event(s) in latest scan")

                # Process summary event (should only be 1)
                new_events = 0
                for vuln_summary in results:
                    event = SecurityEvent(
                        source='trivy',
                        event_type='docker_vulnerabilities_batch',  # Batch type!
                        severity=vuln_summary.get('Severity', 'UNKNOWN'),
                        details=vuln_summary,
                        is_persistent=True  # Docker vulns require fixing!
                    )

                    if await self._is_new_event(event):
                        print(f"🐳 Trivy: NEW batch event detected - {vuln_summary.get('Stats', {}).get('critical', 0)} CRITICAL, {vuln_summary.get('Stats', {}).get('high', 0)} HIGH")
                        await self._handle_new_event(event)
                        new_events += 1
                    else:
                        print(f"🐳 Trivy: Scan already processed (no changes since last check)")

                if new_events > 0:
                    self.stats['trivy']['events'] += new_events
                    print(f"🐳 Trivy: {new_events} neue Batch-Events erkannt → Auto-Remediation")
                    logger.info(f"🐳 Trivy: {new_events} neue Batch-Events erkannt")
                else:
                    print(f"🐳 Trivy: Keine neuen Events (Scan bereits verarbeitet)")

            except Exception as e:
                print(f"❌ Trivy watcher error: {e}")
                logger.error(f"❌ Trivy watcher error: {e}", exc_info=True)

            # Wait for next scan
            print(f"🐳 Trivy: Warte {self.intervals['trivy']}s bis zum nächsten Scan...")
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

                # BATCH PROCESSING: Create ONE summary event instead of individual events
                if bans and len(bans) > 0:
                    # Create batch event with summary
                    batch_event = SecurityEvent(
                        source='fail2ban',
                        event_type='bans_batch',  # Batch type!
                        severity='MEDIUM',
                        details={
                            'Type': 'Fail2ban Batch',
                            'Title': f"Fail2ban: {len(bans)} IP(s) banned",
                            'Description': f"Detected {len(bans)} banned IP addresses",
                            'Stats': {
                                'total_bans': len(bans),
                            },
                            'Bans': bans[:10]  # Include first 10 for details
                        }
                    )

                    if await self._is_new_event(batch_event):
                        print(f"🚫 Fail2ban: NEW batch event - {len(bans)} bans")
                        await self._handle_new_event(batch_event)
                        self.stats['fail2ban']['events'] += 1
                        logger.info(f"🚫 Fail2ban: 1 Batch-Event mit {len(bans)} Bans erkannt")
                    else:
                        print(f"🚫 Fail2ban: Scan already processed (no new bans)")

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
                        details=change,
                        is_persistent=True  # File integrity issues require investigation!
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
        """Get latest Trivy scan results with enhanced image details"""
        if not self.trivy:
            return []

        try:
            # Get DETAILED scan results (includes per-image analysis)
            results = self.trivy.get_detailed_scan_results()

            if not results:
                return []

            # Create enhanced events with image-level details
            vulnerabilities = []

            total_critical = results.get('total_critical', 0)
            total_high = results.get('total_high', 0)

            if total_critical > 0 or total_high > 0:
                # Get image details
                images = results.get('images', {})
                affected_projects = results.get('affected_projects', [])

                # Categorize images
                external_images = []
                own_images = []
                upgradeable_images = []

                for image_name, details in images.items():
                    img_info = details.get('image_info', {})
                    if img_info.get('is_external'):
                        external_images.append(image_name)
                        if img_info.get('update_available'):
                            upgradeable_images.append({
                                'name': image_name,
                                'current': img_info.get('tag'),
                                'latest': img_info.get('latest_version')
                            })
                    else:
                        own_images.append(image_name)

                # Create enhanced event with detailed information
                summary_event = {
                    'Severity': 'CRITICAL' if total_critical > 0 else 'HIGH',
                    'Type': 'Docker Security Scan',
                    'Title': f"Docker Scan: {total_critical} CRITICAL, {total_high} HIGH vulnerabilities",
                    'Description': f"Found {total_critical} CRITICAL and {total_high} HIGH vulnerabilities in {len(images)} images",
                    'ScanDate': results.get('date', 'Unknown'),
                    'SummaryFile': results.get('json_file', ''),
                    'Stats': {
                        'critical': total_critical,
                        'high': total_high,
                        'medium': results.get('total_medium', 0),
                        'low': results.get('total_low', 0),
                        'images': len(images),
                    },
                    # ENHANCED: Image-level details
                    'ImageDetails': images,
                    'AffectedProjects': affected_projects,
                    'ExternalImages': external_images,
                    'OwnImages': own_images,
                    'UpgradeableImages': upgradeable_images,
                    'SummaryMode': results.get('summary_mode', False)
                }
                vulnerabilities.append(summary_event)

            return vulnerabilities
        except Exception as e:
            logger.error(f"Error getting Trivy results: {e}", exc_info=True)
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
        """
        Check if event is new (not seen before)

        Event Types:
        - Persistent (Docker, AIDE): Cached by signature, re-triggers if content changes
        - Self-Resolving (Fail2ban, CrowdSec): 24h expiration cache
        """
        event_signature = self._generate_event_signature(event)
        current_time = datetime.now().timestamp()

        # PERSISTENT EVENTS: Cache by signature, but use longer duration
        if event.is_persistent:
            # Use signature-based caching with 12h duration (2 scan cycles)
            # This prevents repeated fixes for the same issue
            if event_signature in self.seen_events:
                last_seen = self.seen_events[event_signature]
                time_since_seen = current_time - last_seen

                # If seen within 12 hours, skip (already handled or monitoring)
                if time_since_seen < 43200:  # 12 hours in seconds
                    logger.debug(f"Persistent event {event_signature} already seen {time_since_seen/3600:.1f}h ago, skipping")
                    return False
                else:
                    # More than 12h ago, treat as new (allows retry after monitoring period)
                    logger.info(f"✅ Persistent event {event_signature} expired ({time_since_seen/3600:.1f}h ago), treating as new")

            # Mark as seen with current timestamp
            self.seen_events[event_signature] = current_time
            self._save_seen_events()
            return True

        # SELF-RESOLVING EVENTS: Use 24h cache
        # Check if event was seen before
        if event_signature in self.seen_events:
            last_seen = self.seen_events[event_signature]

            # If event is older than 24h, consider it new again
            if current_time - last_seen < self.event_cache_duration:
                logger.debug(f"Event {event_signature} already seen within 24h")
                return False
            else:
                logger.debug(f"Event {event_signature} expired (>24h), treating as new")

        # Mark as seen with current timestamp
        self.seen_events[event_signature] = current_time

        # Save to persistent storage
        self._save_seen_events()

        return True

    def _generate_event_signature(self, event: SecurityEvent) -> str:
        """Generate unique signature for event deduplication"""
        if event.source == 'trivy':
            # For Docker: Either CVE-based or Batch-based
            details = event.details
            if 'VulnerabilityID' in details:
                # Individual vulnerability
                return f"trivy_{details.get('VulnerabilityID')}_{details.get('PkgName')}_{details.get('InstalledVersion')}"
            else:
                # Batch event - use content hash (critical+high+medium count)
                stats = details.get('Stats', {})
                content_hash = f"{stats.get('critical', 0)}c_{stats.get('high', 0)}h_{stats.get('medium', 0)}m_{stats.get('images', 0)}i"
                return f"trivy_batch_{content_hash}"

        elif event.source == 'crowdsec':
            # For CrowdSec: IP + Scenario
            details = event.details
            return f"crowdsec_{details.get('value')}_{details.get('scenario')}"

        elif event.source == 'fail2ban':
            # For Fail2ban: Either individual ban or batch
            details = event.details
            if 'Stats' in details:
                # Batch event - use content hash
                stats = details.get('Stats', {})
                content_hash = f"{stats.get('total_bans', 0)}bans"
                return f"fail2ban_batch_{content_hash}"
            # Individual ban: IP + Jail
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

        # Send channel alerts (for visibility)
        await self._send_channel_alert(event)

        # Route event to Orchestrator (coordinated remediation)
        # The Orchestrator batches events and creates a coordinated plan
        if hasattr(self.bot, 'orchestrator') and self.bot.orchestrator:
            await self.bot.orchestrator.submit_event(event)
            logger.info(f"   📦 Event submitted to Orchestrator for coordinated remediation")
        elif hasattr(self.bot, 'self_healing') and self.bot.self_healing:
            # Fallback to legacy direct remediation if orchestrator not available
            logger.warning("⚠️ Orchestrator not available, using legacy direct remediation")
            await self.bot.self_healing.handle_event(event)
        else:
            logger.warning("Neither Orchestrator nor Self-healing coordinator available, event logged only")

    async def _send_channel_alert(self, event: SecurityEvent):
        """
        Send alert to appropriate Discord channels

        This ensures visibility of security events in channels while
        the auto-remediation system handles fixes in the background.
        """
        try:
            from utils.embeds import EmbedBuilder, Severity

            details = event.details
            embed = None
            channel_id = None
            mention = None

            # Build embed and determine channel based on source
            if event.source == 'trivy':
                # Docker vulnerability scan
                stats = details.get('Stats', {})
                critical = stats.get('critical', 0)
                high = stats.get('high', 0)
                medium = stats.get('medium', 0)
                images = stats.get('images', 0)

                embed = EmbedBuilder.docker_scan_result(
                    total_images=images,
                    critical=critical,
                    high=high,
                    medium=medium,
                    low=0
                )

                # Send to docker channel
                channel_id = self.bot.config.get_channel_for_alert('docker')
                mention = self.bot.config.mention_role_critical if critical > 0 else None

                # Also send to critical channel if CRITICAL vulns found
                if critical > 0:
                    critical_channel_id = self.bot.config.get_channel_for_alert('critical')
                    if critical_channel_id and critical_channel_id != channel_id:
                        await self.bot.send_alert(critical_channel_id, embed, mention)

            elif event.source == 'fail2ban':
                # Fail2ban batch or individual ban
                stats = details.get('Stats', {})
                if stats:
                    # Batch event
                    total_bans = stats.get('total_bans', 0)
                    bans_list = details.get('Bans', [])

                    # Create summary embed
                    embed = EmbedBuilder.create_alert(
                        title=f"🚫 Fail2ban: {total_bans} IP(s) Banned",
                        description=f"Detected {total_bans} banned IP addresses",
                        severity=Severity.MEDIUM,
                        fields=[
                            {
                                'name': '📊 Summary',
                                'value': f"**Total Bans:** {total_bans}\n**Recent IPs:** {', '.join([b.get('ip', 'N/A') for b in bans_list[:5]])}",
                                'inline': False
                            }
                        ]
                    )
                else:
                    # Individual ban
                    ip = details.get('ip', 'Unknown')
                    jail = details.get('jail', 'Unknown')
                    embed = EmbedBuilder.fail2ban_ban(ip, jail)

                channel_id = self.bot.config.get_channel_for_alert('fail2ban')
                mention = self.bot.config.mention_role_high

            elif event.source == 'crowdsec':
                # CrowdSec threat
                ip = details.get('ip', 'Unknown')
                scenario = details.get('scenario', 'Unknown')
                country = details.get('country', 'Unknown')

                embed = EmbedBuilder.crowdsec_alert(ip, scenario, country)
                channel_id = self.bot.config.get_channel_for_alert('critical')
                mention = self.bot.config.mention_role_critical

            elif event.source == 'aide':
                # AIDE file integrity
                changed = details.get('files_changed', 0)
                added = details.get('files_added', 0)
                removed = details.get('files_removed', 0)

                embed = EmbedBuilder.create_alert(
                    title="🔍 AIDE: File Integrity Violation",
                    description=f"Detected unauthorized file system changes",
                    severity=Severity.HIGH,
                    fields=[
                        {
                            'name': '📊 Changes',
                            'value': f"**Changed:** {changed}\n**Added:** {added}\n**Removed:** {removed}",
                            'inline': False
                        }
                    ]
                )

                channel_id = self.bot.config.get_channel_for_alert('critical')
                mention = self.bot.config.mention_role_critical

            # Send alert to channel
            if embed and channel_id:
                await self.bot.send_alert(channel_id, embed, mention)
                logger.info(f"📢 Channel alert sent: {event.source} → Channel {channel_id}")

        except Exception as e:
            logger.error(f"❌ Failed to send channel alert for {event.source}: {e}", exc_info=True)

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

    def _save_seen_events(self):
        """
        Save seen events to persistent storage

        Automatically cleans up events older than 24h to keep file size manageable.
        """
        try:
            # Create logs directory if it doesn't exist
            self.event_cache_file.parent.mkdir(parents=True, exist_ok=True)

            # Clean up old events before saving
            current_time = datetime.now().timestamp()
            cleaned_events = {
                sig: ts
                for sig, ts in self.seen_events.items()
                if current_time - ts < self.event_cache_duration
            }

            # Save to file
            with open(self.event_cache_file, 'w') as f:
                json.dump(cleaned_events, f, indent=2)

            logger.debug(f"💾 Saved {len(cleaned_events)} seen events to {self.event_cache_file}")

        except Exception as e:
            logger.error(f"❌ Failed to save seen events: {e}")

    def _load_seen_events(self):
        """
        Load seen events from persistent storage

        Automatically filters out events older than 24h.
        """
        try:
            if not self.event_cache_file.exists():
                logger.info("📂 No event cache file found, starting fresh")
                return

            with open(self.event_cache_file, 'r') as f:
                loaded_events = json.load(f)

            # Filter out expired events
            current_time = datetime.now().timestamp()
            valid_events = {
                sig: ts
                for sig, ts in loaded_events.items()
                if current_time - ts < self.event_cache_duration
            }

            self.seen_events = valid_events
            expired_count = len(loaded_events) - len(valid_events)

            logger.info(f"📂 Loaded {len(valid_events)} seen events from cache ({expired_count} expired)")

        except Exception as e:
            logger.error(f"❌ Failed to load seen events: {e}")
            self.seen_events = {}
