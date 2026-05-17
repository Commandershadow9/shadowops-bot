# ShadowOps Monitoring — Setup-Anleitung

> Externer Watchdog für den Bot + monatlicher Backup-Restore-Test.
> Beide alerten via Discord-Webhook **direkt** (nicht über den Bot selbst),
> damit auch ein toter Bot Alarme schlagen kann.

## Architektur

```
                ┌──────────────────────────┐
                │ Discord (critical-Channel)│
                └────────────▲─────────────┘
                             │ Webhook (POST)
                             │
   ┌─────────────────┐       │       ┌──────────────────────┐
   │ Bot-Watchdog    │───────┘       │ Backup-Restore-Test  │
   │ alle 5 Minuten  │               │ am 1. jedes Monats   │
   │ (systemd-Timer) │               │ (systemd-Timer)      │
   └────────┬────────┘               └──────────────────────┘
            │ pingt
            ▼
   http://127.0.0.1:8766/health
```

## Erst-Einrichtung (einmalig)

### 1. Discord-Webhook erstellen

1. In Discord: **Server-Einstellungen → Integrationen → Webhooks**
2. **"Neuer Webhook"** klicken
3. Name: `ShadowOps Watchdog`, Channel: `critical` (oder ein anderer dedizierter Alert-Channel)
4. **"Webhook-URL kopieren"** — die URL sieht aus wie
   `https://discord.com/api/webhooks/1234.../abcd...`

### 2. Config-Datei anlegen

```bash
# Template kopieren (nicht editieren — die echte Config gehört NICHT ins Repo)
cp ~/shadowops-bot/deploy/shadowops-watchdog.env.example \
   ~/.config/shadowops-watchdog.env

# Webhook-URL eintragen
nano ~/.config/shadowops-watchdog.env
# → SHADOWOPS_WATCHDOG_WEBHOOK=https://discord.com/api/webhooks/...

# Rechte: nur du darfst lesen (enthält Token!)
chmod 600 ~/.config/shadowops-watchdog.env
```

### 3. systemd-Reload

```bash
systemctl --user daemon-reload
systemctl --user restart shadowops-watchdog.timer
```

### 4. Funktionstest

```bash
# Manueller Watchdog-Run (sollte "OK — Bot healthy" loggen)
systemctl --user start shadowops-watchdog.service
journalctl --user -u shadowops-watchdog.service --no-pager -n 5

# Test-Alert auslösen (mit absichtlich falschem Endpoint)
SHADOWOPS_HEALTH_URL="http://127.0.0.1:9999/health" \
  ~/shadowops-bot/scripts/bot-watchdog.sh

# State zurücksetzen
echo '{"last_status":"up","last_alert_at":"","consecutive_failures":0}' \
  > ~/shadowops-bot/data/watchdog_state.json
```

## Was wird wann alertiert?

### Bot-Watchdog (alle 5 Minuten)

- **🔴 Bot DOWN** — nach 2 konsekutiven Failures (= ~10 Minuten Downtime).
  Verhindert false-positives bei Bot-Restart oder kurzem Netzwerk-Hiccup.
- **✅ Bot wieder UP** — sobald der Bot nach einem Down-Alert wieder antwortet.
- **Keine Wiederholungs-Alerts** — wenn der Bot Stunden down ist, kommt nur EIN
  Initial-Alert, kein Spam.

### Backup-Restore-Test (1. jedes Monats, 04:50 lokal)

- **🔴 Backup FAILED** — wenn `~/ZERODOX/scripts/backup-test.sh` exit-code != 0
  liefert (mindestens 1 der 10 Test-Stufen ist FAIL). Embed enthält die letzten
  40 Log-Zeilen + Pfad zum vollen Log.
- **Keine Success-Alerts** — monatlich grün ist langweilig. Falls gewünscht:
  `BACKUP_TEST_NOTIFY_ON_SUCCESS=1` in `~/.config/shadowops-watchdog.env`.

## Wartung / Inspektion

```bash
# Beide Timer auf einen Blick
systemctl --user list-timers shadowops-watchdog.timer shadowops-backup-test.timer

# Letzten 50 Watchdog-Läufe
journalctl --user -u shadowops-watchdog.service --no-pager -n 50

# Backup-Test-Logs (lokal)
ls -la ~/.local/state/shadowops-bot/backup-test/

# Manueller Sofort-Run (Backup-Test — Achtung: dauert ~5-20 Min)
systemctl --user start shadowops-backup-test.service
journalctl --user -u shadowops-backup-test.service -f
```

## Pause/Disable

```bash
# Watchdog kurz aussetzen (z.B. während geplantem Wartungs-Restart)
systemctl --user stop shadowops-watchdog.timer
# … nach der Wartung wieder an:
systemctl --user start shadowops-watchdog.timer

# Permanent disable (nicht empfohlen)
systemctl --user disable --now shadowops-watchdog.timer
```
