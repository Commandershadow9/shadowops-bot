#!/usr/bin/env bash
# ShadowOps Bot — Sauberer Restart / Deploy
# Nutzt den system-level systemd-service (sudo nötig).
# Usage: ./restart.sh [--pull] [--logs]
#   --pull   git pull vor Restart
#   --logs   Live-Logs nach Start anzeigen
set -euo pipefail

BOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE="shadowops-bot.service"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
fail()  { echo -e "${RED}[FAIL]${NC}  $*"; }

DO_PULL=false
DO_LOGS=false
for arg in "$@"; do
    case "$arg" in
        --pull) DO_PULL=true ;;
        --logs) DO_LOGS=true ;;
        *) echo "Usage: $0 [--pull] [--logs]"; exit 1 ;;
    esac
done

echo ""
echo "=========================================="
echo "  ShadowOps Bot — Restart / Deploy"
echo "=========================================="
echo ""

# ---------------------------------------------------
# 1. Beide Service-Level stoppen (Safety)
# ---------------------------------------------------
info "Stoppe $SERVICE ..."
sudo systemctl stop "$SERVICE" 2>/dev/null || true
systemctl --user stop "$SERVICE" 2>/dev/null || true
sleep 2

# Warten bis Prozess wirklich weg ist (max 15s)
for i in $(seq 1 15); do
    if ! pgrep -f "python3.*src/bot.py" >/dev/null 2>&1; then
        break
    fi
    if [ "$i" -eq 15 ]; then
        warn "Bot-Prozess reagiert nicht — sende SIGKILL"
        pkill -9 -f "python3.*src/bot.py" 2>/dev/null || true
        sleep 2
    fi
    sleep 1
done
ok "Service gestoppt"

# ---------------------------------------------------
# 2. Cleanup: Stale PID-Files + besetzte Ports
# ---------------------------------------------------
info "Räume stale State auf ..."
rm -f "$BOT_DIR/.bot.pid"
for port in 8766 9090 9091; do
    if fuser "$port/tcp" >/dev/null 2>&1; then
        warn "Port $port belegt — kill"
        fuser -k "$port/tcp" 2>/dev/null || true
    fi
done
ok "Cleanup abgeschlossen"

# ---------------------------------------------------
# 3. Optional: git pull
# ---------------------------------------------------
if $DO_PULL; then
    info "Pulling latest code ..."
    cd "$BOT_DIR"
    git pull --ff-only || { fail "git pull fehlgeschlagen"; exit 1; }
    ok "Code aktualisiert"
fi

# ---------------------------------------------------
# 4. systemd daemon-reload (falls Service-File geändert)
# ---------------------------------------------------
info "Reload systemd ..."
sudo systemctl daemon-reload
ok "systemd reloaded"

# ---------------------------------------------------
# 5. Service starten
# ---------------------------------------------------
info "Starte $SERVICE ..."
sudo systemctl start "$SERVICE"

# Warte und prüfe ob Service läuft (max 15s)
for i in $(seq 1 15); do
    STATE=$(sudo systemctl show "$SERVICE" --property=ActiveState --value 2>/dev/null)
    if [ "$STATE" = "active" ]; then
        PID=$(sudo systemctl show "$SERVICE" --property=MainPID --value 2>/dev/null)
        ok "Service läuft (PID: $PID)"
        break
    elif [ "$STATE" = "failed" ]; then
        fail "Service konnte nicht starten!"
        echo ""
        sudo journalctl -u "$SERVICE" --since "30 seconds ago" --no-pager | tail -20
        exit 1
    fi
    sleep 1
done

# Kurz warten damit Startup-Logs reinkommen
sleep 5

# ---------------------------------------------------
# 6. Status-Zusammenfassung
# ---------------------------------------------------
echo ""
echo "=========================================="
echo "  Status"
echo "=========================================="
sudo systemctl status "$SERVICE" --no-pager -l 2>&1 | head -20
echo ""

# ---------------------------------------------------
# 7. Optional: Live-Logs
# ---------------------------------------------------
if $DO_LOGS; then
    echo "=========================================="
    echo "  Live-Logs (Ctrl+C zum Beenden)"
    echo "=========================================="
    sudo journalctl -u "$SERVICE" -f --no-pager
fi
