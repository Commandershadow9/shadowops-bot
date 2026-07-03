# ShadowOps Monitoring — Setup-Anleitung

> Externe Watchdogs für ShadowOps-Bot, ZERODOX und GuildScout + monatlicher
> Backup-Restore-Test. Alle Alerts gehen direkt via Discord-Webhook (nicht
> über den shadowops-bot selbst), damit auch ein toter Bot Alarme schlagen kann.

## Architektur

```
                              ┌─────────────────────────────┐
                              │ Discord #🩺-uptime-alerts   │
                              │ (System & Projekte Kategorie)│
                              └────────────▲────────────────┘
                                           │ Webhook (POST)
                                           │
       ┌─────────┬───────────┬─────────────┴────┬────────────┬────────────┐
       │         │           │                  │            │            │
   shadowops- zerodox-   guildscout-       mayday-sim-  ai-agent-      backup-test
   watchdog   watchdog   watchdog          watchdog     framework-     monatlich
   alle 5min  alle 5min  alle 5min         alle 5min    watchdog       1. d. Monats
                                                        alle 5min
       │         │           │                  │            │
       ▼         ▼           ▼                  ▼            ▼
  :8766/    https://    localhost:8765   maydaysim.de    systemctl --user
  health    zerodox.de  /health          /api/health     is-active
            /api/health                                  (3 Core-Agents)
```

Alle Watchdogs nutzen `scripts/service-watchdog.sh` — ein generisches
Script, parametrisiert via Env-Vars. Fünf Modi:
- `WATCHDOG_MODE=http` (Default): curl auf `WATCHDOG_HEALTH_URL`
- `WATCHDOG_MODE=systemd`: prüft `systemctl is-active` für jede Unit in `WATCHDOG_SYSTEMD_UNITS` (Komma-separiert)
- `WATCHDOG_MODE=systemd-result`: prüft Result + Alter (`ExecMainStartTimestamp`) des letzten Laufs für oneshot/Daily-Jobs. `WATCHDOG_MAX_AGE_HOURS` (Default 36h) — bei `stale_*h` → DOWN.
- `WATCHDOG_MODE=container`: prüft Docker-State + Healthcheck eines Containers (`WATCHDOG_CONTAINER`) via `docker inspect`. Für kritische Container ohne Host-Port.
- `WATCHDOG_MODE=pg-freshness` (seit 2026-06-27): führt `WATCHDOG_PG_QUERY` gegen einen Postgres-Container (`WATCHDOG_PG_CONTAINER`/`_USER`/`_DB`) aus; die Query MUSS eine Zahl = Alter in Stunden liefern, DOWN wenn > `WATCHDOG_MAX_AGE_HOURS` (Default 49h). Prüft die **Wirkung** eines Dienstes (z.B. frischer DB-Eintrag), nicht nur die Prozess-Existenz — fängt Services, die `active` sind aber deren Arbeit still scheitert (Vorfall seo-agent 2026-06-27).

**Optionaler JSON-Pfad-Filter (http-Mode):** `WATCHDOG_HEALTH_JQ_FILTER` — wenn gesetzt, wird der HTTP-Statuscode ignoriert und stattdessen eine jq-Boolean-Expression gegen den Response-Body ausgewertet. Nützlich wenn ein Endpoint HTTP 503 zurückgibt sobald *irgendeine* Komponente kaputt ist, aber nur eine bestimmte Komponente überwacht werden soll. Beispiel: `WATCHDOG_HEALTH_JQ_FILTER=.components.ci_runner.ok`. Test-Coverage: `tests/unit/test_service_watchdog_jq_filter.py`.

Das ursprüngliche `scripts/bot-watchdog.sh` bleibt als Backward-Compat-Variante
für den shadowops-bot Watchdog erhalten.

### Sonderrolle: cmdshadow-design

cmdshadow-design ist ein **Claude-Plugin (Multi-Skill Design-Tool), kein laufender Service**. Daher gibt's keinen kontinuierlich pingbaren Endpoint. Stattdessen läuft täglich um 06:00 `cmdshadow-design-healthcheck.service` (oneshot) mit 6 Stufen (Brand-Spec, Pre-Publish, Scripts, Vitest 26 Tests). Der Watchdog prüft alle 1h ob dieser Daily-Healthcheck **rechtzeitig erfolgreich gelaufen** ist. Wenn nicht (z.B. Timer broken, Service-Failure, Stale > 36h): Discord-Alert.

### Sonderrolle: memory-watchdog (seit 2026-05-25)

Memory-Druck ist ein **System-Ressourcen-Watchdog**, kein Service-Endpoint. Nutzt eigenes Skript `scripts/memory-watchdog.sh` (nicht `service-watchdog.sh`), weil die Datenquelle `/proc/meminfo` ist statt HTTP/systemd. Anlass: OOM-Cascade-Vorfall 2026-05-25 (earlyoom killte `systemd-logind` → start-limit-hit → SSH-Lockout). Frühwarnung bei:

- **RAM used ≥ `RAM_WARN_PCT`** (Default 90% — überschreibbar via Service Env)
- **Swap used ≥ `SWAP_WARN_PCT`** (Default 80%)

Beide Bedingungen sind ODER-verknüpft (Alarm wenn eine zutrifft). **Throttle:** dauerhafter Druck löst nur 1× pro `ALERT_THROTTLE_S` (Default 3600 = 60 min) einen Re-Alert aus. **Recovery-Alert** einmalig sobald beide Werte wieder unter Schwelle. State-File `data/watchdog_state_memory.json` enthält `last_state, last_alert_at, last_checked_at, last_ram_pct, last_swap_pct`.

Discord-Embed mit 5 Feldern: RAM (used/total/%), Available, Swap (used/total/%), Load (1/5/15m), PSI Memory (avg10/60/300).

### Sonderrolle: Selbstpflege-Watchdogs (seit 2026-05-30)

Drei System-Selbstpflege-Watchdogs ergänzen die Service-Watchdogs um automatische Hygiene und Drift-/Kosten-Erkennung. Nutzen eigene Skripte (nicht `service-watchdog.sh`), weil Datenquelle Disk/Doku/JSONL statt HTTP/systemd ist. Details auch in `~/.claude/rules/self-maintenance.md`.

- **`disk-hygiene-watchdog`** (stündlich): Zweistufig — Stufe 1 macht Auto-Prune (`docker builder prune` + alte Images + `journalctl --vacuum`) bei Disk > 85 %, Stufe 2 alarmt bei Disk > 90 %. State: `data/watchdog_state_disk-hygiene.json`. (Bugfix: `du|head` unter `set -e+pipefail` → SIGPIPE 141 → `|| true`.)
- **`doku-drift-watchdog`** (täglich 06:30): Vergleicht laufende Container-Ports gegen die Port-Maps in CLAUDE.md/infrastructure.md und prüft MEMORY.md-Zeilenlimit (<200). Nur Alarm, keine Auto-Korrektur. State: `data/watchdog_state_doku-drift.json`.
- **`ki-cost-watchdog`** (täglich 07:15): Rollup von Token/Kosten aus Claude- + Codex-JSONL + Anomalie-Alarm bei Ausreißern. Postet bevorzugt in `#💰-ki-kosten` (Fallback: `SHADOWOPS_WATCHDOG_WEBHOOK`). State: `data/watchdog_state_ki-cost.json`.

State-Files sind pro Service getrennt (`data/watchdog_state_<service>.json`),
damit Failure-Counter und Alert-Status sich nicht beeinflussen.

## Deklarative Aktivierung via `sync-watchdog-units.sh` (kanonisch, seit #294)

**Empfohlener Weg, um Watchdog-Units zu aktivieren.** Statt jede Unit manuell
zu verlinken und per `systemctl --user enable` einzeln zu aktivieren (siehe
Abschnitt 3 — bleibt als Referenz/Fallback erhalten), spiegelt
`scripts/sync-watchdog-units.sh` **alle** `deploy/*-watchdog.{service,timer}`
idempotent als Symlinks ins user-systemd-Verzeichnis und aktiviert die Timer.
Das macht die Aktivierung deklarativ (IaC) und erkennt Drift.

```bash
# 1. Vorschau — zeigt geplante Aktionen, ändert NICHTS (kein Symlink, kein systemctl)
~/shadowops-bot/scripts/sync-watchdog-units.sh --dry-run

# 2. Anwenden — Symlinks setzen/korrigieren + daemon-reload + Timer enable --now
~/shadowops-bot/scripts/sync-watchdog-units.sh

# 3. Drift-Check (z.B. für CI/Cron) — Exit 1, wenn Orphans existieren
~/shadowops-bot/scripts/sync-watchdog-units.sh --strict --dry-run
```

**Eigenschaften:**
- **Idempotent:** Korrekter Symlink → skip. Falscher/fehlender → neu setzen.
  `daemon-reload` läuft nur bei tatsächlichen Änderungen.
- **Orphan-Erkennung:** Units im Ziel-Verzeichnis, die auf
  `*-watchdog.{service,timer}` matchen, aber kein Pendant in `deploy/` haben,
  werden gemeldet (mit Hinweis ob reguläre Datei oder Symlink). Standard:
  **nur Report, kein Löschen.**
  - **Bekannter Orphan:** `audit-watchdog.{service,timer}` läuft live als
    reguläre Datei in `~/.config/systemd/user/` OHNE deploy/-Pendant. Das Skript
    meldet ihn als Orphan. (Folge-Entscheidung: entweder Units nach `deploy/`
    übernehmen → in IaC aufnehmen, oder bewusst außerhalb belassen.)
- **`--prune`:** Entfernt verwaiste **Symlinks** (niemals reguläre Dateien).
- **`--prune --force`:** Entfernt zusätzlich verwaiste **reguläre Dateien**
  (mit lautem Warnhinweis — vorsichtig nutzen).
- **`--strict`:** Exit-Code 1 bei Orphans (für ein Drift-Gate in CI/Cron).
- **Ziel-Verzeichnis** überschreibbar via `WATCHDOG_UNIT_DIR` (Default
  `~/.config/systemd/user`) — wird in Tests auf ein tmp-Verzeichnis gesetzt.

**Sicherheit:** Im `--dry-run` werden **keine** systemd-Calls ausgeführt; alle
`systemctl --user`-Aufrufe tragen dann das `(dry-run)`-Präfix. Test-Coverage:
`tests/unit/test_sync_watchdog_units.py`.

> Webhook-Config (`~/.config/shadowops-watchdog.env`) wird vom Skript **nicht**
> angefasst — die muss wie unten beschrieben einmalig angelegt werden.

## Erst-Einrichtung (einmalig)

### 1. Discord-Webhook erstellen

1. In Discord: **Server-Einstellungen → Integrationen → Webhooks**
2. **"Neuer Webhook"** klicken
3. Name: `ShadowOps Watchdog`, Channel: `🩺-uptime-alerts` (Kategorie `📦 System & Projekte`)
   - **Wichtig:** NICHT in `🚨-critical` posten — das ist für Security-Alerts. Uptime-Down ist eine andere Klasse.
4. **"Webhook-URL kopieren"** — die URL sieht aus wie
   `https://discord.com/api/webhooks/1234.../abcd...`

Falls der Channel noch nicht existiert: er kann via Discord-Bot-MCP angelegt werden:
- Name: `🩺-uptime-alerts`
- Kategorie: `📦 System & Projekte` (ID `1441655479867805727`)
- Topic: `Service-Watchdogs (shadowops-bot, zerodox, guildscout, mayday-sim, ai-agent-framework) — Down + Recovery Alerts`

### 2. Config-Datei anlegen

```bash
# Template kopieren (nicht editieren — die echte Config gehört NICHT ins Repo)
cp ~/shadowops-bot/deploy/shadowops-watchdog.env.example \
   ~/.config/shadowops-watchdog.env

# Webhook-URL eintragen
nano ~/.config/shadowops-watchdog.env
# → SHADOWOPS_WATCHDOG_WEBHOOK=https://discord.com/api/webhooks/...

# Rechte: nur du darfst lesen (enthält Token!)
chmod 600 ~/.config/shadowops-watchdog.env
```

### 3. systemd-Reload (manuell — Referenz/Fallback)

> **Hinweis:** Der kanonische Weg ist `sync-watchdog-units.sh` (siehe Abschnitt
> "Deklarative Aktivierung" oben). Die folgende manuelle Liste bleibt als
> Referenz/Fallback erhalten — sie muss bei jeder neuen Unit von Hand gepflegt
> werden, das Skript erledigt das automatisch.

```bash
systemctl --user daemon-reload
systemctl --user restart shadowops-watchdog.timer
systemctl --user restart zerodox-watchdog.timer
systemctl --user restart guildscout-watchdog.timer
systemctl --user restart mayday-sim-watchdog.timer
systemctl --user restart ai-agent-framework-watchdog.timer
systemctl --user restart zerodox-akquise-ai-watchdog.timer
systemctl --user restart cmdshadow-design-watchdog.timer
# Seit #416: Build-Drift-Detection fuer mayday-sim
systemctl --user restart mayday-sim-build-drift-watchdog.timer
# Seit #273: CI-Runner-Health mit jq-Filter (mayday-sim#437)
systemctl --user restart mayday-ci-runner-watchdog.timer
# Seit 2026-05-20: Drift-Detection für shadowops-bot Service-State (Vorfall HTTP-healthy + System-Service Restart-Loop)
systemctl --user restart shadowops-drift-watchdog.timer
```

### 4. Funktionstest

```bash
# Manueller Watchdog-Run (sollte "OK — Bot healthy" loggen)
systemctl --user start shadowops-watchdog.service
journalctl --user -u shadowops-watchdog.service --no-pager -n 5

# Test-Alert auslösen (mit absichtlich falschem Endpoint)
SHADOWOPS_HEALTH_URL="http://127.0.0.1:9999/health" \
  ~/shadowops-bot/scripts/bot-watchdog.sh

# State zurücksetzen
echo '{"last_status":"up","last_alert_at":"","consecutive_failures":0}' \
  > ~/shadowops-bot/data/watchdog_state.json
```

## Was wird wann alertiert?

### Watchdog-Familie (gestaffelt — meist alle 5 Min, Daily-Jobs alle 1h)

| Service | Mode | Endpoint/Units | Cycle | Boot-Offset |
|---|---|---|---|---|
| `shadowops-bot` | http | http://127.0.0.1:8766/health (bot_ready=true Pflicht) | 5 min | 2 min |
| `shadowops-drift` | systemd-state + drift | shadowops-bot Service-State + NRestarts-Loop + User-Unit-Drift (Vorfall 2026-05-20: HTTP-Watchdog meldete healthy während System-Service 96 min Restart-Loop hatte) | 5 min | 7 min |
| `zerodox` | http | https://zerodox.de/api/health (testet via Internet DNS+Traefik+TLS+App) | 5 min | 3 min |
| `zerodox-akquise-ai` | http | http://172.19.0.1:9300/health (Bridge-Gateway, kein bot_ready, Vorfall 2026-05-24 OOM-Kill) | 5 min | 7 min |
| `guildscout` | http | http://localhost:8765/health | 5 min | 4 min |
| `mayday-sim` | http | https://maydaysim.de/api/health | 5 min | 5 min |
| `mayday-ci-runner` | http + jq-filter | http://10.8.0.10:9100/health, filter `.components.ci_runner.ok` (#mayday-sim#425) | 5 min | 7 min |
| `mayday-sim-build-drift` | build-drift | https://maydaysim.de/api/build-id vs. origin/main HEAD (max. 30 min Drift, #mayday-sim#416) | 15 min | 2 min |
| `mayday-scheduler` | container | leitstelle-scheduler (Docker-Health, Game-Tick-Owner SB3 #mayday-sim#498) | 5 min | 7 min |
| `ai-agent-framework` | systemd | guildscout-feedback-agent, zerodox-support-agent, seo-agent (nur Prozess-State) | 5 min | 6 min |
| `seo-audit-freshness` | pg-freshness | seo_agent-DB: letzter erfolgreicher zerodox-Audit (`completed_at`) < 49h (Vorfall 2026-06-27: 7 Tage Audit-Crash trotz Service `active` — `systemd`-Mode blind, dieser prüft die Wirkung) | 1 h | 8 min |
| `seo-output-freshness` | pg-freshness | seo_agent-DB: bei frischen Insights (Agent produziert, jüngstes < 3 Tage) Alter der jüngsten echten Ausgabe (letzte Issue via `seo_topic_locks` bzw. Fix-PR via `seo_audits.pr_url`) < 168h (7 Tage) — erkennt Ausgabe-Stau trotz laufendem Audit (Vorfall 19.06.–02.07., #1683). `actioned`-Status bewusst NICHT als Signal (wird kaum je gesetzt) | 1 h | 9 min |
| `cmdshadow-design` | systemd-result | cmdshadow-design-healthcheck.service (max_age=36h) | 1 h | 8 min |
| `disk-hygiene` | disk + auto-prune | Auto-Prune (docker builder/image + journald) bei Disk >85%, Alarm >90% (Selbstpflege seit 2026-05-30) | 1 h | — |
| `doku-drift` | doku-drift | Container-Ports vs. Port-Map + MEMORY.md-Limit (<200), nur Alarm (Selbstpflege seit 2026-05-30) | täglich 06:30 | — |
| `ki-cost` | ki-cost | Token/Kosten-Rollup Claude+Codex aus JSONL + Anomalie-Alarm (Selbstpflege seit 2026-05-30) | täglich 07:15 | — |

Pro Service:
- **🔴 \<service\> DOWN** — nach 2 konsekutiven Failures (= ~10 Minuten Downtime).
- **✅ \<service\> wieder UP** — sobald der Service nach einem Down-Alert wieder antwortet.
- **Keine Wiederholungs-Alerts** — Stunden-langes Down führt zu EINEM Alert.
- **State pro Service getrennt** — wenn shadowops-bot down ist, beeinflusst das nicht den ZERODOX-Counter.

Die ZERODOX-URL `https://zerodox.de/api/health` läuft über das Internet → testet
DNS-Auflösung + Traefik-Routing + TLS-Zertifikat + App-Health in einem.

### Backup-Restore-Test (1. jedes Monats, 04:50 lokal)

- **🔴 Backup FAILED** — wenn `~/ZERODOX/scripts/backup-test.sh` exit-code != 0
  liefert (mindestens 1 der 10 Test-Stufen ist FAIL). Embed enthält die letzten
  40 Log-Zeilen + Pfad zum vollen Log.
- **Keine Success-Alerts** — monatlich grün ist langweilig. Falls gewünscht:
  `BACKUP_TEST_NOTIFY_ON_SUCCESS=1` in `~/.config/shadowops-watchdog.env`.

## Wartung / Inspektion

```bash
# Alle Watchdog-Timer auf einen Blick
systemctl --user list-timers \
  shadowops-watchdog.timer shadowops-drift-watchdog.timer \
  zerodox-watchdog.timer zerodox-akquise-ai-watchdog.timer \
  guildscout-watchdog.timer \
  mayday-sim-watchdog.timer mayday-ci-runner-watchdog.timer mayday-sim-build-drift-watchdog.timer \
  ai-agent-framework-watchdog.timer \
  cmdshadow-design-watchdog.timer \
  shadowops-backup-test.timer

# Letzten 50 Läufe pro Service
journalctl --user -u shadowops-watchdog.service --no-pager -n 50
journalctl --user -u zerodox-watchdog.service --no-pager -n 50
journalctl --user -u guildscout-watchdog.service --no-pager -n 50
journalctl --user -u mayday-sim-watchdog.service --no-pager -n 50
journalctl --user -u ai-agent-framework-watchdog.service --no-pager -n 50
journalctl --user -u zerodox-akquise-ai-watchdog.service --no-pager -n 50

# State-Files pro Service inspizieren
cat ~/shadowops-bot/data/watchdog_state.json
cat ~/shadowops-bot/data/watchdog_state_zerodox.json
cat ~/shadowops-bot/data/watchdog_state_guildscout.json
cat ~/shadowops-bot/data/watchdog_state_mayday-sim.json
cat ~/shadowops-bot/data/watchdog_state_ai-agent-framework.json

# Backup-Test-Logs (lokal)
ls -la ~/.local/state/shadowops-bot/backup-test/

# Manueller Sofort-Run (Backup-Test — Achtung: dauert ~5-20 Min)
systemctl --user start shadowops-backup-test.service
journalctl --user -u shadowops-backup-test.service -f
```

## Pause/Disable

```bash
# Watchdog kurz aussetzen (z.B. während geplantem Wartungs-Restart)
systemctl --user stop shadowops-watchdog.timer
# … nach der Wartung wieder an:
systemctl --user start shadowops-watchdog.timer

# Permanent disable (nicht empfohlen)
systemctl --user disable --now shadowops-watchdog.timer
```
