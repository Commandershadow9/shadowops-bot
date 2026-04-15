---
title: shadowops-bot Dokumentation
status: active
last_reviewed: 2026-04-15
owner: CommanderShadow9
---

# shadowops-bot Dokumentation

ShadowOps ist ein vollstaendig autonomer Security Guardian fuer Debian-VPS-Infrastrukturen: ein Python-Discord-Bot mit Dual-Engine-AI (Codex + Claude), lernfaehigem Security-Analyst und KI-gesteuerter Auto-Remediation. Das System verbindet Trivy, CrowdSec, Fail2ban und AIDE mit einer PostgreSQL-Knowledge-DB, einer Patch-Notes-Pipeline (v6) und dem Jules-SecOps-Workflow zu einer durchgaengigen Sicherheitsautomatisierung. Diese Dokumentation ist der zentrale Einstiegspunkt fuer Betrieb, Architektur und Weiterentwicklung.

## Ich will...

- **...das System verstehen** → [architecture/](architecture/) — wie funktioniert ShadowOps intern
- **...deployen oder konfigurieren** → [operations/](operations/) — Setup, Monitoring, Secrets
- **...auf einen Incident reagieren** → [runbooks/](runbooks/) — Schritt-fuer-Schritt
- **...ein Feature designen** → [design/](design/) — aktive Design-Docs
- **...etwas nachschlagen** → [reference/](reference/) — API, Config, Glossar
- **...eine fruehere Entscheidung verstehen** → [adr/](adr/) — Architecture Decision Records
- **...historische Dokumentation finden** → [archive/INDEX.md](archive/INDEX.md) — entfernte Dateien mit Git-SHAs

## Status

| Kategorie | Dateien | Status |
|-----------|---------|--------|
| architecture/ | 0 | wird in Task 4-7 befuellt |
| operations/ | 0 | wird in Task 4-7 befuellt |
| runbooks/ | 0 | wird in Task 4-7 befuellt |
| design/ | 0 | wird in Task 4-7 befuellt |
| reference/ | 0 | wird in Task 4-7 befuellt |
| adr/ | 8 | aktiv — Architecture Decision Records |
| archive/ | 22 | historisch — mit INDEX.md und Git-SHAs |
| assets/ | 2 | aktiv — Bilder und Diagramme |
| guides/ | 6 | aktiv (wird spaeter in operations/ migriert) |
| plans/ | 27 | aktiv — Design- und Implementierungsplaene |

## Aktive Haupt-Docs

Diese Liste wird nach Abschluss des Doku-Refactors (geplant Task 12) finalisiert.

- [WIP] Security Engine v6 → wird nach `architecture/` migriert
- [WIP] Patch Notes Pipeline v6 → wird nach `design/` migriert
- [WIP] Jules SecOps Workflow → wird nach `architecture/` migriert
- [WIP] Multi-Agent Review Pipeline → wird nach `architecture/` migriert

---

## Referenzen

- [Design-Doc 2026-04-15](design/doku-refactor.md) — warum diese Struktur
- [Implementierungsplan](plans/2026-04-15-shadowops-bot-doku-refactor.md) — 12 Tasks
- [IST-Bewertung](plans/2026-04-15-shadowops-bot-ist-assessment.md) — Score 5.67
- [CLAUDE.md](../CLAUDE.md) — AI-Entwicklungsrichtlinien und Projekt-Index
