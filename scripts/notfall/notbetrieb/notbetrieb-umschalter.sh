#!/usr/bin/env bash
#
# notbetrieb-umschalter.sh — DNS-Umschaltung auf den Notbetrieb auf HEL1 (ZERODOX #5629).
#
# Raucht der Hauptserver vollständig ab (Hardware, Rechenzentrum), zeigt `zerodox.de`
# nach ≥ 10 min auf HEL1. Dort liefert nginx die Notseite, Impressum/Datenschutz von
# ZERODOX und die Kunden-Impressen (Schnappschüsse, alle 5 min gespiegelt). Kommt der
# Hauptserver zurück und bleibt 10 min gesund, schaltet er zurück.
#
# Läuft als Dauerdienst (Schleife, 60 s) unter dem eigenen Benutzer `notbetrieb`.
#
# ⚠️ Ohne DNS-Token (Normalfall seit 12.11.2026): HEL1 ist CI-Host, die Runner sind in der
# docker-Gruppe und damit root-äquivalent. Ein Token dort könnte die ganze Zone samt MX
# übernehmen. Ist HCLOUD_TOKEN leer, ruft dieses Skript die Hetzner-API NIE auf, sondern
# meldet bei Umschaltbedarf einmal je Vorfall „von Hand umschalten“ mit Anleitung.
# Mit Token (nur auf einem eigenen, vertrauenswürdigen Host) liegt er in
# /etc/notbetrieb/notbetrieb.env (0600 notbetrieb) und geht nur über stdin an curl.
#
# Harte Grenzen (im Code, nicht in einer Konfiguration):
#   - geändert wird nur der A-Eintrag `@` der Zone (www ist CNAME darauf)
#     bzw. im Test `notbetrieb-test`; MX und alles andere nie
#   - erlaubte Werte nur HAUPT_IP und HEL1_IP
#   - steht im DNS ein dritter Wert, wird NICHT überschrieben, nur alarmiert
#   - /etc/notbetrieb/sperre vorhanden → keine automatische Umschaltung
#
# TOT  = kein Ping (WireGuard + öffentlich) und keine HTTP-Antwort von der Haupt-IP
# Umschalten, wenn TOT ≥ 10 min UND der vorletzte Hop vor dem Hauptserver im Traceroute
#   antwortet (dann ist die Strecke bis ins Rechenzentrum intakt, der Server selbst tot),
#   ODER TOT ≥ 30 min (Rechenzentrum/Strecke weg, lange genug für einen Fehlalarm-Ausschluss)
# BLIND = HEL1 erreicht weder ntfy.sh noch die Hetzner-API → nichts tun
#
# Aufruf: notbetrieb-umschalter.sh            Dauerdienst
#         notbetrieb-umschalter.sh status     Zustand anzeigen
#         notbetrieb-umschalter.sh an|aus     von Hand umschalten (Betreiber)
#         notbetrieb-umschalter.sh test-dns   Probelauf über notbetrieb-test.zerodox.de
set -uo pipefail

# shellcheck disable=SC1091
[ -r /etc/notbetrieb/notbetrieb.env ] && . /etc/notbetrieb/notbetrieb.env
STATE="${NOTBETRIEB_STATE:-/var/lib/notbetrieb}"
DATEN="${NOTBETRIEB_DATEN:-/srv/notbetrieb/daten}"
SPERRE="${NOTBETRIEB_SPERRE:-/etc/notbetrieb/sperre}"
TAKT_S="${NOTBETRIEB_TAKT_S:-60}"
EINMAL="${NOTBETRIEB_EINMAL:-0}"
readonly HAUPT_IP="88.99.160.210"
readonly HEL1_IP="157.180.55.95"
HAUPT_WG="${HAUPT_WG:-10.8.0.1}"
ZONE_ID="${ZONE_ID:-1395844}"
API="${HCLOUD_API:-https://api.hetzner.cloud/v1}"
NTFY="${NTFY_URL:-https://ntfy.sh}"
REFERENZ="${NOTBETRIEB_REFERENZ:-https://ntfy.sh/v1/health https://api.hetzner.cloud/v1}"
TOT_S="${NOTBETRIEB_TOT_S:-600}"
TOT_OHNE_VORPOSTEN_S="${NOTBETRIEB_TOT_OHNE_VORPOSTEN_S:-1800}"
GESUND_S="${NOTBETRIEB_GESUND_S:-600}"
SPIEGEL_ALT_S="${NOTBETRIEB_SPIEGEL_ALT_S:-3600}"
mkdir -p "$STATE"

log() { echo "[notbetrieb] $*"; }
jetzt() { date +%s; }
lies() { cat "$STATE/$1" 2>/dev/null || true; }
# Zeitstempel nur als Zahl: leer oder kaputt gilt als nicht gesetzt (sonst Syntaxfehler in $(( ))).
zahl() { local v; v="$(lies "$1")"; [[ "$v" =~ ^[0-9]+$ ]] && echo "$v"; return 0; }
setze() { echo "$2" >"$STATE/$1"; }
weg() { rm -f "$STATE/$1"; }

ntfy() { # ntfy <prio> <titel> <text>
  [ -z "${NTFY_TOPIC:-}" ] && return 0
  curl -s -m 15 -H "Title: $2" -H "Priority: $1" -H "Tags: satellite" -d "$3" "$NTFY/$NTFY_TOPIC" >/dev/null || log "ntfy fehlgeschlagen"
}
discord() {
  [ -z "${DISCORD_WEBHOOK:-}" ] && return 0
  curl -s -m 15 -H 'Content-Type: application/json' -X POST "$DISCORD_WEBHOOK" \
    -d "$(jq -n --arg t "$1" '{username:"Notbetrieb HEL1", content:($t|.[0:1900])}')" >/dev/null || true
}
melde() { ntfy "$1" "$2" "$3"; discord "**$2**
$3"; log "$2 — $3"; }
einmal_melden() { # einmal_melden <flag> <prio> <titel> <text>
  [ -n "$(zahl "$1")" ] && return 0
  setze "$1" "$(jetzt)"; melde "$2" "$3" "$4"
}
token_da() { [ -n "${HCLOUD_TOKEN:-}" ]; }

# ── DNS (Hetzner Cloud API) ────────────────────────────────────────────────
name_erlaubt() { [ "$1" = "@" ] || [ "$1" = "notbetrieb-test" ]; }
wert_erlaubt() { [ "$1" = "$HAUPT_IP" ] || [ "$1" = "$HEL1_IP" ]; }
hapi() { # hapi <methode> <pfad> [json] → Körper auf stdout, Status in $HAPI_CODE
  HAPI_CODE=000
  # Ohne Token nie ein API-Aufruf — auch nicht unauthentifiziert.
  token_da || { log "kein HCLOUD_TOKEN — Hetzner-API nicht aufgerufen"; return 1; }
  # Der Token darf keine Zeichen enthalten, die die curl-Konfiguration aufbrechen.
  [[ "$HCLOUD_TOKEN" =~ ^[A-Za-z0-9_-]+$ ]] || { log "HCLOUD_TOKEN hat ungültige Zeichen"; return 1; }
  local out; out="$(mktemp)"
  # Header über stdin (-K -), nie als Argument: Prozessargumente sieht jeder Benutzer in ps.
  HAPI_CODE=$(printf 'header = "Authorization: Bearer %s"\n' "$HCLOUD_TOKEN" \
    | curl -s -m 20 -K - -o "$out" -w '%{http_code}' -X "$1" \
      -H 'Content-Type: application/json' ${3:+-d "$3"} "$API$2")
  cat "$out"; rm -f "$out"
}
dns_lies() { # dns_lies <name> → IPs, eine je Zeile (leer = Eintrag fehlt)
  name_erlaubt "$1" || return 2
  hapi GET "/zones/$ZONE_ID/rrsets/$1/A" | jq -r '.rrset.records[]?.value' 2>/dev/null
}
dns_setze() { # dns_setze <name> <ip>
  name_erlaubt "$1" || { log "verweigert: Name $1"; return 2; }
  wert_erlaubt "$2" || { log "verweigert: Wert $2"; return 2; }
  hapi POST "/zones/$ZONE_ID/rrsets/$1/A/actions/set_records" \
    "$(jq -cn --arg v "$2" '{records:[{value:$v}]}')" >/dev/null
  [[ "$HAPI_CODE" =~ ^20[01]$ ]] || { log "set_records $1 → HTTP $HAPI_CODE"; return 1; }
}

# ── Messungen ──────────────────────────────────────────────────────────────
blind() { local r; for r in $REFERENZ; do curl -s -o /dev/null -m 10 "$r" && return 1; done; return 0; }
haupt_http() { curl -s -o /dev/null -m 10 --connect-to "::$HAUPT_IP:" -w '%{http_code}' https://zerodox.de/api/health 2>/dev/null; }
haupt_tot() {
  ping -c 2 -W 2 "$HAUPT_WG" >/dev/null 2>&1 && return 1
  ping -c 2 -W 2 "$HAUPT_IP" >/dev/null 2>&1 && return 1
  local c; c="$(haupt_http)"; [ -z "$c" ] || [ "$c" = "000" ]
}
haupt_gesund() { [ "$(haupt_http)" = "200" ]; }
traceroute_aus() { traceroute -n -I -w 2 -q 1 -m 25 "$HAUPT_IP" 2>/dev/null; }
vorposten_merken() { # vorletzter antwortender Hop vor dem Hauptserver, solange er lebt
  local alt; alt="$(zahl vorposten_zeit)"
  [ -n "$alt" ] && [ $(( $(jetzt) - alt )) -lt 21600 ] && return 0
  local hop; hop="$(traceroute_aus | awk -v z="$HAUPT_IP" '$2==z{print v; exit} $2!="*"{v=$2}')"
  [ -n "$hop" ] && { setze vorposten "$hop"; setze vorposten_zeit "$(jetzt)"; }
}
vorposten_antwortet() {
  local hop; hop="$(lies vorposten)"; [ -n "$hop" ] || return 1
  traceroute_aus | awk '{print $2}' | grep -qxF "$hop"
}
lokal_bereit() { # liefert HEL1 selbst die Notseite mit gültigem Zertifikat?
  local c; c=$(curl -s -o /dev/null -m 10 --connect-to "::$HEL1_IP:" -w '%{http_code}' https://zerodox.de/impressum 2>/dev/null)
  [ "$c" = "200" ]
}
spiegel_alter() { local s; s="$(cat "$DATEN/stand" 2>/dev/null)"; [[ "$s" =~ ^[0-9]+$ ]] || s=0; echo $(( $(jetzt) - s )); }

# ── Umschalten ─────────────────────────────────────────────────────────────
umschalten_an() { # umschalten_an <grund> [tot-minuten]
  if ! lokal_bereit; then
    einmal_melden gemeldet_nicht_bereit 5 "Notbetrieb NICHT bereit" \
      "Der Hauptserver ist tot, aber HEL1 liefert die Notseite nicht aus (nginx/Zertifikat prüfen). DNS bleibt unverändert."
    return 1
  fi
  if ! token_da; then
    # Kein Token auf HEL1 (bewusst, siehe Kopf): Der Mensch schaltet, das Skript sagt wie.
    einmal_melden gemeldet_hand_an 5 "🛟 Notbetrieb nötig — von Hand umschalten" \
      "Hauptserver seit ${2:-?} min tot, HEL1 ist bereit (Notseite, Impressum, Datenschutz, Kunden-Impressen). In der Hetzner Console (console.hetzner.com → DNS → zerodox.de) den A-Eintrag @ auf $HEL1_IP setzen. Zurück auf $HAUPT_IP, wenn der Hauptserver wieder läuft."
    return 0
  fi
  local ist; ist="$(dns_lies @)"
  if [ "$ist" = "$HEL1_IP" ]; then setze modus notbetrieb; return 0; fi
  if [ "$ist" != "$HAUPT_IP" ]; then
    einmal_melden gemeldet_dns_fremd 5 "Notbetrieb: DNS unerwartet" \
      "zerodox.de zeigt auf '${ist:-nichts}', weder Haupt- noch Notserver. Nichts überschrieben."
    return 1
  fi
  setze absicht notbetrieb   # vor dem API-Aufruf: ein Abbruch danach bleibt nachvollziehbar
  dns_setze @ "$HEL1_IP" || { einmal_melden gemeldet_dns_fehler 5 "Notbetrieb: Umschalten gescheitert" "Hetzner-API HTTP $HAPI_CODE."; return 1; }
  setze modus notbetrieb; setze an_seit "$(jetzt)"; weg gemeldet_dns_fehler
  melde 5 "🛟 Notbetrieb AKTIV" "zerodox.de zeigt jetzt auf HEL1 ($1). Online: Notseite, Impressum, Datenschutz, Kunden-Impressen (Stand vor $(( $(spiegel_alter) / 60 )) min). Portal/Login/Zahlungen aus. Rückschaltung automatisch nach 10 min stabil gesundem Hauptserver."
}
umschalten_aus() { # umschalten_aus <grund>
  if ! token_da; then
    einmal_melden gemeldet_hand_aus 5 "Notbetrieb: von Hand zurückschalten" \
      "Der Hauptserver ist wieder gesund. In der Hetzner Console (console.hetzner.com → DNS → zerodox.de) den A-Eintrag @ auf $HAUPT_IP setzen."
    return 0
  fi
  local ist; ist="$(dns_lies @)"
  if [ "$ist" = "$HEL1_IP" ]; then
    setze absicht normal
    dns_setze @ "$HAUPT_IP" || { einmal_melden gemeldet_dns_fehler 5 "Notbetrieb: Rückschalten gescheitert" "Hetzner-API HTTP $HAPI_CODE."; return 1; }
  elif [ "$ist" != "$HAUPT_IP" ]; then
    einmal_melden gemeldet_dns_fremd 5 "Notbetrieb: DNS unerwartet" "zerodox.de zeigt auf '${ist:-nichts}'. Nichts überschrieben."
    return 1
  fi
  setze modus normal; setze absicht normal; weg an_seit; weg tot_seit; weg gesund_seit; weg gemeldet_dns_fehler
  melde 4 "✅ Notbetrieb beendet" "zerodox.de zeigt wieder auf den Hauptserver ($1)."
}

abgleich() { # Modus normal, DNS zeigt aber auf HEL1 (Abbruch zwischen API-Aufruf und Zustand)
  token_da || return 0
  [ "$(dns_lies @)" = "$HEL1_IP" ] || return 0
  haupt_gesund || return 0
  local vorher; vorher="$(lies absicht)"
  setze absicht normal   # Absicht vor dem API-Aufruf
  if dns_setze @ "$HAUPT_IP"; then
    weg an_seit; weg gesund_seit
    melde 5 "Notbetrieb: Abgleich" "Modus war normal, zerodox.de zeigte aber auf HEL1 (letzte Absicht: ${vorher:-unbekannt}). Hauptserver gesund → DNS zurück auf $HAUPT_IP gesetzt."
  else
    einmal_melden gemeldet_dns_fehler 5 "Notbetrieb: Abgleich gescheitert" "zerodox.de zeigt auf HEL1 bei gesundem Hauptserver, Rückschalten scheiterte (Hetzner-API HTTP $HAPI_CODE)."
  fi
}

lauf() {
  if blind; then log "BLIND — HEL1 erreicht keine Referenz, keine Aktion"; return 0; fi
  local modus; modus="$(lies modus)"; modus="${modus:-normal}"
  if [ -e "$SPERRE" ]; then
    einmal_melden gemeldet_sperre 3 "Notbetrieb gesperrt" "$SPERRE existiert — keine automatische Umschaltung."
    return 0
  fi
  weg gemeldet_sperre
  local t; t="$(jetzt)"
  if [ "$modus" = "normal" ]; then
    if haupt_tot; then
      [ -z "$(zahl tot_seit)" ] && setze tot_seit "$t"
      local d=$(( t - $(zahl tot_seit) ))
      if [ "$d" -ge "$TOT_S" ] && vorposten_antwortet; then
        umschalten_an "Hauptserver $((d / 60)) min tot, Rechenzentrum erreichbar" "$((d / 60))"
      elif [ "$d" -ge "$TOT_OHNE_VORPOSTEN_S" ]; then
        umschalten_an "Hauptserver $((d / 60)) min tot, Strecke ebenfalls gestört" "$((d / 60))"
      else
        log "TOT seit $d s"
      fi
    else
      weg tot_seit; weg gemeldet_nicht_bereit; weg gemeldet_dns_fremd; weg gemeldet_hand_an
      abgleich
      vorposten_merken
      if [ "$(spiegel_alter)" -ge "$SPIEGEL_ALT_S" ]; then
        einmal_melden gemeldet_spiegel_alt 3 "Notbetrieb: Spiegel veraltet" \
          "Letzte Spiegelung vor $(( $(spiegel_alter) / 60 )) min. Timer notbetrieb-spiegel auf dem Hauptserver prüfen."
      else
        weg gemeldet_spiegel_alt
      fi
    fi
  else
    if haupt_gesund; then
      [ -z "$(zahl gesund_seit)" ] && setze gesund_seit "$t"
      local g=$(( t - $(zahl gesund_seit) ))
      if [ "$g" -ge "$GESUND_S" ]; then umschalten_aus "Hauptserver seit $((g / 60)) min gesund"
      else log "Notbetrieb, Hauptserver gesund seit $g s"; fi
    else
      weg gesund_seit; weg gemeldet_hand_aus
    fi
  fi
  setze letzter_lauf "$t"
}

test_dns() { # Probelauf ohne Kunden: notbetrieb-test.zerodox.de anlegen, prüfen, löschen
  if ! token_da; then
    echo "test-dns braucht HCLOUD_TOKEN — auf HEL1 liegt bewusst keiner. Probelauf vom Hauptserver:"
    echo "  HCLOUD_TOKEN=… NOTBETRIEB_STATE=\$(mktemp -d) $0 test-dns"
    return 2
  fi
  local ok=1
  hapi POST "/zones/$ZONE_ID/rrsets" \
    "$(jq -n --arg v "$HEL1_IP" '{name:"notbetrieb-test",type:"A",ttl:60,records:[{value:$v}]}')" >/dev/null
  echo "anlegen: HTTP $HAPI_CODE"; [[ "$HAPI_CODE" =~ ^20[01]$ ]] || ok=0
  local i; for i in $(seq 1 24); do
    [ "$(dig +short A notbetrieb-test.zerodox.de @hydrogen.ns.hetzner.com)" = "$HEL1_IP" ] && break; sleep 5
  done
  echo "autoritativ: $(dig +short A notbetrieb-test.zerodox.de @hydrogen.ns.hetzner.com) nach $((i * 5)) s"
  [ "$(dig +short A notbetrieb-test.zerodox.de @hydrogen.ns.hetzner.com)" = "$HEL1_IP" ] || ok=0
  dns_setze notbetrieb-test "$HAUPT_IP" && echo "set_records (Umschaltweg): ok" || ok=0
  dns_setze notbetrieb-test 1.2.3.4 2>/dev/null && { echo "FEHLER: fremder Wert angenommen"; ok=0; } || echo "fremder Wert: verweigert"
  dns_setze mx.post "$HEL1_IP" 2>/dev/null && { echo "FEHLER: fremder Name angenommen"; ok=0; } || echo "fremder Name: verweigert"
  hapi DELETE "/zones/$ZONE_ID/rrsets/notbetrieb-test/A" >/dev/null; echo "löschen: HTTP $HAPI_CODE"
  lokal_bereit && echo "HEL1 liefert zerodox.de/impressum mit gültigem Zertifikat: ok" || { echo "HEL1 liefert NICHT"; ok=0; }
  [ "$ok" = 1 ]
}

case "${1:-}" in
  status)
    if token_da; then dns="$(dns_lies @ | tr '\n' ' ')"; tok=ja
    else dns="$(dig +short A zerodox.de @hydrogen.ns.hetzner.com 2>/dev/null | tr '\n' ' ')"; tok=nein; fi
    echo "modus=$(lies modus) absicht=$(lies absicht) token=$tok dns=${dns}tot_seit=$(lies tot_seit) gesund_seit=$(lies gesund_seit)"
    echo "vorposten=$(lies vorposten) spiegel_alter=$(spiegel_alter)s lokal_bereit=$(lokal_bereit && echo ja || echo nein) sperre=$([ -e "$SPERRE" ] && echo ja || echo nein)" ;;
  an|aus)
    token_da || { echo "Kein HCLOUD_TOKEN — von Hand in der Hetzner Console umschalten (console.hetzner.com → DNS → zerodox.de, A-Eintrag @: an = $HEL1_IP, aus = $HAUPT_IP)."; exit 2; }
    if [ "$1" = an ]; then setze modus normal; umschalten_an "von Hand"; exit $?; fi
    setze modus notbetrieb; umschalten_aus "von Hand"; exit $? ;;
  test-dns) test_dns; exit $? ;;
  "")
    while :; do lauf; [ "$EINMAL" = 1 ] && break; sleep "$TAKT_S"; done ;;
  *) echo "Aufruf: $0 [status|an|aus|test-dns]"; exit 64 ;;
esac
