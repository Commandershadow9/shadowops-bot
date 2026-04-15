---
title: Multi-Guild Support - Setup Guide
status: active
last_reviewed: 2026-04-15
owner: CommanderShadow9
---

# Multi-Guild Support - Setup Guide
**Date:** 2025-11-25
**Status:** ✅ IMPLEMENTED

## Übersicht

ShadowOps Bot kann jetzt Benachrichtigungen an mehrere Discord-Server senden. Ideal für Kunden-Server!

**Features:**
- ✅ Git-Push Updates an Kunden-Server
- ✅ Status-Benachrichtigungen (offline/online)
- ✅ Fein-granulare Kontrolle pro Projekt
- ✅ Mehrere Server pro Projekt möglich
- ✅ Dein Dev-Server bleibt separat (Übersicht)

## Architektur

### Auf deinem Dev-Server (cmdshadow)
- Getrennte Channels wie bisher
- Technische Details + User-friendly Updates
- Vollständige Übersicht über alle Projekte

### Auf Kunden-Servern
- 1-2 Channels komprimiert
- Nur relevante Projekt-Updates
- User-friendly Patch Notes mit KI
- Status-Benachrichtigungen (optional)

## Setup - Schritt für Schritt

### 1. Bot auf Kunden-Server einladen

**Einladungs-Link generieren:**
```
https://discord.com/api/oauth2/authorize?client_id=YOUR_BOT_ID&permissions=19456&scope=bot
```

**Benötigte Permissions:**
- ✅ Send Messages
- ✅ Embed Links
- ✅ Read Message History (optional - für Deduplizierung)

### 2. Discord IDs ermitteln

**Server ID (Guild ID):**
1. Rechtsklick auf Server-Icon → "Server-ID kopieren"
2. Falls nicht sichtbar: User Settings → Advanced → Developer Mode aktivieren

**Channel ID:**
1. Rechtsklick auf Channel → "Channel-ID kopieren"

### 3. Config anpassen

Füge in `config/config.yaml` unter dem Projekt hinzu:

```yaml
projects:
  guildscout:  # Dein Projekt-Name
    enabled: true
    tag: ⚡ [GUILDSCOUT]
    # ... bestehende Config ...

    # NEU: Externe Benachrichtigungen
    external_notifications:
      - guild_id: 1234567890123456789      # Kunden Discord Server ID
        channel_id: 9876543210987654321    # Channel für Updates & Status
        enabled: true
        notify_on:
          git_push: true      # Git Updates senden?
          offline: true       # Offline-Meldungen senden?
          online: true        # Online-Meldungen senden?
          errors: true        # Error-Meldungen senden?

      # Optional: Weitere Server
      - guild_id: 1111111111111111111
        channel_id: 2222222222222222222
        enabled: true
        notify_on:
          git_push: true
          offline: false      # Keine Status-Meldungen
          online: false
          errors: false
```

### 4. Bot neu starten

```bash
sudo systemctl restart shadowops-bot.service
```

### 5. Test durchführen

**Git-Push Test:**
```bash
# Mache einen kleinen Commit im Projekt
cd /home/cmdshadow/GuildScout
echo "test" >> README.md
git add README.md
git commit -m "test: Multi-guild notification test"
git push
```

**Status-Test:**
Stoppe das Projekt kurz:
```bash
sudo systemctl stop guildscout.service
# Warte 5 Minuten (check_interval)
sudo systemctl start guildscout.service
```

## Config-Optionen erklärt

### notify_on

| Option | Beschreibung | Empfehlung |
|--------|-------------|------------|
| `git_push` | GitHub Push Updates | ✅ Immer aktivieren |
| `offline` | Service offline Meldungen | ✅ Für Production |
| `online` | Service online Meldungen | ⚠️ Optional (kann spammy sein) |
| `errors` | Error-Meldungen | ✅ Für Production |

### Beispiel-Konfigurationen

**Minimalistisch (nur Updates):**
```yaml
external_notifications:
  - guild_id: 1234567890
    channel_id: 9876543210
    enabled: true
    notify_on:
      git_push: true
      offline: false
      online: false
      errors: false
```

**Vollständig (Updates + Monitoring):**
```yaml
external_notifications:
  - guild_id: 1234567890
    channel_id: 9876543210  # Ein Channel für alles
    enabled: true
    notify_on:
      git_push: true
      offline: true
      online: true
      errors: true
```

**Getrennte Channels:**
```yaml
external_notifications:
  - guild_id: 1234567890
    channel_id: 1111111111  # #updates Channel
    enabled: true
    notify_on:
      git_push: true
      offline: false
      online: false
      errors: false

  - guild_id: 1234567890
    channel_id: 2222222222  # #status Channel
    enabled: true
    notify_on:
      git_push: false
      offline: true
      online: true
      errors: true
```

## Was wird gesendet?

### Git-Push Updates (git_push: true)

**Format:**
```
✨ Updates für GuildScout

🆕 New Features:
• Dark mode implemented for better UX
• User dashboard now shows activity stats

🐛 Bug Fixes:
• Login issues on mobile resolved
• Database timeouts fixed

3 commit(s) by YourName
```

**Sprache:** Konfigurierbar pro Projekt (`patch_notes.language: en/de`)
**KI-generiert:** Ja, wenn `patch_notes.use_ai: true`

### Status Updates (offline: true)

**Offline-Meldung:**
```
🔴 GuildScout is DOWN

Project: GuildScout
Status: 🔴 Offline
Consecutive Failures: 3
Error: Connection timeout after 10s
```

**Online-Meldung:**
```
✅ GuildScout is BACK ONLINE

Project: GuildScout
Status: 🟢 Online
Response Time: 45ms
Current Uptime: 99.87%
```

## Deduplizierung

**Problem:** GuildScout Bot könnte selbst "I'm offline" melden, während ShadowOps auch monitort.

**Lösung (geplant):**
- Option 1: GuildScout deaktiviert eigene Status-Meldungen
- Option 2: ShadowOps prüft letzte Nachrichten vor dem Senden

**Aktuell:** Beide Bots senden unabhängig (keine Duplikate, da unterschiedliche Formate)

## Troubleshooting

### Bot sendet nicht an externen Server

**Prüfe:**
1. Bot ist auf dem Kunden-Server eingeladen?
   ```bash
   # Logs prüfen
   sudo journalctl -u shadowops-bot.service | grep "External channel.*not found"
   ```

2. Channel ID korrekt?
   - Rechtsklick auf Channel → "Channel-ID kopieren"
   - In config.yaml eintragen

3. Bot hat Rechte in dem Channel?
   - Send Messages ✅
   - Embed Links ✅

### "External channel not found"

**Ursache:** Bot kann Channel nicht sehen (keine Rechte oder falsche ID)

**Fix:**
```bash
# Logs prüfen welche Channel ID er sucht
sudo journalctl -u shadowops-bot.service --since "1 hour ago" | grep "External channel"

# Bot-Rechte prüfen:
# 1. Server Settings → Roles → @ShadowOps Bot
# 2. Prüfe "View Channels" ist aktiviert
# 3. Channel-spezifische Permissions prüfen
```

### Notifications kommen nicht an

**Debug:**
```bash
# Logs in Echtzeit
sudo journalctl -u shadowops-bot.service -f | grep "external"

# Test: Manueller Git Push
cd /home/cmdshadow/GuildScout
git commit --allow-empty -m "test: External notification test"
git push

# Logs prüfen
sudo journalctl -u shadowops-bot.service --since "2 minutes ago" | grep -E "📤|external|git_push"
```

## Best Practices

### 1. Separate Channels auf Kunden-Server

**Empfohlen:**
- `#project-updates` - Git Updates (user-friendly)
- `#project-status` - Monitoring (online/offline)

**Oder minimal:**
- `#guildscout` - Alles in einem Channel

### 2. Sprache pro Projekt

```yaml
projects:
  guildscout:
    patch_notes:
      language: en  # Kunde spricht Englisch
      use_ai: true

  nexus:
    patch_notes:
      language: de  # Deutscher Kunde
      use_ai: true
```

### 3. Monitoring sinnvoll konfigurieren

**Production Server:** `offline: true`, `online: false`
- Nur bei Problemen benachrichtigen
- Nicht bei jedem Health-Check Erfolg

**Dev/Staging:** `offline: true`, `online: true`
- Vollständige Transparenz
- Kunde sieht alle Status-Wechsel

### 4. Git-Updates immer aktivieren

```yaml
notify_on:
  git_push: true  # ← Immer empfohlen!
```

**Warum?**
- Kunde sieht neue Features sofort
- Transparenz über Entwicklung
- Professionelle Patch Notes mit KI

## Sicherheit

### Bot-Token

**Wichtig:** Bot-Token ist in `config.yaml` gespeichert
- ❌ Nicht committen (`.gitignore`)
- ✅ Nur auf Server speichern
- ✅ Regelmäßig rotieren

### Channel-Zugriff

**Bot sollte nur Zugriff auf:**
- Configured channels (nicht alle Server-Channels)
- Keine Admin-Rechte vergeben

### Webhook Alternative

Falls du dem Bot keinen direkten Zugriff geben möchtest:
- Discord Webhooks erstellen
- In Config eintragen: `webhook_url: "https://..."`
- ⚠️ Aktuell nicht implementiert (geplant)

## Logs interpretieren

**Erfolgreiche Benachrichtigung:**
```
📤 Sent git update for guildscout to external server
📤 Sent offline notification for guildscout to external server
```

**Fehler:**
```
⚠️ External channel 123456 not found for guildscout
❌ Failed to send external git notification for guildscout: Forbidden
```

## Nächste Schritte

1. ✅ Bot auf Kunden-Server einladen
2. ✅ Channel erstellen & IDs kopieren
3. ✅ Config anpassen
4. ✅ Bot neu starten
5. ✅ Test durchführen (Git Push)
6. ✅ Monitoring beobachten

**Bei Fragen:** Logs prüfen mit `sudo journalctl -u shadowops-bot.service -f`
