#!/usr/bin/env bash
#
# notfall-heiler.sh — Stufe 1 der Notfall-Selbstheilung (ZERODOX #5627).
#
# Wird vom service-watchdog als WATCHDOG_HEAL_CMD aufgerufen, sobald ein
# öffentlicher Dienst (zerodox.de, mcp.zerodox.de) zwei Läufe in Folge down ist.
# Er kennt nur BEKANNTE Ausfallklassen und tut nur Umkehrbares:
#
#   ① docker-socket-heiler  — Socket-Proxies mit toter docker.sock-Inode (Vorfall 12.11.)
#   ② Traefik-Log zeigt Fehler des Docker-Providers → Socket-Proxy neu starten
#   ③ Traefik oder ein Socket-Proxy läuft nicht → docker start
#   ④ zerodox-web läuft nicht / unhealthy → docker start / restart
#   ⑤ Außenprüfung: alle öffentlichen Domains über HTTPS
#
# Bewusst NICHT: compose-Dateien ändern, Images ziehen, Volumes, apt, UFW,
# Migrationen. Was hier nicht hilft, eskaliert die Außenprüfung auf HEL1
# (Stufe 2, Notfall-Agent) bzw. der Mensch.
#
# Exit 0 = Außenprüfung grün · 1 = nach dem Heilen weiter rot · 2 = nicht messbar
# TROCKEN=1 zeigt nur, was getan würde.
set -uo pipefail

TROCKEN="${TROCKEN:-0}"
TRAEFIK="${NOTFALL_TRAEFIK:-sicherheitsdienst-traefik}"
# Container, die den Docker-Socket für Traefik/MCP bereitstellen.
PROXIES="${NOTFALL_PROXIES:-traefik-dockerproxy zerodox-mcp-dockerproxy-1 server-mcp-dockerproxy-1}"
WEB="${NOTFALL_WEB:-zerodox-web}"
# Domains der Außenprüfung. 2xx/3xx = erreichbar; 404 hiess am 12.11. „Traefik ohne Router".
DOMAINS="${NOTFALL_DOMAINS:-https://zerodox.de/api/health https://mcp.zerodox.de/.well-known/oauth-authorization-server https://guildscout.eu https://maydaysim.de}"
WARTE_S="${NOTFALL_WARTE_S:-25}"
SOCKET_HEILER="${NOTFALL_SOCKET_HEILER:-$HOME/scripts/docker-socket-heiler.sh}"
SPERRE="${XDG_RUNTIME_DIR:-$HOME/.cache}/notfall-heiler.lock"

say() { echo "$*"; }
getan=0
tu() {
  # tu "<Beschreibung>" befehl args… — führt aus oder zeigt im Trockenlauf nur an.
  local was="$1"; shift
  if [ "$TROCKEN" = "1" ]; then say "TROCKEN: $was"; return 0; fi
  if "$@" >/dev/null 2>&1; then say "✔ $was"; getan=$((getan + 1)); else say "✘ $was (fehlgeschlagen)"; fi
}

# zerodox-watchdog und mcp-watchdog können gleichzeitig auslösen — nur einer heilt.
exec 9>"$SPERRE"
if ! flock -n 9; then say "Ein anderer Heilversuch läuft bereits — übersprungen."; exit 0; fi

if ! docker info >/dev/null 2>&1; then
  say "Docker nicht erreichbar (dockerd aus oder Gruppe docker fehlt) — nicht messbar."
  exit 2
fi

zustand() { docker inspect -f '{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$1" 2>/dev/null || echo "fehlt|"; }

# ① Tote Socket-Inode (eigener Heiler, bewährt seit 12.11.)
if [ -x "$SOCKET_HEILER" ]; then
  out="$(TROCKEN="$TROCKEN" "$SOCKET_HEILER" 2>&1)"; rc=$?
  echo "$out" | grep -qE 'starte neu|TROCKEN' && say "① socket-heiler: $(echo "$out" | grep -E 'starte neu|TROCKEN' | tr '\n' ' ')"
  [ $rc -ne 0 ] && say "① socket-heiler Exit $rc"
fi

# ② Traefik bekommt vom Docker-Provider Fehler → der Proxy davor ist kaputt
if docker logs --since 5m "$TRAEFIK" 2>&1 | grep -q 'providerName=docker'; then
  for p in $PROXIES; do
    [[ "$p" == traefik-* ]] && tu "② $p neu gestartet (Traefik meldet Docker-Provider-Fehler)" docker restart "$p"
  done
fi

# ③ Traefik und Socket-Proxies müssen laufen
for c in $TRAEFIK $PROXIES; do
  z="$(zustand "$c")"
  case "$z" in
    running*) ;;
    fehlt*) say "③ $c existiert nicht — nicht heilbar ohne compose, Eskalation." ;;
    *) tu "③ $c gestartet (war ${z%%|*})" docker start "$c" ;;
  esac
done

# ④ zerodox-web
z="$(zustand "$WEB")"
case "$z" in
  "running|unhealthy") tu "④ $WEB neu gestartet (unhealthy)" docker restart "$WEB" ;;
  running*) ;;
  fehlt*) say "④ $WEB existiert nicht — Eskalation." ;;
  *) tu "④ $WEB gestartet (war ${z%%|*})" docker start "$WEB" ;;
esac

# ⑤ Außenprüfung — nach einem Eingriff kurz warten, bis Traefik die Router neu hat
[ "$getan" -gt 0 ] && sleep "$WARTE_S"
rot=0
for u in $DOMAINS; do
  code="$(curl -s -o /dev/null -m 10 -w '%{http_code}' "$u" 2>/dev/null)"
  case "$code" in
    2??|3??) say "⑤ $code $u" ;;
    *) say "⑤ ✘ $code $u"; rot=$((rot + 1)) ;;
  esac
done

say "Eingriffe: $getan · Außenprüfung: $([ $rot -eq 0 ] && echo 'grün' || echo "$rot rot")"
[ $rot -eq 0 ] && exit 0 || exit 1
