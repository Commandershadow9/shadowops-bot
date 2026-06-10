# Monitoring Plan 2 — ZERODOX-Check-Migration (Phase 2)

> **For agentic workers:** TDD, frequent commits. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Echte ZERODOX-Checks aus dem Inventar deklarativ in die ShadowOps-Engine bringen — parallel zu den Alt-Crons/-Watchdogs, mit Real-Trigger-Verifikation und vorsichtigem Cut-over (Spec §8). Konservativ: meist `alert-only`, autonomes Heal nur wo eindeutig sicher.

**Architecture:** Erweitert die Plan-1-Engine um zwei nötige Features (HTTP-Header für authentifizierte Endpoints, `container`-Check-Typ für Netz-Anbindung), dann deklarative `checks:` unter `projects.zerodox` in config.yaml. Die Engine läuft additiv zum bestehenden `zerodox`-`remediation_command` (Auto-Rollback) — kein Container-Heal auf zerodox-web (würde kollidieren).

**Tech Stack:** Wie Plan 1 (Python/asyncio/aiohttp/pytest). Spec: `docs/2026-06-09-zentrales-monitoring-auto-health-design.md`. Plan 1: `docs/plans/2026-06-09-monitoring-engine-grundlage-plan.md`. Inventar: `docs/MONITORING_INVENTORY.md`.

---

## Scope-Realität (ehrlich)

**Migrierbar in Plan 2** (Bot hat Endpoint-Zugang + ggf. Secret):
| Check | Typ | Auth | Heal | Alt-Quelle |
|---|---|---|---|---|
| `zerodox-health` | http | — (public) | **alert-only** (kein restart — kollidiert mit Auto-Rollback) | cron-health-check.sh, zerodox-watchdog |
| `akquise-liveness` | http | — (local) | alert-only (externe App) | akquise-ai-watchdog.sh, zerodox-akquise-ai-watchdog |
| `analytics-bridge` | **container** (neu) | — (docker) | **network-reconnect (reversible-auto)** ⭐ | ensure-analytics-network.sh |
| `zerodox-onboarding-smoke` | http + **header** (neu) | `ZERODOX_AGENT_API_KEY` ✓ | alert-only | synthetic-monitor.sh (sub) |

**NICHT in Plan 2** (Secret fehlt im Bot → Plan 3 / Operator bringt Secret):
- `agent-listener` (CRON_API_KEY fehlt), `ci-main-health` (GITHUB_PAT fehlt), `akquise-synthetic` (AKQUISE_AI_BEARER_TOKEN fehlt)
- `synthetic-frontend/csp/functional` (Chrome/Playwright + eigene Discord-Alerts → separater Schritt)

---

## File Structure

| Datei | Änderung |
|---|---|
| `src/integrations/check_definitions.py` | `CheckDefinition.headers` Feld |
| `src/integrations/check_runner.py` | `_run_http` mit Headers (+ `$ENV`-Auflösung); neuer `_run_container` (network-attached) |
| `config/config.yaml` | `projects.zerodox.monitor.checks:` (4 Einträge, lokal/gitignored) |
| `tests/unit/test_check_runner.py`, `test_check_definitions.py` | neue Tests |

---

## Task 1: HTTP-Header-Support (mit $ENV-Auflösung)

**Files:** Modify `check_definitions.py`, `check_runner.py`; Test `test_check_definitions.py`, `test_check_runner.py`

- [ ] **Step 1: Failing Tests**

```python
# test_check_definitions.py ergänzen
def test_check_definition_headers():
    cd = CheckDefinition.from_dict({"id": "x", "type": "http", "target": "/h", "interval": 60,
        "headers": {"X-Agent-Key": "$ZERODOX_AGENT_API_KEY"}})
    assert cd.headers == {"X-Agent-Key": "$ZERODOX_AGENT_API_KEY"}

def test_check_definition_headers_default_empty():
    cd = CheckDefinition.from_dict({"id": "x", "type": "http", "target": "/h", "interval": 60})
    assert cd.headers == {}
```

```python
# test_check_runner.py ergänzen — Header werden gesendet, $ENV aufgelöst
@pytest.mark.asyncio
async def test_http_resolves_env_header(monkeypatch):
    monkeypatch.setenv("ZERODOX_AGENT_API_KEY", "secret123")
    cd = CheckDefinition.from_dict({"id": "h", "type": "http", "target": "/h", "interval": 60,
        "expect": {"status": 200}, "headers": {"X-Agent-Key": "$ZERODOX_AGENT_API_KEY"}})
    captured = {}
    def _sess(resp):
        class _G:
            async def __aenter__(s): return resp
            async def __aexit__(s, *a): return False
        class _S:
            def get(s, url, headers=None): captured["headers"] = headers; return _G()
            async def __aenter__(s): return s
            async def __aexit__(s, *a): return False
        return _S()
    runner = CheckRunner(base_url_resolver=lambda p, t: t)
    with patch("aiohttp.ClientSession", return_value=_sess(_FakeResp(200))):
        await runner.run(cd, project_name="zerodox")
    assert captured["headers"] == {"X-Agent-Key": "secret123"}
```

- [ ] **Step 2: Test rot** — `pytest tests/unit/test_check_runner.py -k env_header --no-cov` → FAIL

- [ ] **Step 3: Implementierung**

`check_definitions.py` — `CheckDefinition` Feld + from_dict:
```python
    headers: dict[str, Any] = field(default_factory=dict)
    # in from_dict, vor return:
    #   headers=d.get("headers", {}),
```

`check_runner.py` — `_run_http` Header-Auflösung + Übergabe:
```python
import os
# in _run_http, vor session.get:
        headers = {k: (os.environ.get(v[1:], "") if isinstance(v, str) and v.startswith("$") else v)
                   for k, v in (check.headers or {}).items()}
# session.get(url, headers=headers or None)
```

- [ ] **Step 4: Test grün** — `pytest tests/unit/test_check_runner.py tests/unit/test_check_definitions.py --no-cov`

- [ ] **Step 5: Commit** — `feat(monitor): HTTP-Header-Support mit $ENV-Auflösung`

---

## Task 2: container-Check-Typ (network-attached)

**Files:** Modify `check_runner.py`; Test `test_check_runner.py`

- [ ] **Step 1: Failing Tests**

```python
@pytest.mark.asyncio
async def test_container_network_attached_ok():
    cd = CheckDefinition.from_dict({"id": "br", "type": "container", "target": "guildscout-postgres",
        "interval": 600, "expect": {"network": "project_sicherheitsdienst-network"}})
    runner = CheckRunner(base_url_resolver=lambda p, t: t)
    proc = Mock(); proc.returncode = 0
    proc.communicate = AsyncMock(return_value=(b'{"project_sicherheitsdienst-network":{},"other":{}}', b""))
    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        result = await runner.run(cd, project_name="zerodox")
    assert result.status is CheckStatus.OK

@pytest.mark.asyncio
async def test_container_network_detached_fails():
    cd = CheckDefinition.from_dict({"id": "br", "type": "container", "target": "guildscout-postgres",
        "interval": 600, "expect": {"network": "project_sicherheitsdienst-network"}})
    runner = CheckRunner(base_url_resolver=lambda p, t: t)
    proc = Mock(); proc.returncode = 0
    proc.communicate = AsyncMock(return_value=(b'{"other-network":{}}', b""))
    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        result = await runner.run(cd, project_name="zerodox")
    assert result.status is CheckStatus.FAIL
    assert "project_sicherheitsdienst-network" in result.message
```

- [ ] **Step 2: Test rot** — container-Typ gibt aktuell ERROR

- [ ] **Step 3: Implementierung** — in `run()` Dispatch + `_run_container`:
```python
        if check.type is CheckType.CONTAINER:
            return await self._run_container(check)
```
```python
    async def _run_container(self, check: CheckDefinition) -> CheckResult:
        import json
        want_net = check.expect.get("network")
        if not want_net:
            return CheckResult(check.id, CheckStatus.ERROR, message="container-Check braucht expect.network")
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "inspect", "-f", "{{json .NetworkSettings.Networks}}", check.target,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            out, err = await asyncio.wait_for(proc.communicate(), timeout=check.timeout)
        except Exception as e:
            return CheckResult(check.id, CheckStatus.ERROR, message=f"docker inspect Fehler: {e}")
        if proc.returncode != 0:
            return CheckResult(check.id, CheckStatus.FAIL,
                               message=f"Container {check.target} nicht inspizierbar: {err.decode(errors='replace')[:120]}")
        try:
            nets = json.loads(out.decode() or "{}")
        except Exception:
            nets = {}
        if want_net in (nets or {}):
            return CheckResult(check.id, CheckStatus.OK)
        return CheckResult(check.id, CheckStatus.FAIL,
                           message=f"{check.target} nicht im Netz '{want_net}' (hat: {list((nets or {}).keys())})")
```

- [ ] **Step 4: Test grün** + volle Suite (`pytest tests/unit/ --no-cov`)

- [ ] **Step 5: Commit** — `feat(monitor): container-Check-Typ (network-attached)`

---

## Task 3: ZERODOX-Checks deklarativ aktivieren (parallel, lokal)

**Files:** Modify `config/config.yaml` (gitignored, lokal)

- [ ] **Step 1: checks-Block unter projects.zerodox.monitor (nach check_interval)**

```yaml
      checks:
        - id: zerodox-health
          type: http
          target: https://zerodox.de/api/health
          interval: 120
          expect: { status: 200 }
          flake_polls: 2
          heal: { action: alert-only }   # kein restart — Auto-Rollback via remediation_command bleibt zuständig
        - id: akquise-liveness
          type: http
          target: http://127.0.0.1:9300/health
          interval: 300
          expect: { status: 200 }
          flake_polls: 2
          heal: { action: alert-only }
        - id: zerodox-onboarding-smoke
          type: http
          target: https://zerodox.de/api/internal/onboarding-smoke
          interval: 900
          headers: { X-Agent-Key: "$ZERODOX_AGENT_API_KEY" }
          expect: { status: 200, json_path: "ready", json_eq: true }
          flake_polls: 1
          heal: { action: alert-only }
        - id: analytics-bridge
          type: container
          target: guildscout-postgres
          interval: 600
          expect: { network: project_sicherheitsdienst-network }
          flake_polls: 1
          heal: { action: network-reconnect, target: "project_sicherheitsdienst-network guildscout-postgres" }
```

- [ ] **Step 2: YAML + Engine-Parse validieren** (yaml.safe_load + CheckDefinition.from_dict für jeden) → alle OK

- [ ] **Step 3: Bot restart** (`sudo systemctl restart shadowops-bot`) + warten bis healthy + Project Monitor init (5 Projekte, kein on_ready-Crash)

- [ ] **Step 4: Erst-Lauf verifizieren** — Log: alle 4 Checks laufen OK (Container im Netz, Health 200, onboarding ready). KEIN ungewollter Alert/Heal im Normalzustand.

---

## Task 4: Real-Trigger-Verifikation + Cut-over

- [ ] **Step 1: analytics-bridge real triggern (sicheres Heal) ⭐**

`sg docker -c 'docker network disconnect project_sicherheitsdienst-network guildscout-postgres'`
→ Engine erkennt FAIL → `network-reconnect` → wieder verbunden. Verifizieren: `docker inspect ... Networks` enthält Netz wieder + Discord-Alert. **Vorsicht:** kurze Analytics-Bridge-Unterbrechung (zerodox-web↔guildscout-postgres) — die Engine stellt sie binnen eines Check-Zyklus wieder her.

- [ ] **Step 2: zerodox-health / akquise-liveness Parallel-Verifikation**

Alert-Parität: bei einem echten/injizierten FAIL muss der Engine-Alert deckungsgleich zum Alt-Cron/-Watchdog sein. zerodox-health gegen Alt-Watchdog vergleichen (beide sehen denselben Zustand). Optional: akquise kurz stoppen → beide alarmieren.

- [ ] **Step 3: onboarding-smoke Verifikation**

Bei `ready:false` (503) muss die Engine FAIL + Alert liefern. Real-Trigger nur falls gefahrlos möglich; sonst Parallel-Beobachtung gegen synthetic-monitor.sh.

- [ ] **Step 4: Cut-over (NUR nach beweisbarer Übernahme, §8)**

Pro Check, nach Verifikation: Alt-Quelle **auskommentieren/disablen** (nicht löschen), 48 h beobachten:
- `cron-health-check.sh` (*/10) → Crontab-Zeile auskommentieren (Engine + bestehende remediation decken ab)
- `akquise-ai-watchdog.sh` (*/5) → Crontab-Zeile auskommentieren (Engine + systemd-Watchdog decken ab)
- `ensure-analytics-network.sh` (*/10) → Crontab auskommentieren (Engine network-reconnect übernimmt) — **@reboot-Zeile BEHALTEN** (Boot-Reihenfolge)
- Watchdogs (`zerodox-watchdog`, `zerodox-akquise-ai-watchdog`) **vorerst BEHALTEN** als externe Defense-in-Depth (Dead-Man-Prinzip) — erst in Plan 3 entscheiden.
- **NICHT abschalten:** `cron-agent-listener-health.sh` (erstellt GitHub-Issues — Aktion außerhalb Engine), synthetic-monitor.sh (noch nicht migriert).

- [ ] **Step 5: Inventar-Status aktualisieren** (`docs/MONITORING_INVENTORY.md`): migrierte Checks auf `abgelöst:<id>` + Doku-Commit.

---

## Self-Review
- **Spec-Coverage:** §4 Check-Schema (http+header+container) → Task 1/2; §7 Phase 2 (ZERODOX parallel) → Task 3; §8 Cut-over (beweisbar, 48h-disable) → Task 4.
- **Konservativ:** nur analytics-bridge autonom heilen (reversibel+sicher); zerodox-health bewusst alert-only (Kollision mit Auto-Rollback vermieden).
- **Ehrlich:** 3 Secret-abhängige + 3 synthetic-Checks bewusst Plan 3.

## Offen für Plan 3
- Secrets in Bot bringen (CRON_API_KEY/GITHUB_PAT/AKQUISE_AI_BEARER_TOKEN) → agent-listener, ci-main-health, akquise-synthetic.
- synthetic-* (Chrome/Playwright) + POST-Body-Check-Support.
- GuildScout/MayDay-Migration + Dead-Man-Watchdog-Härtung.
