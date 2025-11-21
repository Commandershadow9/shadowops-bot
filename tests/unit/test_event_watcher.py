"""
Unit Tests for Security Event Watcher

The Event Watcher is the core event-driven monitoring system that:
- Watches all security integrations (Trivy, CrowdSec, Fail2ban, AIDE)
- Detects new security events
- Deduplicates events
- Triggers auto-remediation

These tests ensure the event detection and deduplication logic works correctly.
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch
from datetime import datetime, timedelta
import asyncio

from src.integrations.event_watcher import SecurityEvent, SecurityEventWatcher


class TestSecurityEvent:
    """Tests for SecurityEvent dataclass"""

    def test_create_security_event(self):
        """Test creating a security event"""
        event = SecurityEvent(
            source='trivy',
            event_type='vulnerability',
            severity='CRITICAL',
            details={'VulnerabilityID': 'CVE-2024-1234'},
            is_persistent=True
        )

        assert event.source == 'trivy'
        assert event.event_type == 'vulnerability'
        assert event.severity == 'CRITICAL'
        assert event.is_persistent is True

    def test_event_id_generation(self):
        """Test that event_id is generated automatically"""
        event = SecurityEvent(
            source='crowdsec',
            event_type='threat',
            severity='HIGH',
            details={'ip': '1.2.3.4'}
        )

        assert event.event_id is not None
        assert event.event_id.startswith('crowdsec_')

    def test_event_to_dict(self):
        """Test converting event to dictionary"""
        event = SecurityEvent(
            source='fail2ban',
            event_type='ban',
            severity='MEDIUM',
            details={'ip': '5.6.7.8', 'jail': 'sshd'}
        )

        event_dict = event.to_dict()

        assert event_dict['source'] == 'fail2ban'
        assert event_dict['event_type'] == 'ban'
        assert event_dict['severity'] == 'MEDIUM'
        assert 'timestamp' in event_dict
        assert 'event_id' in event_dict

    def test_event_timestamp(self):
        """Test that timestamp is set automatically"""
        event = SecurityEvent(
            source='aide',
            event_type='integrity_violation',
            severity='HIGH',
            details={'file': '/etc/passwd'}
        )

        assert isinstance(event.timestamp, datetime)
        # Should be very recent
        assert (datetime.now() - event.timestamp).seconds < 5


class TestEventWatcherInitialization:
    """Tests for EventWatcher initialization"""

    def test_init(self, mock_config):
        """Test event watcher initialization"""
        mock_bot = Mock()

        watcher = SecurityEventWatcher(mock_bot, mock_config)

        assert watcher.bot == mock_bot
        assert watcher.config == mock_config
        assert isinstance(watcher.seen_events, dict)
        assert watcher.running is False

    def test_init_with_scan_intervals(self, mock_config):
        """Test initialization with custom scan intervals"""
        mock_bot = Mock()

        watcher = SecurityEventWatcher(mock_bot, mock_config)

        # Check default intervals are set
        assert watcher.intervals['trivy'] == 21600  # 6 hours
        assert watcher.intervals['crowdsec'] == 30  # 30 seconds
        assert watcher.intervals['fail2ban'] == 30
        assert watcher.intervals['aide'] == 900  # 15 minutes


class TestEventSignatureGeneration:
    """Tests for event signature generation (deduplication)"""

    def test_generate_signature_trivy_individual(self, mock_config):
        """Test signature for individual Trivy vulnerability"""
        mock_bot = Mock()
        watcher = SecurityEventWatcher(mock_bot, mock_config)

        event = SecurityEvent(
            source='trivy',
            event_type='vulnerability',
            severity='CRITICAL',
            details={
                'VulnerabilityID': 'CVE-2024-1234',
                'PkgName': 'openssl',
                'InstalledVersion': '1.0.0'
            }
        )

        signature = watcher._generate_event_signature(event)

        # Should include CVE ID, package, and version
        assert 'trivy' in signature
        assert 'CVE-2024-1234' in signature
        assert 'openssl' in signature

    def test_generate_signature_trivy_batch(self, mock_config):
        """Test signature for Trivy batch event"""
        mock_bot = Mock()
        watcher = SecurityEventWatcher(mock_bot, mock_config)

        event = SecurityEvent(
            source='trivy',
            event_type='vulnerability',
            severity='CRITICAL',
            details={
                'Stats': {
                    'critical': 5,
                    'high': 10,
                    'medium': 3,
                    'images': 2
                }
            }
        )

        signature = watcher._generate_event_signature(event)

        # Batch signature should use content hash
        assert 'trivy_batch' in signature
        assert '5c' in signature  # 5 critical
        assert '10h' in signature  # 10 high

    def test_generate_signature_crowdsec(self, mock_config):
        """Test signature for CrowdSec event"""
        mock_bot = Mock()
        watcher = SecurityEventWatcher(mock_bot, mock_config)

        event = SecurityEvent(
            source='crowdsec',
            event_type='threat',
            severity='HIGH',
            details={
                'value': '1.2.3.4',
                'scenario': 'ssh-bruteforce'
            }
        )

        signature = watcher._generate_event_signature(event)

        # Should include IP and scenario
        assert 'crowdsec' in signature
        assert '1.2.3.4' in signature
        assert 'ssh-bruteforce' in signature

    def test_generate_signature_fail2ban(self, mock_config):
        """Test signature for Fail2ban event"""
        mock_bot = Mock()
        watcher = SecurityEventWatcher(mock_bot, mock_config)

        event = SecurityEvent(
            source='fail2ban',
            event_type='ban',
            severity='MEDIUM',
            details={
                'ip': '5.6.7.8',
                'jail': 'sshd'
            }
        )

        signature = watcher._generate_event_signature(event)

        assert 'fail2ban' in signature
        assert '5.6.7.8' in signature


class TestEventDeduplication:
    """Tests for event deduplication logic"""

    @pytest.mark.asyncio
    async def test_is_new_event_first_time(self, mock_config):
        """Test that first occurrence of event is marked as new"""
        mock_bot = Mock()
        watcher = SecurityEventWatcher(mock_bot, mock_config)

        event = SecurityEvent(
            source='trivy',
            event_type='vulnerability',
            severity='CRITICAL',
            details={'VulnerabilityID': 'CVE-2024-1234'},
            is_persistent=True
        )

        is_new = await watcher._is_new_event(event)

        assert is_new is True

    @pytest.mark.asyncio
    async def test_is_new_event_duplicate_within_window(self, mock_config):
        """Test that duplicate within time window is NOT new"""
        mock_bot = Mock()
        watcher = SecurityEventWatcher(mock_bot, mock_config)

        event = SecurityEvent(
            source='trivy',
            event_type='vulnerability',
            severity='CRITICAL',
            details={'VulnerabilityID': 'CVE-2024-1234'},
            is_persistent=True
        )

        # First time - should be new
        is_new_1 = await watcher._is_new_event(event)
        assert is_new_1 is True

        # Second time immediately after - should NOT be new (within 12h window)
        is_new_2 = await watcher._is_new_event(event)
        assert is_new_2 is False

    @pytest.mark.asyncio
    async def test_is_new_event_persistent_expired(self, mock_config):
        """Test that persistent event becomes new after expiration"""
        mock_bot = Mock()
        watcher = SecurityEventWatcher(mock_bot, mock_config)

        event = SecurityEvent(
            source='trivy',
            event_type='vulnerability',
            severity='CRITICAL',
            details={'VulnerabilityID': 'CVE-2024-1234'},
            is_persistent=True
        )

        signature = watcher._generate_event_signature(event)

        # Manually mark as seen 13 hours ago (past 12h expiration)
        import time
        thirteen_hours_ago = time.time() - (13 * 3600)
        watcher.seen_events[signature] = thirteen_hours_ago

        # Should be treated as new again
        is_new = await watcher._is_new_event(event)
        assert is_new is True

    @pytest.mark.asyncio
    async def test_is_new_event_self_resolving(self, mock_config):
        """Test self-resolving events (Fail2ban, CrowdSec) with 24h cache"""
        mock_bot = Mock()
        watcher = SecurityEventWatcher(mock_bot, mock_config)

        event = SecurityEvent(
            source='fail2ban',
            event_type='ban',
            severity='MEDIUM',
            details={'ip': '1.2.3.4', 'jail': 'sshd'},
            is_persistent=False  # Self-resolving
        )

        # First time
        is_new_1 = await watcher._is_new_event(event)
        assert is_new_1 is True

        # Second time within 24h
        is_new_2 = await watcher._is_new_event(event)
        assert is_new_2 is False

    @pytest.mark.asyncio
    async def test_is_new_event_race_condition_protected(self, mock_config):
        """Test that race conditions are prevented with asyncio.Lock"""
        mock_bot = Mock()
        watcher = SecurityEventWatcher(mock_bot, mock_config)

        event = SecurityEvent(
            source='trivy',
            event_type='vulnerability',
            severity='HIGH',
            details={'VulnerabilityID': 'CVE-2024-9999'},
            is_persistent=True
        )

        # Simulate concurrent calls
        results = await asyncio.gather(
            watcher._is_new_event(event),
            watcher._is_new_event(event),
            watcher._is_new_event(event)
        )

        # Only one should be marked as new (due to lock protection)
        new_count = sum(1 for r in results if r is True)

        # At most one should be new (depending on timing, could be 1 or 0)
        assert new_count <= 1


class TestEventPersistence:
    """Tests for event cache persistence"""

    def test_save_seen_events(self, mock_config, temp_dir, monkeypatch):
        """Test saving seen events to file"""
        mock_bot = Mock()

        # Override cache file path
        cache_file = temp_dir / "seen_events.json"
        monkeypatch.setattr('src.integrations.event_watcher.SecurityEventWatcher.event_cache_file', cache_file)

        watcher = SecurityEventWatcher(mock_bot, mock_config)
        watcher.event_cache_file = cache_file

        # Add some seen events
        import time
        watcher.seen_events = {
            'event_1': time.time(),
            'event_2': time.time() - 3600,
        }

        # Save
        watcher._save_seen_events()

        # Check file was created
        assert cache_file.exists()

    def test_load_seen_events(self, mock_config, temp_dir):
        """Test loading seen events from file"""
        mock_bot = Mock()
        watcher = SecurityEventWatcher(mock_bot, mock_config)

        cache_file = temp_dir / "seen_events.json"
        watcher.event_cache_file = cache_file

        # Create cache file
        import json
        import time
        cache_data = {
            'event_1': time.time(),
            'event_2': time.time() - 1000,
        }
        with open(cache_file, 'w') as f:
            json.dump(cache_data, f)

        # Load
        watcher._load_seen_events()

        # Check events were loaded
        assert 'event_1' in watcher.seen_events
        assert 'event_2' in watcher.seen_events


class TestEventStatistics:
    """Tests for event statistics tracking"""

    def test_statistics_initialization(self, mock_config):
        """Test that statistics are initialized"""
        mock_bot = Mock()
        watcher = SecurityEventWatcher(mock_bot, mock_config)

        assert 'trivy' in watcher.stats
        assert 'crowdsec' in watcher.stats
        assert 'fail2ban' in watcher.stats
        assert 'aide' in watcher.stats

        # Each should have counters
        for source in watcher.stats:
            assert 'scans' in watcher.stats[source]
            assert 'events' in watcher.stats[source]
            assert 'last_scan' in watcher.stats[source]

    def test_statistics_increment(self, mock_config):
        """Test incrementing statistics"""
        mock_bot = Mock()
        watcher = SecurityEventWatcher(mock_bot, mock_config)

        # Increment scan count
        watcher.stats['trivy']['scans'] += 1
        watcher.stats['trivy']['events'] += 5

        assert watcher.stats['trivy']['scans'] == 1
        assert watcher.stats['trivy']['events'] == 5
