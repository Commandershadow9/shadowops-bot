#!/bin/bash
# Bot Diagnose & Cleanup Script
# Findet und behebt Bot-Instanz-Probleme

set -e

BOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$BOT_DIR"

echo "🔍 ShadowOps Bot Diagnose"
echo "========================="
echo ""

# 1. Prüfe PID Datei
echo "📄 PID-Datei Status:"
if [ -f ".bot.pid" ]; then
    PID=$(cat .bot.pid)
    echo "   PID-Datei existiert: .bot.pid"
    echo "   Gespeicherte PID: $PID"

    if ps -p "$PID" > /dev/null 2>&1; then
        echo "   ✅ Prozess $PID läuft noch"
        ps -p "$PID" -f
    else
        echo "   ❌ Prozess $PID läuft NICHT mehr (stale PID file)"
    fi
else
    echo "   ⚠️  Keine PID-Datei gefunden"
fi
echo ""

# 2. Finde alle laufenden Bot-Instanzen
echo "🔍 Laufende Bot-Prozesse:"
BOT_PROCS=$(ps aux | grep "python.*src/bot.py" | grep -v grep || true)
if [ -z "$BOT_PROCS" ]; then
    echo "   ✅ Keine Bot-Prozesse gefunden"
else
    echo "$BOT_PROCS"
    BOT_PIDS=$(ps aux | grep "python.*src/bot.py" | grep -v grep | awk '{print $2}')
    echo ""
    echo "   PIDs: $BOT_PIDS"
fi
echo ""

# 3. Prüfe systemd services
echo "🔍 Systemd Services:"
SYSTEMD_SERVICES=$(systemctl list-units --type=service --all 2>/dev/null | grep -i "shadow\|bot" || true)
if [ -z "$SYSTEMD_SERVICES" ]; then
    echo "   ✅ Keine systemd services gefunden"
else
    echo "$SYSTEMD_SERVICES"
fi
echo ""

# 4. Prüfe cron jobs
echo "🔍 Cron Jobs:"
CRON_JOBS=$(crontab -l 2>/dev/null | grep -i "shadow\|bot.py" || true)
if [ -z "$CRON_JOBS" ]; then
    echo "   ✅ Keine cron jobs gefunden"
else
    echo "$CRON_JOBS"
fi
echo ""

# 5. Log-Datei Status
echo "📊 Log-Datei Status:"
if [ -f "/tmp/shadowops-bot.log" ]; then
    LOG_SIZE=$(du -h /tmp/shadowops-bot.log | cut -f1)
    LOG_LINES=$(wc -l < /tmp/shadowops-bot.log)
    LOG_MTIME=$(stat -c %y /tmp/shadowops-bot.log)
    echo "   Datei: /tmp/shadowops-bot.log"
    echo "   Größe: $LOG_SIZE ($LOG_LINES Zeilen)"
    echo "   Letztes Update: $LOG_MTIME"
    echo ""
    echo "   📝 Letzte 3 Zeilen:"
    tail -3 /tmp/shadowops-bot.log | sed 's/^/      /'
else
    echo "   ⚠️  Log-Datei existiert nicht"
fi
echo ""

# 6. Cleanup-Optionen
echo "🧹 CLEANUP-OPTIONEN:"
echo "========================="
echo ""
echo "Was möchtest du tun?"
echo ""
echo "  1) CLEANUP: Alle Bot-Prozesse stoppen + PID-Datei löschen"
echo "  2) HARD RESET: Cleanup + Log-Datei löschen + Neustart"
echo "  3) NUR ANZEIGEN (keine Änderungen)"
echo ""
read -p "Wähle Option (1/2/3): " -n 1 -r
echo ""
echo ""

case $REPLY in
    1)
        echo "🧹 CLEANUP: Stoppe Bot-Prozesse..."

        # Stoppe alle Bot-Prozesse
        if [ -n "$BOT_PIDS" ]; then
            echo "   Stoppe PIDs: $BOT_PIDS"
            for PID in $BOT_PIDS; do
                kill -9 "$PID" 2>/dev/null || true
                echo "   ✅ PID $PID gestoppt"
            done
        fi

        # Lösche PID-Datei
        if [ -f ".bot.pid" ]; then
            rm -f .bot.pid
            echo "   ✅ PID-Datei gelöscht"
        fi

        # Warte und prüfe
        sleep 2
        REMAINING=$(ps aux | grep "python.*src/bot.py" | grep -v grep || true)
        if [ -z "$REMAINING" ]; then
            echo ""
            echo "✅ Cleanup erfolgreich! Keine Bot-Prozesse mehr aktiv."
            echo ""
            echo "🚀 Starte Bot neu: ./start-bot.sh"
        else
            echo ""
            echo "⚠️  Warnung: Noch Bot-Prozesse aktiv:"
            echo "$REMAINING"
        fi
        ;;

    2)
        echo "🧹 HARD RESET: Kompletter Neustart..."

        # Stoppe alle Bot-Prozesse
        if [ -n "$BOT_PIDS" ]; then
            echo "   Stoppe PIDs: $BOT_PIDS"
            for PID in $BOT_PIDS; do
                kill -9 "$PID" 2>/dev/null || true
            done
        fi

        # Lösche PID-Datei
        rm -f .bot.pid
        echo "   ✅ PID-Datei gelöscht"

        # Lösche alte Log-Datei
        if [ -f "/tmp/shadowops-bot.log" ]; then
            rm -f /tmp/shadowops-bot.log
            echo "   ✅ Alte Log-Datei gelöscht"
        fi

        sleep 3

        # Starte Bot neu
        echo ""
        echo "🚀 Starte Bot NEU..."
        ./start-bot.sh
        ;;

    3)
        echo "ℹ️  Keine Änderungen vorgenommen"
        ;;

    *)
        echo "❌ Ungültige Option"
        ;;
esac
