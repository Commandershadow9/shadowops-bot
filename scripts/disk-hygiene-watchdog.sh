#!/usr/bin/env bash
# disk-hygiene-watchdog.sh — Hybrid-Stufen Disk-Pflege.
#
# Stufe 1 (auto) bei Disk >= DISK_WARN_PCT: docker builder/image prune + journald vacuum.
# Stufe 2 (alarm) bei Disk >= DISK_CRIT_PCT NACH Prune: Discord-Alarm mit Top-Verbrauchern.
#
# Sicherheit: rührt AUSSCHLIESSLICH Docker-Cache/dangling-Images + journald an.
# Niemals Volumes, Projektordner, /srv/vault, .env, Worktrees.
# Prune bleibt dangling-only (`-f`, NIE `-a`/`-af`): schuetzt u.a. das getaggte
# ZERODOX-Rollback-Image `zerodox-zerodox-web:rollback` (#1186) — ein `-a`-Prune
# wuerde es entfernen und den Auto-Rollback-Pfad toeten.
#
# Muster: scripts/memory-watchdog.sh. State: data/watchdog_state_disk-hygiene.json
# Webhook-Config: ~/.config/shadowops-watchdog.env (Fallback auf SHADOWOPS_WATCHDOG_WEBHOOK)
#
# Exit: 0 = ok/Aktion erfolgreich, 2 = Konfigfehler
set -euo pipefail

WARN_PCT="${DISK_WARN_PCT:-85}"
CRIT_PCT="${DISK_CRIT_PCT:-90}"
MOUNT="${DISK_MOUNT:-/}"
# Zusaetzlich beobachtete Einhaengepunkte, durch Leerzeichen getrennt.
# /tmp ist eine tmpfs und liegt damit im RAM — volllaufen kostet hier zweierlei:
# Platz UND Arbeitsspeicher. Fuer diese Punkte wird AUSSCHLIESSLICH gewarnt, nie
# automatisch geloescht: Was dort liegt, gehoert fremden Prozessen.
EXTRA_MOUNTS="${DISK_EXTRA_MOUNTS:-/tmp}"
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

# Geteilte Discord-Send-Lib mit 429-Resilienz (#293). Fallback = altes Inline-Curl.
# shellcheck source=lib/discord-send.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/discord-send.sh" 2>/dev/null || true
if ! declare -f discord_post >/dev/null 2>&1; then
  discord_post() { curl -sS -o /dev/null -w '%{http_code}' -X POST \
    -H 'Content-Type: application/json' --data "$2" --max-time 10 "$1" 2>/dev/null || echo 000; }
fi

now_iso=$(date -u +%Y-%m-%dT%H:%M:%SZ)
now_ts=$(date +%s)
disk_pct() { df --output=pcent "${1:-$MOUNT}" | tail -1 | tr -dc '0-9'; }

# Inode-Erschoepfung ist an `df -h` NICHT zu erkennen. Am 08.08.2026 stand /tmp
# bei 100 % Inodes (3.755 von 1.048.576 frei) und meldete gleichzeitig 6,8 GB
# freien Platz. Jeder Vorgang, der viele kleine Dateien anlegt — ein git-Worktree,
# ein npm-Install, ein Build — scheiterte mit „No space left on device", waehrend
# dieser Watchdog „OK — Disk 42 %" meldete. Bytes und Inodes sind zwei getrennte
# Vorraete: Wer nur einen misst, sieht die Haelfte und haelt sie fuer das Ganze.
inode_pct() { df --output=ipcent "${1:-$MOUNT}" 2>/dev/null | tail -1 | tr -dc '0-9'; }

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
  http=$(discord_post "$WEBHOOK_URL" "$payload")
  [ "$http" = "204" ] || [ "$http" = "200" ]
}

freed_note="keine Aktion noetig"
if [ "$pct_before" -ge "$WARN_PCT" ]; then
  # Stufe 1: sichere Auto-Bereinigung.
  #
  # ⚠ INVARIANTE (#1186, ZERODOX-Rollback-Schutz): `docker image prune` MUSS
  # dangling-only bleiben (`-f`, NIEMALS `-a`/`-af`). ZERODOX taggt sein Vorgaenger-
  # Image als `zerodox-zerodox-web:rollback`, damit es nach einem Deploy nicht
  # dangling wird und der Auto-Rollback-Pfad funktioniert. Ein `-a`-Prune wuerde
  # dieses ungenutzte, aber GETAGGTE Image entfernen → Rollback tot genau dann,
  # wenn er gebraucht wird.
  rollback_tag_before=0
  docker image inspect zerodox-zerodox-web:rollback >/dev/null 2>&1 && rollback_tag_before=1

  bc=$(docker builder prune -f 2>/dev/null | awk '/Total:/{print $2}' || echo "0")
  docker image prune -f >/dev/null 2>&1 || true
  journalctl --vacuum-size="$JOURNAL_CAP" >/dev/null 2>&1 || true
  pct_after=$(disk_pct)
  freed_note="builder-cache: ${bc:-0}, Disk ${pct_before}% -> ${pct_after}%"
  echo "[disk-hygiene] Auto-Prune: $freed_note"

  # Defense-in-depth-Tripwire: war der :rollback-Tag VOR dem Prune da und ist
  # danach weg, hat der Prune ein GETAGGTES Image entfernt — das darf dangling-only
  # NIE. Signalisiert, dass die Prune-Semantik gekippt ist (versehentlich `-a`?).
  if [ "$rollback_tag_before" = "1" ] && ! docker image inspect zerodox-zerodox-web:rollback >/dev/null 2>&1; then
    echo "[disk-hygiene] WARN: zerodox-zerodox-web:rollback wurde vom Prune entfernt — Prune ist NICHT mehr dangling-only (ZERODOX-Rollback-Schutz #1186 verletzt)!" >&2
  fi
else
  pct_after="$pct_before"
fi

# Zusatzbefunde: Inodes auf dem Hauptmount, Bytes UND Inodes auf den
# Zusatz-Mounts. Bewusst NACH dem Auto-Prune — ein Docker-Prune gibt auch Inodes
# frei, es waere unehrlich, den Zustand davor zu melden.
#
# Alle Vergleiche stehen in `if`-Bloecken statt als `[ … ] && …`: Unter
# `set -e` ist eine falsche Testbedingung als letzter Befehl ein Skript-Abbruch —
# der Watchdog wuerde stumm enden, statt zu alarmieren.
extra_findings=""
add_finding() {
  if [ -n "$extra_findings" ]; then extra_findings="${extra_findings}"$'\n'"$1"; else extra_findings="$1"; fi
}

ipct_main=$(inode_pct "$MOUNT" || echo "")
if [ -n "$ipct_main" ] && [ "$ipct_main" -ge "$CRIT_PCT" ]; then
  add_finding "${MOUNT} — Inodes ${ipct_main}% (Schwelle ${CRIT_PCT}%)"
fi

ipct_tmp=""
for m in $EXTRA_MOUNTS; do
  if [ ! -d "$m" ]; then continue; fi
  m_bytes=$(disk_pct "$m" || echo "")
  m_inodes=$(inode_pct "$m" || echo "")
  if [ "$m" = "/tmp" ]; then ipct_tmp="$m_inodes"; fi
  if [ -n "$m_bytes" ] && [ "$m_bytes" -ge "$CRIT_PCT" ]; then
    add_finding "${m} — belegt ${m_bytes}% (Schwelle ${CRIT_PCT}%)"
  fi
  if [ -n "$m_inodes" ] && [ "$m_inodes" -ge "$CRIT_PCT" ]; then
    add_finding "${m} — Inodes ${m_inodes}% (Schwelle ${CRIT_PCT}%), Platz sagt ${m_bytes:-?}%"
  fi
done

# Stufe 2: Alarm nur wenn nach Prune weiterhin kritisch (throttled)
should_alert=0
if [ "$pct_after" -ge "$CRIT_PCT" ] || [ -n "$extra_findings" ]; then
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
  # Top-Verbraucher dort zeigen, wo es brennt: Bei einem reinen Zusatzbefund ist
  # eine Liste der groessten Ordner unter / nutzlos — der Platz fehlt woanders.
  top_mount="$MOUNT"
  if [ "$pct_after" -lt "$CRIT_PCT" ] && [ -n "$extra_findings" ]; then
    top_mount=$(printf '%s\n' "$extra_findings" | head -1 | awk '{print $1}')
    if [ ! -d "$top_mount" ]; then top_mount="$MOUNT"; fi
  fi
  top=$(du -xh "$top_mount" 2>/dev/null | sort -rh | head -6 | awk '{printf "%s  %s\n",$1,$2}' || true)

  if [ "$pct_after" -ge "$CRIT_PCT" ]; then
    alert_title="🔴 Disk weiterhin kritisch nach Auto-Prune"
    alert_desc="Manueller Eingriff noetig — Auto-Bereinigung hat nicht gereicht."
  else
    # Auto-Prune hilft hier nicht: Er raeumt Docker-Cache und journald, nicht /tmp.
    alert_title="🔴 Speicher-Engpass ausserhalb des Auto-Prune"
    alert_desc="Manueller Eingriff noetig. Der Auto-Prune raeumt nur Docker-Cache und journald — dieser Befund liegt ausserhalb seiner Reichweite."
  fi

  fields=$(jq -nc --arg p "${pct_after}% (Schwelle ${CRIT_PCT}%)" --arg pr "$freed_note" \
    --arg top "$top" --arg tm "$top_mount" \
    --arg extra "${extra_findings:-—}" \
    '[{name:"Disk nach Auto-Prune",value:$p,inline:false},
      {name:"Auto-Aktion",value:$pr,inline:false},
      {name:"Weitere Befunde",value:("```\n"+$extra+"```"),inline:false},
      {name:("Top-Verbraucher in "+$tm),value:("```\n"+$top+"```"),inline:false}]')
  if send_alert 15158332 "$alert_title" "$alert_desc" "$fields"; then
    new_alert_at="$now_iso"
  else
    echo "[disk-hygiene] ERROR: Webhook fehlgeschlagen" >&2
  fi
elif [ "$pct_before" -ge "$WARN_PCT" ] && [ "$pct_after" -lt "$CRIT_PCT" ]; then
  # Stufe 1 hat gereicht -> Info-Notiz
  send_alert 3066993 "🧹 Disk automatisch bereinigt" "$freed_note" '[]' || true
fi

jq -nc --arg a "$new_alert_at" --arg c "$now_iso" --argjson pb "$pct_before" --argjson pa "$pct_after" \
  --argjson im "${ipct_main:-null}" --argjson it "${ipct_tmp:-null}" \
  '{last_alert_at:$a,last_checked_at:$c,pct_before:$pb,pct_after:$pa,
    inode_pct_main:$im,inode_pct_tmp:$it}' > "$STATE_FILE"

# Die Zeile nennt Inodes ausdruecklich mit. „OK — Disk 42 %" allein hat am
# 08.08.2026 einen Mount bei 100 % Inodes ueberdeckt: Sie war wahr und trotzdem
# irrefuehrend, weil sie den zweiten Vorrat verschwieg.
if [ "$pct_after" -lt "$WARN_PCT" ] && [ -z "$extra_findings" ]; then
  echo "[disk-hygiene] OK — Disk ${pct_after}%, Inodes ${MOUNT} ${ipct_main:-?}%, /tmp ${ipct_tmp:-n/a}%"
fi
exit 0
