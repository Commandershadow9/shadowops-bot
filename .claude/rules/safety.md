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
- **Team-Credits:** `TEAM_MAPPING` in `stages/classify.py`. Bei neuen Teammitgliedern PFLICHT zu pflegen — Git-Username(s) in lowercase als Key, `(Display-Name, Rolle)` als Value. Landet als Inline-Credit pro Change (AI-Output + Discord-Embed + Page /changelog/[version]) und als Team-Footer. Unbekannte Git-Autoren bekommen automatisch `Contributor`-Rolle.
- **Concurrency:** asyncio Lock in `pipeline.py` + Circuit Breaker (5 Fehler → 1h Pause)
- **Crash-Resilience:** Pipeline-State nach jeder Stufe persistiert. Resume nach Restart
- **Self-Healing:** Leere Commits → aus Git seit letztem Release. Kein Commit verloren nach Restart
- **Release-Modi:** Daily (22:00, ≥15) + Weekly (Sonntag 20:00, ≥3). Commits akkumulieren
- NIEMALS `check_feature_count()` oder `check_design_doc_leaks()` deaktivieren
- NIEMALS die Classification-Rules (`_CLASSIFICATION_RULES_DE/EN`) aus templates/base.py entfernen
- NIEMALS die Commit-Gruppierung durch ein Cap ersetzen — das war die Ursache der v5-Probleme
- NIEMALS den `RuntimeError` Abort in `validate.py` bei `ai_result=None` entfernen — sonst Endlosschleife mit leeren DB-Einträgen (Vorfall 2026-04-14: 33 leere Einträge in 4h)
- NIEMALS `discord_highlights` aus dem Schema in `_build_structured_wrapper` entfernen — `AIEngine.generate_structured_patch_notes()` returnt None ohne dieses Feld
- Bei AIEngine-Calls: NICHT raten. Methoden sind: `generate_structured_patch_notes()`, `get_raw_ai_response()`, `query()` (existiert nur in Provider-Klassen, nicht in AIEngine)
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

## Multi-Agent Review Pipeline (seit 2026-04-14)
- **Code:** `src/integrations/github_integration/agent_review/` — 10 Module, 244 Tests
- **Rollout-Philosophie:** Adapter-Pattern ist ADDITIV. Legacy-Jules-Pfad (`_jules_is_jules_pr` → `_jules_run_review`) bleibt primaerer Pfad. Detector laeuft parallel als Diagnostik + fuer `_handle_approval_with_adapter`.
- **Feature-Toggle:** `agent_review.enabled: false` default. NIE ohne Live-Monitoring auf true setzen.
- **Auto-Merge:** `agent_review.auto_merge.enabled: false` default. SEPARATER Toggle von `agent_review.enabled`. Per-Project-Override via `auto_merge.projects.{name}.allowed`. Rollback < 30s.
- **JulesAdapter.merge_policy:** Security-Labels/Keywords -> IMMER MANUAL. Tests-only + <200 Additions -> AUTO. `sicherheitsdienst` -> IMMER MANUAL.
- **SeoAdapter.merge_policy:** Content-Only-Pfade (.md/.mdx/sitemap/robots/blog-data) + <50 Files + in_scope=true -> AUTO. Irgendein Pfad in DANGEROUS_PATHS (package.json, next.config, layout.tsx, prisma/schema.prisma, Dockerfile) -> MANUAL.
- **CodexAdapter.merge_policy:** IMMER MANUAL — Code-Fixes brauchen menschliche Verifikation, auch bei approved Verdict.
- NIEMALS `adapter.merge_policy` umgehen — sie ist die einzige Quelle der Wahrheit fuer Auto-Merge-Entscheidungen. Der Scan-Agent darf niemals direkt `gh pr merge` aufrufen ohne Adapter-Check.
- NIEMALS `_gh_auto_merge_squash` ausserhalb von `_handle_approval_with_adapter` aufrufen — der Flow garantiert dass OutcomeTracker mitschreibt.
- NIEMALS `CONFIDENCE_THRESHOLD = 0.8` im Detector senken ohne Review-Plan — der Wert schuetzt vor Adapter-Kollisionen (SEO erkennt zufaellig einen Jules-PR).
- NIEMALS `discord_highlights`-analoge Pflicht-Felder aus Adapter-Schemas entfernen ohne die jules_review.json Schema-Validierung zu checken.
- **AgentAdapter als Schnittstelle:** Neue Agenten brauchen NUR eine neue Klasse in `adapters/` + Test-Suite + `_get_adapter_toggles()` Update + Config-Flag. KEINE Mixin-Aenderung.
- **Scheduled Tasks-Cascade:** Alle 4 neuen Tasks (`agent_task_queue_scheduler`, `agent_suggestions_poller_task`, `agent_outcome_check_task`, `agent_daily_digest_task`) starten NUR wenn `gh._agent_review_enabled=True`. NICHT manuell starten.
- **OutcomeTracker Revert-Detection ist KONSERVATIV:** `_check_pr_reverted` returnt False bei `gh api` Fehler (lieber False-Positive "healthy" als False-Positive "reverted"). Diese Default-Entscheidung NICHT umkehren.
- **Queue-Scheduler Budget-Check:** `min(15 - concurrent, 100 - released_24h)` — BEIDE Limits pruefen, nie nur eines. Das Concurrent-Limit kommt von Jules API, das 24h-Limit aus der Queue-DB.
- **Bei Multi-Agent-Workflow-Aenderungen IMMER `test_jules_pr123_regression.py` + `tests/unit/agent_review/` komplett laufen lassen.**
- **Rollback-Sequenz bei Incident:**
  1. `agent_review.auto_merge.enabled: false` (stoppt Auto-Merge sofort)
  2. Wenn weiter problematisch: `agent_review.enabled: false` (deaktiviert komplette Pipeline)
  3. Bot-Restart via `scripts/restart.sh` — < 30s bis Legacy-Verhalten wiederhergestellt

## SecurityScanAgent → Queue-Delegation (seit 2026-04-14)
- **Code:** `src/integrations/security_engine/scan_agent.py` — `_should_delegate_to_jules()` + `_enqueue_jules_fix()`
- **Autonomie-Schleife:** Wenn `agent_review.enabled=true`, delegiert der ScanAgent Code-Security-Findings direkt an Jules statt GitHub-Issues zu oeffnen.
- **_JULES_DELEGATABLE_CATEGORIES:** Whitelist von Code-Security-Categories. Nur diese werden delegiert. Infrastruktur-Categories (docker, config, permissions, network_exposure, backup) NIEMALS dazu nehmen — diese brauchen OS-Zugriff, kein Code-Fix.
- **_JULES_KNOWN_PROJECTS:** Whitelist von Projekten mit Jules-Integration. Neue Projekte nur hinzufuegen wenn sie ein Code-Repo + Jules-Zugriff haben.
- **4-stufige Safety in `_should_delegate_to_jules()`:**
  1. `agent_review.enabled` (Feature-Flag) — bei False immer GitHub-Issue-Pfad
  2. Category muss in `_JULES_DELEGATABLE_CATEGORIES` sein
  3. Projekt muss in `_JULES_KNOWN_PROJECTS` sein
  4. `affected_files` muss nicht-leer sein (Jules braucht Code-Context)
- NIEMALS eine dieser 4 Stufen umgehen — jede einzelne schuetzt gegen fehlerhafte Auto-Delegation
- NIEMALS `agent_review_enabled`/`agent_task_queue` als Instanz-Attribute setzen — sie sind `@property` mit Lazy-Read aus `self.bot.github_integration`. Injection-Zeitpunkt-Issues vermeiden.
- **Jules-Prompt in `_enqueue_jules_fix()` ist STRIKT begrenzt:** "NUR affected Files aendern, kein Refactoring, Tests gruen halten". Nicht lockern — Jules ohne Scope-Constraints macht zu viel.
- **priority=1 fuer scan_agent-Tasks** (nach manual=0, vor jules_suggestion=2). Nicht aendern — Security-Fixes sind zeitkritischer als Suggestions aber nicht so kritisch wie manuelle Eingriffe.
- **Bei Aenderungen an _JULES_DELEGATABLE_CATEGORIES oder _JULES_KNOWN_PROJECTS:** `tests/unit/test_scan_agent_jules_delegation.py` komplett laufen lassen.
- **Fallback:** Wenn Queue/Feature disabled, laeuft `_create_github_issue` wie bisher — kein Code-Doppelpfad, nur if/else auf oberster Ebene.

## Jules-Dashboard-Suggestions (2026-04-14 API-Verifikation)
- **Jules API v1alpha hat KEINEN `suggestions`-Endpoint** (Discovery-Doc verifiziert, nur `sessions` + `sources`)
- **Jules CLI hat KEINEN suggestions-Command** (`jules` ⊂ `new, remote, teleport, login, logout, version, help`)
- **Kein offizieller Jules-MCP-Server** — MCP waere nur Transport, kann keine Daten erzeugen die API nicht exposed
- **Suggestions-Poller (`_fetch_suggestions`) bleibt Stub** bis Google die API erweitert
- **NIEMALS Playwright/Browser-Scraping zum Dashboard implementieren** ohne explizite Sicherheits-Diskussion:
  - Braucht Google-OAuth-Credentials im Bot-Prozess → zu grosser Angriffsvektor
  - ToS-Bruch moeglich, UI-Changes brechen das jederzeit
- **Alternative Task-Quellen die heute funktionieren:**
  1. SecurityScanAgent (autonome Delegation, oben dokumentiert)
  2. Dependabot-PRs mit jules-Label (werden automatisch reviewt)
  3. Manuelle `jules new "..."` CLI-Sessions
  4. GitHub-Issues mit jules-Label (Jules iteriert darauf)

## Auto-Deploy-Hardening (seit 2026-04-17, Finding #131 / PR #135)
- **`github.auto_deploy` ist auf `false` gesetzt** in `config/config.yaml`. Direct-Push auf `main` loest kein Deploy mehr aus.
- **Hardcoded Block in `event_handlers_mixin.py:72-81`:** Auch wenn `auto_deploy` wieder auf `true` gestellt wird, blockiert der Code den Deploy auf direct-push und loggt Warning. Fuer Deploys immer `scripts/restart.sh --pull` manuell oder ueber Merge-Hook.
- **`recovery_mixin._deploy_after_fix()`** erstellt statt Direct-Push auf main einen `fix/security-auto-YYYYMMDD-HHMMSS` Branch und pusht dorthin. Security-Fixes landen nicht mehr blind auf Production-Branches.
- NIEMALS den Block in `event_handlers_mixin.py` entfernen ohne Review. Er ist die letzte Verteidigungslinie gegen AI-Commits, die ohne Human-Gate deployed werden.
- NIEMALS `_deploy_after_fix` wieder auf `git push` (ohne Branch-Check) umbauen — das wuerde PR #135 (#131) regressieren.

## WAL-G-Fixer (seit 2026-04-17, Finding #120 / PR #127)
- **Code:** `src/integrations/fixers/walg_fixer.py` + Adapter in `security_engine/fixer_adapters.py:WalGFixerAdapter`.
- **Registrierung:** In `bot.py` on_ready via `self.security_engine.register_existing_fixers(walg_fixer=WalGFixer(...))`. Source `walg` + `wal-g` triggern den Fixer.
- **Aktion bei Trigger:** Download des GitHub-Release (Target-Tag hardcoded `v3.0.8`), SHA256-Verify gegen vier hardcoded Checksums (amd64/aarch64 × ubuntu 20.04/22.04), Backup `/usr/local/bin/wal-g.bak_security_update`, Replace, Post-Verify, Rollback bei Fail.
- **Bedingungen fuer Lauf:** `sudo`-Rechte (nutzt `sudo cp`/`mv`/`chown`), `curl` im PATH.
- NIEMALS die hardcoded Checksums in `walg_fixer.py:self.checksums` ohne Verifikation gegen das offizielle Release aendern — sie sind die einzige Integritaets-Barriere.
- NIEMALS den `target_version` ohne Checksum-Update bumpen — WAL-G veroeffentlicht pro Release neue Assets.
- **Follow-up offen:** Unit-Tests fuer `walg_fixer.py` (aktuell 0% Coverage), dynamisches Release-Lookup statt Hardcoding.
