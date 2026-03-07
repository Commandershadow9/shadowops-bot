#!/bin/bash
# VERALTET — Dieses Skript startet den Bot NEBEN systemd und verursacht Dual-Instanz-Chaos!
# Nutze stattdessen: ./restart.sh [--pull] [--logs]
echo ""
echo "=========================================="
echo "  VERALTET — Nutze restart.sh"
echo "=========================================="
echo ""
echo "Dieses Skript ist deaktiviert. Es startete den Bot"
echo "ausserhalb von systemd und verursachte Restart-Loops."
echo ""
echo "Stattdessen:"
echo "  ./restart.sh          # Sauberer Restart via systemd"
echo "  ./restart.sh --pull   # Git pull + Restart"
echo "  ./restart.sh --logs   # Restart + Live-Logs"
echo ""
exit 1
