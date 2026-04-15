# Architektur-Entscheidungen

Historie der grossen Architektur-Aenderungen seit 15.03.2026. Diese Datei wurde aus CLAUDE.md ausgelagert um die Context-Groesse unter 40k zu halten.

## Architektur-Entscheidungen (seit 15.03.2026)

### Knowledge-Konsolidierung (3 DBs → 1 PostgreSQL)
- **Vorher:** 3 separate DBs (SQLite ai_knowledge.db, SQLite knowledge.db, PostgreSQL security_analyst)
- **Nachher:** 1 PostgreSQL DB (`security_analyst`) mit allen Tabellen
- **Tabellen:** `findings`, `sessions`, `knowledge`, `learned_patterns`, `health_snapshots`, `orchestrator_fixes`, `orchestrator_strategies`, `orchestrator_plans`, `threat_patterns`
- **knowledge_base.py:** psycopg2 statt sqlite3 (sync, gleiche API)
- **Cross-Referenz:** Analyst-Findings fliessen in Orchestrator-Planung, Orchestrator-Fixes erscheinen im Analyst-Kontext

### Security Analyst — Lernende 2-Phasen-Architektur (seit 2026-03-18, seit 2026-03-24 in SecurityScanAgent)
- **HINWEIS:** Diese Logik lebt jetzt im `SecurityScanAgent` (`security_engine/scan_agent.py`).
  Der alte `analyst/security_analyst.py` ist Legacy und wird nicht mehr gestartet.
- **Adaptive Session-Steuerung:**
  - ≥20 Findings → fix_only (bis 3 Sessions/Tag, nur Fixen)
  - 5-19 Findings → full_scan + fix (bis 2 Sessions/Tag)
  - 1-4 Findings → quick_scan + fix (1 Session, 20min statt 45min)
  - 0 Findings → daily full_scan (mind. 1x/Tag, konfigurierbar via `security_analyst.maintenance_scan_days`)
- **Pre-Session Maintenance:** Git-Activity-Sync, Fix-Verifikation (14 Tage), Knowledge-Decay
- **Phase 1 (Scan):** Reine Analyse, Findings + Coverage + Quality-Assessment in DB
- **Phase 2 (Fix):** Findings abarbeiten mit vollem Knowledge-Kontext + vorherigen Fix-Versuchen
  - Sichere Fixes direkt ausführen (Permissions, Configs, Firewall, Docker)
  - Code-Änderungen als PR (1 Branch `fix/security-findings` pro Projekt)
  - Geschützte Infrastruktur nur als Issue/PR (Bind-Adressen, Ports, Docker-Netzwerk)
  - Fehlversuche werden gespeichert → nächstes Mal anderer Ansatz
- **Full Learning Pipeline (4 DB-Tabellen):**
  - `fix_attempts`: Jeden Fix-Versuch mit Ansatz/Commands/Ergebnis aufzeichnen
  - `fix_verifications`: Prüfung ob Fixes noch aktiv sind, Regressionen → re-open
  - `finding_quality`: Selbstbewertung (confidence, false_positive, discovery_method)
  - `scan_coverage`: Welche Bereiche gecheckt, Lücken >7 Tage im Kontext sichtbar
- **Kontext-Injektionen:** Fix-Effektivität, Coverage-Gaps, Finding-Qualität, Git-Activity
- **Knowledge-Decay:** Confidence -5%/Lauf bei >14 Tage altem Wissen (Min: 20%)
- **Finding-Dedup:** DISTINCT ON Titel-Präfix + Keyword-Match bei Duplikat-Close
- **Issue Quality-Gates (seit 2026-03-18):** 4 Prüfungen vor GitHub-Issue-Erstellung:
  1. Mindest-Content: Titel >= 10, Body >= 30 Zeichen (leere Issues blockiert)
  2. Projekt-Skip: SKIP_ISSUE_PROJECTS (openclaw, agents, blogger, content-pipeline — kein Repo)
  3. DB-Dedup: find_similar_open_finding (Titel exakt + Keyword-Match)
  4. GitHub-Dedup: `gh issue list --search` im Ziel-Repo vor Erstellung
- **Erweitertes Repo-Routing:** PROJECT_REPO_MAP +sicherheitsdienst, +project
- **Auto-Close:** Findings >30 Tage ohne GitHub-Issue → automatisch geschlossen
- **fix_policy pro Projekt:** active→critical_only, stable→all, frozen→monitor_only
- **Codex-Quota-Cache:** Nach Quota-Fehler wird Codex 6h übersprungen
- **Design-Doc:** `docs/plans/2026-03-18-analyst-learning-pipeline-design.md`

### Token-Budget (global)
- **daily_token_budget:** 100K Token/Tag (konfigurierbar in config.yaml)
- **Budget-Check:** Zentral in `_execute_with_fallback()` vor jedem AI-Call
- **Token-Tracking:** Geschaetzt aus Prompt-Laenge, pro Session via `_get_session_tokens()` Delta-Messung
- **Session-DB:** Token-Verbrauch wird pro Session in `sessions.tokens_used` gespeichert (nicht mehr 0)

### AI-Call Sicherheit
- **Alle Provider-Methoden:** Prompts via stdin (`communicate(input=...)`) — kein Leak in ps/proc
  - Codex: `query()` + `query_raw()` — Prompt nicht mehr als CLI-Argument
  - Claude: `query()` + `query_raw()` — `-p -` liest von stdin
- **Codex Analyst:** `--dangerously-bypass-approvals-and-sandbox` + `-c mcp_servers={}` (voller System-Zugriff fuer Security-Scans)
- **Claude Analyst:** `--dangerously-skip-permissions` + `--allowed-tools` (Security-Bash-Prefixe + Read/Write/Grep/Glob, keine MCPs)
- **DB-Credentials:** Kein Hardcoded-DSN mehr — `SECURITY_ANALYST_DB_URL` env var oder `config.yaml` (security_analyst.database_dsn)
- **Webhook:** Fail-closed bei fehlendem Secret, Config-Pfad korrigiert (projects.guildscout)
- **Bind-Adressen:** 8766 auf 0.0.0.0 (UFW: nur Docker 172.16.0.0/12), 9091 auf 127.0.0.1, 9090 (GitHub Webhook) auf 0.0.0.0
- **Patch Notes:** jsonschema-Validierung (soft) gegen `src/schemas/patch_notes.json`
- **API-Quota-Erkennung:** Codex (OpenAI usage limit) + Claude (overloaded/rate limit) in stderr erkannt und geloggt
- **Context Manager:** Nur aktive Projekte (sicherheitstool entfernt)

### Security-DB (PostgreSQL, Enterprise-Level)
- **security_events:** Jeder Ban/Block/Alert persistent (IP, Subnet, Severity)
- **ip_reputation:** Akkumulierter Threat-Score pro IP (20 Punkte pro Ban, max 100)
- **subnet_tracking:** Angriffe pro /24 Subnet
- **remediation_log:** Audit-Trail aller Auto-Fixes (mit Rollback-Command)
- **pending_approvals:** Überlebt Bot-Restart (Approval-State persistent)

### Agent-Learning DB (PostgreSQL, seit 2026-03-18)
- **Gemeinsame DB** `agent_learning` auf GuildScout Postgres (Port 5433)
- **ai_learning.enabled: true** — Trainierte Prompts, A/B-Testing und Feedback-Loop fuer ALLE Projekte
- **agent_feedback:** Universelles Feedback (Discord Reactions, Ratings, Text) fuer alle Agents
- **agent_quality_scores:** Qualitaetsbewertung pro Agent-Output (auto + feedback + combined)
- **agent_knowledge:** Cross-Agent Wissensaustausch (Security Analyst → SEO/Feedback)
- **pn_generations:** Jede generierte Patch Note mit Variante, Scores, Discord-Msg-ID
- **pn_variants:** Prompt-Varianten Performance pro Projekt (times_used, avg_score, combined_weight)
- **pn_examples:** Kuratierte Few-Shot Beispiele nach echtem Feedback sortiert (Cross-Project-Sharing)
- **seo_fix_impact:** Score-Delta nach PR-Merge (vorher/nachher, Fix-Kategorien)
- **LearningNotifier:** Automatische Discord-Posts in 🧠-ai-learning (Sessions, Feedback, Weekly, Meilensteine)
- **Event-getriggerte Scans:** Critical/High Events (CrowdSec/Fail2ban) triggern sofort ScanAgent Quick-Scan (via event_watcher → scan_agent.trigger_event_scan)
- **Patch Notes Update-Channels:** Alle Projekte haben eigene Update-Channels mit Feedback-Buttons (shadowops-bot, ai-agent-framework, guildscout, zerodox)

### Recidive-Erkennung
- 3+ Bans derselben IP → automatisch permanent in UFW geblockt
- Kein Approval nötig (automatische Eskalation)
- Ban-History beim Start aus DB geladen (überlebt Restarts)
- IP-Reputation im Analyst-Prompt (Top-Bedrohungen)
- Remediation-Log mit Rollback-Command für jeden Auto-Block

### Changelog-Seiten Redesign (shared-ui v0.2.0, 16.03.2026)
- **shared-ui:** 11 neue/überarbeitete Changelog-Komponenten (Hero, Card, Timeline, Markdown, Stats-Balken, Badge mit Glow, KeywordCloud)
- **Theming:** CSS-Variablen (`--cl-*`) — Projekte überschreiben nur Farben, Rest kommt automatisch
- **GuildScout:** Gold-Theme, Hero-Bild (Guild Hall), OG-Images, `/changelog` + `/changelog/[version]`
- **ZERODOX:** Cyan-Theme, reiner CSS-Gradient, OG-Images, `/changelog` + `/changelog/[version]`
- **API-Flow Client:** Relative URL `""` → Next.js Rewrites → Go API/Proxy → ShadowOps Bot (Port 8766)
- **API-Flow Server (SSR):** Docker-interner Hostname direkt → ShadowOps Bot
- **URL-Slugs:** Versions-Dots werden zu Dashes (`1.0.0` → `1-0-0`), Detail-Page konvertiert zurück
- **Design-Doc:** `docs/plans/2026-03-16-changelog-redesign-design.md`
- **Implementierungsplan:** `docs/plans/2026-03-16-changelog-redesign.md`

### ZERODOX Discord Patch-Notes Channels (18.03.2026)
- **Zwei Channels auf dem ZERODOX-Server** (Guild `1151330239272730755`), nicht DEV-Server:
  - `📋patch-notes` (ID: `1483892059596132483`) — Lobby-Kanäle, öffentlich, read-only. Sanitisierte Patch Notes.
  - `🔧dev-updates` (ID: `1483892060963475617`) — Community-/Kundenbereich, intern, read-only. Gleiche Patch Notes + Rollen-Ping.
- **Config-Keys pro Projekt:** `update_channel_id` (öffentlich), `update_channel_role_mention` (Rollen-Ping im öffentlichen Channel), `internal_channel_id` (intern), `internal_channel_role_mention` (Rollen-ID fuer Ping im internen Channel)
- **Cross-Guild-Support:** `bot.py` ueberspringt Auto-Channel-Creation wenn `update_channel_id` bereits gesetzt und Channel cross-guild erreichbar
- **Öffentlicher Channel:** Rollen-Ping via `update_channel_role_mention` — postet `<@&role_id> Neues Update verfuegbar!` mit `AllowedMentions(roles=True)` bei Multi-Chunk-Embeds nur an erste Nachricht
- **Interner Channel:** `_send_to_internal_customer_channel()` in `notifications_mixin.py` — postet Embed + `<@&role_id> Neues Update verfuegbar!` mit `AllowedMentions(roles=True)`
- **Setup-Script:** `scripts/setup_zerodox_channels.py` (einmalig, nutzt ShadowOps Bot-Token via Discord REST API)

### Patch Notes Pipeline v6 (seit 2026-04-13, ersetzt v5 Mixins)
- **Redesign-Grund:** v5 hatte 5839 Zeilen, 92 Methoden, Commit-Cap bei 50 (Features gingen verloren), 5 konkurrierende Version-Quellen
- **Architektur:** 5-Stufen State Machine in `src/patch_notes/` (~2100 Zeilen, 101 Tests)
- **Stufen:** Collect → Classify → Generate → Validate → Distribute
- **Config-driven Templates:** `gaming` (MayDay), `saas` (GuildScout, ZERODOX), `devops` (ShadowOps, AI-Agent)
- **Commit-Gruppierung:** Deterministisch, ALLE Commits (kein Cap), nach Scope gruppiert, `is_player_facing` Flag
- **Versionierung:** NUR Changelog-DB + SemVer (1 Quelle). Keine Git-Tags, keine AI-Version
- **Self-Healing:** Leere Commits → automatisch aus Git seit letztem Release. Kein Commit geht verloren nach Restart
- **Crash-Resilience:** Pipeline-State persistiert nach jeder Stufe (`data/pipeline_runs/`). Resume nach Restart
- **Safety (5 Checks):** Feature-Count, Design-Doc-Leak (Smart False-Positive), Version-Strip, Content-Sanitizer, Umlaute
- **Inline-Credits:** Keyword-Overlap Git-Commits → `→ Feature · Shadow`
- **Discord:** Summary-Embed (mit `changelog_url`) + Full-Embed mit Kategorie-Headern (Discord-only)
- **Concurrency:** asyncio Lock + Circuit Breaker (5 Fehler → 1h Pause)
- **Release-Modi:** Daily (22:00, ≥15 Commits) + Weekly Fallback (Sonntag 20:00, ≥3 Commits). Commits akkumulieren
- **Rollback:** `retract_patch_notes(project, version)`
- **Metriken:** `METRICS|patch_notes_pipeline|{json}`
- **Conventional Commit Hook:** `scripts/commit-msg-hook.sh` auf allen 5 Projekten deployed
- **Auto-Label Action:** `.github/workflows/auto-label-pr.yml`
- **Design-Doc:** `docs/design/patch-notes-v6.md`
- **v5-Code:** Bleibt als Fallback (wenn v6 crasht), wird nach 3 erfolgreichen v6-Releases archiviert

### Patch Notes v6 — Post-Deploy-Fixes (2026-04-14)
Live-Test deckte 7 Probleme auf — alle gefixt + Dokumentation/Tests verbessert.

**Loop-Bug (kritisch — verursachte 33 leere DB-Einträge in 4h):**
- `generate.py` rief `ai_service.query()` auf — `AIEngine` hat aber `get_raw_ai_response()`. Raw-Fallback crashte mit AttributeError.
- `generate_structured_patch_notes()` returnte None weil `discord_highlights` im Schema fehlte (PFLICHT-Feld in `patch_notes.json`).
- `validate.py` gab nur Warning bei `ai_result=None`, kein Abort → Distribute speicherte LEERE Notes in DB → Version-Bump → Self-Healing fand wieder Commits → Endlosschleife.
- **Fix:** `validate.py` raised RuntimeError bei `ai_result=None` oder leerem dict/string. Pipeline geht in FAILED State, kein DB-Eintrag, kein Discord-Post, kein Version-Bump.
- **Fix:** `_build_structured_wrapper` Schema enthält jetzt `discord_highlights` + `summary` als PFLICHT-Felder.
- **Fix:** `_call_ai` nutzt `get_raw_ai_response(prompt, use_critical_model=...)` als korrekte AIEngine-API.

**Cron-Integration (v6 Crons griffen nicht):**
- Daily/Weekly Crons in `bot.py` nutzten `batcher.release_batch()` — Batcher ist bei v6 oft leer (Self-Healing umgeht Batcher).
- **Fix:** Bei `engine=v6` direkt `_gather_commits_since_last_release()` aufrufen, Min-Commits-Check VOR AI-Call (spart Kosten bei wenigen Commits).
- `/release-notes` Command ebenfalls auf v6-Pfad umgestellt.

**Doku/Tooling:**
- `tests/unit/test_pn_validate.py` — Tests für RuntimeError bei leerem Result.
- 5 aktive Projekte alle auf `engine: v6`: shadowops-bot (devops), guildscout (saas), zerodox (saas), ai-agent-framework (devops), mayday_sim (gaming).

**DB-Cleanup nach Vorfall:**
- 33 leere Einträge gelöscht (Backup: `data/changelogs.db.backup.20260414_090336`).
- 33 leere `~/.shadowops/changelogs/*/v*.json` Files + 4 Index-Files bereinigt.

**Lektion:** Stage 4 (Validate) muss bei leerem AI-Result HARD ABORTEN, nicht silent weitermachen. Eine Pipeline die fail-silent ist, multipliziert Bugs statt sie zu stoppen.

### Security Engine v6 (seit 2026-03-24)
- **Vorher:** 4 isolierte Systeme (EventWatcher, Orchestrator, Self-Healing, Analyst) mit 2 DB-Layern (psycopg2 + asyncpg)
- **Nachher:** 1 SecurityEngine mit 3 Modi (Reactive, Proactive, DeepScan), 1 unified asyncpg DB, Phase-Type-System
- **Phase-Types:** `recon` (read-only), `contain` (Sofort-Block), `fix` (Härten), `verify` (Prüfen), `monitor` (Nachbeobachtung)
- **Provider-Chain:** NoOp-Detection -> Fixer-Adapter -> Fallback (kein hardcoded if/elif mehr)
- **Fast-Path:** 1-2 Events werden direkt gefixt, kein KI-Plan
- **Event-Claiming:** `remediation_status` Tabelle verhindert Doppel-Fixes zwischen Modi
- **Cross-Agent Learning:** LearningBridge liest/schreibt agent_learning DB bidirektional
- **SecurityScanAgent** (seit 2026-03-24, erweitert 2026-03-25): Ersetzt DeepScanMode + alten SecurityAnalyst
  - Autonomer Agent mit Activity Monitor (nur starten wenn User idle)
  - **Taeglicher Scan:** Codex Full-Access (`--dangerously-bypass-approvals-and-sandbox`), 7 Pflicht-Check-Bereiche
  - **Woechentlicher Deep-Scan:** Nur Claude, Code Security Review, Dependency Deep-Dive, Cross-Projekt-Analyse, Compliance (Sonntag Nacht auto, `touch data/force_deep_scan` manuell)
  - **Deterministische Pre-Checks:** UFW, Docker, Fail2ban, CrowdSec, Ports, Services, Disk, Memory — werden VOR der AI-Analyse gesammelt und als Fakten injiziert
  - **Post-Scan Reflection:** AI bewertet eigene Arbeit (Quality Score, Trend, Insights, Blind Spots) nach JEDER Session
  - **Post-Fix Integrity Check:** Prueft nach Fixes ob Container, Ports, Services intakt sind
  - **Content-Deletion-Guard:** Warnt bei Netto-Loeschungen >20 Zeilen in Projekt-Repos
  - Adaptive Session-Steuerung (fix_only/full_scan/quick_scan/maintenance/weekly_deep)
  - Maintenance-Schwelle: 1 Tag (konfigurierbar via `security_analyst.maintenance_scan_days`)
  - Health-Snapshots vor/nach jeder Session
  - Discord-Briefings (sofort wenn online, pending wenn offline) + Weekly-Recap Report
  - GitHub-Issues mit 4 Quality-Gates (Content, Projekt-Skip, DB-Dedup, GitHub-Dedup)
  - Cross-Mode-Lock ueber remediation_status (claim_event/release_event)
  - Nutzt SecurityDB direkt (kein separater AnalystDB Layer)
  - **PROJECT_SECURITY_PROFILES:** Detaillierte Attack-Surface-Profile fuer guildscout, zerodox, ai-agent-framework, shadowops-bot
  - **Projektnamen-Normalisierung:** 62 Varianten → 5 Standard-Namen (guildscout, zerodox, shadowops-bot, ai-agent-framework, infrastructure)
  - **Knowledge-Maintenance:** Woechentlich — Projektnamen normalisieren, alte info_only Findings schliessen, tote Knowledge entfernen
  - **SecurityDB._ensure_schema():** Erstellt ALLE Tabellen (Legacy + v6) bei Neuinstallation
  - **LearningBridge:** Verbindet Security Engine mit agent_learning DB (Cross-Agent Feedback + Knowledge)
  - **Force-Scan Flags:** `touch data/force_scan` (taeglich), `touch data/force_deep_scan` (weekly) — umgeht Activity-Check, Session-Limit und Maintenance-Check
- **Design-Doc:** `docs/plans/2026-03-24-security-engine-v6.md`, `docs/plans/2026-03-24-security-scan-agent-design.md`
- **Architektur-Doc:** `docs/security-engine-v6-overview.md`

### MayDay Sim Changelog — Einsatzprotokoll (seit 2026-03-30)
- **Web-Changelog:** `https://maydaysim.de/changelog` mit Detail-Seiten `/changelog/[version]`
- **Design:** "Einsatzprotokoll"-Stil mit BOS-Farben, Notrufzentrale-HG-Bild, Timeline
- **Architektur:** ShadowOps Bot API (8766) → MayDay Next.js API-Proxy → SSR Frontend
- **12 Gaming-Badges:** feature, content, gameplay, design, performance, multiplayer, fix, breaking, infrastructure, improvement, docs, security
- **OG-Images:** Dynamisch generiert via `next/og` pro Version
- **SEO:** JSON-LD TechArticle + Breadcrumbs, dynamische Sitemap
- **CORS:** `maydaysim.de` + `www.maydaysim.de` + `localhost:3200` in health_server.py

### Externes Mini-Dashboard (seit 2026-03-27)
- **Feature:** Projekte mit `external_notifications` bekommen ein eigenes Status-Embed auf ihrem Discord-Server
- **Aktualisierung:** Alle 5 Minuten (Edit statt neue Nachricht), Message-ID persistiert in state.json
- **Service-Details:** Einzelne TCP-Ports mit Status-Icons (🟢/🔴 Web, DB, Redis, OSRM)
- **DEV-Dashboard:** Gleiches Service-Detail-Format, Tags aus Config, deutsches Layout
- **Implementierung:** `_update_external_dashboards()`, `_create_single_project_dashboard()` in project_monitor.py

### Discord-Nachrichten-Optimierung (seit 2026-03-25)
- **Startup:** 8-10 einzelne Embeds → 1 kompaktes Summary-Embed (`_send_startup_summary`)
- **Deployment-Log:** 5-8 Text-Nachrichten pro Deploy → 1 Embed mit Timeline (`_send_deployment_update` sammelt, Success/Failure zeigt alles)
- **Fail2ban Recidive:** CIDR-Format-Bug gefixt (`INET::TEXT` gibt `/32` Suffix), `force=True` entfernt, Race Condition bei DB-Init behoben (`await` statt `ensure_future`)
- **Proactive Report:** Kein Routine-Spam mehr (nur bei kritischen Empfehlungen), Daten fliessen in Weekly-Recap
- **Pending-Approval:** Roher Text → sauberes Embed mit Batch-ID und Aktion
- **Channel-Naming:** Update-Channels mit Emoji-Prefix umbenannt (`📋-updates-*`, `🧪-ci-*`)
- **Toter Channel:** `sicherheitsdiensttool_updates` aus state.json entfernt (Channel existiert nicht mehr)
- **Ergebnis:** ~200 Nachrichten/Tag → ~20-30 (85% Reduktion)

### Jules SecOps Workflow (seit 2026-04-11)
- **Hybrid-Fix:** ScanAgent fixt Server-Hardening selbst, delegiert Code-Fixes an Jules via GitHub-Issue mit `jules` Label
- **PR-Erkennung:** `_jules_is_jules_pr()` prueft 3 Kriterien: (1) `jules` Label am PR, (2) Author `google-labs-jules`, (3) Body-Marker `PR created automatically by Jules`. Jules erstellt PRs unter User-Account, daher ist Body-Marker primaer
- **Claude-Review:** Strukturiert (BLOCKER/SUGGESTION/NIT), Schema-validiert, deterministischer Verdict
- **Loop-Schutz:** 7 Schichten (Trigger-Whitelist, SHA-Dedupe, Cooldown, Iteration-Cap 5, Circuit-Breaker 20/h, Time-Cap 2h, Single-Comment-Edit)
- **State:** `security_analyst.jules_pr_reviews` mit atomic Lock-Claim, Stale-Lock-Recovery nach 10min
- **Learning:** `agent_learning.jules_review_examples` + `agent_knowledge` (Few-Shot + Projekt-Konventionen), Nightly-Batch
- **Rollback:** Config-Flag `jules_workflow.enabled: false` → ~30s
- **Design-Doc:** `docs/design/jules-workflow.md`
- **Vorfall-Referenz:** PR #123 (ZERODOX) — 31 Kommentare Loop; siehe Design-Doc Anhang A
- **Post-Deploy-Fixes (2026-04-12):** 4 kritische Fixes nach Go-Live:
  - `e5d3e5c` — `self.logger` → `logger` in `AIEngine.review_pr()` (falscher Logger-Scope)
  - `c121ee5` — `from src.` Import-Fehler in 6 Dateien (ModuleNotFoundError im systemd-Kontext)
  - `3171d88` — Redis-Auth URL mit Passwort aus Config laden (Circuit-Breaker brauchte Auth)
  - `bd2038f` — PR-Erkennung via Body-Marker statt nur Label/Author (Jules postet unter User-Account)
- **Jules CLI:** `@google/jules` (v0.1.42) auf Server installiert. Login via `jules login` (Google OAuth). Status: `jules remote list --session`
- **17 Jules-PRs auf ZERODOX gemerged (2026-04-13):** Security (#134, #135, #148), Code Health (#138-#147), Testing (#137, #139, #140), Performance (#151), SEO (#152, #153) — alle via Claude-Review approved
- **Jules API Integration (2026-04-13):** API-Key in config.yaml (`jules_workflow.api_key`). Endpoint: `https://jules.googleapis.com/v1alpha`. Tools: `POST /sessions` (create), `GET /sessions/{id}` (status). Sessions via API gestartet (3 Tests) → Jules öffnet PRs automatisch → Bot reviewt mit Claude → Label-Set → Jules iteriert bei Revision
- **Post-Go-Live Fixes (2026-04-14):** 5 weitere Fixes nach erstem echten Workflow-Test:
  - Discord-Logger `send_to_channel` → `_send_to_channel` (privat mit Underscore)
  - Claude Opus hing bei 3 parallelen Calls — Fallback auf Sonnet implementiert (`review_pr` versucht Primary + Fallback)
  - Intelligente Modell-Wahl: Opus bei Security-Keywords (xss/cve/injection/dos/auth/csrf) oder Diff > 3000 chars, sonst Sonnet
  - Robuster JSON-Parser: Extrahiert JSON-Block auch bei Extra-Text vor/nach dem Objekt
  - Label via REST API `POST /repos/{}/issues/{}/labels` (gh pr edit hat GraphQL-Bug bei ZERODOX wegen Projects-Classic-Deprecation) + Auto-Create Label wenn fehlt
  - `@google-labs-jules` Mention im Revision-Comment damit Jules automatisch iteriert
  - Discord-Review-Embed statt Text-Nachrichten (Farbe grün/rot, Findings-Counter, PR-Link, Iteration)

### Multi-Agent Review Pipeline (seit 2026-04-14)
- **Architektur:** Adapter-Pattern — jeder Agent-Typ (Jules, SEO, Codex) hat einen `AgentAdapter` mit detect/build_prompt/model_preference/merge_policy/discord_channel
- **Location:** `src/integrations/github_integration/agent_review/`
- **Module:**
  - `adapters/base.py` — AgentAdapter ABC, AgentDetection, MergeDecision Enum
  - `adapters/jules.py` — Wrapt bestehende Jules-Review-Logik
  - `adapters/seo.py` — SEO-Agent PRs (SEO/GSC/GEO/AEO), Content-Only-Auto-Merge
  - `adapters/codex.py` — SecurityScanAgent-Code-Fixes, IMMER manual
  - `detector.py` — AgentDetector mit Confidence-Threshold 0.8, Highest-Wins
  - `queue.py` — TaskQueue (asyncpg) fuer Jules-Session-Starts
  - `jules_api.py` — JulesAPIClient (REST v1alpha, POST/GET /sessions)
  - `suggestions_poller.py` — Holt Jules Top-Suggestions (Skeleton, API noch Stub)
  - `outcome_tracker.py` — 24h Post-Merge Revert-Detection (auto_merge_outcomes)
  - `discord_embed.py` — Farbkodierte Review-Embeds (gruen/blau/gelb/orange/rot)
  - `daily_digest.py` — Taeglicher Markdown-Report (Reviews, Auto-Merges, Reverts, Trend)
  - `prompts/seo_prompt.py` — Multi-Domain SEO-Review-Prompt
  - `prompts/codex_prompt.py` — Code-Security-Review-Prompt
- **DB-Schema:** `src/integrations/github_integration/agent_review_schema.sql`
  - `agent_task_queue` — Queue mit Priority, Retry-Delay, status
  - `auto_merge_outcomes` — Auto-Merge-Tracking mit 24h-Check
  - `jules_pr_reviews.agent_type` — additive Spalte, default 'jules'
- **Scheduled Tasks in `bot.py`:**
  - `agent_task_queue_scheduler` (60s) — Queue → Jules-Session, respektiert 100/24h + 15 concurrent
  - `agent_suggestions_poller_task` (8h) — Pollt Jules-Suggestions in Queue
  - `agent_outcome_check_task` (60min) — Prueft Auto-Merges > 24h auf Reverts
  - `agent_daily_digest_task` (08:15) — Postet Markdown-Report in 🧠-ai-learning
  - `agent_weekly_recap_task` (Freitags 18:00) — Postet Discord-Embed mit Ampel-Status in 🧠-ai-learning
- **Config-driven Rollout:** `agent_review.enabled` + per-Adapter-Toggle (`adapters.jules/seo/codex`)
- **Auto-Merge:** Separat aktivierbar (`agent_review.auto_merge.enabled: false` default), per-project `allowed` Flag, Rollback < 30s via Config
- **Outcome-Learning:** `revert_rate_by_rule` gruppiert nach `rule_matched` = `{agent}_{verdict}_{Nb}` — zeigt welche Merge-Entscheidungen zu haeufig reverted werden
- **Vertiefte Adapter-Integration (Phase 6 Final):**
  - `ai_engine.review_pr(prompt_override, model_preference)` akzeptiert adapter-gebauten Prompt und Modell-Wahl
  - `_jules_run_review(adapter=...)` reicht den Adapter durch; fuer non-Jules nutzt der Review-Pfad `adapter.build_prompt()` + `adapter.model_preference()`
  - `handle_jules_pr_event`: Routing-Matrix `is_jules_legacy vs. detected_adapter vs. agent_review_enabled`
  - `_update_review_agent_type()`: setzt `jules_pr_reviews.agent_type` fuer Multi-Agent-Statistik
- **SecurityScanAgent → Queue-Delegation (seit 2026-04-14):** Autonomie-Schleife geschlossen
  - `_should_delegate_to_jules(finding)`: 4-stufige Safety (enabled, category, project, affected_files)
  - `_enqueue_jules_fix(finding)`: baut Jules-Prompt + Queue-Insert
  - Flow: ScanAgent findet Code-Issue → Queue → Scheduler → Jules-API → PR → Bot-Review → Label
  - Categories: `code_security, xss, sql_injection, auth, input_validation, csrf` → Jules
  - Categories: `docker, config, permissions, network_exposure, backup` → GitHub-Issue (wie bisher)
  - Projekte: `zerodox, guildscout, shadowops-bot, ai-agent-framework, mayday-sim`
- **Jules-Dashboard-Suggestions (API-Befund 2026-04-14):** Nicht via API zugaenglich
  - Jules API v1alpha hat nur `sessions` + `sources` Resources
  - Jules CLI hat keinen `suggestions`-Command
  - Kein offizieller Jules-MCP-Server
  - Suggestions-Poller bleibt Stub bis Google die API erweitert (~30min Job zum Nachziehen)
  - Ersatz heute: SecurityScanAgent + Dependabot + manuelle `jules new` Sessions
- **End-to-End Live-Test (2026-04-14):** PR #141, komplette Pipeline in 20s durchlaufen
  - Webhook → Detector → Claude-Review → claude-approved Label
  - Review hat sogar projekt-spezifische Regel-Verletzung (Umlaut-Style) als Nit gefunden
- **Design-Doc:** `docs/design/multi-agent-review.md`
- **Implementierungsplan:** `docs/plans/2026-04-14-multi-agent-review.md`
- **ADR:** `docs/adr/008-multi-agent-review-pipeline.md`
- **Rollout-Guide:** `docs/multi-agent-review-rollout.md`
- **Operator-Runbook:** `docs/multi-agent-review-runbook.md` (Incident-Playbooks, Health-Checks, SQL-Diagnose-Queries)
- **Operations-Guide:** `docs/multi-agent-review-operations.md` (taeglicher Betrieb, Routinen, Discord-Channels, Projekt-Status)
- **Smoke-Test-Script:** `scripts/smoke_test_multi_agent_review.py` (7 Stages, reproduzierbar, kein Config-Change)
- **Weekly-Check-Script:** `scripts/weekly_review_check.sh` (farbige Ampel, 6 Sektionen, Exit-Code 0/1)
- **Test-Coverage:** 296 Unit-Tests (Adapter 91, Detector 14, Queue 18, API 18, Poller 10, Tracker 8, Auto-Merge-Flow 14, Embed 18, Digest 17, Adapter-Prompt-Integration 9, ScanAgent-Delegation 23, Jules 19, PR #123 Regression 17)
