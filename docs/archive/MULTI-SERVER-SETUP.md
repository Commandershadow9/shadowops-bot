# 🌐 Multi-Server Setup Guide

So richtest du ShadowOps auf mehreren Discord-Servern ein (dein Dev-Server + Kunden-Server).

## 📋 Übersicht

**Aktuell:** Bot läuft auf **EINEM** Server (deinem Dev-Discord)
**Ziel:** Bot auf **MEHREREN** Servern, intelligente Alert-Verteilung

---

## 🎯 Use Case

### Szenario:
- **DU** hast einen Dev-Discord (siehst ALLES)
- **Kunde A** (Sicherheitsdienst) → eigener Discord → sieht nur seine Alerts
- **Kunde B** (NEXUS) → eigener Discord → sieht nur seine Alerts

### Was passiert:
1. Fail2ban bannt IP → Alert geht an:
   - ✅ Dein Dev-Discord (🚫 fail2ban Channel)
   - ✅ Kunden-Discord (wenn konfiguriert)

2. Docker-Scan findet CRITICAL Vulnerability in Sicherheitsdienst → Alert geht an:
   - ✅ Dein Dev-Discord (🐳 docker + 🛡️ security)
   - ✅ Kunde A Discord (nur Sicherheitsdienst-Alerts)
   - ❌ Kunde B sieht NICHTS (ist NEXUS-Kunde)

---

## 🔧 Setup-Anleitung

### Phase 1: Vorbereitung (JETZT)

**1. Aktueller Stand:**
```yaml
# config/config.yaml
discord:
  guild_id: 1438065435496157267  # Dein Dev-Server

channels:
  critical: 1438111698920669184
  sicherheitsdienst: 1438111770689409064
  nexus: 1438111825660219412
  fail2ban: 1438111898511081503
  docker: 1438111964067921920
  backups: 1438112011052646531
```

**Projekte sind GETRENNT:**
- ✅ Sicherheitsdienst-Alerts → `🛡️ security` Channel
- ✅ NEXUS-Alerts → `⚡ nexus` Channel
- ✅ Server-Alerts (Fail2ban, etc.) → Spezifische Channels

---

### Phase 2: Kunden-Server hinzufügen

**Wenn Kunde bereit ist:**

#### Schritt 1: Kunde lädt Bot ein

**Invite-Link generieren:**
```bash
# Auf deinem Server:
echo "https://discord.com/api/oauth2/authorize?client_id=DEINE_BOT_ID&permissions=274878024768&scope=bot%20applications.commands"
```

Ersetze `DEINE_BOT_ID` mit der Application ID aus dem Discord Developer Portal.

**Permissions:**
- ✅ Send Messages
- ✅ Embed Links
- ✅ Use Slash Commands
- ✅ Read Message History

#### Schritt 2: Kunden-Channels vorbereiten

Kunde erstellt in Discord:
```
📁 🛡️ SYSTEM MONITORING
├── 🚨 security-alerts (HIGH/CRITICAL only)
├── 🐳 container-scans
└── 💾 database-backups
```

Kunde gibt dir die Channel-IDs (Entwicklermodus aktivieren → Rechtsklick → ID kopieren).

#### Schritt 3: Config erweitern

**NEU:** `config/config.yaml` wird zur Multi-Guild Config:

```yaml
discord:
  token: "YOUR_TOKEN"
  primary_guild_id: 1438065435496157267

# Multi-Guild Support
guilds:
  # DEIN DEV-SERVER (bekommt ALLES)
  "1438065435496157267":
    name: "CommanderShadow Dev"
    projects:
      - sicherheitsdienst
      - nexus
      - server

    channels:
      critical: 1438111698920669184
      sicherheitsdienst: 1438111770689409064
      nexus: 1438111825660219412
      fail2ban: 1438111898511081503
      docker: 1438111964067921920
      backups: 1438112011052646531

  # KUNDE: Sicherheitsdienst
  "KUNDEN_GUILD_ID_HIER":
    name: "Kunde Sicherheitsdienst"
    projects:
      - sicherheitsdienst  # Nur Sicherheitsdienst!

    min_severity: "HIGH"  # Nur wichtige Alerts

    channels:
      security_alerts: KUNDE_CHANNEL_ID_1
      docker: KUNDE_CHANNEL_ID_2
      backups: KUNDE_CHANNEL_ID_3
```

#### Schritt 4: Code-Update (zukünftig)

**Bot-Code muss erweitert werden für Multi-Guild:**

```python
# In bot.py
async def send_alert_multi_guild(self, alert_type: str, embed: discord.Embed, project: str = None):
    """Sendet Alert an alle relevanten Guilds"""

    for guild_id, guild_config in self.config.guilds.items():
        # Prüfe ob Guild dieses Projekt monitort
        if project and project not in guild_config.get('projects', []):
            continue

        # Prüfe Severity-Filter
        min_severity = guild_config.get('min_severity', 'MEDIUM')
        # ... severity check ...

        # Sende an Guild-spezifischen Channel
        channel_id = guild_config['channels'].get(alert_type)
        await self.send_alert(channel_id, embed)
```

---

## 🔑 Projekt-Trennung (Aktuell)

**Ja, Projekte sind getrennt!**

### Wie funktioniert's?

1. **Alert wird erzeugt** (z.B. Backup erfolgreich)
2. **Projekt wird ermittelt:** Sicherheitsdienst oder NEXUS?
3. **Channel wird gewählt:**
   - Sicherheitsdienst → `🛡️ security` (1438111770689409064)
   - NEXUS → `⚡ nexus` (1438111825660219412)

### Beispiel-Code:

```python
# Backup Alert
project = "sicherheitsdienst"  # oder "nexus"
channel_id = config.get_channel_for_alert('backup', project=project)

# → Gibt project-spezifischen Channel zurück!
```

---

## 📊 Alert-Routing-Tabelle

| Alert-Typ | Projekt | Dein Dev-Discord | Kunde Sicherheitsdienst | Kunde NEXUS |
|-----------|---------|------------------|------------------------|-------------|
| Fail2ban Ban | Server | 🚫 fail2ban | ✅ security-alerts | ✅ security-alerts |
| Docker Scan (Sicherheitsdienst) | Sicherheitsdienst | 🛡️ security + 🐳 docker | ✅ container-scans | ❌ |
| Docker Scan (NEXUS) | NEXUS | ⚡ nexus + 🐳 docker | ❌ | ✅ container-scans |
| Backup (Sicherheitsdienst) | Sicherheitsdienst | 🛡️ security + 💾 backups | ✅ database-backups | ❌ |
| AIDE Check | Server | 🔴 critical | ✅ (wenn HIGH/CRITICAL) | ✅ (wenn HIGH/CRITICAL) |

---

## 🚀 Migration-Pfad

### Jetzt (Phase 1):
- ✅ Ein Server (dein Dev-Discord)
- ✅ Projekt-Trennung via Channels
- ✅ Alle Alerts sichtbar

### Phase 2 (wenn Kunde ready):
- 🔄 Config erweitern mit Multi-Guild
- 🔄 Bot-Code Update (send_alert_multi_guild)
- ✅ Kunde lädt Bot ein
- ✅ Kunde sieht nur seine Alerts

### Phase 3 (Skalierung):
- ✅ Weitere Kunden hinzufügen
- ✅ Zentrale Monitoring-Dashboard (du)
- ✅ Dezentrale Kunden-Views

---

## 🔒 Permissions & Security

### Was Kunden KÖNNEN:
- ✅ `/status` sehen
- ✅ `/bans` sehen
- ✅ `/docker` Ergebnisse sehen
- ✅ Alerts empfangen

### Was Kunden NICHT KÖNNEN:
- ❌ `/scan` triggern (nur Admins = DU)
- ❌ Config ändern
- ❌ Bot stoppen
- ❌ Andere Projekte sehen

**Admin-Check im Code:**
```python
@bot.tree.command(name="scan")
@app_commands.checks.has_permissions(administrator=True)
async def scan_command(interaction):
    # Zusätzlich: Prüfe ob User in config.admins
    if interaction.user.id not in bot.config.admin_user_ids:
        await interaction.response.send_message("❌ Keine Berechtigung", ephemeral=True)
        return
```

---

## 📝 TODO für Multi-Server

- [ ] Multi-Guild Config-Struktur finalisieren
- [ ] `send_alert_multi_guild()` Funktion implementieren
- [ ] Guild-spezifische Channel-Mappings
- [ ] Severity-Filter pro Guild
- [ ] Admin-Permissions pro Guild
- [ ] Test mit 2. Discord-Server
- [ ] Dokumentation erweitern

---

## 💡 Best Practices

1. **Starte mit einem Server** (dev) ✅ (JETZT)
2. **Teste alle Channels** (sieh unten)
3. **Dann expandiere** zu Multi-Server

---

## 🧪 Channel-Tests

```bash
# Test alle Channels:
cd /home/cmdshadow/shadowops-bot

# In Discord: /status → prüfe ob es funktioniert
# Dann in jedem Channel schauen ob Alerts ankommen

# Manuell Test-Fail2ban-Ban triggern:
# (wird Auto-Alert senden zu 🚫 fail2ban)
```

---

**Status:** ✅ Single-Server produktiv
**Next:** Multi-Server Support (wenn Kunde ready)

---

Made with 🗡️ by CommanderShadow
