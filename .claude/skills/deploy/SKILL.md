---
name: deploy
description: ShadowOps Bot deployen (restart mit optionalem git pull)
---

# Deploy

## Schritte

1. **Pre-Check:** Pruefe ob der Bot laeuft
   ```bash
   sudo systemctl status shadowops-bot --no-pager | head -5
   ```

2. **Optional: Code aktualisieren**
   Falls neue Aenderungen committet wurden:
   ```bash
   cd /home/cmdshadow/shadowops-bot && git pull --ff-only
   ```

3. **Restart**
   ```bash
   scripts/restart.sh --logs
   ```
   Oder mit Pull:
   ```bash
   scripts/restart.sh --pull --logs
   ```

4. **Verify:** Warte 30s, dann pruefe:
   ```bash
   sudo systemctl status shadowops-bot --no-pager | head -10
   curl -s http://localhost:8766/health || echo "Health Check failed"
   ```

5. **Log-Check:** Pruefe auf Fehler in den letzten 2 Minuten
   ```bash
   journalctl -u shadowops-bot --since "2 minutes ago" --no-pager | grep -i "error\|critical\|traceback"
   ```
