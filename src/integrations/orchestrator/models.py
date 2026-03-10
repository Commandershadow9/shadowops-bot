"""
Dataclasses für den Remediation Orchestrator
"""

import time
from typing import Dict, List, Optional, Set
from dataclasses import dataclass, field


@dataclass
class SecurityEventBatch:
    """Batch von Security-Events die zusammen behandelt werden"""
    events: List = field(default_factory=list)
    batch_id: str = ""
    created_at: float = 0.0
    status: str = "collecting"  # collecting, analyzing, awaiting_approval, executing, completed, failed
    status_message_id: Optional[int] = None  # Discord Message ID für Live-Updates
    status_channel_id: Optional[int] = None  # Discord Channel ID für Live-Updates

    def __post_init__(self):
        if not self.batch_id:
            self.batch_id = f"batch_{int(time.time())}"
        if not self.created_at:
            self.created_at = time.time()

    @property
    def severity_priority(self) -> int:
        """Höchste Severity im Batch (für Priorisierung)"""
        severity_map = {'CRITICAL': 4, 'HIGH': 3, 'MEDIUM': 2, 'LOW': 1, 'UNKNOWN': 0}
        return max([severity_map.get(e.severity, 0) for e in self.events], default=0)

    @property
    def sources(self) -> Set[str]:
        """Alle Event-Quellen im Batch"""
        return {e.source for e in self.events}

    def add_event(self, event):
        """Fügt Event zum Batch hinzu"""
        self.events.append(event)


@dataclass
class RemediationPlan:
    """Koordinierter Gesamt-Plan für alle Fixes"""
    batch_id: str
    description: str
    phases: List[Dict] = field(default_factory=list)
    confidence: float = 0.0
    estimated_duration_minutes: int = 0
    requires_restart: bool = False
    rollback_plan: str = ""
    ai_model: str = ""
    created_at: float = field(default_factory=time.time)
