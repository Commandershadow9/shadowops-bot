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

## Patch Notes Safety (5 Schichten, seit 26.03.2026)
- **Vorfall 2026-03-18:** Batcher-Referenz verloren → Einzelcommit-Patchnotes (KI-Halluzination)
- **Vorfall 2026-03-25:** Design-Doc Commit als Feature halluziniert → v3.0.8 mit erfundenen Features
- **Schicht 1 — Commit-Klassifizierung:** `_classify_commit()` in `ai_patch_notes_mixin.py`. Design-Doc-Bodies werden abgeschnitten, Auto-Commits gruppiert, Merge gefiltert
- **Schicht 2 — Prompt-Regeln:** Typ-Tags in allen 4 Prompt-Pfaden (DE+EN). "[DESIGN-DOC] = NICHT implementiert"
- **Schicht 3 — Post-Validierung:** `_validate_ai_output()` erkennt Design-Doc-Leaks und entfernt sie automatisch
- **Schicht 4 — Batcher + min_commits:** Globaler Check (Default 2), Self-Healing, /release-notes min 3
- **Schicht 5 — Content Sanitizer:** Pfade, IPs, Secrets + changes[].details
- **Semantic Versionierung:** `_calculate_semver()` berechnet Version aus Commit-Typen — NICHT die KI
- **Kollisionsschutz:** `_ensure_unique_version()` verhindert doppelte Versionen
- **Projekt-Kontext:** `project_description` + `target_audience` pro Projekt in config.yaml
- NIEMALS den globalen min_commits Check entfernen — er ist die letzte Verteidigungslinie
- NIEMALS `_validate_ai_output()` deaktivieren — er faengt halluzinierte Features ab

## Learning-System (agent_learning DB)
- DB-Passwort `agent_learn_2026` steht in `patch_notes_learning.py` DSN — nicht aendern ohne alle Referenzen
- `security_analyst` DB-DSN wird aus `config.yaml` (`security_analyst.database_dsn`) oder `SECURITY_ANALYST_DB_URL` env var geladen — KEIN Hardcoded-Fallback mehr
- `PROJECT_SECURITY_PROFILES` in `security_engine/scan_agent.py` manuell pflegen bei Projektaenderungen
- `PROTECTED_PORT_BINDINGS` in `security_engine/scan_agent.py` muss bei neuen Ports aktualisiert werden
- Token-Tracking: `_get_session_tokens()` misst Delta — NICHT manuell auf 0 setzen
- LearningNotifier postet in `🧠-ai-learning` Channel — Channel-ID muss in state.json existieren

## Security Engine v6
- **SecurityDB** nutzt asyncpg Pool (min 2, max 5) — NICHT psycopg2
- **fix_attempts_v2** Tabelle: ALLE Fix-Ergebnisse (success, failed, no_op, skipped_duplicate) werden aufgezeichnet
- **remediation_status** Tabelle: Cross-Mode Lock — vor Fix immer claim_event() pruefen
- **CircuitBreaker**: 5 Failures pro Source → 1h Pause. NICHT manuell resetten ohne Grund
- **Phase-Types sind verbindlich**: recon/verify/monitor duerfen NICHTS aendern (read-only)
- **NoOp-Detection**: Fail2banFixerAdapter prueft Config bevor geschrieben wird — NICHT umgehen
- **LearningBridge**: Separate DB-Connection zur agent_learning DB — Passwort in config.yaml

## SecurityScanAgent (ersetzt DeepScanMode + alten SecurityAnalyst)
- **scan_agent.py**: Autonomer Agent in `security_engine/`, nutzt SecurityDB direkt (kein AnalystDB)
- **Activity Monitor**: `security_engine/activity_monitor.py` — prueft SSH, Git, Claude, Discord
- **Prompts**: `security_engine/prompts.py` — ANALYST_SYSTEM_PROMPT (taeglich), WEEKLY_DEEP_PROMPT (Sonntag), REFLECTION_PROMPT (nach jeder Session), FIX_SESSION_PROMPT
- **Deterministische Pre-Checks**: UFW, Docker, Fail2ban, CrowdSec, Ports, Services — VOR AI-Analyse gesammelt
- **Post-Fix Integrity Check**: Prueft Container, Ports, Services nach jedem Fix
- **Content-Deletion-Guard**: Warnt bei >20 Zeilen Netto-Loeschung in Repos
- **Post-Scan Reflection**: AI bewertet eigene Arbeit, generiert Insights (nach JEDER Session)
- **Weekly-Deep-Scan**: Sonntag Nacht automatisch (nur Claude), manuell: `touch data/force_deep_scan`
- **Force-Scan Flags**: In `data/` (NICHT `/tmp/` wegen PrivateTmp=true in systemd)
- **PROJECT_SECURITY_PROFILES**: In `scan_agent.py` — bei Projektaenderungen manuell pflegen
- **PROTECTED_PORT_BINDINGS**: In `scan_agent.py` — bei neuen Ports aktualisieren
- **Fix-Phase nutzt Cross-Mode-Lock**: claim_event/release_event fuer jedes Finding
- **Alter Analyst** (`analyst/security_analyst.py`): Wird NICHT mehr von Engine gestartet, bleibt vorerst als Referenz
