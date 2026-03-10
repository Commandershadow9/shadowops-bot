# ShadowOps Security Bot

## Stack
- **Runtime:** Python 3.12, discord.py 2.7
- **AI:** Dual-Engine (Codex CLI Primary + Claude CLI Fallback)
- **Monitoring:** Trivy, CrowdSec, Fail2ban, AIDE
- **Data:** SQLite (Knowledge DB), JSON State Files
- **Deploy:** systemd (system-level), logrotate
- **Version:** v4.0.0

## Services & Ports
| Service | Port | Zweck |
|---------|------|-------|
| Discord Bot | — | Gateway-Connection |
| Health Check | 8766 | systemd/Uptime Monitoring |
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
| `admin.py` | AdminCog | `/scan`, `/stop-all-fixes`, `/remediation-stats`, `/set-approval-mode`, `/reload-context` |
| `inspector.py` | InspectorCog | `/get-ai-stats`, `/projekt-status`, `/alle-projekte` |
| `customer_setup_commands.py` | CustomerSetupCommands | `/setup-customer-server` |

### Integrationen (`src/integrations/`)
| Datei | Zweck |
|-------|-------|
| `ai_engine.py` | Dual-Engine AI (Codex Primary + Claude Fallback) |
| `orchestrator.py` | Remediation-Orchestrator (Event-Batching, KI-Analyse, Fix-Ausfuehrung) |
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
| `github_integration.py` | GitHub Webhook Server (Push/PR Events) |
| `project_monitor.py` | Projekt-Health-Monitoring (Uptime, Response Times) |
| `deployment_manager.py` | Deployment-Verwaltung |
| `incident_manager.py` | Incident-Tracking und -Management |
| `customer_notifications.py` | Kunden-Benachrichtigungen (Patch Notes etc.) |
| `customer_server_setup.py` | Auto-Setup von Kunden-Discord-Servern |
| `guildscout_alerts.py` | GuildScout Alert-Forwarding (Port 9091) |
| `server_assistant.py` | Server Assistant (ersetzt Legacy Learning System) |
| `patch_notes_manager.py` | AI-generierte Patch Notes |
| `patch_notes_trainer.py` | Patch Notes Training/Finetuning |
| `patch_notes_feedback.py` | Feedback-Loop fuer Patch Notes Qualitaet |
| `knowledge_base.py` | SQLite Knowledge Database |
| `log_analyzer.py` | Log-Analyse und -Auswertung |
| `code_analyzer.py` | Code-Analyse fuer Fix-Strategien |
| `git_history_analyzer.py` | Git-History Analyse |
| `docker_image_analyzer.py` | Docker Image Vulnerability Analyse |
| `command_executor.py` | Sichere Command-Ausfuehrung (kein shell=True) |
| `impact_analyzer.py` | Impact-Analyse vor Fix-Ausfuehrung |
| `backup_manager.py` | Backup vor Fixes (/tmp/shadowops_backups/) |
| `service_manager.py` | systemd Service Management |
| `verification.py` | Post-Fix Verifikation |
| `research_fetcher.py` | Security Research Fetcher (Legacy) |
| `prompt_auto_tuner.py` | Prompt Auto-Tuning |
| `prompt_ab_testing.py` | Prompt A/B Testing |
| `llm_fine_tuning.py` | LLM Fine-Tuning Utilities |

### Integrationen — Unterverzeichnisse
| Verzeichnis | Zweck |
|-------------|-------|
| `integrations/fixers/` | Tool-spezifische Fixer (trivy, crowdsec, fail2ban, aide) |
| `integrations/ai_learning/` | Legacy AI Learning (knowledge_db, knowledge_synthesizer, continuous_learning_agent) |
| `integrations/analyst/` | Security Analyst (security_analyst, analyst_db, activity_monitor, prompts) |

### Utils (`src/utils/`)
| Datei | Zweck |
|-------|-------|
| `config.py` | Config-Klasse (laedt `config/config.yaml`) |
| `logger.py` | `setup_logger()` — Logging-Setup mit Rotation |
| `embeds.py` | `EmbedBuilder` + `Severity` — Discord Embed Templates |
| `discord_logger.py` | `DiscordChannelLogger` — Logging in Discord Channels |
| `health_server.py` | `HealthCheckServer` — aiohttp auf Port 8766 |
| `state_manager.py` | `StateManager` — Persistenter Bot-State (`data/state.json`) |
| `message_handler.py` | `MessageHandler` — Sicheres Senden (Split bei >2000 Zeichen) |
| `changelog_parser.py` | `ChangelogParser` — CHANGELOG.md Parser fuer Patch Notes |

### Schemas (`src/schemas/`)
| Datei | Zweck |
|-------|-------|
| `fix_strategy.json` | Remediation-Plaene (Orchestrator) |
| `incident_analysis.json` | Incident-Analyse (Self-Healing) |
| `patch_notes.json` | AI-generierte Patch Notes |
| `analyst_session.json` | Security Analyst Session Output |

### Konfiguration
| Datei | Zweck |
|-------|-------|
| `config/config.yaml` | Hauptkonfiguration (NICHT in Git!) |
| `config/config.example.yaml` | Template fuer neue Setups |
| `config/config.recommended.yaml` | Empfohlene Einstellungen |
| `config/safe_upgrades.yaml` | Curated Upgrade-Pfade fuer Packages |
| `config/logrotate.conf` | Logrotate-Konfiguration |
| `data/state.json` | Dynamischer Bot-State (Channel IDs, etc.) |
| `data/knowledge_base.db` | SQLite Learning Database |

### Deploy
| Datei | Zweck |
|-------|-------|
| `deploy/shadowops-bot.service` | systemd Unit-File (Quelle fuer /etc/systemd/system/) |

### Scripts (`scripts/`)
| Datei | Zweck |
|-------|-------|
| `restart.sh` | Bot neustarten (--pull, --logs) |
| `diagnose-bot.sh` | Diagnose: Status, Ports, Logs, Konflikte |
| `setup.sh` | Erstinstallation (venv, Dependencies, Service) |
| `update-config.sh` | Config-Migration bei Updates |
| `get_bot_invite.py` | Discord Bot Invite-URL generieren |
| `test_alerts.py` | Test-Plan fuer Discord Alert Channels |

### Dokumentation
| Pfad | Inhalt |
|------|--------|
| `docs/` | Aktive Referenz-Doku (API, Overview, Security Analyst) |
| `docs/guides/` | Benutzer-Anleitungen (Setup, Quickstart, Multi-Guild) |
| `docs/plans/` | Design- und Implementierungsdokumente |
| `docs/adr/` | Architecture Decision Records (6 ADRs) |
| `docs/archive/` | Veraltete Dokumentation (historisch) |
