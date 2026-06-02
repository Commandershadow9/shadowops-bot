# Security-Agent-Team — P1 Foundation Spec

**Status:** APPROVED (Brainstorm 2026-06-02) · bereit für Implementierungsplan
**Tracking-Issue:** [#290 — EPIC Security-Agent-Team](https://github.com/Commandershadow9/shadowops-bot/issues/290)
**Vorgänger-Doku:** [`docs/design/security-agent-team-vision.md`](./security-agent-team-vision.md) (Vision, gemerged via #289)
**Vorbild:** SEO-Multi-Agent-Team (`~/agents/projects/seo/`, LIVE seit 2026-05-14)

---

## 1. Ziel & Abgrenzung

ShadowOps soll **der** Security/Monitoring-Bot werden. Der gewachsene
`scan_agent.py`-Monolith (2641 Zeilen) wird langfristig zu einem isolierten
**Multi-Agent-Team** refactored. Diese Spec deckt **nur P1** ab: das Fundament neben
dem laufenden Monolithen, ohne den Monolithen abzuschalten.

**P1 liefert:**
- Job-Contract (`SecurityJob` / `JobResult`)
- `BaseSecurityWorker`-Abstraktion (Lifecycle + Exception-Isolation)
- Orchestrator-Stub (Trigger → Job-Dispatch)
- **Einen** echten Worker: `npm-audit-worker` (kleinster, klar abgegrenzter Scope — der #1069-Fall)
- `sec_jobs`-Tabelle (Job-Lifecycle)
- Geteilten `store_finding()`-Helper (entfernt Doppel-INSERT-Duplikation)
- systemd-Unit-Templates in `deploy/` (Install = separater Ops-Schritt)
- Feature-Flag `SECURITY_TEAM_ENABLED` (default **OFF**)

**P1 liefert NICHT** (→ spätere Phasen):
- Weitere Worker (code-scan, secret-scan, container-scan, …) → P2–P4
- Echtes Redis-Token-Bucket-Enforcement → P2 (mit dem ersten LLM-Worker)
- Entkernung des Monolithen zum reinen Orchestrator → P3+
- Webhook-reaktiver Trigger (Push → Sofort-Scan) → P3+
- Cron-Wiring in Produktion (Flag bleibt OFF bis Soak)
- Multi-Projekt-Onboarding-Skript → P5
- Auto-Merge von Security-Fixes (bleibt dauerhaft Human-in-the-Loop)

---

## 2. Festgelegte Entscheidungen

| # | Entscheidung | Begründung |
|---|---|---|
| Heimat | **shadowops-bot** (`src/integrations/security_engine/team/`) | ShadowOps *ist* der Security-Bot; Findings-DB, Discord, Jules-Workflow, Watchdogs leben hier (Issue #290, 2026-05-29) |
| Prozess-Modell | **Always-on systemd-Worker + Redis-Token-Cap** | Idle-Footprint trivial (SEO: 3,6 MB/Worker); echtes RAM-Risiko (parallele LLM-Scans) wird per Token-Bucket gedrosselt; copy-paste vom bewährten SEO-Muster; volle Ökosystem-Konsistenz (Brainstorm 2026-06-02) |
| Token-Bucket | **Seam in P1, Enforcement in P2** | npm-audit ist kein LLM-Scan (`npm audit --json`, billig) → braucht den Cap nie; Bucket bauen, den P1 nie nutzt = YAGNI |
| Contract/BaseWorker | **Muster nativ nachbauen, NICHT importieren** | SEO-Module liegen im Fremd-Repo `~/agents/`; shadowops-bot kann sie nicht importieren → ~150 Z. Pattern-Copy, keine Cross-Repo-Abhängigkeit |
| DB-Schicht | **bestehender asyncpg-Pool** (`security_engine/db.py`) | keine zweite DB-Abstraktion; `sec_jobs` + `findings` über denselben Pool |
| store_finding | **In P1 extrahieren** | echte Duplikation (`deep_scan.py:409` Methode vs. `scan_agent.py:1206` inline-INSERT); Worker + Monolith schreiben künftig denselben Pfad |
| Monolith | **unangetastet, Source-of-Truth** | kein Big-Bang; jeder Worker einzeln soak-getestet bevor Cutover |

---

## 3. Architektur

```
manueller/CLI Trigger  ──publish sec:trigger──▶
        │
        ▼
  security-orchestrator.service  ──persist queued──▶ sec_jobs (Postgres security_engine)
        │ publish sec:job:npm_audit:request   (Redis guildscout-redis:6379, namespace sec:*)
        ▼
  security-npm-audit-worker.service
        │ 1. persist in_progress (sec_jobs)
        │ 2. npm audit --json  (pro konfiguriertem Projekt-Pfad)
        │ 3. parse CVEs → store_finding()  (geteilter Helper)
        │ 4. persist completion (sec_jobs status=ok/partial/failed)
        ▼
  findings-Tabelle (bestehend)
        │ publish sec:job:npm_audit:result
        ▼
  Orchestrator (loggt Result; echte Aggregation erst P2)
```

**Isolations-Gewinn:** Security-Scans laufen ab P1 **außerhalb** des Bot-Prozesses
(heute: `bot.py:653` instanziiert `SecurityEngine` *im* Bot). Ein abstürzender Scan
kann den Discord-Bot nicht mehr mitreißen (Lehre aus #288).

---

## 4. Komponenten-Spezifikation

Alle neuen Dateien unter `src/integrations/security_engine/team/`.

### 4.1 `contracts.py`
Pydantic-Modelle, Muster aus `~/agents/projects/seo/contracts/job.py`.

```python
class JobStatus(str, Enum):
    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    OK = "ok"
    PARTIAL = "partial"      # Scan lief, aber Teilfehler (z.B. 1 von 3 Projekten failed)
    FAILED = "failed"
    CANCELLED = "cancelled"

class SecurityJob(BaseModel):           # Orchestrator → Worker
    job_id: UUID = Field(default_factory=uuid4)
    worker_type: str                    # z.B. "npm_audit"
    project: str                        # z.B. "guildscout"
    trigger: str = "manual"             # manual | daily | webhook
    token_cost: int = 0                 # SEAM für P2-Token-Bucket; npm_audit=0
    payload: dict[str, Any] = Field(default_factory=dict)  # z.B. {"path": "/home/cmdshadow/GuildScout/web"}
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    # validator: worker_type + project müssen non-empty sein

class JobResult(BaseModel):             # Worker → Orchestrator
    job_id: UUID
    worker: str
    project: str
    status: JobStatus
    findings_added: int = 0
    duration_ms: int = 0
    tokens_used: int = 0
    errors: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
```

### 4.2 `base_worker.py`
`BaseSecurityWorker(ABC)`, Muster aus `~/agents/projects/seo/workers/base.py`.

- `worker_type: str` — Subclass MUSS setzen (sonst `TypeError` im `__init__`).
- `__init__(self, db)` — bekommt den asyncpg-Pool (`security_engine/db.py`).
- `@abstractmethod async def process(self, job: SecurityJob) -> JobResult` — Domain-Logik.
- `async def handle_request(self, job) -> JobResult` — Wrapper:
  1. `_persist_in_progress(job)` → `sec_jobs`
  2. `try: result = await self.process(job)`
  3. `except Exception` → `JobResult(status=FAILED, errors=[...])`, **kein** re-raise, `logger.exception`
  4. `_persist_completion(job, result)` → `sec_jobs`
  5. return result
- `_persist_in_progress` / `_persist_completion` — best-effort, fangen eigene DB-Fehler ab + loggen `warning` (DB-Ausfall darf den Worker nicht crashen).

### 4.3 `orchestrator.py`
Stub, Muster aus `~/agents/projects/seo/orchestrator.py`.

- `__init__(self, redis, db)`.
- `async def dispatch_job(self, worker_type, project, payload, trigger="manual") -> SecurityJob`:
  1. `SecurityJob` bauen
  2. `INSERT INTO sec_jobs ... status='queued'` (best-effort)
  3. `publish sec:job:<worker_type>:request` mit `job.model_dump_json()`
- `async def handle_trigger(self, projects: list[str])`: für jeden konfigurierten
  aktiven Worker-Typ × Projekt einen `dispatch_job`. P1: nur `npm_audit`.
- **Token-Seam:** `dispatch_job` prüft in P1 `token_cost` *nicht* — TODO-Kommentar
  markiert die P2-Einhängestelle (Redis-Token erwerben bevor LLM-Worker dispatcht wird).

### 4.4 `workers/npm_audit_worker.py`
`class NpmAuditWorker(BaseSecurityWorker)`, `worker_type = "npm_audit"`.

`process(job)`:
1. `path = job.payload["path"]` (Projekt-Verzeichnis mit `package-lock.json`).
2. `npm audit --json` via `asyncio.create_subprocess_exec` (Timeout, `cwd=path`).
   - npm exit-code ≠ 0 ist **normal** wenn Findings existieren → nicht als Fehler werten.
   - Kein Lockfile / kein npm → `JobStatus.PARTIAL` + error-Notiz, kein Crash.
3. JSON parsen: `vulnerabilities` → je CVE ein Finding-Dict
   (severity, category="npm_audit", title, description, package, version, advisory-URL).
4. Pro CVE: Dedup **im Worker** — `compute_finding_fingerprint(...)` (aus `fingerprint.py`)
   berechnen, Lookup gegen `findings.finding_fingerprint`, nur bei Neuheit
   `db.store_finding(..., finding_fingerprint=fp)` rufen (verhindert tägliche Duplikate im Soak).
5. `JobResult(status=OK, findings_added=N, ...)`.

### 4.5 `runner.py`
Worker-Entrypoint, Muster aus `~/agents/projects/seo/workers/runner.py`.

- Baut Redis- + DB-Verbindung, instanziiert den Worker, `SUBSCRIBE sec:job:npm_audit:request`.
- Loop: Message → `SecurityJob.model_validate_json` → `worker.handle_request` → `publish ...:result`.
- **Graceful Shutdown:** SIGTERM → Subscribe-Loop sauber beenden, Verbindungen schließen.
- `CLAUDECODE` env-Var beim Subprozess-Spawn entfernen (nested-session-Schutz, libs-Regel).

### 4.6 Geteilter `store_finding()`-Helper
**Neue Methode auf `SecurityDB`** in `security_engine/db.py` (besitzt den asyncpg-Pool).
Call-Sites werden zu `await self.db.store_finding(...)`.

> ⚠️ **Befund Self-Review:** Die zwei bestehenden INSERTs sind **nicht identisch** —
> `deep_scan.py:409` schreibt 5 Spalten (`severity, category, title, description,
> affected_project`, kein Fingerprint, **keine** Dedup); `scan_agent.py:1206` schreibt
> 10 Spalten inkl. `session_id, affected_files, fix_type, github_issue_url,
> finding_fingerprint` und dedupt **vor** dem Insert. Der Helper muss daher die
> **Spalten-Obermenge** abdecken, ohne Verhalten zu ändern.

- Signatur (keyword-only, fehlende Felder → `NULL`):
  ```python
  async def store_finding(
      self, *, severity, category, title, description,
      affected_project=None, session_id=None, affected_files=None,
      fix_type=None, github_issue_url=None, finding_fingerprint=None,
      status="open",
  ) -> Optional[int]                     # gibt findings.id zurück (RETURNING id)
  ```
- **Verhaltenstreu:** ein INSERT mit allen Spalten; nicht übergebene = `NULL` (die
  Spalten sind ohnehin nullable, da `deep_scan` sie heute weglässt). `RETURNING id`
  immer; `scan_agent` ignoriert den Rückgabewert wie bisher.
- **Dedup bleibt Caller-Sache** — der Helper macht **kein** eigenes Fingerprint-Lookup.
  `scan_agent` berechnet `fp` weiterhin selbst beim Dedup-Lookup und übergibt ihn;
  `deep_scan` übergibt keinen (= heutiges Verhalten); der npm-audit-Worker dedupt selbst (4.4).
- **Beide Call-Sites migrieren:** `deep_scan.py:409` + `scan_agent.py:1206` → Helper-Call.
- **Regressionstest** (`test_store_finding_extraction.py`) beweist identisches SQL-Ergebnis
  vor/nach Extraktion für **beide** Spalten-Profile.

---

## 5. Datenmodell

Neue Migration `security_engine/migrations/002_sec_jobs.sql` (analog `seo_jobs`;
`001_finding_fingerprint.sql` existiert bereits → nächste Nummer ist 002):

```sql
CREATE TABLE IF NOT EXISTS sec_jobs (
    job_id        UUID PRIMARY KEY,
    worker_type   TEXT NOT NULL,
    project       TEXT NOT NULL,
    trigger       TEXT NOT NULL DEFAULT 'manual',
    status        TEXT NOT NULL DEFAULT 'queued',
    payload       JSONB NOT NULL DEFAULT '{}'::jsonb,
    result        JSONB,
    tokens_used   INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at    TIMESTAMPTZ,
    completed_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_sec_jobs_status  ON sec_jobs (status);
CREATE INDEX IF NOT EXISTS idx_sec_jobs_project ON sec_jobs (project, created_at DESC);
```

`findings`-Tabelle bleibt unverändert (Worker schreibt über `store_finding()`).

---

## 6. Redis-Channels

| Channel | Richtung | Zweck |
|---|---|---|
| `sec:trigger` | extern → Orchestrator | Scan-Anstoß (manuell/CLI in P1) |
| `sec:job:<worker_type>:request` | Orchestrator → Worker | Job-Dispatch (P1: `sec:job:npm_audit:request`) |
| `sec:job:<worker_type>:result` | Worker → Orchestrator | Result-Rückmeldung |

Redis-Instanz: `guildscout-redis:6379` (gleiche Infra wie SEO). Namespace `sec:*`
kollidiert nicht mit `seo:*`.

---

## 7. Konfiguration & Feature-Flag

`config.yaml` (Template in `config.example.yaml`):

```yaml
security_team:
  enabled: false          # Env-Override: SECURITY_TEAM_ENABLED
  active_workers: ["npm_audit"]
  projects:
    guildscout:
      npm_audit_path: "/home/cmdshadow/GuildScout/web"
    zerodox:
      npm_audit_path: "/home/cmdshadow/ZERODOX"
```

- `enabled=false` → Orchestrator/Worker laufen nicht in Prod (nur Soak/manuell).
- Worker- und Orchestrator-Code prüfen das Flag beim Start.

---

## 8. systemd-Units (Templates in `deploy/`, Install separat)

> **Regel (CLAUDE.md):** Worker-PR ändert **keinen** systemd-Service-State. Die
> `.service`-Templates kommen ins Repo (`deploy/`), das Installieren (Symlink in
> `~/.config/systemd/user/`, `daemon-reload`, `enable`) ist ein manueller Ops-Schritt
> des Users — analog zu den Watchdog-Units und dem `#294 sync-watchdog-units.sh`-IaC.

- `deploy/security-orchestrator.service` (`--user`, läuft als `cmdshadow`)
- `deploy/security-npm-audit-worker.service` (`--user`)
- Beide: `MemoryMax=` gesetzt (Leak-Backstop, #290 Q6), `Restart=on-failure`,
  `Environment=SECURITY_TEAM_ENABLED=...`, `XDG_RUNTIME_DIR` falls nötig.
- `--user` (nicht system-level): konsistent mit SEO-Team, kein root für Unit-Changes
  nötig, und der Worker braucht Lese-Zugriff auf die Projekt-Repos (`npm audit` in
  `~/GuildScout` etc.) — als `cmdshadow`-User gegeben.

---

## 9. Fehlerbehandlung & Isolation

1. **Worker-Ebene:** `handle_request` fängt jede Exception → `FAILED`-Result, kein re-raise.
2. **DB-Persistenz:** best-effort, eigene try/except → DB-Ausfall crasht den Worker nicht.
3. **Subprocess:** Timeout + sauberer Kill; npm-exit≠0 ≠ Fehler; fehlendes Lockfile → `PARTIAL`.
4. **systemd:** `Restart=on-failure` + `MemoryMax=` als zweiter Gürtel.
5. **Prozess-Isolation:** Scans außerhalb des Bot-Prozesses → Bot bleibt stabil.

---

## 10. Test-Strategie (kritisch — #288-Lehre)

- **Niemals** echte `npm` / `gh` / Redis / DB-Calls im Unit-Test. Alles gemockt.
- Tests einzeln ausführbar (`pytest tests/unit/test_X.py -x`), kein paralleles Testing (OOM).
- Abdeckung:
  - `test_security_contracts.py` — Serialize/Deserialize-Roundtrip, Validatoren, `token_cost`-Default.
  - `test_base_security_worker.py` — Lifecycle (`queued→in_progress→ok/failed` persistiert), Exception→`FAILED` ohne re-raise, DB-Fehler-Toleranz.
  - `test_security_orchestrator.py` — `dispatch_job` mit Fake-Redis (publish-Call + queued-Persist), `handle_trigger` fan-out.
  - `test_npm_audit_worker.py` — Parser gegen `npm audit --json`-Fixture (mit/ohne Vulns, kaputtes JSON, fehlendes Lockfile → `PARTIAL`), Subprocess gemockt.
  - `test_store_finding_extraction.py` — **Regressionstest**: beweist identisches Verhalten der zwei migrierten Call-Sites vor/nach Extraktion.

---

## 11. Datei-Manifest

**Neu:**
```
src/integrations/security_engine/team/__init__.py
src/integrations/security_engine/team/contracts.py
src/integrations/security_engine/team/base_worker.py
src/integrations/security_engine/team/orchestrator.py
src/integrations/security_engine/team/runner.py
src/integrations/security_engine/team/workers/__init__.py
src/integrations/security_engine/team/workers/npm_audit_worker.py
src/integrations/security_engine/migrations/002_sec_jobs.sql
deploy/security-orchestrator.service
deploy/security-npm-audit-worker.service
tests/unit/test_security_contracts.py
tests/unit/test_base_security_worker.py
tests/unit/test_security_orchestrator.py
tests/unit/test_npm_audit_worker.py
tests/unit/test_store_finding_extraction.py
```

**Geändert:**
```
src/integrations/security_engine/db.py        # store_finding()-Helper
src/integrations/security_engine/deep_scan.py # _store_finding → Helper-Call
src/integrations/security_engine/scan_agent.py# inline-INSERT → Helper-Call
config/config.example.yaml                    # security_team-Sektion
CLAUDE.md                                      # team/-Module dokumentieren
```

---

## 12. Akzeptanzkriterien P1

- [ ] `SECURITY_TEAM_ENABLED=false` → kein Verhalten ändert sich gegenüber heute (Monolith allein, Bot stabil).
- [ ] Mit Flag ON + manuellem `sec:trigger`: Orchestrator dispatcht `npm_audit`-Job, Worker scannt ein Projekt, schreibt Findings über `store_finding()`, `sec_jobs` zeigt `queued→in_progress→ok`.
- [ ] Worker-Crash (simuliert) → `FAILED` in `sec_jobs`, Bot + andere Prozesse unberührt.
- [ ] `store_finding()`-Regressionstest grün (Monolith-Verhalten identisch).
- [ ] Alle neuen Unit-Tests grün, einzeln ausführbar, ohne Live-Services.
- [ ] systemd-Templates in `deploy/`, **nicht** installiert (Ops-Schritt dokumentiert).

---

## 13. Anschluss

Nach P1-Soak (npm-audit-Output 7d gegen Monolith vergleichen) folgt **P2**:
`code-scan-worker` (erster LLM-Worker) + echtes Redis-Token-Bucket-Enforcement +
Result-Aggregation im Orchestrator. Token-Seam aus P1 wird dort aktiviert.
