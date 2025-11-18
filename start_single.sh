#!/bin/bash
PIDFILE=/tmp/shadowops-bot.pid

# Kill if already running
if [ -f "$PIDFILE" ]; then
    OLD_PID=$(cat "$PIDFILE")
    kill -9 "$OLD_PID" 2>/dev/null
    rm -f "$PIDFILE"
fi

# Start bot
cd /home/cmdshadow/shadowops-bot
venv/bin/python3 src/bot.py > /tmp/shadowops-fresh.log 2>&1 &
echo $! > "$PIDFILE"
echo "Bot started with PID: $(cat $PIDFILE)"
