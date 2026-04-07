# 🗡️ ShadowOps - Active Security Guardian v5.0 🚀

**Status:** AKTIV | **Version:** 5.0.0 | **Letzte Aktualisierung:** 29.03.2026

**ShadowOps** ist ein **vollständig autonomer Security Guardian** mit lernfähigem AI Security Analyst, KI-gesteuerter Auto-Remediation, adaptiver Session-Steuerung und wachsender Knowledge-DB — kein statischer Scanner, sondern ein **System das aus seinen Erfahrungen lernt und immer besser wird**.

> 📖 **Security Analyst Doku:** [docs/SECURITY_ANALYST.md](./docs/SECURITY_ANALYST.md)
> 📚 **Dokumentations-Übersicht:** [DOCS_OVERVIEW.md](./DOCS_OVERVIEW.md)
> 🔧 **API Dokumentation:** [docs/API.md](./docs/API.md)
> 🚀 **Setup Guide:** [docs/SETUP_GUIDE.md](./docs/SETUP_GUIDE.md)
> 📐 **Learning Pipeline Design:** [docs/plans/2026-03-18-analyst-learning-pipeline-design.md](./docs/plans/2026-03-18-analyst-learning-pipeline-design.md)

## ⚡ Highlights v5.0

### 🧠 **Lernender Security Analyst (v5.0 - NEW)**
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

## ⚡ Highlights v3.4

### 🧠 **Advanced AI Learning System (v3.4 - NEW)**
- ✅ **Vollständige KI-Trainings-Pipeline**: Kontinuierliches Lernen für bessere Patch Notes
  - CHANGELOG.md-Parsing für vollständigen Kontext
  - Automatische Qualitätsbewertung (0-100 Skala)
  - Few-Shot-Learning mit Top-10 Beispielen
  - Trainingsdaten-Sammlung (≥80 Score)
- ✅ **Discord Feedback-Sammlung**
  - Automatische Reaktions-Buttons (👍 ❤️ 🔥 👎 😐 ❌)
  - Benutzer-Feedback trainiert die KI
  - Funktioniert für ALLE Projekte automatisch
- ✅ **A/B Testing System**
  - 3 Prompt-Varianten mit Performance-Tracking
  - Gewichtete Auswahl basierend auf Erfolg
  - Kombinierte Bewertung (70% Qualität + 30% Feedback)
- ✅ **Auto-Tuning Engine**
  - Automatische Performance-Analyse
  - Verbesserungsvorschläge
  - Automatische Varianten-Erstellung
- ✅ **Fine-Tuning Export**
  - JSONL-Format für LLM Fine-Tuning
  - LoRA-Format (Alpaca-Style)
  - Auto-generiertes Fine-Tuning-Script
- ✅ **Admin-Befehle**
  - `/ai-stats` - Trainings-Statistiken
  - `/ai-variants` - Varianten-Übersicht
  - `/ai-tune` - Tuning-Vorschläge
  - `/ai-export-finetune` - Export für Training
- ✅ **Multi-Projekt-Unterstützung**
  - Gemeinsamer Lern-Pool (alle profitieren voneinander)
  - Zero-Config (automatisch für `use_ai: true`)
  - Projekt-übergreifendes Lernen
- ✅ **Intelligentes RAM-Management**
  - Automatische Prozess-Bereinigung
  - System-Cache-Flush als Fallback
  - Schützt kritische Services (PostgreSQL, Redis, etc.)

## ⚡ Highlights v3.3

### 🔐 **Webhook Security (v3.3 - NEW)**
- ✅ **HMAC-SHA256 Signature Verification**: Sichere GuildScout ↔ ShadowOps Kommunikation
  - Schützt vor gefälschten/gespooften Alerts
  - Validiert Webhook-Authentizität mit Shared Secret
  - Constant-time Signatur-Vergleich verhindert Timing-Attacks
  - Konfigurierbar per Projekt: `webhook_secret` in Config
- ✅ **Automatische Request-Validierung**
  - Validiert `X-Webhook-Signature` Header Format
  - Lehnt ungültige Signaturen mit HTTP 403 ab
  - Abwärtskompatibel (Legacy-Modus ohne Secret)
  - Detailliertes Security-Logging für Audits
- ✅ **Erweiterte GuildScout Integration**
  - Unterstützt alle neuen GuildScout v2.3.0 Alerts:
    - Health Monitoring Alerts
    - Performance Profiling Events
    - Weekly Report Summaries
    - Database Monitoring Warnings

**Konfiguration:**
```yaml
projects:
  guildscout:
    webhook_secret: YOUR_RANDOM_SECRET_HERE  # python -c "import secrets; print(secrets.token_urlsafe(32))"
    # Muss identisch mit GuildScout Config sein!
```

**Security Best Practices:**
- Verwende starke, zufällige Secrets (min. 32 Zeichen)
- Rotiere Secrets regelmäßig (alle 90 Tage)
- Verwende HTTPS für Produktions-Webhooks
- Überwache abgelehnte Requests (403 Errors)

## ⚡ Highlights v3.2

### 🌐 **Multi-Guild Customer Notifications (v3.2 - NEW)**
- ✅ **Automatic Channel Setup**: Bot auto-creates monitoring channels on customer servers
- ✅ **External Notifications**: Send Git updates and status alerts to customer Discord servers
- ✅ **AI-Generated Patch Notes**: Professional, user-friendly updates via Codex/Claude
- ✅ **Dual-Channel System**: Technical logs (internal) + friendly updates (customers)
- ✅ **Per-Project Configuration**: Configurable language (DE/EN) and notification types
- ✅ **Message Splitting**: Automatic handling of Discord's 4096 character limit
- ✅ **Centralized Monitoring**: ShadowOps handles all notifications (Option B)
- ✅ **Manual Setup Command**: `/setup-customer-server` for existing guilds

### 🔧 **Security Integration Fixes (v3.2 - NEW)**
- ✅ **CrowdSec Integration Fixed**: Corrected JSON parsing, now shows "🟢 Aktiv"
- ✅ **Fail2ban Integration Fixed**: Resolved systemd restrictions, now shows "🟢 Aktiv"
- ✅ **GitHub Webhook Logging**: Fixed logger connection for full webhook visibility
- ✅ **Firewall Configuration**: Port 9090 opened with HMAC security

## ⚡ Highlights v3.1

### 🧠 **Persistent AI Learning System (v3.1 - NEW)**
- ✅ **SQL Knowledge Base**: Persistent storage for fixes, strategies, and success rates
- ✅ **Git History Analysis**: Learns from past commits to understand codebase evolution
- ✅ **Code Structure Analyzer**: Deep understanding of project architecture
- ✅ **Log-Based Learning**: Analyzes security logs to improve threat detection
- ✅ **Success Rate Tracking**: Historical performance metrics guide strategy selection
- ✅ **Best Strategy Recommendations**: AI suggests fixes based on proven success
- ✅ **Adaptive Retry Logic**: Failed fixes inform better subsequent attempts

### 🌐 **Multi-Project Management (v3.1 - NEW)**
- ✅ **GitHub Webhook Integration**: Auto-deploy on push/PR merge events
- ✅ **Automated Patch-Notes**: Detaillierte Change-Notifications bei Git-Push für interne und Kunden-Channels.
- ✅ **Real-Time Health Monitoring**: Continuous uptime tracking for all projects
- ✅ **Automated Deployment**: Complete CI/CD pipeline with safety checks
- ✅ **Incident Management**: Auto-detection, tracking, and Discord threads
- ✅ **Customer Notifications**: Professional, user-friendly status updates
- ✅ **Project Dashboard**: `/projekt-status` and `/alle-projekte` commands
- ✅ **Automatic Rollback**: Failed deployments trigger instant restoration

### 🧪 **Enterprise Test Suite (v3.1 - NEW)**
- ✅ **150+ Comprehensive Tests**: Full coverage for all critical systems
- ✅ **Unit Tests**: Config, AI Service, Orchestrator, Knowledge Base, Event Watcher
- ✅ **Integration Tests**: End-to-end learning workflows
- ✅ **AI Learning Documentation**: Tests demonstrate how AI learns patterns
- ✅ **pytest Configuration**: Professional test infrastructure
- ✅ **Test Fixtures**: 20+ reusable fixtures for consistent testing

### 🛡️ **Active Security Guardian (v3.0)**
- ✅ **Echte Fix-Execution**: NPM audit fix, Docker rebuilds, Firewall-Updates, File Restoration
- ✅ **Automatische Backups**: Vor JEDER Änderung mit 7-Tage Retention & Rollback
- ✅ **Impact-Analyse**: Projekt-bewusste Entscheidungen (ShadowOps, GuildScout, Nexus, Sicherheitstool)
- ✅ **Service Management**: Graceful Start/Stop mit Health Checks & Dependency-Ordering
- ✅ **Koordinierte Remediation**: Multi-Event Batching mit single approval flow
- ✅ **Safety First**: Dry-Run Mode, DO-NOT-TOUCH Validation, Circuit Breaker, Command Validation
- ✅ **Live Discord Updates**: Echtzeit-Feedback während kompletter Execution (Backup → Fix → Verify → Restart)

### 🤖 **Dual-Engine AI System (v4.0)**
- **Codex CLI (Primary)**: gpt-4o / gpt-5.3-codex / o3 mit Structured Output (JSON-Schemas)
- **Claude CLI (Fallback)**: claude-sonnet-4-6 / claude-opus-4-6 mit MCP-Zugriff
- **RAG Context**: Projekt-Wissen + DO-NOT-TOUCH Regeln + Infrastructure Knowledge + Code Structure
- **SQL Knowledge Base**: Persistent learning across sessions
- **Event History**: Remembers ALL previous fix attempts with outcomes
- **Confidence-Based**: <85% confidence → automatisch blockiert
- **Batch-Processing**: Mehrere Events → 1 koordinierter Plan
- **Adaptive Strategies**: AI learns from failures and improves over time
- **Git History Integration**: Analyzes commit patterns for better context

### 🎯 Enhanced Workflow (v3.1)
```
1. 🚨 Security Event erkannt
   └─> Event Watcher → Orchestrator (10s Batch-Fenster)

2. 🧠 AI Query Knowledge Base
   ├─ Check previous fixes for similar events
   ├─ Load best strategies based on success rate
   └─ Analyze code structure and git history

3. 🤖 KI-Analyse (ALLE Events zusammen)
   ├─ Hybrid AI mit RAG Context + KB + Code Analysis
   ├─ Koordinierter Multi-Phasen Plan
   └─ Impact-Analyse (Projekte, Downtime, Risks)

4. ✋ Single Approval Request
   ├─ Kompletter Plan mit allen Phasen
   ├─ Betroffene Projekte + Downtime-Schätzung
   ├─ Historical success rate (if applicable)
   └─ Rollback-Strategie

5. 🔧 Autonome Execution
   ├─ Phase 0: Backups erstellen
   ├─ Phase 1-N: Fixes ausführen (npm audit, Docker rebuild, etc.)
   ├─ Verification: Re-Scans prüfen Erfolg
   ├─ Bei Fehler: Automatischer Rollback!
   └─ Record result to Knowledge Base

6. ✅ Completion & Learning
   ├─ Discord: Status + Results + Stats
   ├─ Save fix outcome to SQL KB
   ├─ Update success rates
   └─ Improve future strategies
```

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
- `/scan` - Manuellen Docker-Scan triggern
- `/threats` - Letzte erkannte Bedrohungen
- `/bans` - Aktuell gebannte IPs (Fail2ban + CrowdSec)
- `/aide` - AIDE Integrity Check Status

#### Auto-Remediation
- `/remediation-stats` - Auto-Remediation Statistiken
- `/stop-all-fixes` - 🛑 EMERGENCY: Stoppt alle laufenden Fixes
- `/set-approval-mode [mode]` - Ändere Approval Mode (paranoid/auto/dry-run)

#### AI & Learning System
- `/get-ai-stats` - AI-Provider Status und Fallback-Chain
- `/reload-context` - Lade Project-Context neu

#### Multi-Project Management
- `/projekt-status [name]` - Status für spezifisches Projekt (Uptime, Response Time, Health)
- `/alle-projekte` - Übersicht aller überwachten Projekte

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

Vollständige Konfigurationsdokumentation: [docs/API.md](./docs/API.md)

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
  auto_deploy: true
  deploy_branches: [main, master]

deployment:
  backup_dir: backups
  max_backups: 5
  health_check_timeout: 30
```

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

# Tests ausführen
pytest tests/ -v

# Mit Coverage
pytest tests/ --cov=src --cov-report=html

# Einzelne Test-Kategorie
pytest tests/unit/ -v
pytest tests/integration/ -v

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
│   ├── cogs/                           # NEU: Modulare Slash Commands
│   │   ├── admin.py
│   │   ├── inspector.py
│   │   └── monitoring.py
│   ├── integrations/
│   │   ├── ai_engine.py                # Dual-Engine AI (Codex + Claude CLI)
│   │   ├── smart_queue.py              # SmartQueue (Analyse-Pool + Fix-Lock)
│   │   ├── verification.py             # Pre-Push Verification Pipeline
│   │   ├── orchestrator.py             # Remediation Orchestrator
│   │   ├── event_watcher.py            # Security Event Watcher
│   │   ├── knowledge_base.py           # SQL Learning System
│   │   ├── code_analyzer.py            # Code Structure Analyzer
│   │   ├── context_manager.py          # RAG Context Manager
│   │   ├── github_integration.py       # GitHub Webhooks
│   │   ├── project_monitor.py          # Multi-Project Monitoring
│   │   ├── deployment_manager.py       # Auto-Deployment
│   │   ├── incident_manager.py         # Incident Tracking
│   │   ├── customer_notifications.py   # Customer-Facing Alerts
│   │   ├── fail2ban.py                 # Fail2ban Integration
│   │   ├── crowdsec.py                 # CrowdSec Integration
│   │   ├── aide.py                     # AIDE Integration
│   │   └── docker.py                   # Docker Scan Integration
│   └── utils/
│       ├── config.py                   # Config-Loader
│       ├── state_manager.py            # NEU: State-Management
│       ├── logger.py                   # Logging
│       ├── embeds.py                   # Discord Embed-Builder
│       └── discord_logger.py           # Discord Channel Logger
├── tests/
│   ├── conftest.py                     # Test Fixtures
│   ├── unit/                           # Unit Tests (161)
│   │   ├── test_config.py
│   │   ├── test_ai_engine.py           # 43 Tests (Router, Codex, Claude, AIEngine)
│   │   ├── test_smart_queue.py         # 21 Tests (Pool, Lock, Circuit Breaker)
│   │   ├── test_orchestrator.py
│   │   ├── test_knowledge_base.py
│   │   ├── test_event_watcher.py
│   │   ├── test_github_integration.py
│   │   ├── test_project_monitor.py
│   │   └── test_incident_manager.py
│   └── integration/
│       └── test_learning_workflow.py   # End-to-End Tests
├── config/
│   ├── config.example.yaml             # Example Config
│   ├── config.yaml                     # Your Config (gitignored)
│   ├── DO-NOT-TOUCH.md                 # Safety Rules
│   ├── INFRASTRUCTURE.md               # Infrastructure Knowledge
│   └── PROJECT_*.md                    # Project Documentation
├── config/                             # Konfiguration
│   ├── config.yaml                     # Hauptconfig (gitignored)
│   ├── config.example.yaml             # Template
│   ├── config.recommended.yaml         # Empfehlungen
│   ├── safe_upgrades.yaml              # Upgrade-Pfade
│   └── logrotate.conf                  # Log-Rotation
├── deploy/                             # Deployment
│   └── shadowops-bot.service           # systemd Unit
├── scripts/                            # Utility-Skripte
│   ├── restart.sh                      # Bot neustarten (--pull, --logs)
│   ├── diagnose-bot.sh                 # Diagnose
│   ├── setup.sh                        # Erstinstallation
│   └── ...
├── data/                               # Runtime-Daten (gitignored)
├── logs/                               # Log-Dateien (gitignored)
├── docs/                               # Dokumentation
│   ├── API.md                          # API-Referenz
│   ├── guides/                         # Benutzer-Anleitungen
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

## 📝 Changelog

See [CHANGELOG.md](./CHANGELOG.md) for detailed version history.

### Version 3.2.0 (2025-11-25)
**🌐 Multi-Guild Customer Notifications:**
- Automatic channel setup on customer servers
- AI-generated patch notes
- Dual-channel system (internal technical + customer friendly)
- Per-project language configuration (DE/EN)
- Message splitting for long patch notes
- Manual setup command `/setup-customer-server`

**🔧 Security Integration Fixes:**
- Fixed CrowdSec integration (JSON parsing)
- Fixed Fail2ban integration (systemd restrictions)
- Fixed GitHub webhook logging
- Opened port 9090 with HMAC security

**📚 Documentation:**
- Complete multi-guild setup guide
- Customer onboarding instructions
- GitHub webhook configuration guide
- Security integration fixes documentation

### Version 3.1.0 (2025-11-21)
**🧠 Persistent Learning System:**
- SQL Knowledge Base for permanent learning
- Git history analysis for codebase understanding
- Code structure analyzer for architecture insights
- Enhanced AI prompts with log-based learning
- Success rate tracking and best strategy recommendations

**🌐 Multi-Project Infrastructure:**
- GitHub webhook integration with auto-deploy
- Real-time project health monitoring
- Automated deployment system with rollback
- Incident management with Discord threads
- Customer-facing notification system
- Project status commands (`/projekt-status`, `/alle-projekte`)

**🧪 Enterprise Test Suite:**
- 150+ comprehensive tests (unit + integration)
- AI learning workflow demonstrations
- pytest configuration with fixtures
- Full coverage for critical systems

**🔧 Code Improvements:**
- Before/after verification for fixes
- Race condition protection
- Retry logic with exponential backoff
- Service validation
- Memory leak prevention

**🎮 New Commands:**
- `/set-approval-mode` - Change remediation mode
- `/get-ai-stats` - AI provider status
- `/reload-context` - Reload project context
- `/projekt-status` - Detailed project status
- `/alle-projekte` - All projects overview

### Version 3.0.0 (2025-11-20)
- AI Learning System with event history tracking
- Smart Docker image analysis
- CVE-aware upgrade recommendations
- Multi-project execution
- Git history learning

### Version 2.0.1 (2025-11-15)
- AI Service fixes
- HTTP client conflict resolution

### Version 2.0.0 (2025-11-14)
- Event-driven auto-remediation
- AI-powered analysis
- Live status updates

### Version 1.0.0 (2025-11-12)
- Initial Release
- Basic security monitoring
- Discord integration

## 📊 Statistics (v5.0.0)

- **Total Lines of Code**: 20,000+
- **AI Engines**: 2 (Codex CLI + Claude CLI)
- **AI Models**: 6 (gpt-4o, gpt-5.3-codex, o3, claude-sonnet-4-6, claude-opus-4-6)
- **Security Integrations**: 4 (Fail2ban, CrowdSec, AIDE, Trivy)
- **PostgreSQL Databases**: 3 (security_analyst: 21 Tabellen, agent_learning: 7 Tabellen, seo_agent: 11 Tabellen)
- **Learning Pipeline Tables**: 11 (Security: fix_attempts, fix_verifications, finding_quality, scan_coverage · Shared: agent_feedback, agent_quality_scores, agent_knowledge · Patch Notes: pn_generations, pn_variants, pn_examples · SEO: seo_fix_impact)
- **Scan Areas**: 10 (firewall, ssh, docker, permissions, packages, services, logs, network, credentials, dependencies)
- **Discord Commands**: 15 (inkl. /agent-stats)
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
- 🔧 [API Documentation](./docs/API.md) - Vollständige API-Referenz
- 📚 [Docs Overview](./DOCS_OVERVIEW.md) - Dokumentations-Index

### Bei Problemen

1. Logs prüfen: `sudo journalctl -u shadowops-bot -f`
2. Service-Status: `sudo systemctl status shadowops-bot`
3. Permissions prüfen: Bot braucht Zugriff auf Logs und Deployment-Pfade
4. Test-Suite ausführen: `pytest tests/ -v`
5. GitHub Issues: [Report a Bug](https://github.com/Commandershadow9/shadowops-bot/issues)

---

**Made with 🗡️ by CommanderShadow**

*ShadowOps v5.0 - Lernender AI Security Guardian mit Full Learning Pipeline*
