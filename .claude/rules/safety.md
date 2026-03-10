# Safety Rules

## Dokumentations-Pflege
Wenn Dateien hinzugefuegt, geloescht oder verschoben werden:
- CLAUDE.md "Wo liegt was?" Tabellen SOFORT aktualisieren
- Neue Cogs/Integrationen in die entsprechende Tabelle eintragen
- Geloeschte Dateien aus Tabellen entfernen

## Code-Aenderungen
- Vor Aenderungen an laufenden Services: `sudo systemctl status shadowops-bot` pruefen
- Schema-Aenderungen: ALLE Properties muessen in `required` stehen (Codex Structured Output)
- Signal-Handler: SIGTERM fuer Shutdown, SIGUSR1 fuer Log-Rotation — beide in setup_hook()

## Testing
- Tests EINZELN ausfuehren: `pytest tests/unit/test_NAME.py -x`
- NIEMALS `pytest tests/` ohne `-x` Flag (stoppt bei erstem Fehler)
- KEIN paralleles Testing (kein pytest-xdist auf 8 GB VPS)

## Secrets
- `config/config.yaml` enthaelt Discord Token, GitHub Token, API Keys
- NIEMALS in Git committen — steht in .gitignore
- Template: `config/config.example.yaml`
