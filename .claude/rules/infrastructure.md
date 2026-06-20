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
| `bot-watchdog.sh` | Watchdog fuer shadowops-bot (Backward-Compat-Wrapper, seit 2026-05-17) |
| `service-watchdog.sh` | Generischer Watchdog (HTTP- oder systemd-Mode), parametrisiert via Env-Vars |
| `backup-restore-test.sh` | Wrapper um `~/ZERODOX/scripts/backup-test.sh` mit Discord-Alert |
| `lib/discord-send.sh` | Geteilte `discord_post()`-Funktion mit 429-Resilienz (Jitter + Retry-After, #293). Gesourct von service-/disk-hygiene-/doku-drift-/memory-watchdog (Fallback-Guard hält altes Inline-Curl). `bot-watchdog.sh` bleibt unberührt (Backward-Compat). |

## Watchdog-Familie (seit 2026-05-17 — Defense-in-Depth gegen shadowops-bot-Down)

5 user-systemd Watchdogs pruefen alle 5 Minuten ihren Service und alerten bei Down/Recovery direkt via Discord-Webhook. **Webhook-URL:** in `~/.config/shadowops-watchdog.env` (chmod 600). **Setup-Anleitung:** `deploy/MONITORING_SETUP.md`.

| Timer | Mode | Endpoint/Units | Boot-Offset |
|-------|------|----------------|-------------|
| `shadowops-watchdog.timer` | http | http://127.0.0.1:8766/health (bot_ready=true Pflicht) | 2 min |
| `zerodox-watchdog.timer` | http | https://zerodox.de/api/health (testet via Internet DNS+Traefik+TLS+App) | 3 min |
| `guildscout-watchdog.timer` | http | http://localhost:8765/health | 4 min |
| `mayday-sim-watchdog.timer` | http | http://127.0.0.1:3200/api/health | 5 min |
| `mayday-scheduler-watchdog.timer` | container | leitstelle-scheduler (Docker-Health, Tick-Owner SB3) | 7 min |
| `ai-agent-framework-watchdog.timer` | systemd | guildscout-feedback-agent, zerodox-support-agent, seo-agent | 6 min |
| `memory-watchdog.timer` | meminfo | RAM ≥90% oder Swap ≥80% — Frühwarnung OOM (Throttle 60 min, seit Vorfall 2026-05-25) | 4 min |
| `disk-hygiene-watchdog.timer` | disk + auto-prune | Auto-Prune (docker builder/image + journald) bei Disk >85%, Alarm >90% (stündlich, Selbstpflege seit 2026-05-30) | hourly |
| `doku-drift-watchdog.timer` | doku-drift | Container-Ports vs. Port-Map + MEMORY.md-Limit (<200), nur Alarm (Selbstpflege seit 2026-05-30) | täglich 06:30 |
| `ki-cost-watchdog.timer` | ki-cost | Token/Kosten-Rollup Claude+Codex aus JSONL + Anomalie-Alarm (Selbstpflege seit 2026-05-30) | täglich 07:15 |
| `shadowops-backup-test.timer` | — | monatlich 1. d. Monats 04:50, Wrapper um ZERODOX backup-test.sh | OnCalendar |

**State-Files pro Service:** `data/watchdog_state_<service>.json` (gitignored). Alert nach 2 konsekutiven Failures (~10 Min), Recovery-Alert wenn vorher down war. State-Files getrennt damit Counter sich nicht beeinflussen.

**Service-Files in `deploy/`:** Quelldateien fuer die Watchdogs. User-systemd-Symlinks in `~/.config/systemd/user/`. Git-Updates auf `deploy/*.service|*.timer` wirken sofort (Symlink), `daemon-reload` nur bei Schema-Aenderungen noetig.
