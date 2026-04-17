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
- **...deployen oder konfigurieren** → [operations/](operations/) — Setup, Monitoring, Multi-Guild
- **...auf einen Incident reagieren** → [runbooks/](runbooks/) — Schritt-fuer-Schritt
- **...ein Feature designen** → [design/](design/) — aktive Design-Docs
- **...etwas nachschlagen** → [reference/](reference/) — API-Reference
- **...eine fruehere Entscheidung verstehen** → [adr/](adr/) — 8 Architecture Decision Records
- **...historische Dokumentation finden** → [archive/INDEX.md](archive/INDEX.md) — 46 entfernte Dateien mit Git-SHAs

## Status (Stand 2026-04-15)

| Kategorie | Dateien | Status |
|-----------|---------|--------|
| `architecture/` | 16 | aktiv — 3 Subsysteme (jules-workflow, security-engine, multi-agent-review) |
| `operations/` | 7 | aktiv — Setup, Quickstart, Webhooks, Multi-Guild, Customer-Server, Multi-Agent-Review-Daily+Rollout |
| `runbooks/` | 2 | aktiv — Incident-Response (jules-workflow, multi-agent-review) |
| `design/` | 4 | aktiv — patch-notes-v6, jules-workflow, multi-agent-review, doku-refactor |
| `reference/` | 1 | aktiv — API-Reference |
| `adr/` | 8 | aktiv — ADR-001 bis ADR-008 |
| `archive/INDEX.md` | 1 | Tabelle mit 46 archivierten Dateien (Git-SHA pro Eintrag) |
| `plans/` | 2 | aktueller Implementierungsplan + IST-Bewertung (Doku-Refactor) |
| `assets/` | 2 | Bilder |

Alle aktiven Docs haben YAML Front-Matter mit `title`, `status`, `last_reviewed`, `owner`.

## Aktive Haupt-Docs

### Architecture
- [Jules SecOps Workflow](architecture/jules-workflow/README.md) — autonome PR-Reviews mit 7-Schichten-Loop-Schutz
- [Security Engine v6](architecture/security-engine/README.md) — 3-Modi-Architektur (Reactive, Proactive, DeepScan)
- [Multi-Agent Review Pipeline](architecture/multi-agent-review/README.md) — Adapter-Pattern fuer Jules/SEO/Codex

### Design (aktiv in Umsetzung oder Referenz)
- [Patch Notes Pipeline v6](design/patch-notes-v6.md) — State Machine mit Self-Healing
- [Jules Workflow Design](design/jules-workflow.md)
- [Multi-Agent Review Design](design/multi-agent-review.md)
- [Doku-Refactor Design](design/doku-refactor.md) — Topologie, Front-Matter, Archive-Policy

### Operations
- [Setup](operations/setup.md) · [Quickstart](operations/quickstart.md) · [Multi-Guild](operations/multi-guild-setup.md)
- [GitHub Push Notifications](operations/github-push-notifications.md)
- [Customer Server Setup](operations/customer-server-setup.md)
- Multi-Agent-Review: [Rollout](operations/multi-agent-review-rollout.md) · [Daily-Betrieb](operations/multi-agent-review-daily.md)

### Runbooks (bei Incident)
- [Jules-Workflow Incident](runbooks/jules-workflow.md)
- [Multi-Agent-Review Incident](runbooks/multi-agent-review.md)

---

## Referenzen

- [Design-Doc 2026-04-15](design/doku-refactor.md) — warum diese Struktur
- [Implementierungsplan](plans/2026-04-15-shadowops-bot-doku-refactor.md) — 12 Tasks (alle abgeschlossen)
- [IST-Bewertung](plans/2026-04-15-shadowops-bot-ist-assessment.md) — Score 5.67/10 → voller Refactor
- [CLAUDE.md](../CLAUDE.md) — AI-Entwicklungsrichtlinien und Projekt-Index
