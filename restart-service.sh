#!/bin/bash
# ShadowOps Bot Service Manager
# Managed den systemd service korrekt

set -e

BOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$BOT_DIR"

echo "🔄 ShadowOps Bot Service Manager"
echo "================================="
echo ""

# Prüfe ob systemd service existiert
if systemctl list-units --type=service --all 2>/dev/null | grep -q "shadowops-bot.service"; then
    echo "✅ systemd service gefunden: shadowops-bot.service"
    echo ""

    # Zeige Service Status
    echo "📊 Service Status:"
    systemctl status shadowops-bot.service --no-pager -l | head -20 || true
    echo ""

    # Zeige Service Config
    echo "📄 Service Unit File:"
    SERVICE_FILE=$(systemctl show -p FragmentPath shadowops-bot.service 2>/dev/null | cut -d= -f2)
    if [ -n "$SERVICE_FILE" ] && [ -f "$SERVICE_FILE" ]; then
        echo "   Location: $SERVICE_FILE"
        echo ""
        echo "   Inhalt:"
        cat "$SERVICE_FILE" | sed 's/^/      /'
        echo ""
    fi

    # Frage was zu tun
    echo "🔧 Was möchtest du tun?"
    echo ""
    echo "  1) Service NEU STARTEN (lädt neue Config)"
    echo "  2) Service STOPPEN (komplett deaktivieren)"
    echo "  3) Service LOGS anzeigen (echte Logs!)"
    echo "  4) NUR ANZEIGEN (keine Änderungen)"
    echo ""
    read -p "Wähle Option (1/2/3/4): " -n 1 -r
    echo ""
    echo ""

    case $REPLY in
        1)
            echo "🔄 Starte Service neu mit neuer Config..."
            echo ""

            # Stoppe Service
            echo "   [1/3] Stoppe Service..."
            sudo systemctl stop shadowops-bot.service
            sleep 2

            # Reload systemd (falls Unit File geändert)
            echo "   [2/3] Reload systemd daemon..."
            sudo systemctl daemon-reload

            # Starte Service
            echo "   [3/3] Starte Service..."
            sudo systemctl start shadowops-bot.service

            sleep 3

            echo ""
            echo "✅ Service neu gestartet!"
            echo ""
            echo "📊 Neuer Status:"
            systemctl status shadowops-bot.service --no-pager -l | head -15
            echo ""
            echo "📝 Live Logs (Ctrl+C zum Beenden):"
            echo ""
            sudo journalctl -u shadowops-bot.service -f
            ;;

        2)
            echo "🛑 Stoppe und deaktiviere Service..."
            echo ""

            # Stoppe Service
            sudo systemctl stop shadowops-bot.service
            echo "   ✅ Service gestoppt"

            # Deaktiviere Auto-Start
            sudo systemctl disable shadowops-bot.service
            echo "   ✅ Auto-Start deaktiviert"

            echo ""
            echo "✅ Service deaktiviert!"
            echo ""
            echo "💡 Du kannst den Bot jetzt manuell starten:"
            echo "   ./start-bot.sh"
            ;;

        3)
            echo "📝 Service Logs (Live, Ctrl+C zum Beenden):"
            echo ""
            sudo journalctl -u shadowops-bot.service -f
            ;;

        4)
            echo "ℹ️  Keine Änderungen vorgenommen"
            echo ""
            echo "💡 Service Logs anzeigen:"
            echo "   sudo journalctl -u shadowops-bot.service -f"
            ;;

        *)
            echo "❌ Ungültige Option"
            ;;
    esac

else
    echo "⚠️  Kein systemd service gefunden"
    echo ""
    echo "💡 Bot läuft vermutlich manuell. Nutze:"
    echo "   ./start-bot.sh     # Starten"
    echo "   pkill -f 'python.*src/bot.py'  # Stoppen"
fi
