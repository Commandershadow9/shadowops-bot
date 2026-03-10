#!/bin/bash
# Bot Diagnose Script
# Zeigt Status und hilft bei Problemen
set -e

BOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$BOT_DIR"

echo ""
echo "=========================================="
echo "  ShadowOps Bot — Diagnose"
echo "=========================================="
echo ""

# 1. systemd Service Status
echo "--- systemd Service ---"
sudo systemctl status shadowops-bot --no-pager -l 2>&1 | head -15 || true
echo ""

# 2. Laufende Bot-Prozesse (inkl. Orphans)
echo "--- Bot-Prozesse ---"
BOT_PROCS=$(ps aux | grep "[p]ython3.*src/bot.py" || true)
if [ -z "$BOT_PROCS" ]; then
    echo "  Keine Bot-Prozesse gefunden"
else
    echo "$BOT_PROCS"
fi
echo ""

# 3. Port-Belegung
echo "--- Ports ---"
for port in 8766 9090 9091; do
    HOLDER=$(fuser "$port/tcp" 2>/dev/null || true)
    if [ -n "$HOLDER" ]; then
        echo "  Port $port: BELEGT (PID: $HOLDER)"
    else
        echo "  Port $port: frei"
    fi
done
echo ""

# 4. Stale PID-File
echo "--- PID-File ---"
if [ -f ".bot.pid" ]; then
    PID=$(cat .bot.pid)
    if ps -p "$PID" > /dev/null 2>&1; then
        echo "  .bot.pid existiert, PID $PID laeuft"
    else
        echo "  .bot.pid existiert, PID $PID ist STALE"
    fi
else
    echo "  Keine PID-Datei (korrekt, systemd managed)"
fi
echo ""

# 5. RAM
echo "--- Speicher ---"
free -h | head -2
echo ""

# 6. Letzte Restart-Events
echo "--- Letzte Restart-Events (24h) ---"
sudo journalctl -u shadowops-bot --since "24 hours ago" --no-pager 2>&1 | grep -E "Started|Stopped|exited|KILL|Shutting" | tail -10 || echo "  Keine Events"
echo ""

# 7. Empfehlung
echo "=========================================="
echo "  Befehle"
echo "=========================================="
echo "  scripts/restart.sh          # Sauberer Restart"
echo "  scripts/restart.sh --pull   # Git pull + Restart"
echo "  scripts/restart.sh --logs   # Restart + Live-Logs"
echo "  sudo journalctl -u shadowops-bot -f  # Live-Logs"
echo ""
