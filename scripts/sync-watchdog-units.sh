#!/usr/bin/env bash
#
# sync-watchdog-units.sh — IaC-Sync für user-systemd Watchdog-Units (#294)
#
# Spiegelt die Watchdog-Unit-Dateien aus `deploy/*-watchdog.{service,timer}`
# als Symlinks in das user-systemd-Verzeichnis (Default ~/.config/systemd/user).
# Damit ist die Aktivierung der Watchdogs deklarativ statt manueller
# `ln -s` + `systemctl --user enable`-Sequenzen → kein Drift mehr.
#
# Eigenschaften:
#   - Idempotent: korrekter Symlink → skip, falscher/fehlender → neu setzen.
#   - Orphan-Erkennung: Units im Ziel ohne Pendant in deploy/ werden gemeldet
#     (Hinweis ob reguläre Datei oder Symlink). Standard: nur Report.
#   - --dry-run zeigt alle geplanten Aktionen, ändert NICHTS.
#   - --prune entfernt verwaiste SYMLINKS (niemals reguläre Dateien).
#   - --prune --force entfernt zusätzlich verwaiste REGULÄRE Dateien (laut!).
#   - --strict: Exit 1 wenn Orphans gefunden (für CI/Drift-Gate).
#
# Nach Symlink-Änderungen (außer --dry-run): `systemctl --user daemon-reload`
# + pro Timer `enable --now`.
#
# Anlass: audit-watchdog.{service,timer} läuft live als reguläre Datei im
# user-Verzeichnis OHNE deploy/-Pendant → Orphan außerhalb der IaC.
#
set -euo pipefail

# ---------- Pfade ----------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DEPLOY_DIR="${REPO_ROOT}/deploy"
UNIT_DIR="${WATCHDOG_UNIT_DIR:-${HOME}/.config/systemd/user}"

# Safety-Guard: ein leeres oder "/"-UNIT_DIR würde den prune-rm in einem
# gefährlichen Verzeichnis laufen lassen → hart abbrechen.
if [[ -z "${UNIT_DIR}" || "${UNIT_DIR}" == "/" ]]; then
    echo "FEHLER: UNIT_DIR ungültig (leer oder /)" >&2
    exit 1
fi

# ---------- Flags ----------
DRY_RUN=0
PRUNE=0
FORCE=0
STRICT=0

usage() {
    cat <<'EOF'
sync-watchdog-units.sh — IaC-Sync für Watchdog-Units (#294)

Usage: sync-watchdog-units.sh [OPTIONS]

Options:
  --dry-run   Zeigt geplante Aktionen, ändert nichts (kein Symlink, kein systemctl).
  --prune     Entfernt verwaiste Watchdog-SYMLINKS im Ziel-Verzeichnis.
  --force     Nur mit --prune: entfernt auch verwaiste REGULÄRE Dateien (laut!).
  --strict    Exit-Code 1 wenn Orphans gefunden (für CI/Drift-Gate).
  -h, --help  Diese Hilfe.

Env:
  WATCHDOG_UNIT_DIR   Ziel-Verzeichnis (Default: ~/.config/systemd/user).
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=1 ;;
        --prune)   PRUNE=1 ;;
        --force)   FORCE=1 ;;
        --strict)  STRICT=1 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unbekannte Option: $1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

# --force wirkt nur zusammen mit --prune (entfernt verwaiste reguläre Dateien).
if [[ "${FORCE}" -eq 1 && "${PRUNE}" -eq 0 ]]; then
    echo "[sync-watchdog] WARN: --force ohne --prune hat keine Wirkung"
fi

# ---------- Logging-Helper ----------
log()    { echo "[sync-watchdog] $*"; }
action() {
    if [[ "${DRY_RUN}" -eq 1 ]]; then
        echo "[sync-watchdog] (dry-run) $*"
    else
        echo "[sync-watchdog] $*"
    fi
}

# ---------- systemctl-Gate (im Dry-Run übersprungen, in Tests neutralisierbar) ----------
# Alle systemd-Mutationen laufen NUR über diese Funktion. Im Dry-Run wird sie
# zum reinen Echo. Tests setzen WATCHDOG_UNIT_DIR auf tmp + nutzen --dry-run,
# damit niemals echtes systemd berührt wird.
run_systemctl() {
    if [[ "${DRY_RUN}" -eq 1 ]]; then
        action "würde ausführen: systemctl --user $*"
        return 0
    fi
    if ! command -v systemctl >/dev/null 2>&1; then
        log "WARN: systemctl nicht gefunden — überspringe '$*'"
        return 0
    fi
    systemctl --user "$@"
}

# ---------- Vorbedingungen ----------
if [[ ! -d "${DEPLOY_DIR}" ]]; then
    echo "FEHLER: deploy/-Verzeichnis nicht gefunden: ${DEPLOY_DIR}" >&2
    exit 1
fi

log "Repo-Root:   ${REPO_ROOT}"
log "Deploy-Dir:  ${DEPLOY_DIR}"
log "Ziel-Dir:    ${UNIT_DIR}"
[[ "${DRY_RUN}" -eq 1 ]] && log "Modus:       DRY-RUN (keine Änderungen)"
if [[ "${PRUNE}" -eq 1 ]]; then
    if [[ "${FORCE}" -eq 1 ]]; then
        log "Prune:       aktiv (Symlinks + reguläre Dateien via --force)"
    else
        log "Prune:       aktiv (nur Symlinks)"
    fi
fi
[[ "${STRICT}"  -eq 1 ]] && log "Strict:      aktiv (Exit 1 bei Orphans)"

# Ziel-Verzeichnis sicherstellen (außer dry-run).
if [[ ! -d "${UNIT_DIR}" ]]; then
    if [[ "${DRY_RUN}" -eq 1 ]]; then
        action "würde Verzeichnis anlegen: ${UNIT_DIR}"
    else
        mkdir -p "${UNIT_DIR}"
    fi
fi

# ---------- Quell-Units einsammeln ----------
declare -a SOURCE_UNITS=()
shopt -s nullglob
for f in "${DEPLOY_DIR}"/*-watchdog.service "${DEPLOY_DIR}"/*-watchdog.timer; do
    SOURCE_UNITS+=("$(basename "$f")")
done
shopt -u nullglob

if [[ "${#SOURCE_UNITS[@]}" -eq 0 ]]; then
    log "WARN: keine Watchdog-Units in ${DEPLOY_DIR} gefunden."
fi

# ---------- Sync: Symlinks setzen ----------
SYNCED=0
SKIPPED=0
CHANGED=0   # ob daemon-reload nötig
declare -a SYNCED_TIMERS=()

for unit in "${SOURCE_UNITS[@]}"; do
    src="${DEPLOY_DIR}/${unit}"
    dst="${UNIT_DIR}/${unit}"

    if [[ -L "${dst}" ]]; then
        current="$(readlink "${dst}")"
        if [[ "${current}" == "${src}" ]]; then
            SKIPPED=$((SKIPPED + 1))
            [[ "${unit}" == *.timer ]] && SYNCED_TIMERS+=("${unit}")
            continue
        fi
        action "Symlink korrigieren: ${unit} (war → ${current})"
        if [[ "${DRY_RUN}" -eq 0 ]]; then
            ln -sfn "${src}" "${dst}"
        fi
        SYNCED=$((SYNCED + 1)); CHANGED=1
        [[ "${unit}" == *.timer ]] && SYNCED_TIMERS+=("${unit}")
    elif [[ -e "${dst}" ]]; then
        # Reguläre Datei am Ziel-Pfad — NICHT blind überschreiben UND NICHT
        # aktivieren (würde eine Out-of-IaC-Unit ge-enabled → kein Timer-Append).
        log "WARN: ${unit} existiert als REGULÄRE Datei im Ziel — wird NICHT durch Symlink ersetzt."
        log "      → Manuell prüfen/entfernen, dann erneut syncen. (Datei: ${dst})"
        SKIPPED=$((SKIPPED + 1))
    else
        action "Symlink anlegen:    ${unit} → ${src}"
        if [[ "${DRY_RUN}" -eq 0 ]]; then
            ln -sfn "${src}" "${dst}"
        fi
        SYNCED=$((SYNCED + 1)); CHANGED=1
        [[ "${unit}" == *.timer ]] && SYNCED_TIMERS+=("${unit}")
    fi
done

# Doppelte Timer-Einträge defensiv deduplizieren (sollte nach dem Fix nicht mehr
# vorkommen, schadet aber nicht).
if [[ "${#SYNCED_TIMERS[@]}" -gt 0 ]]; then
    mapfile -t SYNCED_TIMERS < <(printf '%s\n' "${SYNCED_TIMERS[@]}" | sort -u)
fi

# ---------- Orphan-Erkennung ----------
# Units im Ziel, die auf *-watchdog.{service,timer} matchen, aber kein
# Pendant in deploy/ haben.
declare -a ORPHANS=()
shopt -s nullglob
for dst in "${UNIT_DIR}"/*-watchdog.service "${UNIT_DIR}"/*-watchdog.timer; do
    unit="$(basename "${dst}")"
    if [[ ! -e "${DEPLOY_DIR}/${unit}" ]]; then
        ORPHANS+=("${unit}")
    fi
done
shopt -u nullglob

ORPHAN_COUNT="${#ORPHANS[@]}"
PRUNED=0

if [[ "${ORPHAN_COUNT}" -gt 0 ]]; then
    log "----- ORPHANS (kein Pendant in deploy/) -----"
    for unit in "${ORPHANS[@]}"; do
        dst="${UNIT_DIR}/${unit}"
        if [[ -L "${dst}" ]]; then
            kind="Symlink → $(readlink "${dst}")"
        else
            kind="REGULÄRE Datei"
        fi
        log "ORPHAN: ${unit}  [${kind}]"

        if [[ "${PRUNE}" -eq 1 ]]; then
            if [[ -L "${dst}" ]]; then
                action "prune Symlink:    ${unit}"
                if [[ "${DRY_RUN}" -eq 0 ]]; then
                    rm -f "${dst}"
                fi
                PRUNED=$((PRUNED + 1)); CHANGED=1
            elif [[ "${FORCE}" -eq 1 ]]; then
                log "WARN: !!! Entferne verwaiste REGULÄRE Datei (--prune --force): ${unit} !!!"
                action "prune Datei:      ${unit}"
                if [[ "${DRY_RUN}" -eq 0 ]]; then
                    rm -f "${dst}"
                fi
                PRUNED=$((PRUNED + 1)); CHANGED=1
            else
                log "      → reguläre Datei NICHT entfernt (benötigt --prune --force)."
            fi
        fi
    done
fi

# ---------- systemd aktivieren ----------
if [[ "${CHANGED}" -eq 1 ]]; then
    run_systemctl daemon-reload
    for timer in "${SYNCED_TIMERS[@]}"; do
        run_systemctl enable --now "${timer}"
    done
else
    log "Keine Symlink-Änderungen → kein daemon-reload nötig."
fi

# ---------- Zusammenfassung ----------
log "================ Zusammenfassung ================"
log "Quell-Units:   ${#SOURCE_UNITS[@]}"
log "Synced:        ${SYNCED}"
log "Skipped:       ${SKIPPED}"
log "Orphans:       ${ORPHAN_COUNT}"
[[ "${PRUNE}" -eq 1 ]] && log "Pruned:        ${PRUNED}"
log "================================================="

# ---------- Exit-Code ----------
if [[ "${STRICT}" -eq 1 && "${ORPHAN_COUNT}" -gt 0 ]]; then
    log "STRICT-Modus: Orphans gefunden → Exit 1"
    exit 1
fi
exit 0
