#!/bin/bash

# Ollama Live Monitor
# Zeigt in Echtzeit was Ollama gerade macht

echo "╔═══════════════════════════════════════════════════════════╗"
echo "║            🔍 OLLAMA LIVE MONITOR                         ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo ""

while true; do
    clear
    echo "╔═══════════════════════════════════════════════════════════╗"
    echo "║            🔍 OLLAMA LIVE MONITOR                         ║"
    echo "╚═══════════════════════════════════════════════════════════╝"
    echo ""

    # Timestamp
    echo "⏰ $(date '+%H:%M:%S')"
    echo ""

    # Ollama Status
    echo "📊 OLLAMA STATUS:"
    if curl -s http://localhost:11434/api/ps > /dev/null 2>&1; then
        echo "   ✅ Ollama läuft"
    else
        echo "   ❌ Ollama nicht erreichbar"
    fi
    echo ""

    # Loaded Models
    echo "🤖 GELADENE MODELLE:"
    models=$(curl -s http://localhost:11434/api/ps | jq -r '.models[]? | "   🧠 \(.name) | Size: \((.size/1024/1024/1024*100|floor)/100)GB | Expires: \(.expires_at)"' 2>/dev/null)
    if [ -z "$models" ]; then
        echo "   💤 Kein Modell geladen"
    else
        echo "$models"
    fi
    echo ""

    # System Resources
    echo "💻 SYSTEM RESOURCES:"
    free -h | grep "Mem:" | awk '{print "   RAM: "$3" / "$2" used ("$7" available)"}'

    # CPU Load
    cpu_load=$(uptime | awk -F'load average:' '{print $2}' | awk '{print $1, $2, $3}')
    echo "   CPU Load: $cpu_load"
    echo ""

    # Ollama Process
    echo "🔧 OLLAMA PROCESS:"
    ollama_pid=$(pgrep -f "ollama serve" | head -1)
    if [ -n "$ollama_pid" ]; then
        ps aux | grep "$ollama_pid" | grep -v grep | awk '{print "   PID: "$2" | CPU: "$3"% | RAM: "$6/1024"MB"}'
    else
        echo "   ❌ Prozess nicht gefunden"
    fi
    echo ""

    # ShadowOps Bot Logs (last 5 lines with "Ollama" or "llama")
    echo "📝 AKTUELLE BOT-LOGS (Ollama/llama):"
    tail -100 /tmp/shadowops-final.log 2>/dev/null | grep -iE "(ollama|llama|request|response)" | tail -5 | sed 's/^/   /'
    echo ""

    echo "──────────────────────────────────────────────────────────"
    echo "Aktualisierung alle 5 Sekunden... (Strg+C zum Beenden)"

    sleep 5
done
