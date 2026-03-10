---
name: health-check
description: Vollstaendiger Health-Check des ShadowOps Bot
---

# Health Check

## Schritte

1. **systemd Service Status:**
   ```bash
   sudo systemctl status shadowops-bot --no-pager -l | head -15
   ```

2. **Prozess-Check:**
   ```bash
   pgrep -af "shadowops-bot.*bot.py"
   ```

3. **Port-Check:**
   ```bash
   echo "Health (8766):" && curl -s http://localhost:8766/health 2>/dev/null || echo "DOWN"
   echo "GitHub Webhook (9090):" && ss -tlnp | grep 9090 || echo "DOWN"
   echo "GuildScout Alerts (9091):" && ss -tlnp | grep 9091 || echo "DOWN"
   ```

4. **Letzte Fehler (1h):**
   ```bash
   journalctl -u shadowops-bot --since "1 hour ago" --no-pager | grep -i "error\|critical\|traceback" | tail -10
   ```

5. **Log-Datei heute:**
   ```bash
   LOG="logs/shadowops_$(date +%Y%m%d).log"
   [ -f "$LOG" ] && echo "Log: $LOG ($(wc -l < "$LOG") Zeilen)" || echo "Kein Log fuer heute"
   ```

6. **Speicher-Check:**
   ```bash
   ps -p $(pgrep -f "shadowops-bot.*bot.py") -o pid,rss,vsz,pcpu --no-headers 2>/dev/null | awk '{printf "PID: %s, RAM: %.1f MB, CPU: %s%%\n", $1, $2/1024, $4}'
   ```

7. **Discord-Connection:**
   ```bash
   grep "einsatzbereit" "logs/shadowops_$(date +%Y%m%d).log" | tail -1
   ```
