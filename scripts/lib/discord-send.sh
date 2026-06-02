#!/usr/bin/env bash
# discord-send.sh — geteilter Discord-Webhook-Sender mit 429-Resilienz (#293).
#
# WARUM: Bei einem Multi-Service-Ausfall feuern mehrere Watchdogs ueber DENSELBEN
# Webhook (SHADOWOPS_WATCHDOG_WEBHOOK) gleichzeitig -> Discord antwortet mit HTTP
# 429 (Rate-Limit). Bisher behandelte kein Watchdog 429 -> einzelne Alarme gingen
# genau im kritischsten Moment still verloren. Diese Lib zentralisiert das Senden
# mit Jitter + Retry-After-Handling.
#
# NUTZUNG (sourcebar):
#   source "$(dirname "${BASH_SOURCE[0]}")/lib/discord-send.sh"
#   http=$(discord_post "$WEBHOOK_URL" "$payload")   # echo't finalen HTTP-Code
#   # Returncode: 0 bei 2xx, sonst 1
#
# VERHALTEN:
#   - Kleiner zufaelliger Jitter (0..DISCORD_MAX_JITTER_MS ms, default 400) VOR dem
#     Send entzerrt gleichzeitige Alarme (Boot-Offsets helfen nur zeitversetzt).
#   - HTTP 429: respektiert den Retry-After-Header (Sekunden, gedeckelt auf
#     DISCORD_RETRY_CAP=10 s), wartet + GENAU 1 Retry. Andere Codes: kein Retry.
#   - Echo't den finalen HTTP-Code ("204"/"429"/"000" bei curl-Fehler) auf stdout.
#
# ENV-Overrides: DISCORD_MAX_JITTER_MS, DISCORD_RETRY_CAP, DISCORD_SEND_TIMEOUT.
#
# Idempotent sourcebar (Guard) — keine Seiteneffekte beim Sourcen.

[ -n "${_DISCORD_SEND_SH_LOADED:-}" ] && return 0
_DISCORD_SEND_SH_LOADED=1

DISCORD_RETRY_CAP="${DISCORD_RETRY_CAP:-10}"
DISCORD_MAX_JITTER_MS="${DISCORD_MAX_JITTER_MS:-400}"
DISCORD_SEND_TIMEOUT="${DISCORD_SEND_TIMEOUT:-10}"

# Schlaeft die uebergebene Anzahl Millisekunden (best-effort, kein Crash).
_discord_sleep_ms() {
    local ms="$1"
    [ "${ms:-0}" -gt 0 ] 2>/dev/null || return 0
    sleep "$(awk -v m="$ms" 'BEGIN{printf "%.3f", m/1000}')" 2>/dev/null || true
}

# discord_post <webhook-url> <json-payload>
# Echo't den finalen HTTP-Code, Returncode 0 bei 2xx.
discord_post() {
    local webhook="$1" payload="$2"
    if [ -z "$webhook" ]; then echo "000"; return 1; fi

    # Jitter vor dem ersten Send (entzerrt gleichzeitige Multi-Service-Alarme).
    if [ "${DISCORD_MAX_JITTER_MS:-0}" -gt 0 ] 2>/dev/null; then
        _discord_sleep_ms "$(( RANDOM % (DISCORD_MAX_JITTER_MS + 1) ))"
    fi

    local attempt resp http retry_after
    for attempt in 1 2; do
        # Header (-D -) auf stdout, Body verworfen; HTTP-Code via -w angehaengt.
        resp=$(curl -sS -D - -o /dev/null -w $'\nHTTPCODE=%{http_code}' \
            -X POST -H "Content-Type: application/json" \
            --data "$payload" --max-time "$DISCORD_SEND_TIMEOUT" \
            "$webhook" 2>/dev/null) || resp=$'\nHTTPCODE=000'
        http=$(printf '%s\n' "$resp" | sed -n 's/.*HTTPCODE=//p' | tail -1 | tr -dc '0-9')
        [ -z "$http" ] && http="000"

        # Nur beim ersten Versuch + nur bei 429 wird ein Retry versucht.
        if [ "$attempt" -eq 1 ] && [ "$http" = "429" ]; then
            retry_after=$(printf '%s\n' "$resp" | grep -i '^retry-after:' | tail -1 | tr -dc '0-9.')
            [ -z "$retry_after" ] && retry_after="1"
            # Auf Cap deckeln, damit ein boeser Header den Watchdog nicht haengt.
            retry_after=$(awk -v r="$retry_after" -v c="$DISCORD_RETRY_CAP" 'BEGIN{print (r+0>c+0)?c:r}')
            echo "[discord-send] HTTP 429 — warte ${retry_after}s, 1 Retry" >&2
            sleep "$retry_after" 2>/dev/null || sleep 1
            continue
        fi
        break
    done

    echo "$http"
    case "$http" in
        2*) return 0 ;;
        *) return 1 ;;
    esac
}
