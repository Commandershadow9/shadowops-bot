#!/usr/bin/env bash
# postfach-send.sh — additiver Sender an das ZERODOX Team-Postfach (#1983).
#
# WARUM: Watchdogs alarmen bisher ausschließlich per Discord (discord-send.sh).
# Das Team-Postfach (ZERODOX #1980–#1983) führt redaktionelle/fachliche Meldungen
# zusätzlich als durchsuchbaren, als-erledigt-markierbaren Datensatz. Discord bleibt
# für Erreichbarkeits-Wächter der unabhängige Zweitkanal (siehe
# deploy/POSTFACH_ROUTING.md) — dieser Helfer ist rein ADDITIV und darf den
# Discord-Pfad niemals ersetzen, verzögern oder zum Abbruch bringen.
#
# NUTZUNG (sourcebar):
#   source "$(dirname "${BASH_SOURCE[0]}")/lib/postfach-send.sh"
#   postfach_post --category=system --type=watchdog.doku-drift.finding \
#     --severity=WARNING --kind=VORGANG --title="Doku-Drift erkannt" \
#     --body="…" --dedup-key="shadowops:doku-drift-watchdog:$(date -u +%Y-%m-%d)"
#   # Returncode: 0 bei Erfolg (HTTP 2xx), sonst 1 — NIE fatal für den Aufrufer.
#   # Aufrufer mit `set -e` sichern den Call mit `|| true` ab, siehe
#   # doku-drift-watchdog.sh.
#
# VERHALTEN:
#   - Fehlt NOTIFY_INGEST_KEY (nicht konfiguriert): kein Request, stille Rückkehr
#     (return 1). Das ist der Normalfall vor dem Operator-Setup, kein Fehler.
#   - Netzwerkfehler / Nicht-2xx-Antwort: stille Rückkehr (return 1), NIE `exit`.
#   - Diagnose geht ausschließlich nach stderr — stdout bleibt leer, damit
#     Aufrufer, die eigene Ausgaben per Command-Substitution einsammeln,
#     nicht versehentlich den HTTP-Code mit einfangen.
#
# Pflichtfelder: --category --type --severity --kind --title --dedup-key
# Optional:      --body --link
#
# Vertrag (ZERODOX-Seite): web/src/app/api/internal/notifications/ingest/route.ts
#   category: support|customer|billing|system|akquise|recht|sicherheit
#   severity: INFO|WARNING|CRITICAL        kind: VORGANG|KENNTNISNAHME
#   source wird hier fest auf SHADOWOPS gesetzt (Spec E4: shadowops-bot + Watchdogs).
#
# ENV-Overrides: POSTFACH_INGEST_URL, POSTFACH_SEND_TIMEOUT, NOTIFY_INGEST_KEY
#   (kein Default für den Key — Operator-Setup in $WEBHOOK_CONFIG erforderlich,
#   z.B. /home/cmdshadow/.config/shadowops-watchdog.env).
#
# Idempotent sourcebar (Guard) — keine Seiteneffekte beim Sourcen.

[ -n "${_POSTFACH_SEND_SH_LOADED:-}" ] && return 0
_POSTFACH_SEND_SH_LOADED=1

POSTFACH_INGEST_URL="${POSTFACH_INGEST_URL:-https://zerodox.de/api/internal/notifications/ingest}"
POSTFACH_SEND_TIMEOUT="${POSTFACH_SEND_TIMEOUT:-10}"

# postfach_post --category=… --type=… --severity=… --kind=… --title=…
#               --dedup-key=… [--body=…] [--link=…]
# Gibt bei Erfolg (HTTP 2xx) 0 zurück, sonst 1. Schreibt höchstens eine
# Diagnosezeile nach stderr — bricht NIE das Aufrufer-Skript ab.
postfach_post() {
    local category="" type="" severity="" kind="" title="" body="" link="" dedup_key=""

    while [ $# -gt 0 ]; do
        case "$1" in
            --category=*) category="${1#*=}" ;;
            --type=*) type="${1#*=}" ;;
            --severity=*) severity="${1#*=}" ;;
            --kind=*) kind="${1#*=}" ;;
            --title=*) title="${1#*=}" ;;
            --body=*) body="${1#*=}" ;;
            --link=*) link="${1#*=}" ;;
            --dedup-key=*) dedup_key="${1#*=}" ;;
            *) echo "[postfach-send] unbekanntes Argument '$1' — ignoriert" >&2 ;;
        esac
        shift
    done

    if [ -z "$category" ] || [ -z "$type" ] || [ -z "$severity" ] || [ -z "$kind" ] \
        || [ -z "$title" ] || [ -z "$dedup_key" ]; then
        echo "[postfach-send] Pflichtfeld fehlt (category/type/severity/kind/title/dedup-key) — kein Versand" >&2
        return 1
    fi

    local ingest_key="${NOTIFY_INGEST_KEY:-}"
    if [ -z "$ingest_key" ]; then
        # Bewusst kein Fehler-Log: additiver Kanal, Discord trägt unverändert weiter.
        return 1
    fi

    local payload
    payload=$(jq -nc \
        --arg category "$category" --arg type "$type" --arg severity "$severity" \
        --arg kind "$kind" --arg source "SHADOWOPS" --arg title "$title" \
        --arg body "$body" --arg link "$link" --arg dedupKey "$dedup_key" \
        '{category:$category, type:$type, severity:$severity, kind:$kind, source:$source,
          title:$title, dedupKey:$dedupKey}
         + (if $body == "" then {} else {body:$body} end)
         + (if $link == "" then {} else {link:$link} end)' 2>/dev/null) || {
        echo "[postfach-send] jq konnte Payload nicht bauen — kein Versand" >&2
        return 1
    }

    local http
    http=$(curl -sS -o /dev/null -w '%{http_code}' --max-time "$POSTFACH_SEND_TIMEOUT" \
        -X POST -H "Content-Type: application/json" -H "X-Notify-Key: $ingest_key" \
        --data "$payload" "$POSTFACH_INGEST_URL" 2>/dev/null) || http="000"

    case "$http" in
        2??) return 0 ;;
        *)
            echo "[postfach-send] Ingest-Route antwortete mit HTTP '${http}' — Discord-Weg trägt weiter" >&2
            return 1
            ;;
    esac
}
