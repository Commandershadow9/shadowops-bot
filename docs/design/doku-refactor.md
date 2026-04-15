---
title: Doku-Refactoring aller 5 Projekte — Design-Doc
status: active
version: 1.0
last_reviewed: 2026-04-15
owner: CommanderShadow9
related: []
---

# Doku-Refactoring — Alle 5 Projekte

## Kontext

Der VPS hostet 5 aktive Projekte (shadowops-bot, GuildScout, ZERODOX, agents, libs). Ueber die letzten Monate sind Dokumentation und Design-Artefakte unkoordiniert gewachsen. Eine Inventur (2026-04-15) hat fuer shadowops-bot allein 24 Design-Plans mit ~680 KB, drei Monolithen > 50 KB, Dreifach-Dokumentation der Security-Engine und fehlende Standard-Artefakte (Glossar, Runbooks, Navigation) ergeben. Die anderen Projekte sind unterschiedlich weit — einige sehr gut gepflegt, andere chaotisch.

Gleichzeitig arbeitet Claude (dieses Tool) intensiv mit der Doku: CLAUDE.md wird bei jedem Turn geladen, `docs/plans/` wird bei Feature-Entwicklung gelesen, `.claude/rules/` bei Safety-Checks. Schlechte Navigation, Monolithen ohne Anchor und fehlende Metadaten kosten Context-Budget und fuehren zu Fehlentscheidungen (veraltete Info wird als aktuell behandelt).

## Ziel

Einheitliches, AI-konformes, enterprise-taugliches Doku-System ueber alle 5 Projekte — ohne gute bestehende Doku kaputtzumachen, und mit klarer Rollback-Faehigkeit pro Schritt.

## Nicht-Ziele (YAGNI)

- Keine zentrale Doku in `~/docs/` (Harmonisierungs-Level B wurde explizit verworfen).
- Keine oeffentlichen Doku-Sites (MkDocs, Docusaurus). Markdown im Repo reicht.
- Keine automatisierten Doku-Generatoren (Sphinx, TypeDoc). Manuell geschriebene Docs bleiben.
- Keine Migration bestehender externer Links (z.B. GitHub README-Badges) — nur interne Umzuege.
- Kein Refactoring von Code-Dateien, nur Markdown und Struktur.

## Entscheidungen

### D1 — Topologie pro Projekt: Lifecycle-Struktur

Jedes Projekt, das einen vollen Refactor bekommt, erhaelt folgende `docs/`-Struktur:

```
docs/
  README.md              Index + Semantic Map (einheitliches Template)
  architecture/          Wie funktioniert das System (Explanation)
  operations/            Wie wird es betrieben (Deploy, Monitoring, Backup, Secrets)
  runbooks/              Incident-Response, Schritt-fuer-Schritt
  design/                Aktive Design-Docs (ehem. plans/)
  reference/             Nachschlagen: API, Config-Keys, Glossar
  adr/                   unveraendert, fortlaufend nummeriert
  archive/
    INDEX.md             Git-SHAs + Themen entfernter Dateien
```

**Begruendung:** Diataxis waere der Industriestandard, aber fuer Infrastruktur-/Bot-Projekte passt Lifecycle besser. Die 4 Top-Level-Kategorien entsprechen natuerlichen Arbeitsmodi: verstehen (architecture/) → betreiben (operations/) → reagieren (runbooks/) → planen (design/). Nachschlagen (reference/) ist querschnittlich.

### D2 — YAML Front-Matter als Metadaten-Standard

Jede Markdown-Datei in `docs/` bekommt Front-Matter:

```yaml
---
title: Kurzer, eindeutiger Titel
status: active                    # active | draft | superseded | archived
version: v6                       # optional, nur wenn versionierbar
last_reviewed: YYYY-MM-DD
owner: CommanderShadow9 | Claude
superseded_by: pfad/zum/nachfolger.md  # nur bei status=superseded
related: [adr/007, runbooks/incident-x.md]  # optional
---
```

**Nutzen fuer AI:** `grep -l "status: active" docs/` filtert in einer Sekunde nur aktuelle Docs. `last_reviewed` zeigt Veraltungsgefahr. `superseded_by` verhindert Fehlnavigation.

### D3 — docs/README.md als Semantic Map

Einheitliches Template fuer alle 5 Projekte:

```markdown
# <Projekt> — Dokumentation

<3-Saetze-Elevator-Pitch>

## Ich will...

- **...das System verstehen** → [architecture/](architecture/)
- **...deployen oder konfigurieren** → [operations/](operations/)
- **...auf einen Incident reagieren** → [runbooks/](runbooks/)
- **...ein Feature designen** → [design/](design/)
- **...etwas nachschlagen** → [reference/](reference/)
- **...eine frueheres Entscheidung verstehen** → [adr/](adr/)

## Status

| Kategorie | Dateien | Letztes Review |
|-----------|---------|----------------|
| architecture | N | YYYY-MM-DD |
| ...

## Aktive Haupt-Docs

- [Kurzname](pfad.md) — 1-Satz-Beschreibung
```

### D4 — Archive-Policy: Hybrid (Git-rm + INDEX)

Veraltete Dateien werden per `git rm` entfernt (NICHT in `docs/archive/` belassen). Pro Projekt fuehrt `docs/archive/INDEX.md` Buch:

```markdown
# Archiv-Index

| Datum | Themen-Name | Grund | Original-Pfad | Letzter Commit-SHA | Ersatz |
|-------|-------------|-------|---------------|--------------------|--------|
| 2026-04-15 | Patch Notes v2/v3-Design | v6 aktiv | docs/plans/2026-03-10-patch-notes-v2-design.md | abc1234 | docs/design/patch-notes-v6.md |
```

**Begruendung:** Reduziert Rauschen bei `grep -r "X" docs/` massiv (kein Treffer in veralteten Dokumenten). History bleibt via `git show abc1234:docs/plans/2026-03-10-patch-notes-v2-design.md` zugaenglich.

### D5 — Adaptive IST-Bewertung (Safety-Mechanismus)

Vor jedem Projekt bewerte ich drei Achsen auf 1-10:

| Achse | Was wird bewertet | Signale |
|-------|-------------------|---------|
| Vollstaendigkeit | Sind alle Standard-Artefakte da? | README, Deploy-Doc, Monitoring, Troubleshooting, Glossar |
| Aktualitaet | Wie alt/gepflegt? | Letztes Commit-Datum, "TODO/geplant"-Plaster, Status-Felder |
| Struktur | Ist es navigierbar? | Index-Datei, Monolithen-Zaehlung (>500 Zeilen), klare Unterordner |

**Schwellenwerte:**
- **Gesamt ≥ 7/10 = Leicht-Touch**: Front-Matter nachruesten, README-Index vereinheitlichen, keine Umzuege, keine neue Struktur.
- **< 7 = Voller Refactor**: Lifecycle-Topologie einfuehren, Monolithen splitten, archivieren.

Die Bewertung wird pro Projekt vor Arbeitsbeginn als Tabelle im Chat gezeigt und kurz begruendet.

### D6 — Reihenfolge

1. **shadowops-bot** — zuerst (tiefste Kenntnis, hier wohne ich, Learning-Effekt fuer Folge-Projekte).
2. **Folge-Projekte aufsteigend nach Score** — schlechteste Doku zuerst, beste zuletzt. Ergebnis der IST-Bewertung bestimmt die Reihenfolge konkret.

### D7 — Safety-Regeln (hart, nicht verhandelbar)

1. **Atomare Commits pro Projekt pro Phase**: `docs: <projekt> — phase N — <kurzbeschreibung>`. Jede Phase ist einzeln reverttierbar.
2. **Pfad-Referenz-Scan vor Umzug**: Bevor eine Datei bewegt wird, `grep -r "<alter-pfad>" .` im gesamten Projekt-Repo. Treffer in Scripts, CI-Files, README werden aktualisiert.
3. **Tabu-Zone**: `.env`, `config.yaml`, `data/`, `logs/`, `.venv/`, `node_modules/`, `dist/`, Datenbanken — **niemals** anfassen. Keine Ausnahme.
4. **README mit externen Badges/Links**: Nur umformatieren, nie loeschen. GitHub-Badges, Shields, externe Links bleiben.
5. **Pro Projekt Abschluss-Report an User**: Drei Bullets — was geaendert, was archiviert, was neu. Kein Sprung zum naechsten Projekt ohne Freigabe.
6. **Kein grosser Wurf ohne Zwischenstand**: Max 30 Minuten am Stueck, dann Commit und Pause. Verhindert unreversible "halb refactored"-Staende.

### D8 — Neue Standard-Artefakte (nur bei vollem Refactor)

Was Enterprise-Level erwartet und potenziell fehlt — wird projektindividuell entschieden:

- `docs/operations/deployment.md` — wie wird deployed
- `docs/operations/monitoring.md` — welche Dashboards, Alerts, Healthchecks
- `docs/operations/secrets.md` — wo liegen Secrets, Rotation-Strategie
- `docs/runbooks/incident-<name>.md` — pro bekanntem Incident-Typ eine Runbook
- `docs/reference/glossary.md` — Projekt-spezifische Begriffe
- `docs/reference/config.md` — alle Config-Keys dokumentiert

Nur anlegen, wenn die Inhalte existieren (aus bestehender Doku extrahierbar). Niemals leere Stubs.

## Qualitaetskriterien fuer "erledigt" (pro Projekt)

- [ ] IST-Bewertung dokumentiert und in Chat gezeigt
- [ ] docs/README.md folgt einheitlichem Template
- [ ] Alle aktiven Docs haben YAML Front-Matter
- [ ] Keine Datei > 500 Zeilen ohne Anchor-TOC
- [ ] docs/archive/INDEX.md listet alle entfernten Dateien mit Git-SHA
- [ ] Pfad-Referenzen im Code aktualisiert (grep clean)
- [ ] CLAUDE.md zeigt auf neue Pfade
- [ ] Abschluss-Report im Chat, kurze Freigabe vom User

## Offene Punkte (werden waehrend Umsetzung geklaert)

1. **Globale User-Rules** (`~/.claude/rules/*.md`, `~/CLAUDE.md`): Gelten fuer ALLE Projekte, nicht pro Repo. Strategie: erst alle Projekte refactoren, DANN globale Rules angleichen (separate Phase am Ende). Risiko sonst: Kollision zwischen projekt-spezifischen und globalen Regeln waehrend Umbau.
2. **ADR-Nummerierung**: Bleibt fortlaufend pro Projekt (kein Reset). Falls Konflikte, neue ADRs bekommen erste freie Nummer.
3. **Datum-Praefix in Design-Dateinamen**: Bleibt (`YYYY-MM-DD-thema-design.md`), weil chronologische Sortierung via `ls` wertvoll ist.

## Rollback

Jeder Commit ist ein Rollback-Punkt (`git revert <sha>`). Bei Totalversagen (unwahrscheinlich): `git reset --hard <letzter-clean-commit>` in lokalem Repo, aber nur nach User-Bestaetigung.

## Naechste Schritte

1. Design-Doc commiten.
2. `writing-plans` Skill aufrufen fuer Implementierungsplan shadowops-bot.
3. IST-Bewertung shadowops-bot durchfuehren und zeigen.
4. Umsetzung in Phasen mit Zwischen-Commits.
