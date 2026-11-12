#!/usr/bin/env bash
#
# notfall-waechter.sh — Außenprüfung des Hauptservers von HEL1 aus (ZERODOX #5627, Stufen 2+3).
#
# „Der Retter wohnt nicht auf dem Patienten." Läuft als Dauerdienst (Schleife, 60 s) unter dem
# unprivilegierten Benutzer `notfall` auf HEL1 (10.8.0.12).
#
# Zustände je Lauf:
#   OK    alle Domains 2xx/3xx
#   TEIL  mind. eine Domain rot, Server aber per Ping erreichbar (Software-Ausfall)
#   TOT   alle Domains rot UND weder WireGuard (10.8.0.1) noch öffentliche IP pingbar
#   BLIND HEL1 selbst kommt nicht ins Internet → nichts schließen, nichts tun
#
# Eskalation je Vorfall:
#   TEIL ≥ 10 min (und Stufe 1 hat geheilt ODER ≥ 15 min) → ntfy + Notfall-Agent (1× je Vorfall)
#   TEIL hält nach dem Agenten an                         → ntfy dringend
#   TOT  ≥ 10 min                                         → ntfy dringend mit Knopf „Ich übernehme"
#   TOT  ≥ 10 min nach dem Alarm, kein Knopfdruck         → Hetzner-Robot-Hardware-Reset (1× je Vorfall)
#   wieder OK                                             → ntfy + Discord „wieder da"
#
# Konfiguration /etc/notfall/notfall.env (root:notfall 0640):
#   NTFY_TOPIC, DISCORD_WEBHOOK, ROBOT_USER, ROBOT_PASS, ROBOT_SERVER_NUMBER
# Fehlen die Robot-Werte, wird nur alarmiert (Reset-Stufe meldet das ausdrücklich).
set -uo pipefail

# shellcheck disable=SC1091
[ -r /etc/notfall/notfall.env ] && . /etc/notfall/notfall.env
STATE="${NOTFALL_STATE:-/var/lib/notfall}"
BASIS="${NOTFALL_BASIS:-/opt/notfall}"
TAKT_S="${NOTFALL_TAKT_S:-60}"
HAUPT_WG="${HAUPT_WG:-10.8.0.1}"
HAUPT_PUB="${HAUPT_PUB:-88.99.160.210}"
DOMAINS="${NOTFALL_DOMAINS:-https://zerodox.de/api/health https://mcp.zerodox.de/.well-known/oauth-authorization-server https://guildscout.eu https://maydaysim.de}"
REFERENZ="${NOTFALL_REFERENZ:-https://ntfy.sh/v1/health https://www.hetzner.com}"
TEIL_AGENT_S="${TEIL_AGENT_S:-600}"
TEIL_AGENT_SPAET_S="${TEIL_AGENT_SPAET_S:-900}"
TOT_ALARM_S="${TOT_ALARM_S:-600}"
RESET_NACH_ALARM_S="${RESET_NACH_ALARM_S:-600}"
EINMAL="${NOTFALL_EINMAL:-0}"   # 1 = ein Lauf (Tests)
NTFY="${NTFY_URL:-https://ntfy.sh}"
mkdir -p "$STATE"

log() { echo "[notfall-waechter] $*"; }
jetzt() { date +%s; }
lies() { cat "$STATE/$1" 2>/dev/null || true; }
setze() { echo "$2" >"$STATE/$1"; }

ntfy() { # ntfy <prio 1-5> <titel> <text> [knopf]
  [ -z "${NTFY_TOPIC:-}" ] && { log "kein NTFY_TOPIC — ntfy übersprungen"; return 0; }
  local args=(-s -m 15 -H "Title: $2" -H "Priority: $1" -H "Tags: rotating_light")
  [ -n "${4:-}" ] && args+=(-H "Actions: $4")
  curl "${args[@]}" -d "$3" "$NTFY/$NTFY_TOPIC" >/dev/null || log "ntfy fehlgeschlagen"
}
discord() {
  [ -z "${DISCORD_WEBHOOK:-}" ] && return 0
  curl -s -m 15 -H 'Content-Type: application/json' -X POST "$DISCORD_WEBHOOK" \
    -d "$(jq -n --arg t "$1" '{username:"Notfall-Wächter HEL1", content:($t|.[0:1900])}')" >/dev/null || true
}
melde() { ntfy "$1" "$2" "$3" "${4:-}"; discord "**$2**
$3"; }

quittiert_seit() { # hat der Betreiber nach Zeitpunkt $1 „Ich übernehme" gedrückt?
  [ -z "${NTFY_TOPIC:-}" ] && return 1
  curl -s -m 15 "$NTFY/${NTFY_TOPIC}-quittung/json?poll=1&since=$1" | grep -q '"event":"message"'
}

robot_reset() {
  if [ -z "${ROBOT_USER:-}" ] || [ -z "${ROBOT_PASS:-}" ] || [ -z "${ROBOT_SERVER_NUMBER:-}" ]; then
    melde 5 "Hauptserver weiter TOT — kein Auto-Reset konfiguriert" \
      "Robot-Zugang fehlt in /etc/notfall/notfall.env. Bitte selbst in Hetzner Robot resetten."
    return 1
  fi
  local code
  code=$(curl -s -o "$STATE/reset-antwort.json" -w '%{http_code}' -m 30 \
    -u "$ROBOT_USER:$ROBOT_PASS" -d type=hw \
    "https://robot-ws.your-server.de/reset/$ROBOT_SERVER_NUMBER")
  melde 5 "Hardware-Reset des Hauptservers ausgelöst (HTTP $code)" \
    "Server war seit $(( ($(jetzt) - $(lies vorfall_seit)) / 60 )) min tot, keine Quittung. Reset 1× je Vorfall. Antwort: $(head -c 300 "$STATE/reset-antwort.json")"
}

agent_starten() {
  setze agent_gestartet "$(jetzt)"
  melde 4 "Notfall-Agent startet" "Hauptserver: $1 seit $(( ($(jetzt) - $(lies vorfall_seit)) / 60 )) min. Stufe 1 hat nicht gereicht. Bericht folgt (≤ 15 min)."
  (
    # env -i: Der Agent erbt NICHT ROBOT_PASS/NTFY_TOPIC/DISCORD_WEBHOOK des Wächters.
    bericht="$(env -i HOME="$HOME" PATH=/usr/local/bin:/usr/bin:/bin LANG=C.UTF-8 \
      "$BASIS/notfall-agent.sh" 2>&1 | tail -c 3500)"
    printf '%s\n' "$bericht" >"$STATE/agent-bericht-$(date +%Y%m%d-%H%M).txt"
    setze agent_fertig "$(jetzt)"
    zeile="$(printf '%s\n' "$bericht" | grep -m1 '^ERGEBNIS' || echo 'ERGEBNIS: kein Bericht')"
    # ntfy.sh ist ein Fremddienst: dorthin nur die Ergebniszeile. Der volle Bericht
    # (kann Log-Auszüge enthalten) geht in den eigenen Discord-Alarmkanal und bleibt auf HEL1.
    ntfy 4 "Notfall-Agent fertig — ${zeile#ERGEBNIS: }" "Bericht in Discord und auf HEL1 unter $STATE."
    discord "**Notfall-Agent fertig**
$bericht"
  ) &
}

stufe1_hat_geheilt() {
  timeout 20 "$BASIS/notfall-ssh" heilstatus 2>/dev/null \
    | grep -E 'watchdog_state_(zerodox|zerodox-mcp)\.json' | grep -q '"last_heal_at":"20'
}

lauf() {
  local t; t=$(jetzt)
  setze letzter_lauf "$t"

  # HEL1 selbst offline? Dann ist jede Messung wertlos — und ein Reset wäre ein Fehlgriff.
  local ref ok_ref=0
  for ref in $REFERENZ; do curl -s -o /dev/null -m 10 "$ref" && { ok_ref=1; break; }; done
  if [ $ok_ref -eq 0 ]; then log "BLIND — HEL1 erreicht das Internet nicht, keine Bewertung"; return; fi

  local rot=0 gesamt=0 codes="" u c
  for u in $DOMAINS; do
    gesamt=$((gesamt + 1))
    c=$(curl -s -o /dev/null -m 10 -w '%{http_code}' "$u" 2>/dev/null)
    codes+="$c ${u#https://}"$'\n'
    case "$c" in 2??|3??) ;; *) rot=$((rot + 1)) ;; esac
  done
  local wg=0 pub=0
  ping -c2 -W3 "$HAUPT_WG" >/dev/null 2>&1 && wg=1
  ping -c2 -W3 "$HAUPT_PUB" >/dev/null 2>&1 && pub=1

  local zustand=OK
  if [ $rot -gt 0 ]; then
    if [ $rot -eq $gesamt ] && [ $wg -eq 0 ] && [ $pub -eq 0 ]; then zustand=TOT; else zustand=TEIL; fi
  fi
  setze zustand "$zustand"

  if [ "$zustand" = OK ]; then
    if [ -n "$(lies vorfall_seit)" ]; then
      local dauer=$(( (t - $(lies vorfall_seit)) / 60 ))
      [ -n "$(lies alarmiert)" ] && melde 3 "Hauptserver wieder erreichbar" "Alle Domains grün nach ${dauer} min."
      log "Vorfall beendet nach ${dauer} min"
      rm -f "$STATE"/{vorfall_seit,alarmiert,tot_alarm,agent_gestartet,agent_fertig,reset_ausgeloest,teil_eskaliert}
    fi
    return
  fi

  [ -z "$(lies vorfall_seit)" ] && setze vorfall_seit "$t"
  local seit=$(( t - $(lies vorfall_seit) ))
  log "$zustand seit ${seit}s — rot $rot/$gesamt, wg=$wg pub=$pub"

  if [ "$zustand" = TEIL ]; then
    if [ -z "$(lies agent_gestartet)" ] && [ $seit -ge "$TEIL_AGENT_S" ]; then
      if stufe1_hat_geheilt || [ $seit -ge "$TEIL_AGENT_SPAET_S" ]; then
        setze alarmiert "$t"
        agent_starten "$(printf '%s' "$codes" | grep -vE '^(2|3)' | tr '\n' ' ')"
      fi
    elif [ -n "$(lies agent_fertig)" ] && [ -z "$(lies teil_eskaliert)" ] && [ $(( t - $(lies agent_fertig) )) -ge 300 ]; then
      setze teil_eskaliert "$t"
      melde 5 "Hauptserver weiter gestört — Agent hat nicht geholfen" "Seit $((seit / 60)) min:
$codes"
    fi
    return
  fi

  # TOT
  if [ -z "$(lies tot_alarm)" ] && [ $seit -ge "$TOT_ALARM_S" ]; then
    setze tot_alarm "$t"; setze alarmiert "$t"
    melde 5 "HAUPTSERVER TOT seit $((seit / 60)) min" \
      "Kein HTTP, kein Ping (WireGuard + öffentlich). In 10 min löst HEL1 einen Hardware-Reset aus, wenn du nicht übernimmst." \
      "http, Ich übernehme (kein Reset), $NTFY/${NTFY_TOPIC:-x}-quittung, method=POST, body=quittung, clear=true"
  elif [ -n "$(lies tot_alarm)" ] && [ -z "$(lies reset_ausgeloest)" ] \
       && [ $(( t - $(lies tot_alarm) )) -ge "$RESET_NACH_ALARM_S" ]; then
    setze reset_ausgeloest "$t"
    if quittiert_seit "$(lies tot_alarm)"; then
      melde 4 "Kein Reset — Betreiber hat übernommen" "Quittung erhalten."
    else
      robot_reset
    fi
  fi
}

while true; do
  lauf
  [ "$EINMAL" = 1 ] && break
  sleep "$TAKT_S"
done
