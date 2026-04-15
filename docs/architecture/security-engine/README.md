---
title: Security Engine v6 — Uebersicht
status: active
version: v6
last_reviewed: 2026-04-15
owner: CommanderShadow9
related:
---

# Security Engine v6 — Uebersicht

**Goal:** Die 4 fragmentierten Security-Systeme (EventWatcher, Orchestrator, Self-Healing, Analyst) zu einer einheitlichen Security Engine zusammenfuehren — ein Hirn, drei Modi, eine Datenbank.

**Architecture:** Agent-Framework-Pattern (ABC + Hooks + Provider-Chain + Circuit-Breaker) angewendet auf Security. Drei Modi (Reactive, Proactive, DeepScan) teilen sich eine unified asyncpg-Datenbank und eine FixProvider-Registry. Phase-Type-System (recon/contain/fix/verify/monitor) steuert die Execution semantisch.

**Tech Stack:** Python 3.12, asyncpg, discord.py 2.7, Dual-Engine AI (Codex + Claude CLI)

---

## Navigation — Teildokumente

| Datei | Inhalt |
|-------|--------|
| [foundation.md](foundation.md) | Phase 1: DB-Layer (asyncpg), Models, Provider ABC, Fixer-Adapter-Schnittstelle, CircuitBreaker |
| [reactive-mode.md](reactive-mode.md) | Phase 2: Smart Execution, Fast-Path (1-2 Events direkt), Event-Claiming, Fixer-Adapter, Planner-Prompt |
| [deep-scan-mode.md](deep-scan-mode.md) | Phase 3: SecurityScanAgent (ersetzt urspruenglichen DeepScanMode), Adaptive Session-Steuerung |
| [proactive-mode.md](proactive-mode.md) | Phase 4: Proactive Mode, agent_learning Integration, Cogs, Cleanup, Polish |

---

## DB-Entscheidungen

### BEHALTEN (security_analyst DB, Port 5433)
Die `security_analyst` PostgreSQL-DB bleibt als **einzige Security-DB**. Beide Access-Layer (psycopg2 KnowledgeBase + asyncpg AnalystDB) werden zu **einem asyncpg-Layer** konsolidiert.

**Behalten + erweitern:**
| Tabelle | Status | Aenderung |
|---------|--------|----------|
| `sessions` | Behalten | +`mode` (reactive/proactive/deep_scan), +`phase_types_used` |
| `findings` | Behalten | Keine Aenderung |
| `knowledge` | Behalten | Keine Aenderung (Decay funktioniert gut) |
| `learned_patterns` | Behalten | Keine Aenderung |
| `health_snapshots` | Behalten | Keine Aenderung |
| `orchestrator_strategies` | Behalten | +`phase_type` Spalte |
| `orchestrator_plans` | Behalten | Phases-JSONB bekommt `type`-Feld pro Phase |
| `threat_patterns` | Behalten | Keine Aenderung |
| `orchestrator_vulnerabilities` | Behalten | Keine Aenderung |

**Zusammenfuehren:**
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
| `orchestrator_fixes` | migriert nach `fix_attempts` (unified) |
| `orchestrator_code_changes` | Niedriger Wert, Git-Log ist Quelle der Wahrheit |
| `orchestrator_log_patterns` | Niedriger Wert, kaum genutzt |

### NICHT ANFASSEN
| DB | Grund |
|----|-------|
| `agent_learning` (Patch Notes, Cross-Agent) | Kein Security-System, funktioniert gut |
| `changelogs.db` (SQLite) | Kein Security-System |

### Migration
Daten aus `orchestrator_fixes` nach `fix_attempts` migrieren (Spalten-Mapping). Alte Tabellen als `_deprecated` umbenennen (nicht loeschen).

---

## Dateistruktur (Neu)

```
src/integrations/security_engine/
|- __init__.py              # Exports: SecurityEngine, PhaseType, SecurityEvent
|- models.py                # SecurityEvent, PhaseType, FixResult, EngineMode
|- db.py                    # SecurityDB (unified asyncpg, ersetzt KnowledgeBase + AnalystDB)
|- engine.py                # SecurityEngine Hauptklasse (3 Modi, Hooks)
|- reactive.py              # ReactiveMode (Fast-Path + Batch + KI-Plan)
|- proactive.py             # ProactiveMode (Coverage, Trends, Haertung)
|- deep_scan.py             # DeepScanMode (AI-Sessions, Learning Pipeline)
|- executor.py              # PhaseTypeExecutor (recon/contain/fix/verify/monitor Handler)
|- registry.py              # FixerRegistry (Plugin-System fuer Fixer)
|- providers.py             # FixProvider ABC + NoOpProvider + BashFixProvider
`- circuit_breaker.py       # CircuitBreaker (aus Agent Framework Pattern)
```

**Bleibt bestehen (unveraendert):**
```
src/integrations/fixers/         # Trivy, CrowdSec, Fail2ban, AIDE Fixer (nur Interface-Adapter)
src/integrations/backup_manager.py
src/integrations/command_executor.py
src/integrations/approval_modes.py
src/integrations/impact_analyzer.py
src/integrations/context_manager.py
src/integrations/ai_engine.py
```

**Wird ersetzt (nach Migration loeschen):**
```
src/integrations/knowledge_base.py          -> security_engine/db.py
src/integrations/self_healing.py            -> security_engine/engine.py + executor.py
src/integrations/orchestrator/              -> security_engine/reactive.py + executor.py
src/integrations/analyst/security_analyst.py -> security_engine/deep_scan.py
src/integrations/analyst/analyst_db.py      -> security_engine/db.py
src/integrations/event_watcher.py           -> security_engine/engine.py (Event-Loop)
```

---

## Drei Modi — Konzept

### 1. Reactive Mode
**Trigger:** Event von EventWatcher (Fail2ban-Ban, Trivy-CVE, CrowdSec-Alert, AIDE-Integritaetsbruch).

**Entscheidungslogik:**
1. Event claimen (Cross-Mode Lock ueber `remediation_status`)
2. Bekanntes Pattern + CRITICAL? -> CONTAIN sofort (kein KI-Call)
3. 1-2 Events, nicht CRITICAL? -> Fast-Path (direkt Fixer)
4. 3+ Events oder CRITICAL? -> KI-Plan mit typed Phases

Details in [reactive-mode.md](reactive-mode.md).

### 2. Proactive Mode
**Trigger:** Zeitgesteuert (Coverage-Check, Trend-Analyse, Haertungsvorschlaege).

- Welche Bereiche wurden >7 Tage nicht gescannt?
- Gibt es Trends (steigende Ban-Rate, neue IPs)?
- Automatische Haertungsvorschlaege basierend auf DB-Wissen

Details in [proactive-mode.md](proactive-mode.md).

### 3. Deep Scan Mode (SecurityScanAgent)
**Trigger:** Adaptiv — taeglich AI-Session mit Learning Pipeline.

- Adaptive Session-Steuerung (fix_only/full_scan/quick_scan/maintenance)
- Pre-Session Maintenance (Git-Sync, Fix-Verifikation, Knowledge-Decay)
- Scan-Phase + Fix-Phase via PhaseTypeExecutor
- Post-Session: Discord-Notification, Stats

Details in [deep-scan-mode.md](deep-scan-mode.md).

---

## Phase-Type-System

| Phase-Typ | Semantik | Read-Only? |
|-----------|----------|-----------|
| `recon` | Beweissicherung, Ist-Zustand | Ja |
| `contain` | Sofort-Block: IP bannen, Quarantaene | Nein |
| `fix` | Config aendern, Haerten, Patchen | Nein |
| `verify` | Pruefen ob Fix wirkt (Fail = Rollback) | Ja |
| `monitor` | Nachbeobachtung, Alerting | Ja |

Der `PhaseTypeExecutor` steuert Execution anhand dieses Typs. Read-only-Phasen duerfen NICHTS veraendern — das schuetzt vor versehentlichen Seiteneffekten in Verify/Monitor-Phasen.

---

## Phasen-Uebersicht (Implementierungsplan)

| Phase | Tasks | Kern-Dateien | Was es loest |
|-------|-------|-------------|-------------|
| **1: Foundation** | 1.1-1.5 | models, providers, registry, circuit_breaker, db, schema | Typen, DB-Layer, Provider-ABC |
| **2: Reactive** | 2.1-2.4 | executor, reactive, fixer_adapters, planner | Fast-Path, NoOp, Dedup, Phase-Types |
| **3: Deep Scan** | 3.1-3.3 | deep_scan, engine, bot.py | Analyst-Integration, ein Hirn |
| **4: Polish** | 4.1-4.6 | proactive, cogs, cleanup, doku | Coverage, Learning, Aufraeumen |

**Reihenfolge:** Strikt Phase 1 -> 2 -> 3 -> 4. Jede Phase kann einzeln deployed werden — der Bot funktioniert nach jeder Phase.

---

## Status (Stand 2026-04-15)

Alle Phasen sind live. Der urspruenglich geplante DeepScanMode wurde durch den **SecurityScanAgent** (`security_engine/scan_agent.py`) ersetzt, der die gleiche Rolle uebernimmt aber anders aufgebaut ist (Activity Monitor, deterministische Pre-Checks, Post-Scan Reflection, Weekly Deep-Scan). Details siehe [deep-scan-mode.md](deep-scan-mode.md) und CLAUDE.md.
