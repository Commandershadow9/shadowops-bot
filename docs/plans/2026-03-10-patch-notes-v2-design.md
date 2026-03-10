# Patch Notes v2 — Community Edition

## Zusammenfassung

Überarbeitung des Patch-Notes-Systems für bessere Community-Freundlichkeit,
SEO-Web-Export, Text-Feedback, Git-Stats und Batching kleiner Patches.

## Änderungen

### Schema (`src/schemas/patch_notes.json`)
- Neue Felder: `tldr`, `stats`, `web_url`

### Neue Prompt-Variante `community_v1`
- TL;DR am Anfang
- Benefit-fokussiert
- Stats-Sektion bei ≥5 Commits

### Git-Stats-Sammlung
- Lines added/removed, Files changed, Contributors
- Berechnet aus Commit-Daten und `git diff --shortstat`

### Dual-Format Output
- **Discord:** Kurzformat mit TL;DR + Top-Highlights + Stats-Zeile + Web-Link
- **Web:** Ausführliches JSON/Markdown mit SEO-Keywords

### Text-Feedback via Discord Modal
- Button "📝 Feedback" unter Patch Notes
- Discord Modal mit TextInput
- Gespeichert als `feedback_type: 'text'` im Trainer

### Batching kleiner Patches
- Patches ohne Version-Bump werden gesammelt
- Release bei 3+ gesammelten oder manuellem Trigger
- Persistiert in `~/.shadowops/patch_notes_training/pending_batch.json`

### Web-Exporter
- JSON + Markdown Export pro Release
- SEO-optimiert mit Keywords und Meta-Description
- Konfigurierbar pro Projekt (`changelog_output_dir`)
- GuildScout: Öffentlich einsehbar (nicht hinter Login)

### CI-Stats (Lokal)
- Script `scripts/run_tests_with_coverage.sh`
- Schreibt Ergebnisse nach `data/test_results.json`
- Bot liest Datei für Stats in Patch Notes

## Neue Dateien
- `src/integrations/patch_notes_batcher.py`
- `src/integrations/patch_notes_web_exporter.py`
- `scripts/run_tests_with_coverage.sh`

## Geänderte Dateien
- `src/schemas/patch_notes.json`
- `src/integrations/prompt_ab_testing.py`
- `src/integrations/github_integration/ai_patch_notes_mixin.py`
- `src/integrations/github_integration/notifications_mixin.py`
- `src/integrations/patch_notes_manager.py`
- `src/integrations/patch_notes_feedback.py`
- `config/config.example.yaml`
