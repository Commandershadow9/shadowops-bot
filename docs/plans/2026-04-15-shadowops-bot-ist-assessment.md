---
title: IST-Bewertung shadowops-bot Doku
status: active
last_reviewed: 2026-04-15
owner: Claude
related:
  - ../design/doku-refactor.md
  - ./2026-04-15-shadowops-bot-doku-refactor.md
---

# IST-Bewertung shadowops-bot Doku

Erhoben am 2026-04-15 als Phase-0-Input fuer den Doku-Refactor.
Grundlage: Design-Doc `../design/doku-refactor.md`, Abschnitt D5 (IST-Bewertung).

## 1. Metriken (rohe Zahlen)

| Metrik | Wert |
|--------|------|
| MD-Dateien gesamt (inkl. archive) | 70 |
| MD-Dateien aktiv (ohne archive) | 48 |
| Archiv-Dateien | 22 |
| Plans-Dateien | 26 |
| Guides-Dateien | 6 |
| ADRs | 8 |
| Monolithen > 20 KB | 14 |
| Monolithen > 500 Zeilen (aktiv, ohne archive) | 15 |
| Dateien mit YAML Front-Matter (aktiv) | 1 / 48 |
| Top-Level Indexdatei (`docs/README.md`) | fehlt |
| `docs/OVERVIEW.md` vorhanden | ja, verweist aber auf archivierte Dateien |

Erhoben via: `find docs -name '*.md' | wc -l`, `find docs -name '*.md' -exec head -1 {} \; | grep '^---'`, `find docs -name '*.md' -exec wc -l {} + | sort -rn`.

### Top 5 groesste Dateien (Zeilen)

| Zeilen | Datei |
|-------:|-------|
| 4590 | `docs/plans/2026-04-11-jules-secops-workflow.md` |
| 2455 | `docs/plans/2026-03-24-security-engine-v6.md` |
| 1724 | `docs/plans/2026-04-14-multi-agent-review.md` |
| 1385 | `docs/archive/FIX_PLAN.md` (archive) |
| 1286 | `docs/plans/2026-04-13-patch-notes-v6-implementation.md` |

### Top-Level docs/-Wurzel (Chaos-Indikator)

Top-Level-Dateien neben den Unterordnern `plans/`, `adr/`, `guides/`, `archive/`, `assets/`:

- `API.md` (25 KB, 979 Zeilen)
- `OVERVIEW.md` (4,4 KB) — veraltet, verweist auf archive
- `SECURITY_ANALYST.md` (11 KB)
- `architecture-decisions.md` (27 KB)
- `multi-agent-review-operations.md` (7,3 KB)
- `multi-agent-review-rollout.md` (6,4 KB)
- `multi-agent-review-runbook.md` (9,7 KB)
- `security-engine-v6-overview.md` (5,6 KB)
- `Notrufzentrale bei Nacht.png` (Asset im Wurzelverzeichnis)

### Aktivitaet

- Neuester Commit auf `docs/`: 2026-04-15 (Implementierungsplan Refactor)
- Neuestes inhaltliches Design-Doc: 2026-04-14 (Multi-Agent Review)
- Aeltestes Guide: 2025-11-12 (`QUICKSTART.md`) — 5 Monate alt, nicht dokumentiert ob noch gueltig
- Alte Top-Level-Referenz: `OVERVIEW.md` (2026-03-18) verlinkt `ACTIVE_SECURITY_GUARDIAN.md` (liegt in `archive/`) und markiert `AUTO_REMEDIATION.md`, `HYBRID_AI_SYSTEM.md` explizit als veraltet

## 2. Bewertung nach Design-Doc D5

### 2.1 Vollstaendigkeit — **6 / 10**

**Vorhanden:**
- `README.md` (Projekt-Wurzel, nicht in `docs/`)
- Setup- und Quickstart-Guides (`guides/QUICKSTART.md`, `guides/SETUP_GUIDE.md`, `guides/CUSTOMER_SERVER_SETUP.md`, `guides/MULTI_GUILD_SETUP.md`)
- API-Referenz (`API.md`, 979 Zeilen)
- Betriebs-/Runbook-Doku fuer Multi-Agent Review (3 Top-Level-Dateien) und Jules (`guides/JULES_RUNBOOK.md`)
- 8 ADRs, halbwegs gepflegt (ADR-008 zuletzt 2026-04-14)
- Design-/Implementierungs-Docs pro groesserem Feature (26 Plans)

**Fehlend:**
- **Keine `docs/README.md` / kein Index** — Einstieg fehlt komplett, neue Entwickler/AI landen blind auf GitHub-Verzeichnis-Listing
- **Kein Glossar** — Begriffe wie "Pipeline v6", "ScanAgent", "Adapter", "Circuit Breaker", "Queue-Scheduler" leben nur in CLAUDE.md
- **Kein zentrales Deploy-Doc** — Deploy steht verstreut in `scripts/restart.sh`, `deploy/shadowops-bot.service`, CLAUDE.md "Befehle"-Tabelle
- **Kein zentrales Monitoring-Doc** — Health-Checks, Metriken-Format (`METRICS|...|{json}`), Weekly-Check-Script nirgends gesammelt dokumentiert
- **Keine Troubleshooting-Sammlung** — Vorfaelle stehen als Einzeleintraege in CLAUDE.md und Design-Docs (z. B. Loop-Bug 2026-04-14), keine gemeinsame FAQ/Playbook-Datei
- **Kein Lifecycle-Split** — Design-Doc vs. Implementation vs. Runbook vs. Archive in `plans/` vermischt

Solide Basis, aber fuer Enterprise-Betrieb reicht's nicht. Operative Doku ist luckenhaft.

### 2.2 Aktualitaet — **7 / 10**

**Positiv:**
- Kern-Features (Pipeline v6, Multi-Agent Review, Jules) haben Design- UND Implementation-Docs, beide Apr 2026
- ADR-008 (2026-04-14) dokumentiert auch Post-Deploy-Fixes
- 4 der 10 groessten Docs sind aus April 2026 (aktiv gepflegt)
- Post-Go-Live-Fixes und Vorfall-Historie (Loop-Bug 2026-04-14, PR #123) in CLAUDE.md ergaenzt

**Negativ:**
- `docs/OVERVIEW.md` ist **kaputt**: verweist auf `ACTIVE_SECURITY_GUARDIAN.md` (liegt im archive), markiert selbst 2 Files als "(Warn-Hinweis) Veraltet" — Index pflegt sich nicht mit
- Guides aus Nov 2025 (`QUICKSTART.md` vom 2025-11-12, `SETUP_GUIDE.md` vom 2025-11-24) nicht als "reviewed am" markiert — unklar ob noch gueltig (Stack hat sich zwischen Nov 2025 und Apr 2026 stark veraendert)
- 11 aktive Files enthalten TODO/FIXME/geplant/veraltet-Marker — meistens in Design-Docs okay, aber nicht systematisch als "offene Punkte" kuratiert
- Kein `last_reviewed`-Feld irgendwo (Front-Matter-Coverage 2 %), d. h. es gibt keinen Mechanismus um Altdoku zu finden ausser mtime
- 22 Archiv-Dateien ohne Archiv-Index — beim Refactor der n-te Aufwand diese zu sichten

Kern ist aktuell, Rand ist angestaubt.

### 2.3 Struktur — **4 / 10**

**Positiv:**
- `plans/`, `adr/`, `guides/`, `archive/`, `assets/` existieren als grobe Unterordner
- ADRs sind sauber nummeriert (001-008)

**Negativ:**
- **Keine Indexdatei** (`docs/README.md`) → kein Einstiegspunkt, keine Semantic Map
- **9 Dateien direkt in `docs/`-Wurzel** (inkl. einem PNG-Asset "Notrufzentrale bei Nacht.png") — Top-Level ist Muell-Eimer fuer alles was nicht eindeutig Plan/ADR/Guide ist
- **15 Monolithen > 500 Zeilen** (aktiv), davon 3 extreme:
  - `jules-secops-workflow.md` (4590 Zeilen — 1 Datei, 6 Themen: Design, Implementation, Deploy, API, Iteration, Vorfaelle)
  - `security-engine-v6.md` (2455 Zeilen)
  - `multi-agent-review.md` (1724 Zeilen)
- **Kein Lifecycle-Split** in `plans/`: Design-Docs (abgeschlossen), laufende Implementierung und historische Plaene liegen alle unsortiert im selben Ordner
- **Keine Trennung Runbook / Operations / Rollout** — Multi-Agent hat 3 Dateien auf Top-Level statt in einem `operations/`-Ordner
- **Front-Matter fehlt** (2 % Coverage) — kein Status, kein Review-Datum, keine related-Links maschinenlesbar
- **Doppelte Inhalte zu erwarten** (z. B. Jules-Thematik in Design-Doc, Implementation-Doc, Runbook, CLAUDE.md, ADR-007) — ohne Semantic Map nicht nachweisbar welche Version die "Source of Truth" ist

Struktur ist Ablage, keine Architektur.

## 3. Gesamt-Score und Entscheidung

| Achse | Score |
|-------|------:|
| Vollstaendigkeit | 6 / 10 |
| Aktualitaet | 7 / 10 |
| Struktur | 4 / 10 |
| **Gesamt** | **(6+7+4)/3 = 5,67 / 10** |

### Entscheidung: **Voller Refactor** (Gesamt-Score 5,67 < 7,0)

### 3-Saetze-Begruendung

1. **Die Struktur ist der Flaschenhals** (Score 4): ohne Index, mit drei Monolithen ueber 1700 Zeilen und 9 Top-Level-Dateien ist die Doku fuer neue Mitglieder und AI-Agents nur noch mit Vorwissen brauchbar — das ist fuer einen Enterprise-Betrieb mit Multi-Agent-Pipeline, Learning-DB und Jules-Integration nicht tragbar.
2. **Aktualitaet taeuscht ueber Vollstaendigkeit hinweg** (6/7 vs. 4): die neuesten Features sind gut dokumentiert, aber es fehlen operative Grundlagen (Monitoring-Doc, Troubleshooting-FAQ, Deploy-Doc, Glossar) und das einzige Index-Aequivalent (`OVERVIEW.md`) zeigt schon selbst auf archivierte Dateien — die Decke traegt die Wand nicht mehr.
3. **Ein Leicht-Touch wuerde die Monolithen, das fehlende Lifecycle-Modell und die fehlende Front-Matter-Hygiene nicht adressieren** — das sind genau die Punkte aus dem Design-Doc (D1, D2, D7), die der Refactor anpacken soll, und sie lassen sich nicht durch punktuelles Flicken loesen.

## 4. Empfohlene Folge-Tasks

Reihenfolge entspricht dem Implementierungsplan `2026-04-15-shadowops-bot-doku-refactor.md` (Tasks 2-12):

1. Lifecycle-Verzeichnisse anlegen (`design/`, `operations/`, `runbooks/`, `reference/`)
2. `docs/README.md` als Semantic Map schreiben
3. Die 3 extremen Monolithen (jules, security-engine-v6, multi-agent-review) splitten — parallel via Team
4. Aktive Design-Docs aus `plans/` nach `docs/design/` verschieben
5. Top-Level-Dateien einsortieren, PNG-Asset nach `assets/`
6. Guides splitten in `operations/` vs. `runbooks/`
7. Archive-INDEX + Git-rm fuer echte Legacy-Sackgassen
8. YAML Front-Matter auf alle aktiven Docs (Template aus Design-Doc D2)
9. CLAUDE.md auf neue Pfade updaten
10. Code-Pfad-Referenzen (grep-based) aktualisieren
11. Abschluss-Report und Merge

## 5. Offene Fragen / Ueberraschungen

- **Asset im Top-Level**: `docs/Notrufzentrale bei Nacht.png` gehoert eindeutig nach `docs/assets/` — in Task 6 mit einsortieren.
- **README-Fallback**: Die Projekt-Wurzel `README.md` existiert, aber `docs/README.md` fehlt — muss in Task 3 klar vom Wurzel-README abgegrenzt werden (Wurzel-README = Quick-Intro, `docs/README.md` = Doku-Navigation).
- **Guides-Alter**: 4 Guides aus Nov 2025 — in Task 7 aktiv reviewen oder als veraltet flaggen.
- **OVERVIEW.md**: verweist auf `archive/ACTIVE_SECURITY_GUARDIAN.md` — entweder als Legacy archivieren oder in `docs/README.md` aufgehen lassen (Task 3 entscheidet).
- **Front-Matter-Pilot**: Die eine aktive Datei mit Front-Matter ist vermutlich das frisch geschriebene Design-Doc vom 2026-04-15 selbst — Template ist also bereits erprobt (Task 9).
