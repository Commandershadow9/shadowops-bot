#!/bin/bash
# ShadowOps Config Update Script
# Aktualisiert deine config.yaml mit den neuen SERVER-SCHONUNG Einstellungen

set -e

BOT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_FILE="$BOT_DIR/config/config.yaml"
BACKUP_FILE="$BOT_DIR/config/config.yaml.backup-$(date +%Y%m%d-%H%M%S)"

cd "$BOT_DIR"

echo "🔄 ShadowOps Config Update"
echo "=========================="
echo ""

# Backup erstellen
if [ -f "$CONFIG_FILE" ]; then
    echo "📦 Backup erstellen: $BACKUP_FILE"
    cp "$CONFIG_FILE" "$BACKUP_FILE"
    echo "✅ Backup erstellt!"
    echo ""
else
    echo "❌ Keine config.yaml gefunden!"
    echo "Erstelle neue Config aus Template..."
    cp config/config.example.yaml config/config.yaml
    echo "⚠️  Bitte trage deine Werte ein:"
    echo "   - Discord Token"
    echo "   - Guild ID"
    echo "   - Admin User ID"
    exit 1
fi

# Zeige aktuelle Werte
echo "📊 VORHER - Aktuelle Scan Intervals:"
grep -A 4 "scan_intervals:" "$CONFIG_FILE" | head -5 || echo "  (nicht gefunden)"
echo ""

echo "📊 VORHER - AI Request Delay:"
grep "request_delay" "$CONFIG_FILE" || echo "  (nicht gefunden)"
echo ""

# Frage ob Update durchführen
read -p "❓ Config jetzt aktualisieren? (y/n): " -n 1 -r
echo ""
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "❌ Update abgebrochen"
    exit 0
fi

# Update scan_intervals
if grep -q "crowdsec: 30" "$CONFIG_FILE" || grep -q "fail2ban: 30" "$CONFIG_FILE"; then
    echo "🔧 Update scan_intervals (30s → 60s)..."

    # Ersetze crowdsec: 30 → crowdsec: 60
    sed -i 's/crowdsec: 30\([^0-9]\)/crowdsec: 60\1/g' "$CONFIG_FILE"
    sed -i 's/crowdsec: 30$/crowdsec: 60/g' "$CONFIG_FILE"

    # Ersetze fail2ban: 30 → fail2ban: 60
    sed -i 's/fail2ban: 30\([^0-9]\)/fail2ban: 60\1/g' "$CONFIG_FILE"
    sed -i 's/fail2ban: 30$/fail2ban: 60/g' "$CONFIG_FILE"

    echo "✅ Scan Intervals aktualisiert!"
else
    echo "ℹ️  Scan Intervals bereits korrekt (60s)"
fi

# Update request_delay_seconds
if ! grep -q "request_delay_seconds" "$CONFIG_FILE"; then
    echo "🔧 Füge request_delay_seconds hinzu..."

    # Finde die Zeile mit "hybrid_models:" und füge danach ein
    if grep -q "hybrid_models:" "$CONFIG_FILE"; then
        sed -i '/hybrid_models:/a\    request_delay_seconds: 4.0  # ⚡ SERVER-SCHONUNG: Verzögerung zwischen AI-Anfragen' "$CONFIG_FILE"
        echo "✅ request_delay_seconds hinzugefügt!"
    else
        echo "⚠️  Konnte request_delay_seconds nicht automatisch hinzufügen"
        echo "    Füge manuell in ollama-Sektion ein: request_delay_seconds: 4.0"
    fi
else
    echo "ℹ️  request_delay_seconds bereits vorhanden"
fi

# Füge neue Channel-Namen hinzu falls fehlend
if ! grep -q "ai_learning:" "$CONFIG_FILE"; then
    echo "🔧 Füge neue Channel-Namen hinzu..."

    # Suche channel_names Sektion
    if grep -q "channel_names:" "$CONFIG_FILE"; then
        # Füge nach stats: hinzu
        sed -i '/stats: "📊-auto-remediation-stats"/a\    ai_learning: "🧠-ai-learning"\n    code_fixes: "🔧-code-fixes"\n    orchestrator: "⚡-orchestrator"\n    performance: "📈-performance"' "$CONFIG_FILE"
        echo "✅ Neue Channel-Namen hinzugefügt!"
    fi

    # Füge Channel IDs hinzu
    if grep -q "notifications:" "$CONFIG_FILE"; then
        sed -i '/stats_channel: null/a\    ai_learning_channel: null\n    code_fixes_channel: null\n    orchestrator_channel: null\n    performance_channel: null' "$CONFIG_FILE"
    fi
else
    echo "ℹ️  Neue Channels bereits vorhanden"
fi

echo ""
echo "📊 NACHHER - Neue Scan Intervals:"
grep -A 4 "scan_intervals:" "$CONFIG_FILE" | head -5
echo ""

echo "📊 NACHHER - AI Request Delay:"
grep "request_delay" "$CONFIG_FILE" || echo "  (konnte nicht automatisch hinzugefügt werden)"
echo ""

echo "✅ Config Update abgeschlossen!"
echo ""
echo "🔄 Nächste Schritte:"
echo "   1. Prüfe die Änderungen: diff $BACKUP_FILE $CONFIG_FILE"
echo "   2. Stoppe den Bot: pkill -f 'python.*src/bot.py'"
echo "   3. Starte den Bot neu: ./start-bot.sh"
echo ""
echo "📝 Backup gespeichert: $BACKUP_FILE"
