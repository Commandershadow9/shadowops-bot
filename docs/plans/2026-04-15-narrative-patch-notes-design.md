---
title: Narrative Patch Notes v1 — Design
status: active
last_reviewed: 2026-04-15
owner: CommanderShadow9
---

# Narrative Patch Notes v1 — Design

**Problem:** Aktuelle Pipeline v6 generiert technisch solide Patch Notes, aber ohne
Meilenstein-Gefühl, ohne "wieso weshalb warum"-Framing, ohne Dev-Insides. Zwei Versuche
(erst AI, dann manuelles Intro) fielen beide in generische Marketing-Muster zurück.

**Ziel:** Bei `update_size ∈ {mega, major}` soll der Web-Content wie ein richtiger
Gaming-Dev-Commentary klingen (Valve/Blizzard-Stil), gemischt mit Product-Storytelling
(Linear/Notion). Discord-Embed bleibt wie in der vorherigen Iteration (Hero-Stats +
Bold-TL;DR + Section-Header + Sub-Bullets).

---

## Entscheidungen aus Brainstorming (2026-04-15)

### D1: Referenz-Stil
**Mix Gaming-Dev-Commentary + Product-Story.** Anrede ("Dispatcher"/"Team"/"Ops"),
Vorher-Nachher-Framing, konkrete Spieler-Momente PLUS Dev-Insides zu Design-Entscheidungen.

### D2: Insider-Quelle
**Optional `release_notes.md` pro Projekt.** Dev schreibt zwischen Releases lose 2-5
Sätze rein. Pipeline liest wörtlich → übergibt als DEV-KONTEXT an die AI. Wenn leer:
kein Insides-Block im Output (bevor Halluzination in Kauf nehmen, lieber weglassen).

### D3: Automatik-Schienen
- **A (primär):** Projekt-CLAUDE.md bekommt Rule "*bei substantial changes → 1-3 Sätze
  in release_notes.md ergänzen*". AI-Assistenten laden die Rule beim Coden → erinnern
  sich selbst. Mayday-Pilot zuerst.
- **B (Backup):** Nach Archivierung wird die Datei nie komplett leer — Template-Kommentar
  erklärt Format + Beispiele. Pipeline filtert HTML-Kommentare raus beim Prompt-Bau.
- **C (verworfen):** Pre-Commit-Hook mit INFO-Message. YAGNI — wäre Commit-Lärm der nach
  einer Woche deaktiviert würde, und Schiene A fängt das eh ab.

### D4: Lifecycle der release_notes.md
1. Liegt im Projekt-Root als committed File (nicht gitignored — Team sieht was kommt).
2. Pipeline liest bei `generate_release()`.
3. Nach erfolgreichem Release (Distribute-Stage fertig): archiviert nach
   `docs/release-history/v<version>.md`, Original wird mit Template zurückgeschrieben.
4. Archivierung ist idempotent — wenn die Pipeline abbricht vor Archivierung,
   bleibt release_notes.md unverändert und der User kann den nächsten Release neu triggern.

### D5: Prompt-Architektur
Neue Sektionen im System-Prompt (nur für mega/major aktiv):

1. **Rolle + Stil** — Dev-Commentator, konkreter Anrede-Stil pro Template-Typ.
2. **Anti-Pattern-Liste** — explizite Liste was NICHT geht (Statistik-Listings,
   generische Hype-Phrasen, erfundene Insides, Feature-Bullets ohne Spieler-Moment).
3. **Structure-Block** — 6 Sektionen mit Length-Hints:
   - Hook (2-4 Sätze, direkte Anrede, Vorher-Nachher)
   - Leitidee (2-3 Sätze, die EINE Klammer über dem Release)
   - Drei Momente (3× ~60 Worte, "Der Moment wo...")
   - Was dahinter steckt (optional, nur wenn DEV-KONTEXT gefüllt)
   - Warum alles zusammen? (nur bei mega — erklärt die Kopplung)
   - Demnächst (1-2 Sätze Teaser, optional)
4. **Few-Shot-Beispiel** — gekürztes Muster (~500 Zeichen) im Prompt. LLMs imitieren
   Patterns zuverlässiger als sie Regeln folgen.
5. **Input-Block** — zusätzlich zu Commits/Groups:
   - Previous Version: Title + TL;DR aus ChangelogDB (für Vorher-Vergleich)
   - DEV-KONTEXT: release_notes.md wörtlich
   - Autor-Fakten: pro Feature-Group (wer hat wie viele Commits)
   - Zeitfenster: first-commit → last-commit ("über 3 Tage", "über 2 Wochen")
   - Dateien-Hotspots: Top-3 Files + Top-3 Verzeichnisse

### D6: Discord-Output bleibt
`discord_highlights` wird weiterhin als 3-6 Einzeiler generiert. Das Discord-Embed
(Hero-Stats, Bold-TL;DR, Section-Header, Sub-Bullets) wurde in der vorigen Iteration
bereits gebaut und bleibt unverändert.

### D7: Scope
**Nur mega/major bekommt die neue Narrative-Pipeline.** small/normal/big bleiben
kompakt und sachlich — für einen 5-Commit-Release ist Storytelling Overkill.

---

## Risiko-Mitigation

- **AI ignoriert Anti-Pattern-Liste:** Validate-Stage flaggt bestimmte Phrasen als
  Warning (keine Hard-Fail, nur User-Hint im Log). Iteration: Anti-Pattern-Liste
  verfeinern wenn Muster auftauchen.
- **release_notes.md leer:** Fallback auf pure Commit-Story. Kein Hype, aber auch
  kein Quatsch. Log-Warning dass Dev-Kontext fehlt.
- **Zu lang:** `length_limits[mega].max=7500` limitiert. Validate-Stage kürzt
  bei Bedarf (bestehende Logik).
- **Halluzination bei Insides:** Konstruktiv vermieden weil release_notes.md die einzige
  Quelle für personenbezogene Insides ist. AI hat keine Lizenz "Shadow hat drei
  Nächte..." zu erfinden wenn es nicht im DEV-KONTEXT steht.

---

## Test-Strategie

Qualität ist nicht regression-testbar, aber folgende technische Regression-Tests:

- `release_notes.md` Reader ignoriert HTML-Kommentare (Template wird nicht als Content gewertet).
- Prompt für mega enthält Anti-Pattern-Liste (String-Assert).
- Prompt für mega enthält Few-Shot-Beispiel (String-Assert).
- Previous-Version-Kontext wird aus ChangelogDB geladen (Mock-Test).
- Archiv-Flow: release_notes.md → docs/release-history/v<version>.md idempotent.

**Inhaltliche Qualität:** User liest den nächsten Release und gibt Feedback. Anti-
Pattern-Liste wird iterativ erweitert basierend auf tatsächlichen AI-Ausgaben.

---

## Rollout-Reihenfolge

1. Design-Doc (dieses File) + Commit
2. `base.py`: Prompt-Neubau (mega/major-spezifisch)
3. `base.py::_extra_context_section`: release_notes.md Reader
4. `collect.py` / `classify.py`: Input-Erweiterungen (previous_version, author_facts, time_window, hotspots)
5. `distribute.py`: Archive-Flow nach erfolgreichem Release
6. `/srv/leitstelle/app/CLAUDE.md`: Rule ergänzen (MayDay-Pilot)
7. `release_notes.md` Template auf MayDay seeden
8. Tests grün, Bot-Restart
9. Trigger `/release-notes mayday_sim` (oder nächster Daily-Cron)
