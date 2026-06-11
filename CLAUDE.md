# CLAUDE.md — shadowops-bot

> Diese Datei ist die Wissensbasis fuer alle KI-Tools (Claude Code, Codex, Routine-Worker).
> Sie wird automatisch geladen. Halte sie aktuell — wenn die Doku luegt, lernt die KI Falsches.

## Projekt-Ueberblick

**ShadowOps** ist ein autonomer Security-Discord-Bot fuer Server-Monitoring (Fail2ban, CrowdSec, Docker, AIDE) mit Dual-Engine AI (Codex + Claude CLI), persistentem Lernsystem (SQL Knowledge Base) und Multi-Project-Management.

- **Repo:** https://github.com/Commandershadow9/shadowops-bot
- **Default-Branch:** `main`
- **Lizenz:** MIT
- **Maintainer:** Solo-Dev (CommanderShadow), entwickelt mit hohem KI-Anteil

## Tech-Stack

| Bereich | Technologie | Version |
|---|---|---|
| Sprache | Python | 3.9+ (CI nutzt 3.11) |
| Discord | discord.py | siehe requirements.txt |
| Datenbank | PostgreSQL | 3 DBs: security_analyst, agent_learning, seo_agent |
| Cache | Redis | — |
| AI Primary | Codex CLI | gpt-4o / gpt-5.3-codex / o3 |
| AI Fallback | Claude CLI | claude-sonnet-4-6 / claude-opus-4-6 |
| Container | Docker | mit Trivy fuer Scans |
| Service | systemd | `/etc/systemd/system/shadowops-bot.service` |
| Tests | pytest | 700+ Tests, unit + integration |
| Webhook | GitHub Webhooks | HMAC-SHA256 verifiziert |

## Architektur-Prinzipien

1. **Defense-in-Depth.** Jede destruktive Aktion hat Backup → Fix → Verify → Restart, mit Rollback-Pfad.
2. **Confidence-Based AI.** <85% confidence → Fix wird blockiert.
3. **Lernen statt Wiederholen.** Knowledge Base speichert jeden Fix-Versuch + Outcome. Beim Retry waehlt der Agent einen anderen Ansatz.
4. **Single Approval pro Plan.** Multi-Event-Batching, ein Plan, eine Genehmigung.
5. **Dry-Run-First.** Neue Fix-Strategien laufen erst im Dry-Run-Mode.
6. **Loop-Schutz.** 7 Schichten gegen Review-Loops (Trigger-Whitelist, SHA-Dedupe, Cooldown, Iteration-Cap, Circuit-Breaker, Time-Cap, Single-Comment-Edit).

## Verzeichnis-Struktur

```
shadowops-bot/
├── src/
│   ├── bot.py                    # Haupt-Bot
│   ├── cogs/                     # Slash-Commands (admin, inspector, monitoring, claude_cli, cron_heartbeat, customer_setup_commands, phase_5e_health_aggregator)
│   ├── integrations/             # Externe Systeme (siehe unten)
│   ├── patch_notes/              # Patch Notes Pipeline v6 (5-Stufen State Machine, ~2100 Zeilen)
│   ├── schemas/                  # JSON-Schemas fuer Structured Output (fix_strategy, patch_notes, incident_analysis, jules_review)
│   └── utils/                    # config, logging, embeds, state, alert_humanizer, health_server, message_handler, circuit_breaker, changelog_parser, process_lock
├── tests/
│   ├── unit/                     # 700+ Unit-Tests
│   ├── integration/              # End-to-End-Workflows
│   └── conftest.py
├── config/
│   ├── config.example.yaml       # Template (commited)
│   ├── config.yaml               # Real config (gitignored)
│   ├── DO-NOT-TOUCH.md           # Critical files protection
│   ├── INFRASTRUCTURE.md
│   └── PROJECT_*.md              # Per-projekt-Notizen
├── deploy/
│   ├── shadowops-bot.service          # systemd Bot-Service
│   ├── *-watchdog.{service,timer}     # Externe Uptime-Watchdogs (14 Watchdogs: HTTP/systemd/jq-filter/build-drift/state-drift + Backup-Test)
│   ├── shadowops-watchdog.env.example # Webhook-Env Template
│   └── MONITORING_SETUP.md            # Setup-Anleitung Watchdogs
├── .github/
│   └── workflows/
│       ├── ci.yml                     # Test-Pipeline (pytest)
│       ├── worker-dedup-gate.yml      # Verhindert Duplikat-Worker-PRs
│       ├── auto-label-pr.yml          # Auto-Labeling nach Conventional Commits
│       └── external-uptime.yml        # Externer Uptime-Backstop (GitHub-hosted, VPS-unabhängig) — pingt zerodox.de + guildscout.eu alle 5 min, Discord-Alert via Repo-Secret UPTIME_DISCORD_WEBHOOK (seit #277-Folge)
├── scripts/                      # Wartungs-Skripte
├── docs/
│   ├── reference/api.md          # API-Referenz
│   ├── architecture/             # Tiefen-Doku (security-engine/, jules-workflow/, multi-agent-review/)
│   ├── operations/               # Setup, Rollout, Daily-Ops (setup.md, quickstart.md, ...)
│   ├── adr/                      # Architecture Decision Records
│   ├── design/                   # Design-Dokumente (aktuelle Planung)
│   └── plans/                    # Aeltere Design-Dokumente (archiviert)
├── data/                         # Runtime-Daten (gitignored)
├── logs/                         # Logs (gitignored)
├── .claude/                      # KI-spezifische Configs
└── .routines/                    # Worker State + Prompts (siehe unten)
```

### Module unter `src/integrations/`

- `ai_engine.py` — Dual-Engine Router (Codex Primary, Claude Fallback)
- `ai_learning/` — Kontinuierlicher Lernagent: Echtzeit-Systemanalyse, Git-Commit-Mustererkennung, Code-Struktur-Analyse, Security-Event-Korrelation via AI Engine. Klassen: ContinuousLearningAgent, LearningInsight, LearningSession (continuous_learning_agent.py); knowledge_db.py, knowledge_synthesizer.py.
- `smart_queue.py` — Analyse-Pool (Semaphore=3) + serieller Fix-Lock + Circuit Breaker
- `verification.py` — Pre-Push Pipeline (Confidence ≥85% → Tests → Claude-Verify → KB-Check)
- `orchestrator/` — Multi-Event-Batching (10s Fenster) + Approval-Flow (Package: core, batch_mixin, planner_mixin, executor_mixin, recovery_mixin, discord_mixin, models)
- `event_watcher.py` — Lauscht auf Fail2ban/CrowdSec/AIDE/Docker-Events
- `knowledge_base.py` — SQL Learning (fix_attempts, fix_verifications, finding_quality, scan_coverage)
- `code_analyzer.py` — Code Structure Analyzer (Git-History + AST)
- `context_manager.py` — RAG: Project-Context + DO-NOT-TOUCH + Infra
- `github_integration/` — Webhooks mit HMAC-SHA256 Verification + Jules Workflow (Package: core, webhook_mixin, event_handlers_mixin, jules_workflow_mixin, notifications_mixin, ci_mixin, agent_review/)
- `security_engine/` — Autonomer SecurityScanAgent (scan_agent.py), CircuitBreaker, DB-Layer (db.py), Fixer-Adapters, ActivityMonitor, LearningBridge, Prompts (Package: engine, scan_agent, reactive, proactive, deep_scan, executor, fixer_adapters, learning_bridge, activity_monitor, prompts, models, db, migrations)
- `security_engine/team/` — Security-Agent-Team (**W1 LIVE seit 2026-07-09, 7d-Soak läuft**, #290/PR #339): `contracts.py` (SecurityJob/JobResult), `base_worker.py` (Lifecycle/Exception-Isolation), `orchestrator.py` (Fan-out + trigger-Durchreichung), `orchestrator_main.py` (**echter `sec:trigger`-Subscribe-Loop**), `runner.py`, `workers/npm_audit_worker.py`. **Betrieb:** systemd-User-Units `security-orchestrator` + `security-npm-audit-worker` (env: `~/.config/shadowops-security-team.env`, chmod 600, Redis-Auth!), `SECURITY_TEAM_ENABLED=true`, Live-Config-Sektion `security_team:` (guildscout+zerodox, npm_audit). Crons: Trigger 05:23, `scripts/security-job-reaper.sh` 06:41, `scripts/security-soak-compare.sh` 07:31 (`logs/security-soak-w1.log`). Selbstüberwachung: `security-freshness-watchdog` (pg-freshness auf `sec_jobs`, 26h). Monolith bleibt bis Soak-Ende (~2026-07-16) Source-of-Truth für LLM-Scans. Spec v2: `docs/design/2026-07-09-security-agent-team-v2-spec.md` (Fix-Kanal = Claude-CLI + PR-Gate, Jules tot; 5 Wellen), Plan: `docs/plans/2026-07-09-security-agent-team-w1.md`.
- `fixers/` — Konkrete Fix-Implementierungen: fail2ban_fixer.py, crowdsec_fixer.py, aide_fixer.py, trivy_fixer.py, walg_fixer.py
- `project_monitor.py` — Multi-Project Health-Checks + **Zentrale Monitoring-Engine** (2026-06, #277): deklaratives `checks:`-Inventar pro Projekt in config.yaml via `check_definitions.py`/`check_runner.py`/`heal_executor.py`/`maintenance_gate.py`. Check-Typen `http` (+header/POST/json_path/json_schema), `script`, `container` (network-attached). Gestuftes Heal (reversibel-autonom / approval / alert-only) + Circuit-Breaker + Maintenance-Gate (`/maintenance`-Command). 6 ZERODOX-Checks live (analytics-bridge mit Auto-Heal real verifiziert), Watchdogs bleiben als externer Dead-Man (Defense-in-Depth). **Import-Regel:** paket-intern relativ (`from .x`), NICHT `from src.integrations.x` (Bot läuft PYTHONPATH=src). Spec: `docs/2026-06-09-zentrales-monitoring-auto-health-design.md`, Inventar: `docs/MONITORING_INVENTORY.md`, Pläne: `docs/plans/2026-06-{09,10}-monitoring-*.md`.
- `deployment_manager.py` — Auto-Deploy mit Backup/Rollback. **WICHTIG:** Project-Name-Lookup ist dash↔underscore-tolerant (`mayday-sim` ↔ `mayday_sim`, seit 2026-05-25 — siehe `.claude/rules/safety.md`). Gleiche Logik in `github_integration/ci_mixin.py:_trigger_deployment()`.
- `incident_manager.py` — Incident Threads in Discord
- `customer_notifications.py` — Customer-Facing Alerts (Multi-Guild)
- `fail2ban.py` / `crowdsec.py` / `aide.py` / `docker.py` — Security-Event-Quellen (Monitoring-Integrationen)
- `approval_modes.py` — 3-Stufen-Approval-System (PARANOID/BALANCED/AGGRESSIVE) für Auto-Remediation: entscheidet Auto-Execute vs. manuelle Freigabe je nach Risiko + Confidence
- `auto_fix_manager.py` — Reaction-basierter Flow für Code-Fix-Vorschläge (✅ umsetzen / 🧪 nur Tests / ❌ verwerfen), führt Tests/Lint aus + berichtet in Discord. Safety: keine Commits/Merges/Deploys, Pfad-Whitelist, Zeitlimit
- `backup_manager.py` — Automatisches Backup & Restore (Dateien, Verzeichnisse, Docker-Image-Tags, PostgreSQL-Dumps) mit Auto-Rollback + Retention-Policy
- `changelog_db.py` — Zentrale async SQLite-Changelog-DB für alle Projekte (Patch Notes v3): Paginierung, JSON-Felder, Upsert-Logik
- `command_executor.py` — Sichere Shell-Command-Ausführung (Timeout, Output-Capture, Dry-Run/Validate-Mode, Command-Sanitization)
- `content_sanitizer.py` — Filtert sensible Infos (Dateipfade, IPs/localhost, Ports, Config-Namen, API-Pfade) aus AI-generierten Patch Notes vor Discord-/Web-Versand
- `customer_server_setup.py` — Legt automatisch Channels mit korrekten Permissions auf Kunden-Discord-Servern an (Admin-Kategorien je Projekt)
- `docker_image_analyzer.py` — Unterscheidet externe vs. eigene Docker-Images (Dockerfile-Erkennung) + Update-Detection
- `git_history_analyzer.py` — Analysiert Git-Commits (häufig geänderte Files, Fix-/Security-Commits, Author-Expertise) als AI-Context
- `guildscout_alerts.py` — Webhook-Handler (HMAC-verifiziert) für GuildScout-Bot-Alerts (Verification/Errors/Health) → formatierte Discord-Posts
- `health_schema_v1.py` — Dependency-freies Dataclass-Modell + Parse-Validierung für den Phase-5e Health-Aggregator (Quelle: ZERODOX HEALTH_SCHEMA_V1.md)
- `impact_analyzer.py` — Bewertet Auswirkung von Security-Fixes auf laufende Projekte (betroffene Services, Downtime-Risiko, DO-NOT-TOUCH-Validierung)
- `learning_notifier.py` — Automatische Discord-Posts im 🧠-ai-learning Channel über Agent-Erkenntnisse (Session-Summary, Feedback, Wöchentliches Learning, Meilensteine)
- `llm_fine_tuning.py` — Exportiert Trainingsdaten im JSONL-Format für LLM-Fine-Tuning der Patch-Notes-Generierung (Quality-Score-gefiltert)
- `log_analyzer.py` — Multi-Tool Log-Parsing (Fail2ban/CrowdSec/Docker/ShadowOps) für Pattern-Erkennung + Anomalie-Detection
- `patch_notes_batcher.py` — Sammelt ALLE Commits und gibt sie kontrolliert frei (Cron täglich/wöchentlich, Notbremse ≥Schwelle, manuell; max 1 Auto-Release/Projekt/Tag, 24h-Cooldown)
- `patch_notes_feedback.py` — Sammelt User-Feedback zu Patch Notes (Discord-Buttons Like/Rate, Text-Modal, Legacy-Reactions)
- `patch_notes_learning.py` — PostgreSQL-basierter Feedback-Loop (agent_learning DB): Reactions → Variant-Gewichtung → bessere Generierung
- `patch_notes_trainer.py` — AI-Training für Patch-Notes-Qualität (CHANGELOG-Input, Few-Shot-Beispiele, Feedback, Prompt-Optimierung, Quality-Scoring)
- `patch_notes_web_exporter.py` — Exportiert Patch Notes als SEO-optimiertes JSON/Markdown + API-POST an Projekt-Backends (DB-basiert primär, File-Fallback)
- `prompt_ab_testing.py` — A/B-Testing für Prompt-Varianten, trackt welche Variante am besten performt
- `prompt_auto_tuner.py` — Justiert Prompts automatisch anhand Performance-Feedback (nutzt prompt_ab_testing + patch_notes_trainer)
- `research_fetcher.py` — Abgesicherter HTTP-Fetcher mit Domain-Allowlist (PyPI/npm/GitHub API+Raw), Größen-/Timeout-Limit, Discord-Logging
- `self_healing.py` — Self-Healing-Coordinator: AI-gestützte Retry-Logik + Circuit Breaker, orchestriert command_executor/backup_manager/impact_analyzer/service_manager/fixers
- `server_assistant.py` — Token-sparsamer Server-Assistent: tägliches lokales Housekeeping (0 Token) + wöchentlicher Security-Report + event-getriebenes Git-Push-Security-Review
- `service_manager.py` — Projekt-Service-Steuerung (Start/Stop/Restart) mit Graceful-Shutdown, Health-Checks, Dependency-Ordering, Discord-Downtime-Notifications
- `zerodox_auto_fix_gate.py` — **Latent (config-gated, default off):** ZERODOX Auto-Fix Pre-Merge-Gate (Welle 16, #270/#271/#844). Klasse `AutoFixGate` ist implementiert+getestet, aber in keinem Produktivpfad instanziiert. Aktivierung via Config-Flag `zerodox.auto_fix_pipeline.enabled`

## Externes Monitoring (seit 2026-05-17 — Defense-in-Depth)

Zusätzlich zum internen `project_monitor.py` laufen 14 unabhängige user-systemd Watchdogs (Zyklen: 5–15 min je nach Watchdog, cmdshadow-design 1h, Selbstpflege-Watchdogs stündlich/täglich, Backup-Test monatlich) und posten Down/Recovery direkt via Discord-Webhook in `#🩺-uptime-alerts` (NICHT über den Bot — funktioniert auch wenn shadowops-bot tot ist):

| Watchdog | Mode | Target |
|---|---|---|
| `shadowops-watchdog` | http | http://127.0.0.1:8766/health |
| `shadowops-drift-watchdog` | systemd-state + drift | shadowops-bot Service-State + NRestarts-Loop + User-Unit-Drift (Vorfall 2026-05-20) |
| `zerodox-watchdog` | http | https://zerodox.de/api/health |
| `zerodox-akquise-ai-watchdog` | http | http://172.19.0.1:9300/health (Bridge-Gateway, kein bot_ready) |
| `guildscout-watchdog` | http | http://localhost:8765/health |
| `mayday-sim-watchdog` | http | https://maydaysim.de/api/health |
| `mayday-ci-runner-watchdog` | http + jq-filter | http://10.8.0.10:9100/health, filter=`.components.ci_runner.ok` (#mayday-sim#425) |
| `mayday-sim-build-drift-watchdog` | build-drift | https://maydaysim.de/api/build-id vs. origin/main HEAD — Alert bei >30 min Drift, Zyklus 15 min (#mayday-sim#416) |
| `mayday-scheduler-watchdog` | container | leitstelle-scheduler (Docker-Health) — Game-Tick-Owner seit SB3 (#mayday-sim#498), unüberwachter SPOF ohne diesen Watchdog |
| `ai-agent-framework-watchdog` | systemd | guildscout-feedback-agent, zerodox-support-agent, seo-agent |
| `cmdshadow-design-watchdog` | systemd-result | cmdshadow-design-healthcheck.service (max_age=36h, 1h-Cycle) |
| `memory-watchdog` | meminfo | RAM ≥90% oder Swap ≥80% auf VPS, Frühwarnung vor OOM-Cascade (seit 2026-05-25, Vorfall logind-Kill durch earlyoom) |
| `disk-hygiene-watchdog` | disk + auto-prune | Auto-Prune (docker builder/image + journald) bei Disk >85%, Alarm >90% (stündlich, Selbstpflege seit 2026-05-30) |
| `doku-drift-watchdog` | doku-drift | Container-Ports vs. Port-Map + MEMORY.md-Limit (<200), nur Alarm (täglich 06:30, Selbstpflege seit 2026-05-30) |
| `ki-cost-watchdog` | ki-cost | Token/Kosten-Rollup Claude+Codex aus JSONL + Anomalie-Alarm (täglich 07:15, Selbstpflege seit 2026-05-30) |
| `shadowops-backup-test` | — | monatlich 1. d. Monats, Wrapper um `~/ZERODOX/scripts/backup-test.sh` |

**GitHub Actions Externer Backstop (seit 2026-06-10, #277-Folge):** `.github/workflows/external-uptime.yml` läuft auf GitHub-hosted `ubuntu-latest` (VPS-unabhängig), pingt `zerodox.de/api/health` + `guildscout.eu/health` alle 5 min, alarmiert via Repo-Secret `UPTIME_DISCORD_WEBHOOK` in `#🩺-uptime-alerts`. Drei Alert-Klassen: UNREACHABLE (DNS/Totalausfall), ERROR (5xx), DEGRADED (200 + status≠ok). Reiner Backstop bei VPS-Totalausfall — schnelle Erkennung machen die internen Watchdogs.

**Skripte:** `scripts/service-watchdog.sh` (generisch, parametrisiert), `scripts/bot-watchdog.sh` (Backward-Compat), `scripts/sync-watchdog-units.sh` (IaC-Sync: spiegelt `deploy/*-watchdog.{service,timer}` als Symlinks in `~/.config/systemd/user/`, idempotent, `--dry-run`/`--prune`/`--strict`-Flags, `--strict` exit 1 bei Orphans fuer CI-Drift-Gate, seit #294). **Service-Files:** `deploy/<name>-watchdog.{service,timer}`. **Webhook-Config:** `~/.config/shadowops-watchdog.env` (chmod 600). **Setup-Anleitung:** [`deploy/MONITORING_SETUP.md`](./deploy/MONITORING_SETUP.md).

**Regel beim Hinzufügen eines neuen kritischen Services:** Watchdog-Service-File aus `deploy/` kopieren, Env-Vars anpassen, Symlink in `~/.config/systemd/user/`, `daemon-reload + enable + start`, Recovery-Alert testen. Tabelle hier UND in `MONITORING_SETUP.md` erweitern.

### JSON-Path-Filter für aggregierte Health-Endpoints (seit 2026-05-20, mayday-sim#437)

Wenn der Health-Endpoint mehrere Komponenten aggregiert (z.B. `runner-health.service` auf V-Server1 deckt `ci_runner` + `github_runners` + `load` ab) und HTTP 503 zurückgibt sobald **irgendeine** Komponente kaputt ist, würde der Standard-Watchdog False-Positives feuern. Lösung: `WATCHDOG_HEALTH_JQ_FILTER` in der ENV-Datei setzen.

```env
WATCHDOG_HEALTH_JQ_FILTER=.components.ci_runner.ok
# Alternative: alerts[]-Filter
WATCHDOG_HEALTH_JQ_FILTER='[.alerts[] | select(.component == "ci_runner" and .severity == "critical")] | length == 0'
```

Wenn gesetzt: HTTP-Status wird **ignoriert** (außer curl-Fehler), jq-Expression ist Truth-Source. Test-Coverage: `tests/unit/test_service_watchdog_jq_filter.py` (8 Tests, Stub-HTTP-Server).

### Cross-Repo-Contribution-Pfad (seit 2026-05-20)

**keydev (`@hamannmanfred90-lgtm`) hat write-Access** auf diesem Repo via GitHub Collaborator-Status. Pattern für Cross-Team-Beiträge an `service-watchdog.sh` und `deploy/*-watchdog.*`:

1. keydev (oder anderer Cross-Team-Contributor) erstellt Branch `feat/<topic>` direkt im Repo, kein Fork nötig
2. PR auf main, normales CI-Setup (`ci.yml` läuft pytest)
3. cmdshadow reviewed + merged
4. **Roll-out-Step bleibt bei cmdshadow** (ENV-File-Updates + `systemctl --user`-Restart sind cross-user-Operationen → nicht ohne Sudoers-Aufweichung delegierbar)

Read-Only ACL auf `scripts/` und `deploy/` für `keydev` ist gesetzt (`getfacl` zeigt `user:keydev:r-x`) — er kann Files lokal lesen und Tests laufen lassen. ENV-Files (`~/.config/*.env`) bleiben chmod 600, kein Read-Access, weil sie Webhook-Secrets enthalten.

## Coding-Conventions

- **Naming:** `snake_case` fuer Funktionen/Variablen, `PascalCase` fuer Klassen, `UPPER_CASE` fuer Konstanten.
- **Type-Hints:** Pflicht fuer neue Funktionen (auch wenn mypy noch nicht strict laeuft).
- **Docstrings:** Google-Style, mindestens fuer Public-Funktionen.
- **Async-First:** discord.py + aiohttp — neue I/O ist `async`.
- **Fehler-Handling:** Niemals leere `except:`. Mindestens loggen + re-raise oder klar entscheiden.
- **Logging:** `from src.utils.logger import get_logger` — niemals `print()`.
- **Secrets:** AUSSCHLIESSLICH via Env-Vars (DISCORD_BOT_TOKEN, OPENAI_API_KEY, ANTHROPIC_API_KEY, GITHUB_TOKEN). Niemals in Code, niemals in `config.yaml`.
- **Tests:** Neue Module brauchen Tests in `tests/unit/test_<module>.py`. Fixtures in `conftest.py` wiederverwenden.
- **Conventional Commits:** `fix:`, `feat:`, `refactor:`, `perf:`, `docs:`, `chore:`.

## DO und DON'T

### DO
- Backups vor destruktiven Aktionen (`deployment_manager.py` macht das schon — beibehalten).
- Confidence-Score in jeden AI-Output reflektieren.
- Knowledge-Base updaten nach Fix (success/failure egal).
- DO-NOT-TOUCH-Liste pruefen bevor Files angefasst werden.
- Discord-Updates in Echtzeit (Backup → Fix → Verify → Restart sichtbar).

### DON'T
- **Niemals** `config.yaml` oder `.env` committen.
- **Niemals** Secrets in Logs schreiben (Logger maskiert standardmaessig nicht — selbst mitdenken).
- **Niemals** `enforce_admins: true` in der Branch-Protection — sonst sperrt sich Solo-Dev aus.
- **Niemals** Public API in `src/integrations/*` aendern ohne BREAKING-Markierung im Commit.
- **Niemals** `pytest` mit Live-DB laufen lassen — Fixtures nutzen.
- **Niemals** Discord-Token, Webhook-Secrets, oder DB-Passwords im Code referenzieren — nur `os.environ`.
- **Niemals** systemd-Service per Worker-PR aendern — das ist Server-State, nicht Repo-State.

## Setup-Commands

```bash
# Dependencies
pip3 install -r requirements.txt
pip3 install -r requirements-dev.txt

# Config (einmalig)
cp config/config.example.yaml config/config.yaml
chmod 600 config/config.yaml
# Secrets als Env-Vars in ~/.bashrc oder Service-EnvFile:
#   export DISCORD_BOT_TOKEN="..."
#   export OPENAI_API_KEY="..."
#   export ANTHROPIC_API_KEY="..."
#   export GITHUB_TOKEN="..."        # Optional: GitHub-Integration + Webhook-Auto-Create

# Lokal testen
python3 src/bot.py

# Tests
pytest tests/ -v
pytest tests/ --cov=src --cov-report=html

# Service (Production)
sudo systemctl restart shadowops-bot
sudo journalctl -u shadowops-bot -f
```

## Beispiele fuer typische Tasks

### Neuen Slash-Command hinzufuegen
1. Cog-Datei: `src/cogs/<bereich>.py` — Command als Methode mit `@app_commands.command(...)`.
2. Cog in `bot.py` laden (`await self.add_cog(...)` falls noch nicht generisch gelistet).
3. Test: `tests/unit/test_<bereich>.py` mit Mock-Interaction.
4. Doku: README → "Slash Commands" Sektion ergaenzen.

### Neue Security-Integration (analog zu fail2ban.py)
1. Modul in `src/integrations/<name>.py` mit Klasse, async `check()`-Methode, gibt strukturierte Events zurueck.
2. Im `event_watcher.py` registrieren.
3. Config-Key in `config.example.yaml` ergaenzen.
4. Test mit Mock-Subprocess-Output.
5. README + `docs/architecture/security-engine/README.md` updaten.

### Neuen AI-Provider als Engine
1. Klasse in `ai_engine.py` analog zu `CodexEngine` / `ClaudeEngine`.
2. In `TaskRouter` registrieren mit Routing-Regeln.
3. Quota-Failover testen (Mock-Output mit Limit-Marker).
4. Tests in `test_ai_engine.py` ergaenzen.
5. CLAUDE.md (diese Datei) im Tech-Stack-Block updaten.

## Routine-Worker

Drei Worker laufen automatisch (siehe `.routines/prompts/`):

- **Cleanup-Crew** — `.routines/prompts/cleanup.md` — taeglich 03:00 + 14:00. Refactor, Dead-Code, Konsistenz, Quick-Wins. State: `.routines/state/cleanup.json`
- **Guardian** — `.routines/prompts/guardian.md` — taeglich 05:00 + bei Push-Webhook. SAST, Dependency-Scan, Secret-Scan, passive Live-Checks gegen `zerodox.de` (NICHT gegen shadowops-bot — das ist ein Server-side Bot, kein Public-Endpoint). State: `.routines/state/guardian.json`
- **Doku-Kurator** — `.routines/prompts/doku.md` — taeglich 02:00. Drift, Luecken, KI-Konformitaet, Anti-Bloat. State: `.routines/state/doku.json`

Worker-Konventionen:
- Branches: `routine/<worker>/<topic>` (z.B. `routine/cleanup/dedupe-event-watcher`)
- PR-Labels: `status:routine-generated`, `worker:<name>`, `type:<refactor|fix|...>`, `area:<modul>`
- Bei Unsicherheit: Issue statt PR (`status:needs-info`).

## Statistik (Stand v5.1)

20.000+ LoC, 700+ Tests, 3 PostgreSQL DBs (21+8+11 Tabellen), 4 Security-Integrationen, 20 Discord-Commands, 3 Monitored Projects (GuildScout, ZERODOX, AI Agents).

## Aktuelle Doku

- [README.md](./README.md)
- [docs/architecture/security-engine/README.md](./docs/architecture/security-engine/README.md)
- [docs/operations/setup.md](./docs/operations/setup.md)
- [docs/reference/api.md](./docs/reference/api.md)
- [docs/README.md](./docs/README.md)
- [config/DO-NOT-TOUCH.md](./config/DO-NOT-TOUCH.md)

## Letztes Update dieser Datei

2026-07-09 — Security-Agent-Team Phase 0 + W1 (#290 / PRs #333, #338, #339 — alle gemerged + live): **Phase 0:** AI-Kern des Monolithen war seit 07.07. komplett tot — Doppel-Ursache: `security_analyst.model gpt-5.3-codex` vom ChatGPT-Abo nicht mehr unterstützt (HTTP 400) + hartcodierter Claude-CLI-Pfad `~/.local/bin/claude` existiert seit npm-Umzug nicht mehr (FileNotFoundError in Fallback UND Fix-Phase). Fixes: `resolve_claude_cli_path()` (env `CLAUDE_CLI_PATH` → configured → which → Fallback-Liste), Live-Config auf `gpt-5.5` (real getestet; `gpt-5.5-codex` → 400), Reflection-KeyError `'"quality_score"'` = `str.format()` auf Template mit JSON-Beispiel → `render_reflection_prompt()` mit `.replace()`, stale Server-Fakten (Debian 12/8 GB → Debian 13/64 GB) in 7 Prompt-Stellen, fail2ban aus Tool-Listen (nicht installiert, #295). **Live verifiziert:** Session #539 OK via codex/gpt-5.5 (6 Findings/6 Issues) — dabei nächsten verdeckten Bug gefangen: Fix-Phase crashte bei LLM-`summary` als dict (`dict + str`) → #338. **W1 (PR #339, zweistufig subagent-reviewt):** `orchestrator_main` = echter `sec:trigger`-Subscribe-Loop, `trigger`-Durchreichung, Trigger-/Reaper-/Soak-Scripts, `security-freshness-watchdog`, Modell-Default-Hygiene repo-weit. Reviews fingen CRITICAL (`findings` hat `found_at`, NICHT `created_at` — Soak-Script wäre stumm gewesen) + IMPORTANT (psql-Command-Tag-Off-by-one bei `RETURNING 1 | wc -l` → CTE-Zählung; gleicher Bug im SEO-Reaper gefixt, agents@a472627). **Ops:** Units + env-Datei (600) + 3 Crons (05:23/06:41/07:31) installiert, erster E2E-Lauf 2×`ok`/14 Findings (GuildScout: 1 CRITICAL + 4 HIGH npm!), Watchdog getestet + Timer aktiv. **7d-Soak bis ~2026-07-16**, dann W2. Dazu Phase-0-Ops: Trivy 0.72.0 + Daily-Scan 04:15 reaktiviert (`~/scripts/trivy-daily-scan.sh`, Bot-Format; Erstlauf 15 Images: 15 CRITICAL/242 HIGH), kptr_restrict=1, Backup-Dirs 700/750, Dashboard-Binding 127.0.0.1, 9 obsolete Issues geschlossen (#313 #314 #295 #310 #311 #327 #328 #330 #331). Spec v2: `docs/design/2026-07-09-security-agent-team-v2-spec.md`.

2026-06-14 — Externer Deploy-Post erreichte Kunden-Channel nicht (#316 / Issue mayday-sim#504): `_forward_deploy_to_external` (in `deployment_manager.py`) schlug die Projekt-Config mit dem **rohen** GitHub-Repo-Namen `mayday-sim` (Bindestrich) nach — Config-Key ist `mayday_sim` (Underscore) → leere `external_notifications` → der externe Deploy-Post im `#🚀-deploy-log` (mayday-sim Kunden-Discord, `1486899717362421840`) kam **nie** an, obwohl `deploy_channel_id` korrekt war. Der **interne** `#🚀-deployment-log` bekam Posts, weil `_send_deployment_success` den **globalen** `deployment_channel_id` nutzt (kein Projekt-Lookup) — nur der externe Forward hing am kaputten Lookup. **Gleicher dash/underscore-Bug wie 2026-05-25** (PR #449/#450) — `deploy_project`/`_trigger_deployment` waren damals gefixt, `_forward_deploy_to_external` übersehen. Fix: identischer Fallback-Lookup. 3 neue Tests (`test_deployment_external_forward.py`), 41 Deploy/GitHub-Tests grün. **In Produktion verifiziert:** erster Post im bis dahin leeren Channel exakt zum Deploy `ac4af87` (16:04:25). Restschulden (gleiches Muster) in `notifications_mixin.py` → #317. Doku: `docs/runbooks/discord-routing.md` um externe Kunden-Deploy-Posts ergänzt.

2026-06-03 — Auto-Deploy bei DIREKTEM Push (per-Projekt opt-in + per-SHA-Dedup, Commit `ba88ef7`): Bisher deployte der Bot NUR bei PR-Merge; direkter Push auf `main` → nur Discord-Alert (PR-Review-Gate, global). Für Solo-Operator-Projekte unpraktisch — Code lag in main, ging aber nicht live (Anlass: ZERODOX-Push `89b31cf1..4f9eea07` → kein Deploy, manuell via `deploy.sh` nachgezogen). Neu: per-Projekt `deploy.allow_direct_push: true` (default false → **alle anderen Projekte unverändert PR-only**), **nur ZERODOX aktiviert** (config.yaml gitignored, im `config.example.yaml` dokumentiert). `handle_push_event` deployt bei opt-in über DIESELBE gehärtete Pipeline wie PR-Merges (`_trigger_deployment` → Wait-for-CI → `deploy.sh` Pre-Flight-Hard-Gate → kein Deploy bei roter CI). Neuer per-SHA-Dedup `_reserve_deploy` (StateMixin): PR-Merge feuert push UND pull_request (gleicher Merge-SHA) → nur der erste deployt; schützt auch vor GitHub-Doppel-Webhooks (vgl. 15×-Trigger-Bug 2026-05-15). `_project_allows_direct_push` (CIMixin, dash/underscore-tolerant wie `_trigger_deployment`). 6 neue Unit-Tests (`test_direct_push_deploy.py`), 66 grün.

2026-06-02 — Security-Agent-Team P1 (#290 / PR #307 gemerged + deployt): Fundament neben dem `scan_agent`-Monolithen unter `src/integrations/security_engine/team/` (contracts/base_worker/orchestrator/runner/npm_audit_worker + `*_main.py`-Entrypoints + systemd-Templates). Architektur: **Always-on systemd-Worker + Redis-Token-Cap** (Cap-Enforcement erst P2). Feature-Flag `SECURITY_TEAM_ENABLED` default OFF, Monolith bleibt Source-of-Truth, NICHT in `bot.py` verdrahtet — kein Verhaltensunterschied im Bot. Geteilter `SecurityDB.store_finding()`-Helper (deep_scan + scan_agent migriert), `sec_jobs` + `finding_fingerprint` jetzt in `_ensure_schema()`. **2 neue Deps: pydantic + redis** (redis war bisher nur lazy genutzt → nie gepinnt). 30 Tests, subagent-getrieben gebaut; Reviews fingen 2 Critical (Schema-Drift `finding_fingerprint`, ENOLOCK-Fehlscan) + 1 Important (redis-Pin). Spec/Plan: `docs/design|plans/2026-06-02-security-agent-team-p1*`. Regeln in `.claude/rules/safety.md`. **Offen (Ops, kein Code): systemd-Units installieren + Flag für 7d-Soak aktivieren.**

2026-06-02 — Patch-Notes-Archivierung committet+pusht (#302 / PR #304): `_archive_release_notes()` in `stages/distribute.py` ließ den Deploy-Checkout dirty (Archiv + Template-Reset ohne git-Commit) → nächster Auto-Deploy-`git pull` brach ab (Vorfall mayday-sim PR #489). Fix: `_run_git_quiet` + `_commit_and_push_archive` stagen GENAU die zwei Archiv-Dateien (nie `git add -A`), best-effort + git crasht die Pipeline nie. Härtung aus adversarialem Review: `_sanitize_git_error()` (kein Token/URL im Log), SemVer-Version-Guard, Bot-Identität via `git -c` (keine config-Pollution). 18 Tests. Regel in `.claude/rules/safety.md` (Patch Notes Pipeline v6).

2026-06-02 — Cross-Repo-Backlog-Sweep: #292 ki-cost-watchdog Cache-Read/Write-Pricing (0.1×/1.25× Input, `compute_cost()` + Env-Keys) + #293 geteilte `scripts/lib/discord-send.sh` (429-Resilienz: Jitter + Retry-After, eingeklinkt in service-/disk-hygiene-/doku-drift-/memory-watchdog, `bot-watchdog.sh` unberührt). Beide mit Tests, gemerged. Begleit-Doku: discord-send.sh in `.claude/rules/infrastructure.md` Scripts-Tabelle.

2026-05-25 — Auto-Deploy Project-Name-Lookup-Fix (dash↔underscore-Toleranz für `mayday-sim` ↔ `mayday_sim`, neue Regel in `.claude/rules/safety.md`). Anlass: mayday-sim PR #449/#450 lagen 14h ohne Auto-Deploy.

2026-05-24 — Watchdog-Tabelle auf 10 erweitert (shadowops-drift, zerodox-akquise-ai, mayday-ci-runner, mayday-sim-build-drift), Security-Sweep #265/266/268/272 abgearbeitet (siehe CHANGELOG).
