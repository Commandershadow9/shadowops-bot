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

## Patch Notes Pipeline v6 (seit 2026-04-13, ersetzt v5 Mixins)
- **Code:** `src/patch_notes/` — 5-Stufen State Machine (~2100 Zeilen, 101 Tests)
- **Vorfall-Historie:** v5 hatte Commit-Cap (50), 5 Version-Quellen, halluzinierte Features (v2.9.2, v3.0.8)
- **Safety-Checks (Stufe 4 — validate.py):**
  - `check_feature_count()` — AI-Features ≤ echte Feature-Gruppen × 2
  - `check_design_doc_leaks()` — mit Smart False-Positive-Schutz
  - `strip_ai_version()` — generisches SemVer-Regex (faengt AI-erfundene Versionen)
  - `sanitize_content()` — nutzt bestehenden ContentSanitizer (Pfade, IPs, Secrets)
  - `normalize_umlauts()` — ae→ä, oe→ö (nur bei language=de)
  - `enrich_changes_with_authors()` — Inline-Credits aus Git-Daten (nicht AI)
- **Versionierung (versioning.py):** NUR Changelog-DB + SemVer. EINE Quelle, KEIN Git-Tag, KEINE AI-Version
- **Commit-Gruppierung (grouping.py):** ALLE Commits, KEIN Cap. PR-Labels ueberschreiben Commit-Prefix
- **Templates (templates/):** `gaming` (MayDay), `saas` (GuildScout, ZERODOX), `devops` (ShadowOps, AI-Agent). Classification-Rules DE+EN werden IMMER angehaengt
- **Team-Credits:** `TEAM_MAPPING` in `stages/classify.py`. Bei neuen Teammitgliedern pflegen
- **Concurrency:** asyncio Lock in `pipeline.py` + Circuit Breaker (5 Fehler → 1h Pause)
- **Crash-Resilience:** Pipeline-State nach jeder Stufe persistiert. Resume nach Restart
- **Self-Healing:** Leere Commits → aus Git seit letztem Release. Kein Commit verloren nach Restart
- **Release-Modi:** Daily (22:00, ≥15) + Weekly (Sonntag 20:00, ≥3). Commits akkumulieren
- NIEMALS `check_feature_count()` oder `check_design_doc_leaks()` deaktivieren
- NIEMALS die Classification-Rules (`_CLASSIFICATION_RULES_DE/EN`) aus templates/base.py entfernen
- NIEMALS die Commit-Gruppierung durch ein Cap ersetzen — das war die Ursache der v5-Probleme
- Conventional Commit Hook auf allen 5 Projekten. Re-Deploy: `scripts/deploy-commit-hook.sh --all`

## Learning-System (agent_learning DB)
- **KEINE hardcoded DB-Passwörter im Source Code!** Alle DSNs kommen aus `config.yaml` oder Env-Vars:
  - `security_analyst` DB: Config-Property `config.security_analyst_dsn` (Env: `SECURITY_ANALYST_DB_URL`, Config: `security_analyst.database_dsn`)
  - `agent_learning` DB: Config-Property `config.agent_learning_dsn` (Env: `AGENT_LEARNING_DB_URL`, Config: `agent_learning.database_dsn`)
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

## Pipeline v6 Hardening
- **Circuit Breaker** in `pipeline.py`: 5 AI-Failures → 1h Pause. Threshold NICHT aendern
- **asyncio Lock** in `pipeline.py`: Serialisiert ALLE Pipeline-Runs. Verhindert Race Conditions
- **State-Persistenz**: `data/pipeline_runs/{project}.json` nach jeder Stufe. NICHT manuell loeschen
- **Message-ID Tracking**: `_persist_message_ids()` in distribute.py → state.json (FIFO, max 50)
- **`retract_patch_notes(project, version)`**: Loescht Messages cross-guild. Import: `from patch_notes import retract_patch_notes`
- **Pipeline-Metriken**: `METRICS|patch_notes_pipeline|{json}` — Format NICHT aendern (Monitoring parst es)

## Jules SecOps Workflow (seit 2026-04-11)
- **NIEMALS `issue_comment` Events für Auto-Reviews whitelisten** — das war die PR #123 Hauptursache
- **Single-Comment-Edit Strategie ist Pflicht** — neuer Comment pro Iteration triggert Webhook-Loop
- **`compute_verdict` ist deterministisch, nicht AI-überschreibbar** — schützt vor Confidence-Oszillation
- **max_iterations: 5 und max_hours_per_pr: 2 sind harte Limits** — bei Änderung Design-Doc Anhang A reviewen
- **Circuit-Breaker 20/h pro Repo NIE erhöhen** ohne Incident-Analyse
- **Bei Jules-Workflow-Änderungen IMMER `test_jules_pr123_regression.py` laufen lassen**
- **Claude-Opus Timeout-Verhalten:** Opus CLI hängt manchmal (Prozesse leben >10min, asyncio.wait_for greift NICHT auf subprocess stdin). Fallback auf Sonnet ist Pflicht — `review_pr` muss immer beide Modelle durchprobieren
- **Intelligente Modell-Wahl (`ai_engine.review_pr`):** Opus bei Security-Keywords ODER Diff > 3000. NICHT blind Opus für alles — Sonnet reicht für Logger-Swaps/Tests und ist schneller
- **JSON-Parser-Fix:** Claude fügt manchmal Text vor/nach dem JSON ein. `review_pr` muss `{...}`-Block per Klammer-Matching extrahieren, nicht nur `json.loads(raw)`
- **Label-Setzung via REST API** — `gh pr edit --add-label` hat GraphQL-Bug bei Repos mit deprecated Projects-Classic (z.B. ZERODOX). Stattdessen: `gh api repos/{}/issues/{}/labels POST`
- **Label Auto-Create:** Wenn `claude-approved` im Ziel-Repo fehlt, muss der Bot es via `gh api repos/{}/labels POST` anlegen
- **Jules-Iteration:** Revision-Comments brauchen `@google-labs-jules` Mention damit Jules den Blocker fixt — ohne Mention arbeitet Jules nicht automatisch weiter
- **Discord-Logger-API:** `_send_to_channel(channel_key, message, embed=None)` — `message` ist Positional-Arg, bei Embed-only als `message=""` übergeben
- **Jules API Integration:** API-Key aus `config.yaml` (`jules_workflow.api_key`). NIEMALS in Git committen. Format: `sourceContext.source = "sources/github/{owner}/{repo}"` + `sourceContext.githubRepoContext.startingBranch = "main"`
