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
- **Restart:** `always` (auch nach exit(0) — verhindert stillen Tod nach Lock-Conflict)
- **RestartSec:** 10s
- **Burst-Schutz:** max 5 Neustarts in 300s (StartLimitBurst=5)

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
| `bot-watchdog.sh` | Watchdog fuer shadowops-bot (Backward-Compat-Wrapper, seit 2026-05-17) |
| `service-watchdog.sh` | Generischer Watchdog (HTTP- oder systemd-Mode), parametrisiert via Env-Vars |
| `build-drift-watchdog.sh` | Build-Drift-Detection: /api/build-id vs. origin/main HEAD-commit-date (seit #416) |
| `backup-restore-test.sh` | Wrapper um `~/ZERODOX/scripts/backup-test.sh` mit Discord-Alert |

## Watchdog-Familie (seit 2026-05-17 — Defense-in-Depth gegen shadowops-bot-Down)

7 user-systemd Watchdogs pruefen ihren Service und alerten bei Down/Recovery direkt via Discord-Webhook. **Webhook-URL:** in `~/.config/shadowops-watchdog.env` (chmod 600). **Setup-Anleitung:** `deploy/MONITORING_SETUP.md`.

| Timer | Mode | Endpoint/Units | Boot-Offset |
|-------|------|----------------|-------------|
| `shadowops-watchdog.timer` | http | http://127.0.0.1:8766/health (bot_ready=true Pflicht) | 2 min |
| `zerodox-watchdog.timer` | http | https://zerodox.de/api/health (testet via Internet DNS+Traefik+TLS+App) | 3 min |
| `guildscout-watchdog.timer` | http | http://localhost:8765/health | 4 min |
| `mayday-sim-watchdog.timer` | http | http://127.0.0.1:3200/api/health | 5 min |
| `ai-agent-framework-watchdog.timer` | systemd | guildscout-feedback-agent, zerodox-support-agent, seo-agent | 6 min |
| `cmdshadow-design-watchdog.timer` | systemd-result | cmdshadow-design-healthcheck.service (max_age=36h) | 8 min |
| `mayday-sim-build-drift-watchdog.timer` | build-drift | /api/build-id vs. origin/main HEAD-commit-date (gh CLI) | 15 min |
| `shadowops-backup-test.timer` | — | monatlich 1. d. Monats 04:50, Wrapper um ZERODOX backup-test.sh | OnCalendar |

**State-Files pro Service:** `data/watchdog_state_<service>.json` (gitignored). Alert nach 2 konsekutiven Failures (~10 Min), Recovery-Alert wenn vorher down war. State-Files getrennt damit Counter sich nicht beeinflussen.

**Service-Files in `deploy/`:** Quelldateien fuer die Watchdogs. User-systemd-Symlinks in `~/.config/systemd/user/`. Git-Updates auf `deploy/*.service|*.timer` wirken sofort (Symlink), `daemon-reload` nur bei Schema-Aenderungen noetig.
