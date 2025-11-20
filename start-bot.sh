#!/bin/bash
# ShadowOps Bot Single Instance Starter
# Stellt sicher, dass nur EINE Bot-Instanz läuft

BOT_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$BOT_DIR/.bot.pid"
LOG_FILE="/tmp/shadowops-bot.log"

cd "$BOT_DIR" || exit 1

# Prüfe, ob bereits eine Instanz läuft
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if ps -p "$OLD_PID" > /dev/null 2>&1; then
        echo "❌ Bot läuft bereits (PID: $OLD_PID)"
        echo "Zum Stoppen: kill $OLD_PID"
        exit 1
    else
        echo "⚠️ Stale PID file found, cleaning up..."
        rm -f "$PID_FILE"
    fi
fi

# Stoppe alle laufenden Bot-Instanzen (Cleanup)
echo "🧹 Cleanup: Stoppe alte Bot-Instanzen..."
pkill -9 -f "python.*src/bot.py" 2>/dev/null || true
sleep 2

# Aktiviere Virtual Environment und starte Bot
echo "🚀 Starte ShadowOps Bot..."
source venv/bin/activate

python3 src/bot.py > "$LOG_FILE" 2>&1 &
BOT_PID=$!

# Speichere PID
echo "$BOT_PID" > "$PID_FILE"

echo "✅ Bot gestartet (PID: $BOT_PID)"
echo "📊 Logs: tail -f $LOG_FILE"
echo "🛑 Stoppen: kill $BOT_PID"

# Zeige erste Log-Zeilen
sleep 3
tail -20 "$LOG_FILE"
