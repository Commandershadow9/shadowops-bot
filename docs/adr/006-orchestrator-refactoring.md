# ADR-006: Orchestrator + GitHub Integration Refactoring

**Status:** Umgesetzt
**Datum:** 2026-03-10
**Umgesetzt am:** 2026-03-10

## Kontext

Zwei Dateien waren auf unkontrollierbare Größe gewachsen:
- `orchestrator.py` — 2533 Zeilen, 1 Klasse, 28 Methoden
- `github_integration.py` — 2691 Zeilen, 1 Klasse, 67 Methoden

Beide verletzten Single Responsibility und waren schwer testbar.

## Entscheidung

Beide Dateien wurden in Packages mit Mixin-Pattern aufgeteilt. Die Hauptklasse erbt von mehreren Mixins, die jeweils eine klar abgegrenzte Verantwortung haben. `__init__.py` re-exportiert die Hauptklasse für Import-Kompatibilität.

### orchestrator.py → orchestrator/

```
src/integrations/orchestrator/
├── __init__.py          # Re-Export RemediationOrchestrator, SecurityEventBatch, RemediationPlan
├── models.py            # SecurityEventBatch, RemediationPlan Dataclasses (~50 Z)
├── core.py              # RemediationOrchestrator(__init__ + Mixin-Komposition) (~90 Z)
├── batch_mixin.py       # Event-Batching, History-Persistenz, Adaptive Retry (~230 Z)
├── planner_mixin.py     # KI-Planerstellung, Prompt-Building, Streaming (~310 Z)
├── discord_mixin.py     # Status-Messages, Approval-Flow (Discord UI) (~240 Z)
├── executor_mixin.py    # Plan-Ausführung, Multi-Projekt, Phase-Execution (~660 Z)
└── recovery_mixin.py    # Rollback, Verifikation, Summary, Status (~380 Z)
```

### github_integration.py → github_integration/

```
src/integrations/github_integration/
├── __init__.py              # Re-Export GitHubIntegration
├── core.py                  # GitHubIntegration(__init__ + Mixin-Komposition) (~127 Z)
├── webhook_mixin.py         # HTTP-Server, Signature-Verify (~341 Z)
├── polling_mixin.py         # Local Git Polling (~136 Z)
├── event_handlers_mixin.py  # Push/PR/Release/Workflow Handler (~553 Z)
├── ci_mixin.py              # CI-Message-Updates, Deployment-Trigger (~209 Z)
├── state_mixin.py           # Git-State-Tracking, Deduplizierung (~101 Z)
├── git_ops_mixin.py         # Git-Subprocess-Operationen (~139 Z)
├── notifications_mixin.py   # Alle Discord-Notifications (~495 Z)
└── ai_patch_notes_mixin.py  # AI Patch-Notes-Generierung (~708 Z)
```

## Migrations-Strategie (wie durchgeführt)

1. Package-Verzeichnis erstellt, `__init__.py` re-exportiert die Hauptklasse
2. Methoden logisch in Mixins gruppiert (nach Verantwortung)
3. Hauptklasse erbt von allen Mixins via MRO
4. Alle `self._method()`-Aufrufe funktionieren weiter durch Python MRO
5. Tests angepasst (Patch-Pfade auf Mixin-Module aktualisiert)
6. Alte Einzeldateien gelöscht

## Ergebnis

- **5224 Zeilen** in **18 fokussierte Module** aufgeteilt
- Import-Kompatibilität vollständig erhalten
- Alle Unit-Tests laufen weiter (Patch-Pfade angepasst)
- Kein Feature-Branch nötig — direktes Refactoring auf main war sicher
