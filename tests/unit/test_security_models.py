"""Tests fuer Security Engine v6 Datenmodelle"""
import pytest
from datetime import datetime, timezone

from src.integrations.security_engine.models import (
    Severity,
    PhaseType,
    EngineMode,
    SecurityEvent,
    BanEvent,
    ThreatEvent,
    VulnEvent,
    IntegrityEvent,
    FixResult,
)


# --- TestSeverity ---

class TestSeverity:
    """Tests fuer Severity Enum und Priority-Ordering."""

    def test_all_levels_exist(self):
        assert Severity.CRITICAL.value == 'CRITICAL'
        assert Severity.HIGH.value == 'HIGH'
        assert Severity.MEDIUM.value == 'MEDIUM'
        assert Severity.LOW.value == 'LOW'

    def test_priority_ordering(self):
        """CRITICAL hat hoechste Prioritaet (0), LOW niedrigste (3)."""
        assert Severity.CRITICAL.priority < Severity.HIGH.priority
        assert Severity.HIGH.priority < Severity.MEDIUM.priority
        assert Severity.MEDIUM.priority < Severity.LOW.priority

    def test_priority_values(self):
        assert Severity.CRITICAL.priority == 0
        assert Severity.HIGH.priority == 1
        assert Severity.MEDIUM.priority == 2
        assert Severity.LOW.priority == 3

    def test_sortable_by_priority(self):
        """Severities lassen sich nach Prioritaet sortieren."""
        severities = [Severity.LOW, Severity.CRITICAL, Severity.MEDIUM, Severity.HIGH]
        sorted_sevs = sorted(severities, key=lambda s: s.priority)
        assert sorted_sevs == [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW]


# --- TestPhaseType ---

class TestPhaseType:
    """Tests fuer PhaseType Enum und is_read_only Property."""

    def test_all_phases_exist(self):
        assert PhaseType.RECON.value == 'recon'
        assert PhaseType.CONTAIN.value == 'contain'
        assert PhaseType.FIX.value == 'fix'
        assert PhaseType.VERIFY.value == 'verify'
        assert PhaseType.MONITOR.value == 'monitor'

    def test_read_only_phases(self):
        """RECON, VERIFY und MONITOR sind read-only."""
        assert PhaseType.RECON.is_read_only is True
        assert PhaseType.VERIFY.is_read_only is True
        assert PhaseType.MONITOR.is_read_only is True

    def test_write_phases(self):
        """CONTAIN und FIX sind NICHT read-only."""
        assert PhaseType.CONTAIN.is_read_only is False
        assert PhaseType.FIX.is_read_only is False

    def test_phase_count(self):
        """Genau 5 Phasen definiert."""
        assert len(PhaseType) == 5


# --- TestEngineMode ---

class TestEngineMode:
    """Tests fuer EngineMode Enum."""

    def test_all_modes_exist(self):
        assert EngineMode.REACTIVE.value == 'reactive'
        assert EngineMode.PROACTIVE.value == 'proactive'
        assert EngineMode.DEEP_SCAN.value == 'deep_scan'

    def test_mode_count(self):
        assert len(EngineMode) == 3


# --- TestSecurityEvent ---

class TestSecurityEvent:
    """Tests fuer SecurityEvent ABC und konkrete Subklassen."""

    def test_cannot_instantiate_abc(self):
        """SecurityEvent ist abstrakt — direkte Instanziierung schlaegt fehl."""
        with pytest.raises(TypeError):
            SecurityEvent(source='test', severity=Severity.LOW, details={})

    def test_ban_event_creation(self):
        event = BanEvent(
            source='fail2ban',
            severity=Severity.MEDIUM,
            details={'ip': '192.168.1.100', 'jail': 'sshd'},
        )
        assert event.source == 'fail2ban'
        assert event.severity == Severity.MEDIUM
        assert event.details['ip'] == '192.168.1.100'
        assert event.event_type == 'ban'

    def test_threat_event_creation(self):
        event = ThreatEvent(
            source='crowdsec',
            severity=Severity.HIGH,
            details={'scenario': 'http-probing'},
        )
        assert event.event_type == 'threat'
        assert event.severity == Severity.HIGH

    def test_vuln_event_is_persistent(self):
        """VulnEvent ist persistent (bleibt bis gefixt)."""
        event = VulnEvent(
            source='trivy',
            severity=Severity.CRITICAL,
            details={'cve': 'CVE-2026-1234', 'package': 'openssl'},
        )
        assert event.is_persistent is True
        assert event.event_type == 'vulnerability'

    def test_integrity_event_is_persistent(self):
        """IntegrityEvent ist persistent."""
        event = IntegrityEvent(
            source='aide',
            severity=Severity.HIGH,
            details={'path': '/etc/passwd', 'change': 'modified'},
        )
        assert event.is_persistent is True
        assert event.event_type == 'integrity_violation'

    def test_ban_event_not_persistent(self):
        event = BanEvent(source='fail2ban', severity=Severity.LOW, details={})
        assert event.is_persistent is False

    def test_threat_event_not_persistent(self):
        event = ThreatEvent(source='crowdsec', severity=Severity.LOW, details={})
        assert event.is_persistent is False

    def test_event_signature(self):
        """Signature ist source_event_type."""
        event = BanEvent(source='fail2ban', severity=Severity.LOW, details={})
        assert event.signature == 'fail2ban_ban'

        event2 = VulnEvent(source='trivy', severity=Severity.HIGH, details={})
        assert event2.signature == 'trivy_vulnerability'

    def test_event_timestamp_default(self):
        """Timestamp wird automatisch auf UTC gesetzt."""
        before = datetime.now(timezone.utc)
        event = BanEvent(source='test', severity=Severity.LOW, details={})
        after = datetime.now(timezone.utc)
        assert before <= event.timestamp <= after
        assert event.timestamp.tzinfo is not None

    def test_event_id_default_empty(self):
        event = BanEvent(source='test', severity=Severity.LOW, details={})
        assert event.event_id == ''

    def test_event_id_custom(self):
        event = BanEvent(
            source='test',
            severity=Severity.LOW,
            details={},
            event_id='evt-123',
        )
        assert event.event_id == 'evt-123'

    def test_to_dict(self):
        """to_dict gibt vollstaendiges Dictionary zurueck."""
        event = VulnEvent(
            source='trivy',
            severity=Severity.CRITICAL,
            details={'cve': 'CVE-2026-9999'},
            event_id='v-001',
        )
        d = event.to_dict()

        assert d['source'] == 'trivy'
        assert d['event_type'] == 'vulnerability'
        assert d['severity'] == 'CRITICAL'
        assert d['details'] == {'cve': 'CVE-2026-9999'}
        assert d['event_id'] == 'v-001'
        assert d['signature'] == 'trivy_vulnerability'
        assert d['is_persistent'] is True
        # Timestamp ist ISO-Format String
        assert isinstance(d['timestamp'], str)
        datetime.fromisoformat(d['timestamp'])  # Validierung — wirft bei Fehler

    def test_to_dict_contains_all_keys(self):
        event = BanEvent(source='test', severity=Severity.LOW, details={})
        d = event.to_dict()
        expected_keys = {
            'source', 'event_type', 'severity', 'details',
            'event_id', 'timestamp', 'signature', 'is_persistent',
        }
        assert set(d.keys()) == expected_keys


# --- TestFixResult ---

class TestFixResult:
    """Tests fuer FixResult Datenklasse und Factory-Methoden."""

    def test_success_factory(self):
        result = FixResult.success('Paket aktualisiert', PhaseType.FIX)
        assert result.status == 'success'
        assert result.message == 'Paket aktualisiert'
        assert result.phase_type == PhaseType.FIX
        assert result.is_success is True

    def test_failed_factory(self):
        result = FixResult.failed('Permission denied', PhaseType.FIX)
        assert result.status == 'failed'
        assert result.error == 'Permission denied'
        assert result.phase_type == PhaseType.FIX
        assert result.is_success is False

    def test_no_op_factory(self):
        result = FixResult.no_op('Bereits gefixt')
        assert result.status == 'no_op'
        assert result.message == 'Bereits gefixt'
        assert result.is_success is True

    def test_skipped_factory(self):
        result = FixResult.skipped('Duplikat erkannt')
        assert result.status == 'skipped_duplicate'
        assert result.message == 'Duplikat erkannt'
        assert result.is_success is True

    def test_is_success_property(self):
        """success, no_op und skipped_duplicate gelten als Erfolg."""
        assert FixResult(status='success').is_success is True
        assert FixResult(status='no_op').is_success is True
        assert FixResult(status='skipped_duplicate').is_success is True
        assert FixResult(status='failed').is_success is False
        assert FixResult(status='partial').is_success is False

    def test_default_values(self):
        result = FixResult(status='success')
        assert result.message == ''
        assert result.phase_type is None
        assert result.duration_seconds == 0.0
        assert result.error is None
        assert result.rollback_command is None
        assert result.details == {}

    def test_rollback_command(self):
        result = FixResult.success(
            'UFW Regel hinzugefuegt',
            rollback_command='ufw delete deny from 1.2.3.4',
        )
        assert result.rollback_command == 'ufw delete deny from 1.2.3.4'

    def test_extra_details(self):
        result = FixResult.success(
            'Fix angewendet',
            details={'affected_packages': ['openssl', 'libssl']},
        )
        assert result.details['affected_packages'] == ['openssl', 'libssl']

    def test_duration_seconds(self):
        result = FixResult(status='success', duration_seconds=3.14)
        assert result.duration_seconds == 3.14
