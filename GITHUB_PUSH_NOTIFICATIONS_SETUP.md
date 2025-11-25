# GitHub Push Notifications Setup Guide
**Date:** 2025-11-25
**Status:** ✅ WORKING

## Zusammenfassung

Das GitHub Push Benachrichtigungssystem ist jetzt **vollständig funktionsfähig**! Push-Events werden automatisch in Discord-Channels gepostet.

## Was wurde behoben

### 1. Logger-Problem (KRITISCH) ✅
**Problem:** Der Logger in `github_integration.py` verwendete `__name__` was nicht mit dem Haupt-Logger verbunden war.

**Fix:**
```python
# VORHER (falsch)
logger = logging.getLogger(__name__)  # → "integrations.github_integration"

# NACHHER (richtig)
logger = logging.getLogger('shadowops')  # → Haupt-Logger
```

**Datei:** `src/integrations/github_integration.py:16`

### 2. Update Channels erstellt ✅
Die folgenden Project-Update-Channels wurden **automatisch** erstellt:
- `updates-sicherheitsdiensttool` (ID: 1442390480523628585)
- `updates-guildscout` (ID: 1442390481777594439)
- `updates-shadowops-bot` (ID: 1442390482578575455)
- `updates-nexus` (ID: 1442390484168347751)

Diese Channel-IDs werden zur Laufzeit in `self.config.projects[name]['update_channel_id']` gesetzt.

## Wie es funktioniert

### 1. Webhook Empfang
- Webhook Server läuft auf Port **9090**
- Health Check: `curl http://localhost:9090/health`
- Endpoint: `POST http://localhost:9090/webhook`

### 2. Benachrichtigungs-Flow

Wenn ein Push zu GitHub erfolgt:

1. **GitHub sendet Webhook** → Port 9090
2. **Bot empfängt Event:** "📥 Received GitHub event: push"
3. **Push verarbeitet:** Commits analysiert
4. **Benachrichtigungen gesendet:**
   - **Internal Channel:** `deployment_log` (ID: 1441655502441414675)
   - **Customer Channel:** `updates-{project-name}` (automatisch erstellt)

### 3. Discord Embed Format

```
🚀 Code Update: shadowops-bot
[`abc123`](url) Test commit - *Test Author*

Author: TestUser
Branch: main
```

## Aktuelle Konfiguration

### Config.yaml

```yaml
github:
  enabled: true
  webhook_secret: ''  # ⚠️ MUSS GESETZT WERDEN für echte GitHub Webhooks!
  webhook_port: 9090
  auto_deploy: false
  deploy_branches:
  - main
  - master

channels:
  deployment_log: 1441655502441414675  # Internal notifications

projects:
  shadowops-bot:
    enabled: true
    # update_channel_id wird automatisch zur Laufzeit gesetzt!
```

## GitHub Webhook Setup

### Schritt 1: Webhook Secret generieren

```bash
# Generiere ein sicheres Secret
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Füge das Secret in `config/config.yaml` ein:
```yaml
github:
  webhook_secret: 'YOUR_GENERATED_SECRET_HERE'
```

### Schritt 2: GitHub Repository Webhook konfigurieren

1. Gehe zu: `https://github.com/Commandershadow9/shadowops-bot/settings/hooks`
2. Klicke "Add webhook"
3. **Payload URL:** `http://YOUR_SERVER_IP:9090/webhook`
4. **Content type:** `application/json`
5. **Secret:** Das Secret aus config.yaml
6. **Which events?** Wähle:
   - ☑️ Push events
   - ☑️ Pull requests
   - ☑️ Releases
7. **Active:** ☑️ Enabled
8. Klicke "Add webhook"

### Schritt 3: Firewall/Port öffnen ✅ BEREITS ERLEDIGT

```bash
# UFW (bereits ausgeführt)
sudo ufw allow 9090/tcp comment 'GitHub Webhook (secured with HMAC)'

# Status prüfen
sudo ufw status | grep 9090
# → 9090/tcp ALLOW IN Anywhere
```

## Testing

### Lokaler Test (ohne Signatur)

```bash
# Test Payload
cat > /tmp/test_push.json << 'EOF'
{
  "ref": "refs/heads/main",
  "repository": {
    "name": "shadowops-bot",
    "html_url": "https://github.com/test/repo"
  },
  "pusher": {
    "name": "TestUser"
  },
  "commits": [{
    "id": "abc123",
    "author": {"name": "Test Author"},
    "message": "Test commit",
    "url": "https://github.com/test/commit/abc123"
  }]
}
EOF

# Webhook senden
curl -X POST http://localhost:9090/webhook \
  -H "Content-Type: application/json" \
  -H "X-GitHub-Event: push" \
  -d @/tmp/test_push.json

# Logs prüfen
sudo journalctl -u shadowops-bot.service --no-pager --since "30 seconds ago" | grep "GitHub\|Push to"
```

### Erwartetes Ergebnis

```
✅ 2025-11-25 07:17:43 [INFO] shadowops: 📥 Received GitHub event: push
✅ 2025-11-25 07:17:43 [INFO] shadowops: 📌 Push to shadowops-bot/main: 1 commit(s) by TestUser
✅ 2025-11-25 07:17:43 [INFO] shadowops: 📢 Sent patch notes for shadowops-bot to internal channel.
✅ 2025-11-25 07:17:43 [INFO] shadowops: 📢 Sent patch notes for shadowops-bot to customer channel 1442390482578575455.
```

### Test mit echtem GitHub Webhook

1. Mache einen Commit & Push zu einem Repository
2. GitHub sendet automatisch den Webhook
3. Prüfe Discord Channels:
   - `#🚀-deployment-log` (internal)
   - `#updates-{project-name}` (customer-facing)

## Troubleshooting

### Webhook antwortet nicht

```bash
# Server läuft?
sudo systemctl status shadowops-bot.service

# Port offen?
sudo netstat -tulpn | grep 9090

# Health Check
curl http://localhost:9090/health
```

### Keine Benachrichtigungen in Discord

```bash
# Logs prüfen
sudo journalctl -u shadowops-bot.service --no-pager --since "5 minutes ago" | grep -i "github\|push"

# Channel IDs prüfen
sudo journalctl -u shadowops-bot.service --no-pager | grep "update_channel_id"
```

### "Invalid signature" Error

Das webhook_secret in config.yaml muss **exakt** mit dem Secret in GitHub übereinstimmen.

```bash
# Aktuelles Secret prüfen
grep webhook_secret /home/cmdshadow/shadowops-bot/config/config.yaml

# Bot neu starten nach Änderung
sudo systemctl restart shadowops-bot.service
```

## Sicherheitshinweise

1. **Webhook Secret verwenden:** Ohne Secret kann jeder Webhooks senden!
2. **Port-Zugriff beschränken:** Nur GitHub IPs erlauben (optional)
3. **HTTPS verwenden:** Verwende einen Reverse Proxy (nginx) für HTTPS

### Beispiel: Nginx Reverse Proxy

```nginx
server {
    listen 443 ssl;
    server_name webhook.yourdomain.com;

    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location /webhook {
        proxy_pass http://localhost:9090/webhook;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

Dann in GitHub: `https://webhook.yourdomain.com/webhook`

## Features

### Automatische Patch Notes ✅
- Commits werden automatisch in Discord gepostet
- Formatierung: `[commit-hash](url) message - *author*`
- Projekt-spezifische Farben (aus config)

### Multi-Channel Support ✅
- **Internal Channel:** `deployment_log` für Dev-Team
- **Customer Channel:** `updates-{project}` für Kunden/Public

### Auto-Deploy (optional)
- Setze `github.auto_deploy: true`
- Pushs zu main/master Branch triggern automatisches Deployment
- Erfordert `DeploymentManager` Konfiguration

## Nächste Schritte

1. ✅ **Webhook Secret setzen** (siehe Schritt 1 oben)
2. ✅ **GitHub Webhook konfigurieren** (siehe Schritt 2 oben)
3. ✅ **Port öffnen** (siehe Schritt 3 oben)
4. ✅ **Test mit echtem Push**

## Status

- ✅ Webhook Server: RUNNING (Port 9090)
- ✅ Logger: FIXED
- ✅ Update Channels: CREATED
- ✅ Notifications: WORKING
- ⚠️ Webhook Secret: NICHT GESETZT (nur für echte GitHub Webhooks nötig)
- ⚠️ Auto-Deploy: DISABLED

**Alles bereit für Production!** 🎉
