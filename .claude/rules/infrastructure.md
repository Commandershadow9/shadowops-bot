---
paths:
  - "scripts/**"
  - "deploy/**"
  - "config/logrotate.conf"
---
# Infrastructure

## systemd Service
- **Unit:** `/etc/systemd/system/shadowops-bot.service` (system-level, NICHT user)
- **Quelldatei:** `deploy/shadowops-bot.service`
- **Restart:** `on-abnormal` (nur bei Signal-Tod, NICHT bei exit(1))
- **RestartSec:** 30s
- **Burst-Schutz:** max 3 Neustarts in 300s
- **SuccessExitStatus:** 143 SIGTERM (sauberer Shutdown = kein Restart)

## Deploy-Prozess
1. `scripts/restart.sh --pull` (git pull + systemd restart)
2. Wartet bis Service active (max 15s)
3. Zeigt Status-Zusammenfassung
4. Optional: `--logs` fuer Live-Logs

## Logrotate
- **Timer:** `shadowops-logrotate.timer` (user-level systemd)
- **Trigger:** Taeglich um Mitternacht + 02:15
- **Post-Rotate:** Sendet SIGUSR1 an Bot (Log-Dateien neu oeffnen)
- **Retention:** 30 Tage, delayed compression
- **Config:** `config/logrotate.conf`

## Ports
| Port | Service | Zweck |
|------|---------|-------|
| 8766 | Health Check | systemd/Uptime Monitoring |
| 9090 | GitHub Webhook | Push/PR Events empfangen |
| 9091 | GuildScout Alerts | Alert Forwarding |

## Scripts (`scripts/`)
| Script | Zweck |
|--------|-------|
| `restart.sh` | Bot neustarten (--pull, --logs) |
| `diagnose-bot.sh` | Diagnose: Status, Ports, Logs, Konflikte |
| `setup.sh` | Erstinstallation (venv, Dependencies, Config) |
| `update-config.sh` | Config-Migration bei Updates |
| `get_bot_invite.py` | Discord Bot Invite-URL generieren |
