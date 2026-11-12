#!/usr/bin/env bash
#
# notfall-probelauf.sh — prüft, dass die Notfall-Kette nicht still verrottet (ZERODOX #5627, Stufe 4).
#
#   --pruefen  (Standard, monatlich per Timer) — OHNE Ausfall:
#       ① Heiler im Trockenlauf  ② Watchdog→Heilbefehl-Kette gegen eine absichtlich rote URL
#       ③ HEL1: Wächter läuft + Lebenszeichen frisch  ④ HEL1→Wrapper (hilfe) über den Notfall-Schlüssel
#       ⑤ HEL1: Claude-Token vorhanden  ⑥ ntfy-Testnachricht (leise)
#   --echt     — MIT kurzem Ausfall, nur tagsüber mit Betreiber am Rechner:
#       stoppt traefik-dockerproxy, lässt den Heiler laufen, misst die Zeit bis alles grün ist.
#
# Ergebnis nach Discord (#🩺-uptime-alerts). Exit 0 = alles grün.
set -uo pipefail
MODUS="${1:---pruefen}"
HIER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
[ -r "$HOME/.config/shadowops-watchdog.env" ] && . "$HOME/.config/shadowops-watchdog.env"
WEBHOOK="${SHADOWOPS_WATCHDOG_WEBHOOK:-}"
HEL1="${NOTFALL_HEL1:-zerodox-ci-hel1}"
ok=0; rot=0; zeilen=()
pruef() { if [ "$1" = 0 ]; then ok=$((ok + 1)); zeilen+=("✅ $2"); else rot=$((rot + 1)); zeilen+=("❌ $2"); fi; }

if [ "$MODUS" = "--echt" ]; then
  h="$(date +%H)"; if [ "$h" -lt 8 ] || [ "$h" -ge 20 ]; then echo "--echt nur 08–20 Uhr"; exit 64; fi
  echo "Stoppe traefik-dockerproxy …"; t0=$(date +%s)
  sg docker -c "docker stop -t 5 traefik-dockerproxy" >/dev/null
  out="$(sg docker -c "$HIER/notfall-heiler.sh" 2>&1)"; rc=$?
  dauer=$(( $(date +%s) - t0 ))
  echo "$out"
  pruef "$rc" "Echtlauf: Proxy gestoppt → Heiler → Außenprüfung grün nach ${dauer}s"
else
  out="$(TROCKEN=1 sg docker -c "$HIER/notfall-heiler.sh" 2>&1)"; pruef $? "① Heiler-Trockenlauf, Außenprüfung grün"

  tmp="$(mktemp -d)"; marker="$tmp/geheilt"
  echo '{"last_status":"up","last_alert_at":"","consecutive_failures":1,"down_since":""}' >"$tmp/s.json"
  WATCHDOG_SERVICE_NAME="notfall-probelauf" WATCHDOG_STATE_FILE="$tmp/s.json" WATCHDOG_WEBHOOK="" \
    WATCHDOG_HEALTH_URL="https://zerodox.de/notfall-probelauf-absichtlich-404" WATCHDOG_REQUIRE_BOT_READY=0 \
    ZERODOX_AGENT_API_KEY="" WATCHDOG_TAKT_SEK=300 WATCHDOG_HEAL_CMD="touch $marker" \
    "$HIER/../service-watchdog.sh" >/dev/null 2>&1
  [ -f "$marker" ]; pruef $? "② Watchdog löst bei 2. Down-Lauf den Heilbefehl aus"; rm -rf "$tmp"

  hel="$(timeout 40 ssh -o BatchMode=yes -o ConnectTimeout=8 "$HEL1" \
    'systemctl is-active notfall-waechter; echo $(( $(date +%s) - $(cat /var/lib/notfall/letzter_lauf 2>/dev/null || echo 0) )); sudo -u notfall /opt/notfall/notfall-ssh hilfe 2>&1 | head -1; grep -c "^CLAUDE_CODE_OAUTH_TOKEN=." /etc/notfall/claude.env; grep -c "^NTFY_TOPIC=." /etc/notfall/notfall.env; grep -c "^ROBOT_SERVER_NUMBER=." /etc/notfall/notfall.env' 2>&1)"
  mapfile -t h <<<"$hel"
  [ "${h[0]:-}" = active ]; pruef $? "③ HEL1: notfall-waechter aktiv"
  [ "${h[1]:-999}" -lt 180 ] 2>/dev/null; pruef $? "③ HEL1: letzter Wächterlauf vor ${h[1]:-?}s"
  [[ "${h[2]:-}" == LESEN* ]]; pruef $? "④ HEL1 → Hauptserver: Wrapper antwortet"
  [ "${h[3]:-0}" = 1 ]; pruef $? "⑤ HEL1: Claude-Token hinterlegt"
  [ "${h[4]:-0}" = 1 ]; pruef $? "⑥ HEL1: ntfy-Topic hinterlegt"
  [ "${h[5]:-0}" = 1 ]; pruef $? "⑥ HEL1: Robot-Zugang hinterlegt (Auto-Reset scharf)"
fi

text="$(printf '%s\n' "${zeilen[@]}")"
echo "$text"
if [ -n "$WEBHOOK" ]; then
  titel="🧯 Notfall-Probelauf ($MODUS): $([ $rot -eq 0 ] && echo 'alles grün' || echo "$rot rot")"
  curl -s -m 10 -H 'Content-Type: application/json' -X POST "$WEBHOOK" \
    -d "$(jq -n --arg t "**$titel**
$text
$([ "$MODUS" = --pruefen ] && echo '_Echtlauf (Proxy stoppen) bitte tagsüber von Hand: `scripts/notfall/notfall-probelauf.sh --echt`_')" '{content:$t}')" >/dev/null || true
fi
[ $rot -eq 0 ]
