# 🤖 Event-Driven Auto-Remediation System

**Automatische Behebung von Sicherheitslücken mit Self-Healing Loop**

---

## 📋 Inhaltsverzeichnis

1. [Überblick](#überblick)
2. [Features](#features)
3. [Architektur](#architektur)
4. [Setup](#setup)
5. [Approval Modes](#approval-modes)
6. [Slash Commands](#slash-commands)
7. [Monitoring & Statistiken](#monitoring--statistiken)
8. [Troubleshooting](#troubleshooting)
9. [Best Practices](#best-practices)

---

## Überblick

Das Event-Driven Auto-Remediation System erweitert den ShadowOps Bot um vollautomatische Sicherheitslücken-Behebung mit intelligenter Retry-Logik und mehrschichtigem Schutz.

### Was macht es?

**Vorher (Manuell)**:
1. Bot erkennt Vulnerability
2. Sendet Discord Alert
3. Admin muss manuell fixen
4. Zeitverlust, menschliche Fehler möglich

**Jetzt (Automatisch)**:
1. 🔍 **Event Watcher** erkennt Vulnerability in Echtzeit
2. 🤖 **Self-Healing** generiert automatisch Fix-Strategie
3. ✅ **Circuit Breaker** schützt vor Endlos-Loops
4. 🔄 **Retry Logic** versucht bis zu 3x mit unterschiedlichen Strategien
5. 📊 **Discord Live-Updates** zeigen Fortschritt
6. ✋ **Human Approval** bei kritischen Fixes (konfigurierbar)

---

## Features

### 🔍 Event-Driven Architecture

**Unterschiedliche Scan-Intervalle basierend auf Urgency:**

| Quelle   | Interval | Warum?                                    |
|----------|----------|-------------------------------------------|
| Trivy    | 6 Stunden| Docker-Scans sind langsam, Vulnerabilities ändern sich selten |
| CrowdSec | 30 Sek.  | Aktive Bedrohungen müssen sofort erkannt werden |
| Fail2ban | 30 Sek.  | Neue Bans schnell erfassen               |
| AIDE     | 15 Min.  | File Integrity Checks moderat wichtig    |

**Vorteile:**
- ✅ **Effizienter**: Keine unnötigen Scans
- ✅ **Schneller**: Kritische Threats werden sofort erkannt
- ✅ **Ressourcenschonend**: Reduziert CPU/Disk I/O Last

### 🔧 Self-Healing mit AI-Lernen

**3-Versuchs-Strategie:**

```
1. Versuch: Standard-Fix (aus Templates)
   ↓ (bei Fehler)
2. Versuch: Angepasste Strategie (lernt aus Fehler #1)
   ↓ (bei Fehler)
3. Versuch: Alternative Approach (komplett anderer Ansatz)
   ↓ (bei Fehler)
Escalation: Human Review erforderlich
```

**Beispiel Docker Vulnerability:**
```
Versuch 1: Update Package von 1.2.0 → 1.2.5 (empfohlene Version)
   ❌ Fehler: Breaking Change

Versuch 2: Update Package von 1.2.0 → 1.2.4 (vorherige Patch-Version)
   ❌ Fehler: Weiterhin Incompatibility

Versuch 3: Switch zu Alternative Package (Workaround)
   ✅ Erfolg!
```

### ⚡ Circuit Breaker Pattern

Schützt vor Endlos-Schleifen und System-Überlastung:

**States:**
- 🟢 **CLOSED** (Normal): Alle Fixes werden versucht
- 🔴 **OPEN** (Fehler): Nach 5+ Failures wird System gestoppt
- 🟡 **HALF_OPEN** (Test): Nach Cooldown (1h) wird 1 Test-Fix versucht

**Beispiel-Szenario:**
```
Fix 1: ❌ Failed
Fix 2: ❌ Failed
Fix 3: ❌ Failed
Fix 4: ❌ Failed
Fix 5: ❌ Failed
--> Circuit Breaker: OPEN
--> Alle weiteren Fixes werden blockiert für 1 Stunde
--> Discord Alert: "⚠️ Circuit Breaker OPEN - System gestoppt"
--> Nach 1h: HALF_OPEN - Teste 1 Fix
   ✅ Erfolg --> CLOSED (weiter machen)
   ❌ Fehler --> Bleibe OPEN für weitere 1h
```

### 🎯 3 Approval Modes

| Mode       | Verhalten | Wann nutzen? |
|------------|-----------|--------------|
| **PARANOID** | JEDER Fix benötigt Human Approval | ✅ **Empfohlen für Start** - Maximale Kontrolle, lerne System kennen |
| **BALANCED** | LOW/MEDIUM auto, HIGH/CRITICAL brauchen Approval | Empfohlen nach 1 Woche Testing - Gute Balance |
| **AGGRESSIVE** | Nur CRITICAL braucht Approval, Rest auto | ⚠️ NUR für Experten - Riskant! |

---

## Architektur

```
┌─────────────────────────────────────────────────────────────────┐
│                    SECURITY INTEGRATIONS                         │
├────────────┬────────────┬────────────┬────────────┬────────────┤
│ Trivy      │ CrowdSec   │ Fail2ban   │ AIDE       │ (Future)   │
│ (6h)       │ (30s)      │ (30s)      │ (15min)    │            │
└────────────┴────────────┴────────────┴────────────┴────────────┘
                              ↓
                 ┌────────────────────────┐
                 │   Event Watcher        │
                 │   (Deduplication)      │
                 └────────────────────────┘
                              ↓
                 ┌────────────────────────┐
                 │  Self-Healing          │
                 │  Coordinator           │
                 ├────────────────────────┤
                 │ • Job Queue            │
                 │ • Retry Logic          │
                 │ • Circuit Breaker      │
                 └────────────────────────┘
                              ↓
                ┌─────────────┴─────────────┐
                ↓                           ↓
    ┌────────────────────┐      ┌────────────────────┐
    │ Approval Required? │      │  Auto-Fix          │
    │ (PARANOID Mode)    │      │  (BALANCED/        │
    │                    │      │   AGGRESSIVE)      │
    └────────────────────┘      └────────────────────┘
                ↓                           ↓
    ┌────────────────────┐      ┌────────────────────┐
    │ Discord Approval   │      │  Execute Fix       │
    │ Request            │      │  (max 3 attempts)  │
    └────────────────────┘      └────────────────────┘
                ↓                           ↓
    ┌────────────────────┐      ┌────────────────────┐
    │ Human: ✅ Approve  │      │  ✅ Success        │
    │        ❌ Reject   │      │  ❌ Failed → Retry │
    └────────────────────┘      └────────────────────┘
```

---

## Setup

### 1. Prerequisites

Stelle sicher, dass alle Security-Integrationen aktiv sind:
```bash
# Prüfe Services
sudo systemctl status fail2ban
sudo systemctl status crowdsec
docker ps  # Check if Trivy scans running
sudo aide --version
```

### 2. Config aktualisieren

Edit `config/config.yaml`:

```yaml
auto_remediation:
  # 1. Aktiviere System (START MIT FALSE!)
  enabled: false  # Erst nach Config fertig auf true setzen

  # 2. Wähle Approval Mode
  approval_mode: "paranoid"  # Empfohlen für erste Woche!

  # 3. Scan Intervals (optional anpassen)
  scan_intervals:
    trivy: 21600    # 6 Stunden
    crowdsec: 30    # 30 Sekunden
    fail2ban: 30    # 30 Sekunden
    aide: 900       # 15 Minuten

  # 4. Discord Channels erstellen und IDs eintragen
  notifications:
    alerts_channel: 123456789012345678      # Erstelle #auto-remediation-alerts
    approvals_channel: 123456789012345678   # Erstelle #auto-remediation-approvals
    stats_channel: 123456789012345678       # Erstelle #auto-remediation-stats
```

### 3. Discord Channels erstellen

**In Discord:**
1. Erstelle 3 neue Text-Channels:
   - `#auto-remediation-alerts` - Für Live Fix-Updates
   - `#auto-remediation-approvals` - Für Approval-Requests
   - `#auto-remediation-stats` - Für tägliche Statistiken

2. Hole Channel-IDs:
   - Aktiviere Developer Mode (User Settings → App Settings → Advanced)
   - Rechtsklick auf Channel → "ID kopieren"
   - Trage IDs in `config.yaml` ein

### 4. Bot neu starten

```bash
# Stoppe Bot
sudo systemctl stop shadowops-bot

# Starte Bot neu
sudo systemctl start shadowops-bot

# Check Logs
journalctl -u shadowops-bot -n 100 -f
```

**Erwartete Log-Ausgaben:**
```
🗡️ ShadowOps Bot startet...
🤖 Auto-Remediation System wird initialisiert...
✅ Self-Healing Coordinator initialized
✅ Security Event Watcher initialized
🎯 Approval Mode: paranoid
🔍 Starting Security Event Watcher (EFFICIENT Mode)...
🔍 Starting Trivy watcher (21600s intervals)
🔍 Starting CrowdSec watcher (30s intervals)
🔍 Starting Fail2ban watcher (30s intervals)
🔍 Starting AIDE watcher (900s intervals)
✅ Auto-Remediation System initialisiert
✅ Bot eingeloggt als ShadowOps#1234
```

### 5. Teste Commands

In Discord:
```
/remediation-stats
```

Sollte anzeigen:
- Event Watcher Status: 🟢 Running
- Circuit Breaker: 🟢 CLOSED
- Approval Mode: PARANOID

### 6. Aktiviere System

**Nach erfolgreichen Tests:**

Edit `config/config.yaml`:
```yaml
auto_remediation:
  enabled: true  # ✅ Jetzt aktivieren!
```

Restart Bot:
```bash
sudo systemctl restart shadowops-bot
```

---

## Approval Modes

### PARANOID Mode (Empfohlen für Start)

**Verhalten:**
- ✅ JEDER Fix benötigt manuelle Freigabe
- Bot sendet Approval-Request in `#auto-remediation-approvals`
- Fix wird NICHT ausgeführt bis du Approve klickst

**Discord Approval Request:**
```
🔒 APPROVAL REQUIRED

Source: Trivy (Docker)
Severity: CRITICAL
Vulnerability: CVE-2024-12345
Affected: nodejs package 18.0.0

Proposed Fix:
Update nodejs from 18.0.0 to 18.19.1

Confidence: 85%

[✅ Approve] [❌ Reject]
```

**Wann nutzen:**
- ✅ Erste 7 Tage - System kennenlernen
- ✅ Production-Umgebungen
- ✅ Wenn du jede Änderung kontrollieren willst

### BALANCED Mode (Empfohlen nach Testphase)

**Verhalten:**
- ✅ LOW/MEDIUM Vulnerabilities: Auto-Fix (keine Approval)
- ✋ HIGH/CRITICAL: Benötigt Approval

**Beispiel:**
```
LOW (npm package outdated)      → Auto-Fixed in 30 Sekunden
MEDIUM (Config hardening)       → Auto-Fixed in 30 Sekunden
HIGH (Docker CVE)               → Approval erforderlich
CRITICAL (SSH Vulnerability)    → Approval erforderlich
```

**Wann nutzen:**
- ✅ Nach 1 Woche erfolgreichen PARANOID Tests
- ✅ Wenn du Routine hast und triviale Fixes automatisieren willst
- ✅ 90% der Server-Admins empfohlen

### AGGRESSIVE Mode (NUR für Experten!)

**Verhalten:**
- ✅ LOW/MEDIUM/HIGH: Auto-Fix
- ✋ NUR CRITICAL: Benötigt Approval

**Warnung:**
⚠️ Riskant! HIGH-Severity Fixes können Breaking Changes verursachen!

**Wann nutzen:**
- ✅ Nach Monaten erfolgreicher BALANCED Tests
- ✅ Wenn du 100% Vertrauen in System hast
- ✅ Nur in Non-Critical Umgebungen

**Nicht nutzen:**
- ❌ Production Databases
- ❌ Public-Facing Services
- ❌ Financial/Healthcare Systems

---

## Slash Commands

### `/stop-all-fixes`

**Emergency Stop - Stoppt ALLE laufenden Fixes sofort**

```
/stop-all-fixes
```

**Was passiert:**
1. Alle Pending Jobs werden gecancelt
2. Aktive Fixes werden abgebrochen
3. Event Watcher wird gestoppt
4. System geht in Pause-Modus

**Output:**
```
🛑 Emergency Stop Executed

Ausgeführt von: @YourName
Gestoppte Jobs: 5

Reaktivierung:
Bot-Neustart erforderlich:
sudo systemctl restart shadowops-bot
```

**Wann nutzen:**
- ❌ System macht zu viele falsche Fixes
- ❌ Fehler-Kaskade erkannt
- ❌ Du musst System sofort stoppen

### `/remediation-stats`

**Zeigt detaillierte Statistiken an**

```
/remediation-stats
```

**Output:**
```
📊 Auto-Remediation Statistics

🔍 Event Watcher
Status: 🟢 Running
Total Scans: 1,234
Total Events: 56
Events in History: 50

🔧 Self-Healing Coordinator
Total Jobs: 42
✅ Successful: 38
❌ Failed: 2
✋ Requires Approval: 2
📈 Success Rate: 95.0%
🔄 Avg Attempts: 1.2

📋 Queue Status
Pending: 2
Active: 1
Completed: 39

⚡ Circuit Breaker
🟢 CLOSED
Failures: 0

🎯 Approval Mode
PARANOID

⏱️ Scan Intervals
Trivy: 21600s
CrowdSec: 30s
Fail2ban: 30s
AIDE: 900s
```

---

## Monitoring & Statistiken

### Live-Monitoring via Discord

**Channel: `#auto-remediation-alerts`**

Zeigt Live-Updates für jeden Fix:

```
🔍 NEW VULNERABILITY DETECTED

Source: Trivy
Severity: CRITICAL
CVE: CVE-2024-12345
Package: nodejs 18.0.0 → 18.19.1

Status: Analyzing...
```

```
🔧 FIX ATTEMPT #1

Strategy: Update package to 18.19.1
Confidence: 85%
Status: In Progress...
```

```
✅ FIX SUCCESSFUL

Attempts: 1
Duration: 45 seconds
Deployment: Rolling update completed
Health Check: ✅ Passed
```

### Circuit Breaker Alerts

**Bei OPEN:**
```
🔴 CIRCUIT BREAKER OPEN

Too many failures detected (5+)
Auto-Remediation paused for 1 hour

Last Failures:
1. Docker CVE-2024-001: Failed to update package
2. CrowdSec IP Ban: Failed to apply firewall rule
3. AIDE File Restore: Backup not found
4. Fail2ban Config: Syntax error
5. Docker CVE-2024-002: Incompatible version

Action Required:
Review logs and fix underlying issues

Cooldown: 59 minutes remaining
```

### Daily Stats Report

**Channel: `#auto-remediation-stats`**

Täglich um 06:00 Uhr:

```
📊 DAILY AUTO-REMEDIATION REPORT
2025-01-15

Yesterday's Activity:
✅ Successful Fixes: 12
❌ Failed Fixes: 1
✋ Awaiting Approval: 2

Top Fixed Vulnerabilities:
1. Docker npm packages (5 fixes)
2. CrowdSec IP bans (3 fixes)
3. AIDE config updates (2 fixes)

Success Rate: 92.3%
Avg Fix Time: 1.2 minutes
Total Time Saved: ~4 hours

Circuit Breaker: 🟢 Healthy
System Status: ✅ Operational
```

---

## Troubleshooting

### Problem: Bot startet nicht mit Auto-Remediation

**Symptom:**
```
❌ Fehler beim Initialisieren der Auto-Remediation
```

**Ursachen & Lösungen:**

1. **Config fehlt:**
   ```bash
   # Prüfe ob config.yaml existiert
   cat /path/to/shadowops-bot/config/config.yaml | grep auto_remediation
   ```

2. **Channel-IDs falsch:**
   ```yaml
   # In config.yaml - Müssen valide Discord Channel IDs sein
   notifications:
     alerts_channel: 123456789012345678  # Nicht null!
     approvals_channel: 123456789012345678
     stats_channel: 123456789012345678
   ```

3. **Integrationen nicht aktiv:**
   ```bash
   # Prüfe alle Services
   sudo systemctl status fail2ban
   sudo systemctl status crowdsec
   docker ps
   sudo aide --check
   ```

### Problem: Circuit Breaker ist OPEN

**Symptom:**
```
🔴 Circuit Breaker OPEN
Auto-Remediation paused
```

**Ursache:**
Zu viele Failures in kurzer Zeit (5+)

**Lösung:**

1. **Check Logs für root cause:**
   ```bash
   journalctl -u shadowops-bot -n 500 | grep "❌"
   ```

2. **Identifiziere Problem:**
   - Docker Image Pull Failed? → Prüfe Registry
   - Config Syntax Error? → Validate Config Files
   - Permission Denied? → Check sudo/file permissions

3. **Fixe Root Cause**

4. **Warte Cooldown ab** (1 Stunde) ODER **Restart Bot:**
   ```bash
   sudo systemctl restart shadowops-bot
   # Circuit Breaker wird zurückgesetzt
   ```

### Problem: Fixes werden nicht ausgeführt

**Symptom:**
Events werden erkannt, aber keine Fixes passieren

**Check:**

1. **Approval Mode:**
   ```bash
   # In Discord:
   /remediation-stats
   # Check: "Approval Mode: PARANOID"
   ```

   Wenn PARANOID → Fixes brauchen manuelle Approval in `#auto-remediation-approvals`

2. **Queue Status:**
   ```bash
   # In Discord:
   /remediation-stats
   # Check: "Pending: X"
   ```

   Wenn Pending > 0 → Jobs warten auf Approval

3. **Circuit Breaker:**
   ```bash
   # In Discord:
   /remediation-stats
   # Check: "🔴 OPEN" vs "🟢 CLOSED"
   ```

   Wenn OPEN → System ist pausiert

### Problem: Zu viele False-Positive Fixes

**Symptom:**
System versucht Fixes für nicht-kritische Issues

**Lösung:**

Option 1: **Erhöhe Approval Requirements**
```yaml
# config.yaml
auto_remediation:
  approval_mode: "paranoid"  # Jeder Fix benötigt Approval
```

Option 2: **Adjustiere Scan Intervals**
```yaml
# config.yaml - Reduziere Scan-Häufigkeit
scan_intervals:
  trivy: 43200    # 12 Stunden statt 6
  crowdsec: 60    # 1 Minute statt 30 Sekunden
```

Option 3: **Severity Filter** (Future Feature)
```yaml
# config.yaml
auto_remediation:
  min_severity: "HIGH"  # Ignoriere LOW/MEDIUM
```

### Problem: Bot ist zu langsam

**Symptom:**
Fixes dauern zu lange

**Optimierungen:**

1. **Reduziere Max Retry Attempts:**
   ```yaml
   # config.yaml
   auto_remediation:
     max_retry_attempts: 2  # Statt 3
   ```

2. **Erhöhe Scan Intervals für langsame Scans:**
   ```yaml
   scan_intervals:
     trivy: 86400  # 1x täglich statt 6h
   ```

3. **Check Server Resources:**
   ```bash
   htop  # CPU Usage
   iotop  # Disk I/O
   ```

---

## Best Practices

### 🎯 Start Strategy (Week 1)

**Day 1-7: PARANOID Mode**
```yaml
auto_remediation:
  enabled: true
  approval_mode: "paranoid"
```

**Was tun:**
1. ✅ Beobachte JEDEN Approval Request genau
2. ✅ Prüfe ob Fixes sinnvoll sind
3. ✅ Lerne welche Fixes funktionieren
4. ✅ Checke `/remediation-stats` täglich

**Erfolgs-Kriterien für Week 1:**
- ✅ 10+ erfolgreiche Fixes
- ✅ Keine falschen Fixes
- ✅ Circuit Breaker blieb CLOSED
- ✅ Du verstehst System

### 🚀 Week 2-4: BALANCED Mode

Nach erfolgreicher Week 1:
```yaml
auto_remediation:
  approval_mode: "balanced"
```

**Was ändert sich:**
- ✅ LOW/MEDIUM fixes laufen automatisch
- ✋ HIGH/CRITICAL brauchen weiter Approval

**Monitoring:**
- Check `/remediation-stats` 2x täglich
- Überwache `#auto-remediation-alerts` Channel
- Bei Problemen: Zurück zu PARANOID

### ⚡ Month 2+: AGGRESSIVE (Optional)

**NUR wenn:**
- ✅ 100+ erfolgreiche Fixes ohne Probleme
- ✅ Keine Circuit Breaker OPEN Events
- ✅ Du vertraust System zu 100%

```yaml
auto_remediation:
  approval_mode: "aggressive"
```

### 📊 Regelmäßige Reviews

**Täglich:**
- ✅ Check `/remediation-stats`
- ✅ Scan `#auto-remediation-alerts` für Failures

**Wöchentlich:**
- ✅ Review Success Rate (sollte >90% sein)
- ✅ Check Circuit Breaker History
- ✅ Adjustiere Scan Intervals falls nötig

**Monatlich:**
- ✅ Review Total Time Saved
- ✅ Evaluate Approval Mode
- ✅ Update Config basierend auf Learnings

### 🔒 Security Guidelines

**DO:**
- ✅ Start mit PARANOID Mode
- ✅ Teste in Staging zuerst
- ✅ Backup vor kritischen Fixes
- ✅ Monitor Logs aktiv

**DON'T:**
- ❌ NIEMALS AGGRESSIVE in Production ohne Tests
- ❌ NIEMALS Circuit Breaker Threshold zu hoch setzen
- ❌ NIEMALS alle Approvals blindly klicken
- ❌ NIEMALS Auto-Remediation ohne Monitoring

### 📈 Optimization Tips

**Für Performance:**
```yaml
scan_intervals:
  trivy: 43200    # Reduziere zu 12h wenn wenige CVEs
  crowdsec: 60    # Erhöhe zu 60s wenn wenig Traffic
```

**Für Sicherheit:**
```yaml
circuit_breaker_threshold: 3  # Reduziere zu 3 (schneller stop)
max_retry_attempts: 2          # Reduziere zu 2 (weniger Fehler)
```

**Für Convenience:**
```yaml
approval_mode: "balanced"  # Sweet Spot für 90% der User
```

---

## 🎉 Gratulation!

Du hast jetzt ein vollautomatisches, selbstheilendes Security-System!

**Was du erreicht hast:**
- ✅ Automatische Vulnerability-Behebung
- ✅ Schutz vor Endlos-Loops (Circuit Breaker)
- ✅ Intelligente Retry-Logic mit Lernen
- ✅ Live Discord-Monitoring
- ✅ Approval-Workflow für Kontrolle

**Nächste Schritte:**
1. Überwache System für 1 Woche in PARANOID Mode
2. Wechsle zu BALANCED Mode wenn stabil
3. Genieße freie Zeit (keine manuellen Fixes mehr!)
4. Check Statistiken regelmäßig

**Support:**
Bei Fragen oder Problemen → Check Troubleshooting Section oder öffne Issue auf GitHub!

---

**Happy Auto-Remediating! 🤖🔒**
