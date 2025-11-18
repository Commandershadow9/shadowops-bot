#!/bin/bash
# Update Config zu TEST MODE (60s Scans)
# Ausführen: ./update-config-test-mode.sh

set -e

CONFIG_FILE="/home/cmdshadow/shadowops-bot/config/config.yaml"
BACKUP_FILE="/home/cmdshadow/shadowops-bot/config/config.yaml.backup-$(date +%Y%m%d-%H%M%S)"

echo "🔧 ShadowOps Config Update - TEST MODE"
echo "======================================"
echo ""

# Check if config exists
if [ ! -f "$CONFIG_FILE" ]; then
    echo "❌ Config file not found: $CONFIG_FILE"
    exit 1
fi

# Create backup
echo "💾 Creating backup..."
cp "$CONFIG_FILE" "$BACKUP_FILE"
echo "   ✅ Backup: $BACKUP_FILE"
echo ""

# Update scan intervals to 60s for testing
echo "⚡ Updating scan intervals to TEST MODE (60s)..."

sed -i 's/trivy: 21600.*/trivy: 60         # 60 Sekunden - FÜR TESTS! (Normal: 21600 = 6h)/' "$CONFIG_FILE"
sed -i 's/aide: 900.*/aide: 60          # 60 Sekunden - FÜR TESTS! (Normal: 900 = 15min)/' "$CONFIG_FILE"

echo "   ✅ trivy: 21600s → 60s"
echo "   ✅ aide: 900s → 60s"
echo "   ✅ crowdsec: 60s (unchanged)"
echo "   ✅ fail2ban: 60s (unchanged)"
echo ""

# Show new config
echo "📊 New scan intervals:"
grep -A 4 "scan_intervals:" "$CONFIG_FILE" | grep -v "^#"
echo ""

echo "✅ Config updated successfully!"
echo ""
echo "🚀 Next steps:"
echo "   1. sudo systemctl restart shadowops-bot"
echo "   2. sudo journalctl -u shadowops-bot.service -f"
echo ""
echo "⏱️  All scanners will now run every 60 seconds!"
