"""Security Engine v6 — Datenmodelle"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class Severity(Enum):
    """Schweregrad eines Security-Events."""

    CRITICAL = 'CRITICAL'
    HIGH = 'HIGH'
    MEDIUM = 'MEDIUM'
    LOW = 'LOW'

    @property
    def priority(self) -> int:
        """Numerische Prioritaet (0 = hoechste)."""
        return {'CRITICAL': 0, 'HIGH': 1, 'MEDIUM': 2, 'LOW': 3}[self.value]


class PhaseType(Enum):
    """Phasen der Security-Remediation-Pipeline."""

    RECON = 'recon'
    CONTAIN = 'contain'
    FIX = 'fix'
    VERIFY = 'verify'
    MONITOR = 'monitor'

    @property
    def is_read_only(self) -> bool:
        """True wenn die Phase keine Aenderungen am System vornimmt."""
        return self in (PhaseType.RECON, PhaseType.VERIFY, PhaseType.MONITOR)


class EngineMode(Enum):
    """Betriebsmodus der Security Engine."""

    REACTIVE = 'reactive'
    PROACTIVE = 'proactive'
    DEEP_SCAN = 'deep_scan'


@dataclass
class SecurityEvent(ABC):
    """Abstraktes Basis-Event fuer alle Security-Quellen."""

    source: str
    severity: Severity
    details: Dict[str, Any]
    event_id: str = ''
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    @abstractmethod
    def event_type(self) -> str:
        """Eindeutiger Typ-String des Events."""
        ...

    @property
    @abstractmethod
    def is_persistent(self) -> bool:
        """True wenn das Event bestehen bleibt bis es gefixt wird."""
        ...

    @property
    def signature(self) -> str:
        """Eindeutige Signatur: source_event_type."""
        return f"{self.source}_{self.event_type}"

    def to_dict(self) -> Dict[str, Any]:
        """Serialisierung als Dictionary."""
        return {
            'source': self.source,
            'event_type': self.event_type,
            'severity': self.severity.value,
            'details': self.details,
            'event_id': self.event_id,
            'timestamp': self.timestamp.isoformat(),
            'signature': self.signature,
            'is_persistent': self.is_persistent,
        }


@dataclass
class BanEvent(SecurityEvent):
    """IP-Ban Event (Fail2ban, CrowdSec)."""

    @property
    def event_type(self) -> str:
        return 'ban'

    @property
    def is_persistent(self) -> bool:
        return False


@dataclass
class ThreatEvent(SecurityEvent):
    """Bedrohungs-Event (CrowdSec Alerts, Anomalien)."""

    @property
    def event_type(self) -> str:
        return 'threat'

    @property
    def is_persistent(self) -> bool:
        return False


@dataclass
class VulnEvent(SecurityEvent):
    """Schwachstellen-Event (Trivy, CVE-Scans)."""

    @property
    def event_type(self) -> str:
        return 'vulnerability'

    @property
    def is_persistent(self) -> bool:
        return True


@dataclass
class IntegrityEvent(SecurityEvent):
    """Datei-Integritaets-Event (AIDE)."""

    @property
    def event_type(self) -> str:
        return 'integrity_violation'

    @property
    def is_persistent(self) -> bool:
        return True


@dataclass
class FixResult:
    """Ergebnis einer Remediation-Aktion."""

    status: str  # 'success', 'failed', 'no_op', 'skipped_duplicate', 'partial'
    message: str = ''
    phase_type: Optional[PhaseType] = None
    duration_seconds: float = 0.0
    error: Optional[str] = None
    rollback_command: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def success(cls, message: str, phase_type: PhaseType = None, **kwargs) -> FixResult:
        """Factory fuer erfolgreichen Fix."""
        return cls(status='success', message=message, phase_type=phase_type, **kwargs)

    @classmethod
    def failed(cls, error: str, phase_type: PhaseType = None, **kwargs) -> FixResult:
        """Factory fuer fehlgeschlagenen Fix."""
        return cls(status='failed', error=error, phase_type=phase_type, **kwargs)

    @classmethod
    def no_op(cls, message: str, phase_type: PhaseType = None, **kwargs) -> FixResult:
        """Factory wenn kein Fix noetig war."""
        return cls(status='no_op', message=message, phase_type=phase_type, **kwargs)

    @classmethod
    def skipped(cls, message: str, phase_type: PhaseType = None, **kwargs) -> FixResult:
        """Factory fuer uebersprungene Duplikate."""
        return cls(status='skipped_duplicate', message=message, phase_type=phase_type, **kwargs)

    @property
    def is_success(self) -> bool:
        """True wenn der Fix als erfolgreich gilt."""
        return self.status in ('success', 'no_op', 'skipped_duplicate')
