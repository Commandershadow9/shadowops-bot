#!/usr/bin/env bash
#
# mcp-drift-watchdog.sh — Meldet, wenn der MCP-Server nicht mit dem Code läuft,
# der im Repo steht, oder wenn sein Audit-Protokoll stillschweigend ausfällt.
#
# Anlass (17.08.2026): Der Sicherheits-Patch a18d8c0 lag sieben Tage committet
# in ~/mcp, während der Container weiter den Stand vom 08.08. ausführte. Kein
# Werkzeug hat das gemeldet, weil keines danach gesehen hat. Ein grüner
# Health-Check hätte daran nichts geändert — der Dienst war ja gesund, nur eben
# der falsche. Genau diese Lücke schließt dieser Watchdog.
#
# Zweite Prüfung: Ob /data/audit beschreibbar ist. Der Server fängt einen
# Schreibfehler dort ab, um den Zugang nicht lahmzulegen, und protokolliert
# dann nur noch flüchtig. Ohne diese Prüfung wäre genau das unsichtbar.
#
# Konfiguration via Env:
#   MCP_DRIFT_WEBHOOK  — Discord-Webhook (leer: nur Log, kein Alarm)
#   MCP_DRIFT_STATE    — State-Datei (Default unten)
#   MCP_REPO_DIR       — Repo-Verzeichnis (Default /home/cmdshadow/mcp)
#
# Exit-Codes:
#   0 = alles in Ordnung
#   1 = Drift oder Audit-Problem festgestellt (Alarm gesendet)
#   2 = Konfigurations-/Umgebungsfehler

set -euo pipefail

# Fallback auf den generischen Watchdog-Webhook, damit dieser Watchdog ohne
# Eingriff in ~/.config/shadowops-watchdog.env funktioniert. Gleiches Muster
# wie die Backward-Compat-Kette in service-watchdog.sh.
WEBHOOK_URL="${MCP_DRIFT_WEBHOOK:-${SHADOWOPS_WATCHDOG_WEBHOOK:-}}"
STATE_FILE="${MCP_DRIFT_STATE:-/home/cmdshadow/shadowops-bot/data/watchdog_state_mcp-drift.json}"

# Statusmeldung an den ZERODOX-Systemstatus (#2451). Optional: Fehlt die Datei,
# laeuft der Watchdog unveraendert weiter — die Meldung ist Beiwerk, nicht Zweck.
# shellcheck source=lib/watchdog-report.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/watchdog-report.sh" 2>/dev/null || true
REPO_DIR="${MCP_REPO_DIR:-/home/cmdshadow/mcp}"
CONTAINER="zerodox-mcp-mcp-1"
TS="$(date -Is)"
HOSTNAME_SHORT="$(hostname -s)"

mkdir -p "$(dirname "$STATE_FILE")"
log() { printf '%s  %s\n' "$(date +%H:%M:%S)" "$*"; }

read_state() { [[ -f "$STATE_FILE" ]] && cat "$STATE_FILE" || echo '{"status":"unknown"}'; }
write_state() { echo "$1" > "$STATE_FILE.tmp" && mv "$STATE_FILE.tmp" "$STATE_FILE"; }

sende() {  # $1=titel $2=text $3=farbe
    [[ -z "$WEBHOOK_URL" ]] && { log "Kein Webhook gesetzt — nur Log."; return 0; }
    local payload
    payload="$(jq -n --arg t "$1" --arg d "$2" --arg h "$HOSTNAME_SHORT" --argjson c "$3" \
        '{username:"MCP-Drift-Watchdog",
          embeds:[{title:$t, description:$d, color:$c,
                   footer:{text:("Host: " + $h)}, timestamp:(now|todate)}]}')"
    curl -sS -o /dev/null -X POST -H 'Content-Type: application/json' \
        --max-time 10 -d "$payload" "$WEBHOOK_URL" 2>/dev/null \
        || log "Webhook-Zustellung fehlgeschlagen (ignoriert)"
}

# ─── Messen ───────────────────────────────────────────────────────────────
probleme=()

# Docker-Zugriff aus dem systemd-Dienst heraus.
#
# ⚠️ Die `systemd --user`-Instanz laeuft nur mit der primaeren Gruppe des
# Nutzers — `docker` fehlt ihr, auch wenn jede interaktive Shell sie hat. Der
# Umweg ueber `sg docker` ist derselbe, den auch scripts/deploy.sh nimmt.
docker_cmd() {
    if docker "$@" 2>/dev/null; then
        return 0
    fi
    # Zweiter Versuch mit der Gruppe. Die Argumente werden fuer `sg -c` zu einem
    # String — deshalb einzeln quoten, sonst zerfaellt `sh -c 'test -w /data'`
    # am Leerzeichen in drei Argumente.
    local befehl="docker"
    local a
    for a in "$@"; do befehl+=" $(printf '%q' "$a")"; done
    sg docker -c "$befehl" 2>/dev/null
}

docker_ps() {
    docker_cmd ps --format '{{.Names}}'
}

# ⚠️ Zwischen "Container laeuft nicht" und "ich kann nicht nachsehen" liegt der
# ganze Unterschied — und genau den hat dieser Watchdog vom 17.08.2026 an 14
# Laeufe lang verwischt: `docker ps` scheiterte an fehlenden Gruppenrechten,
# die leere Ausgabe las sich wie "Container weg", das Skript stieg mit `exit 0`
# aus, und systemd meldete `success`. Der Watchdog, der erkennen soll, dass ein
# gesund aussehender Dienst falschen Code faehrt, war selbst genau in diesem
# Zustand — vom Tag seiner Einrichtung an.
if ! laufende="$(docker_ps)"; then
    log "FEHLER: Docker-Abfrage nicht moeglich — Befund unbekannt, nicht 'in Ordnung'."
    melde_befund "mcp-drift" auffaellig \
        "Docker-Abfrage fehlgeschlagen — der Watchdog kann nicht messen (Rechte am Socket?)"
    write_state "$(jq -n --arg ts "$TS" '{status:"messung_unmoeglich",last_check:$ts}')"
    exit 1
fi

if ! grep -qx "$CONTAINER" <<<"$laufende"; then
    # Kein Drift-Befund, sondern ein Ausfall — dafür ist der mcp-watchdog
    # zuständig. Hier nur sauber aussteigen, statt einen zweiten Alarm für
    # dieselbe Ursache zu erzeugen. Die Meldung geht trotzdem raus, sonst gilt
    # dieser Watchdog auf der Uebersicht nach zwei Takten als stumm.
    log "Container $CONTAINER läuft nicht — Zuständigkeit liegt beim mcp-watchdog. Ende."
    melde_befund "mcp-drift" ok "Container nicht aktiv — Zustaendigkeit beim mcp-watchdog"
    write_state "$(jq -n --arg ts "$TS" '{status:"container_down",last_check:$ts}')"
    exit 0
fi

im_container="$(docker_cmd exec "$CONTAINER" md5sum /app/server.py | awk '{print $1}' || true)"
in_head="$(git -C "$REPO_DIR" show HEAD:server.py 2>/dev/null | md5sum | awk '{print $1}' || true)"
head_sha="$(git -C "$REPO_DIR" rev-parse --short HEAD 2>/dev/null || echo '?')"

if [[ -z "$im_container" || -z "$in_head" ]]; then
    probleme+=("Hashes nicht ermittelbar (Container=\`${im_container:-leer}\` HEAD=\`${in_head:-leer}\`)")
elif [[ "$im_container" != "$in_head" ]]; then
    probleme+=("**Code-Drift:** Container läuft nicht mit \`$head_sha\`. Beheben mit \`bash $REPO_DIR/deploy.sh\`.")
fi

# Audit-Protokoll: schreibt es wirklich?
if ! docker_cmd exec "$CONTAINER" sh -c "test -w /data/audit"; then
    probleme+=("**Audit-Protokoll:** \`/data/audit\` ist nicht beschreibbar — Aufrufe werden nur flüchtig protokolliert.")
fi

# Liegt der ausgecheckte Stand ueberhaupt auf main? Der Hash-Vergleich oben
# prueft nur "Container == HEAD" und waere zufrieden, obwohl HEAD auf einem
# Feature-Branch steht, den niemand mehr merged. Genau so ist der Patch
# a18d8c0 sieben Tage liegengeblieben: committet, ausgecheckt, nie gemergt.
zweig="$(git -C "$REPO_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')"
if [[ "$zweig" != "main" ]]; then
    vorne="$(git -C "$REPO_DIR" rev-list --count origin/main..HEAD 2>/dev/null || echo '?')"
    probleme+=("**Nicht gemergt:** Ausgecheckt ist \`$zweig\` (${vorne} Commit(s) vor \`origin/main\`). Der laufende Stand existiert auf main nicht.")
fi

# ─── Melden ───────────────────────────────────────────────────────────────
vorher="$(read_state | jq -r '.status // "unknown"')"

if (( ${#probleme[@]} > 0 )); then
    text="$(printf '%s\n' "${probleme[@]}")"
    log "PROBLEM: $text"
    # ⚠️ Die Meldung steht VOR dem Zustandswechsel-Throttle darunter: Der
    # unterdrueckt die Discord-Nachricht bei anhaltender Drift bewusst, damit
    # sie nicht stuendlich wiederkehrt. Die Systemstatus-Seite muss den Zustand
    # aber jedes Mal erfahren — sonst gilt sie nach zwei Takten als stumm,
    # obwohl der Watchdog laeuft und etwas zu sagen hat.
    melde_befund "mcp-drift" auffaellig "$text"
    # Nur beim Zustandswechsel alarmieren, sonst wiederholt sich die Meldung
    # stündlich, bis niemand mehr hinsieht.
    [[ "$vorher" != "drift" ]] && sende "MCP-Server: Drift festgestellt" "$text" 16753920
    write_state "$(jq -n --arg ts "$TS" --arg d "$text" '{status:"drift",last_check:$ts,detail:$d}')"
    exit 1
fi

melde_befund "mcp-drift" ok
log "In Ordnung: Container läuft mit HEAD ($head_sha), Audit-Verzeichnis beschreibbar."
if [[ "$vorher" == "drift" ]]; then
    sende "MCP-Server: Drift behoben" "Container läuft wieder mit \`$head_sha\`, Audit-Protokoll schreibt." 5763719
fi
write_state "$(jq -n --arg ts "$TS" --arg s "$head_sha" '{status:"ok",last_check:$ts,head:$s}')"
