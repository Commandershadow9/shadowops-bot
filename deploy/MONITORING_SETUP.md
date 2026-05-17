# ShadowOps Monitoring — Setup-Anleitung

> Externe Watchdogs für ShadowOps-Bot, ZERODOX und GuildScout + monatlicher
> Backup-Restore-Test. Alle Alerts gehen direkt via Discord-Webhook (nicht
> über den shadowops-bot selbst), damit auch ein toter Bot Alarme schlagen kann.

## Architektur

```
                              ┌──────────────────────────┐
                              │ Discord (critical-Channel)│
                              └────────────▲─────────────┘
                                           │ Webhook (POST)
                                           │
       ┌──────────────┬───────────────────┬─┴─────────────────┬──────────────┐
       │              │                   │                   │              │
 ┌─────┴──────┐ ┌────┴────────┐ ┌─────────┴─────┐ ┌───────────┴────┐ ┌──────┴───────┐
 │ shadowops- │ │ zerodox-    │ │ guildscout-   │ │ mayday-sim-    │ │ backup-test  │
 │ watchdog   │ │ watchdog    │ │ watchdog      │ │ watchdog       │ │ monatlich    │
 │ alle 5 min │ │ alle 5 min  │ │ alle 5 min    │ │ alle 5 min     │ │ 1. d. Monats │
 └─────┬──────┘ └────┬────────┘ └────────┬──────┘ └──────────┬─────┘ └──────────────┘
       │ pingt       │ pingt             │ pingt             │ pingt
       ▼             ▼                   ▼                   ▼
 :8766/health    https://...   localhost:8765/health   127.0.0.1:3200/api/health
                /api/health
```

Alle vier Watchdogs nutzen `scripts/service-watchdog.sh` — ein generisches
Script, parametrisiert via Env-Vars (`WATCHDOG_SERVICE_NAME`, `WATCHDOG_HEALTH_URL`).
Das ursprüngliche `scripts/bot-watchdog.sh` bleibt als Backward-Compat-Variante
für den shadowops-bot Watchdog erhalten.

State-Files sind pro Service getrennt (`data/watchdog_state_<service>.json`),
damit Failure-Counter und Alert-Status sich nicht beeinflussen.

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
systemctl --user restart zerodox-watchdog.timer
systemctl --user restart guildscout-watchdog.timer
systemctl --user restart mayday-sim-watchdog.timer
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

### Watchdog-Familie (jeder alle 5 Minuten, gestaffelt)

| Service | Endpoint | Bot-Ready-Check | Boot-Offset |
|---|---|---|---|
| `shadowops-bot` | http://127.0.0.1:8766/health | ja (`bot_ready=true` Pflicht) | 2 min |
| `zerodox` | https://zerodox.de/api/health | nein (HTTP 200 reicht) | 3 min |
| `guildscout` | http://localhost:8765/health | nein (HTTP 200 reicht) | 4 min |
| `mayday-sim` | http://127.0.0.1:3200/api/health | nein (HTTP 200 reicht) | 5 min |

Pro Service:
- **🔴 \<service\> DOWN** — nach 2 konsekutiven Failures (= ~10 Minuten Downtime).
- **✅ \<service\> wieder UP** — sobald der Service nach einem Down-Alert wieder antwortet.
- **Keine Wiederholungs-Alerts** — Stunden-langes Down führt zu EINEM Alert.
- **State pro Service getrennt** — wenn shadowops-bot down ist, beeinflusst das nicht den ZERODOX-Counter.

Die ZERODOX-URL `https://zerodox.de/api/health` läuft über das Internet → testet
DNS-Auflösung + Traefik-Routing + TLS-Zertifikat + App-Health in einem.

### Backup-Restore-Test (1. jedes Monats, 04:50 lokal)

- **🔴 Backup FAILED** — wenn `~/ZERODOX/scripts/backup-test.sh` exit-code != 0
  liefert (mindestens 1 der 10 Test-Stufen ist FAIL). Embed enthält die letzten
  40 Log-Zeilen + Pfad zum vollen Log.
- **Keine Success-Alerts** — monatlich grün ist langweilig. Falls gewünscht:
  `BACKUP_TEST_NOTIFY_ON_SUCCESS=1` in `~/.config/shadowops-watchdog.env`.

## Wartung / Inspektion

```bash
# Alle 5 Timer auf einen Blick
systemctl --user list-timers \
  shadowops-watchdog.timer zerodox-watchdog.timer guildscout-watchdog.timer \
  mayday-sim-watchdog.timer shadowops-backup-test.timer

# Letzten 50 Läufe pro Service
journalctl --user -u shadowops-watchdog.service --no-pager -n 50
journalctl --user -u zerodox-watchdog.service --no-pager -n 50
journalctl --user -u guildscout-watchdog.service --no-pager -n 50
journalctl --user -u mayday-sim-watchdog.service --no-pager -n 50

# State-Files pro Service inspizieren
cat ~/shadowops-bot/data/watchdog_state.json
cat ~/shadowops-bot/data/watchdog_state_zerodox.json
cat ~/shadowops-bot/data/watchdog_state_guildscout.json
cat ~/shadowops-bot/data/watchdog_state_mayday-sim.json

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
