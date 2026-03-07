#!/bin/bash
# VERALTET — Nutzte sudo systemctl statt systemctl --user und war interaktiv.
# Nutze stattdessen: ./restart.sh [--pull] [--logs]
echo ""
echo "VERALTET — Nutze ./restart.sh stattdessen."
echo ""
echo "Optionen:"
echo "  ./restart.sh          # Sauberer Restart"
echo "  ./restart.sh --pull   # Git pull + Restart"
echo "  ./restart.sh --logs   # Restart + Live-Logs"
echo ""
echo "Manuell:"
echo "  sudo systemctl status shadowops-bot"
echo "  sudo systemctl restart shadowops-bot"
echo "  sudo journalctl -u shadowops-bot -f"
echo ""
exit 1
