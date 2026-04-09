# ShadowOps Security Bot

## Stack
- **Runtime:** Python 3.12, discord.py 2.7
- **AI:** Dual-Engine (Codex CLI Primary + Claude CLI Fallback)
- **Monitoring:** Trivy, CrowdSec, Fail2ban, AIDE
- **Data:** PostgreSQL (Knowledge + Findings, konsolidiert), SQLite (Changelog DB), JSON State Files
- **Deploy:** systemd (system-level), logrotate
- **Version:** v5.0.0

## Services & Ports
| Service | Port | Bind | Zweck |
|---------|------|------|-------|
| Discord Bot | — | — | Gateway-Connection (DEV + ZERODOX Server) |
| Health Check + Changelog API | 8766 | 0.0.0.0 | Health, REST API, RSS Feed, Sitemap (UFW: nur Docker 172.16.0.0/12) |
| GitHub Webhook | 9090 | 0.0.0.0 | Push/PR Events (Traefik) |
| GuildScout Alerts | 9091 | 0.0.0.0 | Alert Forwarding (UFW: nur Docker 172.16.0.0/12) |

## Befehle
| Aktion | Befehl |
|--------|--------|
| Status | `sudo systemctl status shadowops-bot` |
| Restart | `scripts/restart.sh [--pull] [--logs]` |
| Logs (live) | `journalctl -u shadowops-bot -f` |
| Logs (Datei) | `tail -f logs/shadowops_YYYYMMDD.log` |
| Tests | `pytest tests/ -x` (einzeln! OOM-Gefahr bei 8 GB VPS) |

## Absolute Verbote
- NIEMALS `config/config.yaml` loeschen (Discord Token + API Keys)
- NIEMALS `.venv/` loeschen
- NIEMALS `data/` Dateien loeschen (State, Knowledge DB, Incidents)
- NIEMALS `logs/` komplett loeschen (logrotate verwaltet)
- Tests EINZELN ausfuehren (VPS hat nur 8 GB RAM)
- NIEMALS `git push --force` ohne explizite Bestaetigung

## Wo liegt was?

### Slash Commands (`src/cogs/`)
| Datei | Cog | Commands |
|-------|-----|----------|
| `monitoring.py` | MonitoringCog | `/status`, `/bans`, `/threats`, `/docker`, `/aide` |
| `admin.py` | AdminCog | `/scan`, `/stop-all-fixes`, `/remediation-stats`, `/set-approval-mode`, `/reload-context`, `/release-notes`, `/pending-notes` |
| `inspector.py` | InspectorCog | `/get-ai-stats`, `/agent-stats`, `/projekt-status`, `/alle-projekte` |
| `customer_setup_commands.py` | CustomerSetupCommands | `/setup-customer-server` |

### Integrationen (`src/integrations/`)

#### Kern-Module (Packages)
| Package | Module | Zweck |
|---------|--------|-------|
| `orchestrator/` | `core`, `batch_mixin`, `planner_mixin`, `discord_mixin`, `executor_mixin`, `recovery_mixin`, `models` | Remediation-Orchestrator (Event-Batching, KI-Analyse, Fix-Ausfuehrung, Erfahrungslernen via KB) |
| `github_integration/` | `core`, `webhook_mixin`, `polling_mixin`, `event_handlers_mixin`, `ci_mixin`, `state_mixin`, `git_ops_mixin`, `notifications_mixin`, `ai_patch_notes_mixin` | GitHub Webhook Server, Patch Notes, CI/CD |

#### Einzelne Module
| Datei | Zweck |
|-------|-------|
| `ai_engine.py` | Dual-Engine AI (Codex Primary + Claude Fallback, Structured Output, Markdown-Fence-Parser, Schema-Validierung via jsonschema). Prompts werden via stdin uebergeben (kein Leak in ps/proc) |
| `smart_queue.py` | SmartQueue (3 Analyse-Slots, 1 Fix-Lock, Circuit Breaker) |
| `auto_fix_manager.py` | Discord Buttons fuer Approve/Reject, Persistent Views |
| `event_watcher.py` | Periodischer Scanner (Trivy/CrowdSec/Fail2ban/AIDE) |
| `self_healing.py` | SelfHealingCoordinator (Job-Management, Rollback) |
| `fail2ban.py` | Fail2ban Monitor (IP-Bans, Jail Stats) |
| `crowdsec.py` | CrowdSec Monitor (Alerts, Decisions, Metrics) |
| `docker.py` | Docker/Trivy Security Scanner |
| `aide.py` | AIDE File Integrity Monitoring |
| `approval_modes.py` | Approval-Modi (paranoid/auto/dry-run) |
| `context_manager.py` | Project-Context Loader fuer KI-Prompts |
| `project_monitor.py` | Projekt-Health-Monitoring (Uptime, Response Times, systemd-Service-Checks) |
| `deployment_manager.py` | Deployment-Verwaltung |
| `incident_manager.py` | Incident-Tracking und -Management |
| `customer_notifications.py` | Kunden-Benachrichtigungen (Patch Notes etc.) |
| `customer_server_setup.py` | Auto-Setup von Kunden-Discord-Servern |
| `guildscout_alerts.py` | GuildScout Alert-Forwarding (Port 9091) |
| `server_assistant.py` | Server Assistant (ersetzt Legacy Learning System) |
| `changelog_db.py` | Zentrale Changelog-DB (SQLite, alle Projekte, Upsert + Paginierung) |
| `content_sanitizer.py` | Security-Filter fuer Patch Notes (Pfade, IPs, Ports, Secrets) |
| `patch_notes_batcher.py` | Sammelt ALLE Commits ohne Ausnahme. Release via Cron (Sonntag 20:00 / täglich), manuell (/release-notes min 3), oder Notbremse (≥20 mit 24h Cooldown). Max 1 automatischer Release pro Projekt pro Tag. Cooldown persistiert in `last_releases.json` |
| `patch_notes_feedback.py` | Discord Feedback (Persistent Buttons: Like + Bewerten, Text-Modal) + Learning-DB Integration |
| `patch_notes_learning.py` | Patch Notes Learning Pipeline (PostgreSQL agent_learning DB, Varianten-Gewichtung, Feedback-Loop) |
| `patch_notes_web_exporter.py` | Web-Export (zentrale DB Upsert + File-Backup + optional HTTP POST). Frontend: shared-ui v0.2.0 Changelog-Komponenten |
| `learning_notifier.py` | Automatische Discord-Posts in 🧠-ai-learning (Session-Summaries, Feedback-Ergebnisse, Weekly, Meilensteine) |
| `knowledge_base.py` | PostgreSQL Knowledge Database (konsolidiert: Fixes, Strategien, Pläne, Analyst-Cross-Referenz) |
| `log_analyzer.py` | Log-Analyse und -Auswertung |
| `code_analyzer.py` | Code-Analyse fuer Fix-Strategien |
| `git_history_analyzer.py` | Git-History Analyse |
| `command_executor.py` | Sichere Command-Ausfuehrung (kein shell=True) |
| `impact_analyzer.py` | Impact-Analyse vor Fix-Ausfuehrung |
| `backup_manager.py` | Backup vor Fixes (/tmp/shadowops_backups/) |
| `service_manager.py` | systemd Service Management (8 Services: shadowops, guildscout, sicherheitstool, nexus, postgresql + 3 AI-Agent-Services) |
| `verification.py` | Post-Fix Verifikation |

#### Unterverzeichnisse
| Verzeichnis | Zweck |
|-------------|-------|
| `fixers/` | Tool-spezifische Fixer (trivy, crowdsec, fail2ban, aide) |
| `ai_learning/` | Legacy AI Learning (DEAKTIVIERT — knowledge_db, knowledge_synthesizer, continuous_learning_agent) |
| `analyst/` | Legacy Security Analyst (DEAKTIVIERT — ersetzt durch SecurityScanAgent in `security_engine/`) |
| `security_engine/` | Unified Security Engine v6 (engine, db, executor, reactive, deep_scan, proactive, learning_bridge, providers, registry, circuit_breaker, fixer_adapters, models, scan_agent, prompts, activity_monitor) |

### Utils (`src/utils/`)
| Datei | Zweck |
|-------|-------|
| `config.py` | Config-Klasse (laedt `config/config.yaml`) |
| `logger.py` | `setup_logger()` — Logging-Setup mit Rotation |
| `embeds.py` | `EmbedBuilder` + `Severity` — Discord Embed Templates |
| `discord_logger.py` | `DiscordChannelLogger` — Logging in Discord Channels |
| `health_server.py` | `HealthCheckServer` — aiohttp auf Port 8766 (+ Changelog REST API, RSS, Sitemap) |
| `state_manager.py` | `StateManager` — Persistenter Bot-State (`data/state.json`) |
| `message_handler.py` | `MessageHandler` — Sicheres Senden (Split bei >2000 Zeichen) |
| `changelog_parser.py` | `ChangelogParser` — CHANGELOG.md Parser fuer Patch Notes |
| `process_lock.py` | `ProcessLock` — Cross-Process Advisory File Lock (fcntl) fuer Singleton-Services |

### Schemas (`src/schemas/`)
| Datei | Zweck |
|-------|-------|
| `fix_strategy.json` | Remediation-Plaene (Einzelne Events) |
| `coordinated_plan.json` | Koordinierte Batch-Plaene (Orchestrator) |
| `incident_analysis.json` | Incident-Analyse (Self-Healing) |
| `patch_notes.json` | AI-generierte Patch Notes (v3: + seo_keywords, seo_category) |
| `analyst_session.json` | Security Analyst Session Output (+ areas_checked, finding_assessments) |

### Konfiguration
| Datei | Zweck |
|-------|-------|
| `config/config.yaml` | Hauptkonfiguration (NICHT in Git!) |
| `config/config.example.yaml` | Template fuer neue Setups |
| `config/config.recommended.yaml` | Empfohlene Einstellungen |
| `config/safe_upgrades.yaml` | Curated Upgrade-Pfade fuer Packages |
| `config/logrotate.conf` | Logrotate-Konfiguration |
| `data/state.json` | Dynamischer Bot-State (Channel IDs, etc.) |
| `data/ai_knowledge.db` | LEGACY SQLite (Daten nach PostgreSQL migriert, wird nicht mehr aktiv genutzt) |
| `data/changelogs.db` | Zentrale Changelog-DB (alle Projekte, wird zur Laufzeit erstellt) |
| `data/project_monitor_state.json` | Persistenter Monitor-State (Uptime-Stats pro Projekt) |
| `data/last_releases.json` | 24h Release-Cooldown Timestamps pro Projekt (Patch Notes Safety Schicht 4) |
| `.shadowops.lock` | Advisory File Lock — verhindert doppelte Bot-Instanzen (ProcessLock, NICHT in Git) |

### Deploy
| Datei | Zweck |
|-------|-------|
| `deploy/shadowops-bot.service` | systemd Unit-File (Quelle fuer /etc/systemd/system/), setzt XDG_RUNTIME_DIR fuer User-Service-Zugriff |

### Scripts (`scripts/`)
| Datei | Zweck |
|-------|-------|
| `restart.sh` | Bot neustarten (--pull, --logs) |
| `diagnose-bot.sh` | Diagnose: Status, Ports, Logs, Konflikte |
| `setup.sh` | Erstinstallation (venv, Dependencies, Service) |
| `update-config.sh` | Config-Migration bei Updates |
| `get_bot_invite.py` | Discord Bot Invite-URL generieren |
| `test_alerts.py` | Test-Plan fuer Discord Alert Channels |
| `run_tests_with_coverage.sh` | Tests mit Coverage ausfuehren, Ergebnisse nach data/test_results.json |
| `migrate_changelogs.py` | Migration: ZERODOX + GuildScout PG-Changelogs → zentrale SQLite DB |
| `setup_zerodox_channels.py` | Einmalig: ZERODOX Discord-Channels (Patch Notes) einrichten (Kategorie + Permissions) |
| `commit-msg-hook.sh` | Conventional Commit Hook — validiert Commit-Messages (Prefix, Beschreibungslaenge). Auf allen 5 Projekten deployed |
| `deploy-commit-hook.sh` | Deployt den Commit-Hook auf ein/alle Projekte (`--all` fuer alle 5) |

### GitHub Actions (`.github/workflows/`)
| Datei | Zweck |
|-------|-------|
| `auto-label-pr.yml` | Auto-Label PRs aus Conventional Commit Prefixen (feat→feature, fix→bugfix, etc.). SHA-gepinnter actions/github-script, 10 Labels konfiguriert |

### Dokumentation
| Pfad | Inhalt |
|------|--------|
| `docs/` | Aktive Referenz-Doku (API, Overview, Security Engine v6) |
| `docs/guides/` | Benutzer-Anleitungen (Setup, Quickstart, Multi-Guild) |
| `docs/plans/` | Design- und Implementierungsdokumente |
| `docs/adr/` | Architecture Decision Records (6 ADRs) |
| `docs/archive/` | Veraltete Dokumentation (historisch) |

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

### Patch Notes Safety — 5-Schichten-Schutz (26.03.2026)
- **Vorfall 18.03.2026:** Batcher-Referenz ging verloren → 1-Commit Patch Notes fuer ZERODOX v2.9.2 (KI halluzinierte Features aus Doku-Commit)
- **Vorfall 25.03.2026:** Design-Doc Commit (733 Zeilen Referral-Spec) wurde als implementiertes Feature halluziniert → v3.0.8 enthielt "Referral-System" + "Enterprise Backup" die nicht auf main waren
- **Schicht 1 — Commit-Klassifizierung (Pre-Generation):** `_classify_commit()` taggt jeden Commit ([FEATURE], [BUGFIX], [DESIGN-DOC], [SEO-AUTO], [DEPS-AUTO], [REVERT], [MERGE], etc.). Design-Doc-Bodies werden abgeschnitten. Merge/Auto-Commits gefiltert/gruppiert. Body-Noise (Co-Authored-By, Signed-off-by) entfernt. PR-Beschreibungen via `gh pr view` angereichert
- **Schicht 2 — Prompt-Regeln (During Generation):** Explizite Typ-Interpretations-Regeln in allen 4 Prompt-Pfaden (DE+EN Structured, DE+EN Fallback). "[DESIGN-DOC] = GEPLANT, NICHT IMPLEMENTIERT → NIEMALS als Feature listen"
- **Schicht 3 — Post-Generierungs-Validierung:** `_validate_ai_output()` prueft Feature-Count gegen tatsaechliche feat:-Commits, erkennt Design-Doc-Keywords in Feature-Beschreibungen und entfernt halluzinierte Features automatisch
- **Schicht 4 — Batcher + min_commits + Cooldown:** min_commits Check NUR bei skip_batcher. Batcher Self-Healing, /release-notes Minimum. **24h Release-Cooldown:** Nach einem Release wird die Notbremse für 24h pro Projekt blockiert → max 1 automatischer Release/Tag. Cooldown persistiert in `last_releases.json`. Manuelle Releases (/release-notes) und Cron-Releases setzen den Cooldown ebenfalls
- **Schicht 5 — Content Sanitizer:** Pfade, IPs, Ports, Secrets + `changes[].details` Array
- **Duplikat-Guard:** Vorherige Version aus Changelog-DB als "BEREITS ABGEDECKT" Kontext
- **Dev-Branch Teaser:** Aktive feat/* Branches mit Fortschrittsindikator + Hype-Prompt ("🔮 Demnächst")
- **Projekt-Kontext:** `project_description` + `target_audience` in config.yaml pro Projekt
- **Semantic Versionierung:** `_calculate_semver()` berechnet MINOR/PATCH/MAJOR aus Commit-Typen statt KI-Erfindung. Kollisionsschutz via `_ensure_unique_version()` — wird jetzt bei ALLEN 4 Quellen aufgerufen (git_tag, explicit, semver, ai), nicht nur bei semver
- **Team-Credits:** `TEAM_MAPPING` in ai_patch_notes_mixin.py — Git-Autoren → Display-Name + Rolle. Credits erscheinen **inline pro Change** im Discord-Embed (z.B. `→ Score Engine · Shadow`). Author-Zuordnung via Post-Processing: `_enrich_changes_with_git_authors()` matcht AI-Changes gegen echte Git-Commits per Keyword-Overlap. `author`-Feld im Schema ist optional — die AI muss es NICHT füllen, die Zuordnung kommt aus Git-Daten
- **Discord-Teaser entfernt:** Projekte mit `changelog_url` zeigen jetzt `_build_discord_summary` mit Inline-Credits statt eines nichtssagenden AI-Teasers. Alle Pfade (discord_only + summary) haben Credits
- **Alle 4 Trigger-Pfade gesichert:** Webhook Push, Local Polling, Woechentlicher Cron, Manueller /release-notes
- **PR-Label Integration:** GitHub PR-Labels (16 Mappings) via `gh pr view --json labels` als zuverlaessigere Klassifizierung. Labels ueberschreiben Commit-Prefix
- **Smart Diff-Analyse:** Dateien nach 8 Kategorien gruppiert (Frontend, Backend, DB, Config, Tests, Docs, CI/CD, Dependencies). Strukturierte Uebersicht statt roher Diff-Output
- **Conventional Commit Hook:** `scripts/commit-msg-hook.sh` validiert Prefix + Beschreibungslaenge. Deployed auf alle 5 Projekte via `scripts/deploy-commit-hook.sh --all`
- **Auto-Label GitHub Action:** `.github/workflows/auto-label-pr.yml` setzt Labels aus Commit-Prefixen automatisch auf PRs
- **A/B-Varianten-Regelblock:** `_CLASSIFICATION_RULES_DE/EN` wird IMMER an den Prompt angehaengt, egal welche A/B-Variante gewaehlt wird
- **Pipeline-Metriken:** Kompakte Log-Zeile bei jeder Generierung (Commits nach Typ, PR-Labels, Halluzinationen, Version-Source)

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

### Discord-only Patch Notes (seit 2026-03-27)
- **Erkennung:** Projekte ohne `changelog_url` bekommen automatisch das Community-Format
- **Unterschied:** Summary als Einleitung, mehr Features (6-8), Details pro Feature (2 Unterpunkte), mehr Fixes/Improvements
- **Projekte mit `changelog_url`:** Kurzformat + "Alle Details" Web-Link (unveraendert)
- **Betrifft:** Alle zukuenftigen Projekte ohne Web-Changelog

### MayDay Sim Changelog — Einsatzprotokoll (seit 2026-03-30)
- **Web-Changelog:** `https://maydaysim.de/changelog` mit Detail-Seiten `/changelog/[version]`
- **Design:** "Einsatzprotokoll"-Stil mit BOS-Farben, Notrufzentrale-HG-Bild, Timeline
- **Architektur:** Einheitliches Pattern — ShadowOps Bot API (8766) → MayDay Next.js API-Proxy → SSR Frontend
- **9 Gaming-Badges:** feature, content, gameplay, design, performance, multiplayer, fix, breaking, infrastructure (Schema + Template)
- **Discord-Teaser:** `discord_teaser` Feld im gaming_community_v2 Template — Hype-Text + Cliffhanger + Link zur Website
- **OG-Images:** Dynamisch generiert via `next/og` pro Version (BOS-Design, Version, Titel, Stats)
- **SEO:** JSON-LD TechArticle + Breadcrumbs, dynamische Sitemap, Keywords-Tags
- **Varianten-Sync:** `_sync_default_variants()` in prompt_ab_testing.py traegt neue Varianten automatisch nach
- **CORS:** `maydaysim.de` + `www.maydaysim.de` + `localhost:3200` in health_server.py
- **Design-Doc:** `docs/plans/2026-03-30-mayday-changelog-design.md`

### Adaptiver Release-Mode (seit 2026-03-29, überarbeitet 2026-04-08)
- **Alle 5 Projekte:** `release_mode: daily` mit adaptivem Threshold
- **Aktive Projekte (≥15 Commits):** Täglicher Release um 22:00 — alle Commits gebündelt in einer Note
- **Ruhige Projekte (<15 Commits):** Weekly Fallback Sonntag 20:00 (≥3 Commits)
- **Weekly fängt ALLE Projekte auf** — auch daily-Projekte die unter dem Daily-Threshold geblieben sind
- **Emergency Threshold: 100** — praktisch deaktiviert, 24h Cooldown verhindert Mehrfach-Releases
- **Max 1 automatischer Release pro Projekt pro Tag** — Cooldown persistiert in `last_releases.json`
- **Git-Tag-Aware:** Version wird aus Git-Tags im Commit-Batch erkannt (Priorität 1 in _resolve_version)
- **gaming_community_v2:** Story-Telling Template mit konkretem Spielgefühl, → Pfeil-Format, ausführlichen Feature-Beschreibungen

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
