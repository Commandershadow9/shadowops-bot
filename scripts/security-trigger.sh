#!/usr/bin/env bash
# security-trigger.sh — publisht sec:trigger auf dem Job-Bus (guildscout-redis).
# Externer Cron-Trigger statt Self-Retrigger im Prozess (crash-safe — Muster
# aus ~/agents/projects/seo/scripts/trigger-audit.sh).
# Exit 4 = 0 Subscriber → Orchestrator laeuft nicht (Alarm-Signal fuer Cron/Watchdog).
# Aufruf: security-trigger.sh [daily|manual|<beliebig>]
set -euo pipefail

# Gruppen-Session-Drift: falls docker-Gruppe nicht aktiv, re-exec via sg
if ! docker ps >/dev/null 2>&1; then
    exec sg docker -c "$0 ${*:-}"
fi

ENV_FILE="$HOME/.config/shadowops-security-team.env"
if [ -f "$ENV_FILE" ]; then
    set -a; . "$ENV_FILE"; set +a
fi

TRIGGER="${1:-daily}"
# Passwort aus REDIS_URL extrahieren (Form redis://:PASS@host:port/db)
PASS=$(printf '%s' "${REDIS_URL:-}" | sed -n 's|redis://:\([^@]*\)@.*|\1|p')

SUBS=$(docker exec guildscout-redis redis-cli ${PASS:+-a "$PASS"} --no-auth-warning \
    publish sec:trigger "{\"trigger\":\"${TRIGGER}\"}")

if [ "${SUBS:-0}" -eq 0 ]; then
    echo "FEHLER: 0 Subscriber auf sec:trigger — Security-Orchestrator laeuft nicht?" >&2
    exit 4
fi
echo "sec:trigger (${TRIGGER}) an ${SUBS} Subscriber publiziert"
