#!/usr/bin/env bash
#
# shadowops-drift-watchdog.sh — systemd-State + Drift Detection für shadowops-bot
#
# Kontext (2026-05-20-Vorfall): Der HTTP-Watchdog (bot-watchdog.sh) hat den
# Bot als "healthy" gemeldet, während der System-Level Service 96 Min im
# Restart-Loop war (Counter 579). Ursache: parallel laufende User-Level
# Service-Instanz hielt den Health-Endpoint am Leben, aber das Lock-File
# blockierte den System-Service.
#
# Dieser Watchdog schließt die Lücke. Er prüft Zustände, die der HTTP-Probe
# strukturell entgehen:
#   1. User-Level Unit-File existiert wieder (Drift) → Auto-Heal + Alert
#   2. System-Service != active (failed, inactive, activating) → Alert
#   3. NRestarts > Threshold seit letztem Run (Restart-Loop) → Alert
#
# Alle Alerts gehen direkt via Discord-Webhook in #🩺-uptime-alerts —
# unabhängig vom Bot, damit auch tote-Bot-Situationen Alerts produzieren.
#
# Exit-Codes:
#   0 = alles OK oder Alert erfolgreich gesendet
#   1 = Webhook-Call fehlgeschlagen

set -euo pipefail

# ─── Konfiguration ─────────────────────────────────────────────────────
WEBHOOK_URL="${SHADOWOPS_WATCHDOG_WEBHOOK:-}"
STATE_FILE="${SHADOWOPS_DRIFT_STATE:-/home/cmdshadow/shadowops-bot/data/watchdog_state_drift.json}"
USER_UNIT_PATH="${SHADOWOPS_USER_UNIT_PATH:-/home/cmdshadow/.config/systemd/user/shadowops-bot.service}"
# NRestarts-Delta-Threshold pro 5-Min-Window: >= 3 deutet auf Restart-Loop hin
RESTART_DELTA_THRESHOLD="${SHADOWOPS_RESTART_DELTA_THRESHOLD:-3}"
HOSTNAME_SHORT="$(hostname -s 2>/dev/null || echo vServer)"
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

mkdir -p "$(dirname "$STATE_FILE")"

# ─── State-Helper ──────────────────────────────────────────────────────
# State-Format: {"last_nrestarts":N,"last_check_at":"ISO","last_drift_alert_at":"ISO","last_state_alert_at":"ISO","last_loop_alert_at":"ISO"}
read_state_int() {
    local key="$1"
    if [[ -f "$STATE_FILE" ]]; then
        grep -oE "\"$key\":[0-9]+" "$STATE_FILE" 2>/dev/null | cut -d':' -f2 || echo "0"
    else
        echo "0"
    fi
}

read_state_str() {
    local key="$1"
    if [[ -f "$STATE_FILE" ]]; then
        grep -oE "\"$key\":\"[^\"]*\"" "$STATE_FILE" 2>/dev/null | cut -d'"' -f4 || echo ""
    else
        echo ""
    fi
}

write_state() {
    local nrestarts="$1"
    local drift_alert="$2"
    local state_alert="$3"
    local loop_alert="$4"
    cat > "$STATE_FILE" <<EOF
{"last_nrestarts":$nrestarts,"last_check_at":"$TS","last_drift_alert_at":"$drift_alert","last_state_alert_at":"$state_alert","last_loop_alert_at":"$loop_alert"}
EOF
}

# ─── Discord-Alert ─────────────────────────────────────────────────────
send_discord_alert() {
    local title="$1"
    local description="$2"
    local color="$3"  # decimal RGB

    if [[ -z "$WEBHOOK_URL" ]]; then
        echo "[drift-watchdog] WARN: SHADOWOPS_WATCHDOG_WEBHOOK nicht gesetzt"
        return 1
    fi

    local payload
    payload=$(cat <<EOF
{
  "username": "ShadowOps Drift Watchdog",
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
    http_code=$(curl -s -o /tmp/drift_resp.json -w "%{http_code}" \
        --max-time 15 \
        -H "Content-Type: application/json" \
        -X POST -d "$payload" \
        "$WEBHOOK_URL" || echo "000")

    if [[ "$http_code" =~ ^2 ]]; then
        echo "[drift-watchdog] Discord-Alert gesendet (HTTP $http_code)"
        return 0
    else
        echo "[drift-watchdog] ERROR: Webhook fehlgeschlagen (HTTP $http_code)"
        return 1
    fi
}

# ─── Check 1: User-Level Unit-File Drift ───────────────────────────────
# Wenn die Datei existiert kann sie jederzeit per `systemctl --user enable`
# das Dual-Service-Chaos vom 2026-05-20 reproduzieren. Wir benennen sie
# auto-heilend um und alerten.
check_user_unit_drift() {
    if [[ -e "$USER_UNIT_PATH" ]]; then
        local renamed="${USER_UNIT_PATH}.auto-disabled-${TS//:/-}"
        mv "$USER_UNIT_PATH" "$renamed" 2>/dev/null || {
            echo "[drift-watchdog] DRIFT erkannt aber Rename fehlgeschlagen: $USER_UNIT_PATH"
            return 2
        }
        systemctl --user daemon-reload 2>/dev/null || true
        send_discord_alert \
            "⚠️ ShadowOps User-Unit-Drift erkannt + auto-disabled" \
            "Datei \`$USER_UNIT_PATH\` ist wieder aufgetaucht und wurde auto-deaktiviert (umbenannt zu \`$(basename "$renamed")\`).\n\nUrsache prüfen: wer/was hat das Unit-File neu angelegt? Ein Worker-PR? Ein Deploy-Script?" \
            16776960  # gelb
        return 1
    fi
    return 0
}

# ─── Check 2: System-Service-State ─────────────────────────────────────
check_service_state() {
    local active_state
    active_state=$(systemctl is-active shadowops-bot 2>&1 || true)
    # Erlaubte States: active. activating ist transient während Start — also nicht alerten.
    case "$active_state" in
        active|activating|reloading)
            return 0
            ;;
        *)
            local last_alert
            last_alert=$(read_state_str "last_state_alert_at")
            # Alert-Dedup: max 1 Alert pro Stunde für denselben Zustand
            if [[ -n "$last_alert" ]]; then
                local last_epoch now_epoch
                last_epoch=$(date -d "$last_alert" +%s 2>/dev/null || echo 0)
                now_epoch=$(date +%s)
                if (( now_epoch - last_epoch < 3600 )); then
                    echo "[drift-watchdog] State '$active_state' — Alert <1h alt, skip"
                    return 1
                fi
            fi

            send_discord_alert \
                "🔴 ShadowOps Service-State: $active_state" \
                "\`systemctl is-active shadowops-bot\` meldet **$active_state** (erwartet: active).\n\nSofort prüfen: \`sudo systemctl status shadowops-bot\` und \`sudo journalctl -u shadowops-bot -n 50\`" \
                15158332  # rot
            return 1
            ;;
    esac
}

# ─── Check 3: Restart-Loop-Detection ───────────────────────────────────
check_restart_loop() {
    local current_nrestarts last_nrestarts delta
    current_nrestarts=$(systemctl show shadowops-bot -p NRestarts --value 2>/dev/null || echo "0")
    last_nrestarts=$(read_state_int "last_nrestarts")

    delta=$((current_nrestarts - last_nrestarts))

    # Bei Service-Restart kann NRestarts auf 0 zurückspringen — Delta wird negativ
    if (( delta < 0 )); then
        delta=$current_nrestarts
    fi

    if (( delta >= RESTART_DELTA_THRESHOLD )); then
        local last_alert
        last_alert=$(read_state_str "last_loop_alert_at")
        if [[ -n "$last_alert" ]]; then
            local last_epoch now_epoch
            last_epoch=$(date -d "$last_alert" +%s 2>/dev/null || echo 0)
            now_epoch=$(date +%s)
            # Max 1 Restart-Loop-Alert pro 30 Min
            if (( now_epoch - last_epoch < 1800 )); then
                echo "[drift-watchdog] Restart-Loop delta=$delta — Alert <30min alt, skip"
                # State trotzdem updaten
                write_state "$current_nrestarts" \
                    "$(read_state_str last_drift_alert_at)" \
                    "$(read_state_str last_state_alert_at)" \
                    "$last_alert"
                return 1
            fi
        fi

        send_discord_alert \
            "🔁 ShadowOps Restart-Loop erkannt" \
            "Service hat **$delta Restart(s)** in den letzten ~5 Minuten gemacht (NRestarts: $last_nrestarts → $current_nrestarts).\n\nDas deutet auf Lock-Konflikt, Crash-Loop oder Dual-Service-Drift hin. \`sudo journalctl -u shadowops-bot --since '10 minutes ago'\` zeigt die Ursache." \
            15105570  # orange
        write_state "$current_nrestarts" \
            "$(read_state_str last_drift_alert_at)" \
            "$(read_state_str last_state_alert_at)" \
            "$TS"
        return 1
    fi

    # Healthy — State aktualisieren (Counter merken)
    write_state "$current_nrestarts" \
        "$(read_state_str last_drift_alert_at)" \
        "$(read_state_str last_state_alert_at)" \
        "$(read_state_str last_loop_alert_at)"
    return 0
}

# ─── Main ──────────────────────────────────────────────────────────────
main() {
    local issues=0

    if ! check_user_unit_drift; then
        issues=$((issues + 1))
    fi

    if ! check_service_state; then
        issues=$((issues + 1))
    fi

    if ! check_restart_loop; then
        issues=$((issues + 1))
    fi

    if (( issues == 0 )); then
        echo "[drift-watchdog] OK — keine Drift, Service active, keine Restart-Loops"
        exit 0
    fi

    echo "[drift-watchdog] $issues Issue(s) detektiert"
    exit 0
}

main "$@"
