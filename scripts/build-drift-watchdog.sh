#!/usr/bin/env bash
#
# build-drift-watchdog.sh — Pruefen ob das deployte MayDay-Sim-Image
# hinter dem aktuellen origin/main her-hinkt.
#
# Hintergrund (Issue #416, Vorfall 2026-05-18):
# Der ShadowOps-Bot war 14h tot. In dieser Zeit kamen GitHub-Webhooks
# nicht an, ShadowOps-Auto-Deploy lief nicht, der laufende Container
# servierte einen 5h alten Build (B12, B13, #411-Spec usw. waren nicht
# live). Symptom war erst durch User-Bericht ("das einsatz fenster hat
# sich nicht veraendert") sichtbar.
#
# Dieser Watchdog schliesst die Luecke ueber den Bot hinaus: selbst wenn
# ShadowOps stirbt, faengt der Watchdog Build-Drift > 30 Min und
# alarmiert direkt via Discord-Webhook.
#
# Konfiguration via Env:
#   MAYDAY_HEALTH_URL          (Default: http://127.0.0.1:3200/api/build-id)
#   MAYDAY_REPO                (Default: Commandershadow9/mayday-sim)
#   BUILD_DRIFT_MAX_MIN        (Default: 30 — Minuten Drift bis Alert)
#   BUILD_DRIFT_WEBHOOK        (Discord-Webhook URL — falls leer: nur Log)
#   BUILD_DRIFT_STATE          (Default: ~/shadowops-bot/data/build-drift-state.json)
#   BUILD_DRIFT_TIMEOUT_S      (Default: 10)
#
# Exit-Codes: 0 = ok ODER Alert gesendet, 1 = Alert-Send-Fehler,
#             2 = Konfigurationsfehler

set -euo pipefail

HEALTH_URL="${MAYDAY_HEALTH_URL:-http://127.0.0.1:3200/api/build-id}"
REPO="${MAYDAY_REPO:-Commandershadow9/mayday-sim}"
MAX_MIN="${BUILD_DRIFT_MAX_MIN:-30}"
WEBHOOK_URL="${BUILD_DRIFT_WEBHOOK:-}"
STATE_FILE="${BUILD_DRIFT_STATE:-/home/cmdshadow/shadowops-bot/data/build-drift-state.json}"

# Statusmeldung an den ZERODOX-Systemstatus (#2451). Optional: Fehlt die Datei,
# laeuft der Watchdog unveraendert weiter — die Meldung ist Beiwerk, nicht Zweck.
# shellcheck source=lib/watchdog-report.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/watchdog-report.sh" 2>/dev/null || true
TIMEOUT_S="${BUILD_DRIFT_TIMEOUT_S:-10}"
HOSTNAME_SHORT="$(hostname -s 2>/dev/null || echo vServer)"
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

mkdir -p "$(dirname "$STATE_FILE")"

# ─── State-Helper (gleicher Shape wie bot-watchdog) ──────────────────
read_state() {
    if [[ -f "$STATE_FILE" ]]; then
        cat "$STATE_FILE"
    else
        echo '{"last_status":"in_sync","last_alert_at":"","drift_minutes":0}'
    fi
}

write_state() {
    local json="$1"
    echo "$json" > "$STATE_FILE.tmp" && mv "$STATE_FILE.tmp" "$STATE_FILE"
}

log() {
    echo "[build-drift] $*"
}

# ─── Build-ID auslesen ───────────────────────────────────────────────
build_response="$(curl -sS -m "$TIMEOUT_S" "$HEALTH_URL" 2>/dev/null || echo "")"
if [[ -z "$build_response" ]]; then
    log "Health-URL nicht erreichbar ($HEALTH_URL) — skip (kein Alert, mayday-sim koennte gerade neu deployen)"
    exit 0
fi

# startedAt: ms-Epoch — bevorzugt, sonst buildId-Fallback
started_at_ms="$(echo "$build_response" | jq -r '.startedAt // .buildId // empty' 2>/dev/null || echo "")"
if [[ -z "$started_at_ms" || "$started_at_ms" == "null" ]]; then
    log "Konnte startedAt/buildId nicht aus Response parsen: $build_response"
    exit 0
fi

# ms → Sekunden (epoch)
build_epoch=$((started_at_ms / 1000))
build_iso="$(date -u -d "@$build_epoch" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo "")"

# ─── Jüngsten origin/main-Commit holen ───────────────────────────────
# `gh` nutzt den eingeloggten Token — keine extra Auth-Konfig noetig
head_commit_iso="$(gh api "repos/$REPO/commits/main" --jq '.commit.committer.date' 2>/dev/null || echo "")"

# Auf VERWERTBARKEIT pruefen, nicht auf "nicht leer". `gh api` gibt bei einem
# Fehler die JSON-Fehlermeldung aus statt nichts — eine reine Leer-Pruefung
# laesst sie durch, und `date -d '{"message":"Not Found"...}'` bricht unter
# `set -e` das ganze Skript ab. Genau das lief am 17.08.2026 ab 15:36 alle
# 15 Minuten: Der Watchdog starb vor jeder Messung, systemd meldete "failed",
# und weil niemand die Ursache las, war der Build-Drift stundenlang unbewacht.
# Bei privaten Repos heisst GitHubs 404 ausserdem "nicht berechtigt", nicht
# "existiert nicht" — der Dienst hat schlicht keinen gueltigen Token.
head_epoch="$(date -u -d "$head_commit_iso" +%s 2>/dev/null || echo "")"
if [[ -z "$head_commit_iso" || -z "$head_epoch" ]]; then
    log "GitHub-Antwort ist kein verwertbares Datum (Repo $REPO privat und Dienst ohne gueltigen gh-Token?): ${head_commit_iso:0:140}"

    # Nicht still aussteigen. Ein Watchdog, der nicht messen kann, sieht von
    # aussen aus wie einer, der nichts zu melden hat — das ist derselbe
    # Fehlermodus, den er selbst verhindern soll. Nach vier Fehlversuchen in
    # Folge (eine Stunde bei 15-Minuten-Takt) wird das gemeldet.
    fehl_zaehler=$(( $(read_state | jq -r '.probe_failures // 0') + 1 ))
    write_state "$(jq -n --arg ts "$TS" --argjson f "$fehl_zaehler" \
        '{last_status:"probe_failed", last_alert_at:"", drift_minutes:0, updated_at:$ts, probe_failures:$f}')"

    if (( fehl_zaehler == 4 )) && [[ -n "$WEBHOOK_URL" ]]; then
        curl -sS -m 10 -X POST -H "Content-Type: application/json" --data "$(jq -n --arg repo "$REPO" --arg host "$HOSTNAME_SHORT" \
            '{content: "⚠️ **Build-Drift-Watchdog kann nicht messen** — `\($repo)`: GitHub-Abfrage liefert kein Datum (Token/Berechtigung pruefen). Der Build-Drift ist seit ~1 Stunde unbewacht.",
              embeds:[{title:"MayDay-Sim Build-Drift: Messung unmoeglich", color:16753920, footer:{text:("Host: " + $host)}}]}')" \
            "$WEBHOOK_URL" >/dev/null 2>&1 || log "Meldung ueber Messausfall konnte nicht zugestellt werden"
    fi
    exit 0
fi

# ─── Drift berechnen ─────────────────────────────────────────────────
# Negative drift = Build neuer als HEAD (sollte nie passieren) → 0
drift_sec=$((head_epoch - build_epoch))
if [[ "$drift_sec" -lt 0 ]]; then
    drift_sec=0
fi
drift_min=$((drift_sec / 60))

log "Build $build_iso vs HEAD $head_commit_iso → drift=$drift_min min (limit $MAX_MIN)"

state="$(read_state)"
last_status="$(echo "$state" | jq -r '.last_status' 2>/dev/null || echo in_sync)"

# Meldung an den ZERODOX-Systemstatus (#2451) — hier, wo die Drift feststeht
# und BEVOR die Dedup-Logik darunter greift. Die unterdrueckt die wiederholte
# Discord-Nachricht bei anhaltender Drift bewusst; die Seite braucht den Wert
# trotzdem jedes Mal, sonst gilt der Watchdog nach zwei Takten als stumm.
if [[ "$drift_min" -gt "$MAX_MIN" ]]; then
    melde_befund "mayday-sim-build-drift" auffaellig \
        "Live-Build ${drift_min} min hinter origin/main (Grenze ${MAX_MIN} min)"
else
    melde_befund "mayday-sim-build-drift" ok
fi

if [[ "$drift_min" -gt "$MAX_MIN" ]]; then
    # Drift over limit
    if [[ "$last_status" == "drift" ]]; then
        log "drift weiterhin >$MAX_MIN min — kein neuer Alert (Dedup)"
        write_state "$(jq -n --arg ts "$TS" --argjson d "$drift_min" \
            '{last_status:"drift", last_alert_at:"'"$(echo "$state" | jq -r .last_alert_at)"'", drift_minutes:$d, updated_at:$ts}')"
        exit 0
    fi
    # Neuer drift → Alert
    log "DRIFT erkannt ($drift_min min hinter HEAD) — sende Discord-Alert"
    if [[ -n "$WEBHOOK_URL" ]]; then
        payload="$(jq -n --arg host "$HOSTNAME_SHORT" --argjson d "$drift_min" \
            --arg build "$build_iso" --arg head "$head_commit_iso" \
            --arg repo "$REPO" --arg limit "$MAX_MIN" '{
                content: "⚠️ **Build-Drift erkannt** — `\($repo)` ist seit \($d) Min hinter `origin/main`. ShadowOps-Bot/Webhook moeglicherweise tot, Auto-Deploy steht.",
                embeds: [{
                    title: "MayDay-Sim Build-Drift",
                    color: 16753920,
                    fields: [
                        {name: "Host", value: $host, inline: true},
                        {name: "Drift", value: "\($d) Min (Limit: \($limit))", inline: true},
                        {name: "Build (live)", value: $build, inline: false},
                        {name: "HEAD (origin/main)", value: $head, inline: false}
                    ],
                    footer: {text: "Auto-Diagnose: shadowops-bot/webhook pruefen, ggf. manueller Rebuild via Runbook docs/ops/manual-deploy.md"}
                }]
            }')"
        if curl -sS -m 10 -X POST -H "Content-Type: application/json" \
            -d "$payload" "$WEBHOOK_URL" >/dev/null 2>&1; then
            log "Discord-Alert OK"
        else
            log "Discord-Alert FAILED"
            exit 1
        fi
    fi
    write_state "$(jq -n --arg ts "$TS" --argjson d "$drift_min" \
        '{last_status:"drift", last_alert_at:$ts, drift_minutes:$d, updated_at:$ts}')"
else
    # In sync
    if [[ "$last_status" == "drift" ]]; then
        log "DRIFT recovered (drift jetzt $drift_min min) — sende Recovery-Alert"
        if [[ -n "$WEBHOOK_URL" ]]; then
            payload="$(jq -n --arg host "$HOSTNAME_SHORT" --argjson d "$drift_min" \
                --arg build "$build_iso" --arg repo "$REPO" '{
                    content: "✅ **Build-Drift behoben** — `\($repo)` wieder in sync (drift \($d) Min).",
                    embeds: [{
                        title: "MayDay-Sim Build-Drift Recovery",
                        color: 5763719,
                        fields: [
                            {name: "Host", value: $host, inline: true},
                            {name: "Drift jetzt", value: "\($d) Min", inline: true},
                            {name: "Build (live)", value: $build, inline: false}
                        ]
                    }]
                }')"
            curl -sS -m 10 -X POST -H "Content-Type: application/json" \
                -d "$payload" "$WEBHOOK_URL" >/dev/null 2>&1 || log "Recovery-Webhook FAILED (ignoriert)"
        fi
    fi
    write_state "$(jq -n --arg ts "$TS" --argjson d "$drift_min" \
        '{last_status:"in_sync", last_alert_at:"", drift_minutes:$d, updated_at:$ts}')"
fi
