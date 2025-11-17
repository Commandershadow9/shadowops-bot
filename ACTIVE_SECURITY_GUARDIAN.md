# Active Security Guardian - Vollständige Implementierung

## 🎯 Übersicht

Der ShadowOps Bot ist jetzt ein **vollständig aktiver Security Guardian**, der nicht nur Bedrohungen erkennt und analysiert, sondern diese auch **eigenständig behebt**.

### Was wurde implementiert?

✅ **Foundation Layer** - Sichere Infrastruktur für alle Operationen
✅ **Fixer Modules** - Echte Implementierungen für alle Security-Tools
✅ **Orchestration** - Koordinierte Multi-Event Remediation
✅ **Service Management** - Graceful Stop/Start mit Health Checks
✅ **Backup & Rollback** - Automatische Sicherung und Wiederherstellung
✅ **Impact Analysis** - Projekt-bewusste Risikobewertung

---

## 🏗️ Architektur

```
┌─────────────────────────────────────────────────────────────┐
│                    ShadowOps Bot                            │
│               (Discord Event Gateway)                       │
└──────┬──────────────────────────────────────────────┬───────┘
       │                                               │
       ├──── Security Monitors ────────────────────────┤
       │     • Trivy (Docker Vulnerabilities)         │
       │     • CrowdSec (Network Threats)             │
       │     • Fail2ban (Intrusion Prevention)        │
       │     • AIDE (File Integrity)                  │
       │                                               │
       └──────────────┬──────────────────────────────┘
                      │
       ┌──────────────▼──────────────────────────────┐
       │    Event Watcher (Threat Detection)         │
       │  - Monitors all security tools              │
       │  - Deduplicates events                      │
       │  - Submits to Orchestrator                  │
       └──────────────┬──────────────────────────────┘
                      │
       ┌──────────────▼──────────────────────────────┐
       │  Remediation Orchestrator (Coordinator)     │
       │  - Event batching (10s windows)             │
       │  - Coordinated AI analysis                  │
       │  - Single approval flow                     │
       │  - Sequential execution                     │
       └──────────────┬──────────────────────────────┘
                      │
       ┌──────────────▼──────────────────────────────┐
       │    AI Service (Hybrid Multi-Model)          │
       │  - Ollama (local, primary)                  │
       │  - Claude/Anthropic (fallback)              │
       │  - OpenAI (final fallback)                  │
       │  - RAG Context awareness                    │
       └──────────────┬──────────────────────────────┘
                      │
       ┌──────────────▼──────────────────────────────┐
       │   Self-Healing Coordinator                  │
       │  - Job queue management                     │
       │  - Retry logic with learning                │
       │  - Circuit breaker                          │
       │  - Fix delegation to Fixers                 │
       └──────────────┬──────────────────────────────┘
                      │
       ┌──────────────▼──────────────────────────────┐
       │          Fixer Modules                      │
       │  ┌────────────────────────────────────────┐ │
       │  │  Command Executor                      │ │
       │  │  - Safe shell execution                │ │
       │  │  - Timeout protection                  │ │
       │  │  - Dry-run mode                        │ │
       │  │  - Dangerous pattern blocking          │ │
       │  └────────────────────────────────────────┘ │
       │  ┌────────────────────────────────────────┐ │
       │  │  Backup Manager                        │ │
       │  │  - File/directory/Docker backups       │ │
       │  │  - Automatic rollback                  │ │
       │  │  - 7-day retention                     │ │
       │  └────────────────────────────────────────┘ │
       │  ┌────────────────────────────────────────┐ │
       │  │  Impact Analyzer                       │ │
       │  │  - Project identification              │ │
       │  │  - Downtime estimation                 │ │
       │  │  - Risk assessment                     │ │
       │  │  - DO-NOT-TOUCH validation             │ │
       │  └────────────────────────────────────────┘ │
       │  ┌────────────────────────────────────────┐ │
       │  │  Service Manager                       │ │
       │  │  - Graceful shutdown/startup           │ │
       │  │  - Health check monitoring             │ │
       │  │  - Dependency-aware ordering           │ │
       │  │  - Discord notifications               │ │
       │  └────────────────────────────────────────┘ │
       │  ┌────────────────────────────────────────┐ │
       │  │  Trivy Fixer                           │ │
       │  │  - NPM audit fix                       │ │
       │  │  - APT package updates                 │ │
       │  │  - Base image updates                  │ │
       │  │  - Docker rebuild & verify             │ │
       │  └────────────────────────────────────────┘ │
       │  ┌────────────────────────────────────────┐ │
       │  │  CrowdSec Fixer                        │ │
       │  │  - Permanent IP blocking               │ │
       │  │  - UFW firewall integration            │ │
       │  │  - Extended CrowdSec decisions         │ │
       │  │  - IP range blocking                   │ │
       │  └────────────────────────────────────────┘ │
       │  ┌────────────────────────────────────────┐ │
       │  │  Fail2ban Fixer                        │ │
       │  │  - Jail configuration hardening        │ │
       │  │  - Permanent bans                      │ │
       │  │  - Filter optimization                 │ │
       │  └────────────────────────────────────────┘ │
       │  ┌────────────────────────────────────────┐ │
       │  │  AIDE Fixer                            │ │
       │  │  - Unauthorized change restoration     │ │
       │  │  - Suspicious file quarantine          │ │
       │  │  - Malware scanning (ClamAV)           │ │
       │  │  - AIDE database updates               │ │
       │  └────────────────────────────────────────┘ │
       └─────────────────────────────────────────────┘
```

---

## 🔧 Komponenten im Detail

### 1. Command Executor (`command_executor.py`)

**Sichere Shell-Command Ausführung**

- ✅ Async/await Support
- ✅ Configurable Timeouts (max 1 Stunde)
- ✅ stdout/stderr Capturing
- ✅ Dry-run Mode (Simulation ohne echte Ausführung)
- ✅ Gefährliche Pattern-Blockierung (rm -rf /, dd, mkfs, etc.)
- ✅ Automatisches sudo Handling
- ✅ Working Directory Support
- ✅ Environment Variable Injection
- ✅ Command History (letzte 1000 Commands)
- ✅ Statistiken (Success Rate, Average Duration)

**Blockierte gefährliche Commands:**
- `rm -rf /` - Root-Löschung
- `dd if=.*of=/dev/` - Disk Overwrite
- `mkfs.` - Filesystem Formatting
- `:(){ :|:& };:` - Fork Bomb
- `chmod -R 777` - Recursive Permission Change
- `shutdown/reboot/halt` - System Shutdown
- Und weitere...

### 2. Backup Manager (`backup_manager.py`)

**Automatisches Backup & Restore System**

**Backup-Typen:**
- **File**: Einzelne Dateien (mit gzip Kompression)
- **Directory**: Ganze Verzeichnisse (tar.gz Archive)
- **Docker**: Docker Images (via Tags)
- **Database**: PostgreSQL Dumps (komprimiert)

**Features:**
- ✅ Automatische Backups vor jeder Änderung
- ✅ Compression (gzip/tar.gz)
- ✅ Verification nach Backup
- ✅ Size Limit (max 1GB per Backup)
- ✅ 7-Tage Retention Policy
- ✅ Batch Backup/Rollback
- ✅ Automatisches Cleanup

**Backup-Root:** `/tmp/shadowops_backups/`

### 3. Impact Analyzer (`impact_analyzer.py`)

**Projekt-bewusste Impact Analysis**

**Tracked Projects:**
1. **ShadowOps Bot** (Priorität 1)
   - Path: `/home/cmdshadow/shadowops-bot`
   - Status Monitoring: Python Prozesse

2. **GuildScout** (Priorität 2)
   - Path: `/home/cmdshadow/GuildScout`
   - Database: SQLite Cache

3. **Sicherheitstool** (Priorität 3)
   - Path: `/home/cmdshadow/project`
   - Status: PRODUCTION
   - Port: 3001
   - Database: PostgreSQL

4. **NEXUS** (Priorität 2)
   - Path: `/opt/nexus`
   - Port: 8081

**Impact Severity Levels:**
- **NONE**: Keine Auswirkung
- **MINIMAL**: Kleine Änderungen, kein Neustart
- **MODERATE**: Service Neustart erforderlich
- **SIGNIFICANT**: Downtime erwartet
- **CRITICAL**: Customer-facing Outage

**Downtime Estimation:**
- Automatische Berechnung basierend auf:
  - Impact Severity
  - Anzahl betroffener Projekte
  - Typ der Operation (Rebuild, Restart, etc.)

### 4. Service Manager (`service_manager.py`)

**Service Control mit Health Checks**

**Managed Services:**
- shadowops-bot (Python)
- guildscout (Python)
- sicherheitstool (Node.js/npm)
- nexus (Java)
- postgresql (Database)

**Features:**
- ✅ Graceful Shutdown (mit Timeout)
- ✅ Health Check Monitoring
- ✅ Dependency-aware Start/Stop Order
- ✅ Discord Notifications bei Downtime
- ✅ Batch Operations (Stop/Start mehrere Services)
- ✅ Process State Tracking
- ✅ Auto-retry bei Health Check Failures

**Service States:**
- RUNNING
- STOPPED
- STARTING
- STOPPING
- FAILED
- UNKNOWN

### 5. Trivy Fixer (`fixers/trivy_fixer.py`)

**Docker Vulnerability Remediation**

**Fix Methods:**

**NPM Audit Fix:**
```bash
# 1. Backup package.json & package-lock.json
# 2. npm audit fix
# 3. Falls failed: npm audit fix --force
# 4. npm install (Consistency)
# 5. Docker rebuild
# 6. Trivy re-scan verification
```

**APT Package Updates:**
```bash
# 1. Identify vulnerable packages
# 2. apt-get update
# 3. apt-get upgrade -y [package]
# 4. Docker rebuild
# 5. Verification
```

**Base Image Update:**
```bash
# 1. Backup Dockerfile
# 2. Parse FROM instruction
# 3. Update to newer version
# 4. Docker build
# 5. Trivy re-scan verification
```

**Combined Fix:**
- NPM + APT zusammen
- Mehrere Fix-Methoden nacheinander

### 6. CrowdSec Fixer (`fixers/crowdsec_fixer.py`)

**Network Threat Mitigation**

**Fix Methods:**

**UFW Permanent Blocking:**
```bash
# 1. Validate IP (check whitelist)
# 2. ufw deny from <IP>
# 3. ufw reload
# 4. Verify blocking
```

**Extended CrowdSec Decisions:**
```bash
# 1. Parse duration from strategy (default 24h)
# 2. cscli decisions add --ip <IP> --duration 24h
# 3. Verify decision
```

**IP Range Blocking:**
```bash
# 1. Group IPs by /24 subnet
# 2. If ≥2 IPs from same subnet → Block entire subnet
# 3. ufw deny from <SUBNET>/24
# 4. Verification
```

**Combined Blocking:**
- UFW + CrowdSec zusammen
- Redundante Protection

**Whitelist:**
- 127.0.0.1 (localhost)
- ::1 (IPv6 localhost)
- Configurable zusätzliche IPs

### 7. Fail2ban Fixer (`fixers/fail2ban_fixer.py`)

**Intrusion Prevention Configuration**

**Fix Methods:**

**Jail Hardening:**
```python
# Default Hardened Config:
maxretry = 3      # Reduced from 5
bantime = 3600    # 1 hour (from 10 minutes)
findtime = 600    # 10 minutes

# Updates jail.local with stricter settings
```

**Permanent Bans:**
```bash
# 1. fail2ban-client set sshd banip <IP>
# 2. ufw deny from <IP>  (Redundancy)
# 3. Verification
```

**Filter Optimization:**
- Analyze log patterns
- Update regex filters
- Improve detection rate

### 8. AIDE Fixer (`fixers/aide_fixer.py`)

**File Integrity Violation Resolution**

**Fix Methods:**

**Restore Unauthorized Changes:**
```bash
# 1. Try restore from Git (if in repo)
# 2. Try restore from /var/backups/
# 3. If no backup: Quarantine file
```

**Quarantine Suspicious Files:**
```bash
# 1. Move file to /tmp/aide_quarantine/
# 2. Scan with ClamAV (if available)
# 3. Log malware detection
# 4. Keep for investigation
```

**Approve Legitimate Changes:**
```bash
# 1. Mark as approved
# 2. aide --update (Update database)
# 3. mv aide.db.new aide.db
```

**Change Categorization:**
- **Unauthorized**: Critical paths changed without approval
- **Suspicious**: New files, removed files, unusual changes
- **Legitimate**: Project files, safe directories, approved changes

---

## 📊 Workflow: Von der Bedrohung zum Fix

### Beispiel: Docker Vulnerability (Trivy)

```
1. DETECTION (Event Watcher)
   ├─ Trivy scan findet 5 CRITICAL CVEs
   ├─ Event wird erstellt mit Vulnerability-Details
   └─ Event an Orchestrator gesendet

2. BATCHING (Orchestrator)
   ├─ 10 Sekunden Sammel-Fenster
   ├─ Weitere Events werden gebatched
   └─ Nach 10s: Batch geschlossen

3. AI ANALYSIS (AI Service)
   ├─ ALLE Events zusammen analysiert
   ├─ Kontext aus RAG System (Projekt-Wissen)
   ├─ Generiert koordinierten Plan mit Phasen:
   │  ├─ Phase 1: Backup erstellen
   │  ├─ Phase 2: npm audit fix
   │  ├─ Phase 3: Docker rebuild
   │  └─ Phase 4: Verification
   └─ Confidence Score: 87%

4. IMPACT ANALYSIS (Impact Analyzer)
   ├─ Betroffene Projekte: ShadowOps Bot
   ├─ Severity: MODERATE
   ├─ Downtime: ~2 Minuten
   ├─ Risks: Service restart erforderlich
   └─ Approval: REQUIRED (PARANOID mode)

5. USER APPROVAL (Discord)
   ├─ Embed mit Plan-Details
   ├─ Buttons: ✅ Approve | ❌ Reject | 📋 Details
   ├─ Timeout: 30 Minuten
   └─ User klickt ✅ Approve

6. EXECUTION (Orchestrator + Self-Healing + Trivy Fixer)
   ├─ Phase 0: System Backup
   │  └─ Backup: package.json, package-lock.json, Dockerfile
   ├─ Phase 1: NPM Audit Fix
   │  ├─ npm audit fix
   │  ├─ npm install
   │  └─ ✅ Success
   ├─ Phase 2: Docker Rebuild
   │  ├─ docker build -t shadowops-bot:latest .
   │  └─ ✅ Success
   ├─ Phase 3: Verification
   │  ├─ trivy image --format json shadowops-bot:latest
   │  ├─ Compare: 5 → 0 vulnerabilities
   │  └─ ✅ All vulnerabilities fixed!
   └─ Phase 4: Service Restart
      ├─ Service Manager: Stop shadowops-bot (graceful)
      ├─ Wait for shutdown (timeout: 30s)
      ├─ Start shadowops-bot
      ├─ Health check: Wait for RUNNING
      └─ ✅ Service healthy

7. VERIFICATION & NOTIFICATION (Discord)
   ├─ Discord Update: ✅ All 4 phases successful
   ├─ Stats: Fixed 5 vulnerabilities in 3 minutes
   ├─ Downtime: 45 seconds (estimated 120s)
   └─ Status: Service back online
```

### Bei Fehler: Automatischer Rollback

```
Wenn Phase 2 fehlschlägt:
├─ Execution STOP
├─ Rollback Phase 1 (npm audit fix)
│  └─ Restore package.json, package-lock.json from backup
├─ Rollback Phase 0 (System state)
│  └─ Restore all backups
├─ Service Manager: Restart mit altem Code
└─ Discord Notification: ❌ Fix failed, rolled back
```

---

## ⚙️ Konfiguration

### config.yaml

```yaml
auto_remediation:
  enabled: true

  # DRY-RUN MODE (WICHTIG!)
  dry_run: false  # true = Nur Simulation, false = Echte Fixes

  # Approval Mode
  approval_mode: "paranoid"  # paranoid | balanced | aggressive

  # Scan Intervals
  scan_intervals:
    trivy: 21600      # 6 Stunden
    crowdsec: 30      # 30 Sekunden
    fail2ban: 30      # 30 Sekunden
    aide: 900         # 15 Minuten

  # Circuit Breaker
  circuit_breaker_threshold: 5
  circuit_breaker_timeout: 3600

  # Retry Settings
  max_retry_attempts: 3
```

---

## 🔒 Sicherheitsmaßnahmen

### DO-NOT-TOUCH Paths

**Niemals modifiziert ohne Approval:**
```
/etc/passwd          # User database
/etc/shadow          # Password hashes
/etc/ssh/            # SSH configuration
/boot/               # Boot files
/etc/systemd/system/ # System services
/etc/postgresql/     # Database config
/home/cmdshadow/project/  # Production Sicherheitstool
```

### SAFE-TO-MODIFY (mit Backups)

```
/tmp/                # Temporary files
/var/log/            # Log rotation
/home/cmdshadow/shadowops-bot/logs/
/home/cmdshadow/GuildScout/logs/
```

### REQUIRES-APPROVAL

```
/etc/fail2ban/       # Fail2ban rules
/etc/crowdsec/       # CrowdSec config
/var/lib/docker/     # Docker volumes
/etc/ufw/            # Firewall rules
```

### Command Validation

Jeder Command wird validiert:
1. **Pattern Blocking**: Gefährliche Regex Patterns blockiert
2. **Path Checking**: DO-NOT-TOUCH Validation
3. **Timeout Protection**: Max 1 Stunde per Command
4. **Sandbox Option**: Zukünftig isolierte Execution

---

## 🚀 Erste Schritte

### 1. Dry-Run Modus aktivieren (Empfohlen!)

```yaml
# config.yaml
auto_remediation:
  dry_run: true  # Nur Simulation, keine echten Änderungen!
```

### 2. Bot starten

```bash
cd /home/cmdshadow/shadowops-bot
python src/bot.py
```

### 3. Ersten Test durchführen

Discord → `#auto-remediation-approvals`

- Bot erkennt Bedrohung
- Analysiert mit AI
- Sendet Approval-Request
- Zeigt EXAKT was gemacht würde
- Du klickst ✅ Approve
- Im Dry-Run: Nur Logs, keine echten Änderungen!

### 4. Logs prüfen

```bash
tail -f logs/shadowops-bot.log

# Beispiel Log:
# [INFO] 🔧 Command Executor initialized (mode: DRY-RUN)
# [INFO] 💾 Backup Manager initialized (root: /tmp/shadowops_backups)
# [INFO] 🐳 Applying Trivy fix: Update npm packages
# [INFO] [DRY-RUN] Would execute: npm audit fix
# [INFO] ✅ Fix successful (DRY-RUN mode)
```

### 5. Wenn alles gut läuft: Dry-Run deaktivieren

```yaml
# config.yaml
auto_remediation:
  dry_run: false  # Jetzt werden echte Fixes ausgeführt!
```

---

## 📈 Monitoring & Stats

### Discord Channels

- `#auto-remediation-alerts` - Live Updates während Execution
- `#auto-remediation-approvals` - Approval Requests
- `#auto-remediation-stats` - Erfolgs-Statistiken
- `#bot-status` - Service Status Updates

### Statistiken

Der Bot tracked:
- Total Jobs
- Successful Fixes
- Failed Fixes
- Average Attempts per Job
- Circuit Breaker Status
- Backup Statistics
- Service Uptimes

---

## 🔧 Troubleshooting

### "Fixes werden nicht ausgeführt"

**Check:**
1. `dry_run: true` in config.yaml?
2. Approval Mode = PARANOID → User muss klicken!
3. Circuit Breaker OPEN? (Nach 5 Failures)

### "Backup Fehler"

**Check:**
1. `/tmp/shadowops_backups/` existiert?
2. Disk Space verfügbar?
3. Permissions korrekt?

### "Service Start Failed"

**Check:**
1. Service Manager Commands in `service_manager.py` korrekt?
2. Health Checks erreichbar?
3. Ports frei? (3001 für Sicherheitstool)

### "Rollback funktioniert nicht"

**Check:**
1. Backups wurden erstellt? (Logs prüfen)
2. Backup Manager hat Permissions?
3. Original Pfade noch vorhanden?

---

## 🎓 Next Steps & Future Enhancements

### Bereits Implementiert ✅

- Command Execution mit Safety
- Backup & Rollback System
- Impact Analysis
- Service Management
- Alle 4 Fixer (Trivy, CrowdSec, Fail2ban, AIDE)
- Orchestrator Integration
- Self-Healing Integration

### Geplant für Zukunft 🔮

- **Continuous Fix Loop**: Solange fixen bis alles behoben
- **Health Checker**: Automatische Post-Fix Validation
- **Fix Verifier**: Verify dass Vulnerability wirklich weg ist
- **Web Dashboard**: Grafische Übersicht aller Fixes
- **Metrics Export**: Prometheus/Grafana Integration
- **ML-Based Learning**: Bot lernt aus Fehlern
- **Multi-Server Support**: Mehrere Server gleichzeitig verwalten

---

## 📝 Wichtige Hinweise

⚠️ **PARANOID Mode:** Ist der sicherste Modus. JEDE Änderung braucht deine Freigabe!

⚠️ **Dry-Run:** Beim ersten Start IMMER aktivieren zum Testen!

⚠️ **Backups:** Werden automatisch erstellt, aber prüfe regelmäßig `/tmp/shadowops_backups/`

⚠️ **Production:** Sicherheitstool ist PRODUCTION → Extra vorsichtig!

⚠️ **Circuit Breaker:** Schützt vor Infinite Loops. Nach 5 Fehlern → STOP

---

## 🙏 Support

Bei Fragen oder Problemen:
1. Logs prüfen: `logs/shadowops-bot.log`
2. Discord: Check `#bot-status` Channel
3. Dry-Run aktivieren zum Debuggen
4. Config validieren: `config.yaml`

**Der Bot ist jetzt ein vollwertiger Active Security Guardian! 🛡️**
