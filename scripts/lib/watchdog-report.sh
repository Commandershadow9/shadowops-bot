#!/usr/bin/env bash
#
# watchdog-report.sh — Meldet ein Watchdog-Ergebnis an ZERODOX (#2441).
#
# ⚠️ DIESE MELDUNG IST BEIWERK. Messung und Discord-Alarm sind die Hauptaufgabe
# jedes Watchdogs. Deshalb schluckt diese Funktion JEDEN Fehler und gibt immer 0
# zurueck.
#
# Waere sie fehlerkritisch, zoege ein ZERODOX-Ausfall die Watchdogs mit in den
# Abgrund: Unter `set -e` — das jeder Watchdog nutzt — wuerde ein fehlgeschlagener
# curl das Skript beenden, bevor der Discord-Alarm rausgeht. Die Alarmierung
# fiele damit genau in dem Moment aus, in dem sie gebraucht wird. Das waere eine
# Verschlechterung gegenueber dem Zustand ohne diese Datei.
#
# Genau dieser Fehler steckte am 17.08.2026 im build-drift-watchdog: Ein
# unbedachter Abbruchpfad liess ihn stundenlang vor jeder Messung sterben.
#
# Nutzung (Muster wie lib/discord-send.sh):
#   source "$(dirname "${BASH_SOURCE[0]}")/lib/watchdog-report.sh" 2>/dev/null || true
#   declare -f melde_status >/dev/null && melde_status "mcp-watchdog" "OK" "" 300
#
# Die `declare -f`-Pruefung haelt den Watchdog lauffaehig, falls diese Datei
# fehlt — etwa nach einem unvollstaendigen Deploy.
#
# Konfiguration:
#   ZERODOX_AGENT_API_KEY  — Pflicht. Fehlt er, passiert stillschweigend nichts.
#   ZERODOX_WATCHDOG_URL   — optional, Default unten.

melde_status() {  # $1=name $2=OK|AUFFAELLIG $3=detail $4=takt_sek
    local url="${ZERODOX_WATCHDOG_URL:-https://zerodox.de/api/internal/watchdog-status}"
    local key="${ZERODOX_AGENT_API_KEY:-}"

    # Ohne Schluessel schlicht nichts tun. Die Watchdogs muessen auch auf einem
    # System laufen koennen, auf dem ZERODOX gar nicht existiert.
    [[ -z "$key" ]] && return 0

    local payload
    payload="$(jq -n --arg n "$1" --arg s "$2" --arg d "${3:-}" --argjson t "${4:-300}" \
        '{name:$n, status:$s, detail:(if $d == "" then null else $d end),
          gemessenAm:(now|todate), erwarteterTaktSek:$t}' 2>/dev/null)" || return 0

    curl -sS -m 5 -X POST -H "Content-Type: application/json" \
        -H "X-Agent-Key: $key" --data "$payload" "$url" >/dev/null 2>&1 || true
    return 0
}
