# Security Engine v6 — Architektur-Übersicht

## Zusammenfassung

Die Security Engine v6 vereint vier zuvor isolierte Security-Systeme (EventWatcher, Orchestrator, Self-Healing, Analyst) in einem einheitlichen System mit drei Modi, einer Datenbank und einem Phase-Type-System.

**Version:** v6.0.0 (2026-03-24)
**Architektur:** Agent-Framework-Pattern (ABC + Hooks + Provider-Chain + Circuit-Breaker)
**Datenbank:** PostgreSQL (asyncpg), security_analyst DB auf Port 5433

## Architektur

### Ein Hirn, Drei Modi

| Modus | Trigger | Zweck | Typische Dauer |
|-------|---------|-------|----------------|
| **Reactive** | Security Event erkannt | Sofortige Reaktion, Fast-Path oder KI-Plan | Sekunden bis Minuten |
| **Proactive** | Geplanter Scan/Timer | Coverage-Lücken finden, Trends erkennen | Minuten |
| **Deep Scan** | User idle + Session-Planner | Vollständige AI-Session mit Learning | 10-120 Minuten |

### Phase-Type-System

Jede Remediation-Phase hat einen semantischen Typ:

| Type | Zweck | Read-Only | Beispiel |
|------|-------|-----------|----------|
| `recon` | Beweissicherung | ja | Fail2ban-Status erfassen |
| `contain` | Sofort-Eindämmung | nein | IP permanent blocken |
| `fix` | Dauerhafte Behebung | nein | Jail-Config härten |
| `verify` | Fix-Verifikation | ja | Jail-Status prüfen |
| `monitor` | Nachbeobachtung | ja | Ban-Rate tracken |

### Provider-Chain (Fix-Execution)

Fixes werden über eine Provider-Chain ausgeführt (ähnlich AI Provider-Chain im Agent Framework):

```
Event -> FixerRegistry.get_providers(source, phase_type)
          |
    [1] NoOpProvider     -> Config bereits korrekt? -> no_op
    [2] FixerAdapter     -> Fixer aufrufen -> success/failed
    [3] Fallback-Provider -> Alternativer Ansatz
```

### Datenfluss

```
SecurityEvent (EventWatcher/External)
    |
SecurityEngine.handle_security_event()
    | (Circuit Breaker Check)
ReactiveMode.handle_events()
    +-- 1-2 Events, nicht CRITICAL -> Fast-Path (direkt Fixer)
    +-- 3+ Events oder CRITICAL -> KI-Plan mit typed Phases
        |
PhaseTypeExecutor.execute_phase()
    +-- recon/verify/monitor -> Read-only (Logging)
    +-- contain/fix -> Provider-Chain durchlaufen
    +-- Dedup: Event nur 1x pro Batch fixen
        |
SecurityDB.record_fix_attempt()
    |
LearningBridge.record_fix_feedback()  -> agent_learning DB
```

## Module

### Kern-Module

| Modul | Datei | Zweck |
|-------|-------|-------|
| **SecurityEngine** | `engine.py` | Hauptklasse, 3 Modi, Hooks, CircuitBreaker |
| **SecurityDB** | `db.py` | Unified asyncpg Layer (3 neue Tabellen) |
| **PhaseTypeExecutor** | `executor.py` | Semantische Phase-Ausführung mit Dedup |
| **FixerRegistry** | `registry.py` | Plugin-System für Fix-Provider |

### Modi

| Modul | Datei | Zweck |
|-------|-------|-------|
| **ReactiveMode** | `reactive.py` | Fast-Path + KI-Plan + Event-Claiming |
| **DeepScanMode** | `deep_scan.py` | Adaptive Sessions + Learning Pipeline |
| **ProactiveMode** | `proactive.py` | Coverage-Gaps + Trends + Härtung |

### Provider + Adapter

| Modul | Datei | Zweck |
|-------|-------|-------|
| **FixProvider ABC** | `providers.py` | Interface für alle Fix-Provider |
| **NoOpProvider** | `providers.py` | Erkennt unnötige Fixes |
| **Fixer-Adapter** | `fixer_adapters.py` | Wrappen bestehende Fixer (Fail2ban, Trivy, CrowdSec, AIDE) |

### Cross-System

| Modul | Datei | Zweck |
|-------|-------|-------|
| **CircuitBreaker** | `circuit_breaker.py` | Per-Key Failure-Tracking, Cooldown |
| **LearningBridge** | `learning_bridge.py` | Bidirektionale agent_learning Integration |

## Datenbank-Schema

### Neue Tabellen (security_analyst DB)

| Tabelle | Zweck | Erstellt von |
|---------|-------|-------------|
| `fix_attempts_v2` | Unified Fix-Tracking (ersetzt orchestrator_fixes + fix_attempts) | SecurityDB |
| `remediation_status` | Cross-Mode Lock (wer arbeitet gerade woran?) | SecurityDB |
| `phase_executions` | Phase-Execution-Tracking mit Typ + Dauer | SecurityDB |

### Bestehende Tabellen (unverändert)

| Tabelle | Zweck | Genutzt von |
|---------|-------|-------------|
| `sessions` | Analyst-Sessions | DeepScanMode |
| `findings` | Security-Findings | DeepScanMode |
| `knowledge` | Akkumuliertes Wissen + Decay | Alle Modi |
| `orchestrator_strategies` | Fix-Strategien + Success-Rates | SecurityDB |
| `orchestrator_plans` | Koordinierte Pläne | ReactiveMode |

### agent_learning DB (Cross-Agent)

| Tabelle | Zugriff | Zweck |
|---------|---------|-------|
| `agent_feedback` | Schreiben | Fix-Ergebnisse als Feedback |
| `agent_quality_scores` | Lesen + Schreiben | Quality-Trends |
| `agent_knowledge` | Lesen + Schreiben | Cross-Agent Wissensaustausch |

## Konfiguration

Die Security Engine wird über `config.yaml` konfiguriert:

```yaml
security_analyst:
  database_dsn: "postgresql://security_analyst:***@127.0.0.1:5433/security_analyst"

auto_remediation:
  enabled: true
  approval_mode: "paranoid"  # paranoid | auto | dry-run
  circuit_breaker_threshold: 5
  circuit_breaker_timeout: 3600
```

## Safety-Mechanismen

1. **Circuit Breaker**: 5 Failures -> 1h Pause (per Event-Source)
2. **Event-Claiming**: Cross-Mode Lock verhindert Doppel-Fixes
3. **NoOp-Detection**: Prüft Config bevor geschrieben wird
4. **Phase-Dedup**: Events nur 1x pro Batch gefixt
5. **Read-Only Phasen**: recon/verify/monitor ändern nichts
6. **Hooks**: on_fix_failed() und on_regression_detected() für Custom-Handling
7. **Graceful Degradation**: Fehlende DB/AI -> Fallback, kein Crash

## Tests

- **167+ Unit-Tests** über 11 Test-Dateien
- **Integration-Tests** für E2E Pipeline
- Alle Tests laufen in <3 Sekunden
