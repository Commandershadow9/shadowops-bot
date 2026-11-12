#!/usr/bin/env bash
#
# notfall-wrapper.sh — die EINZIGE Tür des Notfall-Agenten auf HEL1 in den Hauptserver
# (ZERODOX #5627, Stufe 2).
#
# Eingebunden über ~cmdshadow/.ssh/authorized_keys:
#   command="/home/cmdshadow/shadowops-bot/scripts/notfall/notfall-wrapper.sh",from="10.8.0.12",restrict ssh-ed25519 … notfall-hel1
#
# sshd führt für diesen Schlüssel IMMER dieses Skript aus; was der Agent als Befehl
# schickt, steht nur in $SSH_ORIGINAL_COMMAND und wird hier gegen eine Positivliste
# geprüft. Diese Grenze hängt NICHT am Prompt und nicht an --allowedTools des Agenten:
# auch ein vollständig fehlgeleiteter Agent kann nur, was hier steht.
#
# Erlaubt: lesen (Status, Logs, Ressourcen, Socket-Inodes) und Umkehrbares
# (Container start/restart, ausgewählte systemd-User-Dienste neu starten,
# `compose up -d` ohne Build/Pull in bekannten Projekten, Stufe-1-Heiler).
# Gesperrt durch Weglassen: rm, down, -v, pull, build, apt, ufw, Dateien schreiben,
# Migrationen, git, Secrets lesen, beliebige Shell.
#
# Jeder Aufruf wird protokolliert (Datei + journald), auch abgelehnte.
set -uo pipefail
export LC_ALL=C.UTF-8

LOG="${NOTFALL_WRAPPER_LOG:-$HOME/.local/state/notfall-wrapper.log}"
mkdir -p "$(dirname "$LOG")"
CMD="${SSH_ORIGINAL_COMMAND:-${1:-}}"
HIER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

protokoll() {
  # %q: ein Befehl kann keine gefälschten Protokollzeilen erzeugen.
  printf '%s %s %q\n' "$(date -Is)" "$1" "$CMD" >>"$LOG"
  logger -t notfall-wrapper "$1 $(printf '%q' "$CMD")" 2>/dev/null || true
}
ablehnen() { protokoll "ABGELEHNT"; echo "ABGELEHNT: $1" >&2; echo "Erlaubt: hilfe" >&2; exit 64; }

# Nur einfache Wörter — keine Steuerzeichen, keine Shell-Metazeichen, keine Pfade.
[[ "$CMD" =~ [[:cntrl:]] ]] && ablehnen "Steuerzeichen"
case "$CMD" in
  *[\;\&\|\$\`\<\>\(\)\{\}\\\'\"*?~]*|*..*) ablehnen "Sonderzeichen" ;;
esac
# shellcheck disable=SC2206
set -f; ARGS=($CMD); set +f
VERB="${ARGS[0]:-hilfe}"
ARG1="${ARGS[1]:-}"
ARG2="${ARGS[2]:-}"
[ "${#ARGS[@]}" -le 3 ] || ablehnen "zu viele Argumente"

name_ok() { [[ "$1" =~ ^[a-z0-9][a-z0-9_.-]{0,62}$ ]]; }
docker_() { sg docker -c "docker $(printf '%q ' "$@")"; }

# Container, die der Agent anfassen darf: alles, was gerade existiert, AUSSER
# Datenbanken/Caches (ein Neustart dort ist nie die Heilung für „Seite 404“
# und kann laufende Transaktionen kosten).
container_erlaubt() {
  name_ok "$1" || return 1
  # ⚠️ docker inspect akzeptiert auch ID-Präfixe: `restart 3f2a` träfe sonst einen
  # Postgres-Container an der Namens-Sperre vorbei. Deshalb den echten Namen
  # auflösen, exakte Gleichheit verlangen und zusätzlich das IMAGE prüfen.
  local info echt bild
  info="$(docker_ inspect --type container -f '{{.Name}}|{{.Config.Image}}' "$1" 2>/dev/null)" || return 1
  echt="${info%%|*}"; echt="${echt#/}"; bild="${info#*|}"
  [ "$echt" = "$1" ] || return 1
  case "$echt|$bild" in
    *postgres*|*-db|*-db\|*|*-db-*|*redis*|*pgbouncer*|*walg*|*backup*|*mysql*|*mariadb*|*mongo*) return 1 ;;
  esac
  return 0
}
# systemd-User-Dienste, die neu gestartet werden dürfen.
DIENSTE="shadowops-bot zerodox-support-agent seo-agent guildscout-feedback-agent"
# compose-Projekte für `up -d` (Verzeichnis je Kurzname). Ohne Build, ohne Pull:
# es startet nur, was laut vorhandener Datei laufen soll.
# ⚠️ Bewusst NICHT zerodox: `up -d` würde bei Konfigurationsdrift auch die
# Datenbank neu erzeugen. zerodox-web heilt `start`/`restart`.
compose_dir() {
  case "$1" in
    traefik) echo "$HOME/traefik-proxy" ;;
    mcp) echo "$HOME/.zerodox-mcp-build" ;;
    *) return 1 ;;
  esac
}

case "$VERB" in
  hilfe)
    protokoll "OK"
    cat <<'EOF'
LESEN:   status · logs <container> [zeilen≤400] · zustand <container> · inodes · ressourcen
         · traefik-fehler · aussen · heilstatus · journal <dienst> · dns
HANDELN: start <container> · restart <container> · dienst-neustart <dienst>
         · compose-up <traefik|mcp> · heiler
Nicht erlaubt: löschen, down, pull, build, apt, ufw, Dateien ändern, Migrationen, Secrets.
EOF
    ;;
  status)
    protokoll "OK"; docker_ ps -a --format '{{.Names}}\t{{.Status}}\t{{.Image}}' ;;
  logs)
    container_erlaubt "$ARG1" || ablehnen "Container unbekannt oder gesperrt"
    n="${ARG2:-150}"; [[ "$n" =~ ^[0-9]{1,3}$ ]] && [ "$n" -le 400 ] || ablehnen "zeilen 1–400"
    protokoll "OK"; docker_ logs --tail "$n" "$ARG1" 2>&1 | cut -c1-400 ;;
  zustand)
    name_ok "$ARG1" || ablehnen "Name"
    protokoll "OK"
    docker_ inspect -f '{{.Name}} status={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{end}} gestartet={{.State.StartedAt}} restarts={{.RestartCount}}' "$ARG1" ;;
  inodes)
    protokoll "OK"
    echo "host $(stat -c '%i %z' /var/run/docker.sock)"
    for c in $(docker_ ps --format '{{.Names}}'); do
      docker_ inspect -f '{{range .Mounts}}{{if eq .Destination "/var/run/docker.sock"}}x{{end}}{{end}}' "$c" | grep -q x || continue
      echo "$c $(docker_ exec "$c" ls -i /var/run/docker.sock 2>&1 | awk '{print $1}')"
    done ;;
  ressourcen)
    protokoll "OK"; uptime; free -h; df -h / /var/lib/docker 2>/dev/null; systemctl --failed --no-legend --no-pager | head -20 ;;
  traefik-fehler)
    protokoll "OK"; docker_ logs --since 15m sicherheitsdienst-traefik 2>&1 | grep -iE 'error|level=err|providerName' | tail -40 | cut -c1-400 ;;
  aussen)
    protokoll "OK"
    for u in https://zerodox.de/api/health https://mcp.zerodox.de/.well-known/oauth-authorization-server https://guildscout.eu https://maydaysim.de; do
      echo "$(curl -s -o /dev/null -m 10 -w '%{http_code}' "$u") $u"
    done ;;
  heilstatus)
    protokoll "OK"
    for f in "$HOME"/shadowops-bot/data/watchdog_state_*.json; do
      [ -f "$f" ] && grep -q '"last_status":"down"' "$f" && echo "$(basename "$f"): $(cat "$f")"
    done; echo "(nur Watchdogs im Zustand down)" ;;
  journal)
    [[ " $DIENSTE docker-socket-heiler zerodox-watchdog mcp-watchdog " == *" $ARG1 "* ]] || ablehnen "Dienst nicht freigegeben"
    protokoll "OK"; journalctl --user -u "$ARG1" -n 80 --no-pager 2>&1 | cut -c1-400 ;;
  dns)
    protokoll "OK"; for h in zerodox.de github.com; do echo "$h $(getent hosts "$h" | awk '{print $1}' | head -1)"; done ;;
  start|restart)
    container_erlaubt "$ARG1" || ablehnen "Container unbekannt oder gesperrt (DB/Cache/Backup)"
    protokoll "AKTION"; docker_ "$VERB" "$ARG1" ;;
  dienst-neustart)
    [[ " $DIENSTE " == *" $ARG1 "* ]] || ablehnen "Dienst nicht freigegeben"
    protokoll "AKTION"; systemctl --user restart "$ARG1" && systemctl --user is-active "$ARG1" ;;
  compose-up)
    dir="$(compose_dir "$ARG1")" || ablehnen "Projekt unbekannt"
    protokoll "AKTION"
    sg docker -c "cd $(printf '%q' "$dir") && docker compose up -d --no-build --pull never 2>&1" | tail -30 ;;
  heiler)
    protokoll "AKTION"; sg docker -c "$HIER/notfall-heiler.sh" ;;
  *)
    ablehnen "unbekannter Befehl '$VERB'" ;;
esac
