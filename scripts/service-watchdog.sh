#!/usr/bin/env bash
#
# service-watchdog.sh — Generischer Health-Endpoint-Watchdog für beliebige
# Services. Postet bei Down/Recovery via Discord-Webhook **direkt** — kein
# Bot-Indirektion, damit auch ein toter Service alertieren kann.
#
# Defaults sind auf den ShadowOps-Bot ausgelegt; jedes andere Service braucht
# nur die Env-Vars überschreiben (siehe deploy/zerodox-watchdog.service als
# Beispiel).
#
# Erweiterung von bot-watchdog.sh (PR #253) für ZERODOX und GuildScout —
# damit auch deren Ausfälle direkt alertieren, NICHT nur via shadowops-bot
# project_monitor (das selbst tot sein könnte).
#
# Konfiguration via Env-Vars (mit Defaults):
#   WATCHDOG_SERVICE_NAME — Display-Name für Discord-Embed (default "shadowops-bot")
#   WATCHDOG_HEALTH_URL   — Health-Endpoint (default http://127.0.0.1:8766/health)
#   WATCHDOG_STATE_FILE   — State-Persistenz pro Service
#                           (default data/watchdog_state_<service>.json)
#   WATCHDOG_WEBHOOK      — Discord-Webhook-URL (Pflicht für Alert)
#   WATCHDOG_TIMEOUT_S    — Curl-Timeout (default 10)
#   WATCHDOG_REQUIRE_BOT_READY — wenn "1": prüfe zusätzlich JSON-Feld
#                                bot_ready=true (Default 1 für shadowops, sonst 0)
#
# Backward-Compat: Falls SHADOWOPS_HEALTH_URL/SHADOWOPS_WATCHDOG_WEBHOOK/
# SHADOWOPS_WATCHDOG_STATE/SHADOWOPS_WATCHDOG_TIMEOUT gesetzt sind, werden
# diese als Fallback genutzt (alte systemd-Units brechen nicht).
#
# Exit-Codes:
#   0 = Service healthy oder Alert erfolgreich
#   1 = Service down UND Webhook-Call fehlgeschlagen
#   2 = Konfigurationsfehler

set -euo pipefail

# ─── Konfiguration mit Backward-Compat ─────────────────────────────────
SERVICE_NAME="${WATCHDOG_SERVICE_NAME:-shadowops-bot}"
HEALTH_URL="${WATCHDOG_HEALTH_URL:-${SHADOWOPS_HEALTH_URL:-http://127.0.0.1:8766/health}}"
WEBHOOK_URL="${WATCHDOG_WEBHOOK:-${SHADOWOPS_WATCHDOG_WEBHOOK:-}}"
CURL_TIMEOUT_S="${WATCHDOG_TIMEOUT_S:-${SHADOWOPS_WATCHDOG_TIMEOUT:-10}}"
# REQUIRE_BOT_READY: shadowops-bot liefert bot_ready im Health-JSON.
# Generische Services (ZERODOX, GuildScout) haben das nicht — Default OFF
# für non-shadowops Services. Explizit setzbar via Env.
if [[ -z "${WATCHDOG_REQUIRE_BOT_READY:-}" ]]; then
    if [[ "$SERVICE_NAME" == "shadowops-bot" ]]; then
        WATCHDOG_REQUIRE_BOT_READY=1
    else
        WATCHDOG_REQUIRE_BOT_READY=0
    fi
fi

# State-File: pro Service eigene Datei (mehrere Services laufen parallel)
SERVICE_SLUG="$(echo "$SERVICE_NAME" | tr 'A-Z ' 'a-z_' | tr -cd 'a-z0-9_-')"
DEFAULT_STATE="/home/cmdshadow/shadowops-bot/data/watchdog_state_${SERVICE_SLUG}.json"
# Spezialfall: alte Pfad-Konvention für shadowops-bot weiterführen
if [[ "$SERVICE_NAME" == "shadowops-bot" && -f "/home/cmdshadow/shadowops-bot/data/watchdog_state.json" ]]; then
    DEFAULT_STATE="/home/cmdshadow/shadowops-bot/data/watchdog_state.json"
fi
STATE_FILE="${WATCHDOG_STATE_FILE:-${SHADOWOPS_WATCHDOG_STATE:-$DEFAULT_STATE}}"

HOSTNAME_SHORT="$(hostname -s 2>/dev/null || echo vServer)"
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

mkdir -p "$(dirname "$STATE_FILE")"

# ─── State-Helper ──────────────────────────────────────────────────────
read_state() {
    if [[ -f "$STATE_FILE" ]]; then
        cat "$STATE_FILE"
    else
        echo '{"last_status":"up","last_alert_at":"","consecutive_failures":0}'
    fi
}

write_state() {
    local status="$1"
    local consecutive="$2"
    local alert_ts="$3"
    cat > "$STATE_FILE" <<EOF
{"last_status":"$status","last_alert_at":"$alert_ts","consecutive_failures":$consecutive,"updated_at":"$TS","service":"$SERVICE_NAME"}
EOF
}

# ─── Discord-Alert ─────────────────────────────────────────────────────
send_discord_alert() {
    local title="$1"
    local description="$2"
    local color="$3"

    if [[ -z "$WEBHOOK_URL" ]]; then
        echo "[watchdog:$SERVICE_NAME] WARN: WATCHDOG_WEBHOOK nicht gesetzt — kein Discord-Alert"
        return 1
    fi

    local payload
    payload=$(cat <<EOF
{
  "username": "ShadowOps Watchdog",
  "embeds": [{
    "title": "$title",
    "description": "$description",
    "color": $color,
    "footer": {"text": "Service: $SERVICE_NAME — Host: $HOSTNAME_SHORT — $TS"}
  }]
}
EOF
)

    local http_code
    http_code=$(curl -s -o /tmp/watchdog_resp.json -w "%{http_code}" \
        --max-time 15 \
        -H "Content-Type: application/json" \
        -X POST -d "$payload" \
        "$WEBHOOK_URL" || echo "000")

    if [[ "$http_code" =~ ^2 ]]; then
        echo "[watchdog:$SERVICE_NAME] Discord-Alert gesendet (HTTP $http_code)"
        return 0
    else
        echo "[watchdog:$SERVICE_NAME] ERROR: Discord-Webhook fehlgeschlagen (HTTP $http_code)"
        cat /tmp/watchdog_resp.json 2>/dev/null | head -2
        return 1
    fi
}

# ─── Health-Check (HTTP-Mode) ──────────────────────────────────────────
check_health_http() {
    local resp http_code body

    if ! resp=$(curl -s -m "$CURL_TIMEOUT_S" -w "\n%{http_code}" "$HEALTH_URL" 2>/dev/null); then
        echo "DOWN:curl_failed"
        return 1
    fi

    http_code=$(echo "$resp" | tail -n1)
    body=$(echo "$resp" | head -n -1)

    if [[ "$http_code" != "200" ]]; then
        echo "DOWN:http_$http_code"
        return 1
    fi

    # Optional: bot_ready-Feld checken (nur wenn explizit gewünscht)
    if [[ "$WATCHDOG_REQUIRE_BOT_READY" == "1" ]]; then
        local bot_ready
        bot_ready=$(echo "$body" | grep -oE '"bot_ready"\s*:\s*(true|false)' | grep -oE '(true|false)' || echo "true")
        if [[ "$bot_ready" != "true" ]]; then
            echo "DOWN:bot_not_ready"
            return 1
        fi
    fi

    echo "UP"
    return 0
}

# ─── Health-Check (systemd-Mode) ───────────────────────────────────────
# Prueft jede Unit in $WATCHDOG_SYSTEMD_UNITS (Komma-separiert) ueber
# `systemctl is-active`. Wenn auch nur EINE Unit nicht active → DOWN.
# Nutzt --user wenn $WATCHDOG_SYSTEMD_USER=1 (Default 1), sonst System.
check_health_systemd() {
    if [[ -z "${WATCHDOG_SYSTEMD_UNITS:-}" ]]; then
        echo "DOWN:no_units_configured"
        return 1
    fi

    local user_flag=""
    if [[ "${WATCHDOG_SYSTEMD_USER:-1}" == "1" ]]; then
        user_flag="--user"
    fi

    local failed_units=""
    IFS=',' read -ra units <<< "$WATCHDOG_SYSTEMD_UNITS"
    for unit in "${units[@]}"; do
        unit=$(echo "$unit" | xargs)  # trim whitespace
        [[ -z "$unit" ]] && continue
        local status
        status=$(systemctl $user_flag is-active "$unit" 2>/dev/null || echo "unknown")
        if [[ "$status" != "active" ]]; then
            failed_units="${failed_units}${unit}=${status} "
        fi
    done

    if [[ -n "$failed_units" ]]; then
        echo "DOWN:$(echo $failed_units | tr ' ' ',' | sed 's/,$//')"
        return 1
    fi
    echo "UP"
    return 0
}

# ─── Health-Check Dispatcher ───────────────────────────────────────────
check_health() {
    case "${WATCHDOG_MODE:-http}" in
        http)    check_health_http ;;
        systemd) check_health_systemd ;;
        *)       echo "DOWN:invalid_mode_${WATCHDOG_MODE}"
                 return 1
                 ;;
    esac
}

# ─── Main ──────────────────────────────────────────────────────────────
main() {
    local state last_status consecutive
    state=$(read_state)
    last_status=$(echo "$state" | grep -oE '"last_status":"[^"]*"' | cut -d'"' -f4)
    consecutive=$(echo "$state" | grep -oE '"consecutive_failures":[0-9]+' | cut -d':' -f2)
    last_status="${last_status:-up}"
    consecutive="${consecutive:-0}"

    local result
    result=$(check_health || true)
    local status="${result%%:*}"
    local reason="${result#*:}"

    if [[ "$status" == "UP" ]]; then
        if [[ "$last_status" == "down" ]]; then
            send_discord_alert \
                "✅ $SERVICE_NAME wieder UP" \
                "Service ist wieder erreichbar nach $consecutive Fehlversuch(en).\n\`$HEALTH_URL\`" \
                3066993 || true  # grün
        fi
        write_state "up" 0 ""
        echo "[watchdog:$SERVICE_NAME] OK — healthy"
        exit 0
    fi

    # DOWN-Pfad
    consecutive=$((consecutive + 1))

    if [[ "$consecutive" -ge 2 && "$last_status" != "down" ]]; then
        if send_discord_alert \
            "🔴 $SERVICE_NAME DOWN" \
            "Health-Check fehlgeschlagen ($consecutive konsekutive Failures): \`$reason\`\nEndpoint: \`$HEALTH_URL\`\n\nSofort prüfen: Service-Status und Logs." \
            15158332  # rot
        then
            write_state "down" "$consecutive" "$TS"
            echo "[watchdog:$SERVICE_NAME] ALERT gesendet — down ($reason)"
            exit 0
        else
            write_state "down" "$consecutive" ""
            echo "[watchdog:$SERVICE_NAME] FAIL — down ($reason) UND Webhook fehlgeschlagen"
            exit 1
        fi
    fi

    write_state "$([[ $consecutive -ge 2 ]] && echo down || echo up)" "$consecutive" "$([[ "$last_status" == "down" ]] && echo "$TS" || echo "")"
    echo "[watchdog:$SERVICE_NAME] down ($reason), consecutive=$consecutive, last_status=$last_status — kein neuer Alert"
    exit 0
}

main "$@"
