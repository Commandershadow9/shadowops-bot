# ShadowOps Security Bot

## Stack
- **Runtime:** Python 3.12, discord.py 2.7
- **AI:** Dual-Engine (Codex CLI Primary + Claude CLI Fallback)
- **Monitoring:** Trivy, CrowdSec, Fail2ban, AIDE
- **Data:** PostgreSQL (Knowledge + Findings, konsolidiert), SQLite (Changelog DB), JSON State Files
- **Deploy:** systemd (system-level), logrotate
- **Version:** v5.0.0

## Services & Ports
| Service | Port | Zweck |
|---------|------|-------|
| Discord Bot | — | Gateway-Connection |
| Health Check + Changelog API | 8766 | Health, REST API, RSS Feed, Sitemap |
| GitHub Webhook | 9090 | Push/PR Events |
| GuildScout Alerts | 9091 | Alert Forwarding |

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
| `ai_engine.py` | Dual-Engine AI (Codex Primary + Claude Fallback, Structured Output, Markdown-Fence-Parser) |
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
| `patch_notes_batcher.py` | Sammelt Commits, Release via Cron (Sonntag), manuell (/release-notes) oder Notbremse (≥20) |
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
| `analyst/` | Security Analyst (security_analyst, analyst_db, activity_monitor, prompts) — Full Learning Pipeline, Adaptive Sessions, Fix-Verifikation, Coverage-Tracking |

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

### Dokumentation
| Pfad | Inhalt |
|------|--------|
| `docs/` | Aktive Referenz-Doku (API, Overview, Security Analyst) |
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

### Security Analyst — Lernende 2-Phasen-Architektur (seit 2026-03-18)
- **Adaptive Session-Steuerung:**
  - ≥20 Findings → fix_only (bis 3 Sessions/Tag, nur Fixen)
  - 5-19 Findings → full_scan + fix (bis 2 Sessions/Tag)
  - 1-4 Findings → quick_scan + fix (1 Session, 20min statt 45min)
  - 0 Findings → maintenance (nur wenn letzter Scan >3 Tage her)
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
- **Codex Analyst:** Prompt via stdin statt CLI-Argument (ARG_MAX Fix), `-c mcp_servers={}` (kein MCP-Laden)
- **Claude Analyst:** Prompt via stdin + `--dangerously-skip-permissions` + `--allowed-tools` (nur Security-Bash-Prefixe + Read/Write/Grep/Glob, keine MCPs)
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
- **agent_feedback:** Universelles Feedback (Discord Reactions, Ratings, Text) fuer alle Agents
- **agent_quality_scores:** Qualitaetsbewertung pro Agent-Output (auto + feedback + combined)
- **agent_knowledge:** Cross-Agent Wissensaustausch (Security Analyst → SEO/Feedback)
- **pn_generations:** Jede generierte Patch Note mit Variante, Scores, Discord-Msg-ID
- **pn_variants:** Prompt-Varianten Performance pro Projekt (times_used, avg_score, combined_weight)
- **pn_examples:** Kuratierte Few-Shot Beispiele nach echtem Feedback sortiert
- **seo_fix_impact:** Score-Delta nach PR-Merge (vorher/nachher, Fix-Kategorien)
- **LearningNotifier:** Automatische Discord-Posts in 🧠-ai-learning (Sessions, Feedback, Weekly, Meilensteine)
- **Event-getriggerte Scans:** Critical/High Events (CrowdSec/Fail2ban) triggern sofort Analyst Quick-Scan

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
