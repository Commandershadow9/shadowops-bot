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
#   WATCHDOG_HEALTH_JQ_FILTER — optional: jq-Boolean-Expression gegen Response-Body.
#                                Wenn gesetzt: HTTP-Status wird IGNORIERT (außer
#                                curl-Fehler), stattdessen Truth-Source = jq-Result.
#                                Beispiele:
#                                  .components.ci_runner.ok
#                                    → true wenn Komponente sich selbst als ok meldet
#                                  '[.alerts[] | select(.component == "ci_runner" and .severity == "critical")] | length == 0'
#                                    → true wenn keine critical-alerts für component
#                                Use-Case: Aggregierte Health-Endpoints filtern auf eine
#                                Komponente (z.B. mayday-ci-Pool bei runner-health.service
#                                der auch ZERODOX-Pool aggregiert).
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

# Geteilte Discord-Send-Lib mit 429-Resilienz (#293). Fallback haelt das alte
# Inline-Curl-Verhalten, falls die Lib bei kaputtem Deploy fehlt (Resilienz first).
# shellcheck source=lib/discord-send.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/discord-send.sh" 2>/dev/null || true
if ! declare -f discord_post >/dev/null 2>&1; then
    discord_post() { curl -sS -o /dev/null -w '%{http_code}' -X POST \
        -H 'Content-Type: application/json' --data "$2" --max-time 10 "$1" 2>/dev/null || echo 000; }
fi
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
    http_code=$(discord_post "$WEBHOOK_URL" "$payload")

    if [[ "$http_code" =~ ^2 ]]; then
        echo "[watchdog:$SERVICE_NAME] Discord-Alert gesendet (HTTP $http_code)"
        return 0
    else
        echo "[watchdog:$SERVICE_NAME] ERROR: Discord-Webhook fehlgeschlagen (HTTP $http_code)"
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

    # JQ-Filter-Mode: HTTP-Status wird ignoriert, jq-Boolean-Expression ist Truth-Source.
    # Use-Case: aggregierte Health-Endpoints die status:critical melden weil EINE von
    # vielen Komponenten down ist — Watchdog soll aber nur auf SEINE Komponente reagieren.
    if [[ -n "${WATCHDOG_HEALTH_JQ_FILTER:-}" ]]; then
        if ! command -v jq >/dev/null 2>&1; then
            echo "DOWN:jq_not_installed"
            return 1
        fi
        local jq_result
        jq_result=$(echo "$body" | jq -r "$WATCHDOG_HEALTH_JQ_FILTER" 2>/dev/null || echo "ERROR")
        case "$jq_result" in
            true)
                echo "UP"
                return 0
                ;;
            false)
                echo "DOWN:jq_filter_false"
                return 1
                ;;
            *)
                # Filter lieferte weder true noch false (Syntax-Error, null, missing key, …)
                echo "DOWN:jq_filter_invalid:$jq_result"
                return 1
                ;;
        esac
    fi

    # Default-Mode: HTTP-Status ist Truth-Source.
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

# ─── Health-Check (systemd-result-Mode) ────────────────────────────────
# Fuer oneshot-Services (z.B. Daily-Healthcheck) die NICHT 24/7 laufen.
# Prueft das ERGEBNIS des letzten Laufs:
#   - Result=success (sonst → DOWN)
#   - ExecMainStartTimestamp innerhalb $WATCHDOG_MAX_AGE_HOURS (Default 36h)
# Wenn Service Result=success aber zu alt: DOWN:stale.
# Wenn Service noch nie gelaufen ist: DOWN:never_ran.
check_health_systemd_result() {
    if [[ -z "${WATCHDOG_SYSTEMD_UNITS:-}" ]]; then
        echo "DOWN:no_units_configured"
        return 1
    fi

    local user_flag=""
    if [[ "${WATCHDOG_SYSTEMD_USER:-1}" == "1" ]]; then
        user_flag="--user"
    fi

    local max_age_hours="${WATCHDOG_MAX_AGE_HOURS:-36}"
    local max_age_seconds=$((max_age_hours * 3600))
    local now_epoch
    now_epoch=$(date +%s)

    local issues=""
    IFS=',' read -ra units <<< "$WATCHDOG_SYSTEMD_UNITS"
    for unit in "${units[@]}"; do
        unit=$(echo "$unit" | xargs)
        [[ -z "$unit" ]] && continue

        # systemctl show liefert KEY=VALUE — robust parsen
        local props
        props=$(systemctl $user_flag show "$unit" \
            --property=Result,ExecMainStartTimestamp,LoadState 2>/dev/null || echo "")
        if [[ -z "$props" ]]; then
            issues="${issues}${unit}=no_status "
            continue
        fi

        local load_state result_state start_ts
        load_state=$(echo "$props" | grep -oE '^LoadState=.*' | cut -d= -f2-)
        result_state=$(echo "$props" | grep -oE '^Result=.*' | cut -d= -f2-)
        start_ts=$(echo "$props" | grep -oE '^ExecMainStartTimestamp=.*' | cut -d= -f2-)

        if [[ "$load_state" != "loaded" ]]; then
            issues="${issues}${unit}=not_loaded "
            continue
        fi
        if [[ -z "$start_ts" ]]; then
            issues="${issues}${unit}=never_ran "
            continue
        fi
        if [[ "$result_state" != "success" ]]; then
            issues="${issues}${unit}=result_${result_state} "
            continue
        fi

        # Alter pruefen: ExecMainStartTimestamp in epoch konvertieren
        local start_epoch age
        start_epoch=$(date -d "$start_ts" +%s 2>/dev/null || echo 0)
        if [[ "$start_epoch" -eq 0 ]]; then
            issues="${issues}${unit}=bad_timestamp "
            continue
        fi
        age=$((now_epoch - start_epoch))
        if [[ "$age" -gt "$max_age_seconds" ]]; then
            local hours=$((age / 3600))
            issues="${issues}${unit}=stale_${hours}h "
        fi
    done

    if [[ -n "$issues" ]]; then
        echo "DOWN:$(echo $issues | tr ' ' ',' | sed 's/,$//')"
        return 1
    fi
    echo "UP"
    return 0
}

# ─── Health-Check (container-Mode) ─────────────────────────────────────
# Prueft den Docker-Healthcheck-Status eines Containers via `docker inspect`.
# Fuer kritische Container OHNE Host-Port-Binding (z.B. leitstelle-scheduler
# :3203 nur intern erreichbar). DOWN wenn der Container fehlt, nicht laeuft,
# oder ein definierter Healthcheck != healthy meldet. Container ohne eigenen
# Healthcheck gelten als UP solange sie laufen ("running").
# Nutzt `docker` ohne sudo (Watchdog-User ist in der docker-Gruppe; vgl.
# disk-hygiene-watchdog.sh).
check_health_container() {
    if [[ -z "${WATCHDOG_CONTAINER:-}" ]]; then
        echo "DOWN:no_container_configured"
        return 1
    fi
    if ! command -v docker >/dev/null 2>&1; then
        echo "DOWN:docker_not_found"
        return 1
    fi

    # docker-Zugriff: bevorzugt direkt (Watchdog-User in docker-Gruppe), sonst
    # via passwordless sudo. Noetig, weil der `systemd --user`-Manager die
    # docker-Gruppe nicht aktiv haben kann, wenn er vor dem usermod gestartet
    # wurde (Gruppen-Caching) → `docker` ohne sudo schlaegt dann fehl.
    local DK="docker"
    if ! docker info >/dev/null 2>&1; then
        if sudo -n docker info >/dev/null 2>&1; then
            DK="sudo -n docker"
        else
            echo "DOWN:docker_unreachable"
            return 1
        fi
    fi

    local state
    state=$($DK inspect "$WATCHDOG_CONTAINER" --format '{{.State.Status}}' 2>/dev/null || echo "missing")
    if [[ "$state" == "missing" ]]; then
        echo "DOWN:container_missing"
        return 1
    fi
    if [[ "$state" != "running" ]]; then
        echo "DOWN:state_${state}"
        return 1
    fi

    # Health-Status nur pruefen, wenn der Container einen Healthcheck definiert.
    local health
    health=$($DK inspect "$WATCHDOG_CONTAINER" \
        --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' 2>/dev/null || echo "unknown")
    if [[ "$health" != "none" && "$health" != "healthy" ]]; then
        echo "DOWN:health_${health}"
        return 1
    fi

    echo "UP"
    return 0
}

# ─── Health-Check Dispatcher ───────────────────────────────────────────
check_health() {
    case "${WATCHDOG_MODE:-http}" in
        http)           check_health_http ;;
        systemd)        check_health_systemd ;;
        systemd-result) check_health_systemd_result ;;
        container)      check_health_container ;;
        *)              echo "DOWN:invalid_mode_${WATCHDOG_MODE}"
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
