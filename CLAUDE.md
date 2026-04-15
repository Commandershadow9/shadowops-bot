# ShadowOps Security Bot

## Stack
- **Runtime:** Python 3.12, discord.py 2.7
- **AI:** Dual-Engine (Codex CLI Primary + Claude CLI Fallback)
- **Monitoring:** Trivy, CrowdSec, Fail2ban, AIDE
- **Data:** PostgreSQL (Knowledge + Findings, konsolidiert), SQLite (Changelog DB), JSON State Files
- **Deploy:** systemd (system-level), logrotate
- **Patch Notes:** Pipeline v6 (State Machine, seit 2026-04-13)
- **Version:** v5.1.0

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

### Patch Notes Pipeline v6 (`src/patch_notes/`)

Eigenstaendiges Package mit 5-Stufen State Machine. Ersetzt die alten Mixins (`ai_patch_notes_mixin.py`, Teile von `notifications_mixin.py`).

| Datei | Zweck |
|-------|-------|
| `__init__.py` | Public API: `generate_release()`, `retract_patch_notes()` |
| `pipeline.py` | `PatchNotePipeline` — State Machine Orchestrator (asyncio Lock, Circuit Breaker, Crash-Resume) |
| `context.py` | `PipelineContext` Dataclass — traegt alle Daten durch die 5 Stufen |
| `state.py` | `PipelineStateStore` — JSON-Persistenz fuer Crash-Resilience (`data/pipeline_runs/`) |
| `versioning.py` | DB-basierte SemVer (EINE Quelle: Changelog-DB, kein Git-Tag) |
| `grouping.py` | Deterministische Commit-Gruppierung (ALLE Commits, kein Cap, PR-Label Override) |
| `stages/collect.py` | Stufe 1: PR-Daten anreichern, Git-Stats, Self-Healing (Commits aus Git wenn leer) |
| `stages/classify.py` | Stufe 2: Gruppierung + Version + Team-Credits + Update-Groesse |
| `stages/generate.py` | Stufe 3: Template-Auswahl + AI-Call (Codex/Claude) + Structured Output Parsing |
| `stages/validate.py` | Stufe 4: 5 Safety-Checks (Feature-Count, Design-Doc-Leak, Version-Strip, Sanitizer, Umlaute) + Inline-Credits |
| `stages/distribute.py` | Stufe 5: Discord (Summary/Full Embed), Changelog-DB, Web-Export, Feedback-Buttons, Rollback, Metriken |
| `templates/base.py` | BaseTemplate mit `build_prompt()`, Classification-Rules (DE+EN), Release-Guide, Context-Files |
| `templates/gaming.py` | Gaming-Template: MayDay Sim (Storytelling, BOS-Sprache, Hype, 12 Badges) |
| `templates/saas.py` | SaaS-Template: GuildScout, ZERODOX (sachlich, Business-Value, 6 Badges) |
| `templates/devops.py` | DevOps-Template: ShadowOps, AI-Agent-Framework (kompakt, technisch, 4 Badges) |

**Trigger-Pfade (alle fuehren zu v6):**
- Webhook Push (Port 9090) → `_send_push_notification` → engine=v6 → `PatchNotePipeline.run()`
- Local Polling (60s) → gleicher Pfad
- Daily Cron (22:00, ≥15 Commits) → `generate_release()` direkt aus Git
- Weekly Cron (Sonntag 20:00, ≥3 Commits) → `generate_release()` direkt aus Git
- `/release-notes <projekt>` → `generate_release()` direkt aus Git, kein Minimum

**Self-Healing:** Wenn Pipeline ohne Commits aufgerufen wird (Restart, Webhook-Ausfall), holt Stufe 1 automatisch ALLE Commits seit dem letzten Release aus Git via `_gather_commits_since_last_release()`.

### Integrationen (`src/integrations/`)

#### Kern-Module (Packages)
| Package | Module | Zweck |
|---------|--------|-------|
| `orchestrator/` | `core`, `batch_mixin`, `planner_mixin`, `discord_mixin`, `executor_mixin`, `recovery_mixin`, `models` | Remediation-Orchestrator (Event-Batching, KI-Analyse, Fix-Ausfuehrung, Erfahrungslernen via KB) |
| `github_integration/` | `core`, `webhook_mixin`, `polling_mixin`, `event_handlers_mixin`, `ci_mixin`, `state_mixin`, `git_ops_mixin`, `notifications_mixin`, `ai_patch_notes_mixin` | GitHub Webhook Server, Patch Notes, CI/CD |
| `github_integration/jules_workflow_mixin.py` | Jules SecOps Workflow — PR-Handler, Gate-Pipeline (7 Schichten), Review-Orchestrierung, Multi-Agent Auto-Merge-Flow |
| `github_integration/agent_review/` | Multi-Agent Review Pipeline (seit 2026-04-14) — Adapter, Detector, Queue, API-Client, Outcome-Tracker, Daily-Digest |
| `github_integration/jules_state.py` | asyncpg-Layer fuer security_analyst.jules_pr_reviews, atomic Lock-Claim |
| `github_integration/jules_learning.py` | Few-Shot + Projekt-Knowledge Loader aus agent_learning DB |
| `github_integration/jules_review_prompt.py` | Claude-Prompt-Builder fuer strukturierte PR-Reviews |
| `github_integration/jules_gates.py` | Pure Loop-Schutz-Gates (Trigger-Whitelist, Cooldown, Cap, Circuit-Breaker) |
| `github_integration/jules_comment.py` | PR-Comment-Body-Builder + Self-Filter-Marker |
| `github_integration/jules_batch.py` | Nightly Outcome-Klassifizierung + jules_review_examples Update |
| `github_integration/jules_state_schema.sql` | DDL fuer security_analyst.jules_pr_reviews + jules_daily_stats View |
| `github_integration/jules_learning_schema.sql` | DDL fuer agent_learning.jules_review_examples |

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
| `circuit_breaker.py` | `CircuitBreaker` — Leichtgewichtiger CB (threshold + timeout), genutzt fuer AI Patch Notes |

### Schemas (`src/schemas/`)
| Datei | Zweck |
|-------|-------|
| `fix_strategy.json` | Remediation-Plaene (Einzelne Events) |
| `coordinated_plan.json` | Koordinierte Batch-Plaene (Orchestrator) |
| `incident_analysis.json` | Incident-Analyse (Self-Healing) |
| `patch_notes.json` | AI-generierte Patch Notes (v3: + seo_keywords, seo_category) |
| `analyst_session.json` | Security Analyst Session Output (+ areas_checked, finding_assessments) |
| `jules_review.json` | Claude PR-Review Output (verdict, blockers, suggestions, nits, scope_check) |

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

## Architektur-Historie

Alle grossen Architektur-Entscheidungen (seit 15.03.2026) sind in einer separaten Doku gebuendelt — damit CLAUDE.md unter der 40k-Context-Schwelle bleibt.

**→ [`docs/architecture-decisions.md`](docs/architecture-decisions.md)**

Enthaelt Details zu:
- Knowledge-Konsolidierung (3 DBs → 1 PostgreSQL)
- Security Analyst Learning (2-Phasen-Architektur, 4 DB-Tabellen, Quality-Gates)
- Token-Budget & AI-Call-Sicherheit (stdin, Quota-Erkennung, DSN via Config)
- Security-DB & Agent-Learning DB (PostgreSQL Schemas)
- Patch Notes Pipeline v6 (5-Stufen State Machine) + Post-Deploy-Fixes (2026-04-14)
- Security Engine v6 + SecurityScanAgent (seit 2026-03-24)
- Changelog Redesign (shared-ui v0.2.0), MayDay Einsatzprotokoll (2026-03-30)
- Discord-Nachrichten-Optimierung, Externes Mini-Dashboard
- Jules SecOps Workflow (seit 2026-04-11) + Multi-Agent Review Pipeline (seit 2026-04-14)

Operative Safety-Regeln zu diesen Themen stehen weiterhin in `.claude/rules/safety.md`.
