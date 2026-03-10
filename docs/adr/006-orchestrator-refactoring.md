# ADR-006: Orchestrator + GitHub Integration Refactoring

**Status:** Geplant
**Datum:** 2026-03-10

## Kontext

Zwei Dateien sind auf unkontrollierbare Größe gewachsen:
- `orchestrator.py` — 2533 Zeilen, 1 Klasse, 28 Methoden
- `github_integration.py` — 2691 Zeilen, 1 Klasse, 67 Methoden

Beide verletzen Single Responsibility und sind schwer testbar.

## Entscheidung

Beide Dateien werden in Packages mit klar getrennten Modulen aufgeteilt.

### orchestrator.py → orchestrator/

```
src/integrations/orchestrator/
├── __init__.py                  # Re-Export RemediationOrchestrator
├── models.py                    # SecurityEventBatch, RemediationPlan (~30 Z)
├── core.py                      # Orchestrator-Shell, submit_event, get_status (~200 Z)
├── batch_manager.py             # Event-Batching, History-Persistence (~250 Z)
├── planner.py                   # AI-Planerstellung, Prompt-Building (~400 Z)
├── discord_ui.py                # Approval-Flow, Status-Embeds (~350 Z)
├── executor.py                  # Plan-Ausfuehrung, Multi-/Single-Project (~900 Z)
└── recovery.py                  # Rollback, Verifikation, Image-Lookup (~200 Z)
```

### github_integration.py → github/

```
src/integrations/github/
├── __init__.py                  # Re-Export GitHubIntegration
├── core.py                      # Haupt-Klasse, Init, Config (~200 Z)
├── webhook_server.py            # HTTP-Server, Signature-Verify (~200 Z)
├── event_handlers.py            # Push/PR/Release/Workflow Handler (~500 Z)
├── ci_manager.py                # CI-Polling, Workflow-Updates (~150 Z)
├── git_operations.py            # Git-Befehle, Commit-Fetching (~200 Z)
├── state_tracking.py            # Commit-State, In-Flight, Duplikate (~150 Z)
├── notifications.py             # Alle Discord-Send-Methoden (~350 Z)
├── ai_patch_notes.py            # AI Patch-Notes-Generierung (~500 Z)
└── deployment.py                # Deployment-Trigger (~80 Z)
```

## Migrations-Strategie

1. Package erstellen, `__init__.py` re-exportiert die Hauptklasse
2. Alte Datei bleibt als Fallback (Import-Kompatibilitaet)
3. Module einzeln extrahieren und testen
4. Alle Imports im Projekt aktualisieren
5. Alte Datei loeschen

## Risiko

- HOCH: Beide Dateien sind Kern-Logik des Bots
- Eigener Feature-Branch (`refactor/orchestrator-split`) empfohlen
- Jedes Modul einzeln testen vor naechstem Split
- Geschaetzter Aufwand: 2-3 Sessions
