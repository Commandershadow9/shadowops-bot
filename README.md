# 🗡️ ShadowOps - Active Security Guardian v5.1 🚀

**Status:** AKTIV | **Version:** 5.1.0 | **Letzte Aktualisierung:** 18.05.2026

**ShadowOps** ist ein **vollständig autonomer Security Guardian** mit lernfähigem AI Security Analyst, KI-gesteuerter Auto-Remediation, adaptiver Session-Steuerung und wachsender Knowledge-DB — kein statischer Scanner, sondern ein **System das aus seinen Erfahrungen lernt und immer besser wird**.

> 📖 **Security Analyst Doku:** [docs/SECURITY_ANALYST.md](./docs/SECURITY_ANALYST.md)
> 📚 **Dokumentations-Übersicht:** [DOCS_OVERVIEW.md](./DOCS_OVERVIEW.md)
> 🔧 **API Dokumentation:** [docs/reference/api.md](./docs/reference/api.md)
> 🚀 **Setup Guide:** [docs/SETUP_GUIDE.md](./docs/SETUP_GUIDE.md)
> 📐 **Learning Pipeline Design:** [docs/plans/2026-03-18-analyst-learning-pipeline-design.md](./docs/plans/2026-03-18-analyst-learning-pipeline-design.md)

## ⚡ Highlights v5.1

### 🔗 **Jules SecOps Workflow (v5.1 - NEW)**

Automatisierter Security-Fix-Workflow mit Google Jules + Claude Opus Review:

- **Hybrid-Fix:** ScanAgent fixt Server-Haertung selbst, delegiert Code-Fixes an Jules via GitHub-Issue
- **Strukturierter Review:** Claude Opus prueft jeden Jules-PR (BLOCKER/SUGGESTION/NIT), deterministisches Verdict
- **7-Schichten Loop-Schutz:** Trigger-Whitelist, SHA-Dedupe, Cooldown, Iteration-Cap, Circuit-Breaker, Time-Cap, Single-Comment-Edit
- **Selbstlernend:** Few-Shot-Beispiele + Projekt-Konventionen aus `agent_learning` DB
- **Defense-in-Depth:** Entwickelt nach Analyse des PR #123 Vorfalls (31 Comments in 90min durch Review-Loop)
- **Monitoring:** `/health/jules` Endpoint, Discord-Alerts, Nightly-Batch fuer Quality-Tracking

### 🧠 **Lernender Security Analyst (v5.0)**
- ✅ **Full Learning Pipeline**: 4 neue DB-Tabellen (fix_attempts, fix_verifications, finding_quality, scan_coverage)
- ✅ **Adaptive Session-Steuerung**: 4 Modi (full_scan, quick_scan, fix_only, maintenance) — passt Intensität an Workload an
- ✅ **Pre-Session Maintenance**: Git-Activity-Sync, Fix-Verifikation, Knowledge-Decay, Security-Profile
- ✅ **Datengetriebener Scan-Plan**: Priorisiert nach Coverage-Lücken, Regressionen, Hotspots, Git-Delta
- ✅ **Fix-Memory**: Vorherige Fix-Versuche sichtbar — Agent wählt beim Retry einen anderen Ansatz
- ✅ **Selbstbewertung**: Analyst bewertet eigene Findings (Confidence, False Positives)
- ✅ **Knowledge-Decay**: Altes Wissen verliert Confidence — zwingt zur Re-Verifikation
- ✅ **3-Ebenen-Schutz**: Prompt + DB + Port-Validierung gegen Infrastruktur-Breaks
- ✅ **Projekt-Security-Profile**: Angriffsoberflächen, Auth-Mechanismen, Secrets-Orte pro Projekt

### 🤖 **Dual-Engine AI System (v4.0)**
- ✅ **Codex CLI (Primary, 97%)**: gpt-4o (fast), gpt-5.3-codex (standard), o3 (thinking)
- ✅ **Claude CLI (Fallback + Verify, 3%)**: claude-sonnet-4-6 (standard), claude-opus-4-6 (thinking)
- ✅ **Config-basierter TaskRouter**: Routing nach Severity (CRITICAL→o3, HIGH→gpt-5.3-codex, LOW→gpt-4o)
- ✅ **Quota-aware Failover**: Provider-Limits werden aus CLI-Output erkannt; Weekly-Deep scannt bei Claude-Limit automatisch via Codex weiter
- ✅ **SmartQueue**: 3 parallele Analysen (Semaphore), serieller Fix-Lock, Circuit Breaker, Batch-Erkennung
- ✅ **VerificationPipeline**: 4-Stufen Pre-Push (Confidence ≥85% → Tests → Claude-Verify → KB-Check)
- ✅ **JSON-Schemas**: Structured Output für Codex (`--output-schema`) — fix_strategy, patch_notes, incident_analysis
- ✅ **Vollständige MCP-Anbindung**: Beide Engines haben Zugriff auf Postgres, Redis, Docker, GitHub, Filesystem, Prisma
- ✅ **Ollama komplett entfernt**: 1364 Zeilen Dead Code gelöscht, 0 Ollama-Referenzen im Quellcode

**Architektur:**
```
Event → TaskRouter → Codex CLI (Primary)
                      ↓ (bei Fehler/Timeout)
                   Claude CLI (Fallback)
                      ↓
               VerificationPipeline (Pre-Push)
                      ↓
               SmartQueue (Fix-Lock + Circuit Breaker)
```

### 📝 **Patch Notes v7 Editorial Layer**
- ✅ **Hero-Changes statt Commitliste**: Releases bekommen 1-4 priorisierte Highlights mit konkretem Nutzer-, Spieler- oder Ops-Nutzen.
- ✅ **Before/After/Impact/User-Action**: Strukturierte Felder fuer Web-Changelog und Discord, ohne alte AI-Outputs zu brechen.
- ✅ **Kanaltrennung**: Discord bleibt kurz und stark, Web bekommt Highlights + Detailgruppen, Ops-Hinweise nennen Migrationen/Config/Downtime.
- ✅ **Qualitaetsguard**: Generische Formulierungen wie "bessere UX" oder "verbesserte Performance" werden gewarnt, wenn kein konkreter Beleg dabei ist.
- ✅ **Kompatibler Rollout**: Neue Felder im `changes[]` Schema sind optional; bestehende Releases, DB-Eintraege und Export-Pfade bleiben kompatibel.

## 🎯 Features

### 🔔 Auto-Alerts
- **Fail2ban** - IP-Bans bei Brute-Force-Angriffen
- **CrowdSec** - KI-basierte Bedrohungserkennung
- **AIDE** - File Integrity Monitoring
- **Docker Security Scans** - Container-Schwachstellen (Trivy)
- **Project Health Checks** - Real-time monitoring for all services
- **Incident Detection** - Automatic incident creation and tracking
- **GitHub Events** - Detaillierte Patch-Notes für Push, PR und Release Events
- **Deployment Status** - Real-time deployment progress

### 🤖 Slash Commands

#### Security & Monitoring
- `/status` - Gesamt-Sicherheitsstatus
- `/scan` - Manuellen Docker-Scan triggern (Admin)
- `/threats` - Letzte erkannte Bedrohungen
- `/bans` - Aktuell gebannte IPs (Fail2ban + CrowdSec)
- `/docker` - Letzte Docker Scan Ergebnisse
- `/aide` - AIDE Integrity Check Status

#### Auto-Remediation
- `/remediation-stats` - Auto-Remediation Statistiken (Admin)
- `/stop-all-fixes` - EMERGENCY: Stoppt alle laufenden Fixes (Admin)
- `/set-approval-mode [mode]` - Ändere Approval Mode (paranoid/auto/dry-run) (Admin)

#### Patch Notes
- `/release-notes [project]` - Commits als Patch Notes veröffentlichen (Admin)
- `/pending-notes` - Übersicht ausstehender Commit-Batches (Admin)
- `/mark-duplicate` - Finding als Duplikat markieren (Learning-Feedback)

#### AI & Learning System
- `/get-ai-stats` - AI-Provider Status und Fallback-Chain
- `/reload-context` - Lade Project-Context neu (Admin)
- `/agent-stats` - Agent-Learning Statistiken
- `/security-engine` - Security Engine v6 Status und Statistiken

#### Multi-Project Management
- `/projekt-status [name]` - Status für spezifisches Projekt (Uptime, Response Time, Health)
- `/alle-projekte` - Übersicht aller überwachten Projekte

#### Mobile Workflow (Owner-only)
- `/claude [prompt] [project] [model] [timeout]` - Headless Claude-Session auf dem Server starten und Antwort in Discord empfangen (owner-only)

#### Server Setup
- `/setup-customer-server` - Monitoring-Channels für Customer-Server einrichten (Admin)

### 🎨 Features
- **Rich Embeds** - Farbcodierte Alerts (🔴 CRITICAL, 🟠 HIGH, 🟢 OK)
- **Multi-Channel Support** - Kategorisierte Channels (Security, AI Learning, Deployments, etc.)
- **Project Tagging** - Filtere Alerts nach Projekt
- **Role Permissions** - Admin-only Commands
- **Auto-Reconnect** - Robust gegen Netzwerk-Probleme
- **Incident Threads** - Automatische Discord-Threads pro Incident
- **Real-Time Dashboards** - Live project status updates

## 📋 Voraussetzungen

- Python 3.9+
- Discord Bot Token (siehe Setup)
- Systemd (für Service)
- Root/Sudo-Zugriff (für Log-Zugriff und Deployments)
- Optional: GitHub Webhook für Auto-Deploy
- Optional: Codex CLI (ChatGPT Plus) und/oder Claude CLI (Claude Max) für AI-Features

## 🚀 Quick Start

### 1. Discord Bot erstellen

1. Gehe zu [Discord Developer Portal](https://discord.com/developers/applications)
2. "New Application" → Name: `ShadowOps`
3. Bot-Tab → "Add Bot"
4. "Reset Token" → Token kopieren (⚠️ nur einmal sichtbar!)
5. Unter "Privileged Gateway Intents":
   - ✅ Message Content Intent (optional)
   - ✅ Server Members Intent (optional)
6. OAuth2 → URL Generator:
   - Scopes: `bot`, `applications.commands`
   - Permissions: `Send Messages`, `Embed Links`, `Use Slash Commands`, `Create Public Threads`, `Send Messages in Threads`
7. Generierte URL öffnen → Bot zu Server einladen

### 2. Bot installieren

```bash
cd /home/user/shadowops-bot

# Dependencies installieren
pip3 install -r requirements.txt

# Config erstellen
cp config/config.example.yaml config/config.yaml
nano config/config.yaml  # guild_id und andere statische IDs eintragen

# Secrets als Umgebungsvariablen setzen
# (z.B. in ~/.bashrc, ~/.zshrc oder einer .env Datei, die vom Service geladen wird)
export DISCORD_BOT_TOKEN="DEIN_BOT_TOKEN_HIER"
# Optional:
# export ANTHROPIC_API_KEY="DEIN_ANTHROPIC_KEY"
# export OPENAI_API_KEY="DEIN_OPENAI_KEY"
# export GITHUB_TOKEN="DEIN_GITHUB_TOKEN"   # Benoetigt fuer GitHub-Integration (Webhooks, Auto-Deploy)
```

### 3. Systemd Service aktivieren

```bash
sudo cp deploy/shadowops-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable shadowops-bot
sudo systemctl start shadowops-bot

# Status prüfen
sudo systemctl status shadowops-bot

# Logs live verfolgen
sudo journalctl -u shadowops-bot -f
```

### 4. Discord Channels Setup (Automatisch)

Der Bot erstellt automatisch alle benötigten Channels beim ersten Start:

**🤖 Auto-Remediation Kategorie:**
- `🚨-security-alerts` - Sicherheits-Alarme
- `✅-approval-requests` - Fix-Genehmigungen
- `⚙️-execution-logs` - Execution-Logs
- `📊-stats` - Tägliche Statistiken
- `🧠-ai-learning` - AI Learning Logs
- `🔧-code-fixes` - Code Fixer Logs
- `⚡-orchestrator` - Orchestrator Logs

> ℹ️ **Channel-Fallbacks**: Falls die Auto-Remediation-Notification-IDs nicht gesetzt sind, nutzt der Bot automatisch die IDs aus `channels.*` (z.B. `channels.ai_learning`, `channels.code_fixes`, `channels.orchestrator`). So bleiben AI-Learning und Discord-Logs aktiv, selbst wenn die Notifications-Section fehlt.

**🌐 Multi-Project Kategorie (v3.1):**
- `👥-customer-alerts` - Kunden-sichtbare Alerts
- `📊-customer-status` - Projekt-Status Updates
- `🚀-deployment-log` - Deployment-Benachrichtigungen

> 💡 **Tipp**: Der Bot updated die Config automatisch mit allen Channel-IDs!

## ⚙️ Konfiguration

Vollständige Konfigurationsdokumentation: [docs/reference/api.md](./docs/reference/api.md)

Basis-Config in `config/config.yaml`:

```yaml
discord:
  # token: "" # WIRD JETZT ÜBER ENV VAR: DISCORD_BOT_TOKEN GESETZT
  guild_id: 123456789

ai:
  enabled: true

  primary:
    engine: codex
    models:
      fast: gpt-4o
      standard: gpt-5.3-codex
      thinking: o3
    timeout: 300

  fallback:
    engine: claude
    cli_path: /home/user/.local/bin/claude
    models:
      fast: claude-sonnet-4-6
      standard: claude-sonnet-4-6
      thinking: claude-opus-4-6
    timeout: 300

  routing:
    critical_analysis: { engine: codex, model: thinking }
    high_analysis: { engine: codex, model: standard }
    low_analysis: { engine: codex, model: fast }
    critical_verify: { engine: claude, model: thinking }

auto_remediation:
  enabled: true
  dry_run: false
  approval_mode: paranoid  # paranoid | auto | dry-run
  max_batch_size: 10

projects:
  shadowops-bot:
    enabled: true
    path: /home/user/shadowops-bot
    branch: main
    monitor:
      enabled: true
      url: http://localhost:5000/health
      check_interval: 60
    deploy:
      run_tests: true
      test_command: pytest tests/

github:
  enabled: false
  webhook_secret: "your_webhook_secret"
  webhook_port: 8080
  auto_deploy: false
  deploy_branches: [main, master]

deployment:
  backup_dir: backups
  max_backups: 5
  health_check_timeout: 30
```

> **Welle 9.10 (2026-05-11) — Wait-for-CI vor Auto-Deploy:** Sobald ein PR auf einen `deploy_branches`-Branch gemergt wird, wartet der Bot vor dem Trigger von `deploy.sh` auf den Abschluss der in `projects.<name>.ci_workflows` konfigurierten Workflows (z.B. `["Web Quality"]`). Bei `failure`/`timeout` wird `deploy.sh` NICHT aufgerufen — stattdessen erscheint ein Alert im projekt-`ci_channel_id` oder `deployment_log`. Hard-Timeout 30 min (überschreibbar via `projects.<name>.ci_wait_max_min`). Exponential backoff 60s → 120s → 240s → cap 300s.

**AI komplett deaktivieren (Monitoring + Patch Notes ohne KI):**
- `ai.enabled: false`
- `ai_learning.enabled: false`
- `projects.*.patch_notes.use_ai: false`

> ℹ️ **Config Loader**: Die Einstellungen können per Attribute **und** Dictionary-Access gelesen werden (z.B. `config.discord['token']` oder `config['discord']`). Fehlende Pflichtfelder (`discord.token`, `discord.guild_id`) lösen einen klaren `KeyError` aus, damit Fehlkonfigurationen sofort auffallen.

## 📊 Verwendung

### Commands in Discord

```
Security Commands:
  /status              - Gesamt-Sicherheitsstatus
  /scan                - Docker Security Scan
  /threats [hours]     - Bedrohungen der letzten X Stunden
  /bans [limit]        - Gebannte IPs
  /aide                - AIDE Check-Status

Auto-Remediation:
  /remediation-stats             - Statistiken
  /stop-all-fixes                - Emergency Stop
  /set-approval-mode [mode]      - Approval Mode ändern

AI System:
  /get-ai-stats                  - AI Provider Status
  /reload-context                - Context neu laden

Multi-Project:
  /projekt-status [name]         - Detaillierter Projekt-Status
  /alle-projekte                 - Übersicht aller Projekte
```

### GitHub Webhook Setup

1. Repository Settings → Webhooks → Add webhook
2. Payload URL: `http://your-server:8080/webhook`
3. Content type: `application/json`
4. Secret: (from config.yaml)
5. Events: `Push`, `Pull request`, `Release`

## 🔧 Entwicklung & Testing

```bash
# Dependencies installieren
pip3 install -r requirements.txt
pip3 install -r requirements-dev.txt

# Tests ausfuehren (IMMER -x verwenden — stoppt bei erstem Fehler, verhindert OOM auf 8 GB VPS)
pytest tests/unit/test_NAME.py -x

# Gesamte Test-Suite (selten, nur lokal mit ausreichend RAM)
pytest tests/ -x -v

# Mit Coverage
pytest tests/ -x --cov=src --cov-report=html

# Einzelne Test-Kategorie
pytest tests/unit/ -x -v
pytest tests/integration/ -x -v

# Bot lokal testen
python3 src/bot.py

# Logs anschauen
tail -f logs/shadowops.log

# Service neu starten
sudo systemctl restart shadowops-bot
```

## 📁 Projekt-Struktur

```
shadowops-bot/
├── src/
│   ├── bot.py                          # Haupt-Bot-Logik
│   ├── cogs/                           # Modulare Slash Commands
│   │   ├── admin.py                    # /scan, /stop-all-fixes, /remediation-stats, ...
│   │   ├── inspector.py                # /get-ai-stats, /projekt-status, /agent-stats, ...
│   │   ├── monitoring.py               # /status, /bans, /threats, /docker, /aide
│   │   ├── customer_setup_commands.py  # /setup-customer-server
│   │   ├── claude_cli.py               # /claude (owner-only Mobile-Trigger)
│   │   ├── cron_heartbeat.py           # Cron-Heartbeat
│   │   └── phase_5e_health_aggregator.py  # Health-Aggregation
│   ├── integrations/
│   │   ├── ai_engine.py                # Dual-Engine AI (Codex + Claude CLI)
│   │   ├── smart_queue.py              # SmartQueue (Analyse-Pool + Fix-Lock)
│   │   ├── verification.py             # Pre-Push Verification Pipeline
│   │   ├── orchestrator/               # Remediation Orchestrator (Package)
│   │   ├── event_watcher.py            # Security Event Watcher
│   │   ├── knowledge_base.py           # SQL Learning System
│   │   ├── code_analyzer.py            # Code Structure Analyzer
│   │   ├── context_manager.py          # RAG Context Manager
│   │   ├── github_integration/         # GitHub Webhooks + Jules Workflow (Package)
│   │   ├── security_engine/            # Autonomer SecurityScanAgent + CircuitBreaker + DB
│   │   ├── project_monitor.py          # Multi-Project Monitoring
│   │   ├── deployment_manager.py       # Auto-Deployment
│   │   ├── incident_manager.py         # Incident Tracking
│   │   ├── customer_notifications.py   # Customer-Facing Alerts
│   │   ├── fixers/                     # Security Fixers (fail2ban, crowdsec, aide, trivy, wal-g)
│   │   ├── analyst/                    # Security Analyst (Legacy-Referenz)
│   │   ├── ai_learning/                # Continuous Learning Agent
│   │   ├── fail2ban.py                 # Fail2ban Integration
│   │   ├── crowdsec.py                 # CrowdSec Integration
│   │   ├── aide.py                     # AIDE Integration
│   │   └── docker.py                   # Docker Scan Integration
│   ├── patch_notes/                    # Patch Notes Pipeline v6 (5-Stufen State Machine)
│   ├── schemas/                        # JSON-Schemas fuer Structured Output (Codex/Claude)
│   └── utils/
│       ├── config.py                   # Config-Loader
│       ├── state_manager.py            # State-Management
│       ├── logger.py                   # Logging
│       ├── embeds.py                   # Discord Embed-Builder
│       ├── discord_logger.py           # Discord Channel Logger
│       ├── alert_humanizer.py          # Status-Telemetrie zu mensch-lesbarem Deutsch
│       ├── health_server.py            # HTTP /health-Endpoint + Changelog REST API
│       ├── message_handler.py          # Discord Rate-Limit + Message-Splitting
│       ├── circuit_breaker.py          # Leichtgewichtiger Circuit Breaker (Util-Variante)
│       ├── changelog_parser.py         # CHANGELOG.md Parser fuer Patch Notes
│       └── process_lock.py             # Cross-Process Singleton Lock (fcntl + Stale-Detection)
├── tests/
│   ├── conftest.py                     # Test Fixtures
│   ├── unit/                           # Unit Tests (700+, 67 Dateien)
│   │   ├── test_config.py
│   │   ├── test_ai_engine.py
│   │   ├── test_smart_queue.py
│   │   ├── test_orchestrator.py
│   │   ├── test_knowledge_base.py
│   │   ├── test_event_watcher.py
│   │   ├── test_github_integration.py
│   │   ├── test_project_monitor.py
│   │   ├── test_incident_manager.py
│   │   ├── agent_review/               # Multi-Agent-Pipeline Tests
│   │   └── security_engine/            # SecurityScanAgent Tests
│   └── integration/
│       └── test_learning_workflow.py   # End-to-End Tests
├── config/
│   ├── config.example.yaml             # Template (commited)
│   ├── config.yaml                     # Real Config (gitignored)
│   ├── config.recommended.yaml         # Empfehlungen
│   ├── safe_upgrades.yaml              # Upgrade-Pfade
│   └── logrotate.conf                  # Log-Rotation
├── deploy/                             # Deployment + Watchdogs
│   ├── shadowops-bot.service           # systemd Bot-Service
│   ├── *-watchdog.{service,timer}      # Externe Uptime-Watchdogs (14 Watchdogs: HTTP/systemd/jq-filter/build-drift/state-drift)
│   ├── shadowops-watchdog.env.example  # Webhook-Env Template
│   └── MONITORING_SETUP.md             # Setup-Anleitung Watchdogs
├── .github/
│   └── workflows/
│       ├── ci.yml                      # Test-Pipeline
│       ├── worker-dedup-gate.yml       # Worker-PR-Dedup-Gate
│       └── auto-label-pr.yml           # Auto-Labeling
├── scripts/                            # Utility-Skripte
│   ├── restart.sh                      # Bot neustarten (--pull, --logs)
│   ├── diagnose-bot.sh                 # Diagnose
│   ├── setup.sh                        # Erstinstallation
│   └── ...
├── data/                               # Runtime-Daten (gitignored)
├── logs/                               # Log-Dateien (gitignored)
├── docs/                               # Dokumentation
│   ├── reference/api.md                # API-Referenz
│   ├── adr/                            # Architecture Decision Records
│   ├── plans/                          # Design-Dokumente
│   └── archive/                        # Historische Doku
├── .claude/                            # KI-Konfiguration
│   ├── rules/                          # Pfad-gefilterte Rules
│   ├── skills/                         # Workflow-Skills
│   └── agents/                         # Spezialisierte Agents
├── requirements.txt                    # Python Dependencies
├── pyproject.toml                      # Projekt-Definition
├── CLAUDE.md                           # KI-Projektinstruktionen
├── CHANGELOG.md                        # Version History
└── README.md                           # This file
```

## 🛡️ Security

- **Secrets Management**: Secrets (Token, API Keys) **müssen** als Umgebungsvariablen gesetzt werden.
- **Config-Schutz**: Niemals die `config.yaml` oder `.env`-Dateien committen!
- **File Permissions**: `chmod 600 config/config.yaml`
- **Service-User**: Bot läuft als nicht-root user
- **Rate Limiting**: Eingebaut gegen Spam
- **Webhook Verification**: HMAC signatures for GitHub webhooks
- **DO-NOT-TOUCH Validation**: Critical files protected
- **Dry-Run Mode**: Test fixes without execution
- **Automatic Backups**: Before every change
- **Rollback Capability**: Instant restoration on failure

## 📈 Performance & Reliability

- **Persistent Learning**: SQL database survives restarts
- **Exponential Backoff**: Smart retry logic for API calls
- **Circuit Breaker**: Prevents cascade failures
- **Race Condition Protection**: Async locks for shared state
- **Memory Management**: Automatic cleanup of old data
- **Health Monitoring**: Continuous project uptime tracking
- **Auto-Recovery**: Projects automatically resume after downtime

## Changelog

See [CHANGELOG.md](./CHANGELOG.md) for the full version history.

## 📊 Statistics (v5.1.0)

- **Total Lines of Code**: 20,000+
- **AI Engines**: 2 (Codex CLI + Claude CLI)
- **AI Models**: 6 (gpt-4o, gpt-5.3-codex, o3, claude-sonnet-4-6, claude-opus-4-6)
- **Security Integrations**: 4 (Fail2ban, CrowdSec, AIDE, Trivy)
- **PostgreSQL Databases**: 3 (security_analyst: 21 Tabellen, agent_learning: 7 Tabellen, seo_agent: 11 Tabellen)
- **Learning Pipeline Tables**: 11 (Security: fix_attempts, fix_verifications, finding_quality, scan_coverage · Shared: agent_feedback, agent_quality_scores, agent_knowledge · Patch Notes: pn_generations, pn_variants, pn_examples · SEO: seo_fix_impact)
- **Scan Areas**: 10 (firewall, ssh, docker, permissions, packages, services, logs, network, credentials, dependencies)
- **Discord Commands**: 16 (inkl. /agent-stats, /claude)
- **Monitored Projects**: 3 (GuildScout, ZERODOX, AI Agents)
- **Auto Discord-Posts**: Session-Summaries, Feedback-Auswertungen, Weekly Summary, Meilensteine

## 📄 Lizenz

MIT License - Erstellt von CommanderShadow

## 🤝 Support & Troubleshooting

### Häufige Probleme

**Bot startet nicht:**
```bash
# Logs prüfen
sudo journalctl -u shadowops-bot -f

# Service-Status
sudo systemctl status shadowops-bot

# Config validieren
python3 -c "from src.utils.config import get_config; get_config()"
```

**Slash Commands werden nicht angezeigt:**
```bash
# Commands neu synchronisieren (automatisch beim Bot-Start)
# Kann bis zu 1 Stunde dauern (Discord Cache)
```

**AI Engine funktioniert nicht:**
```bash
# Codex CLI prüfen
codex --version

# Claude CLI prüfen
~/.local/bin/claude --version

# AI Stats in Discord
/get-ai-stats
```

**Deployments schlagen fehl:**
```bash
# Permissions prüfen
sudo -l

# Backup-Verzeichnis prüfen
ls -la backups/

# Repo/Projekt-Name prüfen (Config ist case-insensitive)
# z.B. "GuildScout" ↔ "guildscout"

# rsync prüfen (wird für Backup/Rollback genutzt; ohne rsync gibt es einen Fallback)
which rsync

# Deployment-Logs
tail -f logs/shadowops.log | grep deployment
```

### Vollständige Dokumentation

- 📖 [Setup Guide](./docs/SETUP_GUIDE.md) - Schritt-für-Schritt Installation
- 🔧 [API Documentation](./docs/reference/api.md) - Vollständige API-Referenz
- 📚 [Docs Overview](./DOCS_OVERVIEW.md) - Dokumentations-Index

### Bei Problemen

1. Logs prüfen: `sudo journalctl -u shadowops-bot -f`
2. Service-Status: `sudo systemctl status shadowops-bot`
3. Permissions prüfen: Bot braucht Zugriff auf Logs und Deployment-Pfade
4. Test-Suite ausführen: `pytest tests/ -v`
5. GitHub Issues: [Report a Bug](https://github.com/Commandershadow9/shadowops-bot/issues)

---

**Made with 🗡️ by CommanderShadow**

*ShadowOps v5.1 - Lernender AI Security Guardian mit Jules SecOps Workflow + Full Learning Pipeline*
