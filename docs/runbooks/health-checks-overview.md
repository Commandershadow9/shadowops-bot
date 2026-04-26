# Enterprise-Health-Checks — Vollständige Übersicht

**Status:** Live seit 2026-04-26 (Issue #278, PRs #184 + Phase 5c)
**Pfad:** `src/integrations/project_monitor.py` + `src/cogs/cron_heartbeat.py`

## Defense-in-Depth-Map

```
                         User-Browser → Production
                              │
                              ▼
    ┌─────────────────────────────────────────────────────┐
    │  ZERODOX-API (Next.js)                              │
    │   ├─ /api/health (HTTP 200 = liveness)             │
    │   └─ /api/internal/health-stats (DB-Pool, Logins)  │ ← Auth via X-Agent-Key
    └─────────────────────────────────────────────────────┘
                              │
                              │ aiohttp polling
                              ▼
    ┌─────────────────────────────────────────────────────┐
    │  ShadowOps-Bot (systemd-Service mit Restart=on-failure)
    │   ProjectMonitor — _monitor_project() Loop         │
    │   ┌─────────────────────────────────────────────┐  │
    │   │  Existing (Phase 1-3):                      │  │
    │   │   • _check_project_health (HTTP-Liveness)   │  │
    │   │   • _check_systemd_health                   │  │
    │   │   • _check_tcp_ports                        │  │
    │   │   • _check_project_logs                     │  │
    │   ├─────────────────────────────────────────────┤  │
    │   │  Phase 5b (Server-Resources):               │  │
    │   │   • _check_disk_space          → critical   │  │
    │   │   • _check_memory_usage        → critical   │  │
    │   │   • _check_container_restart_count → bot-status │
    │   │   • _check_ssl_cert_expiry     → bot-status │  │
    │   │   • _check_backup_freshness    → backups    │  │
    │   ├─────────────────────────────────────────────┤  │
    │   │  Phase 5c (App-Insights via internal API):  │  │
    │   │   • _check_db_pool_saturation  → ci-zerodox │  │
    │   │   • _check_failed_login_rate   → critical   │  │
    │   └─────────────────────────────────────────────┘  │
    │                                                     │
    │  CronHeartbeatCog (separate task.loop)              │
    │   • Watcht /home/cmdshadow/ZERODOX/logs/synthetic-monitor.log
    │   • Mtime > 35 Min → ci-zerodox                     │
    └─────────────────────────────────────────────────────┘
```

## Check-Tabelle (alle 7 Health-Checks)

| Check | Was geprüft | Schwelle | Severity | Channel | Cooldown | Frequenz |
|-------|-------------|----------|----------|---------|----------|----------|
| **Disk-Space** | `shutil.disk_usage(path)` | < 15% frei | CRITICAL | `🚨-critical` | 60 Min | 5 Min |
| **Memory** | `docker stats --no-stream` | > 90% | CRITICAL | `🚨-critical` | 60 Min | 60s |
| **Restart-Count** | `docker inspect → RestartCount` | > 3 / 24h | MEDIUM | `🤖-bot-status` | 6h | 1h |
| **SSL-Cert** | `asyncio.open_connection` + cert.notAfter | < 30 Tage | HIGH (<7d), MEDIUM | `🤖-bot-status` | 24h | 6h |
| **Backup** | Mtime von `<path>/backups/daily/` neuestem File | > 25h alt | HIGH | `backup-dashboard` | 60 Min | 30 Min |
| **DB-Pool-Saturation** | `pg_stat_activity` / `max_connections` via API | > 80% | HIGH | `🧪-ci-zerodox` | 30 Min | 5 Min |
| **Failed-Login-Rate** | `LoginAttempt.success=false WHERE createdAt > NOW()-5min` via API | > 100 / 5 Min | HIGH (CRITICAL >500) | `🚨-critical` | 15 Min | 60s |

## Konfiguration

`config/config.yaml` pro Projekt:

```yaml
projects:
  zerodox:
    monitor:
      url: https://zerodox.de/api/health
      check_interval: 60
      container: zerodox-web
      # Phase 5c: App-Insights API
      internal_health_endpoint: https://zerodox.de/api/internal/health-stats
      health_api_key_env: ZERODOX_AGENT_API_KEY
      # Schwellen pro Projekt überschreibbar (Defaults siehe HEALTH_CHECK_DEFAULTS)
      thresholds:
        disk_warn_percent: 15
        memory_warn_percent: 90
        restart_count_warn: 3
        ssl_cert_warn_days: 30
        backup_max_age_hours: 25
        db_pool_saturation_warn: 80
        failed_login_per_5min_warn: 100
```

## Auth: ZERODOX_AGENT_API_KEY

Der Bot-Service (`/etc/systemd/system/shadowops-bot.service`) lädt env-vars aus `/home/cmdshadow/shadowops-bot/.env`. Dort muss stehen:

```
ZERODOX_AGENT_API_KEY=<gleicher Wert wie in /home/cmdshadow/ZERODOX/.env>
```

**Wichtig:** Der Key muss in BEIDEN .env-Dateien identisch sein. Bei Rotation:
1. Neuen Key in ZERODOX `.env`
2. Gleichen Key in shadowops-bot `.env`
3. Beide Services restarten

## Anti-Spam-Pattern

- **Cooldown pro Check + Project**: nach Alert wird `f"{project.name}:{check_type}"` in `_health_check_alerts: Dict[str, datetime]` gespeichert. Zweiter Trigger innerhalb der Cooldown-Zeit → kein neuer Alert.
- **Recovery-Reset**: bei Threshold wieder OK → `_clear_health_alert_cooldown()` löscht den Eintrag. Nächste Verletzung alarmiert sofort.
- **Min-Intervall-Filter**: `_should_run_health_check()` skippt Checks die noch im Min-Intervall sind (auch wenn der Loop sie aufruft).

## Wenn ein neuer Check hinzukommt

1. Methode `_check_<name>` schreiben (Pattern: `_check_disk_space`)
2. Defaults in `HEALTH_CHECK_DEFAULTS` ergänzen
3. Min-Intervall in `HEALTH_CHECK_MIN_INTERVAL_SECONDS`
4. Cooldown in `HEALTH_CHECK_ALERT_COOLDOWNS`
5. In `_monitor_project()` Loop aufrufen
6. Tests in `tests/unit/test_health_checks_extension.py` (mind. 3 Tests pro Check: trigger, no-trigger, cooldown)
7. Diese Doku updaten

## Slash-Commands für Manual-Test

(Folgeaufgabe — aktuell nicht implementiert): `/healthcheck zerodox <type>` würde einen einzelnen Check sofort triggern, unabhängig vom Min-Intervall. Sinnvoll für Debugging.

## Lehre aus dem 11-Tage-CSP-Outage

Vor Phase 5 hatten wir nur HTTP-Liveness-Checks. Der Outage konnte 11 Tage unbemerkt bleiben weil:
- HTTP 200 ✓ (Liveness OK, aber Frontend tot)
- Container running ✓
- Keine Server-Resource-Issues ✓

Mit Phase 5b/c hätten wir zusätzliche Signale gehabt:
- DB-Pool wäre niedrig gewesen → erwartbar, aber im Vergleich zum Vorzustand seltsam
- Failed-Login-Rate wäre niedrig gewesen → User können nicht buchen, also weniger Aktivität

Für DEN spezifischen CSP-Bug ist der Synthetic-Monitor (Phase 4) der richtige Layer. Phase 5 ergänzt für klassische Server-Outages.

---

**Verwandt:**
- `docs/runbooks/deploy-hardening.md` — Auto-Deploy via deploy.sh
- `docs/runbooks/discord-routing.md` — Channel-Map aller Notifications
- ZERODOX `docs/SECURITY_CSP.md` — CSP-Strategie + Defense-in-Depth

**Erstellt:** 2026-04-26 nach Abschluss Issue #278.
