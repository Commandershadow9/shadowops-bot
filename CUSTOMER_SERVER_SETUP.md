# Customer Server Setup - GuildScout Bot
**Date:** 2025-11-25
**Status:** ✅ READY

## Übersicht

ShadowOps Bot kann jetzt **automatisch Channels** auf Kunden-Servern erstellen und verwalten.

**Was passiert automatisch:**
- ✅ Erstellt 2 Channels in der Admin-Kategorie
- ✅ Setzt Admin-Only Permissions
- ✅ Konfiguriert Benachrichtigungen (Git + Status)
- ✅ Zeigt Config in Logs zum Kopieren

## Voraussetzungen

### 1. Bot Client ID herausfinden

**Option A: Discord Developer Portal**
1. Gehe zu https://discord.com/developers/applications
2. Wähle deine Application (ShadowOps Bot)
3. Unter "General Information" → Application ID kopieren

**Option B: Aus Discord**
```bash
# Bot ist bereits auf deinem Server - Rechtsklick auf Bot → Copy ID
# (Developer Mode muss aktiviert sein)
```

### 2. Bot-Einladungs-Link generieren

```
https://discord.com/api/oauth2/authorize?client_id=DEINE_BOT_CLIENT_ID&permissions=268446736&scope=bot
```

**Benötigte Permissions (268446736):**
- ✅ View Channels
- ✅ Send Messages
- ✅ Embed Links
- ✅ Read Message History
- ✅ Manage Channels (für Auto-Setup)
- ✅ Read Messages/View Channels in Category

**Wichtig:** Ersetze `DEINE_BOT_CLIENT_ID` mit der tatsächlichen Client ID!

## Setup Schritte

### Schritt 1: Kategorie auf Kunden-Server prüfen

**Der Bot braucht:**
- Kategorie: `🚨 | ADMIN AREA`
- Category ID: `1398982574923321494`

**Prüfen ob vorhanden:**
1. Discord → Rechtsklick auf Kategorie "🚨 | ADMIN AREA"
2. Copy ID
3. Prüfe ob ID = `1398982574923321494`

**Falls nicht vorhanden:** Kategorie erstellen mit dieser ID oder Code anpassen (siehe unten)

### Schritt 2: Bot einladen

1. Einladungs-Link öffnen (mit deiner Client ID)
2. Kunden-Server auswählen
3. Permissions bestätigen
4. Bot joinet Server

### Schritt 3: Automatisches Setup läuft

**Was passiert:**
```
Bot joined: KundenServer (ID: 123456789)
🔧 Starting automatic channel setup for KundenServer
🔒 Admin role found: Admin
✅ Created channel: 📢guildscout-updates (ID: 987654321)
✅ Created channel: 🔴guildscout-status (ID: 111222333)
🎉 Setup complete for guildscout on KundenServer
```

**Ergebnis:**
- 2 neue Channels in 🚨 | ADMIN AREA
- Nur Admins können sie sehen
- Bot postet Welcome-Message mit Konfiguration

### Schritt 4: Config-Snippet kopieren

**ShadowOps Bot loggt automatisch:**
```
====================================
📋 ADD THIS TO config/config.yaml:
====================================
projects:
  guildscout:
    external_notifications:
      - guild_id: 123456789
        channel_id: 987654321
        enabled: true
        notify_on:
          git_push: true
          offline: false
          online: false
          errors: false

      - guild_id: 123456789
        channel_id: 111222333
        enabled: true
        notify_on:
          git_push: false
          offline: true
          online: true
          errors: true
====================================
```

**Logs ansehen:**
```bash
sudo journalctl -u shadowops-bot.service -n 100 | grep -A 20 "ADD THIS TO config"
```

### Schritt 5: Config anpassen

**Auf dem Server:**
```bash
nano /home/cmdshadow/shadowops-bot/config/config.yaml
```

**Snippet einfügen:**
```yaml
projects:
  guildscout:
    enabled: true
    tag: ⚡ [GUILDSCOUT]
    # ... bestehende config ...

    # NEU: Aus Logs kopiert
    external_notifications:
      - guild_id: 123456789  # Kunden-Server
        channel_id: 987654321  # #guildscout-updates
        enabled: true
        notify_on:
          git_push: true      # Git Updates senden
          offline: false      # Keine offline Alerts hier
          online: false
          errors: false

      - guild_id: 123456789  # Kunden-Server
        channel_id: 111222333  # #guildscout-status
        enabled: true
        notify_on:
          git_push: false     # Keine Git Updates hier
          offline: true       # Nur Status Alerts
          online: true
          errors: true
```

### Schritt 6: Bot neu starten

```bash
sudo systemctl restart shadowops-bot.service
```

### Schritt 7: Testen

**Git Push Test:**
```bash
cd /home/cmdshadow/GuildScout
git commit --allow-empty -m "test: Customer notification test"
git push
```

**Ergebnis:**
- Message in `#guildscout-updates` auf Kunden-Server ✅
- Message in deinem `deployment_log` Channel ✅

**Status Test:**
```bash
# GuildScout kurz stoppen
sudo systemctl stop guildscout.service
# 5 Minuten warten (oder check_interval Zeit)
sudo systemctl start guildscout.service
```

**Ergebnis:**
- Offline-Message in `#guildscout-status` ✅
- Online-Message in `#guildscout-status` ✅

## Channels erklärt

### #📢guildscout-updates
**Zweck:** Git Push Updates mit AI-generierten Patch Notes

**Beispiel-Nachricht:**
```
✨ Updates for GuildScout

🆕 New Features:
• Member tracking system implemented
• Dashboard shows real-time statistics

🐛 Bug Fixes:
• Login timeout issues resolved
• Database connection stability improved

3 commit(s) by YourName
```

**Sprache:** Englisch (weil `patch_notes.language: en`)
**Benachrichtigungen:** Nur Git Pushes

### #🔴guildscout-status
**Zweck:** Bot Status Monitoring

**Beispiel Offline:**
```
🔴 guildscout is DOWN

Project: guildscout
Status: 🔴 Offline
Consecutive Failures: 3
Error: Connection timeout after 10s
Uptime (before incident): 99.87%
```

**Beispiel Online:**
```
✅ guildscout is BACK ONLINE

Project: guildscout
Status: 🟢 Online
Response Time: 45ms
Current Uptime: 99.92%
```

**Benachrichtigungen:** Nur Status (offline/online/errors)

## Anpassungen

### Andere Kategorie verwenden

**Falls Kunde andere Kategorie hat:**

```python
# src/integrations/customer_server_setup.py
# Zeile 35
'category_id': 1234567890123456789,  # Neue Kategorie ID
```

### Andere Channel-Namen

```python
# src/integrations/customer_server_setup.py
# Zeile 37-57
'channels': [
    {
        'name': 'bot-updates',  # Statt guildscout-updates
        'emoji': '🔔',
        # ...
    }
]
```

### Andere Projekte hinzufügen

```python
# src/integrations/customer_server_setup.py
# Zeile 32
self.project_channels = {
    'guildscout': { ... },
    'nexus': {  # Neues Projekt
        'category_id': 1398982574923321494,
        'channels': [ ... ]
    }
}
```

## Troubleshooting

### Bot erstellt keine Channels

**Logs prüfen:**
```bash
sudo journalctl -u shadowops-bot.service -f | grep "Setup\|channel"
```

**Mögliche Ursachen:**
1. **Kategorie nicht gefunden**
   ```
   ❌ Category 1398982574923321494 not found on KundenServer
   ```
   **Fix:** Kategorie erstellen oder ID in Code anpassen

2. **Keine Permissions**
   ```
   ❌ Missing permissions to create channels on KundenServer
   ```
   **Fix:** Bot mit "Manage Channels" Permission einladen

3. **Channels existieren bereits**
   ```
   ✅ Channel 📢guildscout-updates already exists, skipping
   ```
   **Info:** Normal, Setup läuft nur einmal

### Bot hat keine Rechte in Channels

**Symptom:** Bot kann nicht posten

**Fix:**
1. Channel Settings → Permissions
2. @ShadowOps Bot Role hinzufügen
3. Permissions: Send Messages ✅, Embed Links ✅

### Notifications kommen nicht an

**Debug:**
```bash
# Logs in Echtzeit
sudo journalctl -u shadowops-bot.service -f | grep "external"

# Test Git Push
cd /home/cmdshadow/GuildScout
git commit --allow-empty -m "test"
git push

# Prüfe Logs
sudo journalctl -u shadowops-bot.service --since "2 minutes ago" | grep "📤"
```

## GuildScout Bot stumm schalten (Option B)

**Aktuell:** GuildScout sendet selbst Status-Messages

**Ziel:** Nur ShadowOps soll senden (keine Duplikate)

**Schritte:**
1. GuildScout Config finden
2. Status-Notifications deaktivieren
3. Nur ShadowOps monitort und benachrichtigt

**Vorteil:**
- ✅ Keine doppelten Messages
- ✅ Zentrale Überwachung
- ✅ Einheitliches Format

## Nächste Schritte

1. ✅ Bot Client ID herausfinden
2. ✅ Einladungs-Link generieren
3. ✅ Bot auf Kunden-Server einladen
4. ✅ Automatisches Setup abwarten (2-5 Sekunden)
5. ✅ Config-Snippet aus Logs kopieren
6. ✅ config.yaml anpassen
7. ✅ Bot neu starten
8. ✅ Testen mit Git Push

## Support

**Bei Problemen:**
```bash
# Vollständige Logs
sudo journalctl -u shadowops-bot.service --since "10 minutes ago"

# Setup-spezifisch
sudo journalctl -u shadowops-bot.service | grep -A 30 "Starting automatic channel setup"

# Fehler
sudo journalctl -u shadowops-bot.service | grep "❌"
```

**Häufige Fehler:**
- Category not found → ID prüfen
- Missing permissions → Bot neu einladen mit korrekten Permissions
- Channel already exists → Normal, skip

**Dokumentation:**
- MULTI_GUILD_SETUP.md - Allgemeine Multi-Guild Features
- GITHUB_PUSH_NOTIFICATIONS_SETUP.md - Git Integration Details
