#!/usr/bin/env bash
#
# backup-restore-test.sh — Monatlicher Wrapper um ~/ZERODOX/scripts/backup-test.sh.
#
# Ruft das bestehende End-to-End-Backup-Testskript auf und alerted via
# Discord-Webhook bei Failure. Das eigentliche Backup-System bleibt
# unverändert (~/ZERODOX/scripts/backup-test.sh ist die Quelle der Wahrheit).
#
# Solo-Dev-Wert: erfährt monatlich automatisch, ob WAL-G, S3-Sync und
# Secrets-Backup tatsächlich funktionieren — bevor ein echter Disaster
# zeigt, dass die Restore-Kette seit Monaten kaputt ist.
#
# Konfiguration:
#   SHADOWOPS_WATCHDOG_WEBHOOK — Discord-Webhook (gleicher wie Bot-Watchdog)
#   BACKUP_TEST_LOG_DIR        — wo Output gespeichert wird (Default: ~/.local/state/shadowops-bot/backup-test/)
#
# Exit-Codes:
#   0 = Backup-Test erfolgreich (oder Alert wegen Fail erfolgreich gesendet)
#   1 = Backup-Test failed UND Webhook-Call fehlgeschlagen
#   2 = Konfigurationsfehler

set -euo pipefail

# ─── Konfiguration ─────────────────────────────────────────────────────
BACKUP_TEST_SCRIPT="${BACKUP_TEST_SCRIPT:-/home/cmdshadow/ZERODOX/scripts/backup-test.sh}"
WEBHOOK_URL="${SHADOWOPS_WATCHDOG_WEBHOOK:-}"
LOG_DIR="${BACKUP_TEST_LOG_DIR:-/home/cmdshadow/.local/state/shadowops-bot/backup-test}"
HOSTNAME_SHORT="$(hostname -s 2>/dev/null || echo vServer)"
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
TS_LOCAL="$(date '+%Y-%m-%d %H:%M:%S')"

mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/$(date +%Y-%m-%d)_backup-test.log"

# ─── Discord-Alert ─────────────────────────────────────────────────────
send_discord_alert() {
    local title="$1"
    local description="$2"
    local color="$3"

    if [[ -z "$WEBHOOK_URL" ]]; then
        echo "[backup-test] WARN: SHADOWOPS_WATCHDOG_WEBHOOK nicht gesetzt — kein Discord-Alert"
        return 1
    fi

    # Description auf 1800 Chars cappen (Discord-Embed-Limit ~4096, aber wir wollen lesbar bleiben)
    local desc_short
    desc_short=$(echo "$description" | head -c 1800)

    local payload
    payload=$(cat <<EOF
{
  "username": "ShadowOps Backup-Monitor",
  "embeds": [{
    "title": "$title",
    "description": "$desc_short",
    "color": $color,
    "footer": {"text": "Host: $HOSTNAME_SHORT — $TS — Log: $LOG_FILE"}
  }]
}
EOF
)

    local http_code
    http_code=$(curl -s -o /tmp/backup_alert_resp.json -w "%{http_code}" \
        --max-time 15 \
        -H "Content-Type: application/json" \
        -X POST -d "$payload" \
        "$WEBHOOK_URL" || echo "000")

    if [[ "$http_code" =~ ^2 ]]; then
        echo "[backup-test] Discord-Alert gesendet (HTTP $http_code)"
        return 0
    else
        echo "[backup-test] ERROR: Discord-Webhook fehlgeschlagen (HTTP $http_code)"
        return 1
    fi
}

# ─── Main ──────────────────────────────────────────────────────────────
if [[ ! -x "$BACKUP_TEST_SCRIPT" ]]; then
    echo "[backup-test] FEHLER: $BACKUP_TEST_SCRIPT nicht gefunden oder nicht ausfuehrbar."
    exit 2
fi

echo "[backup-test] Starte $BACKUP_TEST_SCRIPT — Log: $LOG_FILE"
echo "=== ShadowOps Backup-Restore-Test $TS_LOCAL ===" > "$LOG_FILE"

# Backup-Test laufen lassen, Output in Log + stdout. ANSI-Escapes
# rausstrippen, damit Discord-Embed lesbar bleibt.
# ⚠️ `sg docker` ist Pflicht, nicht Kosmetik. Dieser Dienst laeuft als
# `systemd --user`, und der hat NUR die primaere Gruppe — `docker` fehlt ihr,
# auch wenn jede interaktive Shell sie besitzt. Ohne `sg` scheitert die erste
# Zeile von backup-test.sh (`docker exec zerodox-db psql ...`) mit
# "permission denied", dessen `set -euo pipefail` bricht ab, und der Lauf endet
# nach der ersten Ueberschrift. Genau das geschah am 01.09.2026: 573 Bytes
# Protokoll, keine der zehn Pruefungen, systemd meldete Erfolg (#3184).
# Nachweis: `systemd-run --user --wait --pipe docker ps` -> permission denied.
# Dieselbe Falle hat den `mcp-drift-watchdog` monatelang blind laufen lassen.
set +e
sg docker -c "$(printf '%q' "$BACKUP_TEST_SCRIPT")" 2>&1 | tee -a "$LOG_FILE"
test_exit=${PIPESTATUS[0]}
set -e

# ⚠️ `|| true` ist hier zwingend. Diese Zeile stand bisher OHNE — und `grep`
# gibt 1 zurueck, wenn es nichts findet. Bei einem abgebrochenen Lauf enthaelt
# das Log keine Ergebnis-Zeile, `set -e` beendete daraufhin den WRAPPER, bevor
# er alarmieren konnte. Deshalb stand am 01.09.2026 weder "FAIL" noch "OK" im
# Journal: Die Meldestelle starb an der Bedingung, die sie melden sollte.
result_summary=$(grep -E "Ergebnis:|ACHTUNG:|Hinweis:|Alle Tests bestanden" "$LOG_FILE" \
    | tail -3 \
    | sed -e 's/\x1b\[[0-9;]*m//g' || true)

# ⚠️ Der Exit-Code allein beweist keinen vollstaendigen Lauf. Ein Skript kann
# mitten in Test 3 abbrechen und trotzdem 0 zurueckgeben — oder, wie hier, an
# einer Stelle sterben, an der niemand hinsieht. Deshalb wird zusaetzlich
# geprueft, ob die LETZTE Pruefung das Protokoll erreicht hat.
# "Nichts gefunden" und "konnte nicht messen" sind verschiedene Zustaende;
# nur der erste ist eine Entwarnung.
LETZTE_PRUEFUNG="${BACKUP_TEST_LETZTE_PRUEFUNG:-Test 10}"
if ! grep -qF "$LETZTE_PRUEFUNG" "$LOG_FILE"; then
    echo "[backup-test] UNVOLLSTAENDIG — '$LETZTE_PRUEFUNG' fehlt im Protokoll"
    test_exit=1
    zeilen=$(wc -l < "$LOG_FILE")
    result_summary="Lauf unvollstaendig: '$LETZTE_PRUEFUNG' nicht erreicht (Protokoll: ${zeilen} Zeilen). Das ist KEINE Entwarnung — es wurde nichts geprueft."
fi

# Ohne Ergebniszeile UND ohne Abbruch-Erkennung waere die Discord-Meldung leer.
if [[ -z "$result_summary" ]]; then
    result_summary="Keine Ergebniszeile im Protokoll gefunden."
fi

if [[ "$test_exit" -eq 0 ]]; then
    # Success: nur loggen, kein Discord-Spam (monatlich grün ist langweilig)
    echo "[backup-test] OK — Backup-System gesund"
    # Optional: kurze "alles ok" Bestaetigung — abschaltbar via Env-Var
    if [[ "${BACKUP_TEST_NOTIFY_ON_SUCCESS:-0}" == "1" ]]; then
        send_discord_alert \
            "✅ Backup-Test OK" \
            "Monatlicher Backup-Restore-Test bestanden:\n\`\`\`\n${result_summary}\n\`\`\`" \
            3066993 || true
    fi
    exit 0
fi

# Failure: IMMER alerten
echo "[backup-test] FAIL — Backup-Test exited mit $test_exit"

# Letzte 40 Log-Zeilen für Context im Alert
log_tail=$(tail -40 "$LOG_FILE" | sed -e 's/\x1b\[[0-9;]*m//g')

if send_discord_alert \
    "🔴 Backup-Test FAILED" \
    "**$result_summary**

Monatlicher Backup-Restore-Test ist fehlgeschlagen (exit=$test_exit).

Tail vom Log:
\`\`\`
${log_tail}
\`\`\`

Komplettes Log: \`$LOG_FILE\`" \
    15158332
then
    exit 0
else
    exit 1
fi
