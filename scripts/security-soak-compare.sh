#!/usr/bin/env bash
# security-soak-compare.sh — 7d-Soak W1: vergleicht npm-audit-Findings des
# Team-Workers (session_id IS NULL) mit denen des Monolithen (session_id IS NOT NULL)
# der letzten 24h anhand finding_fingerprint. Output → Log (Cron, taeglich).
set -euo pipefail

if ! docker ps >/dev/null 2>&1; then
    if [ -n "${_SEC_SG_REEXEC:-}" ]; then
        echo "FEHLER: docker weiterhin unerreichbar nach sg-Re-Exec — Abbruch." >&2
        exit 3
    fi
    export _SEC_SG_REEXEC=1
    exec sg docker -c "$0 ${*:-}"
fi

DB_CONTAINER="${DB_CONTAINER:-guildscout-postgres}"
DB_USER="${DB_USER:-security_analyst}"
DB_NAME="${DB_NAME:-security_analyst}"
LOG_FILE="/home/cmdshadow/shadowops-bot/logs/security-soak-w1.log"
mkdir -p "$(dirname "$LOG_FILE")"

psql_q() {
    docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -tAc "$1"
}

worker=$(psql_q "SELECT COUNT(DISTINCT finding_fingerprint) FROM findings
    WHERE category LIKE '%npm%' AND session_id IS NULL
      AND found_at > NOW() - INTERVAL '24 hours'")
monolith=$(psql_q "SELECT COUNT(DISTINCT finding_fingerprint) FROM findings
    WHERE category LIKE '%npm%' AND session_id IS NOT NULL
      AND found_at > NOW() - INTERVAL '24 hours'")
nur_worker=$(psql_q "SELECT COUNT(*) FROM (
    SELECT DISTINCT finding_fingerprint FROM findings
    WHERE category LIKE '%npm%' AND session_id IS NULL
      AND found_at > NOW() - INTERVAL '24 hours'
    EXCEPT
    SELECT DISTINCT finding_fingerprint FROM findings
    WHERE category LIKE '%npm%' AND session_id IS NOT NULL
      AND found_at > NOW() - INTERVAL '24 hours') d")
nur_monolith=$(psql_q "SELECT COUNT(*) FROM (
    SELECT DISTINCT finding_fingerprint FROM findings
    WHERE category LIKE '%npm%' AND session_id IS NOT NULL
      AND found_at > NOW() - INTERVAL '24 hours'
    EXCEPT
    SELECT DISTINCT finding_fingerprint FROM findings
    WHERE category LIKE '%npm%' AND session_id IS NULL
      AND found_at > NOW() - INTERVAL '24 hours') d")

echo "[$(date -Iseconds)] worker=${worker} monolith=${monolith} nur_worker=${nur_worker} nur_monolith=${nur_monolith}" \
    | tee -a "$LOG_FILE"
