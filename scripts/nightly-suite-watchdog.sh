#!/usr/bin/env bash
#
# nightly-suite-watchdog.sh — meldet, wenn die nächtliche Vollsuite dauerhaft rot ist.
#
# ─── Anlass (ZERODOX #2467) ────────────────────────────────────────────────
#
# Am 19.08.2026 gemessen: Von den letzten 60 Läufen des Workflows
# "Web Quality Nightly" waren **59 rot und einer abgebrochen — kein einziger
# grün**. Bei täglichem Lauf sind das rund zwei Monate ohne einen einzigen
# durchgelaufenen Vollsuite-Test.
#
# Die Fehler waren echt: vier von sechs Shards mit konkret scheiternden Tests
# (Portal-ShieldCard, Posteingang, Operator-Review, Mailing-DOI-Flow). **Keiner
# davon war als Issue erfasst.**
#
# Der Workflow meldet jeden Fehlschlag nach Discord. Das ist ein EREIGNIS-Signal
# — "heute Nacht war es rot". In der sechzigsten Nacht sah es exakt aus wie in
# der ersten. "Seit zwei Monaten nichts mehr grün" ist eine völlig andere
# Aussage, und die sendet heute niemand.
#
# Derselbe Fehlermodus wie beim Einmal-Alarm des runner-vm-disk-Watchdogs
# (#2425), nur eine Ebene höher: Ein Dauerzustand, der wie ein Einzelvorfall
# gemeldet wird, wird zu Hintergrundrauschen.
#
# ⚠️ Dieser Watchdog ersetzt den Discord-Alarm des Workflows NICHT. Er beantwortet
# eine andere Frage: nicht "war es heute rot?", sondern "ist es das schon lange?".
#
# Konfiguration:
#   NIGHTLY_SERIE_SCHWELLE   ab wie vielen Läufen ohne Erfolg (Default 3)
#   NIGHTLY_WATCHDOG_WEBHOOK Discord (Fallback SHADOWOPS_WATCHDOG_WEBHOOK)
#   NIGHTLY_WATCHDOG_STATE   State-Datei

set -euo pipefail

SKRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEBHOOK_URL="${NIGHTLY_WATCHDOG_WEBHOOK:-${SHADOWOPS_WATCHDOG_WEBHOOK:-}}"
STATE_FILE="${NIGHTLY_WATCHDOG_STATE:-/home/cmdshadow/shadowops-bot/data/watchdog_state_nightly-suite.json}"
TS="$(date -Is)"
HOSTNAME_SHORT="$(hostname -s 2>/dev/null || echo vServer)"

# shellcheck source=lib/discord-send.sh
source "$SKRIPT_DIR/lib/discord-send.sh" 2>/dev/null || true
# Statusmeldung an den ZERODOX-Systemstatus (#2451). Optional.
# shellcheck source=lib/watchdog-report.sh
source "$SKRIPT_DIR/lib/watchdog-report.sh" 2>/dev/null || true

mkdir -p "$(dirname "$STATE_FILE")"
log() { printf '%s  %s\n' "$(date +%H:%M:%S)" "$*"; }

read_state() { [[ -f "$STATE_FILE" ]] && cat "$STATE_FILE" || echo '{"zustand":"unbekannt","serie":0}'; }
write_state() { echo "$1" > "$STATE_FILE.tmp" && mv "$STATE_FILE.tmp" "$STATE_FILE"; }

sende() {  # $1=titel $2=text $3=farbe
    [[ -z "$WEBHOOK_URL" ]] && { log "Kein Webhook gesetzt — nur Log."; return 0; }
    local payload
    payload="$(jq -n --arg t "$1" --arg d "$2" --arg h "$HOSTNAME_SHORT" --argjson c "$3" \
        '{username:"Nightly-Suite-Watchdog",
          embeds:[{title:$t, description:$d, color:$c,
                   footer:{text:("Host: " + $h)}, timestamp:(now|todate)}]}')"
    curl -sS -o /dev/null -X POST -H 'Content-Type: application/json' \
        --max-time 10 -d "$payload" "$WEBHOOK_URL" 2>/dev/null \
        || log "Webhook-Zustellung fehlgeschlagen (ignoriert)"
}

# ─── Messen ────────────────────────────────────────────────────────────────
if ! ausgabe="$(bash "$SKRIPT_DIR/nightly-serie.sh" 2>/dev/null)"; then
    log "FEHLER: Serienabfrage nicht möglich — Befund unbekannt, nicht 'in Ordnung'."
    declare -f melde_befund >/dev/null 2>&1 && \
        melde_befund "nightly-suite" auffaellig "Serienabfrage fehlgeschlagen (gh erreichbar?)"
    write_state "$(jq -n --arg ts "$TS" '{zustand:"messung_unmoeglich",last_check:$ts}')"
    exit 1
fi
eval "$ausgabe"   # SERIE, ZUSTAND, LETZTER_GRUENER

vorher="$(read_state | jq -r '.zustand // "unbekannt"')"

if [[ "$ZUSTAND" == "auffaellig" ]]; then
    if [[ -n "$LETZTER_GRUENER" ]]; then
        seit="seit dem ${LETZTER_GRUENER}"
    else
        seit="im gesamten abgefragten Zeitraum kein einziges Mal"
    fi
    text="Die nächtliche Vollsuite ist **${SERIE} Läufe in Folge** nicht durchgelaufen (${seit} grün).

Der Workflow meldet jeden einzelnen Fehlschlag — aber niemand meldet, dass es **anhält**. Genau darum geht es hier.

Die Vollsuite ist der einzige Lauf, der alle Tests fährt; PR und main fahren nur die Smoke-Auswahl. Solange sie rot ist, gibt es für die übrigen Tests **keine Abdeckung**.

Nächster Schritt: \`gh run list --workflow \"Web Quality Nightly\" --limit 1\` und die scheiternden Specs einzeln erfassen."
    log "BEFUND: ${SERIE} Läufe ohne Erfolg (${seit} grün)"
    declare -f melde_befund >/dev/null 2>&1 && \
        melde_befund "nightly-suite" auffaellig "${SERIE} Läufe in Folge ohne Erfolg"

    # ⚠️ Nur beim Zustandswechsel alarmieren — sonst wiederholt sich täglich
    # genau die Meldung, deren Wirkungslosigkeit der Anlass dieses Watchdogs war.
    # Die Systemstatus-Meldung darüber geht in JEDEM Lauf raus; die Seite braucht
    # den Zustand laufend, der Mensch nur den Wechsel.
    [[ "$vorher" != "auffaellig" ]] && \
        sende "🔴 Nächtliche Vollsuite dauerhaft rot" "$text" 15158332
elif [[ "$ZUSTAND" == "unbekannt" ]]; then
    log "Lage unbekannt: keine abgeschlossenen Läufe in der Abfrage (Workflow abgeschaltet oder umbenannt?)"
    # ⚠️ Kein 'ok'. Eine leere Abfrage beweist nichts — sie kann bedeuten, dass
    # der Workflow gar nicht mehr läuft, und das wäre der schlechtere Zustand.
    declare -f melde_befund >/dev/null 2>&1 && \
        melde_befund "nightly-suite" auffaellig "Keine abgeschlossenen Läufe gefunden — läuft der Workflow noch?"
    [[ "$vorher" != "unbekannt" ]] && \
        sende "⚠️ Nächtliche Vollsuite: keine Läufe gefunden" \
              "Die Abfrage liefert keinen abgeschlossenen Lauf. Wurde der Workflow abgeschaltet oder umbenannt?" 16753920
else
    log "OK — ${SERIE} Läufe seit dem letzten Erfolg (Schwelle ${NIGHTLY_SERIE_SCHWELLE:-3})"
    declare -f melde_befund >/dev/null 2>&1 && melde_befund "nightly-suite" ok
    [[ "$vorher" == "auffaellig" ]] && \
        sende "✅ Nächtliche Vollsuite wieder grün" \
              "Die Vollsuite ist wieder durchgelaufen." 5763719
fi

write_state "$(jq -n --arg z "$ZUSTAND" --argjson s "${SERIE:-0}" --arg g "$LETZTER_GRUENER" --arg ts "$TS" \
    '{zustand:$z, serie:$s, letzter_gruener:$g, last_check:$ts}')"
exit 0
