---
paths:
  - "src/integrations/orchestrator.py"
  - "src/integrations/auto_fix_manager.py"
  - "src/integrations/smart_queue.py"
  - "src/integrations/self_healing.py"
  - "src/integrations/verification.py"
  - "src/integrations/backup_manager.py"
  - "src/integrations/command_executor.py"
  - "src/integrations/impact_analyzer.py"
---
# Remediation Orchestrator

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

## Auto-Fix Manager
- Discord Buttons: Approve / Reject / Details
- Persistent Views (ueberleben Bot-Restart)
- Timeout: 24h fuer Approval-Entscheidungen
- Rollback bei Fehler automatisch

## Safety-Pipeline
| Schritt | Modul | Zweck |
|---------|-------|-------|
| 1 | `impact_analyzer.py` | Auswirkung auf Services pruefen |
| 2 | `backup_manager.py` | Backup erstellen |
| 3 | `command_executor.py` | Commands sicher ausfuehren |
| 4 | `verification.py` | Post-Fix Verifikation (4 Stufen) |

## Wichtig
- `orchestrator.py` ist 112 KB — das groesste Modul. Aenderungen mit Bedacht!
- Fix-Commands werden NIEMALS mit `shell=True` ausgefuehrt
- Backups unter `/tmp/shadowops_backups/`
- Event-History in `logs/event_history.json`
