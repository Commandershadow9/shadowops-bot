#!/usr/bin/env bash
# prozess-hygiene-watchdog.sh — raeumt verwaiste Prozesse toter Sitzungen ab.
#
# Anlass (2026-08-28): 152 Prozesse mit PPID 1 und ueber zwei Tagen Laufzeit
# belegten 4,1 GB RSS. Darunter vier Testprozesse, die seit dem 12.08. mit
# 0 s CPU-Zeit warteten, und 45 MCP-Server, deren Sitzung laengst beendet war.
# Ein MCP-Server bekommt EOF auf stdin, wenn sein Client stirbt, und sollte sich
# beenden — diese tun es nicht. Der Zufluss haelt also an, deshalb dieser Dienst.
#
# Stufe 1 (auto): SIGTERM an jede erkannte Waise, SIGKILL nur bei Verweigerung.
# Stufe 2 (alarm): Discord, wenn ein einzelner Lauf mehr als ALARM_AB Waisen
#                  findet — das deutet auf einen Client hin, der reihenweise
#                  Server zuruecklaesst, und ist mehr als normaler Verschleiss.
#
# ─────────────────────── SICHERHEIT ───────────────────────
# Beendet wird ausschliesslich, was auf die POSITIVLISTE passt. Alles andere
# bleibt unangetastet, auch mit PPID 1. Das ist der Kern dieses Skripts:
#
#   PPID 1 ist KEIN Beweis fuer "verwaist". `containerd-shim` traegt sie
#   bauartbedingt und gehoert zu einem LAUFENDEN Container; dasselbe gilt fuer
#   systemd, dockerd, crowdsec, sshd, die VS-Code-Server und jeden anderen
#   Systemdienst. Eine Fassung, die auf "PPID 1 + alt" allein loescht, haette am
#   28.08. zwanzig Container-Shims erwischt — also zwanzig Produktivcontainer.
#
# Zweite Sicherung: Mindestalter (Default 2 Tage). Alles Juengere koennte zu
# einer aktiven Sitzung gehoeren.
#
# Dritte Sicherung: Der PPID-Test trennt lebende von toten MCP-Servern
# zuverlaessig. Am 28.08. hatten 45 der 52 Codex-MCP-Server PPID 1, sieben einen
# lebenden Elternprozess — die sieben blieben korrekt stehen.
#
# Muster: scripts/disk-hygiene-watchdog.sh
# State:  data/watchdog_state_prozess-hygiene.json
#
# Aufruf: ohne Argument  = scharf (fuer den Timer)
#         --dry-run      = nur auflisten, nichts beenden
#
# Exit: 0 = ok, 2 = Konfigfehler
set -euo pipefail

MINDESTALTER_S="${PROZESS_MINDESTALTER_S:-172800}"   # 2 Tage
ALARM_AB="${PROZESS_ALARM_AB:-40}"                   # Waisen je Lauf bis Alarm
ALERT_THROTTLE_S="${ALERT_THROTTLE_S:-21600}"        # 6 h
STATE_FILE="${STATE_FILE:-/home/cmdshadow/shadowops-bot/data/watchdog_state_prozess-hygiene.json}"
WEBHOOK_CONFIG="${WEBHOOK_CONFIG:-/home/cmdshadow/.config/shadowops-watchdog.env}"

TROCKEN=0
[ "${1:-}" = "--dry-run" ] && TROCKEN=1

[ -f "$WEBHOOK_CONFIG" ] && source "$WEBHOOK_CONFIG"
WEBHOOK_URL="${PROZESS_HYGIENE_WEBHOOK:-${SHADOWOPS_WATCHDOG_WEBHOOK:-}}"
if [ -z "$WEBHOOK_URL" ] && [ "$TROCKEN" = "0" ]; then
  echo "[prozess-hygiene] ERROR: kein Webhook konfiguriert" >&2
  exit 2
fi
mkdir -p "$(dirname "$STATE_FILE")"

# shellcheck source=lib/discord-send.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/discord-send.sh" 2>/dev/null || true
# shellcheck source=lib/watchdog-report.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/watchdog-report.sh" 2>/dev/null || true
if ! declare -f discord_post >/dev/null 2>&1; then
  discord_post() { curl -sS -o /dev/null -w '%{http_code}' -X POST \
    -H 'Content-Type: application/json' --data "$2" --max-time 10 "$1" 2>/dev/null || echo 000; }
fi

now_ts=$(date +%s)
now_iso=$(date -u +%Y-%m-%dT%H:%M:%SZ)

# ─────────────────────── POSITIVLISTE ───────────────────────
# Nur wer hier passt, gilt als aufraeumbare Waise:
#   1. MCP-Server aus uv-Caches (docker-mcp, postgres-mcp, …) — der Sammelposten.
#   2. Testlaeufe, die ohne --test-timeout gestartet wurden und haengen bleiben.
#
# ⚠️ BEWUSST NICHT AUFGENOMMEN: die Log- und Datei-Follows der Claude-Monitore
# (`journalctl -u … -f`, `tail -f /tmp/claude-…`) samt ihren Shell-Huellen.
# Sie sehen wie Leichen aus, sind es aber oft nicht: Der Wrapper eines LAUFENDEN
# Monitors traegt ebenfalls PPID 1. Beim Abnahmetest am 28.08. stand der eigene,
# aktive Deploy-Monitor dieser Sitzung in der Trefferliste — nur das Mindestalter
# hielt ihn heraus. Ein Monitor mit `persistent: true` in einer mehrtaegigen
# Sitzung waere nach zwei Tagen abgeschossen worden.
#
# Fuer einen MCP-Server ist PPID 1 ein eindeutiges Todessignal (sein Client hat
# ihn gestartet und ist weg). Fuer einen Monitor-Wrapper ist es keins. Der Dienst
# raeumt deshalb nur ab, was er sicher entscheiden kann; die paar Monitor-Reste
# (rund 50 MB) bleiben liegen, statt ein Risiko fuer laufende Arbeit einzugehen.
MUSTER='(/(uv|uvx)(/| )|cache/archive-v0/.*/bin/[a-z-]+-mcp|[a-z-]+-mcp( |$)|(tsx|node) --test)'

# Harte Ausschluesse. Redundant zur Positivliste — genau so soll es sein:
# Wenn jemand das Muster spaeter weiter fasst, faengt diese Zeile den Fehler.
TABU='containerd|/lib/systemd|systemd-|unattended-upgrade|dockerd|crowdsec|sshd|/usr/sbin/|code-[0-9a-f]{8}|tmux'

gefunden=0
beendet=0
hartnaeckig=0
speicher_kb=0
beispiele=""

while read -r pid ppid alter rss rest; do
  [ "$ppid" = "1" ] || continue
  [ "$alter" -gt "$MINDESTALTER_S" ] || continue
  [[ "$rest" =~ $MUSTER ]] || continue
  [[ "$rest" =~ $TABU ]] && continue

  gefunden=$((gefunden + 1))
  speicher_kb=$((speicher_kb + rss))
  [ "$gefunden" -le 3 ] && beispiele="${beispiele}\`${rest:0:60}\` ($((alter / 86400))d)\n"

  [ "$TROCKEN" = "1" ] && { echo "wuerde beenden: $pid ($((alter / 86400))d) ${rest:0:70}"; continue; }

  # Ein angehaltener Prozess (Status T) ignoriert SIGTERM, bis er fortgesetzt
  # wird — am 28.08. hing genau daran der letzte Ueberlebende.
  kill -CONT "$pid" 2>/dev/null || true
  kill -TERM "$pid" 2>/dev/null || true
  beendet=$((beendet + 1))
  merkliste="${merkliste:-} $pid"
done < <(ps -eo pid=,ppid=,etimes=,rss=,args= 2>/dev/null)

# Nachfassen: Wer SIGTERM nach 5 s ignoriert, bekommt SIGKILL. Bewusst getrennt,
# damit ein sauberer Abgang die Regel bleibt und der Holzhammer die Ausnahme.
if [ "$TROCKEN" = "0" ] && [ -n "${merkliste:-}" ]; then
  sleep 5
  for pid in $merkliste; do
    if kill -0 "$pid" 2>/dev/null; then
      kill -KILL "$pid" 2>/dev/null || true
      hartnaeckig=$((hartnaeckig + 1))
    fi
  done
fi

speicher_mb=$((speicher_kb / 1024))

if [ "$TROCKEN" = "1" ]; then
  echo "--- Trockenlauf: $gefunden Waisen, ${speicher_mb} MB (nichts beendet) ---"
  exit 0
fi

echo "[prozess-hygiene] $gefunden Waisen gefunden, $beendet beendet (${hartnaeckig}× SIGKILL), ${speicher_mb} MB frei"

# ─────────────────────── Stufe 2: Alarm ───────────────────────
# Aufgeraeumt wird immer; gemeldet nur, wenn die Menge auffaellt. Ein Dienst,
# der jeden Lauf meldet, wird zu Tapete — dieselbe Lehre wie beim
# nightly-suite-watchdog.
letzter_alarm=0
[ -f "$STATE_FILE" ] && letzter_alarm=$(grep -o '"letzter_alarm_ts":[0-9]*' "$STATE_FILE" 2>/dev/null | cut -d: -f2 || echo 0)
: "${letzter_alarm:=0}"

befund="ok"
detail="${gefunden} Waisen, ${speicher_mb} MB"

if [ "$gefunden" -ge "$ALARM_AB" ]; then
  befund="auffaellig"
  if [ $((now_ts - letzter_alarm)) -ge "$ALERT_THROTTLE_S" ]; then
    nachricht=$(printf '%b' "In einem Lauf wurden **%d verwaiste Prozesse** aufgeraeumt (%d MB).\nDas liegt ueber der Schwelle von %d und deutet auf einen Client, der Server reihenweise zuruecklaässt.\n\n%s" \
      "$gefunden" "$speicher_mb" "$ALARM_AB" "$beispiele")
    payload=$(jq -nc --arg t "🧹 Ungewoehnlich viele verwaiste Prozesse" --arg d "$nachricht" \
      '{embeds:[{title:$t,description:$d,color:16098851}]}' 2>/dev/null) || payload=""
    [ -n "$payload" ] && discord_post "$WEBHOOK_URL" "$payload" >/dev/null
    letzter_alarm=$now_ts
  fi
fi

printf '{"letzter_lauf":"%s","gefunden":%d,"beendet":%d,"sigkill":%d,"speicher_mb":%d,"letzter_alarm_ts":%d}\n' \
  "$now_iso" "$gefunden" "$beendet" "$hartnaeckig" "$speicher_mb" "$letzter_alarm" > "$STATE_FILE"

melde_befund "prozess-hygiene" "$befund" "$detail"
exit 0
