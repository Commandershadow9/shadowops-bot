# Jules SecOps Workflow — Design Document

**Datum:** 2026-04-11
**Status:** Approved (Brainstorming Phase)
**Autor:** Shadow + Claude (Brainstorming-Session)
**Implementation Plan:** wird in separatem Dokument via `superpowers:writing-plans` erstellt

---

## 1. Kontext und Motivation

### 1.1 Ausgangslage

Der ShadowOps Bot betreibt einen `SecurityScanAgent` (`security_engine/scan_agent.py`),
der regelmäßig alle gehosteten Projekte auf Security-Findings scannt und diese in der
`security_analyst` Datenbank festhält. Für Code-Level-Findings (NPM/Python Dependencies,
Dockerfile-Issues, Semgrep-Hits) werden bereits GitHub-Issues erstellt.

Mit Einführung von **Jules** (Googles AI-Coding-Agent via GitHub-App, `google-labs-jules[bot]`)
entsteht die Möglichkeit, diese Code-Fixes **nicht mehr manuell bearbeiten zu müssen**, sondern
von Jules automatisiert als Pull-Request einreichen zu lassen.

### 1.2 Der PR #123 Vorfall (2026-04-11)

Ein initialer Versuch, Jules mit ShadowOps zu verbinden (via Gemini generiert), hat zu einem
**infinite Review-Loop** in [ZERODOX PR #123](https://github.com/Commandershadow9/ZERODOX/pull/123)
geführt — **31 Kommentare innerhalb von 90 Minuten** auf einem PR, der nur 9 Zeilen hinzufügt.

**Ursachen-Analyse:**

1. **`issue_comment` Events triggerten Re-Reviews.**
   Claudes eigene Review-Kommentare sind GitHub-Events, die den Handler re-triggerten.
   Loop-Schutz war nur für `pull_request` Events implementiert.

2. **Self-Comment-Filter unvollständig.**
   Der Handler filterte `shadowops-bot` und `google-labs-jules`, aber nicht
   `Commandershadow9` — der User, unter dem der Bot via `gh` CLI postet.

3. **`state_manager.get/set` existiert nicht.**
   Die SHA-Dedupe über `self.state_manager.get()` crashte mit `AttributeError`
   (der `StateManager` bietet nur `get_value(guild_id, key, default)`).

4. **`_delegate_to_jules` existiert nicht.**
   Im `executor_mixin.py` wurde eine nicht-definierte Methode aufgerufen.

5. **`verify_fix()` wurde als PR-Reviewer missbraucht.**
   Die Methode ist für Server-Fix-Verifikation designed, nicht für strukturierte PR-Reviews.
   Das führte zu semantischen Oszillationen (Confidence 92% → 82% → 88% → 92%...).

6. **Jules' "Acknowledged"-Auto-Reply verdoppelte die Comment-Rate.**
   Jules antwortet höflich auf jeden Review, jede Antwort ist ein neues Event.

**Konsequenz:** Das Design muss **mehrere unabhängige Sicherheitsnetze** gegen Loops haben,
und der gesamte Workflow muss auf ein sauberes State-Modell aufbauen.

### 1.3 Ziele

1. **Hybrid-Fix-Modus:** Der `SecurityScanAgent` fixt Server-Hardening-Findings selbst
   (UFW, Fail2ban, Docker, Permissions), delegiert aber Code-Findings an Jules via GitHub-Issue.

2. **Strukturierter Review-Workflow:** Jules öffnet PR → Claude Opus reviewt strukturiert
   (Blockers/Suggestions/Nits) → iteriert mit Jules bis zur Approval → Shadow merged manuell.

3. **Defense-in-Depth gegen Loops:** 7 unabhängige Sicherheitsnetze, jedes fängt einen
   spezifischen Fehlerfall ab.

4. **Selbstlernend:** Integration mit `agent_learning` DB — Shadow gibt Feedback via Discord-Reactions,
   Claude lernt Projekt-Konventionen und false-positive-Patterns über Zeit.

5. **Enterprise-Grade interne Struktur:** Feature-Flag, eigene DB-Tabelle, eigener Prompt,
   eigener Health-Endpoint, vollständige Config-ierbarkeit, 30-Sekunden-Rollback.

### 1.4 Non-Goals

- Kein Auto-Merge (Shadow's Final-Approval ist die letzte Verteidigungslinie).
- Keine eigene Dashboard-UI (Discord + PostgreSQL-Views reichen).
- Kein separater Microservice (Monolith-intern, sauber moduliert — Upgrade auf Service-Split
  bleibt für später offen).
- Kein ML-Modell, keine Embeddings — reiner Regel-Extraktions-Learning-Loop.
- Keine SLA-Eskalation bei langen Jules-Response-Zeiten.
- Keine Cross-Projekt-Statistiken in Phase 1.

---

## 2. Architektur-Entscheidung

**Gewählt: Option A — Modular Monolith im bestehenden `github_integration/` Mixin-Pattern.**

Begründung (gegen Option C — separater Service):

| Ressource | Heute im Bot | C-Kosten |
|---|---|---|
| GitHub Webhook-Endpoint (Port 9090) | aiohttp in Bot | Separater Endpoint oder IPC-Bridge pro Event |
| `ai_engine` (Claude/Codex) | Bot | Duplicate Config + Secrets |
| PostgreSQL-Pool zu `security_analyst` DB | asyncpg in Bot | Doppelte Connections |
| Discord-Client | discord.py in Bot | IPC pro Nachricht oder duplicated Client |
| Finding→Issue-Pipeline | `scan_agent.py` | Cross-Service DB-Access |

Bei ~5 PRs/Tag über 5 Projekte ist Service-Split **Premature Distribution**. Der Upgrade-Pfad
A → C bleibt offen, weil die Modul-Grenzen schon sauber sind.

---

## 3. High-Level Flow

```
1. SecurityScanAgent findet Finding → klassifiziert als "code_fix"
   → erstellt GitHub-Issue mit Label ["security","jules"] + Jules-Embed im Body

2. Jules öffnet PR → GitHub webhook → aiohttp (Port 9090)
   → WebhookMixin dispatched pull_request event
   → JulesWorkflowMixin.handle_pr_event() erkennt via PR-Body "Fixes #N"
     + Issue hat Label "jules" → das ist ein Jules-PR

3. JulesWorkflowMixin:
   → 7 Loop-Schutz-Gates (Section 6)
   → ruft ai_engine.review_pr() mit dediziertem Prompt + Learning-Kontext
   → Claude liefert strukturiertes JSON (Schema-validiert)

4. Entscheidung (deterministisch aus JSON):
   (a) 0 Blockers AND scope_check.in_scope → APPROVED
       → Label "claude-approved", Discord-Ping, DB-State 'approved'
       → Single-Comment editiert (nicht neu)
   (b) blockers > 0 OR out of scope → REVISION
       → Single-Comment editiert mit Revision-Liste
       → DB-State 'revision_requested', iteration_count++
       → Jules macht Commits → neuer HEAD-SHA → zurück zu Schritt 3

5. Abbruch-Bedingungen (jederzeit):
   → iteration_count >= 5 → 'escalated'
   → (now - created_at) >= 2h → 'escalated'
   → tokens_consumed >= 50000 → 'escalated'
   → Circuit-Breaker Trip → 'escalated'

6. Shadow merged manuell → PR-Close-Event → state='merged' → Finding resolved
```

---

## 4. Komponenten

### 4.1 Neue Dateien (alle in `src/integrations/github_integration/`)

| Datei | Umfang | Zweck |
|---|---|---|
| `jules_workflow_mixin.py` | ~400 Zeilen | PR-Handler, Gate-Pipeline, Review-Orchestrierung, Comment-Edit, Final-Approval |
| `jules_state.py` | ~150 Zeilen | asyncpg-Layer für `jules_pr_reviews`: atomic Lock-Claim, Stale-Lock-Recovery, CRUD |
| `jules_review_prompt.py` | ~100 Zeilen | Prompt-Builder mit 6 Blöcken + Learning-Kontext-Loader |
| `src/schemas/jules_review.json` | ~30 Zeilen | JSON-Schema für Claude-Review-Output, `jsonschema`-validiert |

### 4.2 Modifikationen bestehender Dateien

| Datei | Änderung |
|---|---|
| `github_integration/core.py` | `JulesWorkflowMixin` zur Klassen-Komposition, `self.jules_state` init, `pull_request` Handler-Dispatch an das neue Mixin **vor** dem bestehenden EventHandlersMixin (first-match-wins bei Jules-PRs) |
| `security_engine/scan_agent.py` | `_create_github_issue()` erweitern: Fix-Mode-Klassifizierung via `FIX_MODE_DECISION`-Map, `jules`-Label + Jules-Embed im Body bei `code_fix`-Findings |
| `integrations/ai_engine.py` | **Neue** Methode `review_pr(diff, finding_context, project, iteration)` — keine Änderung an TaskRouter oder bestehenden Methoden |
| `utils/health_server.py` | Neuer Endpoint `GET /health/jules` mit Status-Metriken aus der DB |
| `config/config.example.yaml` | Neuer `jules_workflow:` Block |

### 4.3 Was explizit unverändert bleibt

- `ai_engine.py` TaskRouter — keine globalen Mapping-Änderungen (HIGH bleibt `standard`).
- `orchestrator/` — kein Jules-Code. Der Orchestrator ist für Server-Response-Workflows,
  nicht für PR-Reviews.
- `event_handlers_mixin.py` — bleibt für normale PR-Notifications zuständig.

---

## 5. State-Machine

```
                       ┌──────────────┐
                       │   PENDING    │  ← Issue erstellt, noch kein PR
                       └──────┬───────┘
                              │ Jules öffnet PR
                              ▼
                       ┌──────────────┐
    Gates passed ────▶│   REVIEWING  │  ← Lock gehalten, kein paralleler Review
                       └──────┬───────┘
                              │ Claude fertig
                              ▼
                       ┌──────────────┐
                       │   DECIDING   │  ← Deterministischer Verdict-Compute
                       └──────┬───────┘
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
       ┌────────────┐  ┌────────────┐  ┌────────────┐
       │  APPROVED  │  │  REVISION  │  │  ESCALATED │
       └─────┬──────┘  └─────┬──────┘  └─────┬──────┘
             │               │                │
       PR-Merge-Event   Jules committed   Human needed
             │          → neuer SHA        (kein Auto-Action
             ▼          → REVIEWING         bis Reset)
       ┌────────────┐
       │   MERGED   │  ← Finding resolved in security_analyst.findings
       └────────────┘
```

**Terminal-States:** `MERGED` (happy path), `ESCALATED` (Human-Override nötig),
`ABANDONED` (PR manuell geschlossen ohne Merge).

**`REVIEWING` ist ein Lock.** Via atomarem PostgreSQL-UPDATE gesetzt, durch Stale-Lock-Recovery
(Timeout 10min) selbstheilend.

---

## 6. Loop-Schutz — 7 Schichten

Alle Gates laufen in dieser Reihenfolge; jedes Gate kann `SKIP` zurückgeben, was keinen
Comment, keinen Discord-Log (außer DEBUG) und keine Token-Kosten erzeugt.

### Schicht 1: Trigger-Whitelist

```python
ALLOWED_TRIGGERS = {
    'pull_request:opened',
    'pull_request:synchronize',
    'pull_request:ready_for_review',
}

BLOCKED_TRIGGERS = {
    'issue_comment:*',                    # Die Hauptursache von PR #123
    'pull_request:edited',
    'pull_request:labeled',
    'pull_request_review:*',
    'pull_request_review_comment:*',
}
```

Der `issue_comment` Handler existiert ausschließlich für den manuellen `/review`-Trigger,
und nur wenn:
- Comment-Body == exakt `/review`
- Comment-Author == `Commandershadow9`
- Comment-Body startet NICHT mit `### 🛡️` (Self-Comment-Filter)

### Schicht 2: SHA-Dedupe mit atomic Lock-Claim

```sql
UPDATE jules_pr_reviews
SET status = 'reviewing',
    lock_acquired_at = now(),
    lock_owner = $1
WHERE repo = $2
  AND pr_number = $3
  AND status IN ('pending', 'revision_requested', 'approved')
  AND (last_reviewed_sha IS NULL OR last_reviewed_sha != $4)
RETURNING id;
```

Drei Effekte in einem Query:
1. `status='reviewing'` bereits → UPDATE matcht nicht → Lock gehalten
2. Gleicher SHA → UPDATE matcht nicht → kein Re-Review
3. 0 Rows zurück → kein Review gestartet

**Stale-Lock-Recovery** beim Bot-Start und bei jedem Handler-Eintritt:

```sql
UPDATE jules_pr_reviews
SET status = 'revision_requested',
    lock_owner = NULL,
    lock_acquired_at = NULL
WHERE status = 'reviewing'
  AND lock_acquired_at < now() - interval '10 minutes';
```

### Schicht 3: Cooldown (5 Minuten)

```python
if (now_utc() - row.last_review_at).total_seconds() < 300:
    return ReviewDecision.SKIP('cooldown')
```

Fängt duplicate Webhook-Deliveries und schnelle Jules-Commits ab.

### Schicht 4: Hard-Cap auf Iterationen

```python
if row.iteration_count >= 5:
    await self._release_lock_and_escalate(row, 'max_iterations')
    return ReviewDecision.SKIP('max_iterations')
```

`escalated` ist terminal-bis-Human-Reset — kein Webhook kann den PR zurück holen.

### Schicht 5: Global Circuit-Breaker (Redis)

```python
key = f'jules:circuit:{repo}'
count = await redis.incr(key)
if count == 1:
    await redis.expire(key, 3600)
if count > 20:
    await self._notify_discord_alarm()
    return ReviewDecision.SKIP('circuit_breaker_open')
```

Letzte Reißleine bei Bug-Kaskade.

### Schicht 6: Time-Cap pro PR (2 Stunden)

```python
if row.created_at < now_utc() - timedelta(hours=2):
    await self._release_lock_and_escalate(row, 'timeout_2h')
    return ReviewDecision.SKIP('timeout')
```

Zweites Sicherheitsnetz neben Iteration-Cap — fängt "langsame" Loops ab.

### Schicht 7: Single-Comment-Edit-Strategie

Kein Loop-Schutz-Gate im engeren Sinne, aber der wichtigste Noise-Reducer:

```python
if not row.review_comment_id:
    result = gh('pr', 'comment', pr, '--body', body)
    row.review_comment_id = parse_comment_id(result)
else:
    gh('api', f'repos/{owner}/{repo}/issues/comments/{row.review_comment_id}',
       '--method', 'PATCH', '--field', f'body={body}')
```

**Effekt:** Ein einziger Comment pro PR, wächst zu einer History mit collapsed
`<details>`-Block für Vor-Iterationen. `PATCH` erzeugt **kein** `issue_comment:created` Event —
der Webhook-Feedback-Loop ist damit eliminiert.

### Gate-Pipeline (Code-Skizze)

```python
async def should_review(
    self, repo: str, pr_number: int, head_sha: str, event_type: str
) -> ReviewDecision:
    if not self.config.jules_workflow.enabled:
        return ReviewDecision.SKIP('feature_disabled')

    if event_type not in ALLOWED_TRIGGERS:
        return ReviewDecision.SKIP('blocked_trigger')

    if await self._circuit_breaker_open(repo):
        return ReviewDecision.SKIP('circuit_breaker_open')

    row = await self.jules_state.try_claim_review(repo, pr_number, head_sha, self.process_id)
    if not row:
        return ReviewDecision.SKIP('already_reviewed_or_locked')

    if row.iteration_count >= 5:
        await self._release_lock_and_escalate(row, 'max_iterations')
        return ReviewDecision.SKIP('max_iterations')

    if row.created_at < now_utc() - timedelta(hours=2):
        await self._release_lock_and_escalate(row, 'timeout_2h')
        return ReviewDecision.SKIP('timeout')

    if not self._cooldown_passed(row):
        await self.jules_state.release_lock(row)
        return ReviewDecision.SKIP('cooldown')

    return ReviewDecision.PROCEED(row)
```

Reihenfolge: billige Checks zuerst (Feature-Flag, Config), teure Checks später
(DB-Atomic-Claim).

---

## 7. Datenbank-Schemas

### 7.1 `security_analyst.jules_pr_reviews` (Primär-State)

Lebt in der bestehenden `security_analyst` Datenbank (asyncpg-Pool vorhanden).

```sql
CREATE TABLE IF NOT EXISTS jules_pr_reviews (
    id              BIGSERIAL PRIMARY KEY,
    repo            TEXT NOT NULL,
    pr_number       INTEGER NOT NULL,
    issue_number    INTEGER,
    finding_id      BIGINT REFERENCES findings(id) ON DELETE SET NULL,

    status          TEXT NOT NULL CHECK (status IN (
                      'pending','reviewing','approved','revision_requested',
                      'escalated','merged','abandoned')),

    last_reviewed_sha  TEXT,
    iteration_count    INTEGER NOT NULL DEFAULT 0,
    last_review_at     TIMESTAMPTZ,
    lock_acquired_at   TIMESTAMPTZ,
    lock_owner         TEXT,

    review_comment_id  BIGINT,                     -- GitHub Comment-ID für Edit
    last_review_json   JSONB,
    last_blockers      JSONB,
    tokens_consumed    INTEGER NOT NULL DEFAULT 0,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at       TIMESTAMPTZ,
    human_override  BOOLEAN NOT NULL DEFAULT false,

    UNIQUE (repo, pr_number)
);

CREATE INDEX idx_jules_status ON jules_pr_reviews(status)
    WHERE status NOT IN ('merged','abandoned');

CREATE INDEX idx_jules_finding ON jules_pr_reviews(finding_id);
```

### 7.2 `agent_learning` Integration (Learning-Loop)

Lebt in der bestehenden `agent_learning` Datenbank (Port 5433 via `patch_notes_learning`).

**Reused bestehende Tabellen:**

| Tabelle | Verwendung |
|---|---|
| `agent_feedback` | Shadow 👍/👎/📝 aus Discord → `agent_name='jules_reviewer'`, `reference_id=pr_number` |
| `agent_quality_scores` | Pro Review: Auto-Score + Feedback-Score + Combined — Rolling-Trend über Zeit |
| `agent_knowledge` | Gelernte Projekt-Konventionen, vor jedem Review als Kontext geladen |

**Neue Tabelle:**

```sql
CREATE TABLE IF NOT EXISTS jules_review_examples (
    id              BIGSERIAL PRIMARY KEY,
    project         TEXT NOT NULL,
    pr_ref          TEXT,
    diff_summary    TEXT NOT NULL,
    review_json     JSONB NOT NULL,
    outcome         TEXT NOT NULL CHECK (outcome IN (
                      'good_catch','false_positive','missed_issue','approved_clean')),
    user_feedback   TEXT,
    weight          REAL NOT NULL DEFAULT 1.0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_jrex_project_outcome ON jules_review_examples(project, outcome);
```

### 7.3 Learning-Loop

```
1. Review-Start: lade letzte 3 good_catch Examples + 1 false_positive
                 + 10 agent_knowledge entries für dieses Projekt
   → Few-Shot im Prompt

2. Discord-Post: Shadow reagiert mit 👍/👎/📝 (Kommentar-Button)
   → LearningNotifier schreibt agent_feedback

3. Nightly-Batch (23:00): klassifiziert outcome automatisch:
   - 👍 + Merge → 'approved_clean'
   - 👎 + Merge trotzdem → 'false_positive' (weight −0.5)
   - 👎 + Revision → 'good_catch' (weight +0.3)
   - Human-Override nach Approval → 'missed_issue' (weight −0.3)
   → jules_review_examples + agent_quality_scores update

4. Nächster Review nutzt diese Daten automatisch
```

---

## 8. Prompt-Design

### 8.1 Prompt-Struktur (6 Blöcke)

1. **Rolle:** Senior Security-Reviewer, strikt bei Security, pragmatisch bei Stil.
2. **Finding-Kontext:** Vollständiges Finding aus `security_analyst.findings` — CVE, Severity,
   Beschreibung, erwarteter Scope.
3. **Projekt-Knowledge:** `SELECT * FROM agent_knowledge WHERE agent='jules_reviewer' AND project=$1 LIMIT 10`
4. **Few-Shot:** 3 Beispiele mit `outcome='good_catch'` + 1 `false_positive` zur Abschreckung.
5. **Diff:** `gh pr diff`, abgeschnitten auf `max_diff_chars` (default 8000) + Datei-Liste
   nach 8 Kategorien.
6. **Aufgabe + Schema:** JSON-Output gemäß `jules_review.json`, Klassifizierung nach
   BLOCKER/SUGGESTION/NIT.

### 8.2 JSON-Schema (`src/schemas/jules_review.json`)

```json
{
  "type": "object",
  "required": ["verdict","summary","blockers","suggestions","nits","scope_check"],
  "properties": {
    "verdict": {"type": "string", "enum": ["approved","revision_requested"]},
    "summary": {"type": "string", "maxLength": 500},
    "blockers":    {"type": "array", "items": {"$ref": "#/$defs/issue"}},
    "suggestions": {"type": "array", "items": {"$ref": "#/$defs/issue"}},
    "nits":        {"type": "array", "items": {"$ref": "#/$defs/issue"}},
    "scope_check": {
      "type": "object",
      "required": ["in_scope","explanation"],
      "properties": {
        "in_scope":    {"type": "boolean"},
        "explanation": {"type": "string"}
      }
    }
  },
  "$defs": {
    "issue": {
      "type": "object",
      "required": ["title","reason","file","severity"],
      "properties": {
        "title":         {"type": "string"},
        "reason":        {"type": "string"},
        "file":          {"type": "string"},
        "line":          {"type": ["integer","null"]},
        "severity":      {"enum": ["critical","high","medium"]},
        "suggested_fix": {"type": "string"}
      }
    }
  }
}
```

### 8.3 Deterministische Verdict-Regel

Claudes `verdict`-Feld wird **nach der AI-Antwort** vom Bot überschrieben:

```python
def compute_verdict(review: dict) -> str:
    if review['blockers']:
        return 'revision_requested'
    if not review['scope_check']['in_scope']:
        return 'revision_requested'
    return 'approved'
```

Verhindert Confidence-Oszillation (der PR #123 Approval-Loop).

### 8.4 Comment-Format (der editierbare Single-Comment)

```markdown
### 🛡️ Claude Security Review — Iteration 2 of 5

**Verdict:** 🔴 REVISION REQUESTED

**Summary:** ...

---

#### 🔴 Blockers (muss gefixt werden)
1. **Scope-Violation: `defu` wurde entfernt**
   - Datei: `web/package.json:23`
   - Grund: ...
   - Fix: ...

#### 🟡 Suggestions (nicht blockierend)
...

#### ⚪ Nits
...

**Scope-Check:** ❌ Out of scope

<details>
<summary>Previous Reviews</summary>
- Iteration 1 (2026-04-11 13:48): ...
</details>

---
*ShadowOps SecOps Workflow · PR #123 · Finding #4567*
```

---

## 9. ScanAgent-Integration

### 9.1 Fix-Mode-Klassifizierung

```python
FIX_MODE_DECISION = {
    # Code-Findings → Jules
    'npm_audit':          'jules',
    'pip_audit':          'jules',
    'dockerfile':         'jules',
    'code_vulnerability': 'jules',     # CodeQL, Semgrep

    # Infrastruktur → Self-Fix
    'ufw':              'self_fix',
    'fail2ban':         'self_fix',
    'crowdsec':         'self_fix',
    'aide':             'self_fix',
    'docker_config':    'self_fix',

    # Explizit NICHT automatisiert
    'ssh_config':       'human_only',
    'database_schema':  'human_only',
}

def classify_fix_mode(finding: Finding) -> str:
    mode = FIX_MODE_DECISION.get(finding.category, 'human_only')
    if mode == 'jules':
        if finding.project in SKIP_ISSUE_PROJECTS:
            return 'human_only'
        if not PROJECT_REPO_MAP.get(finding.project):
            return 'human_only'
    return mode
```

### 9.2 Issue-Body-Template (Jules-spezifisch)

Enthält explizit:
- **Acceptance Criteria** (was für Approval nötig ist)
- **Scope-Warnung** ("nur die affected files anfassen, kein Refactoring")
- **Anweisung `Fixes #N` im PR-Body** (für die Issue-Verknüpfung)
- **Anweisung: KEIN "Acknowledged"-Comment** (zweite Verteidigungslinie gegen den PR #123-Loop)
- **Finding-ID** als Rückverweis für Cross-DB-Joins

Labels: `security`, `jules`, `severity-{level}`.

---

## 10. Konfiguration

```yaml
jules_workflow:
  enabled: true
  max_iterations: 5
  cooldown_seconds: 300
  max_hours_per_pr: 2
  circuit_breaker:
    max_reviews_per_hour: 20
    pause_duration_seconds: 3600

  excluded_projects:
    - sicherheitsdienst   # frozen

  max_diff_chars: 8000
  few_shot_examples: 3
  project_knowledge_limit: 10
  token_cap_per_pr: 50000

  notification_channel: "🛡️-security-ops"
  escalation_channel: "🚨-alerts"
  role_ping_on_escalation: "@Shadow"

  dry_run: false           # für Phase 4 Testing
```

Alle Werte live-editierbar ohne Code-Deploy.

---

## 11. Error-Handling

### 11.1 Fehler-Taxonomie

| Klasse | Beispiel | Reaktion |
|---|---|---|
| Transient | GitHub 502, Claude-Timeout | 3 Retries mit Backoff (2s/8s/30s), Lock gehalten |
| Config | `claude-approved` Label fehlt | Auto-Create via API, dann Retry |
| AI-Invalid-JSON | Schema-Validation fail | 1 Retry mit expliziterem Hint, dann `escalated` |
| DB | asyncpg Connection lost | Bubble-up, Webhook 200 zurück, Stale-Lock-Recovery |
| Permanent | Repo-Access fehlt | Einmaliger Discord-Ping, `escalated` |
| Bug | KeyError/AttributeError | Try/Except am Entry, Discord-Alarm, Webhook 200, Lock-Release |

### 11.2 "Webhook-200-Immer"-Regel

```python
async def _webhook_handler(request):
    try:
        await jules_workflow.process(payload)
    except Exception:
        logger.exception('jules workflow error')
        await discord_alarm('jules handler crashed')
    return web.Response(status=200)
```

Vermeidet GitHub-Redelivery-Stürme bei Bugs.

### 11.3 Lock-Cleanup-Garantie

Jeder Code-Pfad der `status='reviewing'` setzt MUSS den Lock im `finally`-Block freigeben.

---

## 12. Observability

### 12.1 Discord-Kanäle

| Kanal | Was |
|---|---|
| `🛡️-security-ops` | Neue Jules-PR erkannt, Review abgeschlossen (APPROVED/REVISION), Iteration-Count |
| `🚨-alerts` | Eskalationen mit `@Shadow`-Ping |
| `🧠-ai-learning` | Nightly-Summary (Reviews/Approvals/Quality-Score) |

### 12.2 Health-Endpoint

`GET /health/jules` auf Port 8766 mit:
- `active_reviews`, `pending_prs`, `escalated_24h`
- `circuit_breakers_open` (Liste aktiver Breaker)
- `stats_24h` (Totals, Tokens, avg iterations)
- `last_review_at`, `stale_locks_cleaned`

### 12.3 Strukturiertes Logging

```
[jules] repo=ZERODOX pr=123 finding=4567 iter=2/5 sha=0b08c73 event=pull_request:synchronize
[jules]   → gate=cooldown passed
[jules]   → gate=sha_dedupe passed
[jules]   → gate=lock_claim acquired
[jules]   → ai_engine.review_pr called (model=claude-opus-4-6)
[jules]   → review returned verdict=revision_requested blockers=1
[jules]   → comment edited (comment_id=4229455735)
[jules] ✓ processed in 18.4s (tokens=4200)
```

### 12.4 PostgreSQL-Views als Metrik-Quelle

```sql
CREATE OR REPLACE VIEW jules_daily_stats AS
SELECT
    date_trunc('day', created_at) AS day,
    repo,
    COUNT(*) FILTER (WHERE status = 'approved')           AS approved,
    COUNT(*) FILTER (WHERE status = 'revision_requested') AS revisions,
    COUNT(*) FILTER (WHERE status = 'escalated')          AS escalated,
    COUNT(*) FILTER (WHERE status = 'merged')             AS merged,
    AVG(iteration_count)                                  AS avg_iterations,
    SUM(tokens_consumed)                                  AS total_tokens
FROM jules_pr_reviews
GROUP BY 1, 2
ORDER BY 1 DESC, 2;
```

---

## 13. Testing-Strategie

### 13.1 Test-Pyramide

- **Unit (~25 Tests)** — pro Gate + Verdict-Compute + Prompt-Builder.
- **Mixin-Level (~10-15 Tests)** — Handler-Flow, State-Übergänge, Lock-Claim gegen echte
  Postgres (via `testcontainers-postgres`).
- **Integration (1 Test)** — End-to-End-Happy-Path mit gemockten externen Services.
- **Regression (1 Test)** — PR #123 Szenario: 31 `issue_comment` Events, keine triggern
  einen Review, `tokens_consumed==0`.

### 13.2 Was gemockt wird

- `ai_engine.review_pr` → feste JSON-Responses
- GitHub API → subprocess-Mock
- PostgreSQL → **echte DB** via testcontainers (wegen atomic Lock-Claim)
- Redis → `fakeredis`

### 13.3 Dry-Run-Mode

Config-Flag `jules_workflow.dry_run: true` → Bot loggt, was er tun würde, schreibt
aber nicht in DB und postet nichts an GitHub/Discord. Risikofreies Live-Testing.

---

## 14. Rollout-Plan

| Phase | Dauer | Aktion |
|---|---|---|
| **1. Groundwork** | 2h | Revert Gemini-Changes, Design-Doc committen, DB-Tabelle anlegen, Config-Schema |
| **2. Core-Impl** | 6-8h | `JulesWorkflowMixin`, `jules_state.py`, `jules_review_prompt.py`, `ai_engine.review_pr()`, ScanAgent-Classifier |
| **3. Tests** | 3-4h | Unit + Mixin + Integration + Regression |
| **4. Dry-Run** | 1 Tag | Live-Events, keine Writes, Log-Review |
| **5. Live (1 Projekt)** | 2-3 Tage | ZERODOX als Testballon |
| **6. Full Rollout** | 1 Woche | shadowops-bot, GuildScout, ai-agent-framework |

`sicherheitsdienst` bleibt excluded (frozen).

### 14.1 Rollback

Config-Flip: `jules_workflow.enabled: false`.
Kein Code-Revert, kein Bot-Restart. ~30 Sekunden Rollback-Zeit.

---

## 15. Offene Punkte für den Implementation-Plan

- Exaktes Mapping der `FIX_MODE_DECISION` Kategorien zu den heutigen Finding-Categories im ScanAgent
- `testcontainers-postgres` vs. einfacher pytest-Fixture: Entscheidung in Phase 3
- Ob `/jules-stats` Slash-Command in Phase 1 oder später kommt (Non-Goal laut Abschnitt 1.4, aber trivial)
- Genaue Prompt-Formulierung der BLOCKER/SUGGESTION/NIT-Definitionen — wird in Phase 2 iteriert
- Konkrete Discord-Embed-Struktur für Approval-Ping (Thread oder Inline?)

Diese Punkte werden im Implementation-Plan (via `superpowers:writing-plans`) konkretisiert.

---

## Anhang A: Vermeidung der PR #123 Fehler

| PR #123 Fehler | Wie das neue Design es abfängt |
|---|---|
| `issue_comment` triggert Review | Schicht 1: Trigger-Whitelist blockt alle `issue_comment` Events außer manuelles `/review` |
| `Commandershadow9` als Bot-Author nicht gefiltert | Schicht 1: Self-Comment-Filter prüft Body-Prefix `### 🛡️` zusätzlich zu Author |
| `state_manager.get/set` crasht | Eigener `jules_state.py` asyncpg-Layer, keine StateManager-Abhängigkeit |
| `_delegate_to_jules` undefiniert | Keine Orchestrator-Änderungen — Jules-Workflow ist GitHub-only |
| `verify_fix()` missbraucht als Reviewer | Eigene `ai_engine.review_pr()` Methode mit dediziertem Prompt und Schema |
| Jules' "Acknowledged" verdoppelt Comments | Explizite Anweisung im Issue-Body; außerdem ignoriert Schicht 1 diese Events sowieso |
| Kein Iteration-Cap | Schicht 4: Hard-Cap 5 Iterationen → `escalated` |
| Kein Cooldown | Schicht 3: 5-Min-Cooldown zwischen Reviews |
| Jeder Review = neuer Comment | Schicht 7: Single-Comment-Edit via PATCH |
| Kein globaler Breaker | Schicht 5: Redis Circuit-Breaker 20/h pro Repo |
| Kein Time-Cap | Schicht 6: Max 2h pro PR |
| Verdict oszilliert (92%→82%→88%) | Deterministische Verdict-Regel aus strukturiertem JSON, nicht Confidence-Score |

**31 Kommentare in 90 Minuten → in der neuen Architektur: maximal 5 Kommentare in max 2 Stunden,
und die sind alle Edits desselben Comments.**
