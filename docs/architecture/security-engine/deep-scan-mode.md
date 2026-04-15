---
title: Security Engine v6 — Deep Scan Mode (SecurityScanAgent)
status: active
version: v6
last_reviewed: 2026-04-15
owner: CommanderShadow9
related:
  - ../../plans/2026-03-24-security-engine-v6.md
  - ../../plans/2026-03-24-security-scan-agent-design.md
---

# Security Engine v6 — Deep Scan Mode

> **Hinweis (2026-04-15):** Der urspruenglich geplante `DeepScanMode` (in diesem
> Plan beschrieben) wurde seit 2026-03-24 durch den `SecurityScanAgent` in
> `security_engine/scan_agent.py` ersetzt. Die Funktionalitaet ist erhalten —
> Adaptive Session-Steuerung, Learning Pipeline, Findings-Abarbeitung ueber den
> `PhaseTypeExecutor` — aber die Implementierung unterscheidet sich:
>
> - Autonomer Agent mit Activity Monitor (startet nur wenn User idle)
> - Deterministische Pre-Checks (UFW, Docker, Fail2ban, CrowdSec, Ports)
>   werden VOR der AI-Analyse gesammelt und als Fakten injiziert
> - Post-Scan Reflection nach JEDER Session (Quality Score, Trend, Insights)
> - Post-Fix Integrity Check (Container, Ports, Services intakt?)
> - Content-Deletion-Guard (warnt bei Netto-Loeschungen >20 Zeilen)
> - Weekly-Deep-Scan (Sonntag Nacht, nur Claude, Code Security Review)
> - PROJECT_SECURITY_PROFILES + PROTECTED_PORT_BINDINGS (projektspezifisch)
>
> Fuer den aktuellen Stand siehe CLAUDE.md (Abschnitt "Security Engine v6" und
> "SecurityScanAgent") sowie das ADR
> `docs/plans/2026-03-24-security-scan-agent-design.md`. Das Doc hier beschreibt
> die **urspruengliche Planung** — der Scope und die Rolle sind gleich, die
> Implementierungsdatei heisst jetzt `scan_agent.py` statt `deep_scan.py`.

Diese Datei dokumentiert Phase 3: die Ablaesung des alten `SecurityAnalyst` durch einen einheitlichen Deep-Scan-Mode in der Security Engine. Siehe Hinweis oben — in der Realitaet wurde diese Rolle durch den `SecurityScanAgent` uebernommen.

---

## Task 3.1: DeepScanMode

**Files (urspruenglich geplant):**
- Create: `src/integrations/security_engine/deep_scan.py`
- Test: `tests/unit/test_deep_scan_mode.py`

**Files (heute):**
- `src/integrations/security_engine/scan_agent.py`
- `src/integrations/security_engine/activity_monitor.py`
- `src/integrations/security_engine/prompts.py`
- Tests in `tests/unit/test_scan_agent*.py`

**Beschreibung:** Migriert die Kern-Logik aus `security_analyst.py` in den DeepScanMode. Nutzt die gleichen AI-Sessions, Learning-Pipeline und Adaptive Steuerung — aber ueber SecurityDB statt separater AnalystDB.

Der DeepScanMode/SecurityScanAgent enthaelt:
- Adaptive Session-Steuerung (fix_only/full_scan/quick_scan/maintenance)
- Pre-Session Maintenance (Git-Sync, Fix-Verifikation, Knowledge-Decay)
- Scan-Phase (AI-Session mit findings)
- Fix-Phase (Findings abarbeiten via PhaseTypeExecutor)
- Post-Session: Discord-Notification, Stats

**Implementation:** Zu gross fuer inline im Plan. Die Methoden `_determine_session_mode()`, `_pre_session_maintenance()`, `_run_scan_phase()`, `_run_fix_phase()` werden 1:1 aus `security_analyst.py` uebernommen, aber auf SecurityDB umgestellt (async statt sync, `fix_attempts_v2` statt `orchestrator_fixes` + `fix_attempts`).

**Kritische Aenderungen vs. aktueller Analyst:**
- `AnalystDB` -> `SecurityDB` (gleiche Tabellen, nur neuer Access-Layer)
- `_apply_fix()` -> `PhaseTypeExecutor.execute_phase()` (typed, mit NoOp+Dedup)
- Fix-Verifikation: Liest jetzt aus `fix_attempts_v2` (unified, sieht auch Orchestrator-Fixes)

### Test (Session-Modus-Logik)

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

## Task 3.2: SecurityEngine Hauptklasse (3 Modi vereinen)

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

    # Hooks (wie Agent Framework)

    async def on_fix_failed(self, event, error):
        """Override-faehig: Was passiert wenn ein Fix fehlschlaegt?"""
        logger.error(f"Fix fehlgeschlagen fuer {event.event_id}: {error}")

    async def on_regression_detected(self, finding, verification):
        """Override-faehig: Was passiert bei Regression?"""
        logger.warning(f"Regression erkannt: Finding {finding['id']} wieder offen")

    # Event-Handler (wird von EventWatcher aufgerufen)

    async def handle_security_event(self, event: SecurityEvent):
        """Haupteinstieg fuer alle Security-Events"""
        if not self.circuit_breaker.can_attempt:
            logger.warning("Circuit Breaker offen — Event uebersprungen")
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

## Task 3.3: bot.py Integration

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

**WICHTIG:** Backward-Compat fuer Cogs — Cogs die `self.bot.self_healing` oder `self.bot.orchestrator` referenzieren muessen auf `self.bot.security_engine` umgestellt werden.

---

## Event-Struktur und Planning-Regeln

Die Event-Strukturen (`BanEvent`, `VulnEvent`, `ThreatEvent`, `IntegrityEvent`) und die Planning-Regeln (maximal 3-4 Phasen, type MUSS gesetzt sein, recon/contain/fix/verify/monitor Semantik) sind gemeinsam mit dem Reactive-Mode — siehe [reactive-mode.md](reactive-mode.md) Abschnitt "Event-Struktur und Planning-Regeln".

Der Deep-Scan-Mode/SecurityScanAgent verwendet diese Event-Typen in der Fix-Phase, wenn Findings in Phasen uebersetzt und ueber den `PhaseTypeExecutor` ausgefuehrt werden.
