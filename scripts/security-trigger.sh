#!/usr/bin/env bash
# security-trigger.sh — publisht sec:trigger auf dem Job-Bus (guildscout-redis).
# Externer Cron-Trigger statt Self-Retrigger im Prozess (crash-safe — Muster
# aus ~/agents/projects/seo/scripts/trigger-audit.sh).
# Exit 4 = 0 Subscriber → Orchestrator laeuft nicht (Alarm-Signal fuer Cron/Watchdog).
# Aufruf: security-trigger.sh [daily|manual|<beliebig>]
set -euo pipefail

# Gruppen-Session-Drift: falls docker-Gruppe nicht aktiv, re-exec via sg
if ! docker ps >/dev/null 2>&1; then
    if [ -n "${_SEC_SG_REEXEC:-}" ]; then
        echo "FEHLER: docker weiterhin unerreichbar nach sg-Re-Exec — Abbruch." >&2
        exit 3
    fi
    export _SEC_SG_REEXEC=1
    exec sg docker -c "$0 ${*:-}"
fi

ENV_FILE="$HOME/.config/shadowops-security-team.env"
if [ -f "$ENV_FILE" ]; then
    set -a; . "$ENV_FILE"; set +a
fi

TRIGGER="${1:-daily}"
# Passwort aus REDIS_URL extrahieren (Form redis://:PASS@host:port/db)
PASS=$(printf '%s' "${REDIS_URL:-}" | sed -n 's|redis://:\([^@]*\)@.*|\1|p')

# Passwort NICHT via argv uebergeben (sichtbar in ps/argv) — auch nicht als
# "-e REDISCLI_AUTH=$PASS" (der Wert landet dabei ebenfalls in der host-argv
# des docker-exec-Aufrufs, empirisch belegt). Stattdessen Name-only-Form:
# "-e REDISCLI_AUTH" reicht den Wert aus der Shell-Umgebung durch, ohne dass
# er als Argument erscheint.
if [ -n "$PASS" ]; then
    export REDISCLI_AUTH="$PASS"
    SUBS=$(docker exec -e REDISCLI_AUTH guildscout-redis redis-cli --no-auth-warning \
        publish sec:trigger "{\"trigger\":\"${TRIGGER}\"}")
    unset REDISCLI_AUTH
else
    SUBS=$(docker exec guildscout-redis redis-cli --no-auth-warning \
        publish sec:trigger "{\"trigger\":\"${TRIGGER}\"}")
fi

if ! [[ "${SUBS:-}" =~ ^[0-9]+$ ]]; then
    echo "FEHLER: unerwartete redis-cli-Antwort: ${SUBS:-<leer>}" >&2
    exit 5
fi

if [ "${SUBS:-0}" -eq 0 ]; then
    echo "FEHLER: 0 Subscriber auf sec:trigger — Security-Orchestrator laeuft nicht?" >&2
    exit 4
fi
echo "sec:trigger (${TRIGGER}) an ${SUBS} Subscriber publiziert"
