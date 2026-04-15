---
title: Security Engine v6 — Foundation (DB, Models, Providers)
status: active
version: v6
last_reviewed: 2026-04-15
owner: CommanderShadow9
related:
  - ../../adr/006-security-engine-v6.md
  - ../../plans/2026-03-24-security-engine-v6.md
---

# Security Engine v6 — Foundation (Phase 1)

Diese Datei dokumentiert Phase 1 der Security Engine v6: die Basis-Schichten (Models, Provider-ABC, Registry, CircuitBreaker, unified DB, Schema-Update).

Diese Schicht hat keine Abhaengigkeiten zu Discord oder Bot-Logik — sie ist fuer sich testbar und wird von allen drei Modi (Reactive, Proactive, Deep Scan) gemeinsam genutzt.

---

## Task 1.1: SecurityEvent + PhaseType Models

**Files:**
- Create: `src/integrations/security_engine/__init__.py`
- Create: `src/integrations/security_engine/models.py`
- Test: `tests/unit/test_security_models.py`

### Test

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

### Implementation

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
    CONTAIN = 'contain'   # Sofort-Block: IP bannen, Quarantaene
    FIX = 'fix'           # Config aendern, Haerten, Patchen
    VERIFY = 'verify'     # Pruefen ob Fix wirkt (Fail = Rollback)
    MONITOR = 'monitor'   # Nachbeobachtung, Alerting

    @property
    def is_read_only(self) -> bool:
        return self in (PhaseType.RECON, PhaseType.VERIFY, PhaseType.MONITOR)


class EngineMode(Enum):
    REACTIVE = 'reactive'       # Event -> sofort reagieren
    PROACTIVE = 'proactive'     # Geplant: Coverage, Trends, Haertung
    DEEP_SCAN = 'deep_scan'     # AI-Session mit Learning Pipeline


@dataclass
class SecurityEvent(ABC):
    """Basis fuer alle Security-Events"""
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

---

## Task 1.2: FixProvider ABC + NoOpProvider + Registry

**Files:**
- Create: `src/integrations/security_engine/providers.py`
- Create: `src/integrations/security_engine/registry.py`
- Test: `tests/unit/test_fix_providers.py`

### Test

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
        # NoOp prueft: target_config == current_config
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
        assert result is None  # None = "ich bin nicht zustaendig, naechster Provider"


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
        registry.register('fail2ban', None, mock_provider)  # Fuer alle PhaseTypes
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

### Implementation

```python
# src/integrations/security_engine/providers.py
"""Fix-Provider ABC und Basis-Implementierungen"""

from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from .models import SecurityEvent, PhaseType, FixResult


class FixProvider(ABC):
    """Basis-Klasse fuer alle Fix-Provider (wie AIProviderChain im Agent Framework)"""

    @abstractmethod
    async def execute(
        self,
        event: SecurityEvent,
        strategy: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> Optional[FixResult]:
        """
        Fuehrt Fix aus.

        Returns:
            FixResult bei Erfolg/Fehler, None wenn dieser Provider nicht zustaendig ist.
            None signalisiert: naechsten Provider in der Chain versuchen.
        """
        ...


class NoOpProvider(FixProvider):
    """Erkennt wenn ein Fix nicht noetig ist (Config bereits korrekt)"""

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

        return None  # Aenderung noetig -> naechster Provider


class BashFixProvider(FixProvider):
    """Fuehrt Fixes via CommandExecutor aus (sudo-Commands)"""

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
"""Fixer-Registry — Plugin-System fuer Fix-Provider"""

from __future__ import annotations
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from .models import PhaseType
from .providers import FixProvider, NoOpProvider


class FixerRegistry:
    """
    Registriert FixProvider pro (source, phase_type) Kombination.

    Lookup-Reihenfolge:
    1. NoOp-Provider (immer zuerst, prueft ob Fix noetig ist)
    2. Exakte Matches: (source, phase_type)
    3. Fallback: (source, None) — fuer alle PhaseTypes
    """

    def __init__(self):
        self._providers: Dict[Tuple[str, Optional[PhaseType]], List[FixProvider]] = defaultdict(list)
        self._noop: Optional[NoOpProvider] = None

    def register(self, source: str, phase_type: Optional[PhaseType], provider: FixProvider):
        """Registriert einen Provider fuer source + optional phase_type"""
        self._providers[(source, phase_type)].append(provider)

    def register_noop(self, provider: NoOpProvider):
        """Registriert den NoOp-Provider (global, wird immer zuerst geprueft)"""
        self._noop = provider

    def get_providers(self, source: str, phase_type: PhaseType) -> List[FixProvider]:
        """
        Gibt Provider-Chain fuer (source, phase_type) zurueck.

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

---

## Task 1.3: CircuitBreaker (nach Agent Framework Pattern)

**Files:**
- Create: `src/integrations/security_engine/circuit_breaker.py`
- Test: `tests/unit/test_circuit_breaker.py`

### Test

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

### Implementation

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

    Nach failure_threshold Fehlern fuer einen Key -> Sperre fuer cooldown_seconds.
    Ein Erfolg resettet den Counter fuer den Key.
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

---

## Task 1.4: SecurityDB (Unified asyncpg Layer)

**Files:**
- Create: `src/integrations/security_engine/db.py`
- Create: `scripts/migrate_security_db.py`
- Test: `tests/unit/test_security_db.py`

### Test

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

### Implementation SecurityDB

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
    """Unified Security Database — ein asyncpg-Pool fuer alles"""

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
        """Erstellt neue Tabellen (IF NOT EXISTS), laesst bestehende intakt"""
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

    # Fix-Attempts

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

    # Remediation Status (Cross-Mode Lock)

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

    # Phase Executions

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

    # Strategy Stats (existierende Tabelle nutzen)

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

    # Bestehende Analyst-Tabellen (Wrapper)
    # Diese Methoden wrappen die existierenden Tabellen (sessions, findings,
    # knowledge, etc.) und werden in Phase 3 (DeepScan) vollstaendig integriert.

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

### Migration Script

```python
# scripts/migrate_security_db.py
"""
Migration: orchestrator_fixes -> fix_attempts_v2

Migriert bestehende Daten und benennt alte Tabelle um.
Idempotent: Kann mehrfach ausgefuehrt werden.
"""
import psycopg2
import psycopg2.extras
import sys

DSN = "dbname=security_analyst user=security_analyst password=sec_analyst_2026 host=127.0.0.1 port=5433"


def migrate():
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # 1. Pruefe ob fix_attempts_v2 existiert
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

    # 2. Pruefe ob orchestrator_fixes existiert und Daten hat
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
    print(f"Migriere {count} Eintraege aus orchestrator_fixes -> fix_attempts_v2...")

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

    # 4. Alte Tabelle umbenennen (nicht loeschen!)
    cur.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'orchestrator_fixes')
            AND NOT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'orchestrator_fixes_deprecated')
            THEN
                ALTER TABLE orchestrator_fixes RENAME TO orchestrator_fixes_deprecated;
                RAISE NOTICE 'orchestrator_fixes -> orchestrator_fixes_deprecated';
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
                    RAISE NOTICE '{table} -> {table}_deprecated';
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

---

## Task 1.5: Schema-Update (coordinated_plan.json)

**Files:**
- Modify: `src/schemas/coordinated_plan.json`

### Update Schema

Fuege `type` Feld zu Phase-Items hinzu und setze `maxItems: 4`:

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
