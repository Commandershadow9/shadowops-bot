# Security-Agent-Team W1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Das P1-Fundament des Security-Agent-Teams lauffähig machen (Orchestrator-Trigger-Loop, korrekte Configs), plus Selbstüberwachung (Freshness-Watchdog, Job-Reaper) und Start des 7d-Soaks npm-audit-Worker vs. Monolith.

**Architecture:** Redis-Pub/Sub-Job-Bus (`sec:*` auf guildscout-redis:6379 MIT Auth), eigenständige systemd-User-Prozesse (Orchestrator + npm-audit-Worker), Findings/Jobs in der `security_analyst`-DB (guildscout-postgres:5433). Spec: `docs/design/2026-07-09-security-agent-team-v2-spec.md`.

**Tech Stack:** Python 3 (asyncio, redis.asyncio, asyncpg, pydantic v2), pytest (mock-only!), systemd --user, bash, cron.

**Arbeitsverzeichnis:** git worktree `/home/cmdshadow/shadowops-bot/.worktrees/w1-security-team` auf Branch `feat/security-team-w1` (Basis `origin/main`). Der Haupt-Tree ist der LIVE-Tree des Bots — NIE dessen Branch wechseln!

**Tests ausführen:** `cd <worktree> && /home/cmdshadow/shadowops-bot/.venv/bin/python -m pytest <datei> -v` (venv NIEMALS aktivieren; im Worktree ausführen, damit der Worktree-Code getestet wird). Keine echten gh/npm/redis/psql-Calls in Tests — alles mocken (Lehre #1069).

**Status-Werte `sec_jobs`:** `queued → in_progress → ok|partial|failed|cancelled` (contracts.py `JobStatus`).

---

### Task 1: `trigger`-Durchreichung im Orchestrator

Der Orchestrator faechert Jobs auf, verliert aber den Trigger-Typ (immer `manual`). Für den Soak wollen wir `daily` vs. `manual` in `sec_jobs.trigger` unterscheiden können.

**Files:**
- Modify: `src/integrations/security_engine/team/orchestrator.py` (Methode `handle_trigger`)
- Test: `tests/unit/test_security_orchestrator.py` (existiert, ergänzen)

- [ ] **Step 1: Failing Test schreiben** — in `tests/unit/test_security_orchestrator.py` ergänzen. Die Datei hat bereits die Fixtures `_redis()`/`_db()` und importiert `SecurityOrchestrator` — den neuen Test einfach ans Ende:

```python
@pytest.mark.asyncio
async def test_handle_trigger_reicht_trigger_typ_durch():
    """handle_trigger(trigger='daily') muss den Trigger-Typ in die Jobs schreiben."""
    orch = SecurityOrchestrator(redis=_redis(), db=_db())

    jobs = await orch.handle_trigger(
        projects={"zerodox": {"npm_audit_path": "/tmp/x"}},
        active_workers=["npm_audit"],
        trigger="daily",
    )

    assert len(jobs) == 1
    assert jobs[0].trigger == "daily"
```

- [ ] **Step 2: Test laufen lassen — muss FAILen** (`TypeError: unexpected keyword argument 'trigger'`):

```bash
cd /home/cmdshadow/shadowops-bot/.worktrees/w1-security-team
/home/cmdshadow/shadowops-bot/.venv/bin/python -m pytest tests/unit/test_security_orchestrator.py -v
```

- [ ] **Step 3: Implementieren** — `handle_trigger` in `orchestrator.py`:

```python
    async def handle_trigger(
        self, projects: dict[str, dict], active_workers: list[str],
        trigger: str = "manual",
    ) -> list[SecurityJob]:
        """Fan-out: je aktivem Worker-Typ × Projekt (mit passendem <type>_path) ein Job."""
        jobs: list[SecurityJob] = []
        for worker_type in active_workers:
            path_key = f"{worker_type}_path"
            for project, cfg in projects.items():
                if path_key not in cfg:
                    continue
                jobs.append(await self.dispatch_job(
                    worker_type=worker_type, project=project,
                    payload={"path": cfg[path_key]}, trigger=trigger,
                ))
        return jobs
```

- [ ] **Step 4: Tests laufen lassen — alle in der Datei müssen PASSen**
- [ ] **Step 5: Commit** — `fix(secteam): handle_trigger reicht Trigger-Typ in Jobs durch (W1, #290)`

---

### Task 2: Orchestrator-Hauptschleife (sec:trigger-Subscribe-Loop)

`orchestrator_main.py` ist ein Exit-Stub — ohne Loop kein dauerhafter Betrieb. Muster: `team/runner.py::_amain`.

**Files:**
- Rewrite: `src/integrations/security_engine/team/orchestrator_main.py`
- Test: `tests/unit/test_security_orchestrator_main.py` (neu)

- [ ] **Step 1: Failing Tests schreiben** — neue Datei `tests/unit/test_security_orchestrator_main.py`:

```python
"""Tests für den Orchestrator-Entrypoint (sec:trigger-Loop, W1)."""
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.integrations.security_engine.team import orchestrator_main


@pytest.mark.asyncio
async def test_handle_trigger_message_daily():
    """Gültige Payload → handle_trigger mit trigger='daily'."""
    orch = MagicMock()
    orch.handle_trigger = AsyncMock(return_value=[MagicMock(), MagicMock()])
    config = MagicMock()
    config.security_team_projects = {"zerodox": {"npm_audit_path": "/tmp/x"}}
    config.security_team_active_workers = ["npm_audit"]

    jobs = await orchestrator_main.handle_trigger_message(
        orch, config, json.dumps({"trigger": "daily"})
    )

    assert len(jobs) == 2
    orch.handle_trigger.assert_awaited_once_with(
        projects=config.security_team_projects,
        active_workers=["npm_audit"],
        trigger="daily",
    )


@pytest.mark.asyncio
async def test_handle_trigger_message_kaputte_payload_faellt_auf_manual():
    """Ungültiges JSON darf nicht crashen — Fallback trigger='manual'."""
    orch = MagicMock()
    orch.handle_trigger = AsyncMock(return_value=[])
    config = MagicMock()
    config.security_team_projects = {}
    config.security_team_active_workers = []

    await orchestrator_main.handle_trigger_message(orch, config, "{kaputt")

    orch.handle_trigger.assert_awaited_once_with(
        projects={}, active_workers=[], trigger="manual",
    )
```

- [ ] **Step 2: Tests laufen lassen — FAIL** (`AttributeError: module ... has no attribute 'handle_trigger_message'`)

- [ ] **Step 3: `orchestrator_main.py` neu schreiben:**

```python
"""systemd-Entrypoint: Security-Orchestrator mit sec:trigger-Subscribe-Loop (W1).

Lauscht auf sec:trigger (Redis Pub/Sub) und faechert pro Trigger Jobs an die
aktiven Worker auf (SecurityOrchestrator.handle_trigger). Result-Aggregation
+ Token-Cap-Enforcement folgen in W2. Muster: team/runner.py::_amain.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal

import redis.asyncio as aioredis

from utils.config import Config

from ..db import SecurityDB
from .orchestrator import SecurityOrchestrator

logger = logging.getLogger("security.orchestrator.main")

TRIGGER_CHANNEL = "sec:trigger"


async def handle_trigger_message(orchestrator, config, raw) -> list:
    """Verarbeitet EINE sec:trigger-Message. Ungueltige Payload → trigger='manual'."""
    trigger = "manual"
    try:
        payload = json.loads(raw) if raw else {}
        if isinstance(payload, dict):
            trigger = str(payload.get("trigger", "manual"))
    except (json.JSONDecodeError, TypeError):
        logger.warning("Ungueltige sec:trigger-Payload: %r", raw)
    jobs = await orchestrator.handle_trigger(
        projects=config.security_team_projects,
        active_workers=config.security_team_active_workers,
        trigger=trigger,
    )
    logger.info("sec:trigger (%s) → %d Jobs dispatched", trigger, len(jobs))
    return jobs


async def _amain() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    config = Config()
    if not config.security_team_enabled:
        logger.info("security_team disabled — orchestrator exit")
        return

    dsn = os.environ.get("SECURITY_ANALYST_DB_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("SECURITY_ANALYST_DB_URL oder DATABASE_URL muss gesetzt sein")
    redis_url = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")

    db = SecurityDB(dsn)
    await db.initialize()
    rconn = aioredis.from_url(redis_url, decode_responses=True)
    orchestrator = SecurityOrchestrator(redis=rconn, db=db)

    pubsub = rconn.pubsub()
    await pubsub.subscribe(TRIGGER_CHANNEL)
    logger.info("Orchestrator bereit — subscribed=%s", TRIGGER_CHANNEL)

    stop = asyncio.Event()

    def _graceful(*_):
        logger.info("SIGTERM — fahre Orchestrator herunter")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _graceful)

    try:
        while not stop.is_set():
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message is None or message.get("type") != "message":
                continue
            try:
                await handle_trigger_message(orchestrator, config, message.get("data"))
            except Exception:
                # Ein kaputter Trigger darf den Loop nie beenden.
                logger.exception("Trigger-Verarbeitung fehlgeschlagen")
    finally:
        await pubsub.unsubscribe(TRIGGER_CHANNEL)
        await rconn.aclose()
        await db.close()


if __name__ == "__main__":
    asyncio.run(_amain())
```

Hinweis: `Config()` lädt `config/config.yaml` relativ — funktioniert, weil die systemd-Unit `WorkingDirectory=%h/shadowops-bot` setzt. Flag aus → Exit 0 → `Restart=on-failure` startet NICHT neu (gewollt).

- [ ] **Step 4: Tests laufen lassen — PASS** (beide neuen + bestehende `test_security_runner.py` als Regression)
- [ ] **Step 5: Commit** — `feat(secteam): Orchestrator sec:trigger-Subscribe-Loop (W1, #290)`

---

### Task 3: Config-Fixes (npm_audit_path + Redis-Auth-Platzhalter)

**Files:**
- Modify: `config/config.example.yaml` (security_team-Sektion)
- Modify: `deploy/shadowops-security-team.env.example`

- [ ] **Step 1: `config.example.yaml`** — zerodox-Pfad korrigieren (package.json liegt in `web/`):

```yaml
# Security-Agent-Team (W1) — default OFF. Env-Override: SECURITY_TEAM_ENABLED
security_team:
  enabled: false
  active_workers: ["npm_audit"]
  projects:
    guildscout:
      npm_audit_path: "/home/cmdshadow/GuildScout/web"
    zerodox:
      npm_audit_path: "/home/cmdshadow/ZERODOX/web"
```

- [ ] **Step 2: `deploy/shadowops-security-team.env.example`** — Auth-Platzhalter (guildscout-redis hat `requirepass`!):

```bash
# Security-Agent-Team (#290 W1) — wird von den systemd-Units via EnvironmentFile geladen.
# Kopieren nach ~/.config/shadowops-security-team.env und chmod 600.
# Enthaelt KEINE echten Secrets im Repo — nur Platzhalter.
# DSN: identisch zu security_analyst.database_dsn in config/config.yaml.
SECURITY_ANALYST_DB_URL=postgresql://security_analyst:PASSWORD@127.0.0.1:5433/security_analyst
# ACHTUNG: guildscout-redis laeuft MIT requirepass — Passwort-Form ist Pflicht:
REDIS_URL=redis://:REDIS_PASSWORD@127.0.0.1:6379/0
SECURITY_TEAM_ENABLED=false
```

- [ ] **Step 3: Commit** — `fix(secteam): zerodox npm_audit_path → ZERODOX/web + Redis-Auth-Platzhalter (W1, #290)`

---

### Task 4: Trigger-Script (Cron → Redis publish)

Externer Cron-Trigger statt Self-Retrigger (crash-safe, Lehre aus SEO `trigger-audit.sh`). Exit 4 bei 0 Subscribern = totes Team.

**Files:**
- Create: `scripts/security-trigger.sh` (chmod +x)

- [ ] **Step 1: Script anlegen:**

```bash
#!/usr/bin/env bash
# security-trigger.sh — publisht sec:trigger auf dem Job-Bus (guildscout-redis).
# Externer Cron-Trigger statt Self-Retrigger im Prozess (crash-safe — Muster
# aus ~/agents/projects/seo/scripts/trigger-audit.sh).
# Exit 4 = 0 Subscriber → Orchestrator laeuft nicht (Alarm-Signal fuer Cron/Watchdog).
# Aufruf: security-trigger.sh [daily|manual|<beliebig>]
set -euo pipefail

# Gruppen-Session-Drift: falls docker-Gruppe nicht aktiv, re-exec via sg
if ! docker ps >/dev/null 2>&1; then
    exec sg docker -c "$0 ${*:-}"
fi

ENV_FILE="$HOME/.config/shadowops-security-team.env"
if [ -f "$ENV_FILE" ]; then
    set -a; . "$ENV_FILE"; set +a
fi

TRIGGER="${1:-daily}"
# Passwort aus REDIS_URL extrahieren (Form redis://:PASS@host:port/db)
PASS=$(printf '%s' "${REDIS_URL:-}" | sed -n 's|redis://:\([^@]*\)@.*|\1|p')

SUBS=$(docker exec guildscout-redis redis-cli ${PASS:+-a "$PASS"} --no-auth-warning \
    publish sec:trigger "{\"trigger\":\"${TRIGGER}\"}")

if [ "${SUBS:-0}" -eq 0 ]; then
    echo "FEHLER: 0 Subscriber auf sec:trigger — Security-Orchestrator laeuft nicht?" >&2
    exit 4
fi
echo "sec:trigger (${TRIGGER}) an ${SUBS} Subscriber publiziert"
```

- [ ] **Step 2: Syntax-Check:** `bash -n scripts/security-trigger.sh` → kein Output
- [ ] **Step 3: `chmod +x scripts/security-trigger.sh`**
- [ ] **Step 4: Commit** — `feat(secteam): security-trigger.sh — crash-safer Cron-Trigger (W1, #290)`

---

### Task 5: Job-Reaper (Zombie-in_progress-Jobs)

**Files:**
- Create: `scripts/security-job-reaper.sh` (chmod +x) — adaptiert von `~/agents/projects/seo/scripts/job-reaper.sh`

- [ ] **Step 1: Script anlegen:**

```bash
#!/usr/bin/env bash
# security-job-reaper.sh — Bricht Zombie-Jobs (status='in_progress') in sec_jobs ab.
# Worker, die hart sterben (OOM/SIGKILL), hinterlassen sonst ewige in_progress-Rows.
# Muster: ~/agents/projects/seo/scripts/job-reaper.sh. Cron: taeglich (siehe W1-Ops).
set -euo pipefail

if ! docker ps >/dev/null 2>&1; then
    exec sg docker -c "$0 ${*:-}"
fi

DB_CONTAINER="${DB_CONTAINER:-guildscout-postgres}"
DB_USER="${DB_USER:-security_analyst}"
DB_NAME="${DB_NAME:-security_analyst}"
STALE_HOURS="${STALE_HOURS:-6}"
LOG_FILE="/home/cmdshadow/shadowops-bot/logs/security-job-reaper.log"
mkdir -p "$(dirname "$LOG_FILE")"

log() { echo "[$(date -Iseconds)] $*" | tee -a "$LOG_FILE" >&2; }

reaped=$(docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -tAc \
  "UPDATE sec_jobs
   SET status='cancelled', completed_at=NOW(),
       error_message='job-reaper: in_progress > ${STALE_HOURS}h (Zombie)'
   WHERE status='in_progress'
     AND started_at < NOW() - make_interval(hours => ${STALE_HOURS})
   RETURNING 1" | wc -l)

log "Reaped: ${reaped} Zombie-Jobs (threshold=${STALE_HOURS}h)"
```

- [ ] **Step 2: Syntax-Check + chmod +x**
- [ ] **Step 3: Commit** — `feat(secteam): security-job-reaper.sh gegen Zombie-sec_jobs (W1, #290)`

---

### Task 6: security-freshness-watchdog (Arbeit-statt-Prozess-Liveness)

Klon des `seo-audit-freshness-watchdog`-Musters — der generische `scripts/service-watchdog.sh` beherrscht `WATCHDOG_MODE=pg-freshness` bereits, es braucht NUR Service+Timer.

**Files:**
- Create: `deploy/security-freshness-watchdog.service`
- Create: `deploy/security-freshness-watchdog.timer`

- [ ] **Step 1: Service-Unit:**

```ini
[Unit]
Description=Security-Team Freshness Watchdog — letzter erfolgreicher sec_jobs-Lauf < 26h (DB completed_at)
Documentation=https://github.com/Commandershadow9/shadowops-bot
After=network-online.target

[Service]
Type=oneshot
EnvironmentFile=-/home/cmdshadow/.config/shadowops-watchdog.env
Environment=WATCHDOG_SERVICE_NAME=security-freshness
Environment=WATCHDOG_MODE=pg-freshness
Environment=WATCHDOG_PG_CONTAINER=guildscout-postgres
Environment=WATCHDOG_PG_USER=security_analyst
Environment=WATCHDOG_PG_DB=security_analyst
Environment=WATCHDOG_MAX_AGE_HOURS=26
Environment="WATCHDOG_PG_QUERY=SELECT COALESCE(EXTRACT(EPOCH FROM (NOW()-MAX(completed_at)))/3600, 999999) FROM sec_jobs WHERE status IN ('ok','partial')"
ExecStart=/home/cmdshadow/shadowops-bot/scripts/service-watchdog.sh
SuccessExitStatus=0 1
StandardOutput=journal
StandardError=journal
```

(`partial` zählt bewusst als „Team lebt" — dauerhaft-PARTIAL-Erkennung ist W2-Result-Aggregation. `26h` = Daily-Trigger + Puffer.)

- [ ] **Step 2: Timer-Unit:**

```ini
[Unit]
Description=Security-Team Freshness Watchdog Timer — stündlich (mit Jitter)
Documentation=https://github.com/Commandershadow9/shadowops-bot

[Timer]
# Erster Lauf 11 Minuten nach Boot (versetzt zu den anderen Watchdogs).
OnBootSec=11min
OnUnitActiveSec=1h
RandomizedDelaySec=5min
AccuracySec=1min
Persistent=false

[Install]
WantedBy=timers.target
```

- [ ] **Step 3: Commit** — `feat(secteam): security-freshness-watchdog (pg-freshness auf sec_jobs) (W1, #290)`

---

### Task 7: Soak-Vergleichs-Script (Worker vs. Monolith)

Grundlage des 7d-Soak-Gates: Worker-Findings (`session_id IS NULL`) vs. Monolith-Findings (`session_id IS NOT NULL`) der Kategorie npm_audit, verglichen über `finding_fingerprint`.

**Files:**
- Create: `scripts/security-soak-compare.sh` (chmod +x)

- [ ] **Step 1: Script anlegen:**

```bash
#!/usr/bin/env bash
# security-soak-compare.sh — 7d-Soak W1: vergleicht npm-audit-Findings des
# Team-Workers (session_id IS NULL) mit denen des Monolithen (session_id IS NOT NULL)
# der letzten 24h anhand finding_fingerprint. Output → Log (Cron, taeglich).
set -euo pipefail

if ! docker ps >/dev/null 2>&1; then
    exec sg docker -c "$0 ${*:-}"
fi

DB_CONTAINER="${DB_CONTAINER:-guildscout-postgres}"
DB_USER="${DB_USER:-security_analyst}"
DB_NAME="${DB_NAME:-security_analyst}"
LOG_FILE="/home/cmdshadow/shadowops-bot/logs/security-soak-w1.log"
mkdir -p "$(dirname "$LOG_FILE")"

psql_q() {
    docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -tAc "$1"
}

worker=$(psql_q "SELECT COUNT(DISTINCT finding_fingerprint) FROM findings
    WHERE category LIKE '%npm%' AND session_id IS NULL
      AND created_at > NOW() - INTERVAL '24 hours'")
monolith=$(psql_q "SELECT COUNT(DISTINCT finding_fingerprint) FROM findings
    WHERE category LIKE '%npm%' AND session_id IS NOT NULL
      AND created_at > NOW() - INTERVAL '24 hours'")
nur_worker=$(psql_q "SELECT COUNT(*) FROM (
    SELECT DISTINCT finding_fingerprint FROM findings
    WHERE category LIKE '%npm%' AND session_id IS NULL
      AND created_at > NOW() - INTERVAL '24 hours'
    EXCEPT
    SELECT DISTINCT finding_fingerprint FROM findings
    WHERE category LIKE '%npm%' AND session_id IS NOT NULL
      AND created_at > NOW() - INTERVAL '24 hours') d")
nur_monolith=$(psql_q "SELECT COUNT(*) FROM (
    SELECT DISTINCT finding_fingerprint FROM findings
    WHERE category LIKE '%npm%' AND session_id IS NOT NULL
      AND created_at > NOW() - INTERVAL '24 hours'
    EXCEPT
    SELECT DISTINCT finding_fingerprint FROM findings
    WHERE category LIKE '%npm%' AND session_id IS NULL
      AND created_at > NOW() - INTERVAL '24 hours') d")

echo "[$(date -Iseconds)] worker=${worker} monolith=${monolith} nur_worker=${nur_worker} nur_monolith=${nur_monolith}" \
    | tee -a "$LOG_FILE"
```

- [ ] **Step 2: Syntax-Check + chmod +x**
- [ ] **Step 3: Commit** — `feat(secteam): Soak-Vergleichs-Script Worker vs. Monolith (W1, #290)`

---

### Task 8: Regression + PR

- [ ] **Step 1: Alle Team-Tests EINZELN laufen lassen** (jede Datei separat, OOM-Regel):

```bash
cd /home/cmdshadow/shadowops-bot/.worktrees/w1-security-team
for f in tests/unit/test_security_contracts.py tests/unit/test_security_orchestrator.py \
         tests/unit/test_security_orchestrator_main.py tests/unit/test_security_runner.py \
         tests/unit/test_base_security_worker.py tests/unit/test_npm_audit_worker.py \
         tests/unit/test_config_security_team.py; do
  /home/cmdshadow/shadowops-bot/.venv/bin/python -m pytest "$f" -q || break
done
```

Expected: alle PASS.

- [ ] **Step 2: PR erstellen** — Branch pushen, PR gegen main:
  - Titel: `feat(secteam): W1 — Orchestrator-Loop, Trigger/Reaper/Soak-Scripts, Freshness-Watchdog (#290)`
  - Body (deutsch, echte Umlaute): Was/Warum je Task, Test-Nachweis, Verweis auf Spec + diesen Plan, Ops-Checkliste (= Task 9) explizit als „nach Merge" markiert. KEIN `Closes #290`.

---

### Task 9: Ops-Aktivierung (NACH PR-Review + Merge, auf dem Host — macht der Lead)

- [ ] **Step 1: env-Datei anlegen** (Secrets aus Live-`config/config.yaml` security_analyst.database_dsn + Redis-Passwort aus `~/agents/projects/seo/.env` REDIS_URL):

```bash
install -m 600 /dev/null ~/.config/shadowops-security-team.env
# Inhalt (Werte aus den genannten Quellen einsetzen):
# SECURITY_ANALYST_DB_URL=postgresql://security_analyst:<PASS>@127.0.0.1:5433/security_analyst
# REDIS_URL=redis://:<REDIS_PASS>@127.0.0.1:6379/0
# SECURITY_TEAM_ENABLED=true
```

- [ ] **Step 2: Live-`config/config.yaml`** — `security_team`-Sektion einfügen (wie example, aber `enabled: true` + korrekte Pfade). Vorher `cp config/config.yaml config/config.yaml.bak-w1 && chmod 600 config/config.yaml.bak-w1`.
- [ ] **Step 3: Haupt-Tree auf main aktualisieren:** `git -C /home/cmdshadow/shadowops-bot pull --ff-only` (der Bot-Prozess selbst braucht für W1 KEINEN Restart — Orchestrator/Worker sind eigene Prozesse).
- [ ] **Step 4: Units installieren:**

```bash
mkdir -p ~/.config/systemd/user
cp ~/shadowops-bot/deploy/security-orchestrator.service ~/.config/systemd/user/
cp ~/shadowops-bot/deploy/security-npm-audit-worker.service ~/.config/systemd/user/
cp ~/shadowops-bot/deploy/security-freshness-watchdog.service ~/.config/systemd/user/
cp ~/shadowops-bot/deploy/security-freshness-watchdog.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now security-orchestrator security-npm-audit-worker
```

> **Hinweis:** Der `security-freshness-watchdog.timer` wird hier bewusst NICHT mit-enabled — `sec_jobs` ist zu diesem Zeitpunkt noch leer, der Watchdog würde sofort einen Kaltstart-Alarm werfen (Alter „seit nie" → 999999h). Erst nach dem ersten erfolgreichen manuellen Trigger-Lauf aktivieren (siehe Step 7).

- [ ] **Step 5: Smoke:** `systemctl --user status security-orchestrator security-npm-audit-worker` → beide `active (running)`, Journal zeigt `Orchestrator bereit — subscribed=sec:trigger` bzw. `npm_audit bereit`.
- [ ] **Step 6: Manueller Trigger-Lauf:** `~/shadowops-bot/scripts/security-trigger.sh manual` → Ausgabe `an 1 Subscriber publiziert`; danach in der DB prüfen:

```bash
docker exec guildscout-postgres psql -U security_analyst -d security_analyst \
  -c "SELECT worker_type, project, trigger, status, completed_at FROM sec_jobs ORDER BY created_at DESC LIMIT 5"
```

Expected: 2 Rows (guildscout+zerodox), status `ok`/`partial`, completed_at gesetzt.

- [ ] **Step 7: Watchdog-Timer erst jetzt aktivieren:** `systemctl --user enable --now security-freshness-watchdog.timer` (erst jetzt — Kaltstart-Alarm vermeiden, siehe Hinweis bei Step 4; `sec_jobs` hat durch Step 6 bereits frische Rows).
- [ ] **Step 8: Watchdog-Staleness-Test:** einmalig mit `WATCHDOG_MAX_AGE_HOURS=0` laufen lassen (systemd-run oder env-Override) → Discord-Alarm in `#🩺-uptime-alerts` kommt; danach normaler Lauf → kein Alarm.
- [ ] **Step 9: Crons eintragen** (Off-Minuten, bewusst NICHT :00/:30):

```
23 5 * * * /home/cmdshadow/shadowops-bot/scripts/security-trigger.sh daily # Security-Team Daily-Trigger (W1, #290)
41 6 * * * /home/cmdshadow/shadowops-bot/scripts/security-job-reaper.sh   # sec_jobs Zombie-Reaper (W1, #290)
31 7 * * * /home/cmdshadow/shadowops-bot/scripts/security-soak-compare.sh # 7d-Soak Worker vs. Monolith (W1, #290)
```

- [ ] **Step 10: Issue-#290-Kommentar** — W1 aktiv, Soak-Start-Datum + geplantes Soak-Ende (Start + 7 Tage), Verweis auf `logs/security-soak-w1.log`.
- [ ] **Step 11: CLAUDE.md (shadowops-bot)** — Security-Team-Eintrag aktualisieren: W1 aktiv, Soak läuft, Flag-Standort, neue Crons + Watchdog dokumentieren.

---

## Soak-Gate (Ende W1, nach 7 Tagen)

- `logs/security-soak-w1.log`: an ≥6 von 7 Tagen `nur_worker=0` und `nur_monolith=0` (bzw. erklärbare Abweichungen — Monolith-AI-Sessions finden ggf. andere Kategorien, nur npm-audit zählt)
- Kein Freshness-Alarm während des Soaks (außer dem bewussten Staleness-Test)
- `sec_jobs` enthält 7 tägliche `trigger='daily'`-Läufe mit status `ok`
- Dann: W2 starten (Spec Abschnitt 8)
