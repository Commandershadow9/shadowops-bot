#!/usr/bin/env bash
#
# zerodox-hauptbaum-sync.sh — hält /home/cmdshadow/ZERODOX aktuell (ZERODOX#2801).
#
# WARUM ES DAS GIBT
# Der Hauptbaum ist längst keine reine Arbeitskopie mehr, sondern eine
# Laufzeitkomponente: 71 Cron-Zeilen führen Skripte aus `scripts/` aus, und jede
# interaktive Claude-Sitzung lädt ihre Hooks aus `.claude/`. Trotzdem hielt ihn
# nichts aktuell.
#
#   29.08.  PR #2800 gemergt und deployt — der stündliche Backup-Cron las
#           `backup-files.sh` weiter in der alten Fassung.
#   30.08.  Der Session-Wachhund war als Stop-Hook registriert, aber nur auf
#           origin/main; im Baum fehlte er. Seine Datenbank blieb leer.
#
# Zweimal von Hand repariert, beim zweiten Mal hielt die Reparatur keine zwei
# Stunden (36 Commits).
#
# WARUM ES SO VORSICHTIG IST
# Der naheliegende Weg — `fetch && reset --hard` wie beim Deploy-Baum — ist hier
# verboten: In diesem Baum kann eine Sitzung arbeiten, und `reset --hard`
# vernichtet ihre Arbeit ohne Rückfrage. Deshalb ausschliesslich:
#
#   * `merge --ff-only`  — kann per Konstruktion nichts überschreiben. Gibt es
#                          lokale Commits, schlägt es fehl, statt sie wegzuwerfen.
#   * nur auf einem Branch (detached HEAD wird gemeldet, nicht repariert —
#     ein `checkout` dort wäre wieder ein Eingriff in fremde Arbeit)
#   * nur bei sauberem Baum
#
# Lieber gar nichts tun und melden, als einmal zu viel eingreifen. Ausbleibende
# Aktualisierung ist überwacht — `zerodox-host-tree-drift` meldet den veralteten
# Baum ohnehin. Vernichtete Arbeit meldet niemand.
#
# ⚠️ UNTRACKTE Dateien blockieren NICHT. Der echte Baum trägt dauerhaft welche
# (Notizen, Videos, Screenshots). Wer sie mitzählt, baut ein Skript, das nie
# läuft — und das wäre derselbe Fehler wie der, den es beheben soll.

set -uo pipefail   # bewusst KEIN -e: Fehler werden hier einzeln behandelt

BAUM="${ZERODOX_BAUM:-/home/cmdshadow/ZERODOX}"

# Discord-/Statusmeldung ist Beiwerk; fehlt die Datei, läuft der Sync weiter.
# shellcheck source=lib/watchdog-report.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/watchdog-report.sh" 2>/dev/null || true

melde() {  # $1 = Text
    echo "[hauptbaum-sync] $1"
    declare -f melde_status >/dev/null 2>&1 \
        && melde_status "zerodox-hauptbaum-sync" "AUFFAELLIG" "$1" 3600
    return 0
}

git_baum() { git -C "$BAUM" "$@" 2>&1; }

if [[ ! -d "$BAUM/.git" ]]; then
    melde "kein Git-Arbeitsbaum: $BAUM"
    exit 1
fi

# ─── 1. Auf einem Branch? ───────────────────────────────────────────────────
branch="$(git_baum rev-parse --abbrev-ref HEAD)"
if [[ "$branch" == "HEAD" ]]; then
    melde "detached HEAD in $BAUM — dort ist kein ff-only-Merge möglich. Von Hand auf einen Branch setzen; ein checkout von hier aus wäre ein Eingriff in fremde Arbeit."
    exit 2
fi

# ─── 2. Sauber? (untrackte Dateien zählen bewusst NICHT) ────────────────────
schmutz="$(git_baum status --porcelain --untracked-files=no)"
if [[ -n "$schmutz" ]]; then
    anzahl="$(wc -l <<<"$schmutz")"
    melde "$BAUM ist nicht sauber ($anzahl geänderte/gestagete Datei(en)) — nicht angefasst. Dort arbeitet vermutlich jemand."
    exit 3
fi

# ─── 3. Holen ───────────────────────────────────────────────────────────────
if ! fetch_aus="$(git_baum fetch --quiet origin)"; then
    melde "git fetch fehlgeschlagen: ${fetch_aus:0:150}"
    exit 4
fi

vorher="$(git_baum rev-parse HEAD)"
ziel="$(git_baum rev-parse "origin/${branch}")"
if [[ "$vorher" == "$ziel" ]]; then
    echo "[hauptbaum-sync] $BAUM ist auf origin/${branch} — nichts zu tun."
    declare -f melde_status >/dev/null 2>&1 \
        && melde_status "zerodox-hauptbaum-sync" "OK" "" 3600
    exit 0
fi

# ─── 4. Nachziehen, ohne etwas zerstören zu können ──────────────────────────
if ! merge_aus="$(git_baum merge --ff-only "origin/${branch}")"; then
    melde "ff-only-Merge nicht möglich — $BAUM ist von origin/${branch} divergiert (lokale Commits). Nichts verworfen, nichts geändert. Von Hand klären: ${merge_aus:0:120}"
    exit 5
fi

nachher="$(git_baum rev-parse HEAD)"
anzahl="$(git_baum rev-list --count "${vorher}..${nachher}")"
echo "[hauptbaum-sync] $BAUM aktualisiert: ${vorher:0:8} → ${nachher:0:8} (${anzahl} Commit(s))"
declare -f melde_status >/dev/null 2>&1 \
    && melde_status "zerodox-hauptbaum-sync" "OK" "" 3600
exit 0
