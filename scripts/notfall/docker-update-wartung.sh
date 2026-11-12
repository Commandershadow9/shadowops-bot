#!/usr/bin/env bash
#
# docker-update-wartung.sh — der EINZIGE vorgesehene Weg, Docker/containerd auf dem
# Hauptserver zu aktualisieren (ZERODOX #5627, Stufe 4 „Wartungsfenster").
#
# Vorfall 12.11.2026: eine nächtliche KI-Sitzung zog per `apt upgrade -y` docker-ce mit,
# dockerd startete neu, die Socket-Proxies hielten eine tote Inode, 70 min alle Domains 404.
# Seitdem stehen die Pakete auf `apt-mark hold` — ein gewöhnliches `apt upgrade` lässt sie aus.
#
# Dieses Skript: nur 08–20 Uhr · hold lösen · nur die Docker-Pakete aktualisieren · hold
# wieder setzen (auch bei Fehler) · Socket-Heiler + Notfall-Heiler · Außenprüfung.
# Aufruf durch den Betreiber (braucht sudo):  bash scripts/notfall/docker-update-wartung.sh
set -uo pipefail
PAKETE="docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin"
HIER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
h="$(date +%H)"
if [ "$h" -lt 8 ] || [ "$h" -ge 20 ] && [ "${WARTUNG_NACHTS_ERZWINGEN:-}" != ja ]; then
  echo "Docker-Updates nur 08–20 Uhr, wenn jemand reagieren kann."; exit 64
fi
trap 'sudo apt-mark hold $PAKETE >/dev/null' EXIT
sudo apt-mark unhold $PAKETE >/dev/null
sudo apt-get update -q && sudo apt-get install -y --only-upgrade $PAKETE
echo "Warte 15 s auf dockerd …"; sleep 15
"$HOME/scripts/docker-socket-heiler.sh"
sg docker -c "$HIER/notfall-heiler.sh"
