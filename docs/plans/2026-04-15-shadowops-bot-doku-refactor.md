---
title: ShadowOps-Bot — Doku-Refactor Implementation Plan
status: active
last_reviewed: 2026-04-15
owner: CommanderShadow9
---

# ShadowOps-Bot — Doku-Refactor Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** shadowops-bot-Doku auf einheitliche Lifecycle-Struktur bringen (Design-Doc 2026-04-15), ohne bestehende Funktion oder externe Links zu brechen.

**Architecture:** Lifecycle-Topologie (`architecture/`, `operations/`, `runbooks/`, `design/`, `reference/`, `adr/`, `archive/`). YAML Front-Matter auf allen aktiven Docs. Monolithen ueber 500 Zeilen werden gesplittet. Veraltete Design-Docs per `git rm` entfernt, `archive/INDEX.md` fuehrt Buch mit Git-SHAs.

**Tech Stack:** Git, Markdown, Bash (grep/find/wc), keine Runtime-Abhaengigkeiten.

**Vorher-Design:** [2026-04-15-doku-refactor-design.md](./2026-04-15-doku-refactor-design.md)

---

## Safety-Regeln (aus Design-Doc D7, hier gespiegelt)

1. **Atomarer Commit pro Task** — jede Task endet mit `git commit`.
2. **Vor Datei-Umzug**: `grep -r "<alter-pfad>" /home/cmdshadow/shadowops-bot` im gesamten Projekt. Treffer in Scripts, README, Code werden vor dem Umzug aktualisiert.
3. **Tabu-Zone**: `.env`, `config/config.yaml`, `data/`, `logs/`, `.venv/` — niemals anfassen.
4. **README mit externen Badges**: Nur umformatieren, nie loeschen.
5. **Max 30 Minuten am Stueck**, dann Commit und Pause.
6. **Beim kleinsten Zweifel**: User fragen, nicht raten.

---

## Task 1: IST-Bewertung dokumentieren

**Files:**
- Create: `docs/plans/2026-04-15-shadowops-bot-ist-assessment.md`

**Step 1: Metriken erheben**

Commands:
```bash
cd /home/cmdshadow/shadowops-bot
find docs -name "*.md" | wc -l                    # Anzahl Doku-Dateien
find docs -name "*.md" -size +20k                 # Monolithen > 20 KB
find docs -name "*.md" -exec wc -l {} \; | sort -rn | head -10   # Top 10 groesste
ls docs/                                          # Top-Level-Struktur
ls docs/archive/ | wc -l                          # Archiv-Datei-Anzahl
grep -l "^---$" docs/**/*.md 2>/dev/null | wc -l  # Front-Matter-Zaehlung
```

Erwartetes Ergebnis dokumentieren (Zahlen in Assessment-Doc).

**Step 2: Bewertung schreiben**

Assessment-Doc mit drei Achsen (Vollstaendigkeit, Aktualitaet, Struktur), jede 1-10. Begruendung je 1 Satz. Gesamt-Score. Entscheidung: Voller Refactor oder Leicht-Touch.

**Step 3: Commit**

```bash
git add docs/plans/2026-04-15-shadowops-bot-ist-assessment.md
git commit -m "docs: shadowops-bot IST-Bewertung (Phase 0/Doku-Refactor)"
```

---

## Task 2: Lifecycle-Verzeichnisse anlegen

**Files:**
- Create: `docs/architecture/.gitkeep`
- Create: `docs/operations/.gitkeep`
- Create: `docs/runbooks/.gitkeep`
- Create: `docs/design/.gitkeep`
- Create: `docs/reference/.gitkeep`

**Step 1: Verzeichnisse erstellen**

```bash
cd /home/cmdshadow/shadowops-bot
mkdir -p docs/architecture docs/operations docs/runbooks docs/design docs/reference
touch docs/architecture/.gitkeep docs/operations/.gitkeep docs/runbooks/.gitkeep docs/design/.gitkeep docs/reference/.gitkeep
```

**Step 2: Verifikation**

```bash
ls -la docs/
```

Erwartet: 5 neue Verzeichnisse, bestehende Ordner (adr/, archive/, plans/, guides/, assets/) unveraendert.

**Step 3: Commit**

```bash
git add docs/architecture docs/operations docs/runbooks docs/design docs/reference
git commit -m "docs: shadowops-bot Lifecycle-Verzeichnisse anlegen"
```

---

## Task 3: docs/README.md als Semantic Map schreiben

**Files:**
- Create: `docs/README.md`

**Step 1: Template instantiieren**

Inhalt folgt Design-Doc D3. Elevator-Pitch (3 Saetze), "Ich will..."-Navigation, Kategorie-Tabelle, aktive Haupt-Docs.

**Step 2: Links verifizieren**

Jeder Link im README muss auf existierende oder bald zu erstellende Datei zeigen. Nicht-existierende Links bekommen `[WIP]`-Marker und stehen in Task 12 zur Pflege.

**Step 3: Commit**

```bash
git add docs/README.md
git commit -m "docs: shadowops-bot docs/README.md als Semantic Map"
```

---

## Task 4: Monolithen identifizieren und splitten

**Files:**
- Eingangs-Kandidaten: `docs/plans/2026-04-11-jules-secops-workflow.md` (~149 KB), `docs/plans/2026-03-24-security-engine-v6.md` (~86 KB), `docs/plans/2026-04-14-multi-agent-review.md` (~55 KB)
- Target-Struktur: je eigener Unterordner in `docs/architecture/` mit 3-5 Teildateien + `_index.md`

**Step 1: Inhaltsverzeichnis pro Monolith extrahieren**

```bash
for f in docs/plans/2026-04-11-jules-secops-workflow.md docs/plans/2026-03-24-security-engine-v6.md docs/plans/2026-04-14-multi-agent-review.md; do
  echo "=== $f ==="
  grep -n "^## " "$f"
done
```

**Step 2: Split-Plan pro Monolith schreiben (im Chat)**

Fuer jeden Monolith: Liste der geplanten Teildateien mit Titel und Ziel-Pfad. User kurz freigeben lassen vor Split.

**Step 3: Split durchfuehren**

Fuer Jules-Workflow (Beispielstruktur, konkrete Struktur aus Step 2):
- `docs/architecture/jules-workflow/README.md` (Uebersicht + Index)
- `docs/architecture/jules-workflow/detection.md` (PR-Erkennung, 3 Kriterien)
- `docs/architecture/jules-workflow/review-pipeline.md` (Claude-Review, Schema)
- `docs/architecture/jules-workflow/loop-protection.md` (7 Schichten)
- `docs/architecture/jules-workflow/learning.md` (Few-Shot + Knowledge)
- `docs/architecture/jules-workflow/rollback.md` (Config-Flags, Incident-Referenz)

Analog fuer security-engine und multi-agent-review.

**Step 4: Front-Matter auf allen neuen Dateien**

Jede neue Datei bekommt Front-Matter nach Standard (Design-Doc D2) mit `status: active`, `last_reviewed: 2026-04-15`, `related: [adr/007]` etc.

**Step 5: Ursprungs-Monolithen werden NICHT geloescht** — sie werden Ziel von Task 8 (Archive-Index nach Extraktion).

**Step 6: Commit**

Pro Monolith ein Commit:
```bash
git add docs/architecture/jules-workflow/
git commit -m "docs: shadowops-bot Jules-Workflow-Monolith in 6 Teildateien gesplittet"
```

---

## Task 5: Aktive Design-Docs nach design/ migrieren

**Files:**
- Quelle: `docs/plans/2026-04-*.md` (14.04 Multi-Agent, 13.04 Patch-Notes-v6, 11.04 Jules — nur die jeweils -design.md-Varianten, Implementation-Docs gelten als abgeschlossen und wandern ins Archiv)
- Ziel: `docs/design/`

**Step 1: Aktive Design-Docs identifizieren**

Aktiv = Status "in Umsetzung oder aktuelle Referenz". Nach Design-Doc-Studie:
- `docs/plans/2026-04-14-multi-agent-review-design.md` → `docs/design/multi-agent-review.md`
- `docs/plans/2026-04-13-patch-notes-v6-design.md` → `docs/design/patch-notes-v6.md`
- `docs/plans/2026-04-11-jules-secops-workflow-design.md` → `docs/design/jules-workflow.md`
- `docs/plans/2026-04-15-doku-refactor-design.md` → `docs/design/doku-refactor.md`

**Step 2: Pfad-Referenzen-Scan**

```bash
for old in docs/plans/2026-04-14-multi-agent-review-design.md docs/plans/2026-04-13-patch-notes-v6-design.md docs/plans/2026-04-11-jules-secops-workflow-design.md docs/plans/2026-04-15-doku-refactor-design.md; do
  echo "=== Referenzen auf $old ==="
  grep -rn "$old" . --include="*.md" --include="*.py" --include="*.sh" --include="*.yaml" 2>/dev/null
done
```

Treffer werden in Task 10 (CLAUDE.md) und Task 11 (Code) aktualisiert.

**Step 3: git mv durchfuehren**

```bash
git mv docs/plans/2026-04-14-multi-agent-review-design.md docs/design/multi-agent-review.md
git mv docs/plans/2026-04-13-patch-notes-v6-design.md docs/design/patch-notes-v6.md
git mv docs/plans/2026-04-11-jules-secops-workflow-design.md docs/design/jules-workflow.md
git mv docs/plans/2026-04-15-doku-refactor-design.md docs/design/doku-refactor.md
```

**Step 4: Front-Matter ergaenzen in verschobenen Dateien**

**Step 5: Commit**

```bash
git commit -m "docs: shadowops-bot aktive Design-Docs nach docs/design/ migriert"
```

---

## Task 6: Top-Level docs/-Dateien einsortieren

**Files:**
- `docs/OVERVIEW.md` → wird durch `docs/README.md` ersetzt, nach `archive/` via Git-rm (INDEX-Eintrag)
- `docs/API.md` → `docs/reference/api.md`
- `docs/SECURITY_ANALYST.md` → `docs/archive/` via git-rm (durch SecurityScanAgent abgeloest, INDEX-Eintrag)
- `docs/security-engine-v6-overview.md` → `docs/architecture/security-engine-v6.md`
- `docs/architecture-decisions.md` → bleibt als Legacy-Index, oder wird auf `docs/archive/INDEX.md` umgelenkt (Entscheidung bei Task-Execution)
- `docs/multi-agent-review-rollout.md` → `docs/operations/multi-agent-review-rollout.md`
- `docs/multi-agent-review-runbook.md` → `docs/runbooks/multi-agent-review.md`
- `docs/multi-agent-review-operations.md` → `docs/operations/multi-agent-review-daily.md`

**Step 1: Pfad-Referenzen-Scan** (analog Task 5 Step 2)

**Step 2: git mv + Front-Matter** fuer jede Datei.

**Step 3: Commit**

```bash
git commit -m "docs: shadowops-bot Top-Level-Dateien in Lifecycle-Kategorien einsortiert"
```

---

## Task 7: docs/guides/ in operations/ + runbooks/ aufteilen

**Files:**
- `docs/guides/SETUP.md` → `docs/operations/setup.md`
- `docs/guides/QUICKSTART.md` → `docs/operations/quickstart.md`
- `docs/guides/GITHUB_WEBHOOKS.md` → `docs/operations/github-webhooks.md`
- `docs/guides/JULES_RUNBOOK.md` → `docs/runbooks/jules-workflow.md`
- `docs/guides/MULTI_GUILD.md` → `docs/operations/multi-guild.md`
- `docs/guides/CUSTOMER_SERVER_SETUP.md` → `docs/operations/customer-server-setup.md`

**Step 1: Pfad-Referenzen-Scan**

**Step 2: git mv + Front-Matter**

**Step 3: docs/guides/ loeschen (leer)**

```bash
rmdir docs/guides
```

**Step 4: Commit**

```bash
git commit -m "docs: shadowops-bot docs/guides/ in operations/ + runbooks/ aufgeteilt"
```

---

## Task 8: Archive-INDEX und Git-rm von Legacy

**Files:**
- Create: `docs/archive/INDEX.md`
- Delete via `git rm`: `docs/archive/*.md` (22 bestehende Legacy-Dateien) + `docs/plans/2026-03-*` Legacy-Designs + `docs/OVERVIEW.md` + `docs/SECURITY_ANALYST.md` + die 3 Monolithen (nach Extraktion in Task 4)

**Step 1: Liste der zu archivierenden Dateien erstellen**

Aus Inventur + Task-4/6-Entscheidungen:

```
docs/archive/HYBRID_AI_SYSTEM.md              # Ollama, durch Dual-Engine ersetzt
docs/archive/IMPLEMENTATION_COMPLETE.md       # v2.0-Completion
docs/archive/ACTIVE_SECURITY_GUARDIAN.md      # v3.0, durch Security-Engine-v6 ersetzt
... (weitere 19 archive/-Dateien)
docs/plans/2026-03-06-shadowops-v4-design.md           # Ollama->Dual-Engine, durch v5.1 ersetzt
docs/plans/2026-03-06-shadowops-v4-implementation.md
docs/plans/2026-03-10-patch-notes-v2-design.md         # durch v6 ersetzt
docs/plans/2026-03-12-patch-notes-v3-design.md
docs/plans/2026-03-12-patch-notes-v3-implementation.md
docs/plans/2026-03-11-db-changelogs-design.md
docs/plans/2026-03-11-db-changelogs.md
docs/plans/2026-03-09-security-analyst.md              # durch Security-Engine-v6 ersetzt
docs/plans/2026-04-11-jules-secops-workflow.md         # Monolith, extrahiert in Task 4
docs/plans/2026-03-24-security-engine-v6.md            # Monolith, extrahiert in Task 4
docs/plans/2026-04-14-multi-agent-review.md            # Monolith, extrahiert in Task 4
docs/OVERVIEW.md                                        # durch docs/README.md ersetzt
docs/SECURITY_ANALYST.md                                # durch SecurityScanAgent ersetzt
```

**Step 2: Git-SHAs fuer jede Datei ermitteln**

```bash
for f in <datei>; do
  sha=$(git log -n 1 --pretty=format:%h -- "$f")
  echo "$f → $sha"
done
```

**Step 3: INDEX.md schreiben**

Tabelle nach Design-Doc D4. Pro Datei: Datum, Thema, Grund, Original-Pfad, Letzter-SHA, Ersatz-Pfad.

**Step 4: git rm aller Dateien aus Step 1**

```bash
git rm docs/archive/HYBRID_AI_SYSTEM.md docs/archive/IMPLEMENTATION_COMPLETE.md ...
git rm docs/plans/2026-03-06-*.md docs/plans/2026-03-10-*.md ...
git rm docs/OVERVIEW.md docs/SECURITY_ANALYST.md
```

**Step 5: Verifikation**

```bash
ls docs/archive/   # Nur INDEX.md + .gitkeep
ls docs/plans/     # Nur aktive Implementation-Plans (2026-04-14+2026-04-15)
```

**Step 6: Commit**

```bash
git add docs/archive/INDEX.md
git commit -m "docs: shadowops-bot Legacy-Docs archiviert (INDEX mit Git-SHAs)"
```

---

## Task 9: Front-Matter auf verbleibenden aktiven Docs

**Files:** Alle `.md`-Dateien unterhalb von `docs/` (ausser `docs/archive/INDEX.md`, der hat eigenes Format).

**Step 1: Kandidatenliste generieren**

```bash
find docs -name "*.md" ! -path "docs/archive/*"
grep -L "^---$" $(find docs -name "*.md" ! -path "docs/archive/*")
```

Zweite Zeile listet Dateien OHNE Front-Matter.

**Step 2: Fuer jede Datei Front-Matter ergaenzen**

Template:
```yaml
---
title: <aus H1 ableitbar>
status: active
last_reviewed: 2026-04-15
owner: CommanderShadow9
---
```

Bei Design-Docs zusaetzlich `version: vX` wenn erkennbar.

**Step 3: Verifikation**

```bash
for f in $(find docs -name "*.md" ! -path "docs/archive/*"); do
  head -1 "$f" | grep -q "^---$" || echo "FEHLT: $f"
done
```

Erwartet: keine Ausgabe (alle Dateien haben Front-Matter).

**Step 4: Commit**

```bash
git add docs/
git commit -m "docs: shadowops-bot YAML Front-Matter auf allen aktiven Docs"
```

---

## Task 10: CLAUDE.md auf neue Pfade aktualisieren

**Files:**
- Modify: `CLAUDE.md`

**Step 1: Referenzen in CLAUDE.md finden**

```bash
grep -n "docs/" CLAUDE.md
grep -n "plans/" CLAUDE.md
grep -n "guides/" CLAUDE.md
```

**Step 2: Pfade mappen**

Nach Task 5-7 existieren `docs/plans/`, `docs/guides/`, `docs/SECURITY_ANALYST.md` usw. nicht mehr. Pro Treffer neuen Pfad setzen.

**Step 3: `## Architektur-Historie`-Block in CLAUDE.md auf neuen Archiv-Pfad umlenken**

Derzeit: `docs/architecture-decisions.md`. Entscheidung: Diese Datei wird entweder ganz nach `docs/archive/INDEX.md` integriert oder als Legacy-Referenz gelassen. Beim Task-Execution klaeren.

**Step 4: Edit CLAUDE.md**

**Step 5: Verifikation**

```bash
for path in $(grep -oE "docs/[a-zA-Z0-9_/.-]+" CLAUDE.md); do
  test -e "$path" || echo "FEHLT: $path"
done
```

Erwartet: keine Ausgabe.

**Step 6: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: CLAUDE.md auf neue Lifecycle-Pfade aktualisiert"
```

---

## Task 11: Pfad-Referenzen im Code aktualisieren

**Files:**
- Potenziell: `README.md`, `scripts/*.sh`, `scripts/*.py`, `.github/workflows/*.yml`, `src/**/*.py`

**Step 1: Globaler Pfad-Scan**

```bash
grep -rn "docs/plans/" . --include="*.md" --include="*.py" --include="*.sh" --include="*.yml" --include="*.yaml" 2>/dev/null | grep -v "^\.git/"
grep -rn "docs/guides/" . --include="*.md" --include="*.py" --include="*.sh" --include="*.yml" --include="*.yaml" 2>/dev/null | grep -v "^\.git/"
grep -rn "docs/OVERVIEW.md\|docs/SECURITY_ANALYST.md\|docs/API.md" . 2>/dev/null | grep -v "^\.git/"
```

**Step 2: Pro Treffer Edit durchfuehren**

Neuer Pfad entsprechend Task-6/7-Mapping.

**Step 3: Verifikation**

Wiederholung von Step 1 — sollte leer sein.

**Step 4: Commit**

```bash
git commit -m "docs: shadowops-bot Pfad-Referenzen auf neue Doku-Struktur aktualisiert"
```

---

## Task 12: docs/README.md Links vervollstaendigen + Abschluss-Report

**Files:**
- Modify: `docs/README.md`

**Step 1: Im docs/README.md alle `[WIP]`-Marker aufloesen**

Jetzt, nach Tasks 2-11, existieren alle Zieldateien. Links updaten.

**Step 2: Tabelle in docs/README.md mit finalen Zahlen befuellen**

```bash
ls docs/architecture/ | wc -l
ls docs/operations/ | wc -l
...
```

**Step 3: Abschluss-Report fuer User im Chat**

Drei Bullets:
- Was geaendert (Zahlen: X Dateien verschoben, Y Monolithen gesplittet, Z Dateien archiviert)
- Was neu (docs/README.md, Lifecycle-Struktur, Front-Matter auf allen aktiven Docs)
- Was nicht angefasst (Zahlen: N Dateien ≥7/10 unveraendert, externe README-Badges intakt)

**Step 4: Commit**

```bash
git add docs/README.md
git commit -m "docs: shadowops-bot Doku-Refactor abgeschlossen (README finalisiert)"
```

**Step 5: Freigabe vom User einholen vor Uebergang zu Projekt 2 (GuildScout/ZERODOX/agents/libs)**

---

## Rollback-Strategie

- Pro Task existiert ein Commit. `git revert <sha>` rollt eine Task zurueck ohne andere zu beruehren.
- Bei Totalversagen: `git log --oneline --since="1 hour ago"` zeigt alle Refactor-Commits. `git reset --hard <letzter-vor-refactor>` stellt Ursprung her (nur nach User-Bestaetigung).

## Erfolgsdefinition

- `find docs -name "*.md" -size +20k` liefert maximal `docs/design/*` (vollstaendige Designs sind erlaubt gross zu sein)
- `find docs -name "*.md" ! -path "docs/archive/*" -exec head -1 {} \; | grep -c "^---$"` == Anzahl aktiver Docs (100% Front-Matter-Coverage)
- `docs/README.md` existiert und hat keine Broken Links
- `grep -r "docs/plans/2026-03-\|docs/guides/\|docs/OVERVIEW.md" .` liefert keine Treffer ausser im Archiv-INDEX
- CLAUDE.md hat keine toten Pfade
- User hat Abschluss-Report gesehen und freigegeben

---

**Nach Abschluss dieses Plans:** Neuer Plan fuer Projekt 2 (mit eigener IST-Bewertung), dann Projekt 3, usw.
