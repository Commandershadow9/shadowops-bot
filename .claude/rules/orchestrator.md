---
paths:
  - "src/integrations/orchestrator/**"
  - "src/integrations/auto_fix_manager.py"
  - "src/integrations/smart_queue.py"
  - "src/integrations/self_healing.py"
  - "src/integrations/verification.py"
  - "src/integrations/backup_manager.py"
  - "src/integrations/command_executor.py"
  - "src/integrations/impact_analyzer.py"
---
# Remediation Orchestrator

## Package-Struktur (`orchestrator/`)
| Modul | Zweck |
|-------|-------|
| `core.py` | RemediationOrchestrator Klasse (__init__ + Mixin-Komposition) |
| `models.py` | SecurityEventBatch, RemediationPlan Dataclasses |
| `batch_mixin.py` | Event-Batching, History-Persistenz, Adaptive Retry |
| `planner_mixin.py` | KI-Planerstellung, Prompt-Building, Streaming |
| `discord_mixin.py` | Status-Messages, Approval-Flow (Discord UI) |
| `executor_mixin.py` | Plan-Ausfuehrung, Multi-Projekt, Phase-Execution |
| `recovery_mixin.py` | Rollback, Verifikation, Summary, Status |

## SmartQueue
- **3 parallele Analyse-Slots** (asyncio.Semaphore fuer concurrent Security-Event Analyse)
- **1 serieller Fix-Lock** (asyncio.Lock — nur ein Fix gleichzeitig)
- **Circuit Breaker:** 5 Fehler in 3600s → 1h Pause (verhindert Endlos-Loops)
- **Batching:** 10s Fenster sammelt Events, max 10 pro Batch

## Orchestrator-Flow
1. Events aus SmartQueue empfangen
2. Batch-Collection (10s Fenster)
3. KI-Analyse: Koordinierter Prompt mit allen Events + Infrastructure-Kontext
4. Plan-Validierung: Confidence > 0%, mindestens 1 Phase
5. Fix-Ausfuehrung: Sequentiell mit Backup vor jedem Schritt

## Safety-Pipeline
| Schritt | Modul | Zweck |
|---------|-------|-------|
| 1 | `impact_analyzer.py` | Auswirkung auf Services pruefen |
| 2 | `backup_manager.py` | Backup erstellen |
| 3 | `command_executor.py` | Commands sicher ausfuehren |
| 4 | `verification.py` | Post-Fix Verifikation (4 Stufen) |

## Wichtig
- Fix-Commands werden NIEMALS mit `shell=True` ausgefuehrt
- Backups unter `/tmp/shadowops_backups/`
- Event-History in `logs/event_history.json`
