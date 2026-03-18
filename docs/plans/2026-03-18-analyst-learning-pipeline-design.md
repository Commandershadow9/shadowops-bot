# Analyst Learning Pipeline — Design-Dokument

**Datum:** 2026-03-18
**Status:** Implementiert
**Ausloeser:** Analyst-verursachter 11h-Ausfall (Bind-Address Fix d9d7e86)

## Problem

Der Security Analyst akkumuliert Wissen, aber lernt nicht wirklich:
- Keine Verifikation ob Fixes noch aktiv sind
- Kein Tracking welche Bereiche gecheckt wurden
- Keine Qualitaetsbewertung eigener Findings
- Kein Lernen aus Fix-Erfolgen/-Misserfolgen

## Loesung: Full Learning Pipeline

### Neue DB-Tabellen (4 Stueck)

| Tabelle | Zweck |
|---------|-------|
| `fix_attempts` | Jeden Fix-Versuch aufzeichnen (Ansatz, Commands, Ergebnis, Seiteneffekte) |
| `fix_verifications` | Periodische Pruefung ob Fixes noch aktiv sind |
| `finding_quality` | Selbstbewertung: Confidence, Discovery-Methode, False Positives |
| `scan_coverage` | Welche Bereiche wurden in welcher Session gecheckt |

### Pre-Session Maintenance

Vor jeder Analyst-Session laufen 3 Tasks:

1. **Git-Activity Sync** — Schreibt Commit-Aktivitaet aller Projekte in Knowledge-DB
2. **Fix-Verifikation** — Prueft bis zu 10 unverified Fixes der letzten 14 Tage
3. **Knowledge-Decay** — Reduziert Confidence von altem Wissen (-5% pro Lauf wenn >14 Tage)

### Datenfluss im Lernzyklus

```
Session N:
  _pre_session_maintenance()
    ├── _sync_git_activity_to_db()     → "Was hat sich geaendert?"
    ├── _verify_recent_fixes()          → "Halten meine Fixes noch?"
    └── decay_knowledge_confidence()    → "Ist mein Wissen noch frisch?"

  build_ai_context() liefert:
    ├── Fix-Effektivitaet pro Kategorie (Erfolgsraten)
    ├── Coverage-Luecken (>7 Tage nicht gecheckt)
    ├── Finding-Qualitaet (False-Positive-Rate)
    └── Alles bisherige (Knowledge, Patterns, IP-Rep, Stats)

  Scan-Session:
    └── Output: areas_checked, areas_deferred, finding_assessments

  _process_results():
    ├── record_scan_coverage()          → Coverage-DB
    └── assess_finding_quality()        → Quality-DB

  Fix-Phase:
    └── record_fix_attempt()            → Fix-Attempts-DB
        ├── result: success → verified_at in naechster Session
        ├── result: failure → Ansatz gespeichert, naechstes Mal anders
        └── result: partial → Lernen was funktioniert hat

Session N+1:
  _verify_recent_fixes() prueft Session N Fixes
    ├── still_valid=TRUE  → verified_at aktualisiert
    └── still_valid=FALSE → Finding re-opened + Discord-Alert + Pattern
```

### Schema-Erweiterungen

`analyst_session.json` neue Felder:
- `areas_checked`: Liste der geprueften Bereiche
- `areas_deferred`: Uebersprungene Bereiche
- `finding_assessments`: Selbstbewertung pro Finding (confidence, discovery_method, is_actionable, is_false_positive)

### Kontext-Erweiterungen in build_ai_context()

Drei neue Sektionen die automatisch aus der DB in den Analyst-Prompt fliessen:
- **Fix-Effektivitaet** — Erfolgsraten pro Kategorie (90 Tage)
- **Scan-Luecken** — Bereiche >7 Tage nicht gecheckt
- **Finding-Qualitaet** — False-Positive-Rate und Durchschnitts-Confidence

### Architektur-Prinzip

**Schlanker Prompt + wachsende Knowledge-DB.**

Der Prompt gibt nur den Auftrag. Alles Wissen kommt aus der DB via `build_ai_context()`.
Die DB kann unbegrenzt wachsen und wird durch Confidence-Decay aktuell gehalten.

### Voraussetzungen

- PostgreSQL security_analyst DB mit 4 neuen Tabellen
- ContextManager wird an SecurityAnalyst durchgereicht (seit diesem Release)
- Analyst muss `areas_checked` und `finding_assessments` im Output liefern

### Dateien geaendert

| Datei | Aenderung |
|-------|-----------|
| `src/integrations/analyst/analyst_db.py` | 8 neue Methoden (fix_attempts CRUD, quality, coverage, decay) + 3 Sektionen in build_ai_context() |
| `src/integrations/analyst/security_analyst.py` | _pre_session_maintenance(), _verify_recent_fixes(), _notify_regressions(), Fix-Recording in Fix-Phase, Quality+Coverage in _process_results() |
| `src/integrations/analyst/prompts.py` | Selbstkontrolle-Sektion im Scan-Prompt, failed-Action + commands im Fix-Prompt |
| `src/schemas/analyst_session.json` | areas_checked, areas_deferred, finding_assessments |
| `src/bot.py` | context_manager an SecurityAnalyst durchreichen |
