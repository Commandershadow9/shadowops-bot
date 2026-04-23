---
title: 🚀 ShadowOps Bot - Quick Start Guide
status: active
last_reviewed: 2026-04-15
owner: CommanderShadow9
---

# 🚀 ShadowOps Bot - Quick Start Guide

Schnellstart-Anleitung um den Bot in wenigen Minuten zum Laufen zu bringen.

## ⚡ Schnellinstallation (5 Minuten)

### Schritt 1: Discord Bot erstellen (2min)

1. Gehe zu https://discord.com/developers/applications
2. Klicke "New Application"
3. Name: `ShadowOps` → Create
4. **Bot Tab** → "Add Bot" → "Yes, do it!"
5. **Reset Token** → Token kopieren (⚠️ NUR EINMAL sichtbar!)
6. Unter **Privileged Gateway Intents** aktivieren:
   - ✅ Message Content Intent
   - ✅ Server Members Intent
7. **Speichern**!

### Schritt 2: Bot einladen (1min)

1. Im Developer Portal: **OAuth2** → **URL Generator**
2. **Scopes** auswählen:
   - ✅ `bot`
   - ✅ `applications.commands`
3. **Bot Permissions** auswählen:
   - ✅ Send Messages
   - ✅ Embed Links
   - ✅ Use Slash Commands
   - ✅ Read Message History
4. Generierte URL kopieren → in Browser öffnen
5. Server auswählen → Bot einladen

### Schritt 3: Channel IDs finden (1min)

1. Discord → **Benutzereinstellungen** → **Erweitert**
2. **Entwicklermodus** aktivieren ✅
3. Rechtsklick auf deinen Security-Channel → **ID kopieren**
4. Rechtsklick auf deinen Server-Namen → **ID kopieren** (Guild ID)

### Schritt 4: Bot installieren (1min)

```bash
cd /home/cmdshadow/shadowops-bot

# Setup-Script ausführen
./setup.sh

# Config bearbeiten
nano config/config.yaml
```

**Füge ein:**
- Bot Token (von Schritt 1)
- Guild ID (deine Server-ID)
- Channel IDs (Security-Alerts Channel)

**Speichern:** `CTRL+O`, `ENTER`, `CTRL+X`

### Schritt 5: Bot starten

```bash
# Service starten
sudo systemctl start shadowops-bot

# Status prüfen
sudo systemctl status shadowops-bot

# Live-Logs anschauen
sudo journalctl -u shadowops-bot -f
```

### Schritt 6: Testen

In Discord, tippe:
```
/status
```

✅ **Fertig!** Der Bot ist jetzt aktiv und monitort deine Security-Tools!

---

## 📝 Minimale Config (Copy & Paste)

```yaml
discord:
  token: "DEIN_BOT_TOKEN_HIER"
  guild_id: 123456789012345678

channels:
  security_alerts: 987654321012345678

projects:
  sicherheitsdienst:
    enabled: true
    tag: "🛡️ [SECURITY]"
    color: 0xE74C3C

  server:
    enabled: true
    tag: "🖥️ [SERVER]"
    color: 0x95A5A6

alerts:
  min_severity: "HIGH"
  rate_limit_seconds: 60

permissions:
  admins:
    - 111111111111111111  # Deine Discord User ID
```

**Deine User ID finden:**
1. Discord → Entwicklermodus aktiviert
2. Rechtsklick auf dein Profil → ID kopieren

---

## 🔍 Troubleshooting

### Bot startet nicht

```bash
# Fehler-Logs anschauen
sudo journalctl -u shadowops-bot -n 50

# Config validieren
python3 src/bot.py
```

### "Config-Datei nicht gefunden"

```bash
cd /home/cmdshadow/shadowops-bot
ls -la config/

# Config erstellen falls fehlt
cp config/config.example.yaml config/config.yaml
nano config/config.yaml
```

### "Permission Denied" bei Security-Tools

Bot braucht Sudo-Zugriff für:
- `fail2ban-client`
- `cscli` (CrowdSec)
- `systemctl` (Status-Checks)

**Sudo ohne Passwort für cmdshadow:**

```bash
sudo visudo
```

Füge hinzu:
```
cmdshadow ALL=(ALL) NOPASSWD: /usr/bin/fail2ban-client, /usr/bin/cscli, /usr/bin/systemctl, /usr/bin/aide
```

### Commands erscheinen nicht in Discord

1. Warte 1-2 Minuten (Sync dauert)
2. Prüfe Guild ID in config.yaml
3. Lade Discord neu (`CTRL+R`)
4. Bot neu starten: `sudo systemctl restart shadowops-bot`

---

## 🎮 Available Commands

| Command | Beschreibung | Permission |
|---------|-------------|------------|
| `/status` | Security-Status-Übersicht | Alle |
| `/scan` | Manueller Docker-Scan | Admin |
| `/bans` | Gebannte IPs anzeigen | Alle |
| `/threats` | Letzte Bedrohungen | Alle |
| `/docker` | Docker Scan Ergebnisse | Alle |
| `/aide` | AIDE Check Status | Alle |

---

## ✅ Checklist

- [ ] Discord Bot erstellt
- [ ] Bot-Token kopiert
- [ ] Bot zu Server eingeladen
- [ ] Channel IDs kopiert
- [ ] config.yaml erstellt und ausgefüllt
- [ ] Dependencies installiert (`pip3 install -r requirements.txt`)
- [ ] Service gestartet (`sudo systemctl start shadowops-bot`)
- [ ] `/status` funktioniert in Discord

**Alles ✅? Perfekt! 🎉**

---

## 📚 Weiterführende Docs

- [Vollständiges README](README.md)
- [Config-Dokumentation](config/config.example.yaml)
- [Discord.py Docs](https://discordpy.readthedocs.io/)

**Bei Problemen:** Logs prüfen mit `sudo journalctl -u shadowops-bot -f`
