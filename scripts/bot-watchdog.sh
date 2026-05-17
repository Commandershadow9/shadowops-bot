#!/usr/bin/env bash
#
# bot-watchdog.sh — Externer Watchdog für den ShadowOps Bot.
#
# Pingt den Health-Endpoint (Default: http://127.0.0.1:8766/health) und
# alertiert via Discord-Webhook wenn:
#   - HTTP-Status != 200
#   - JSON-Feld bot_ready != true
#   - Curl-Timeout
#
# Solo-Dev-Kontext: Der Bot war am 14.-17.05.2026 für 3 Tage down, ohne
# dass es bemerkt wurde (Issue #249-Cleanup). Dieser Watchdog schließt
# die Lücke — direkter Discord-Webhook (nicht über den Bot selbst!), damit
# Alerts auch bei totem Bot ankommen.
#
# State-File verhindert Alert-Spam: einmal "down" → einmal Alert, danach
# nichts bis "recovery".
#
# Konfiguration: Env-Var SHADOWOPS_WATCHDOG_WEBHOOK muss gesetzt sein
# (Discord-Webhook-URL). Wenn leer/unset: Script läuft trotzdem, loggt
# nur lokal — kein Discord-Alert.
#
# Exit-Codes:
#   0 = Bot healthy oder Alert erfolgreich gesendet
#   1 = Bot down UND Webhook-Call fehlgeschlagen
#   2 = Konfigurationsfehler

set -euo pipefail

# ─── Konfiguration ─────────────────────────────────────────────────────
HEALTH_URL="${SHADOWOPS_HEALTH_URL:-http://127.0.0.1:8766/health}"
WEBHOOK_URL="${SHADOWOPS_WATCHDOG_WEBHOOK:-}"
STATE_FILE="${SHADOWOPS_WATCHDOG_STATE:-/home/cmdshadow/shadowops-bot/data/watchdog_state.json}"
CURL_TIMEOUT_S="${SHADOWOPS_WATCHDOG_TIMEOUT:-10}"
HOSTNAME_SHORT="$(hostname -s 2>/dev/null || echo vServer)"
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

mkdir -p "$(dirname "$STATE_FILE")"

# ─── State-Helper ──────────────────────────────────────────────────────
# State-Format (JSON): {"last_status": "up"|"down", "last_alert_at": "ISO", "consecutive_failures": N}
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
{"last_status":"$status","last_alert_at":"$alert_ts","consecutive_failures":$consecutive,"updated_at":"$TS"}
EOF
}

# ─── Discord-Alert ─────────────────────────────────────────────────────
send_discord_alert() {
    local title="$1"
    local description="$2"
    local color="$3"  # 0xRRGGBB als Decimal

    if [[ -z "$WEBHOOK_URL" ]]; then
        echo "[watchdog] WARN: SHADOWOPS_WATCHDOG_WEBHOOK nicht gesetzt — kein Discord-Alert"
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
    "footer": {"text": "Host: $HOSTNAME_SHORT — $TS"}
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
        echo "[watchdog] Discord-Alert gesendet (HTTP $http_code)"
        return 0
    else
        echo "[watchdog] ERROR: Discord-Webhook fehlgeschlagen (HTTP $http_code)"
        cat /tmp/watchdog_resp.json 2>/dev/null | head -2
        return 1
    fi
}

# ─── Health-Check ──────────────────────────────────────────────────────
check_health() {
    local resp http_code bot_ready

    # Curl mit Timeout, fängt Connection-Refused und Hangs ab
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

    # JSON-Feld bot_ready prüfen — wenn Bot zwar HTTP-up aber discord-disconnected,
    # gilt es als down. Tolerant: fehlt das Feld → trotzdem up (legacy-Format).
    bot_ready=$(echo "$body" | grep -oE '"bot_ready"\s*:\s*(true|false)' | grep -oE '(true|false)' || echo "true")
    if [[ "$bot_ready" != "true" ]]; then
        echo "DOWN:bot_not_ready"
        return 1
    fi

    echo "UP"
    return 0
}

# ─── Main ──────────────────────────────────────────────────────────────
main() {
    local state last_status consecutive
    state=$(read_state)
    last_status=$(echo "$state" | grep -oE '"last_status":"[^"]*"' | cut -d'"' -f4)
    consecutive=$(echo "$state" | grep -oE '"consecutive_failures":[0-9]+' | cut -d':' -f2)
    last_status="${last_status:-up}"
    consecutive="${consecutive:-0}"

    # `|| true` ist nötig: check_health gibt bewusst non-zero zurück bei DOWN.
    # Ohne || true würde `set -e` das Script abbrechen bevor wir den Status parsen.
    local result
    result=$(check_health || true)
    local status="${result%%:*}"
    local reason="${result#*:}"

    if [[ "$status" == "UP" ]]; then
        # Recovery-Pfad: alert nur wenn vorher down. `|| true` weil Webhook-Fehler
        # nicht den State-Write blockieren darf (Idempotenz vor Cosmetic).
        if [[ "$last_status" == "down" ]]; then
            send_discord_alert \
                "✅ ShadowOps Bot wieder UP" \
                "Bot ist wieder erreichbar nach $consecutive Fehlversuch(en).\n\`$HEALTH_URL\`" \
                3066993 || true  # grün (0x2ECC71)
        fi
        write_state "up" 0 ""
        echo "[watchdog] OK — Bot healthy"
        exit 0
    fi

    # DOWN-Pfad
    consecutive=$((consecutive + 1))

    # Erst ab 2 konsekutiven Failures alerten — vermeidet false-positives bei
    # transienten Netzwerk-Issues oder Bot-Restart-Phasen.
    if [[ "$consecutive" -ge 2 && "$last_status" != "down" ]]; then
        if send_discord_alert \
            "🔴 ShadowOps Bot DOWN" \
            "Health-Check fehlgeschlagen ($consecutive konsekutive Failures): \`$reason\`\nEndpoint: \`$HEALTH_URL\`\n\nSofort prüfen: \`systemctl status shadowops-bot\` und \`journalctl -u shadowops-bot -n 50\`" \
            15158332  # rot (0xE74C3C)
        then
            write_state "down" "$consecutive" "$TS"
            echo "[watchdog] ALERT gesendet — Bot down ($reason)"
            exit 0
        else
            write_state "down" "$consecutive" ""
            echo "[watchdog] FAIL — Bot down ($reason) UND Webhook fehlgeschlagen"
            exit 1
        fi
    fi

    # Noch nicht im Alert-Threshold ODER bereits gealertet
    write_state "$([[ $consecutive -ge 2 ]] && echo down || echo up)" "$consecutive" "$([[ "$last_status" == "down" ]] && echo "$TS" || echo "")"
    echo "[watchdog] Bot down ($reason), consecutive=$consecutive, last_status=$last_status — kein neuer Alert"
    exit 0
}

main "$@"
