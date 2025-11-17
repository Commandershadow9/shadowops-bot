# 🗡️ ShadowOps - Active Security Guardian

**ShadowOps** ist ein **vollständig autonomer Security Guardian** mit KI-gesteuerter Auto-Remediation, der Sicherheitsbedrohungen nicht nur erkennt und analysiert, sondern **eigenständig behebt**.

> 📖 **Vollständige v3.0 Dokumentation:** [ACTIVE_SECURITY_GUARDIAN.md](./ACTIVE_SECURITY_GUARDIAN.md)
> 📚 **Dokumentations-Übersicht:** [DOCS_OVERVIEW.md](./DOCS_OVERVIEW.md)

## ⚡ Highlights

### 🛡️ **Active Security Guardian (v3.0 - CURRENT)**
- ✅ **Echte Fix-Execution**: NPM audit fix, Docker rebuilds, Firewall-Updates, File Restoration
- ✅ **Automatische Backups**: Vor JEDER Änderung mit 7-Tage Retention & Rollback
- ✅ **Impact-Analyse**: Projekt-bewusste Entscheidungen (ShadowOps, GuildScout, Nexus, Sicherheitstool)
- ✅ **Service Management**: Graceful Start/Stop mit Health Checks & Dependency-Ordering
- ✅ **Koordinierte Remediation**: Multi-Event Batching mit single approval flow
- ✅ **Safety First**: Dry-Run Mode, DO-NOT-TOUCH Validation, Circuit Breaker, Command Validation
- ✅ **Live Discord Updates**: Echtzeit-Feedback während kompletter Execution (Backup → Fix → Verify → Restart)

### 🤖 **AI-Powered Analysis**
- **Hybrid AI System**: Ollama (local) → Claude → OpenAI (fallback chain)
- **RAG Context**: Projekt-Wissen + DO-NOT-TOUCH Regeln + Infrastructure Knowledge
- **Confidence-Based**: <85% confidence → automatisch blockiert
- **Batch-Processing**: Mehrere Events → 1 koordinierter Plan
- **Learning Retry Logic**: KI lernt aus fehlgeschlagenen Fixes

### 🎯 Workflow (v3.0)
```
1. 🚨 Security Event erkannt
   └─> Event Watcher → Orchestrator (10s Batch-Fenster)

2. 🤖 KI-Analyse (ALLE Events zusammen)
   ├─ Hybrid AI mit RAG Context
   ├─ Koordinierter Multi-Phasen Plan
   └─ Impact-Analyse (Projekte, Downtime, Risks)

3. ✋ Single Approval Request
   ├─ Kompletter Plan mit allen Phasen
   ├─ Betroffene Projekte + Downtime-Schätzung
   └─ Rollback-Strategie

4. 🔧 Autonome Execution
   ├─ Phase 0: Backups erstellen
   ├─ Phase 1-N: Fixes ausführen (npm audit, Docker rebuild, etc.)
   ├─ Verification: Re-Scans prüfen Erfolg
   └─ Bei Fehler: Automatischer Rollback!

5. ✅ Completion
   └─> Discord: Status + Results + Stats
```

## 🎯 Features

### 🔔 Auto-Alerts
- **Fail2ban** - IP-Bans bei Brute-Force-Angriffen
- **CrowdSec** - KI-basierte Bedrohungserkennung
- **AIDE** - File Integrity Monitoring
- **Docker Security Scans** - Container-Schwachstellen (Trivy)
- **Backup-Status** - Erfolgreiche/fehlgeschlagene Backups
- **SSH-Angriffe** - Login-Versuche und Anomalien

### 🤖 Slash Commands
- `/status` - Gesamt-Sicherheitsstatus
- `/scan` - Manuellen Docker-Scan triggern
- `/threats` - Letzte erkannte Bedrohungen
- `/backup` - Backup-Status und Historie
- `/bans` - Aktuell gebannte IPs (Fail2ban + CrowdSec)
- `/aide` - AIDE Integrity Check Status

### 🎨 Features
- **Rich Embeds** - Farbcodierte Alerts (🔴 CRITICAL, 🟠 HIGH, 🟢 OK)
- **Multi-Channel Support** - Verschiedene Channels für verschiedene Alert-Typen
- **Project Tagging** - Filtere Alerts nach Projekt (Sicherheitsdienst, NEXUS, Server)
- **Role Permissions** - Admin-only Commands
- **Auto-Reconnect** - Robust gegen Netzwerk-Probleme

## 📋 Voraussetzungen

- Python 3.9+
- Discord Bot Token (siehe Setup)
- Systemd (für Service)
- Root/Sudo-Zugriff (für Log-Zugriff)

## 🚀 Installation

### 1. Discord Bot erstellen

1. Gehe zu [Discord Developer Portal](https://discord.com/developers/applications)
2. "New Application" → Name: `ShadowOps`
3. Bot-Tab → "Add Bot"
4. "Reset Token" → Token kopieren (⚠️ nur einmal sichtbar!)
5. Unter "Privileged Gateway Intents":
   - ✅ Message Content Intent
   - ✅ Server Members Intent
6. OAuth2 → URL Generator:
   - Scopes: `bot`, `applications.commands`
   - Permissions: `Send Messages`, `Embed Links`, `Use Slash Commands`
7. Generierte URL öffnen → Bot zu Server einladen

### 2. Bot installieren

```bash
cd /home/cmdshadow/shadowops-bot

# Dependencies installieren
pip3 install -r requirements.txt

# Config erstellen
cp config/config.example.yaml config/config.yaml
nano config/config.yaml  # Token + Channel IDs eintragen
```

### 3. Systemd Service aktivieren

```bash
sudo cp shadowops-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable shadowops-bot
sudo systemctl start shadowops-bot

# Status prüfen
sudo systemctl status shadowops-bot
```

## ⚙️ Konfiguration

Bearbeite `config/config.yaml`:

```yaml
discord:
  token: "YOUR_BOT_TOKEN_HERE"
  guild_id: 123456789  # Deine Server-ID

channels:
  # Haupt-Security Channel
  security_alerts: 987654321

  # Optional: Separate Channels
  fail2ban: 111111111
  crowdsec: 222222222
  docker_scans: 333333333
  backups: 444444444

projects:
  sicherheitsdienst:
    enabled: true
    tag: "[SECURITY]"
    color: 0xFF0000  # Rot

  nexus:
    enabled: true
    tag: "[NEXUS]"
    color: 0x00FF00  # Grün

  server:
    enabled: true
    tag: "[SERVER]"
    color: 0x0099FF  # Blau

alerts:
  min_severity: "HIGH"  # LOW, MEDIUM, HIGH, CRITICAL
  rate_limit: 60  # Sekunden zwischen gleichen Alerts
```

## 📊 Verwendung

### Commands in Discord

```
/status           - Zeige Gesamt-Sicherheitsstatus
/scan             - Trigger Docker Security Scan
/threats [hours]  - Zeige Bedrohungen der letzten X Stunden (default: 24)
/backup           - Zeige Backup-Status
/bans [limit]     - Zeige gebannte IPs (default: 10)
/aide             - AIDE Check-Status
```

### Channel IDs finden

1. Discord → Einstellungen → Erweitert → "Entwicklermodus" aktivieren
2. Rechtsklick auf Channel → "ID kopieren"

## 🔧 Entwicklung

```bash
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
│   ├── bot.py              # Haupt-Bot-Logik
│   ├── cogs/
│   │   ├── security.py     # Security-Commands
│   │   ├── monitoring.py   # Monitoring-Commands
│   │   └── admin.py        # Admin-Commands
│   ├── integrations/
│   │   ├── fail2ban.py     # Fail2ban Integration
│   │   ├── crowdsec.py     # CrowdSec Integration
│   │   ├── aide.py         # AIDE Integration
│   │   └── docker.py       # Docker Scan Integration
│   └── utils/
│       ├── config.py       # Config-Loader
│       ├── logger.py       # Logging
│       └── embeds.py       # Discord Embed-Builder
├── config/
│   ├── config.example.yaml # Example Config
│   └── config.yaml         # Deine Config (gitignored)
├── logs/                   # Log-Dateien (gitignored)
├── docs/                   # Dokumentation
├── requirements.txt        # Python Dependencies
├── shadowops-bot.service   # Systemd Service
└── README.md
```

## 🛡️ Security

- **Token-Schutz**: Niemals `config.yaml` committen!
- **File Permissions**: `chmod 600 config/config.yaml`
- **Service-User**: Bot läuft als `cmdshadow` (kein root)
- **Rate Limiting**: Eingebaut gegen Spam

## 📝 Changelog

### Version 1.0.0 (2025-11-12)
- Initial Release
- Fail2ban Integration
- CrowdSec Integration
- AIDE Integration
- Docker Security Scanning
- Backup Monitoring
- Slash Commands

## 📄 Lizenz

MIT License - Erstellt von CommanderShadow

## 🤝 Support

Bei Problemen:
1. Logs prüfen: `sudo journalctl -u shadowops-bot -f`
2. Service-Status: `sudo systemctl status shadowops-bot`
3. Permissions prüfen: Bot braucht Zugriff auf `/var/log/fail2ban/`, `/var/log/crowdsec/`, etc.

---

**Made with 🗡️ by CommanderShadow**
