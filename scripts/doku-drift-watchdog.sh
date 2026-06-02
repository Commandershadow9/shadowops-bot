#!/usr/bin/env bash
# doku-drift-watchdog.sh — billige deterministische Frühwarnung vor Doku-Drift.
#
# Prüft: (a) laufende Container-Host-Ports vs. Port-Map in CLAUDE.md/infrastructure.md
#        (b) MEMORY.md-Zeilenzahl gegen Limit
# NUR Alarm (Doku-Änderung ist Graubereich → Mensch/Claude entscheidet).
# Komplementär zum KI-getriebenen Doku-Kurator: dies ist die token-freie Frühwarnung.
#
# Muster: scripts/memory-watchdog.sh. State: data/watchdog_state_doku-drift.json
#
# WICHTIG (Lektion disk-hygiene): unter set -e+pipefail killt eine Pipe mit
# non-zero/SIGPIPE die Command-Substitution → riskante Pipes mit || true absichern.
set -euo pipefail

MEMORY_FILE="${MEMORY_FILE:-/home/cmdshadow/.claude/projects/-home-cmdshadow/memory/MEMORY.md}"
MEMORY_LIMIT="${MEMORY_LIMIT:-200}"
PORTMAP_FILES="${PORTMAP_FILES:-/home/cmdshadow/CLAUDE.md /home/cmdshadow/.claude/rules/infrastructure.md}"
ALERT_THROTTLE_S="${ALERT_THROTTLE_S:-86400}"   # max 1 Alarm/Tag bei gleichem Drift
STATE_FILE="${STATE_FILE:-/home/cmdshadow/shadowops-bot/data/watchdog_state_doku-drift.json}"
WEBHOOK_CONFIG="${WEBHOOK_CONFIG:-/home/cmdshadow/.config/shadowops-watchdog.env}"

[ -f "$WEBHOOK_CONFIG" ] && source "$WEBHOOK_CONFIG"
WEBHOOK_URL="${DOKU_DRIFT_WEBHOOK:-${SHADOWOPS_WATCHDOG_WEBHOOK:-}}"
if [ -z "$WEBHOOK_URL" ]; then
  echo "[doku-drift] ERROR: kein Webhook konfiguriert" >&2
  exit 2
fi
mkdir -p "$(dirname "$STATE_FILE")"
now_iso=$(date -u +%Y-%m-%dT%H:%M:%SZ)
now_ts=$(date +%s)

# Geteilte Discord-Send-Lib mit 429-Resilienz (#293). Fallback = altes Inline-Curl.
# shellcheck source=lib/discord-send.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/discord-send.sh" 2>/dev/null || true
if ! declare -f discord_post >/dev/null 2>&1; then
  discord_post() { curl -sS -o /dev/null -w '%{http_code}' -X POST \
    -H 'Content-Type: application/json' --data "$2" --max-time 10 "$1" 2>/dev/null || echo 000; }
fi

drift_lines=()

# (a) Container-Host-Ports, die in keiner Port-Map-Datei stehen.
# docker ps via process-substitution (kein pipefail-Risiko im while).
while read -r name ports; do
  [ -z "$name" ] && continue
  # Host-Ports extrahieren (Muster ":NNNN->" oder "NNNN->"); grep kann exit 1 → || true
  hostports=$(echo "$ports" | grep -oE '[0-9]{2,5}->' | tr -d '>-' | sort -u || true)
  for hp in $hostports; do
    if ! grep -qE "\b${hp}\b" $PORTMAP_FILES 2>/dev/null; then
      drift_lines+=("Container ${name}: Host-Port ${hp} fehlt in Port-Map")
    fi
  done
done < <(docker ps --format '{{.Names}} {{.Ports}}' 2>/dev/null || true)

# (b) MEMORY.md über Limit
if [ -f "$MEMORY_FILE" ]; then
  ml=$(wc -l < "$MEMORY_FILE" 2>/dev/null || echo 0)
  if [ "$ml" -gt "$MEMORY_LIMIT" ]; then
    drift_lines+=("MEMORY.md = ${ml} Zeilen > Limit ${MEMORY_LIMIT}")
  fi
fi

# Fingerprint für Throttle (gleicher Drift → nicht erneut alarmen)
fp=$(printf '%s\n' "${drift_lines[@]:-}" | sha256sum | cut -d' ' -f1)
last_fp=""; last_alert_at=""
if [ -f "$STATE_FILE" ]; then
  last_fp=$(jq -r '.fingerprint // ""' "$STATE_FILE" 2>/dev/null || echo "")
  last_alert_at=$(jq -r '.last_alert_at // ""' "$STATE_FILE" 2>/dev/null || echo "")
fi

send_alert() {  # title desc
  local title="$1" desc="$2" payload http
  payload=$(jq -nc --arg t "$title" --arg d "$desc" --arg ts "$now_iso" \
    '{username:"ShadowOps Doku-Drift Watchdog",
      embeds:[{title:$t,description:$d,color:16776960,
      footer:{text:"doku-drift-watchdog auf VPS (10.8.0.1)"},timestamp:$ts}]}')
  http=$(discord_post "$WEBHOOK_URL" "$payload")
  [ "$http" = "204" ] || [ "$http" = "200" ]
}

new_alert_at="$last_alert_at"
if [ "${#drift_lines[@]}" -gt 0 ]; then
  throttled=0
  if [ "$fp" = "$last_fp" ] && [ -n "$last_alert_at" ]; then
    elapsed=$(( now_ts - $(date -d "$last_alert_at" +%s 2>/dev/null || echo 0) ))
    [ "$elapsed" -lt "$ALERT_THROTTLE_S" ] && throttled=1
  fi
  if [ "$throttled" -eq 0 ]; then
    desc="$(printf '• %s\n' "${drift_lines[@]}")"$'\n'"_Bitte Port-Map / MEMORY.md aktualisieren._"
    if send_alert "📋 Doku-Drift erkannt" "$desc"; then
      new_alert_at="$now_iso"
    else
      echo "[doku-drift] ERROR: Webhook fehlgeschlagen" >&2
    fi
  fi
  echo "[doku-drift] ${#drift_lines[@]} Drift(s) erkannt"
else
  echo "[doku-drift] OK — keine Abweichung"
fi

jq -nc --arg fp "$fp" --arg a "$new_alert_at" --arg c "$now_iso" --argjson n "${#drift_lines[@]}" \
  '{fingerprint:$fp,last_alert_at:$a,last_checked_at:$c,drift_count:$n}' > "$STATE_FILE"
exit 0
