#!/usr/bin/env bash
# security-job-reaper.sh — Bricht Zombie-Jobs (status='in_progress') in sec_jobs ab.
# Worker, die hart sterben (OOM/SIGKILL), hinterlassen sonst ewige in_progress-Rows.
# Muster: ~/agents/projects/seo/scripts/job-reaper.sh. Cron: taeglich (siehe W1-Ops).
set -euo pipefail

if ! docker ps >/dev/null 2>&1; then
    exec sg docker -c "$0 ${*:-}"
fi

DB_CONTAINER="${DB_CONTAINER:-guildscout-postgres}"
DB_USER="${DB_USER:-security_analyst}"
DB_NAME="${DB_NAME:-security_analyst}"
STALE_HOURS="${STALE_HOURS:-6}"
LOG_FILE="/home/cmdshadow/shadowops-bot/logs/security-job-reaper.log"
mkdir -p "$(dirname "$LOG_FILE")"

log() { echo "[$(date -Iseconds)] $*" | tee -a "$LOG_FILE" >&2; }

reaped=$(docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -tAc \
  "UPDATE sec_jobs
   SET status='cancelled', completed_at=NOW(),
       error_message='job-reaper: in_progress > ${STALE_HOURS}h (Zombie)'
   WHERE status='in_progress'
     AND started_at < NOW() - make_interval(hours => ${STALE_HOURS})
   RETURNING 1" | wc -l)

log "Reaped: ${reaped} Zombie-Jobs (threshold=${STALE_HOURS}h)"
