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

# ─── Takt-Ableitung (ZERODOX #2452) ────────────────────────────────────────
#
# Der Melderhythmus kam bis zum 18.08.2026 aus `WATCHDOG_TAKT_SEK` mit Default
# 300. Gesetzt hatte die Variable KEINE Unit — also galten fuer alle 300 s,
# auch fuer die sieben Watchdogs mit 30-Minuten- bis 6-Stunden-Rhythmus. Die
# ZERODOX-Systemstatus-Seite wertet ab dem doppelten Takt als "stumm" und
# zeigte diese sieben die meiste Zeit als ausgefallen an, obwohl sie liefen.
#
# Der Default stimmte fuer 8 von 15 Watchdogs. Genau deshalb blieb er so lange
# unentdeckt: Ein Default, der bei der Mehrheit passt, sieht nicht nach einem
# Fehler aus, sondern nach ein paar kaputten Diensten.
#
# Die Antwort darauf ist nicht "die Variable ueberall nachtragen" — dann fehlt
# sie beim naechsten neuen Watchdog wieder. Der Takt wird stattdessen aus dem
# Timer abgeleitet, der den Watchdog tatsaechlich startet. Damit kann er per
# Konstruktion nicht mehr abweichen.

# Ermittelt die eigene systemd-Unit aus dem cgroup-Pfad.
# Leer, wenn der Watchdog nicht unter systemd laeuft (z. B. manueller Aufruf).
_eigene_unit() {
    local zeile
    zeile="$(cat /proc/self/cgroup 2>/dev/null | tail -1)" || return 0
    grep -oE '[^/]+\.service' <<<"$zeile" | tail -1
}

# Rechnet den Wiederholungsrhythmus eines Timers in Sekunden um.
# Gibt nichts aus, wenn der Timer keinen wiederkehrenden Takt hat oder
# nicht existiert — bewusst leer statt geraten.
_takt_aus_timer() {
    local timer="$1" zeilen wert spec

    zeilen="$(systemctl --user show "$timer" -p TimersMonotonic -p TimersCalendar 2>/dev/null)" || return 0
    [[ -z "$zeilen" ]] && return 0

    # 1. Intervall-Timer. ⚠️ Gezielt OnUnitActive/OnUnitInactive greifen:
    # Jeder Timer traegt zusaetzlich OnBootUSec (Verzoegerung nach dem Start).
    # Wer den ersten TimersMonotonic-Eintrag nimmt, liest je nach Reihenfolge
    # die Startverzoegerung statt des Rhythmus.
    wert="$(grep -oE 'OnUnit(Active|Inactive)USec=[^ ;]+' <<<"$zeilen" | head -1 | cut -d= -f2)"
    if [[ -n "$wert" ]]; then
        local usec
        usec="$(systemd-analyze timespan "$wert" 2>/dev/null | grep -oE '^[[:space:]]*μs:[[:space:]]*[0-9]+' | grep -oE '[0-9]+')"
        if [[ -n "$usec" && "$usec" -gt 0 ]]; then
            echo $(( usec / 1000000 ))
            return 0
        fi
    fi

    # 2. Kalender-Timer (doku-drift 06:30, ki-cost 07:15). Der Abstand wird aus
    # zwei aufeinanderfolgenden Ausloesezeitpunkten berechnet statt die Angabe
    # zu parsen — das stimmt fuer jede Kalenderangabe, auch woechentliche.
    spec="$(grep -oE 'OnCalendar=[^;]+' <<<"$zeilen" | head -1 | cut -d= -f2- | sed 's/[[:space:]]*$//')"
    if [[ -n "$spec" ]]; then
        local zeiten t1 t2 e1 e2
        zeiten="$(systemd-analyze calendar --iterations=2 "$spec" 2>/dev/null \
                  | grep -oE '\(in UTC\):.*' | sed 's/(in UTC)://; s/^[[:space:]]*//')"
        t1="$(sed -n 1p <<<"$zeiten")"
        t2="$(sed -n 2p <<<"$zeiten")"
        if [[ -n "$t1" && -n "$t2" ]]; then
            e1="$(date -d "$t1" +%s 2>/dev/null)" || return 0
            e2="$(date -d "$t2" +%s 2>/dev/null)" || return 0
            if [[ -n "$e1" && -n "$e2" ]] && (( e2 > e1 )); then
                echo $(( e2 - e1 ))
                return 0
            fi
        fi
    fi

    return 0
}

# Der zu meldende Melderhythmus in Sekunden.
#
# Rangfolge — Ableitung schlaegt Konfiguration:
#   1. aus dem eigenen Timer     — kann nicht vergessen werden
#   2. WATCHDOG_TAKT_SEK         — Notausgang fuer Watchdogs ohne eigenen Timer
#   3. 300 mit Warnung ins Journal
#
# Der Default bleibt als letzte Stufe erhalten, weil ein Fehlschlag der
# Ableitung sonst alle Watchdogs gleichzeitig verstummen liesse — und ein
# stiller Totalausfall der Anzeige waere schlimmer als ein falscher Takt.
# Neu ist, dass er sich nicht mehr verstecken kann: Er schreibt eine Warnung.
ermittle_takt_sek() {
    local unit takt
    unit="$(_eigene_unit)"
    if [[ -n "$unit" ]]; then
        takt="$(_takt_aus_timer "${unit%.service}.timer")"
        if [[ -n "$takt" && "$takt" -gt 0 ]]; then
            echo "$takt"
            return 0
        fi
    fi

    if [[ -n "${WATCHDOG_TAKT_SEK:-}" ]]; then
        echo "$WATCHDOG_TAKT_SEK"
        return 0
    fi

    echo "[watchdog] WARN: Melderhythmus weder aus dem Timer ableitbar (Unit='${unit:-?}')" \
         "noch als WATCHDOG_TAKT_SEK gesetzt — melde Default 300 s." \
         "Die Systemstatus-Seite kann diesen Watchdog dadurch falsch als stumm werten." >&2
    echo 300
}

# Bequemer Einzeiler fuer Watchdogs mit eigenem Skript (ZERODOX #2451).
#
# `melde_status` verlangt den Takt als Argument; ihn in jedem der acht Skripte
# einzeln zu ermitteln hiesse, denselben Block acht Mal zu pflegen — und beim
# naechsten Umbau sieben davon zu vergessen. Diese Huelle nimmt ihn ab.
#
#   melde_befund "disk-hygiene" ok             # alles in Ordnung
#   melde_befund "disk-hygiene" auffaellig "Disk 91 %"
melde_befund() {
    declare -f melde_status >/dev/null 2>&1 || return 0
    local name="$1" befund="$2" detail="${3:-}"
    local status="OK"
    [[ "$befund" != "ok" ]] && status="AUFFAELLIG"
    melde_status "$name" "$status" "$detail" "$(ermittle_takt_sek)"
    return 0
}
