# Discord-Routing für Notifications (DEV Server)

## Zielsetzung

Alle Notifications laufen zentral im **DEV Commandershadow** Discord-Server (`1438065435496157267`) zusammen. Pro Notification-Typ ein dedizierter Channel — kein Spam, klare Trennung.

## Channel-Mapping (Stand 2026-04-26)

### 🔐 Security Monitoring (Kategorie 1441655477820981361)
| Channel | ID | Wer postet | Was |
|---------|-----|-----------|-----|
| `🚨-critical` | `1441655480840617994` | ShadowOps `_send_incident_alert`, project_monitor (HTTP rot, OOM, Brute-Force) | Sofortige Reaktion erforderlich |
| `🚫-fail2ban` | `1441655483781087283` | Fail2ban-Cog | IP-Bans |
| `🐳-docker` | `1441655484628336682` | Trivy-Scan-Cog | Container-Vulnerabilities |
| `🛡️-crowdsec` | `1442390476631183423` | CrowdSec-Integration | Threat-Alerts |
| `⚡-guildscout` | `1444840133244354696` | GuildScout Verification | Performance-Issues |
| `🛡-security-briefing` | `1480646063390982215` | Security Analyst Agent | Autonome Briefings |

### 📦 System & Projekte (Kategorie 1441655479867805727)
| Channel | ID | Wer postet | Was |
|---------|-----|-----------|-----|
| `🤖-bot-status` | `1441655486981214309` | ShadowOps Startup-Logs, Recovery-Alerts | Bot-System-Status |
| `👥-customer-alerts` | `1441655498515550370` | ZERODOX Eskalationen (Kunden) | DSGVO-/Customer-relevant |
| `🚀-deployment-log` | `1441655502441414675` | ShadowOps `DeploymentManager._send_deployment_success`/`_failure` (alle Auto-Deploys). `deploy.sh` selbst postet **nicht** nach Discord — der Embed kommt aus dem Bot (Issue mayday-sim#504) | Deploy-Erfolg/Fehler/Rollback (intern, DEV-Server) |
| `📊-dashboard` | `1479615549356114124` | ShadowOps `_update_dashboard_loop` (5 Min Update) | Live Status aller Projekte |
| `🎮-mayday-sim` | `1486896113503043725` | MayDay Sim Health | Spezial-Projekt |

### 📢 Updates & CI (Kategorie 1442390475700043868)
| Channel | ID | Wer postet | Was |
|---------|-----|-----------|-----|
| `📋-updates-zerodox` | `1454497926666522859` | ShadowOps Patch-Notes-Engine | ZERODOX Versions-Updates |
| `📋-updates-guildscout` | `1442390481777594439` | ShadowOps Patch-Notes | GuildScout Updates |
| `📋-updates-shadowops` | `1442390482578575455` | ShadowOps Patch-Notes | Bot Updates |
| `📋-updates-agents` | `1482817123960225802` | ShadowOps Patch-Notes | AI-Agent-Framework |
| `🧪-ci-zerodox` | `1463512208083521577` | `synthetic-monitor.sh` (via `DISCORD_MONITOR_WEBHOOK`), CronHeartbeatCog (ZERODOX-Override) | CI/CD-Test-Ergebnisse + Live-Frontend-Smoke |
| `updates-database-ports` | `1486637661862232114` | Patch-Notes | DB-Ports Updates |
| `updates-mayday_sim` | `1486896140321292459` | Patch-Notes | MayDay Updates |

### Sonstige
- `🤖-agent-reviews` (`1493613914062323712`) — Claude-Reviews für Agent-PRs
- SEO-Kategorie (vier Channels) — vollständig SEO-Agent
- Backups: `backup-dashboard` (`1486479593602023486`)

## Externe Kunden-Deploy-Posts (nicht DEV-Server)

Zusätzlich zu den internen Posts oben sendet ShadowOps Deploy-Embeds an **Kunden-Discord-Server** — konfiguriert pro Projekt über `external_notifications` in `config.yaml`. Versand: `DeploymentManager._forward_deploy_to_external` → `external_notifications[].deploy_channel_id` (nur wenn `notify_on.deployments: true`).

| Projekt | Server | Channel | ID | Gate |
|---------|--------|---------|-----|------|
| mayday-sim | MayDay Sim (`1486692590198853672`) | `🚀-deploy-log` | `1486899717362421840` | `notify_on.deployments: true` |

**⚠️ Repo-Name (Bindestrich) vs. Config-Key (Underscore):** `_forward_deploy_to_external` bekommt den GitHub-Repo-Namen (`mayday-sim`) und muss ihn dash/underscore-tolerant auf den Config-Key (`mayday_sim`) auflösen. Fehlt die Normalisierung, bleibt der Post **still** aus — genau das war der Vorfall **#316 / Issue mayday-sim#504** (Channel wochenlang leer, ohne Fehler im Log). Restschulden gleichen Musters in `notifications_mixin.py`: **#317**.

**Verwechslungsgefahr:** Der externe `#🚀-deploy-log` (Kunden, ID endet `…421840`) ist **nicht** der interne `#🚀-deployment-log` (DEV-Server, ID endet `…414675`). Zwei verschiedene Channels auf zwei verschiedenen Servern.

## Webhook-Routing (`.env` ZERODOX)

`DISCORD_DEPLOY_WEBHOOK` und `DISCORD_MONITOR_WEBHOOK` sind in `.env` der ZERODOX-App konfiguriert (auf dem VPS, nicht in Git wegen Secrets).

**Wenn diese Webhooks rotiert werden müssen:**
1. Alten Webhook im Channel-Settings → Integrations → Webhooks löschen
2. Neuen Webhook erstellen mit gleichem Namen (`ZERODOX Deploy` bzw. `ZERODOX Synthetic Monitor`)
3. URL kopieren + in `.env` der ZERODOX-App eintragen
4. **Kein Service-Restart nötig** — Skripts lesen `.env` bei jedem Run neu

## Code-Routing (im Bot)

`HeartbeatTarget` in `src/cogs/cron_heartbeat.py` hat ein `channel_id_override`-Feld. Pro Target kann ein anderer Channel gesetzt werden — z.B. ZERODOX-Heartbeat geht in `🧪-ci-zerodox` statt allgemeines `🚨-critical`.

```python
HEARTBEAT_TARGETS = [
    HeartbeatTarget(
        name="ZERODOX Synthetic Monitor",
        log_path=Path("/home/cmdshadow/ZERODOX/logs/synthetic-monitor.log"),
        expected_interval_minutes=15,
        channel_id_override=1463512208083521577,  # 🧪-ci-zerodox
    ),
]
```

## Anti-Spam-Pattern

- **CronHeartbeatCog:** 60 Min Cooldown pro Target zwischen Alerts
- **`synthetic-monitor.sh`:** Stille bei OK (kein Alert wenn nichts kaputt)
- **`deploy.sh`:** nur bei Failure + bei Erfolg eine Zusammenfassung
- **ShadowOps `project_monitor`:** Recovery-Alerts (statt Spam) wenn Service wieder online

## Lehre aus dem ZERODOX-Vorfall 2026-04-13/14

Vor der Konsolidierung 2026-04-26:
- `DISCORD_DEPLOY_WEBHOOK` war NICHT in `.env` gesetzt → Deploy-Notifications waren stumm
- `synthetic-monitor.sh` schrieb in `DISCORD_ESCALATION_WEBHOOK_URL` (Eskalation = Kunden!)
- CronHeartbeatCog schrieb in `🚨-critical` (Server-Security-Channel) statt projekt-spezifisch

Diese Fragmentierung war einer der Gründe, warum der 11-Tage-Outage unbemerkt blieb — Notifications kamen entweder nicht an oder im falschen Channel.

## Verwandt

- `deploy-hardening.md` — Auto-Deploy via `deploy.sh`
- ZERODOX `docs/SECURITY_CSP.md` — Defense-in-Depth-Strategie

---

**Erstellt:** 2026-04-26 nach Härtungs-Sprint-Konsolidierung.
