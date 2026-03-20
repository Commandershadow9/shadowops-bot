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

## Shared-Service Aenderungen (KRITISCH)
Bei Aenderungen an Shared-Services (Redis, PostgreSQL, Traefik) MUESSEN alle Konsumenten geprueft werden:
- **Redis-Auth:** `agents/scripts/seo-audit-cron.sh` nutzt `redis-cli -a PASSWORD` — bei Passwort-Aenderung updaten!
- **Redis-Auth:** SEO Agent config.yaml hat Redis-URL mit Passwort — bei Aenderung updaten!
- **Port-Bindings:** Docker-Container erreichen Host ueber 172.17.0.1 — NICHT auf 127.0.0.1 aendern!
- **Vorfaelle:** 2026-03-17 Bind-Address (11h Ausfall), 2026-03-18 Redis-Auth (SEO-Audit ausgefallen)
- **Checkliste vor Auth-Aenderungen:** `grep -r "redis-cli\|redis://\|5433\|6379" ~/agents/ ~/shadowops-bot/scripts/`

## Patch Notes Safety
- **Vorfall 2026-03-18:** Batcher-Referenz verloren → Einzelcommit-Patchnotes (KI-Halluzination)
- Globaler `min_commits` Check (Default 2) in `notifications_mixin.py` — blockiert ALLE Pfade
- Batcher Self-Healing: Fallback vom Bot-Objekt bei None-Referenz
- `/release-notes` erfordert min `cron_min_commits` (3) Commits
- Pro Projekt konfigurierbar: `patch_notes.min_commits` in config.yaml
- NIEMALS den globalen min_commits Check entfernen — er ist die letzte Verteidigungslinie

## Learning-System (agent_learning DB)
- DB-Passwort `agent_learn_2026` steht in `patch_notes_learning.py` DSN — nicht aendern ohne alle Referenzen
- `security_analyst` DB-DSN wird aus `config.yaml` (`security_analyst.database_dsn`) oder `SECURITY_ANALYST_DB_URL` env var geladen — KEIN Hardcoded-Fallback mehr
- Analyst `PROJECT_SECURITY_PROFILES` in `security_analyst.py` manuell pflegen bei Projektaenderungen
- `PROTECTED_PORT_BINDINGS` muss bei neuen Ports aktualisiert werden
- Token-Tracking: `_get_session_tokens()` misst Delta — NICHT manuell auf 0 setzen
- LearningNotifier postet in `🧠-ai-learning` Channel — Channel-ID muss in state.json existieren
