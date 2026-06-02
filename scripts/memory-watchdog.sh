#!/usr/bin/env bash
#
# memory-watchdog.sh — Watchdog der bei Memory-Druck via Discord-Webhook
# alarmiert. Direkt-Alert ohne Bot-Indirektion, damit auch OOM-Crash gemeldet wird.
#
# Erstellt 2026-05-25 nach OOM-Cascade-Vorfall (logind-kill + SSH-Lockout).
# Frühwarnung BEVOR earlyoom kritische Prozesse killt.
#
# Schwellen (Default, via Env überschreibbar):
#   RAM_WARN_PCT=90   — RAM-Auslastung in %, ab der gewarnt wird
#   SWAP_WARN_PCT=80  — Swap-Auslastung in %, ab der gewarnt wird
#
# Throttling: bei dauerhaftem Druck nur 1× pro 60 min alarmen (kein Spam).
# Recovery-Alert: einmalig wenn beide Werte wieder unter Schwelle.
#
# State-File: ~/shadowops-bot/data/watchdog_state_memory.json
# Webhook-Config: ~/.config/shadowops-watchdog.env (Pflicht)
#
# Exit:
#   0 = healthy ODER Alert erfolgreich gesendet
#   1 = Webhook-Call fehlgeschlagen
#   2 = Konfigurationsfehler

set -euo pipefail

# ─── Konfig ─────────────────────────────────────────────
RAM_WARN_PCT="${RAM_WARN_PCT:-90}"
SWAP_WARN_PCT="${SWAP_WARN_PCT:-80}"
ALERT_THROTTLE_S="${ALERT_THROTTLE_S:-3600}"   # 60 min zwischen Re-Alerts
STATE_FILE="${STATE_FILE:-/home/cmdshadow/shadowops-bot/data/watchdog_state_memory.json}"
WEBHOOK_CONFIG="${WEBHOOK_CONFIG:-/home/cmdshadow/.config/shadowops-watchdog.env}"

# Webhook aus Config laden
if [ -f "$WEBHOOK_CONFIG" ]; then
  # shellcheck source=/dev/null
  source "$WEBHOOK_CONFIG"
fi
WEBHOOK_URL="${WATCHDOG_WEBHOOK:-${SHADOWOPS_WATCHDOG_WEBHOOK:-}}"
if [ -z "$WEBHOOK_URL" ]; then
  echo "[memory-watchdog] ERROR: Kein Webhook konfiguriert" >&2
  exit 2
fi

mkdir -p "$(dirname "$STATE_FILE")"

# Geteilte Discord-Send-Lib mit 429-Resilienz (#293). Fallback = altes Inline-Curl.
# shellcheck source=lib/discord-send.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/discord-send.sh" 2>/dev/null || true
if ! declare -f discord_post >/dev/null 2>&1; then
  discord_post() { curl -sS -o /dev/null -w '%{http_code}' -X POST \
    -H 'Content-Type: application/json' --data "$2" --max-time 10 "$1" 2>/dev/null || echo 000; }
fi

# ─── State lesen (Defaults) ───────────────────────────────
if [ -f "$STATE_FILE" ]; then
  last_state=$(jq -r '.last_state // "ok"' "$STATE_FILE" 2>/dev/null || echo "ok")
  last_alert_at=$(jq -r '.last_alert_at // ""' "$STATE_FILE" 2>/dev/null || echo "")
else
  last_state="ok"
  last_alert_at=""
fi

# ─── Memory-Werte lesen aus /proc/meminfo (in Bytes, ganzzahlig) ─────────
# Verwendet integer-arithmetik um scientific notation issues zu vermeiden.
get_meminfo_kb() { awk -v key="^$1:" '$1 ~ key {print $2; exit}' /proc/meminfo; }

ram_total_kb=$(get_meminfo_kb MemTotal)
ram_available_kb=$(get_meminfo_kb MemAvailable)
swap_total_kb=$(get_meminfo_kb SwapTotal)
swap_free_kb=$(get_meminfo_kb SwapFree)
buffers_kb=$(get_meminfo_kb Buffers)
cached_kb=$(get_meminfo_kb Cached)

ram_total=$(( ram_total_kb * 1024 ))
ram_available=$(( ram_available_kb * 1024 ))
ram_used=$(( ram_total - ram_available ))
swap_total=$(( swap_total_kb * 1024 ))
swap_used=$(( (swap_total_kb - swap_free_kb) * 1024 ))

# Prozent-Berechnungen (integer-arithmetik in KB-Domain um overflow zu vermeiden)
ram_used_pct=$(( (ram_total_kb - ram_available_kb) * 100 / ram_total_kb ))
if [ "$swap_total_kb" -gt 0 ]; then
  swap_used_pct=$(( (swap_total_kb - swap_free_kb) * 100 / swap_total_kb ))
else
  swap_used_pct=0
fi

# ─── Druck-Check ───────────────────────────────────────
alarm=0
alarm_reasons=()
if [ "$ram_used_pct" -ge "$RAM_WARN_PCT" ]; then
  alarm=1
  alarm_reasons+=("RAM: ${ram_used_pct}% used (Schwelle ${RAM_WARN_PCT}%)")
fi
if [ "$swap_total" -gt 0 ] && [ "$swap_used_pct" -ge "$SWAP_WARN_PCT" ]; then
  alarm=1
  alarm_reasons+=("Swap: ${swap_used_pct}% used (Schwelle ${SWAP_WARN_PCT}%)")
fi

now_ts=$(date +%s)
now_iso=$(date -u +%Y-%m-%dT%H:%M:%SZ)

# ─── Throttling-Check ────────────────────────────────
should_alert=0
if [ "$alarm" -eq 1 ]; then
  if [ "$last_state" = "ok" ]; then
    # Übergang ok → alarm: immer alarmen
    should_alert=1
  elif [ -n "$last_alert_at" ]; then
    last_alert_ts=$(date -d "$last_alert_at" +%s 2>/dev/null || echo 0)
    elapsed=$(( now_ts - last_alert_ts ))
    if [ "$elapsed" -ge "$ALERT_THROTTLE_S" ]; then
      should_alert=1
    fi
  else
    should_alert=1
  fi
fi

# ─── Recovery-Alert ──────────────────────────────────
recovered=0
if [ "$alarm" -eq 0 ] && [ "$last_state" = "alarm" ]; then
  recovered=1
fi

# ─── Helpers für Discord-Embed ──────────────────────────
human_gb() {
  awk -v b="$1" 'BEGIN { printf "%.2f", b/1024/1024/1024 }'
}

send_alert() {
  local color="$1"        # 15158332=red, 16776960=yellow, 3066993=green
  local title="$2"
  local desc="$3"
  local fields_json="$4"

  local payload
  payload=$(jq -nc \
    --arg title "$title" \
    --arg desc "$desc" \
    --argjson color "$color" \
    --argjson fields "$fields_json" \
    --arg ts "$now_iso" \
    '{
      username: "ShadowOps Memory Watchdog",
      embeds: [{
        title: $title,
        description: $desc,
        color: $color,
        fields: $fields,
        footer: { text: "memory-watchdog auf VPS (10.8.0.1)" },
        timestamp: $ts
      }]
    }'
  )

  local http
  http=$(discord_post "$WEBHOOK_URL" "$payload")
  if [ "$http" = "204" ] || [ "$http" = "200" ]; then
    echo "[memory-watchdog] Alert gesendet (HTTP $http)"
    return 0
  else
    echo "[memory-watchdog] ERROR: Webhook fehlgeschlagen (HTTP $http)" >&2
    return 1
  fi
}

build_fields() {
  jq -nc \
    --arg ram "$(human_gb $ram_used) GB / $(human_gb $ram_total) GB (${ram_used_pct}%)" \
    --arg avail "$(human_gb $ram_available) GB" \
    --arg swap "$(human_gb $swap_used) GB / $(human_gb $swap_total) GB (${swap_used_pct}%)" \
    --arg load "$(awk '{print $1, $2, $3}' /proc/loadavg) (1/5/15m)" \
    --arg psi_mem "$(awk '/^some/{print $2,$3,$4}' /proc/pressure/memory 2>/dev/null || echo "n/a")" \
    '[
      { name: "RAM", value: $ram, inline: true },
      { name: "Available", value: $avail, inline: true },
      { name: "Swap", value: $swap, inline: true },
      { name: "Load", value: $load, inline: false },
      { name: "PSI Memory", value: $psi_mem, inline: false }
    ]'
}

# ─── Alert / Recovery senden ────────────────────────────
new_state="$last_state"
new_alert_at="$last_alert_at"
exit_code=0

if [ "$should_alert" -eq 1 ]; then
  reason_str=$(printf '%s\n' "${alarm_reasons[@]}" | jq -Rs .)
  if send_alert 15158332 "⚠️ Memory-Druck auf VPS!" \
    "$(printf '%s\n' "${alarm_reasons[@]}")" \
    "$(build_fields)"; then
    new_state="alarm"
    new_alert_at="$now_iso"
  else
    exit_code=1
  fi
elif [ "$recovered" -eq 1 ]; then
  if send_alert 3066993 "✅ Memory-Druck aufgelöst" \
    "RAM und Swap sind wieder unter den Schwellen ($RAM_WARN_PCT% / $SWAP_WARN_PCT%)" \
    "$(build_fields)"; then
    new_state="ok"
  else
    exit_code=1
  fi
elif [ "$alarm" -eq 0 ]; then
  new_state="ok"
fi

# ─── State persistieren ────────────────────────────────
jq -nc \
  --arg state "$new_state" \
  --arg alert_at "$new_alert_at" \
  --arg checked_at "$now_iso" \
  --argjson ram_pct "$ram_used_pct" \
  --argjson swap_pct "$swap_used_pct" \
  '{
    last_state: $state,
    last_alert_at: $alert_at,
    last_checked_at: $checked_at,
    last_ram_pct: $ram_pct,
    last_swap_pct: $swap_pct
  }' > "$STATE_FILE"

# Quiet-Log wenn alles ok (für cron-freundlichen Output)
if [ "$alarm" -eq 0 ] && [ "$recovered" -eq 0 ]; then
  echo "[memory-watchdog] OK — RAM ${ram_used_pct}%, Swap ${swap_used_pct}%"
fi

exit "$exit_code"
