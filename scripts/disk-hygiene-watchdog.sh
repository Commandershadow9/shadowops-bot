#!/usr/bin/env bash
# disk-hygiene-watchdog.sh — Hybrid-Stufen Disk-Pflege.
#
# Stufe 1 (auto) bei Disk >= DISK_WARN_PCT: docker builder/image prune + journald vacuum.
# Stufe 2 (alarm) bei Disk >= DISK_CRIT_PCT NACH Prune: Discord-Alarm mit Top-Verbrauchern.
#
# Sicherheit: rührt AUSSCHLIESSLICH Docker-Cache/dangling-Images + journald an.
# Niemals Volumes, Projektordner, /srv/vault, .env, Worktrees.
#
# Muster: scripts/memory-watchdog.sh. State: data/watchdog_state_disk-hygiene.json
# Webhook-Config: ~/.config/shadowops-watchdog.env (Fallback auf SHADOWOPS_WATCHDOG_WEBHOOK)
#
# Exit: 0 = ok/Aktion erfolgreich, 2 = Konfigfehler
set -euo pipefail

WARN_PCT="${DISK_WARN_PCT:-85}"
CRIT_PCT="${DISK_CRIT_PCT:-90}"
MOUNT="${DISK_MOUNT:-/}"
JOURNAL_CAP="${JOURNAL_CAP:-500M}"
ALERT_THROTTLE_S="${ALERT_THROTTLE_S:-3600}"
STATE_FILE="${STATE_FILE:-/home/cmdshadow/shadowops-bot/data/watchdog_state_disk-hygiene.json}"
WEBHOOK_CONFIG="${WEBHOOK_CONFIG:-/home/cmdshadow/.config/shadowops-watchdog.env}"

[ -f "$WEBHOOK_CONFIG" ] && source "$WEBHOOK_CONFIG"
WEBHOOK_URL="${DISK_HYGIENE_WEBHOOK:-${SHADOWOPS_WATCHDOG_WEBHOOK:-}}"
if [ -z "$WEBHOOK_URL" ]; then
  echo "[disk-hygiene] ERROR: kein Webhook konfiguriert" >&2
  exit 2
fi
mkdir -p "$(dirname "$STATE_FILE")"

now_iso=$(date -u +%Y-%m-%dT%H:%M:%SZ)
now_ts=$(date +%s)
disk_pct() { df --output=pcent "$MOUNT" | tail -1 | tr -dc '0-9'; }
pct_before=$(disk_pct)

last_alert_at=""
if [ -f "$STATE_FILE" ]; then
  last_alert_at=$(jq -r '.last_alert_at // ""' "$STATE_FILE" 2>/dev/null || echo "")
fi

send_alert() {  # color title desc fields_json
  local color="$1" title="$2" desc="$3" fields="$4" payload http
  payload=$(jq -nc --arg t "$title" --arg d "$desc" --argjson c "$color" \
    --argjson f "$fields" --arg ts "$now_iso" \
    '{username:"ShadowOps Disk-Hygiene Watchdog",
      embeds:[{title:$t,description:$d,color:$c,fields:$f,
      footer:{text:"disk-hygiene-watchdog auf VPS (10.8.0.1)"},timestamp:$ts}]}')
  http=$(curl -sS -o /dev/null -w "%{http_code}" -X POST -H "Content-Type: application/json" \
    --data "$payload" --max-time 10 "$WEBHOOK_URL" 2>/dev/null || echo 000)
  [ "$http" = "204" ] || [ "$http" = "200" ]
}

freed_note="keine Aktion noetig"
if [ "$pct_before" -ge "$WARN_PCT" ]; then
  # Stufe 1: sichere Auto-Bereinigung
  bc=$(docker builder prune -f 2>/dev/null | awk '/Total:/{print $2}' || echo "0")
  docker image prune -f >/dev/null 2>&1 || true
  journalctl --vacuum-size="$JOURNAL_CAP" >/dev/null 2>&1 || true
  pct_after=$(disk_pct)
  freed_note="builder-cache: ${bc:-0}, Disk ${pct_before}% -> ${pct_after}%"
  echo "[disk-hygiene] Auto-Prune: $freed_note"
else
  pct_after="$pct_before"
fi

# Stufe 2: Alarm nur wenn nach Prune weiterhin kritisch (throttled)
should_alert=0
if [ "$pct_after" -ge "$CRIT_PCT" ]; then
  if [ -n "$last_alert_at" ]; then
    elapsed=$(( now_ts - $(date -d "$last_alert_at" +%s 2>/dev/null || echo 0) ))
    [ "$elapsed" -ge "$ALERT_THROTTLE_S" ] && should_alert=1
  else
    should_alert=1
  fi
fi

new_alert_at="$last_alert_at"
if [ "$should_alert" -eq 1 ]; then
  # || true: du liefert non-zero (Permission-Fehler + SIGPIPE durch head) — unter
  # set -e+pipefail würde das sonst das Script killen BEVOR der Alarm gesendet wird.
  top=$(du -xh "$MOUNT" 2>/dev/null | sort -rh | head -6 | awk '{printf "%s  %s\n",$1,$2}' || true)
  fields=$(jq -nc --arg p "${pct_after}% (Schwelle ${CRIT_PCT}%)" --arg pr "$freed_note" \
    --arg top "$top" \
    '[{name:"Disk nach Auto-Prune",value:$p,inline:false},
      {name:"Auto-Aktion",value:$pr,inline:false},
      {name:"Top-Verbraucher",value:("```\n"+$top+"```"),inline:false}]')
  if send_alert 15158332 "🔴 Disk weiterhin kritisch nach Auto-Prune" \
    "Manueller Eingriff noetig — Auto-Bereinigung hat nicht gereicht." "$fields"; then
    new_alert_at="$now_iso"
  else
    echo "[disk-hygiene] ERROR: Webhook fehlgeschlagen" >&2
  fi
elif [ "$pct_before" -ge "$WARN_PCT" ] && [ "$pct_after" -lt "$CRIT_PCT" ]; then
  # Stufe 1 hat gereicht -> Info-Notiz
  send_alert 3066993 "🧹 Disk automatisch bereinigt" "$freed_note" '[]' || true
fi

jq -nc --arg a "$new_alert_at" --arg c "$now_iso" --argjson pb "$pct_before" --argjson pa "$pct_after" \
  '{last_alert_at:$a,last_checked_at:$c,pct_before:$pb,pct_after:$pa}' > "$STATE_FILE"

[ "$pct_after" -lt "$WARN_PCT" ] && echo "[disk-hygiene] OK — Disk ${pct_after}%"
exit 0
