#!/usr/bin/env bash
#
# dns-resolver-check.sh — Messung für den dns-resolver-watchdog (ZERODOX #4259).
#
# Gibt auf stdout GENAU EINE Zeile aus, die service-watchdog.sh (Mode "command")
# auswertet:
#   UP
#   DOWN:<Grund>
# Messdetails (Antwortzeiten je Adresse/Name) gehen nach stderr → Journal.
#
# Unterschied, auf den es ankommt: "ist kaputt" ≠ "ich kann nicht messen".
# Fehlt `dig`, ist der Resolver nicht als gesund belegt — das Ergebnis lautet
# dann DOWN:nicht_messbar_…, niemals UP.
#
# Bewertung:
#   unbound.service nicht active                    → DOWN (Resolver antwortet nicht)
#   eine Adresse antwortet auf KEINEN der Namen     → DOWN (Resolver antwortet nicht)
#   Adresse antwortet, ≥2 von 3 Namen SERVFAIL/…    → DOWN (Auflösung scheitert)
#   sonst                                           → UP
# NXDOMAIN/NOERROR zählen als Antwort. SERVFAIL, REFUSED, Timeout zählen als Fehlschlag.
#
# ⚠️ Eine einzelne tote Adresse (z. B. 172.17.0.1) ist ebenfalls DOWN: Die
# Docker-Container fragen nur diese Adresse, ein Ausfall träfe also die App,
# auch wenn der Host-Loopback antwortet.
#
# Overrides (für Trockenlauf/Negativtest):
#   DNS_WATCHDOG_TARGETS  Leerzeichen-getrennt (default "127.0.0.1 172.17.0.1")
#   DNS_WATCHDOG_NAMES    Leerzeichen-getrennt (default "zerodox.de api.twitch.tv gmx.de")
#   DNS_WATCHDOG_UNIT     System-Unit (default unbound.service; "-" = nicht prüfen)

set -uo pipefail

TARGETS="${DNS_WATCHDOG_TARGETS:-127.0.0.1 172.17.0.1}"
NAMES="${DNS_WATCHDOG_NAMES:-zerodox.de api.twitch.tv gmx.de}"
UNIT="${DNS_WATCHDOG_UNIT:-unbound.service}"
# Quelladresse der Messung. ⚠️ Host→172.17.0.1 mit Quelle 172.17.0.1 (dig-Standard)
# antwortet Unbound mit REFUSED, obwohl 172.16.0.0/12 erlaubt ist (08.10.2026
# gemessen; mit -b 127.0.0.1 NOERROR). Container-Quellen sind nicht betroffen —
# gemessen wird daher der Listener, nicht diese Host-Eigenheit.
DNS_BIND="${DNS_WATCHDOG_BIND:-127.0.0.1}"
# Anzeigename im Meldetext (Reserve-Resolver nennt sich anders als der Haupt-Resolver).
LABEL="${DNS_WATCHDOG_LABEL:-Resolver}"

if ! command -v dig >/dev/null 2>&1; then
    echo "DOWN:nicht_messbar_dig_fehlt"
    exit 0
fi

if [[ "$UNIT" != "-" ]]; then
    if ! command -v systemctl >/dev/null 2>&1; then
        echo "DOWN:nicht_messbar_systemctl_fehlt"
        exit 0
    fi
    state="$(systemctl is-active "$UNIT" 2>/dev/null || true)"
    if [[ "$state" != "active" ]]; then
        echo "DOWN:$LABEL antwortet nicht ($UNIT=${state:-unbekannt})"
        exit 0
    fi
fi

grund=""
for target in $TARGETS; do
    antworten=0 fehl=0 gesamt=0 ms_summe=0
    for name in $NAMES; do
        gesamt=$((gesamt + 1))
        out="$(dig +time=2 +tries=1 +noall +comments +stats -b "$DNS_BIND" "@$target" "$name" A 2>/dev/null)" || out=""
        status="$(grep -oE 'status: [A-Z]+' <<<"$out" | head -1 | cut -d' ' -f2)"
        ms="$(grep -oE 'Query time: [0-9]+' <<<"$out" | grep -oE '[0-9]+$' | head -1)"
        if [[ -z "$status" ]]; then
            echo "[dns] @$target $name: keine Antwort" >&2
            fehl=$((fehl + 1))
            continue
        fi
        antworten=$((antworten + 1))
        ms_summe=$((ms_summe + ${ms:-0}))
        echo "[dns] @$target $name: $status ${ms:-?} ms" >&2
        case "$status" in
            NOERROR|NXDOMAIN) ;;
            *) fehl=$((fehl + 1)) ;;
        esac
    done
    if (( antworten == 0 )); then
        grund="${grund:+$grund; }$LABEL antwortet nicht (@$target, 0 von $gesamt Namen)"
    elif (( fehl * 2 > gesamt )); then
        grund="${grund:+$grund; }$LABEL antwortet, Auflösung scheitert (@$target, $fehl von $gesamt Namen SERVFAIL/Timeout)"
    else
        echo "[dns] @$target ok — Ø $((ms_summe / antworten)) ms über $antworten Antworten" >&2
    fi
done

if [[ -n "$grund" ]]; then
    echo "DOWN:$grund"
else
    echo "UP"
fi
exit 0
