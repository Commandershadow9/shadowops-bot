#!/bin/bash
#######################################
# ShadowOps Bot - Automatisches Setup
#######################################

set -e

echo "🗡️  ShadowOps Bot - Setup"
echo "========================="
echo ""

# Prüfe ob als cmdshadow ausgeführt
if [ "$USER" != "cmdshadow" ]; then
    echo "❌ Bitte als User 'cmdshadow' ausführen!"
    exit 1
fi

# Prüfe Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Python3 nicht gefunden! Bitte installieren."
    exit 1
fi

# Installiere Dependencies
echo "📦 Installiere Python-Dependencies..."
pip3 install -r requirements.txt --user

# Erstelle Config aus Example
if [ ! -f "config/config.yaml" ]; then
    echo "📝 Erstelle config.yaml aus Template..."
    cp config/config.example.yaml config/config.yaml
    echo ""
    echo "⚠️  WICHTIG: Bearbeite config/config.yaml und füge deinen Bot-Token ein!"
    echo ""
    echo "    nano config/config.yaml"
    echo ""
    read -p "Drücke Enter wenn du fertig bist..."
fi

# Erstelle Log-Verzeichnis
mkdir -p logs

# Setze Permissions
chmod 600 config/config.yaml
chmod +x src/bot.py

# Installiere Systemd Service
if [ -f "/etc/systemd/system/shadowops-bot.service" ]; then
    echo "⚠️  Service bereits installiert"
else
    echo "🔧 Installiere Systemd Service..."
    sudo cp deploy/shadowops-bot.service /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable shadowops-bot
    echo "✅ Service installiert und aktiviert"
fi

echo ""
echo "✅ Setup abgeschlossen!"
echo ""
echo "📋 Nächste Schritte:"
echo ""
echo "1. Erstelle Discord Bot:"
echo "   → https://discord.com/developers/applications"
echo "   → 'New Application' → Bot erstellen → Token kopieren"
echo ""
echo "2. Config bearbeiten:"
echo "   nano config/config.yaml"
echo "   → Token einfügen"
echo "   → Guild ID einfügen"
echo "   → Channel IDs einfügen"
echo ""
echo "3. Bot starten:"
echo "   sudo systemctl start shadowops-bot"
echo ""
echo "4. Status prüfen:"
echo "   sudo systemctl status shadowops-bot"
echo "   sudo journalctl -u shadowops-bot -f"
echo ""
echo "5. Bot einladen:"
echo "   → Discord Developer Portal → OAuth2 → URL Generator"
echo "   → Scopes: 'bot', 'applications.commands'"
echo "   → Permissions: 'Send Messages', 'Embed Links'"
echo ""
