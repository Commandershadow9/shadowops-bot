# Monitoring-Inventar (Single Source of Truth)

> **Zweck:** Vollständige Liste *aller* Health/Monitoring/Auto-Heal-Mechanismen über alle Projekte. Diese Datei ist die SSoT für die Migration zur zentralen ShadowOps-Engine (Spec: `docs/2026-06-09-zentrales-monitoring-auto-health-design.md`). Sie existiert, damit beim nächsten Server-Umzug **kein Check verloren geht** (Lehre aus mayday-sim#491).
>
> **Stand:** 2026-06-10 — **Plan 1+2+3 live** (Engine + 6 ZERODOX-Checks, siehe §6/§7 + Gesamtstand unten). **Status-Spalte:** `aktiv` = läuft als Cron/Watchdog · `→ Engine` = soll in ShadowOps wandern · `Dead-Man` = bleibt bewusst extern · `abgelöst:<id>` = von ShadowOps übernommen (nach Cut-over-Kriterien §8).

## Legende Ziel-Check-Typ / Heal-Stufe

- **Check-Typen:** `http` · `script` (synthetic) · `resource` (disk/mem/netz) · `container` · `report` (kein Health/Heal)
- **Heal-Stufen:** `reversible-auto` (Container/Service/Netz-Restart, autonom) · `approval` (riskant, Discord-Freigabe) · `alert-only`

---

## 1 · cmdshadow-Crontab — Health/Monitoring-Crons

| id | Script | Intervall | Kategorie | Ziel-Typ | Heal heute | Status |
|---|---|---|---|---|---|---|
| zerodox-health | `cron-health-check.sh` | */10 | liveness | `http` | alert-only (curl /api/cron/health) | → Engine |
| zerodox-onboarding-smoke | `synthetic-monitor.sh` | */15 | funktional | `script` | alert-only | → Engine |
| akquise-liveness | `akquise-ai-watchdog.sh` | */5 | liveness | `http` (:9300) | alert-only | → Engine |
| akquise-synthetic | `akquise-ai-synthetic-check.sh` | */15 | funktional | `script` | alert-only | → Engine |
| agent-listener | `cron-agent-listener-health.sh` | */5 | funktional | `http`/`script` | alert-only (pg_notify-Listener) | → Engine |
| analytics-bridge | `ensure-analytics-network.sh` | @reboot+*/10 | resource/netz | `container` | **network-reconnect** (reversible-auto) | → Engine |
| ci-main-health | `ci-main-health-check.sh` | hourly | meta/CI | `http` (GitHub) | alert-only | → Engine (oder report) |
| billing-pdf-drift | `billing-pdf-drift-check` (curl) | daily 04:50 | business | `http` | alert-only | → Engine |
| soak-monitor | `cron-soak-monitor.sh` | daily 07:30 | report | `report` | — | Phase-0-Entscheid (bleibt Cron) |
| stale-pr-monitor | `cron-stale-pr-monitor.sh` | Mo 06:00 | report | `report` | — | bleibt Cron |
| backup-monitor | `cron-backup-monitor.sh` | Mo 07:00 | report | `report` | — | bleibt Cron |

> **Phase-0-Entscheidung (Defaults):** Report-only-Crons (soak/stale-pr/backup-monitor) bleiben vorerst Cron — kein Health/Heal, niedrige Prio. ci-main-health kann als `http`-Check rein.

## 2 · user-systemd Watchdog-Schicht (~14 aktiv / 28 Units)

| Watchdog | Mode | Target | Ziel-Typ | Status |
|---|---|---|---|---|
| **shadowops-watchdog** | http | :8766/health (bot_ready Pflicht) | — | **Dead-Man (bleibt extern)** |
| **shadowops-drift-watchdog** | systemd-state + drift | shadowops-bot State + NRestarts-Loop | — | **Dead-Man (bleibt extern)** |
| zerodox-watchdog | http | https://zerodox.de/api/health | `http` | → Engine |
| guildscout-watchdog | http | localhost:8765/health | `http` | → Engine |
| mayday-sim-watchdog | http | 127.0.0.1:3200/api/health | `http` | → Engine |
| zerodox-akquise-ai-watchdog | http | 172.19.0.1:9300/health | `http` | → Engine |
| mayday-ci-runner-watchdog | http + jq | 10.8.0.10:9100/health (`.components.ci_runner.ok`) | `http` | → Engine |
| mayday-sim-build-drift-watchdog | build-drift | :3200/api/build-id vs origin/main | `script` | → Engine |
| disk-hygiene-watchdog | disk + auto-prune | Disk >85% prune, >90% alarm | `resource` | → Engine (heal: disk-prune = reversible-auto) |
| memory-watchdog | meminfo | RAM ≥90% / Swap ≥80% | `resource` | → Engine |
| ai-agent-framework-watchdog | systemd | guildscout-feedback/zerodox-support/seo-agent | `container`/`http` | → Engine |
| cmdshadow-design-watchdog (+healthcheck) | systemd-result | cmdshadow-design-healthcheck (max_age 36h) | `script` | → Engine |
| doku-drift-watchdog | doku-drift | Container-Ports vs Port-Map + MEMORY.md-Limit | `report` | bleibt (oder report) |
| ki-cost-watchdog | ki-cost | Token/Kosten-Rollup Claude+Codex | `report` | bleibt (report) |
| check-worker-drift | systemd | Worker-Daemon-Drift | `script` | → Engine |

## 3 · ShadowOps `project_monitor` — was schon zentral läuft

Bestehende `_check_*`-Methoden pro Projekt (HTTP-health, systemd, TCP, log-pattern, disk, memory, container-restart, SSL-cert, backup-freshness, db-pool, failed-login, onboarding-smoke, critical-endpoint-5xx) + `auto_remediation` (balanced approval, AI-Fix codex→claude). **Diese bleiben** — die deklarative `checks:`-Schicht (neu) ergänzt sie und löst die verstreuten Crons/Watchdogs ab.

## 4 · Offen für Phase-0-Vervollständigung

- [ ] GuildScout-/MayDay-eigene Crons (`/srv/leitstelle/scripts/cron-*.sh`, GuildScout-Container-Crons) — separat katalogisieren, falls Checks außerhalb der cmdshadow-Crontab existieren.
- [ ] Watchdog-Webhook-Targets je Unit (`~/.config/shadowops-watchdog.env`) dokumentieren.
- [ ] Pro `→ Engine`-Eintrag: deklarativen `checks:`-Block in config.yaml schreiben (Plan 2/3).

## 5 · Migrations-Status-Tracking

Wenn ein Check via ShadowOps `checks:` übernommen + nach Cut-over-Kriterien (Spec §8) verifiziert ist: Status hier auf `abgelöst:<check-id>` setzen und Alt-Cron/-Watchdog `disable` (48 h Beobachtung, dann entfernen). Dead-Man-Einträge werden **nie** abgelöst.

## 6 · Plan 2 — ZERODOX-Migrations-Status (2026-06-10)

Engine-Erweiterung: HTTP-Header (`$ENV`-Auflösung) + `container`-Check-Typ (network-attached). 4 ZERODOX-Checks deklarativ aktiv (config.yaml), real verifiziert:

| Check | Engine-Status | Heal | Alt-Quelle | Cut-over-Stand |
|---|---|---|---|---|
| `zerodox-health` | ✅ aktiv (http) | alert-only (Auto-Rollback via remediation bleibt) | cron-health-check.sh */10 | **Cron disabled (06-10, 48h-Soak)** — Watchdog + project_monitor bleiben |
| `akquise-liveness` | ✅ aktiv (http) | alert-only | akquise-ai-watchdog.sh */5 | **Cron disabled (06-10, 48h-Soak)** — systemd-Watchdog bleibt |
| `analytics-bridge` | ✅ aktiv (container) | **network-reconnect (real getriggert + autonom geheilt ✅)** | ensure-analytics-network.sh */10 | **Cron disabled (06-10)** — `@reboot`-Zeile behalten |
| `zerodox-onboarding-smoke` | ✅ aktiv (http+header) | alert-only | synthetic-monitor.sh (1 Sub-Check) | **Alt behalten** (andere Sub-Checks noch nicht migriert) |

**Real-Trigger-Beweise:** analytics-bridge (Netz getrennt → Engine reconnect ~27s + Discord-Alert), chaos-container (Plan-1-Verifikation). http-Checks im Normalzustand OK-verifiziert.

**Noch NICHT migriert (Plan 3):** `agent-listener`/`ci-main-health`/`akquise-synthetic` (Secrets fehlen im Bot: CRON_API_KEY/GITHUB_PAT/AKQUISE_AI_BEARER_TOKEN), `synthetic-frontend/csp/functional` (Chrome/Playwright + POST-Body-Check), GuildScout/MayDay, Dead-Man-Härtung.

**Nach 48h-Soak (ab 2026-06-12):** disabled Crons entfernen (oder bei Divergenz reaktivieren). Backup: `/tmp/crontab-backup-plan2-*.txt`.

## 7 · Plan 3 — Secret-Checks (2026-06-10)

Engine-Erweiterung: HTTP-Header `$ENV` **eingebettet** (`Bearer $TOKEN` via `os.path.expandvars`), POST-Body, `json_schema`-Check (dot-path-fähig). Secrets (CRON_API_KEY/GITHUB_PAT/AKQUISE_AI_BEARER_TOKEN) in shadowops-bot/.env kopiert.

| Check | Engine-Status | Heal | Alt-Quelle | Cut-over |
|---|---|---|---|---|
| `agent-listener` | ✅ aktiv (http + X-API-Key, json_path healthy=true) | alert-only | cron-agent-listener-health.sh */5 | **Cron BEHALTEN** (erstellt GitHub-Issues — Aktion außerhalb Engine) |
| `akquise-synthetic` | ✅ aktiv (POST + Bearer + json_schema result.*, real 29s) | alert-only | akquise-ai-synthetic-check.sh */15 | **Cron disabled (06-10, Soak)** |

**Real verifiziert:** agent-listener (healthy=true), akquise-synthetic (POST gegen :9300, result.hook/finding/bridge non-empty, 29s).

**Bewusst zurückgestellt:** `ci-main-health` — braucht age-Logik (CI rot >1h) + Array-Index-Parse, die ein reiner `json_path` nicht leistet; Cron + GitHub-CI decken zuverlässig ab. Aufwand > Mehrwert.

**Secret-Wartung:** Bei Rotation von CRON_API_KEY/AKQUISE_AI_BEARER_TOKEN/GITHUB_PAT jetzt an **zwei** Orten (ZERODOX/.env + shadowops-bot/.env). Backup Plan-3-Crontab: `/tmp/crontab-backup-plan3-*.txt`.

---

## Zentralisierung — Gesamtstand (Plan 1+2+3)

**Engine** (Plan 1): deklaratives Check-Inventar, gestuftes Heal (reversibel-autonom/approval/alert-only), Circuit-Breaker, Maintenance-Gate, Discord-Alert, Dead-Man-Watchdog extern. Check-Typen: http (+header/POST/json_path/json_schema), script, container.

**Aktive ZERODOX-Engine-Checks (6):** zerodox-health, akquise-liveness, zerodox-onboarding-smoke, analytics-bridge (⭐ Auto-Heal), agent-listener, akquise-synthetic.

**Abgelöste Crons (Soak):** cron-health-check, akquise-ai-watchdog, ensure-analytics-network (*/10), akquise-ai-synthetic-check. **Behalten:** @reboot-ensure-analytics, cron-agent-listener-health (Issues), synthetic-monitor (Chrome/Playwright-Sub-Checks), alle Watchdogs (Defense-in-Depth).

**Offen (kein klarer Mehrwert / Operator):** ci-main-health (age-Logik), synthetic-frontend/csp/functional (Browser), GuildScout/MayDay (redundant zu project_monitor+Watchdog).
