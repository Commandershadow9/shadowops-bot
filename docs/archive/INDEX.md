---
title: Archiv-Index (Legacy-Doku)
status: active
last_reviewed: 2026-04-15
owner: CommanderShadow9
related:
  - ../README.md
  - ../design/doku-refactor.md
---

# Archiv-Index

Diese Datei listet alle Dokumente, die per `git rm` aus dem aktiven Baum
entfernt wurden — mit Datum, Grund, letztem Commit-SHA und (wenn vorhanden)
Ersatz-Pfad.

Die Inhalte bleiben via Git-History verfuegbar:

```bash
git show <sha>:<pfad>
```

## Archivierte Dateien

| Datum | Pfad (vorher) | Grund | Letzter SHA | Ersatz |
|-------|---------------|-------|-------------|--------|
| 2026-04-15 | `docs/archive/ACTIVE_SECURITY_GUARDIAN.md` | v3.0, durch Security-Engine-v6 ersetzt | `db6b0b4` | docs/architecture/security-engine/ |
| 2026-04-15 | `docs/archive/ADVANCED_PATCH_NOTES.md` | v2/v3, durch Patch-Notes-v6 ersetzt | `db6b0b4` | docs/design/patch-notes-v6.md |
| 2026-04-15 | `docs/archive/AGENT_INSTRUCTIONS.md` | Legacy, durch CLAUDE.md ersetzt | `db6b0b4` | CLAUDE.md |
| 2026-04-15 | `docs/archive/AI_LEARNING_MULTI_PROJECT.md` | Entwurf, integriert in agent_learning DB | `79812c7` | - |
| 2026-04-15 | `docs/archive/AUTO_FIX_FLOW.md` | durch Security-Engine-v6 ersetzt | `db6b0b4` | docs/architecture/security-engine/ |
| 2026-04-15 | `docs/archive/AUTO_REMEDIATION.md` | durch Orchestrator + Security-Engine ersetzt | `db6b0b4` | docs/architecture/security-engine/ |
| 2026-04-15 | `docs/archive/FIXES_2025-11-25.md` | Snapshot einer Fix-Welle, nicht mehr relevant | `79812c7` | - |
| 2026-04-15 | `docs/archive/FIX_PLAN.md` | historischer Massnahmen-Plan | `ad04060` | - |
| 2026-04-15 | `docs/archive/GuildScout_KI_Prompt.md` | fremdes Projekt | `79812c7` | - |
| 2026-04-15 | `docs/archive/GuildScout_Konzept.md` | fremdes Projekt | `79812c7` | - |
| 2026-04-15 | `docs/archive/GuildScout_START_HIER.md` | fremdes Projekt | `79812c7` | - |
| 2026-04-15 | `docs/archive/HYBRID_AI_SYSTEM.md` | Ollama-Hybrid, durch Dual-Engine ersetzt | `79812c7` | - |
| 2026-04-15 | `docs/archive/IMPLEMENTATION_COMPLETE.md` | v2.0-Completion, v5.1 aktiv | `79812c7` | - |
| 2026-04-15 | `docs/archive/LEARNING_SYSTEM_IMPLEMENTATION_PLAN.md` | durch analyst-learning-pipeline abgeloest | `79812c7` | - |
| 2026-04-15 | `docs/archive/LIVE_DISCORD_UPDATES.md` | Vorgaengerfunktion fuer Patch Notes | `79812c7` | docs/design/patch-notes-v6.md |
| 2026-04-15 | `docs/archive/MULTI-SERVER-SETUP.md` | durch multi-guild-setup.md ersetzt | `79812c7` | docs/operations/multi-guild-setup.md |
| 2026-04-15 | `docs/archive/UNRESOLVED_ISSUES.md` | Snapshot von Nov 2025 | `79812c7` | - |
| 2026-04-15 | `docs/archive/UPDATE_GUIDE.md` | Vorgaenger des Patch-Notes-Workflows | `79812c7` | - |
| 2026-04-15 | `docs/archive/context_guildscout.md` | Kontext-Draft | `db6b0b4` | - |
| 2026-04-15 | `docs/archive/context_infrastructure.md` | Kontext-Draft | `db6b0b4` | - |
| 2026-04-15 | `docs/archive/context_shadowops-bot.md` | Kontext-Draft, in CLAUDE.md konsolidiert | `db6b0b4` | CLAUDE.md |
| 2026-04-15 | `docs/archive/context_sicherheitstool.md` | fremdes Projekt | `db6b0b4` | - |
| 2026-04-15 | `docs/OVERVIEW.md` | durch docs/README.md ersetzt | `af27e98` | docs/README.md |
| 2026-04-15 | `docs/SECURITY_ANALYST.md` | durch SecurityScanAgent ersetzt | `f7ad4a4` | docs/architecture/security-engine/deep-scan-mode.md |
| 2026-04-15 | `docs/architecture-decisions.md` | temporaere CLAUDE.md-Auslagerung, durch Lifecycle-Struktur abgeloest | `ad04060` | docs/README.md |
| 2026-04-15 | `docs/security-engine-v6-overview.md` | dupliziert in docs/architecture/security-engine/README.md | `beae222` | docs/architecture/security-engine/README.md |
| 2026-04-15 | `docs/plans/2026-04-11-jules-secops-workflow.md` | Monolith, in docs/architecture/jules-workflow/ extrahiert | `d8fca03` | docs/architecture/jules-workflow/ |
| 2026-04-15 | `docs/plans/2026-03-24-security-engine-v6.md` | Monolith, in docs/architecture/security-engine/ extrahiert | `c99d50d` | docs/architecture/security-engine/ |
| 2026-04-15 | `docs/plans/2026-04-14-multi-agent-review.md` | Monolith, in docs/architecture/multi-agent-review/ extrahiert | `ad04060` | docs/architecture/multi-agent-review/ |
| 2026-04-15 | `docs/plans/2026-03-06-shadowops-v4-design.md` | Ollama->Dual-Engine, durch v5.1 ersetzt | `1c929ca` | - |
| 2026-04-15 | `docs/plans/2026-03-06-shadowops-v4-implementation.md` | Ollama->Dual-Engine Implementation, abgeschlossen | `28b9a6b` | - |
| 2026-04-15 | `docs/plans/2026-03-09-security-analyst.md` | durch Security-Engine-v6 ersetzt | `9819617` | docs/architecture/security-engine/ |
| 2026-04-15 | `docs/plans/2026-03-10-patch-notes-v2-design.md` | v2, durch v6 ersetzt | `b95b28b` | docs/design/patch-notes-v6.md |
| 2026-04-15 | `docs/plans/2026-03-10-project-restructure-design.md` | alte Restrukturierung, durch aktuelles Refactor abgeloest | `4803e90` | docs/design/doku-refactor.md |
| 2026-04-15 | `docs/plans/2026-03-11-db-changelogs-design.md` | durch v6 Unified Pipeline ersetzt | `ba10d0a` | docs/design/patch-notes-v6.md |
| 2026-04-15 | `docs/plans/2026-03-11-db-changelogs.md` | durch v6 Unified Pipeline ersetzt | `ba10d0a` | docs/design/patch-notes-v6.md |
| 2026-04-15 | `docs/plans/2026-03-12-patch-notes-v3-design.md` | v3, durch v6 ersetzt | `0fa2949` | docs/design/patch-notes-v6.md |
| 2026-04-15 | `docs/plans/2026-03-12-patch-notes-v3-implementation.md` | v3, durch v6 ersetzt | `5d55928` | docs/design/patch-notes-v6.md |
| 2026-04-15 | `docs/plans/2026-03-15-patch-notes-unified-pipeline.md` | Vorstufe v6 | `a95c466` | docs/design/patch-notes-v6.md |
| 2026-04-15 | `docs/plans/2026-03-16-changelog-redesign-design.md` | shared-ui Changelog, Umsetzung abgeschlossen | `c6a2982` | - |
| 2026-04-15 | `docs/plans/2026-03-16-changelog-redesign.md` | shared-ui Changelog, Umsetzung abgeschlossen | `ad09f5c` | - |
| 2026-04-15 | `docs/plans/2026-03-18-analyst-learning-pipeline-design.md` | durch Security-Engine-v6 abgeloest | `af27e98` | docs/architecture/security-engine/ |
| 2026-04-15 | `docs/plans/2026-03-24-security-scan-agent-design.md` | integriert in docs/architecture/security-engine/deep-scan-mode.md | `4725efe` | docs/architecture/security-engine/deep-scan-mode.md |
| 2026-04-15 | `docs/plans/2026-03-29-patch-notes-v6-daily-digest.md` | Teilaspekt v6, in Multi-Agent-Review-Architektur aufgegangen | `90870c8` | docs/architecture/multi-agent-review/ |
| 2026-04-15 | `docs/plans/2026-03-30-mayday-changelog-design.md` | MayDay-Frontend, Umsetzung abgeschlossen | `c4d27cc` | - |
| 2026-04-15 | `docs/plans/2026-03-30-mayday-changelog-implementation.md` | MayDay-Frontend, Umsetzung abgeschlossen | `c4d27cc` | - |
| 2026-04-15 | `docs/plans/2026-04-13-patch-notes-v6-implementation.md` | Implementation abgeschlossen | `d8fca03` | docs/design/patch-notes-v6.md |
