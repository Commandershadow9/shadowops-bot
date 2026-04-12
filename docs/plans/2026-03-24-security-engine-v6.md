# Security Engine v6 — Implementierungsplan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Die 4 fragmentierten Security-Systeme (EventWatcher, Orchestrator, Self-Healing, Analyst) zu einer einheitlichen Security Engine zusammenführen — ein Hirn, drei Modi, eine Datenbank.

**Architecture:** Agent-Framework-Pattern (ABC + Hooks + Provider-Chain + Circuit-Breaker) angewendet auf Security. Drei Modi (Reactive, Proactive, DeepScan) teilen sich eine unified asyncpg-Datenbank und eine FixProvider-Registry. Phase-Type-System (recon/contain/fix/verify/monitor) steuert die Execution semantisch.

**Tech Stack:** Python 3.12, asyncpg, discord.py 2.7, Dual-Engine AI (Codex + Claude CLI)

---

## DB-Entscheidungen

### BEHALTEN (security_analyst DB, Port 5433)
Die `security_analyst` PostgreSQL-DB bleibt als **einzige Security-DB**. Beide Access-Layer (psycopg2 KnowledgeBase + asyncpg AnalystDB) werden zu **einem asyncpg-Layer** konsolidiert.

**Behalten + erweitern:**
| Tabelle | Status | Änderung |
|---------|--------|----------|
| `sessions` | ✅ Behalten | +`mode` (reactive/proactive/deep_scan), +`phase_types_used` |
| `findings` | ✅ Behalten | Keine Änderung |
| `knowledge` | ✅ Behalten | Keine Änderung (Decay funktioniert gut) |
| `learned_patterns` | ✅ Behalten | Keine Änderung |
| `health_snapshots` | ✅ Behalten | Keine Änderung |
| `orchestrator_strategies` | ✅ Behalten | +`phase_type` Spalte |
| `orchestrator_plans` | ✅ Behalten | Phases-JSONB bekommt `type`-Feld pro Phase |
| `threat_patterns` | ✅ Behalten | Keine Änderung |
| `orchestrator_vulnerabilities` | ✅ Behalten | Keine Änderung |

**Zusammenführen:**
| Alt | Neu | Grund |
|-----|-----|-------|
| `orchestrator_fixes` (KB) + `fix_attempts` (Analyst) | `fix_attempts` (unified) | Selbe Daten, doppelt getrackt |

**Neu erstellen:**
| Tabelle | Zweck |
|---------|-------|
| `remediation_status` | Cross-Mode Lock: wer arbeitet gerade woran? |
| `phase_executions` | Jede Phase-Execution mit Typ, Dauer, Ergebnis |

**Entfernen:**
| Tabelle | Grund |
|---------|-------|
| `orchestrator_fixes` | → migriert nach `fix_attempts` (unified) |
| `orchestrator_code_changes` | Niedriger Wert, Git-Log ist Quelle der Wahrheit |
| `orchestrator_log_patterns` | Niedriger Wert, kaum genutzt |

### NICHT ANFASSEN
| DB | Grund |
|----|-------|
| `agent_learning` (Patch Notes, Cross-Agent) | Kein Security-System, funktioniert gut |
| `changelogs.db` (SQLite) | Kein Security-System |

### Migration
Daten aus `orchestrator_fixes` → `fix_attempts` migrieren (Spalten-Mapping). Alte Tabellen als `_deprecated` umbenennen (nicht löschen).

---

## Dateistruktur (Neu)

```
src/integrations/security_engine/
├── __init__.py              # Exports: SecurityEngine, PhaseType, SecurityEvent
├── models.py                # SecurityEvent, PhaseType, FixResult, EngineMode
├── db.py                    # SecurityDB (unified asyncpg, ersetzt KnowledgeBase + AnalystDB)
├── engine.py                # SecurityEngine Hauptklasse (3 Modi, Hooks)
├── reactive.py              # ReactiveMode (Fast-Path + Batch + KI-Plan)
├── proactive.py             # ProactiveMode (Coverage, Trends, Härtung)
├── deep_scan.py             # DeepScanMode (AI-Sessions, Learning Pipeline)
├── executor.py              # PhaseTypeExecutor (recon/contain/fix/verify/monitor Handler)
├── registry.py              # FixerRegistry (Plugin-System für Fixer)
├── providers.py             # FixProvider ABC + NoOpProvider + BashFixProvider
└── circuit_breaker.py       # CircuitBreaker (aus Agent Framework Pattern)
```

**Bleibt bestehen (unverändert):**
```
src/integrations/fixers/         # Trivy, CrowdSec, Fail2ban, AIDE Fixer (nur Interface-Adapter)
src/integrations/backup_manager.py
src/integrations/command_executor.py
src/integrations/approval_modes.py
src/integrations/impact_analyzer.py
src/integrations/context_manager.py
src/integrations/ai_engine.py
```

**Wird ersetzt (nach Migration löschen):**
```
src/integrations/knowledge_base.py          → security_engine/db.py
src/integrations/self_healing.py            → security_engine/engine.py + executor.py
src/integrations/orchestrator/              → security_engine/reactive.py + executor.py
src/integrations/analyst/security_analyst.py → security_engine/deep_scan.py
src/integrations/analyst/analyst_db.py      → security_engine/db.py
src/integrations/event_watcher.py           → security_engine/engine.py (Event-Loop)
```

---

## Phase 1: Foundation (DB + Models + Provider ABC)

### Task 1.1: SecurityEvent + PhaseType Models

**Files:**
- Create: `src/integrations/security_engine/__init__.py`
- Create: `src/integrations/security_engine/models.py`
- Test: `tests/unit/test_security_models.py`

**Step 1: Write the test**

```python
# tests/unit/test_security_models.py
import pytest
from src.integrations.security_engine.models import (
    SecurityEvent, BanEvent, VulnEvent, IntegrityEvent, ThreatEvent,
    PhaseType, FixResult, EngineMode, Severity
)

class TestSecurityEvent:
    def test_ban_event_creation(self):
        event = BanEvent(
            source='fail2ban',
            severity=Severity.HIGH,
            details={'ip': '1.2.3.4', 'jail': 'sshd'},
            event_id='f2b_001'
        )
        assert event.source == 'fail2ban'
        assert event.event_type == 'ban'
        assert event.severity == Severity.HIGH
        assert event.is_persistent is False  # Bans sind self-resolving

    def test_vuln_event_is_persistent(self):
        event = VulnEvent(
            source='trivy',
            severity=Severity.CRITICAL,
            details={'cve': 'CVE-2026-1234', 'package': 'openssl'},
            event_id='trivy_001'
        )
        assert event.is_persistent is True  # CVEs brauchen Fix

    def test_event_signature(self):
        event = BanEvent(
            source='fail2ban',
            severity=Severity.HIGH,
            details={'ip': '1.2.3.4', 'jail': 'sshd'},
            event_id='f2b_001'
        )
        assert event.signature == 'fail2ban_ban'

    def test_event_to_dict(self):
        event = BanEvent(
            source='fail2ban',
            severity=Severity.HIGH,
            details={'ip': '1.2.3.4'},
            event_id='f2b_001'
        )
        d = event.to_dict()
        assert d['source'] == 'fail2ban'
        assert d['event_type'] == 'ban'
        assert d['severity'] == 'HIGH'

class TestPhaseType:
    def test_all_types_exist(self):
        assert PhaseType.RECON.value == 'recon'
        assert PhaseType.CONTAIN.value == 'contain'
        assert PhaseType.FIX.value == 'fix'
        assert PhaseType.VERIFY.value == 'verify'
        assert PhaseType.MONITOR.value == 'monitor'

    def test_is_read_only(self):
        assert PhaseType.RECON.is_read_only is True
        assert PhaseType.VERIFY.is_read_only is True
        assert PhaseType.MONITOR.is_read_only is True
        assert PhaseType.FIX.is_read_only is False
        assert PhaseType.CONTAIN.is_read_only is False

class TestFixResult:
    def test_success(self):
        r = FixResult.success("Jail hardened", phase_type=PhaseType.FIX)
        assert r.status == 'success'
        assert r.phase_type == PhaseType.FIX

    def test_no_op(self):
        r = FixResult.no_op("Config bereits korrekt", phase_type=PhaseType.FIX)
        assert r.status == 'no_op'

    def test_skipped(self):
        r = FixResult.skipped("Event bereits gefixt", phase_type=PhaseType.FIX)
        assert r.status == 'skipped_duplicate'

class TestEngineMode:
    def test_modes(self):
        assert EngineMode.REACTIVE.value == 'reactive'
        assert EngineMode.PROACTIVE.value == 'proactive'
        assert EngineMode.DEEP_SCAN.value == 'deep_scan'
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_security_models.py -x -v`
Expected: FAIL (ModuleNotFoundError)

**Step 3: Implement models**

```python
# src/integrations/security_engine/models.py
"""Security Engine v6 — Datenmodelle"""

from __future__ import annotations
from abc import ABC
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class Severity(Enum):
    CRITICAL = 'CRITICAL'
    HIGH = 'HIGH'
    MEDIUM = 'MEDIUM'
    LOW = 'LOW'

    @property
    def priority(self) -> int:
        return {'CRITICAL': 0, 'HIGH': 1, 'MEDIUM': 2, 'LOW': 3}[self.value]


class PhaseType(Enum):
    RECON = 'recon'       # Beweissicherung, Ist-Zustand (read-only)
    CONTAIN = 'contain'   # Sofort-Block: IP bannen, Quarantäne
    FIX = 'fix'           # Config ändern, Härten, Patchen
    VERIFY = 'verify'     # Prüfen ob Fix wirkt (Fail = Rollback)
    MONITOR = 'monitor'   # Nachbeobachtung, Alerting

    @property
    def is_read_only(self) -> bool:
        return self in (PhaseType.RECON, PhaseType.VERIFY, PhaseType.MONITOR)


class EngineMode(Enum):
    REACTIVE = 'reactive'       # Event → sofort reagieren
    PROACTIVE = 'proactive'     # Geplant: Coverage, Trends, Härtung
    DEEP_SCAN = 'deep_scan'     # AI-Session mit Learning Pipeline


@dataclass
class SecurityEvent(ABC):
    """Basis für alle Security-Events"""
    source: str
    severity: Severity
    details: Dict[str, Any]
    event_id: str = ''
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def event_type(self) -> str:
        raise NotImplementedError

    @property
    def is_persistent(self) -> bool:
        """True = braucht Fix (CVE, Tampering), False = self-resolving (Ban, Block)"""
        raise NotImplementedError

    @property
    def signature(self) -> str:
        return f"{self.source}_{self.event_type}"

    def to_dict(self) -> Dict[str, Any]:
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
    @property
    def event_type(self) -> str:
        return 'ban'

    @property
    def is_persistent(self) -> bool:
        return False


@dataclass
class ThreatEvent(SecurityEvent):
    @property
    def event_type(self) -> str:
        return 'threat'

    @property
    def is_persistent(self) -> bool:
        return False


@dataclass
class VulnEvent(SecurityEvent):
    @property
    def event_type(self) -> str:
        return 'vulnerability'

    @property
    def is_persistent(self) -> bool:
        return True


@dataclass
class IntegrityEvent(SecurityEvent):
    @property
    def event_type(self) -> str:
        return 'integrity_violation'

    @property
    def is_persistent(self) -> bool:
        return True


@dataclass
class FixResult:
    """Ergebnis einer Fix-Execution"""
    status: str  # 'success', 'failed', 'no_op', 'skipped_duplicate', 'partial'
    message: str = ''
    phase_type: Optional[PhaseType] = None
    duration_seconds: float = 0.0
    error: Optional[str] = None
    rollback_command: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def success(cls, message: str, phase_type: PhaseType = None, **kwargs) -> FixResult:
        return cls(status='success', message=message, phase_type=phase_type, **kwargs)

    @classmethod
    def failed(cls, error: str, phase_type: PhaseType = None, **kwargs) -> FixResult:
        return cls(status='failed', error=error, phase_type=phase_type, **kwargs)

    @classmethod
    def no_op(cls, message: str, phase_type: PhaseType = None, **kwargs) -> FixResult:
        return cls(status='no_op', message=message, phase_type=phase_type, **kwargs)

    @classmethod
    def skipped(cls, message: str, phase_type: PhaseType = None, **kwargs) -> FixResult:
        return cls(status='skipped_duplicate', message=message, phase_type=phase_type, **kwargs)

    @property
    def is_success(self) -> bool:
        return self.status in ('success', 'no_op', 'skipped_duplicate')
```

```python
# src/integrations/security_engine/__init__.py
"""Security Engine v6 — Unified Security System"""

from .models import (
    SecurityEvent, BanEvent, ThreatEvent, VulnEvent, IntegrityEvent,
    PhaseType, FixResult, EngineMode, Severity,
)

__all__ = [
    'SecurityEvent', 'BanEvent', 'ThreatEvent', 'VulnEvent', 'IntegrityEvent',
    'PhaseType', 'FixResult', 'EngineMode', 'Severity',
]
```

**Step 4: Run test**

Run: `pytest tests/unit/test_security_models.py -x -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/integrations/security_engine/ tests/unit/test_security_models.py
git commit -m "feat(security-engine): SecurityEvent ABC + PhaseType + FixResult Models"
```

---

### Task 1.2: FixProvider ABC + NoOpProvider + Registry

**Files:**
- Create: `src/integrations/security_engine/providers.py`
- Create: `src/integrations/security_engine/registry.py`
- Test: `tests/unit/test_fix_providers.py`

**Step 1: Write the test**

```python
# tests/unit/test_fix_providers.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from src.integrations.security_engine.models import (
    BanEvent, PhaseType, FixResult, Severity
)
from src.integrations.security_engine.providers import (
    FixProvider, NoOpProvider, BashFixProvider
)
from src.integrations.security_engine.registry import FixerRegistry


class TestNoOpProvider:
    @pytest.mark.asyncio
    async def test_detects_no_change(self):
        provider = NoOpProvider()
        event = BanEvent(
            source='fail2ban', severity=Severity.HIGH,
            details={'ip': '1.2.3.4'}, event_id='test'
        )
        # NoOp prüft: target_config == current_config
        result = await provider.execute(
            event,
            strategy={'description': 'harden'},
            context={'current_config': {'maxretry': 3, 'bantime': 3600},
                     'target_config': {'maxretry': 3, 'bantime': 3600}}
        )
        assert result.status == 'no_op'
        assert result.is_success is True

    @pytest.mark.asyncio
    async def test_detects_change_needed(self):
        provider = NoOpProvider()
        event = BanEvent(
            source='fail2ban', severity=Severity.HIGH,
            details={'ip': '1.2.3.4'}, event_id='test'
        )
        result = await provider.execute(
            event,
            strategy={'description': 'harden'},
            context={'current_config': {'maxretry': 5, 'bantime': 600},
                     'target_config': {'maxretry': 3, 'bantime': 3600}}
        )
        assert result is None  # None = "ich bin nicht zuständig, nächster Provider"


class TestFixerRegistry:
    def test_register_and_lookup(self):
        registry = FixerRegistry()
        mock_provider = MagicMock(spec=FixProvider)
        registry.register('fail2ban', PhaseType.FIX, mock_provider)
        providers = registry.get_providers('fail2ban', PhaseType.FIX)
        assert mock_provider in providers

    def test_fallback_to_source_only(self):
        registry = FixerRegistry()
        mock_provider = MagicMock(spec=FixProvider)
        registry.register('fail2ban', None, mock_provider)  # Für alle PhaseTypes
        providers = registry.get_providers('fail2ban', PhaseType.CONTAIN)
        assert mock_provider in providers

    def test_no_op_always_first(self):
        registry = FixerRegistry()
        noop = NoOpProvider()
        fixer = MagicMock(spec=FixProvider)
        registry.register('fail2ban', PhaseType.FIX, fixer)
        registry.register_noop(noop)
        providers = registry.get_providers('fail2ban', PhaseType.FIX)
        assert providers[0] is noop  # NoOp immer zuerst
```

**Step 2: Run test → FAIL**

**Step 3: Implement**

```python
# src/integrations/security_engine/providers.py
"""Fix-Provider ABC und Basis-Implementierungen"""

from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from .models import SecurityEvent, PhaseType, FixResult


class FixProvider(ABC):
    """Basis-Klasse für alle Fix-Provider (wie AIProviderChain im Agent Framework)"""

    @abstractmethod
    async def execute(
        self,
        event: SecurityEvent,
        strategy: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> Optional[FixResult]:
        """
        Führt Fix aus.

        Returns:
            FixResult bei Erfolg/Fehler, None wenn dieser Provider nicht zuständig ist.
            None signalisiert: nächsten Provider in der Chain versuchen.
        """
        ...


class NoOpProvider(FixProvider):
    """Erkennt wenn ein Fix nicht nötig ist (Config bereits korrekt)"""

    async def execute(
        self,
        event: SecurityEvent,
        strategy: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> Optional[FixResult]:
        if not context:
            return None

        current = context.get('current_config')
        target = context.get('target_config')

        if current is None or target is None:
            return None

        if current == target:
            return FixResult.no_op(
                f"Config bereits korrekt: {current}",
                phase_type=PhaseType.FIX,
            )

        return None  # Änderung nötig → nächster Provider


class BashFixProvider(FixProvider):
    """Führt Fixes via CommandExecutor aus (sudo-Commands)"""

    def __init__(self, command_executor):
        self.executor = command_executor

    async def execute(
        self,
        event: SecurityEvent,
        strategy: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> Optional[FixResult]:
        commands = strategy.get('commands', [])
        if not commands:
            return None

        import time
        start = time.time()
        errors = []

        for cmd in commands:
            result = await self.executor.execute(cmd, sudo=True, timeout=30)
            if not result.get('success', False):
                errors.append(result.get('error', f'Command failed: {cmd}'))

        duration = time.time() - start

        if errors:
            return FixResult.failed(
                '; '.join(errors),
                phase_type=context.get('phase_type', PhaseType.FIX) if context else PhaseType.FIX,
                duration_seconds=duration,
            )

        return FixResult.success(
            strategy.get('description', 'Bash fix applied'),
            phase_type=context.get('phase_type', PhaseType.FIX) if context else PhaseType.FIX,
            duration_seconds=duration,
        )
```

```python
# src/integrations/security_engine/registry.py
"""Fixer-Registry — Plugin-System für Fix-Provider"""

from __future__ import annotations
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from .models import PhaseType
from .providers import FixProvider, NoOpProvider


class FixerRegistry:
    """
    Registriert FixProvider pro (source, phase_type) Kombination.

    Lookup-Reihenfolge:
    1. NoOp-Provider (immer zuerst, prüft ob Fix nötig ist)
    2. Exakte Matches: (source, phase_type)
    3. Fallback: (source, None) — für alle PhaseTypes
    """

    def __init__(self):
        self._providers: Dict[Tuple[str, Optional[PhaseType]], List[FixProvider]] = defaultdict(list)
        self._noop: Optional[NoOpProvider] = None

    def register(self, source: str, phase_type: Optional[PhaseType], provider: FixProvider):
        """Registriert einen Provider für source + optional phase_type"""
        self._providers[(source, phase_type)].append(provider)

    def register_noop(self, provider: NoOpProvider):
        """Registriert den NoOp-Provider (global, wird immer zuerst geprüft)"""
        self._noop = provider

    def get_providers(self, source: str, phase_type: PhaseType) -> List[FixProvider]:
        """
        Gibt Provider-Chain für (source, phase_type) zurück.

        Reihenfolge: [NoOp] + [exakte Matches] + [source-Fallbacks]
        """
        chain: List[FixProvider] = []

        if self._noop:
            chain.append(self._noop)

        chain.extend(self._providers.get((source, phase_type), []))
        chain.extend(self._providers.get((source, None), []))

        return chain

    def list_registered(self) -> Dict[str, List[str]]:
        """Debug: Zeigt alle registrierten Provider"""
        result = {}
        for (source, ptype), providers in self._providers.items():
            key = f"{source}/{ptype.value if ptype else '*'}"
            result[key] = [type(p).__name__ for p in providers]
        return result
```

**Step 4: Run test → PASS**

**Step 5: Commit**

```bash
git add src/integrations/security_engine/providers.py src/integrations/security_engine/registry.py tests/unit/test_fix_providers.py
git commit -m "feat(security-engine): FixProvider ABC, NoOpProvider, FixerRegistry"
```

---

### Task 1.3: CircuitBreaker (nach Agent Framework Pattern)

**Files:**
- Create: `src/integrations/security_engine/circuit_breaker.py`
- Test: `tests/unit/test_circuit_breaker.py`

**Step 1: Write the test**

```python
# tests/unit/test_circuit_breaker.py
import pytest
import time
from src.integrations.security_engine.circuit_breaker import CircuitBreaker


class TestCircuitBreaker:
    def test_starts_closed(self):
        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=60)
        assert cb.is_closed is True
        assert cb.can_attempt is True

    def test_opens_after_threshold(self):
        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=60)
        cb.record_failure('test')
        cb.record_failure('test')
        assert cb.is_closed is True
        cb.record_failure('test')
        assert cb.is_closed is False
        assert cb.can_attempt is False

    def test_success_resets(self):
        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=60)
        cb.record_failure('test')
        cb.record_failure('test')
        cb.record_success('test')
        assert cb.failure_count == 0
        assert cb.is_closed is True

    def test_per_key_tracking(self):
        cb = CircuitBreaker(failure_threshold=2, cooldown_seconds=60)
        cb.record_failure('fail2ban')
        cb.record_failure('fail2ban')
        assert cb.is_open_for('fail2ban') is True
        assert cb.is_open_for('trivy') is False

    def test_cooldown_resets(self):
        cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=0.1)
        cb.record_failure('test')
        assert cb.can_attempt is False
        time.sleep(0.15)
        assert cb.can_attempt is True

    def test_get_status(self):
        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=60)
        cb.record_failure('test')
        status = cb.get_status()
        assert status['failure_count'] == 1
        assert status['is_open'] is False
        assert status['threshold'] == 3
```

**Step 2: Run test → FAIL**

**Step 3: Implement**

```python
# src/integrations/security_engine/circuit_breaker.py
"""Circuit Breaker — verhindert Retry-Loops (Agent Framework Pattern)"""

from __future__ import annotations
import time
from collections import defaultdict
from typing import Any, Dict


class CircuitBreaker:
    """
    Per-Key Circuit Breaker.

    Nach failure_threshold Fehlern für einen Key → Sperre für cooldown_seconds.
    Ein Erfolg resettet den Counter für den Key.
    Global: can_attempt ist False wenn IRGENDEIN Key offen ist.
    """

    def __init__(self, failure_threshold: int = 5, cooldown_seconds: int = 3600):
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._failures: Dict[str, int] = defaultdict(int)
        self._opened_at: Dict[str, float] = {}

    def record_failure(self, key: str = '_global') -> None:
        self._failures[key] += 1
        if self._failures[key] >= self.failure_threshold:
            self._opened_at[key] = time.time()

    def record_success(self, key: str = '_global') -> None:
        self._failures[key] = 0
        self._opened_at.pop(key, None)

    def is_open_for(self, key: str) -> bool:
        if key not in self._opened_at:
            return False
        elapsed = time.time() - self._opened_at[key]
        if elapsed >= self.cooldown_seconds:
            self._failures[key] = 0
            del self._opened_at[key]
            return False
        return True

    @property
    def is_closed(self) -> bool:
        return not any(self.is_open_for(k) for k in list(self._opened_at))

    @property
    def can_attempt(self) -> bool:
        return self.is_closed

    @property
    def failure_count(self) -> int:
        return sum(self._failures.values())

    def get_status(self) -> Dict[str, Any]:
        return {
            'is_open': not self.is_closed,
            'failure_count': self.failure_count,
            'threshold': self.failure_threshold,
            'cooldown_seconds': self.cooldown_seconds,
            'open_keys': [k for k in self._opened_at if self.is_open_for(k)],
        }
```

**Step 4: Run test → PASS**

**Step 5: Commit**

```bash
git add src/integrations/security_engine/circuit_breaker.py tests/unit/test_circuit_breaker.py
git commit -m "feat(security-engine): CircuitBreaker mit per-Key Tracking"
```

---

### Task 1.4: SecurityDB (Unified asyncpg Layer)

**Files:**
- Create: `src/integrations/security_engine/db.py`
- Create: `scripts/migrate_security_db.py`
- Test: `tests/unit/test_security_db.py`

**Step 1: Write the test**

```python
# tests/unit/test_security_db.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.integrations.security_engine.db import SecurityDB


class TestSecurityDB:
    """Unit-Tests mit gemockter DB-Connection"""

    @pytest.mark.asyncio
    async def test_record_fix_attempt(self):
        db = SecurityDB.__new__(SecurityDB)
        db.pool = AsyncMock()
        db.pool.fetchrow = AsyncMock(return_value={'id': 42})

        fix_id = await db.record_fix_attempt(
            event_source='fail2ban',
            event_type='ban',
            event_signature='fail2ban_ban',
            phase_type='fix',
            approach='harden_config',
            commands=['sudo fail2ban-client set sshd maxretry 3'],
            result='success',
            duration_ms=150,
        )
        assert fix_id == 42

    @pytest.mark.asyncio
    async def test_record_no_op(self):
        db = SecurityDB.__new__(SecurityDB)
        db.pool = AsyncMock()
        db.pool.fetchrow = AsyncMock(return_value={'id': 43})

        fix_id = await db.record_fix_attempt(
            event_source='fail2ban',
            event_type='ban',
            event_signature='fail2ban_ban',
            phase_type='fix',
            approach='harden_config',
            commands=[],
            result='no_op',
            duration_ms=5,
            metadata={'reason': 'Config bereits korrekt'}
        )
        assert fix_id == 43

    @pytest.mark.asyncio
    async def test_claim_event(self):
        db = SecurityDB.__new__(SecurityDB)
        db.pool = AsyncMock()
        # Simulate successful claim (no existing claim)
        db.pool.fetchrow = AsyncMock(return_value={'id': 1})
        claimed = await db.claim_event('evt_001', 'reactive')
        assert claimed is True

    @pytest.mark.asyncio
    async def test_get_success_rate(self):
        db = SecurityDB.__new__(SecurityDB)
        db.pool = AsyncMock()
        db.pool.fetchrow = AsyncMock(return_value={
            'total': 10, 'successes': 8, 'no_ops': 1
        })
        rate = await db.get_success_rate('fail2ban_ban', days=30)
        assert rate == pytest.approx(0.9)  # (8+1)/10

    @pytest.mark.asyncio
    async def test_record_phase_execution(self):
        db = SecurityDB.__new__(SecurityDB)
        db.pool = AsyncMock()
        db.pool.fetchrow = AsyncMock(return_value={'id': 1})

        phase_id = await db.record_phase_execution(
            batch_id='batch_123',
            phase_type='contain',
            phase_name='IP blocken',
            events_processed=3,
            result='success',
            duration_ms=500,
        )
        assert phase_id == 1
```

**Step 2: Run test → FAIL**

**Step 3: Implement SecurityDB**

```python
# src/integrations/security_engine/db.py
"""
SecurityDB — Unified async Database Layer (asyncpg)

Ersetzt: KnowledgeBase (psycopg2, sync) + AnalystDB (asyncpg, async)
Nutzt: security_analyst DB auf Port 5433

Neue Tabellen: remediation_status, phase_executions
Migrierte Tabelle: fix_attempts (merged aus orchestrator_fixes + analyst fix_attempts)
"""

from __future__ import annotations
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

import asyncpg

logger = logging.getLogger('shadowops.security_db')


class SecurityDB:
    """Unified Security Database — ein asyncpg-Pool für alles"""

    def __init__(self, dsn: str):
        self.dsn = dsn
        self.pool: Optional[asyncpg.Pool] = None

    async def initialize(self):
        """Pool erstellen und Schema migrieren"""
        self.pool = await asyncpg.create_pool(self.dsn, min_size=2, max_size=5)
        await self._ensure_schema()
        logger.info("SecurityDB initialisiert (asyncpg)")

    async def close(self):
        if self.pool:
            await self.pool.close()

    async def _ensure_schema(self):
        """Erstellt neue Tabellen (IF NOT EXISTS), lässt bestehende intakt"""
        async with self.pool.acquire() as conn:
            await conn.execute("""
                -- Unified Fix-Attempts (ersetzt orchestrator_fixes + analyst fix_attempts)
                CREATE TABLE IF NOT EXISTS fix_attempts_v2 (
                    id SERIAL PRIMARY KEY,
                    event_source TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    event_signature TEXT NOT NULL,
                    phase_type TEXT NOT NULL DEFAULT 'fix',
                    approach TEXT,
                    commands JSONB DEFAULT '[]',
                    result TEXT NOT NULL CHECK(result IN (
                        'success', 'failed', 'partial', 'no_op', 'skipped_duplicate'
                    )),
                    error_message TEXT,
                    duration_ms INTEGER DEFAULT 0,
                    was_fast_path BOOLEAN DEFAULT FALSE,
                    engine_mode TEXT DEFAULT 'reactive',
                    batch_id TEXT,
                    finding_id INTEGER,
                    metadata JSONB DEFAULT '{}',
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_fix_attempts_v2_sig
                    ON fix_attempts_v2(event_signature);
                CREATE INDEX IF NOT EXISTS idx_fix_attempts_v2_result
                    ON fix_attempts_v2(result);
                CREATE INDEX IF NOT EXISTS idx_fix_attempts_v2_created
                    ON fix_attempts_v2(created_at);

                -- Cross-Mode Remediation Lock
                CREATE TABLE IF NOT EXISTS remediation_status (
                    id SERIAL PRIMARY KEY,
                    event_signature TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    handler TEXT NOT NULL CHECK(handler IN (
                        'reactive', 'proactive', 'deep_scan'
                    )),
                    status TEXT NOT NULL DEFAULT 'in_progress' CHECK(status IN (
                        'in_progress', 'completed', 'failed', 'released'
                    )),
                    claimed_at TIMESTAMPTZ DEFAULT NOW(),
                    completed_at TIMESTAMPTZ,
                    UNIQUE(event_id)
                );
                CREATE INDEX IF NOT EXISTS idx_remediation_status_sig
                    ON remediation_status(event_signature, status);

                -- Phase-Execution Tracking
                CREATE TABLE IF NOT EXISTS phase_executions (
                    id SERIAL PRIMARY KEY,
                    batch_id TEXT NOT NULL,
                    phase_type TEXT NOT NULL,
                    phase_name TEXT NOT NULL,
                    events_processed INTEGER DEFAULT 0,
                    result TEXT NOT NULL CHECK(result IN (
                        'success', 'failed', 'skipped', 'no_op'
                    )),
                    duration_ms INTEGER DEFAULT 0,
                    details JSONB DEFAULT '{}',
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_phase_exec_batch
                    ON phase_executions(batch_id);
                CREATE INDEX IF NOT EXISTS idx_phase_exec_type
                    ON phase_executions(phase_type);
            """)

    # ── Fix-Attempts ──────────────────────────────────────

    async def record_fix_attempt(
        self,
        event_source: str,
        event_type: str,
        event_signature: str,
        phase_type: str,
        approach: str,
        commands: List[str],
        result: str,
        duration_ms: int = 0,
        error_message: str = None,
        was_fast_path: bool = False,
        engine_mode: str = 'reactive',
        batch_id: str = None,
        finding_id: int = None,
        metadata: Dict = None,
    ) -> int:
        row = await self.pool.fetchrow("""
            INSERT INTO fix_attempts_v2 (
                event_source, event_type, event_signature, phase_type,
                approach, commands, result, duration_ms, error_message,
                was_fast_path, engine_mode, batch_id, finding_id, metadata
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
            RETURNING id
        """,
            event_source, event_type, event_signature, phase_type,
            approach, json.dumps(commands), result, duration_ms, error_message,
            was_fast_path, engine_mode, batch_id, finding_id,
            json.dumps(metadata or {}, default=str),
        )
        return row['id']

    async def get_fix_history(
        self, event_signature: str, days: int = 30, limit: int = 10
    ) -> List[Dict]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        rows = await self.pool.fetch("""
            SELECT id, approach, result, phase_type, duration_ms, error_message,
                   was_fast_path, created_at
            FROM fix_attempts_v2
            WHERE event_signature = $1 AND created_at > $2
            ORDER BY created_at DESC LIMIT $3
        """, event_signature, cutoff, limit)
        return [dict(r) for r in rows]

    async def get_success_rate(
        self, event_signature: str, days: int = 30
    ) -> float:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        row = await self.pool.fetchrow("""
            SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE result = 'success') as successes,
                COUNT(*) FILTER (WHERE result = 'no_op') as no_ops
            FROM fix_attempts_v2
            WHERE event_signature = $1 AND created_at > $2
        """, event_signature, cutoff)
        total = row['total']
        if total == 0:
            return 0.5  # Default: 50% bei keiner History
        return (row['successes'] + row['no_ops']) / total

    # ── Remediation Status (Cross-Mode Lock) ──────────────

    async def claim_event(self, event_id: str, handler: str) -> bool:
        """
        Versucht ein Event zu claimen. Returns False wenn schon claimed.
        Erlaubt Re-Claim wenn vorheriger Handler failed/released hat.
        """
        try:
            event_sig = event_id.rsplit('_', 1)[0] if '_' in event_id else event_id
            await self.pool.fetchrow("""
                INSERT INTO remediation_status (event_signature, event_id, handler)
                VALUES ($1, $2, $3)
                ON CONFLICT (event_id) DO UPDATE
                SET handler = $3, status = 'in_progress', claimed_at = NOW()
                WHERE remediation_status.status IN ('failed', 'released')
                RETURNING id
            """, event_sig, event_id, handler)
            return True
        except Exception:
            return False

    async def release_event(self, event_id: str, status: str = 'completed') -> None:
        await self.pool.execute("""
            UPDATE remediation_status
            SET status = $2, completed_at = NOW()
            WHERE event_id = $1
        """, event_id, status)

    async def is_event_claimed(self, event_id: str) -> Optional[str]:
        """Returns handler name if claimed and in_progress, else None"""
        row = await self.pool.fetchrow("""
            SELECT handler FROM remediation_status
            WHERE event_id = $1 AND status = 'in_progress'
        """, event_id)
        return row['handler'] if row else None

    # ── Phase Executions ──────────────────────────────────

    async def record_phase_execution(
        self,
        batch_id: str,
        phase_type: str,
        phase_name: str,
        events_processed: int,
        result: str,
        duration_ms: int = 0,
        details: Dict = None,
    ) -> int:
        row = await self.pool.fetchrow("""
            INSERT INTO phase_executions (
                batch_id, phase_type, phase_name, events_processed,
                result, duration_ms, details
            ) VALUES ($1,$2,$3,$4,$5,$6,$7)
            RETURNING id
        """,
            batch_id, phase_type, phase_name, events_processed,
            result, duration_ms, json.dumps(details or {}, default=str),
        )
        return row['id']

    async def get_phase_stats(self, days: int = 30) -> Dict[str, Any]:
        """Statistiken pro Phase-Typ"""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        rows = await self.pool.fetch("""
            SELECT phase_type,
                   COUNT(*) as total,
                   COUNT(*) FILTER (WHERE result = 'success') as successes,
                   COUNT(*) FILTER (WHERE result = 'no_op') as no_ops,
                   AVG(duration_ms) as avg_duration
            FROM phase_executions
            WHERE created_at > $1
            GROUP BY phase_type
        """, cutoff)
        return {r['phase_type']: dict(r) for r in rows}

    # ── Strategy Stats (existierende Tabelle nutzen) ──────

    async def update_strategy_stats(
        self, strategy_name: str, event_type: str, success: bool, phase_type: str = 'fix'
    ) -> None:
        await self.pool.execute("""
            INSERT INTO orchestrator_strategies (
                strategy_name, event_type, approach, success_rate,
                times_used, times_succeeded, last_used_at
            ) VALUES ($1, $2, $3, $4, 1, $5, NOW())
            ON CONFLICT (strategy_name, event_type) DO UPDATE SET
                times_used = orchestrator_strategies.times_used + 1,
                times_succeeded = orchestrator_strategies.times_succeeded + $5::int,
                success_rate = (orchestrator_strategies.times_succeeded + $5::int)::real
                    / (orchestrator_strategies.times_used + 1)::real,
                last_used_at = NOW()
        """, strategy_name, event_type, phase_type, 1.0 if success else 0.0,
             1 if success else 0)

    # ── Bestehende Analyst-Tabellen (Wrapper) ─────────────
    # Diese Methoden wrappen die existierenden Tabellen (sessions, findings,
    # knowledge, etc.) und werden in Phase 3 (DeepScan) vollständig integriert.
    # Hier nur die wichtigsten für Phase 1+2.

    async def get_open_findings_count(self) -> int:
        row = await self.pool.fetchrow(
            "SELECT COUNT(*) as cnt FROM findings WHERE status = 'open'"
        )
        return row['cnt'] if row else 0

    async def store_knowledge(
        self, category: str, subject: str, content: str, confidence: float = 0.5
    ) -> int:
        row = await self.pool.fetchrow("""
            INSERT INTO knowledge (category, subject, content, confidence, last_verified)
            VALUES ($1, $2, $3, $4, NOW())
            ON CONFLICT (category, subject) DO UPDATE SET
                content = $3, confidence = $4, updated_at = NOW(), last_verified = NOW()
            RETURNING id
        """, category, subject, content, confidence)
        return row['id']

    async def get_knowledge(self, category: str, min_confidence: float = 0.2) -> List[Dict]:
        rows = await self.pool.fetch("""
            SELECT subject, content, confidence, last_verified
            FROM knowledge
            WHERE category = $1 AND confidence >= $2
            ORDER BY confidence DESC
        """, category, min_confidence)
        return [dict(r) for r in rows]
```

**Step 4: Run test → PASS**

**Step 5: Write DB-Migration Script**

```python
# scripts/migrate_security_db.py
"""
Migration: orchestrator_fixes → fix_attempts_v2

Migriert bestehende Daten und benennt alte Tabelle um.
Idempotent: Kann mehrfach ausgeführt werden.
"""
import psycopg2
import psycopg2.extras
import sys

DSN = "dbname=security_analyst user=security_analyst password=SICHERES_PASSWORT host=127.0.0.1 port=5433"


def migrate():
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # 1. Prüfe ob fix_attempts_v2 existiert
    cur.execute("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables
            WHERE table_name = 'fix_attempts_v2'
        )
    """)
    if not cur.fetchone()['exists']:
        print("fix_attempts_v2 existiert noch nicht — wird von SecurityDB erstellt.")
        print("Starte erst den Bot, dann dieses Script erneut.")
        return

    # 2. Prüfe ob orchestrator_fixes existiert und Daten hat
    cur.execute("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables
            WHERE table_name = 'orchestrator_fixes'
        )
    """)
    if not cur.fetchone()['exists']:
        print("orchestrator_fixes existiert nicht — nichts zu migrieren.")
        return

    cur.execute("SELECT COUNT(*) as cnt FROM orchestrator_fixes")
    count = cur.fetchone()['cnt']
    print(f"Migriere {count} Einträge aus orchestrator_fixes → fix_attempts_v2...")

    # 3. Migriere Daten
    cur.execute("""
        INSERT INTO fix_attempts_v2 (
            event_source, event_type, event_signature, phase_type,
            approach, commands, result, duration_ms, error_message,
            was_fast_path, engine_mode, metadata, created_at
        )
        SELECT
            event_source,
            event_type,
            event_source || '_' || event_type as event_signature,
            'fix' as phase_type,
            fix_description as approach,
            COALESCE(fix_steps, '[]'::jsonb) as commands,
            CASE WHEN success THEN 'success' ELSE 'failed' END as result,
            COALESCE(execution_time_ms, 0) as duration_ms,
            error_message,
            FALSE as was_fast_path,
            'reactive' as engine_mode,
            COALESCE(metadata, '{}'::jsonb),
            created_at
        FROM orchestrator_fixes
        WHERE NOT EXISTS (
            SELECT 1 FROM fix_attempts_v2 v2
            WHERE v2.created_at = orchestrator_fixes.created_at
            AND v2.event_source = orchestrator_fixes.event_source
        )
    """)
    print(f"Migration abgeschlossen.")

    # 4. Alte Tabelle umbenennen (nicht löschen!)
    cur.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'orchestrator_fixes')
            AND NOT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'orchestrator_fixes_deprecated')
            THEN
                ALTER TABLE orchestrator_fixes RENAME TO orchestrator_fixes_deprecated;
                RAISE NOTICE 'orchestrator_fixes → orchestrator_fixes_deprecated';
            END IF;
        END $$;
    """)

    # 5. Ungenutzte Tabellen umbenennen
    for table in ['orchestrator_code_changes', 'orchestrator_log_patterns']:
        cur.execute(f"""
            DO $$
            BEGIN
                IF EXISTS (SELECT FROM information_schema.tables WHERE table_name = '{table}')
                AND NOT EXISTS (SELECT FROM information_schema.tables WHERE table_name = '{table}_deprecated')
                THEN
                    ALTER TABLE {table} RENAME TO {table}_deprecated;
                    RAISE NOTICE '{table} → {table}_deprecated';
                END IF;
            END $$;
        """)

    print("Alte Tabellen als _deprecated umbenannt.")
    print("DONE.")

    cur.close()
    conn.close()


if __name__ == '__main__':
    migrate()
```

**Step 6: Commit**

```bash
git add src/integrations/security_engine/db.py scripts/migrate_security_db.py tests/unit/test_security_db.py
git commit -m "feat(security-engine): SecurityDB unified asyncpg Layer + Migration"
```

---

### Task 1.5: Schema-Update (coordinated_plan.json)

**Files:**
- Modify: `src/schemas/coordinated_plan.json`
- Test: Manuell via jsonschema

**Step 1: Update Schema**

Füge `type` Feld zu Phase-Items hinzu und setze `maxItems: 4`:

```json
{
  "type": "object",
  "properties": {
    "description": { "type": "string" },
    "confidence": { "type": "number", "minimum": 0, "maximum": 1 },
    "estimated_duration_minutes": { "type": "integer" },
    "requires_restart": { "type": "boolean" },
    "phases": {
      "type": "array",
      "maxItems": 4,
      "items": {
        "type": "object",
        "properties": {
          "name": { "type": "string" },
          "type": {
            "type": "string",
            "enum": ["recon", "contain", "fix", "verify", "monitor"],
            "default": "fix"
          },
          "description": { "type": "string" },
          "steps": {
            "type": "array",
            "items": { "type": "string" }
          },
          "estimated_minutes": { "type": "integer" }
        },
        "required": ["name", "type", "description", "steps", "estimated_minutes"],
        "additionalProperties": false
      }
    },
    "rollback_plan": { "type": "string" }
  },
  "required": ["description", "confidence", "estimated_duration_minutes", "requires_restart", "phases", "rollback_plan"],
  "additionalProperties": false
}
```

**Step 2: Commit**

```bash
git add src/schemas/coordinated_plan.json
git commit -m "feat(security-engine): Phase-Type + maxItems:4 im coordinated_plan Schema"
```

---

## Phase 2: Reactive Mode (Smart Execution)

### Task 2.1: PhaseTypeExecutor

**Files:**
- Create: `src/integrations/security_engine/executor.py`
- Test: `tests/unit/test_phase_executor.py`

**Step 1: Write the test**

```python
# tests/unit/test_phase_executor.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from src.integrations.security_engine.executor import PhaseTypeExecutor
from src.integrations.security_engine.models import (
    BanEvent, PhaseType, FixResult, Severity
)
from src.integrations.security_engine.registry import FixerRegistry
from src.integrations.security_engine.providers import NoOpProvider


class TestPhaseTypeExecutor:
    @pytest.mark.asyncio
    async def test_recon_phase_read_only(self):
        """Recon darf keine Fixes ausführen"""
        registry = FixerRegistry()
        db = AsyncMock()
        db.record_phase_execution = AsyncMock(return_value=1)
        executor = PhaseTypeExecutor(registry=registry, db=db)

        event = BanEvent(
            source='fail2ban', severity=Severity.HIGH,
            details={'ip': '1.2.3.4'}, event_id='test'
        )
        phase = {
            'name': 'Beweissicherung',
            'type': 'recon',
            'description': 'Status erfassen',
            'steps': ['fail2ban-client status sshd']
        }
        result = await executor.execute_phase(phase, [event], batch_id='b1')
        assert result is True
        db.record_phase_execution.assert_called_once()

    @pytest.mark.asyncio
    async def test_fix_phase_calls_provider(self):
        """Fix-Phase ruft den registrierten Provider auf"""
        registry = FixerRegistry()
        mock_provider = AsyncMock()
        mock_provider.execute = AsyncMock(
            return_value=FixResult.success("Fixed", phase_type=PhaseType.FIX)
        )
        registry.register('fail2ban', PhaseType.FIX, mock_provider)

        db = AsyncMock()
        db.record_phase_execution = AsyncMock(return_value=1)
        db.record_fix_attempt = AsyncMock(return_value=1)
        executor = PhaseTypeExecutor(registry=registry, db=db)

        event = BanEvent(
            source='fail2ban', severity=Severity.HIGH,
            details={'ip': '1.2.3.4'}, event_id='test'
        )
        phase = {
            'name': 'Config härten',
            'type': 'fix',
            'description': 'Jail härten',
            'steps': ['maxretry=3']
        }
        result = await executor.execute_phase(phase, [event], batch_id='b1')
        assert result is True
        mock_provider.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_dedup_skips_already_fixed(self):
        """Events die bereits gefixt wurden werden übersprungen"""
        registry = FixerRegistry()
        mock_provider = AsyncMock()
        mock_provider.execute = AsyncMock(
            return_value=FixResult.success("Fixed", phase_type=PhaseType.FIX)
        )
        registry.register('fail2ban', PhaseType.FIX, mock_provider)

        db = AsyncMock()
        db.record_phase_execution = AsyncMock(return_value=1)
        db.record_fix_attempt = AsyncMock(return_value=1)
        executor = PhaseTypeExecutor(registry=registry, db=db)

        event = BanEvent(
            source='fail2ban', severity=Severity.HIGH,
            details={'ip': '1.2.3.4'}, event_id='test'
        )

        # Phase 1: Fix
        await executor.execute_phase(
            {'name': 'Fix', 'type': 'fix', 'description': 'Fix', 'steps': []},
            [event], batch_id='b1'
        )
        # Phase 2: Gleicher Event → sollte übersprungen werden
        await executor.execute_phase(
            {'name': 'Fix2', 'type': 'fix', 'description': 'Fix2', 'steps': []},
            [event], batch_id='b1'
        )
        # Provider sollte nur 1x aufgerufen worden sein
        assert mock_provider.execute.call_count == 1

    @pytest.mark.asyncio
    async def test_verify_phase_does_not_dedup(self):
        """Verify-Phase überspringt NICHT — sie muss immer laufen"""
        registry = FixerRegistry()
        db = AsyncMock()
        db.record_phase_execution = AsyncMock(return_value=1)
        executor = PhaseTypeExecutor(registry=registry, db=db)

        event = BanEvent(
            source='fail2ban', severity=Severity.HIGH,
            details={'ip': '1.2.3.4'}, event_id='test'
        )
        # Markiere als bereits gefixt
        executor._fixed_events.add('test')

        result = await executor.execute_phase(
            {'name': 'Verify', 'type': 'verify', 'description': 'Prüfen', 'steps': []},
            [event], batch_id='b1'
        )
        assert result is True  # Verify läuft trotzdem
```

**Step 2: Run test → FAIL**

**Step 3: Implement PhaseTypeExecutor**

```python
# src/integrations/security_engine/executor.py
"""
PhaseTypeExecutor — Semantische Phase-Ausführung

Steuert die Execution basierend auf Phase-Typ:
- recon/verify/monitor: Read-only, kein Fix
- contain: Sofort-Block, kein KI-Call
- fix: Provider-Chain durchlaufen (NoOp → Fixer)

Dedup: Events werden pro Batch nur 1x gefixt (außer verify/monitor).
"""

from __future__ import annotations
import logging
import time
from typing import Any, Dict, List, Optional, Set

from .models import SecurityEvent, PhaseType, FixResult
from .registry import FixerRegistry

logger = logging.getLogger('shadowops.phase_executor')


class PhaseTypeExecutor:

    def __init__(self, registry: FixerRegistry, db=None, backup_manager=None):
        self.registry = registry
        self.db = db
        self.backup_manager = backup_manager
        self._fixed_events: Set[str] = set()  # Event-IDs die bereits gefixt wurden

    def reset_batch(self):
        """Vor neuem Batch aufrufen"""
        self._fixed_events.clear()

    async def execute_phase(
        self,
        phase: Dict[str, Any],
        events: List[SecurityEvent],
        batch_id: str = '',
        discord_callback=None,
    ) -> bool:
        phase_type_str = phase.get('type', 'fix')
        try:
            phase_type = PhaseType(phase_type_str)
        except ValueError:
            logger.warning(f"Unbekannter Phase-Typ '{phase_type_str}', Fallback auf 'fix'")
            phase_type = PhaseType.FIX

        phase_name = phase.get('name', 'Unnamed')
        start = time.time()
        events_processed = 0
        all_success = True

        logger.info(f"⚙️ Phase '{phase_name}' ({phase_type.value}) mit {len(events)} Events...")

        if phase_type.is_read_only:
            # Read-only Phasen: Logging, kein Fix
            logger.info(f"   📖 Read-only Phase ({phase_type.value}) — kein Fix")
            events_processed = len(events)
        else:
            # Fix/Contain Phasen: Provider-Chain durchlaufen
            for event in events:
                # Dedup: bereits gefixt? (außer verify/monitor)
                if event.event_id in self._fixed_events and phase_type == PhaseType.FIX:
                    logger.info(f"   ⏭️ Event {event.event_id} bereits gefixt — übersprungen")
                    if self.db:
                        await self.db.record_fix_attempt(
                            event_source=event.source,
                            event_type=event.event_type,
                            event_signature=event.signature,
                            phase_type=phase_type.value,
                            approach='dedup_skip',
                            commands=[],
                            result='skipped_duplicate',
                            batch_id=batch_id,
                        )
                    continue

                result = await self._execute_provider_chain(event, phase, phase_type)
                events_processed += 1

                if result and result.is_success:
                    if result.status != 'no_op':
                        self._fixed_events.add(event.event_id)
                    if self.db:
                        await self.db.record_fix_attempt(
                            event_source=event.source,
                            event_type=event.event_type,
                            event_signature=event.signature,
                            phase_type=phase_type.value,
                            approach=result.message,
                            commands=[],
                            result=result.status,
                            duration_ms=int(result.duration_seconds * 1000),
                            batch_id=batch_id,
                        )
                elif result:
                    all_success = False
                    if self.db:
                        await self.db.record_fix_attempt(
                            event_source=event.source,
                            event_type=event.event_type,
                            event_signature=event.signature,
                            phase_type=phase_type.value,
                            approach='provider_chain',
                            commands=[],
                            result='failed',
                            error_message=result.error,
                            duration_ms=int(result.duration_seconds * 1000),
                            batch_id=batch_id,
                        )

        duration_ms = int((time.time() - start) * 1000)

        if self.db:
            await self.db.record_phase_execution(
                batch_id=batch_id,
                phase_type=phase_type.value,
                phase_name=phase_name,
                events_processed=events_processed,
                result='success' if all_success else 'failed',
                duration_ms=duration_ms,
            )

        status = "✅" if all_success else "⚠️"
        logger.info(f"   {status} Phase '{phase_name}' abgeschlossen ({duration_ms}ms)")
        return all_success

    async def _execute_provider_chain(
        self,
        event: SecurityEvent,
        phase: Dict,
        phase_type: PhaseType,
    ) -> Optional[FixResult]:
        """Durchläuft Provider-Chain bis einer ein Ergebnis liefert"""
        providers = self.registry.get_providers(event.source, phase_type)

        if not providers:
            logger.warning(f"   Kein Provider für {event.source}/{phase_type.value}")
            return FixResult.failed(
                f"Kein Provider registriert für {event.source}/{phase_type.value}",
                phase_type=phase_type,
            )

        strategy = {
            'description': phase.get('description', ''),
            'steps': phase.get('steps', []),
            'phase_name': phase.get('name', ''),
        }
        context = {'phase_type': phase_type}

        for provider in providers:
            try:
                result = await provider.execute(event, strategy, context)
                if result is not None:
                    return result
            except Exception as e:
                logger.error(f"   Provider {type(provider).__name__} Fehler: {e}")
                continue

        return FixResult.failed("Alle Provider haben None zurückgegeben", phase_type=phase_type)
```

**Step 4: Run test → PASS**

**Step 5: Commit**

```bash
git add src/integrations/security_engine/executor.py tests/unit/test_phase_executor.py
git commit -m "feat(security-engine): PhaseTypeExecutor mit Dedup + Provider-Chain"
```

---

### Task 2.2: ReactiveMode (Fast-Path + Batch)

**Files:**
- Create: `src/integrations/security_engine/reactive.py`
- Test: `tests/unit/test_reactive_mode.py`

**Step 1: Write the test**

```python
# tests/unit/test_reactive_mode.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.integrations.security_engine.reactive import ReactiveMode
from src.integrations.security_engine.models import (
    BanEvent, Severity, FixResult, PhaseType
)


class TestReactiveMode:
    @pytest.mark.asyncio
    async def test_fast_path_single_non_critical_event(self):
        """1 nicht-kritisches Event → Fast-Path, kein KI-Plan"""
        db = AsyncMock()
        db.claim_event = AsyncMock(return_value=True)
        db.release_event = AsyncMock()
        db.record_fix_attempt = AsyncMock(return_value=1)

        executor = AsyncMock()
        executor.execute_phase = AsyncMock(return_value=True)
        executor.reset_batch = MagicMock()

        mode = ReactiveMode(db=db, executor=executor, ai_service=None)

        event = BanEvent(
            source='fail2ban', severity=Severity.HIGH,
            details={'ip': '1.2.3.4'}, event_id='f2b_001'
        )
        result = await mode.handle_events([event])
        assert result is True
        # Kein KI-Call (ai_service nicht aufgerufen)

    @pytest.mark.asyncio
    async def test_batch_triggers_ki_plan(self):
        """3+ Events → KI-Plan erstellen"""
        db = AsyncMock()
        db.claim_event = AsyncMock(return_value=True)
        db.release_event = AsyncMock()

        executor = AsyncMock()
        executor.execute_phase = AsyncMock(return_value=True)
        executor.reset_batch = MagicMock()

        ai_service = AsyncMock()
        ai_service.generate_coordinated_plan = AsyncMock(return_value={
            'description': 'Test Plan',
            'confidence': 0.9,
            'estimated_duration_minutes': 10,
            'requires_restart': False,
            'phases': [
                {'name': 'Contain', 'type': 'contain', 'description': 'Block',
                 'steps': ['block ip'], 'estimated_minutes': 2},
                {'name': 'Verify', 'type': 'verify', 'description': 'Check',
                 'steps': ['check'], 'estimated_minutes': 2},
            ],
            'rollback_plan': 'Rollback'
        })

        mode = ReactiveMode(db=db, executor=executor, ai_service=ai_service)

        events = [
            BanEvent(source='fail2ban', severity=Severity.HIGH,
                     details={'ip': f'1.2.3.{i}'}, event_id=f'f2b_{i}')
            for i in range(3)
        ]
        result = await mode.handle_events(events)
        assert result is True
        ai_service.generate_coordinated_plan.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_already_claimed_event(self):
        """Event das bereits von einem anderen Mode bearbeitet wird → skip"""
        db = AsyncMock()
        db.claim_event = AsyncMock(return_value=False)  # Bereits claimed

        executor = AsyncMock()
        executor.reset_batch = MagicMock()
        mode = ReactiveMode(db=db, executor=executor, ai_service=None)

        event = BanEvent(
            source='fail2ban', severity=Severity.HIGH,
            details={'ip': '1.2.3.4'}, event_id='f2b_001'
        )
        result = await mode.handle_events([event])
        assert result is True  # Kein Fehler, nur skip
```

**Step 2: Run test → FAIL**

**Step 3: Implement ReactiveMode**

```python
# src/integrations/security_engine/reactive.py
"""
ReactiveMode — Sofortige Reaktion auf Security Events

Entscheidungslogik:
1. Event claimen (Cross-Mode Lock)
2. Bekanntes Pattern + CRITICAL? → CONTAIN sofort (kein KI)
3. 1-2 Events, nicht CRITICAL? → Fast-Path (direkt Fixer)
4. 3+ Events oder CRITICAL? → KI-Plan mit typed Phases
"""

from __future__ import annotations
import logging
from typing import List, Optional

from .models import SecurityEvent, PhaseType, FixResult, Severity

logger = logging.getLogger('shadowops.reactive')

# Schwelle: ab wie vielen Events wird ein KI-Plan erstellt?
BATCH_PLAN_THRESHOLD = 3


class ReactiveMode:

    def __init__(self, db, executor, ai_service=None, approval_manager=None):
        self.db = db
        self.executor = executor
        self.ai_service = ai_service
        self.approval_manager = approval_manager

    async def handle_events(self, events: List[SecurityEvent]) -> bool:
        """
        Verarbeitet eine Liste von Events.
        Returns True wenn alle erfolgreich (oder übersprungen).
        """
        self.executor.reset_batch()

        # 1. Events claimen
        claimable = []
        for event in events:
            claimed = await self.db.claim_event(event.event_id, 'reactive')
            if claimed:
                claimable.append(event)
            else:
                logger.info(f"⏭️ Event {event.event_id} bereits claimed — übersprungen")

        if not claimable:
            return True

        try:
            # 2. Entscheidung: Fast-Path oder KI-Plan?
            if len(claimable) < BATCH_PLAN_THRESHOLD and not self._has_critical(claimable):
                return await self._fast_path(claimable)
            else:
                return await self._planned_path(claimable)
        finally:
            # Events freigeben
            for event in claimable:
                await self.db.release_event(event.event_id, 'completed')

    async def _fast_path(self, events: List[SecurityEvent]) -> bool:
        """Direct fix ohne KI-Plan — für 1-2 einfache Events"""
        logger.info(f"⚡ Fast-Path: {len(events)} Event(s) direkt fixen")
        all_success = True

        for event in events:
            # Für jeden Event eine Mini-Phase: contain (wenn Ban/Threat) oder fix
            phase_type = 'contain' if not event.is_persistent else 'fix'
            phase = {
                'name': f'Fast-Fix {event.source}',
                'type': phase_type,
                'description': f'Direkte Behandlung: {event.source} {event.event_type}',
                'steps': [],
            }
            success = await self.executor.execute_phase(
                phase, [event], batch_id=f'fast_{event.event_id}'
            )
            if not success:
                all_success = False

        return all_success

    async def _planned_path(self, events: List[SecurityEvent]) -> bool:
        """KI-gestützter Plan mit typed Phases"""
        logger.info(f"🧠 Planned-Path: {len(events)} Events → KI-Plan")

        if not self.ai_service:
            logger.warning("Kein AI-Service — Fallback auf Fast-Path")
            return await self._fast_path(events)

        # KI-Plan generieren
        plan = await self.ai_service.generate_coordinated_plan(
            self._build_plan_prompt(events),
            context={'events': [e.to_dict() for e in events]},
        )

        if not plan or not plan.get('phases'):
            logger.error("KI konnte keinen Plan erstellen — Fallback auf Fast-Path")
            return await self._fast_path(events)

        # Phasen ausführen
        batch_id = f"batch_{events[0].event_id}" if events else "batch_unknown"
        all_success = True

        for phase in plan['phases']:
            success = await self.executor.execute_phase(
                phase, events, batch_id=batch_id
            )
            if not success:
                all_success = False
                # Bei Fix-Failure: Rollback-Log, aber weitermachen
                logger.warning(f"Phase '{phase.get('name')}' fehlgeschlagen — weiter mit nächster")

        return all_success

    def _has_critical(self, events: List[SecurityEvent]) -> bool:
        return any(e.severity == Severity.CRITICAL for e in events)

    def _build_plan_prompt(self, events: List[SecurityEvent]) -> str:
        event_summary = "\n".join([
            f"- {e.source.upper()} ({e.severity.value}): {e.event_type} — {e.details}"
            for e in events
        ])
        return f"""Erstelle einen koordinierten Remediation-Plan.

## Events
{event_summary}

## Regeln
- Maximal 3-4 Phasen
- Jede Phase MUSS einen type haben: recon, contain, fix, verify, monitor
- recon/verify/monitor sind read-only (keine Änderungen)
- contain ist für sofortige Eindämmung (IP blocken)
- fix ist für dauerhafte Behebung (Config ändern)
- NIEMALS gleichen Fix in mehreren Phasen wiederholen
- Bei SSH-Bans: contain zuerst (IP blocken), dann fix (Jail härten), dann verify
"""
```

**Step 4: Run test → PASS**

**Step 5: Commit**

```bash
git add src/integrations/security_engine/reactive.py tests/unit/test_reactive_mode.py
git commit -m "feat(security-engine): ReactiveMode mit Fast-Path + KI-Plan"
```

---

### Task 2.3: Fixer-Adapter (bestehende Fixer → FixProvider)

**Files:**
- Create: `src/integrations/security_engine/fixer_adapters.py`
- Test: `tests/unit/test_fixer_adapters.py`

**Beschreibung:** Adapter-Klassen die bestehende Fixer (Fail2banFixer, TrivyFixer, etc.) als FixProvider wrappen. Die Fixer selbst bleiben unverändert — nur ihre Schnittstelle wird adaptiert.

**Step 1: Write the test**

```python
# tests/unit/test_fixer_adapters.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.integrations.security_engine.fixer_adapters import (
    Fail2banFixerAdapter, TrivyFixerAdapter, CrowdSecFixerAdapter, AideFixerAdapter
)
from src.integrations.security_engine.models import BanEvent, Severity, PhaseType


class TestFail2banAdapter:
    @pytest.mark.asyncio
    async def test_no_op_when_config_unchanged(self):
        """Adapter erkennt No-Op bevor Fixer aufgerufen wird"""
        mock_fixer = AsyncMock()
        mock_fixer._get_jail_config = AsyncMock(return_value={
            'maxretry': 3, 'bantime': 3600
        })
        mock_fixer.hardened_config = {'maxretry': 3, 'bantime': 3600, 'findtime': 600}

        adapter = Fail2banFixerAdapter(mock_fixer)
        event = BanEvent(
            source='fail2ban', severity=Severity.HIGH,
            details={'jail': 'sshd'}, event_id='test'
        )
        result = await adapter.execute(
            event,
            strategy={'description': 'harden_config'},
            context={'phase_type': PhaseType.FIX}
        )
        assert result.status == 'no_op'
        mock_fixer.fix.assert_not_called()  # Fixer wurde NICHT aufgerufen

    @pytest.mark.asyncio
    async def test_delegates_when_change_needed(self):
        """Adapter delegiert an Fixer wenn Änderung nötig"""
        mock_fixer = AsyncMock()
        mock_fixer._get_jail_config = AsyncMock(return_value={
            'maxretry': 5, 'bantime': 600
        })
        mock_fixer.hardened_config = {'maxretry': 3, 'bantime': 3600, 'findtime': 600}
        mock_fixer.fix = AsyncMock(return_value={
            'status': 'success', 'message': 'Jail hardened'
        })

        adapter = Fail2banFixerAdapter(mock_fixer)
        event = BanEvent(
            source='fail2ban', severity=Severity.HIGH,
            details={'jail': 'sshd'}, event_id='test'
        )
        result = await adapter.execute(
            event,
            strategy={'description': 'harden_config'},
            context={'phase_type': PhaseType.FIX}
        )
        assert result.status == 'success'
        mock_fixer.fix.assert_called_once()
```

**Step 2: Run test → FAIL**

**Step 3: Implement Adapters**

```python
# src/integrations/security_engine/fixer_adapters.py
"""
Adapter: Bestehende Fixer → FixProvider Interface

Wrappen die existierenden Fixer-Klassen (TrivyFixer, Fail2banFixer, etc.)
als FixProvider, inkl. No-Op-Detection wo möglich.
"""

from __future__ import annotations
import logging
import time
from typing import Any, Dict, Optional

from .models import SecurityEvent, PhaseType, FixResult
from .providers import FixProvider

logger = logging.getLogger('shadowops.fixer_adapters')


class Fail2banFixerAdapter(FixProvider):
    """Adapter für bestehenden Fail2banFixer mit No-Op-Detection"""

    def __init__(self, fixer):
        self.fixer = fixer

    async def execute(
        self,
        event: SecurityEvent,
        strategy: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> Optional[FixResult]:
        phase_type = context.get('phase_type', PhaseType.FIX) if context else PhaseType.FIX

        # No-Op Check: Aktuelle Config vs. Ziel-Config vergleichen
        try:
            jail_name = event.details.get('jail', 'sshd')
            current_config = await self.fixer._get_jail_config(jail_name)
            if current_config:
                target = self.fixer.hardened_config
                if (current_config.get('maxretry') == target.get('maxretry')
                        and current_config.get('bantime') == target.get('bantime')):
                    return FixResult.no_op(
                        f"Jail {jail_name} bereits gehärtet "
                        f"(maxretry={target['maxretry']}, bantime={target['bantime']})",
                        phase_type=phase_type,
                    )
        except Exception as e:
            logger.debug(f"No-Op-Check fehlgeschlagen: {e}")

        # Delegiere an Original-Fixer
        start = time.time()
        result = await self.fixer.fix(event.to_dict(), strategy)
        duration = time.time() - start

        if result.get('status') == 'success':
            return FixResult.success(
                result.get('message', 'Fail2ban fix applied'),
                phase_type=phase_type,
                duration_seconds=duration,
            )
        return FixResult.failed(
            result.get('error', 'Fail2ban fix failed'),
            phase_type=phase_type,
            duration_seconds=duration,
        )


class TrivyFixerAdapter(FixProvider):
    """Adapter für TrivyFixer"""

    def __init__(self, fixer):
        self.fixer = fixer

    async def execute(
        self,
        event: SecurityEvent,
        strategy: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> Optional[FixResult]:
        phase_type = context.get('phase_type', PhaseType.FIX) if context else PhaseType.FIX
        start = time.time()
        result = await self.fixer.fix(event.to_dict(), strategy)
        duration = time.time() - start

        if result.get('status') == 'success':
            return FixResult.success(
                result.get('message', 'Trivy fix applied'),
                phase_type=phase_type, duration_seconds=duration,
            )
        return FixResult.failed(
            result.get('error', 'Trivy fix failed'),
            phase_type=phase_type, duration_seconds=duration,
        )


class CrowdSecFixerAdapter(FixProvider):
    """Adapter für CrowdSecFixer"""

    def __init__(self, fixer):
        self.fixer = fixer

    async def execute(
        self,
        event: SecurityEvent,
        strategy: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> Optional[FixResult]:
        phase_type = context.get('phase_type', PhaseType.FIX) if context else PhaseType.FIX
        start = time.time()
        result = await self.fixer.fix(event.to_dict(), strategy)
        duration = time.time() - start

        if result.get('status') == 'success':
            return FixResult.success(
                result.get('message', 'CrowdSec fix applied'),
                phase_type=phase_type, duration_seconds=duration,
            )
        return FixResult.failed(
            result.get('error', 'CrowdSec fix failed'),
            phase_type=phase_type, duration_seconds=duration,
        )


class AideFixerAdapter(FixProvider):
    """Adapter für AideFixer"""

    def __init__(self, fixer):
        self.fixer = fixer

    async def execute(
        self,
        event: SecurityEvent,
        strategy: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> Optional[FixResult]:
        phase_type = context.get('phase_type', PhaseType.FIX) if context else PhaseType.FIX
        start = time.time()
        result = await self.fixer.fix(event.to_dict(), strategy)
        duration = time.time() - start

        if result.get('status') == 'success':
            return FixResult.success(
                result.get('message', 'AIDE fix applied'),
                phase_type=phase_type, duration_seconds=duration,
            )
        return FixResult.failed(
            result.get('error', 'AIDE fix failed'),
            phase_type=phase_type, duration_seconds=duration,
        )
```

**Step 4: Run test → PASS**

**Step 5: Commit**

```bash
git add src/integrations/security_engine/fixer_adapters.py tests/unit/test_fixer_adapters.py
git commit -m "feat(security-engine): Fixer-Adapter mit No-Op-Detection (Fail2ban)"
```

---

### Task 2.4: Planner Prompt-Update

**Files:**
- Modify: `src/integrations/orchestrator/planner_mixin.py` (Prompt-Template)

**Step 1: Prompt-Guidance für Phase-Types und Limit hinzufügen**

Finde die `_build_coordinated_planning_prompt()` Methode und ergänze am Ende des Prompts vor dem JSON-Template:

```
## WICHTIGE REGELN FÜR DEN PLAN
- Maximal 3-4 Phasen (weniger ist besser)
- Jede Phase MUSS ein "type" Feld haben: "recon", "contain", "fix", "verify" oder "monitor"
- recon: Read-only Beweissicherung (Status erfassen, Logs sammeln)
- contain: Sofortige Eindämmung (IP blocken, Service isolieren)
- fix: Dauerhafte Behebung (Config ändern, Packages updaten)
- verify: Prüfen ob Fix wirkt (Status checken, Tests laufen)
- monitor: Nachbeobachtung (nur Logging, keine Aktion)
- NIEMALS den gleichen Fix in mehreren Phasen wiederholen!
- Wenn nur 1-2 Events: contain + verify reicht meistens
```

**Step 2: Commit**

```bash
git add src/integrations/orchestrator/planner_mixin.py
git commit -m "feat(security-engine): Phase-Type Guidance im Planner-Prompt"
```

---

## Phase 3: Deep Scan Mode (Analyst-Integration)

### Task 3.1: DeepScanMode

**Files:**
- Create: `src/integrations/security_engine/deep_scan.py`
- Test: `tests/unit/test_deep_scan_mode.py`

**Beschreibung:** Migriert die Kern-Logik aus `security_analyst.py` in den DeepScanMode. Nutzt die gleichen AI-Sessions, Learning-Pipeline und Adaptive Steuerung — aber über SecurityDB statt separater AnalystDB.

Der DeepScanMode enthält:
- Adaptive Session-Steuerung (fix_only/full_scan/quick_scan/maintenance)
- Pre-Session Maintenance (Git-Sync, Fix-Verifikation, Knowledge-Decay)
- Scan-Phase (AI-Session mit findings)
- Fix-Phase (Findings abarbeiten via PhaseTypeExecutor)
- Post-Session: Discord-Notification, Stats

**Implementation:** Zu groß für inline im Plan. Die Methoden `_determine_session_mode()`, `_pre_session_maintenance()`, `_run_scan_phase()`, `_run_fix_phase()` werden 1:1 aus `security_analyst.py` übernommen, aber auf SecurityDB umgestellt (async statt sync, `fix_attempts_v2` statt `orchestrator_fixes` + `fix_attempts`).

**Kritische Änderungen vs. aktueller Analyst:**
- `AnalystDB` → `SecurityDB` (gleiche Tabellen, nur neuer Access-Layer)
- `_apply_fix()` → `PhaseTypeExecutor.execute_phase()` (typed, mit NoOp+Dedup)
- Fix-Verifikation: Liest jetzt aus `fix_attempts_v2` (unified, sieht auch Orchestrator-Fixes)

**Step 1: Test schreiben** (Session-Modus-Logik)

```python
# tests/unit/test_deep_scan_mode.py
import pytest
from unittest.mock import AsyncMock
from src.integrations.security_engine.deep_scan import DeepScanMode


class TestSessionModeDetermination:
    @pytest.mark.asyncio
    async def test_fix_only_when_many_findings(self):
        db = AsyncMock()
        db.get_open_findings_count = AsyncMock(return_value=25)
        mode = DeepScanMode(db=db, ai_engine=None, executor=None)
        session_mode = await mode._determine_session_mode()
        assert session_mode == 'fix_only'

    @pytest.mark.asyncio
    async def test_full_scan_when_moderate_findings(self):
        db = AsyncMock()
        db.get_open_findings_count = AsyncMock(return_value=10)
        mode = DeepScanMode(db=db, ai_engine=None, executor=None)
        session_mode = await mode._determine_session_mode()
        assert session_mode == 'full_scan'

    @pytest.mark.asyncio
    async def test_quick_scan_when_few_findings(self):
        db = AsyncMock()
        db.get_open_findings_count = AsyncMock(return_value=2)
        mode = DeepScanMode(db=db, ai_engine=None, executor=None)
        session_mode = await mode._determine_session_mode()
        assert session_mode == 'quick_scan'

    @pytest.mark.asyncio
    async def test_maintenance_when_no_findings(self):
        db = AsyncMock()
        db.get_open_findings_count = AsyncMock(return_value=0)
        mode = DeepScanMode(db=db, ai_engine=None, executor=None)
        session_mode = await mode._determine_session_mode()
        assert session_mode == 'maintenance'
```

**Steps 2-5:** Implementierung, Test, Commit — wie bei den vorherigen Tasks.

---

### Task 3.2: SecurityEngine Hauptklasse (3 Modi vereinen)

**Files:**
- Create: `src/integrations/security_engine/engine.py`
- Test: `tests/unit/test_security_engine.py`

**Beschreibung:** Die zentrale Klasse die alle 3 Modi orchestriert. Wird in `bot.py` statt der separaten Komponenten (EventWatcher, Orchestrator, SelfHealing, Analyst) initialisiert.

```python
# src/integrations/security_engine/engine.py (Kern-Struktur)
class SecurityEngine:
    """Ein Hirn, drei Modi, eine Datenbank"""

    def __init__(self, bot, config, ai_service, context_manager):
        self.bot = bot
        self.config = config
        self.ai_service = ai_service
        self.context_manager = context_manager

        # Unified DB
        self.db = SecurityDB(dsn=config.get_security_db_dsn())

        # Fixer-Registry
        self.registry = FixerRegistry()
        self.registry.register_noop(NoOpProvider())

        # Phase Executor
        self.executor = PhaseTypeExecutor(registry=self.registry, db=self.db)

        # 3 Modi
        self.reactive = ReactiveMode(db=self.db, executor=self.executor, ai_service=ai_service)
        self.proactive = ProactiveMode(db=self.db, executor=self.executor)
        self.deep_scan = DeepScanMode(db=self.db, ai_engine=ai_service, executor=self.executor)

        # Circuit Breaker
        self.circuit_breaker = CircuitBreaker(failure_threshold=5, cooldown_seconds=3600)

    async def initialize(self):
        """Async-Init: DB, Fixer registrieren, Event-Loop starten"""
        await self.db.initialize()
        self._register_fixers()

    def _register_fixers(self):
        """Alle Fixer als FixProvider registrieren"""
        # ... Fail2banFixerAdapter, TrivyFixerAdapter, etc.

    # ── Hooks (wie Agent Framework) ──

    async def on_fix_failed(self, event, error):
        """Override-fähig: Was passiert wenn ein Fix fehlschlägt?"""
        logger.error(f"Fix fehlgeschlagen für {event.event_id}: {error}")

    async def on_regression_detected(self, finding, verification):
        """Override-fähig: Was passiert bei Regression?"""
        logger.warning(f"Regression erkannt: Finding {finding['id']} wieder offen")

    # ── Event-Handler (wird von EventWatcher aufgerufen) ──

    async def handle_security_event(self, event: SecurityEvent):
        """Haupteinstieg für alle Security-Events"""
        if not self.circuit_breaker.can_attempt:
            logger.warning("Circuit Breaker offen — Event übersprungen")
            return

        try:
            await self.reactive.handle_events([event])
            self.circuit_breaker.record_success(event.source)
        except Exception as e:
            self.circuit_breaker.record_failure(event.source)
            await self.on_fix_failed(event, str(e))
```

**Steps:** Test schreiben, implementieren, commit.

---

### Task 3.3: bot.py Integration

**Files:**
- Modify: `src/bot.py` (SecurityEngine statt separate Komponenten)

**Beschreibung:** Ersetze in `on_ready()`:
```python
# ALT:
self.self_healing = SelfHealingCoordinator(...)
self.orchestrator = RemediationOrchestrator(...)
self.security_analyst = SecurityAnalyst(...)
self.event_watcher = SecurityEventWatcher(...)

# NEU:
self.security_engine = SecurityEngine(
    bot=self, config=self.config,
    ai_service=self.ai_service,
    context_manager=self.context_manager,
)
await self.security_engine.initialize()
```

**WICHTIG:** Backward-Compat für Cogs — Cogs die `self.bot.self_healing` oder `self.bot.orchestrator` referenzieren müssen auf `self.bot.security_engine` umgestellt werden.

---

## Phase 4: Proactive Mode + Polish

### Task 4.1: ProactiveMode

**Files:**
- Create: `src/integrations/security_engine/proactive.py`
- Test: `tests/unit/test_proactive_mode.py`

**Beschreibung:** Regelmäßige Coverage-Checks und proaktive Härtung:
- Welche Bereiche wurden >7 Tage nicht gescannt?
- Gibt es Trends (steigende Ban-Rate, neue IPs)?
- Automatische Härtungsvorschläge basierend auf DB-Wissen

Nutzt `scan_coverage` und `knowledge` Tabellen aus der bestehenden DB.

---

### Task 4.2: agent_learning Integration

**Files:**
- Modify: `src/integrations/security_engine/db.py` (add agent_learning queries)

**Beschreibung:** Security-Fixes als `agent_feedback` in die agent_learning DB schreiben, damit alle Agents davon lernen. Gleiche Pattern wie Patch Notes:
- Erfolgreiche Fixes → positive Feedback
- Fehlgeschlagene Fixes → negative Feedback
- Automatische Quality-Scores

---

### Task 4.3: Cogs aktualisieren

**Files:**
- Modify: `src/cogs/monitoring.py` (SecurityEngine statt self_healing/orchestrator)
- Modify: `src/cogs/admin.py` (SecurityEngine statt self_healing/orchestrator)
- Modify: `src/cogs/inspector.py` (neue Stats-Methoden)

**Beschreibung:** Slash-Commands auf SecurityEngine umstellen:
- `/remediation-stats` → `security_engine.get_stats()`
- `/set-approval-mode` → `security_engine.set_approval_mode()`
- `/scan` → `security_engine.trigger_scan()`

---

### Task 4.4: Alte Module entfernen

**Files:**
- Delete: `src/integrations/knowledge_base.py` (→ security_engine/db.py)
- Delete: `src/integrations/self_healing.py` (→ security_engine/engine.py)
- Delete: `src/integrations/orchestrator/` (→ security_engine/reactive.py + executor.py)
- Delete: `src/integrations/analyst/security_analyst.py` (→ security_engine/deep_scan.py)
- Delete: `src/integrations/analyst/analyst_db.py` (→ security_engine/db.py)
- Keep: `src/integrations/analyst/prompts.py` (wird von deep_scan.py importiert)
- Keep: `src/integrations/analyst/activity_monitor.py` (wird von engine.py importiert)

**WICHTIG:** Erst löschen wenn alle Tests grün sind und der Bot erfolgreich startet.

---

### Task 4.5: CLAUDE.md + Doku aktualisieren

**Files:**
- Modify: `CLAUDE.md` (Neue Architektur dokumentieren)
- Modify: `.claude/rules/safety.md` (SecurityEngine-Referenzen)
- Create: `docs/security-engine-v6-overview.md`

---

### Task 4.6: Integration-Tests

**Files:**
- Create: `tests/integration/test_security_engine_integration.py`

**Beschreibung:** End-to-End Test mit gemockter DB:
1. Event erstellen
2. SecurityEngine.handle_security_event() aufrufen
3. Prüfen: Fix wurde aufgerufen, DB wurde geschrieben, Discord wurde benachrichtigt
4. Prüfen: No-Op bei doppeltem Event
5. Prüfen: Fast-Path bei 1 Event vs. KI-Plan bei 3+ Events

---

## Zusammenfassung

| Phase | Tasks | Kern-Dateien | Was es löst |
|-------|-------|-------------|-------------|
| **1: Foundation** | 1.1-1.5 | models, providers, registry, circuit_breaker, db, schema | Typen, DB-Layer, Provider-ABC |
| **2: Reactive** | 2.1-2.4 | executor, reactive, fixer_adapters, planner | Fast-Path, NoOp, Dedup, Phase-Types |
| **3: Deep Scan** | 3.1-3.3 | deep_scan, engine, bot.py | Analyst-Integration, ein Hirn |
| **4: Polish** | 4.1-4.6 | proactive, cogs, cleanup, doku | Coverage, Learning, Aufräumen |

**Reihenfolge:** Strikt Phase 1 → 2 → 3 → 4. Jede Phase kann einzeln deployed werden — der Bot funktioniert nach jeder Phase.
