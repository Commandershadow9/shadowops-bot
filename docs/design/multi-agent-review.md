# Multi-Agent Review Pipeline — Design Document

**Datum:** 2026-04-14
**Status:** Approved
**Autor:** Shadow + Claude (Brainstorming-Session)
**Implementation-Plan:** wird in separatem Dokument via `superpowers:writing-plans` erstellt

---

## 1. Kontext

Der ShadowOps Bot hat heute drei Klassen automatisierter Agents, die unabhängig arbeiten:

| Agent | Was macht er heute | Output |
|---|---|---|
| **SecurityScanAgent** (`scan_agent.py`) | Server-Härtung + Code-Findings in allen Projekten | Self-Fix (UFW/Fail2ban) ODER GitHub-Issue mit `jules` Label ODER GitHub-Issue ohne Jules |
| **SEO-Agent** (`~/agents/projects/seo/`) | SEO/GSC/GEO/AEO-Optimierung in ZERODOX, GuildScout, MayDay-Sim | PR (für sichere Fixes) — aktuell ohne Jules-Delegation |
| **Jules Workflow** (seit 2026-04-11) | Claude Opus reviewt Jules-PRs mit 7-Schichten Loop-Schutz | Approved-Label + Discord-Ping ODER Revision-Comment |

**Status 2026-04-14:**
- 17 Jules-PRs auf ZERODOX erfolgreich reviewed + gemerged
- SEO-Agent erstellt PRs direkt — werden aktuell nicht automatisch reviewt
- Jules' eigene "Top Suggestions" aus dem Dashboard werden nicht automatisch genutzt
- Server-Agents und Jules arbeiten nebeneinander, aber nicht verzahnt

### 1.1 Ziel

Die existierenden Agents mit Jules und Claude verzahnen, sodass:
1. **Unsichere/komplexe Fixes der Server-Agents** werden an Jules delegiert (wie bisher bei Security, neu auch bei SEO)
2. **Jules' eigene Findings** werden automatisch als Sessions gestartet
3. **Alle Agent-PRs** (egal ob von Jules, SEO-Agent oder Codex-Sessions) werden von Claude abgesegnet
4. **Triviale PRs** werden automatisch gemerged nach Claude-Approval
5. **Das System lernt** — Outcome-Tracking in `agent_learning` DB fliesst in zukünftige Reviews ein

### 1.2 Nicht-Ziele

- **SEO-Agent Refactoring** — der Code bleibt unberührt, wir schalten nur Review vor/nach
- **SecurityScanAgent Refactoring** — existierende Jules-Integration bleibt
- **Dependabot-Review** — GitHub prüft schon Dep-Updates, keine Redundanz
- **Premature Adapter-Pattern** — wir extrahieren nur was wir 2-3x brauchen

---

## 2. Architektur-Entscheidung

**Gewählt: Adapter-Pattern mit 3 konkreten Agent-Adaptern, bestehenden Jules-Code als Basis.**

Begründung:
- Wir haben drei unterschiedliche PR-Typen (Jules, SEO, Codex) mit unterschiedlichen Review-Anforderungen
- Bestehender `JulesWorkflowMixin` ist bereits Agent-agnostisch genug (Gates, Lock, State) — braucht nur einen Detector-Dispatcher
- 3 echte Adapter-Implementations rechtfertigen das Pattern (Rule of Three)
- Alternativen verworfen:
  - *Separate Mixins pro Agent* → 3x Code-Duplikation (Gates, Lock, Learning)
  - *Ein universeller Prompt* → schwächere Review-Qualität bei Edge-Cases
  - *Refactoring in große Pipeline* → zu hohes Risiko für bestehenden funktionierenden Code

---

## 3. High-Level Flow

Zwei parallele Eintrittspunkte führen zum gemeinsamen Review-Flow:

```
FLOW A: Agent erstellt Issue → Jules → PR → Claude
────────────────────────────────────────────────────────
ScanAgent oder SEO-Agent findet komplexes Finding
         ↓
Erstellt GitHub-Issue mit @jules Mention + Label
         ↓
Jules API bekommt Issue → öffnet Session → arbeitet → öffnet PR
         ↓
GitHub Webhook → unser Bot detectet Jules-PR
         ↓
Claude Opus reviewt (Jules-Prompt) → Verdict
         ↓
Approved → claude-approved Label + ggf. Auto-Merge
Revision → @jules Mention im Comment → Jules iteriert


FLOW B: Agent erstellt PR direkt → Claude
────────────────────────────────────────────────────────
SEO-Agent oder Codex-Session macht sicheren Fix
         ↓
Erstellt PR direkt (ohne Jules-Umweg)
         ↓
GitHub Webhook → unser Bot detectet Agent-Typ
         ↓
Claude reviewt mit Agent-spezifischem Prompt (SEO-Prompt bzw. Codex-Prompt)
         ↓
Approved → Label + ggf. Auto-Merge (trivial Whitelist)
Revision → Comment (kein @jules, weil kein Jules zuständig)
         ↓
User merged manuell wenn nicht auto


FLOW C: Jules-Suggestions-Poller
────────────────────────────────────────────────────────
3x/Tag: Jules-API GET /sessions → nein, GET suggestions pro Repo
         ↓
Für jede Suggestion (oder Subset):
  Prüfe Limits: concurrent < 15 AND started_24h < 100
  Wenn OK: POST /sessions → Jules startet Session
  Sonst: in Queue, retry alle 60s
         ↓
Jules öffnet PR → weiter wie Flow A
```

**Gemeinsamer Endpunkt: Claude reviewt, du behältst Merge-Hoheit** (außer bei Auto-Merge-Whitelist).

---

## 4. Komponenten

### 4.1 Neue Dateien

Alle unter `src/integrations/github_integration/agent_review/`:

| Datei | Zweck | Umfang |
|---|---|---|
| `__init__.py` | Package-Init | klein |
| `adapters/base.py` | `AgentAdapter` ABC + `AgentDetection` | ~60 Zeilen |
| `adapters/jules.py` | `JulesAdapter` — wrap existierende Jules-Logik | ~120 Zeilen |
| `adapters/seo.py` | `SeoAdapter` — SEO/GSC/GEO/AEO-Review | ~150 Zeilen |
| `adapters/codex.py` | `CodexAdapter` — Code-Quality Review | ~100 Zeilen |
| `prompts/seo_prompt.py` | SEO-Review-Prompt-Builder | ~120 Zeilen |
| `prompts/codex_prompt.py` | Codex-Review-Prompt-Builder | ~100 Zeilen |
| `detector.py` | Adapter-Dispatcher (confidence-basiert) | ~50 Zeilen |
| `merge_policy.py` | Whitelist-basierte Auto-Merge-Engine | ~80 Zeilen |
| `queue.py` | Jules-Session-Queue (nur Queue 1) | ~120 Zeilen |
| `suggestions_poller.py` | Jules API Top-Suggestions Auto-Start | ~100 Zeilen |
| `outcome_tracker.py` | 24h-Post-Merge-Monitoring | ~80 Zeilen |
| `daily_digest.py` | Scheduled Task für Discord-Digest | ~100 Zeilen |

**Total neu: ~1180 Zeilen** verteilt auf 13 Dateien.

### 4.2 Modifikationen bestehender Dateien

| Datei | Änderung |
|---|---|
| `jules_workflow_mixin.py` | Ersetze Jules-only Detection durch `detector.detect_agent(pr)` — Dispatcher an Adapter |
| `jules_workflow_mixin.py` | In `_jules_apply_approval`: vor Label-Setzen → `merge_policy.evaluate()` → ggf. Auto-Merge via `gh pr merge` |
| `ai_engine.py` | Neue Methode `review_pr()` akzeptiert `prompt_template` Parameter (derzeit hardcoded) |
| `~/agents/projects/seo/` | **Optional**: Prüfen ob SEO-Agent bei unsicheren Fixes Issues erstellt. Falls nicht: kleine Ergänzung (~10 Zeilen) |
| `config/config.example.yaml` | Neuer `agent_review:` Block (ersetzt `jules_workflow:`) |

### 4.3 Was NICHT angefasst wird

- `security_engine/scan_agent.py` — bleibt wie er ist
- `~/agents/projects/seo/` (außer optional minimal) — bleibt
- Bestehende 7 Jules-Module inkl. `jules_state.py`, `jules_gates.py`, `jules_comment.py` — bleiben
- 106 Jules-Tests — bleiben grün

---

## 5. Adapter-Pattern im Detail

### 5.1 `AgentAdapter` Base-Klasse

```python
# adapters/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

@dataclass
class AgentDetection:
    matched: bool
    confidence: float           # 0.0 - 1.0
    metadata: dict = None

class AgentAdapter(ABC):
    @property
    @abstractmethod
    def agent_name(self) -> str: ...

    @abstractmethod
    def detect(self, pr_payload: dict) -> AgentDetection: ...

    @abstractmethod
    def build_prompt(
        self, *, diff: str, pr_payload: dict, finding_context: dict,
        iteration: int, few_shot: list, knowledge: list, project: str,
    ) -> str: ...

    @abstractmethod
    def model_preference(self, pr_payload: dict, diff_len: int) -> tuple[str, str]:
        """Returns (primary, fallback) model classes."""

    @abstractmethod
    def merge_policy(self, review: dict, pr_payload: dict, project: str) -> 'MergeDecision': ...

    @abstractmethod
    def discord_channel(self, verdict: str) -> str: ...

    def iteration_mention(self) -> Optional[str]:
        return None
```

### 5.2 Drei konkrete Adapter

**JulesAdapter:** Wrap der bestehenden Jules-Detection und Prompt-Logik. Model-Preference wie bisher (Security → Opus, rest → Sonnet). Merge-Policy nur für Tests-only Whitelist. `@google-labs-jules` Mention für Revisions.

**SeoAdapter:** Detect via Body-Marker `## 🔍 SEO Audit`, Branch `seo/`, Title-Prefix `[SEO]`. SEO-Prompt mit Fokus auf Content-Scope, Metadata-Korrektheit, Canonical-Tags, Sitemap-Konsistenz, GSC-Konformität, GEO/AEO-Überlegungen. Standard-Model Sonnet (SEO ist meist nicht komplex). Merge-Policy erlaubt Auto-Merge nur für reine Content-Files + `in_scope=true`.

**CodexAdapter:** Detect via Body-Marker `## Summary`, `This PR addresses`, `Finding #`. Codex-Prompt fokussiert auf Code-Quality + Security-Fix-Verifikation + Test-Coverage. Merge-Policy immer MANUAL (Codex-PRs sind meist Security-Fixes → Human-Review Pflicht).

### 5.3 Detector

```python
# detector.py
class AgentDetector:
    def __init__(self, adapters: list[AgentAdapter]):
        # Reihenfolge: Jules (specific), SEO, Codex (generic)
        self.adapters = adapters

    def detect(self, pr_payload: dict) -> Optional[AgentAdapter]:
        matches = [
            (adapter.detect(pr_payload).confidence, adapter)
            for adapter in self.adapters
            if adapter.detect(pr_payload).matched
        ]
        matches = [m for m in matches if m[0] >= 0.8]
        if not matches:
            return None
        return max(matches, key=lambda x: x[0])[1]
```

Confidence-basiertes Matching verhindert False-Positives bei mehrdeutigen PRs (z.B. ein SEO-PR mit `## Summary` Body).

---

## 6. Projekt-Spezifische Behandlung

Jedes Projekt bekommt seine eigene Review-Behandlung:

### 6.1 Projekt-Knowledge im Prompt

Bestehender `JulesLearning.fetch_project_knowledge()` lädt pro Projekt aus `agent_knowledge`. Alle Adapter nutzen das.

### 6.2 Few-Shot-Examples pro Projekt

Bestehende `jules_review_examples` Tabelle hat `project` Spalte. Few-Shot-Loader filtert.

### 6.3 Merge-Policy pro Projekt

```python
PROJECT_AUTO_MERGE_POLICIES = {
    "ZERODOX":             {"allowed": True,  "trivial_threshold": 100},
    "GuildScout":          {"allowed": True,  "trivial_threshold": 150},
    "mayday-sim":          {"allowed": True,  "trivial_threshold": 500},
    "shadowops-bot":       {"allowed": True,  "trivial_threshold": 50},
    "sicherheitsdienst":   {"allowed": False},
    "ai-agent-framework":  {"allowed": True,  "trivial_threshold": 100},
}
```

### 6.4 Discord Review-Embed zeigt Projekt

Embed-Titel: `"✅ Jules PR #154 — ZERODOX — APPROVED"` mit Repo prominent sichtbar.

---

## 7. Queue-System (nur für Jules-Session-Start)

### 7.1 Warum nur eine Queue

**Eine Queue** existiert nur für `POST /sessions` Calls an Jules — weil Jules externe hard-limits hat:
- 100 neue Sessions pro rollierende 24h (Jules Mittel-Plan)
- 15 concurrent Sessions (Jules hard limit)

**Keine Queue für Claude-Reviews** — Claude-CLI läuft lokal, bei Bedarf erweitern wir den `claude_cli_concurrent_cap` (aktuell 8 aus empirischen Daten vom 2026-04-13).

**Keine Queue für Server-Agents** — SEO und Security laufen selbstständig, output = Issues/PRs die wir reviewen.

### 7.2 DB-Schema

```sql
-- security_analyst.agent_task_queue
CREATE TABLE IF NOT EXISTS agent_task_queue (
    id              BIGSERIAL PRIMARY KEY,
    source          TEXT NOT NULL,          -- 'jules_suggestion'|'scan_agent'|'manual'
    priority        INTEGER NOT NULL,        -- 0 (urgent) - 4 (trivial)
    payload         JSONB NOT NULL,          -- {repo, prompt, title}
    project         TEXT,                    -- für Filter/Digest
    scheduled_for   TIMESTAMPTZ NOT NULL DEFAULT now(),
    released_at     TIMESTAMPTZ,
    released_as     TEXT,                    -- jules_session_id nach Release
    status          TEXT NOT NULL DEFAULT 'queued'
                    CHECK (status IN ('queued','released','failed','cancelled')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_queue_next ON agent_task_queue(scheduled_for, priority)
    WHERE status = 'queued';
```

### 7.3 Scheduler

```python
# queue.py
class JulesTaskQueue:
    async def try_release(self) -> int:
        """Läuft alle 60s. Released Tasks wenn Limits es erlauben."""
        concurrent = await jules_api.count_concurrent_sessions()
        started_24h = await self._count_started_last_24h()
        
        budget = min(15 - concurrent, 100 - started_24h)
        if budget <= 0:
            return 0
        
        tasks = await self._get_queued_tasks(limit=budget)
        released = 0
        for task in tasks:
            try:
                session_id = await jules_api.create_session(task.payload)
                await self._mark_released(task.id, session_id)
                released += 1
            except RateLimitError:
                break  # Queue bleibt, retry beim nächsten Tick
        return released
```

### 7.4 Revisions-Iterationen zählen nicht

Wenn Claude Revision meldet und Jules iteriert:
- Jules arbeitet in **bestehender Session** weiter
- Kein neuer `POST /sessions` Call
- Verbraucht **nichts** vom 100/24h-Budget
- Bestehender `iteration_count: 5` Cap im Mixin schützt vor Loops

---

## 8. Auto-Merge-Whitelist

### 8.1 Rule-Engine

```python
# merge_policy.py
class MergeDecision(Enum):
    AUTO = "auto"
    MANUAL = "manual"
    BLOCKED = "blocked"

@dataclass
class MergeRule:
    name: str
    predicate: callable
    decision: MergeDecision

    def evaluate(self, pr, review, project) -> Optional[MergeDecision]:
        try:
            if self.predicate(pr, review, project):
                return self.decision
        except Exception:
            return None
        return None
```

### 8.2 Rules pro Adapter

Implementiert in jedem Adapter als `merge_policy(review, pr, project) -> MergeDecision`.

**JulesAdapter:**
1. Projekt `allow_auto_merge=False` → MANUAL
2. Body enthält "BREAKING CHANGE" → MANUAL
3. Nur Tests-Files + `<200 Zeilen` + approved → AUTO
4. Nur Logger-Refactor + approved → AUTO
5. Sonst MANUAL

**SeoAdapter:**
1. Projekt blocked → MANUAL
2. Scope-Check `in_scope=false` → MANUAL
3. `>50 Files` → MANUAL (zu groß)
4. Touches Build-Configs (`package.json`, `next.config.*`, `tsconfig.*`) → MANUAL
5. Nur Content-Files + approved → AUTO
6. Nur Metadata/Sitemap + approved → AUTO
7. Sonst MANUAL

**CodexAdapter:** Immer MANUAL (Security-Kontext).

### 8.3 Auto-Merge Execution

Nach Claude-Approval:
```python
decision = adapter.merge_policy(review, pr_payload, project=repo)
if decision == MergeDecision.AUTO:
    await self._gh_auto_merge(owner, repo, pr_number, method="squash")
    await self.outcome_tracker.record_auto_merge(pr_id, rule_matched)
    # 24h später: check revert + CI + incident
```

---

## 9. Learning-Loop

### 9.1 Was wird getracked

Erweitert existierende `agent_learning` DB:

**Neue Tabelle `auto_merge_outcomes`:**
```sql
CREATE TABLE IF NOT EXISTS auto_merge_outcomes (
    id              BIGSERIAL PRIMARY KEY,
    agent_type      TEXT NOT NULL,
    project         TEXT NOT NULL,
    pr_number       INTEGER NOT NULL,
    rule_matched    TEXT NOT NULL,
    merged_at       TIMESTAMPTZ NOT NULL,
    -- 24h-Outcome-Check
    reverted                   BOOLEAN DEFAULT false,
    reverted_at                TIMESTAMPTZ,
    ci_passed_after_merge      BOOLEAN,
    deployed_without_incident  BOOLEAN,
    follow_up_fix_needed       BOOLEAN DEFAULT false,
    created_at                 TIMESTAMPTZ DEFAULT now()
);
```

**Bestehend erweitert:** `jules_review_examples` wird von allen 3 Adaptern befüllt (nicht nur Jules).

### 9.2 Outcome-Tracker

Scheduled Task 24h nach Auto-Merge:
- Prüft Git-History auf Revert-Commit
- Prüft CI-Status des Main-Branches danach
- Prüft Discord-Alarm-Kanäle auf Incident-Reports
- Prüft auf Follow-up-Fix-PRs mit ähnlichen Files

### 9.3 Auto-Promotion der Whitelist

Wöchentlich:
```sql
SELECT agent_type, rule_matched, project,
       COUNT(*) as total,
       AVG(CASE WHEN reverted OR follow_up_fix_needed THEN 0 ELSE 1 END) as success_rate
FROM auto_merge_outcomes
WHERE merged_at > now() - interval '14 days'
GROUP BY 1, 2, 3
HAVING COUNT(*) >= 5 AND success_rate >= 0.95;
```

→ Bot postet in `🧠-ai-learning`:
> *"Rule `seo/content-only` auf ZERODOX hat 15/15 saubere Auto-Merges (100%). Soll sie auf `additions < 500` erweitert werden?"*

Shadow reagiert mit ✅ Reaction → Bot updated die Rule-Schwellwerte in der Config.

### 9.4 Feedback fließt in Prompts

Wenn eine Rule öfter zu Reverts führt:
- In `agent_knowledge` wird projekt-spezifische Warnung geschrieben
- Nächster Review für dieses Projekt bekommt die Warnung im Prompt
- Beispiel: *"ZERODOX Rule `content-only` hatte 2 Reverts letzte Woche — prüfe bei Content-Changes ob Routing/Sitemap-Updates mitgehen"*

---

## 10. Discord-Strategie

### 10.1 Channel-Mapping

| Event | Channel | Grund |
|---|---|---|
| Jules Security-Review | `🔧-code-fixes` | bestehender Channel dafür |
| SEO-Agent PR-Review | `seo-fixes` | bestehender Channel dafür |
| Codex Code-Health Review | `🤖-agent-reviews` | neu erstellt 2026-04-14 |
| Escalation (max_iterations, timeout) | `✋-approvals` | bestehender Human-Approval-Channel |
| Daily Digest | `🧠-ai-learning` | bestehender AI-Learning-Channel |

### 10.2 Review-Embeds

Pro Review ein sauberes Embed (kein Text-Spam):

```
┌─────────────────────────────────────────┐
│ ✅ Jules PR #154 — ZERODOX — APPROVED   │
├─────────────────────────────────────────┤
│ Repo:        ZERODOX                     │
│ Iteration:   1 / 5                       │
│ Findings:    🔴 0  🟡 1  ⚪ 1            │
│ Auto-Merge:  ✅ Tests-only Rule greift  │
│ 🔗 https://github.com/.../pull/154       │
└─────────────────────────────────────────┘
```

Farbkodiert:
- Grün: APPROVED + Auto-Merge
- Blau: APPROVED + Manual-Merge-Wait
- Gelb: REVISION_REQUESTED
- Rot: ESCALATED

### 10.3 Daily Digest (08:15 in `🧠-ai-learning`)

```markdown
### 🤖 Agent-Review Daily — 2026-04-15

**Reviews letzte 24h:** 23
- 🤖 14 Jules · 🔍 6 SEO · 🛠️ 3 Codex
- ✅ 19 approved · 🔴 3 revision · 🚨 1 escalated

**Auto-Merges:** 8
- 5× SEO content-only (ZERODOX)
- 3× Jules tests-only (shadowops-bot, GuildScout)
- 0 Reverts in letzten 24h

**Queue-Status:**
- Jules: 3/15 concurrent · 23/100 daily budget
- 0 Tasks wartend

**Offen für dich:**
- ZERODOX #170 Jules approved → warten auf Merge
- GuildScout #120 Codex approved → warten auf Merge

**Trends (7 Tage):**
- Approval-Rate: 85% ↑
- Auto-Merge-Success: 12/12 (100%) ✨
- Candidate for whitelist expansion: `seo/content-only` → `< 500 Zeilen`
```

---

## 11. Error-Handling

| Fehler | Reaktion |
|---|---|
| Jules API 429/rate-limit | Task bleibt in Queue, exponential backoff (60s/5min/15min) |
| Jules Session FAILED | Task auf `failed`, Discord-Alert in `✋-approvals` |
| Claude empty/invalid | Bestehender Fallback Opus→Sonnet, bei beidem Fehler → Escalation |
| Schema-Validation fail | Bestehender Parser extrahiert JSON-Block, sonst Escalation |
| Auto-Merge CI-Fail | PR bleibt offen, `follow_up_fix_needed=true`, Discord-Ping |
| Queue-Row orphaned | Stündlicher Cleanup-Job |
| Adapter detect() crash | Try/Except, nächster Adapter versucht |
| Discord-Ausfall | Log-only, Review läuft trotzdem durch |
| Webhook-Crash | HTTP 200 zurück + interner Error-Log (kein Redelivery) |

---

## 12. Rollout-Plan

**Phase 0: Groundwork (Tag 1, 2h)**
- DB-Migration: neue Tabellen `agent_task_queue`, `auto_merge_outcomes`
- `jules_pr_reviews` erweitert: `agent_type` Spalte (default 'jules' für existing rows)
- Config-Block `agent_review:` in `config.example.yaml`
- Design-Doc committen

**Phase 1: Adapter-Extraktion (Tag 2, 4h)**
- `AgentAdapter` ABC
- `JulesAdapter` — wrapped existierenden Jules-Code, keine Logik-Änderung
- `AgentDetector` mit nur Jules als Adapter
- Refactor `jules_workflow_mixin.py` — Dispatcher statt Hardcoded-Jules
- **Regression:** Alle 106 Jules-Tests + Live-Betrieb unverändert

**Phase 2: SEO- + Codex-Adapter (Tag 3, 5h)**
- `SeoAdapter` mit SEO-spezifischem Prompt
- `CodexAdapter` mit Codex-Prompt
- Tests für Detect-Logik
- Noch nicht live aktiviert

**Phase 3: Jules-Suggestions-Poller (Tag 4, 3h)**
- `suggestions_poller.py` mit Jules API
- Queue-System (`agent_task_queue`)
- Scheduler alle 60s
- Tests inkl. Rate-Limit-Szenarien

**Phase 4: Auto-Merge + Outcome-Tracker (Tag 5, 4h)**
- `merge_policy.py`
- Auto-Merge-Execution
- `outcome_tracker.py`

**Phase 5: Daily-Digest + Discord-Embeds (Tag 6, 3h)**
- `daily_digest.py` Scheduled Task
- Review-Embed-Formatter

**Phase 6: Live-Aktivierung (Tag 7 ff.)**
- Tag 1: Nur JulesAdapter aktiv (= wie vorher, Regression-Test)
- Tag 3: SEO-Adapter aktiv (erste echte SEO-Reviews)
- Tag 7: Codex-Adapter + Auto-Merge für Trivial-Whitelist
- Tag 14: Auto-Promotion-Workflow aktiv

**Rollback in 30 Sekunden:** Config `agent_review.enabled: false` → Restart.

---

## 13. Testing-Strategie

- **Unit-Tests (~40 neu)**: Pro Adapter (detect, prompt, merge_policy), Queue, Merge-Policy, Outcome-Tracker
- **Integration-Test (1 pro Flow)**:
  1. SEO-Agent PR → Claude reviewt → Auto-Merge content-only
  2. Jules-Suggestion Queue → Release → PR → Review
  3. ScanAgent Issue → Jules PR → Claude Revision → Iteration
  4. Rate-Limit-Overflow → Queue → Retry
- **Regression**: `test_jules_pr123_regression.py` bleibt grün
- **Dry-Run-Mode**: Config `agent_review.dry_run: true` loggt Actions ohne auszuführen

---

## 14. Bestätigte Entscheidungen

| Frage | Entscheidung |
|---|---|
| Scope | Alle AI-Agent-PRs (Jules, SEO, Codex) — Dependabot raus |
| Prompts | Per-Agent spezialisiert (3 Varianten) |
| Jules-Suggestions | Auto-Start aller Suggestions (respektiert Limits) |
| Merge-Strategie | Whitelist-basierter Auto-Merge (trivial) |
| Discord | 4 bestehende Channels + 1 neuer + Daily Digest |
| Jules-Limits | 100/24h + 15 concurrent (Mittel-Plan) |
| Revisions | Zählen nicht gegen Limits (bestehende Session) |
| Projekt-Spezifisch | Knowledge, Examples, Merge-Policy alle per-project |
| Server-Agents | Unberührt, nur deren PRs werden reviewt |
| Refactoring-Risiko | Minimiert durch Adapter-Wrapping |

---

## 15. Referenzen

- Bestehender Jules-Workflow: `docs/design/jules-workflow.md`
- Operational Runbook: `docs/guides/JULES_RUNBOOK.md`
- ADR-007: `docs/adr/007-jules-secops-workflow.md`
- PR #123 Incident: Jules Design-Doc Anhang A
