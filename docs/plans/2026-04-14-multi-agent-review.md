# Multi-Agent Review Pipeline Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Erweitere den bestehenden Jules-Workflow um Adapter-Pattern für SEO/Codex-PR-Reviews, Jules-Suggestions-Auto-Start, Queue für Jules-Limits, projekt-spezifisches Auto-Merge und Daily-Digest — ohne bestehende Server-Agents oder die 7 Jules-Module anzufassen.

**Design Reference:** [`docs/plans/2026-04-14-multi-agent-review-design.md`](./2026-04-14-multi-agent-review-design.md)

**Architecture:** Adapter-Pattern als additive Erweiterung. `JulesAdapter` wrapt existierenden Jules-Code 1:1 (keine Regression). Neue `SeoAdapter` + `CodexAdapter`. Detector-Dispatcher ersetzt hardcoded Jules-Detection im Mixin. Queue nur für Jules-Session-Start (100/24h, 15 concurrent). Auto-Merge via Rule-Engine, Outcome-Tracker lernt aus Ergebnissen.

**Tech Stack:** Python 3.12, asyncpg, aiohttp, Claude CLI (Opus+Sonnet), Jules API v1alpha, PostgreSQL, Redis, pytest.

**Execution Notes:**
- VPS 8 GB — Tests einzeln: `pytest tests/unit/test_X.py -x`. NIE `pytest tests/`.
- Bot nicht restarten bis Phase 6. Bestehender Jules-Workflow muss durchgehend laufen.
- Nach jeder Task: `git add <files> && git commit`. Commit-Hook validiert Conventional Commits.
- Config-Kill-Switch: `agent_review.enabled: false` → Rollback in 30s.
- Referenzen beim Implementieren:
  - Design-Doc `docs/plans/2026-04-14-multi-agent-review-design.md`
  - Bestehend: `src/integrations/github_integration/jules_*.py`
  - Tests-Muster: `tests/unit/test_jules_*.py`

---

## Phase 0: Groundwork (DB + Config)

### Task 0.1: DB-Migration — `agent_task_queue` Tabelle

**Files:**
- Create: `src/integrations/github_integration/agent_review_schema.sql`

**Step 1: Schema schreiben**

```sql
-- Multi-Agent Review Pipeline — Queue + Outcome Tracking
-- Siehe docs/plans/2026-04-14-multi-agent-review-design.md §7, §9

-- Queue für Jules-Session-Starts (nur POST /sessions)
CREATE TABLE IF NOT EXISTS agent_task_queue (
    id              BIGSERIAL PRIMARY KEY,
    source          TEXT NOT NULL,                     -- 'jules_suggestion'|'scan_agent'|'manual'
    priority        INTEGER NOT NULL CHECK (priority BETWEEN 0 AND 4),
    payload         JSONB NOT NULL,                    -- {repo, prompt, title, ...}
    project         TEXT,
    scheduled_for   TIMESTAMPTZ NOT NULL DEFAULT now(),
    released_at     TIMESTAMPTZ,
    released_as     TEXT,                              -- jules session_id nach Release
    failure_reason  TEXT,
    retry_count     INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'queued'
                    CHECK (status IN ('queued','released','failed','cancelled')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_queue_next
    ON agent_task_queue(scheduled_for ASC, priority ASC)
    WHERE status = 'queued';

-- Outcome-Tracking für Auto-Merges
CREATE TABLE IF NOT EXISTS auto_merge_outcomes (
    id              BIGSERIAL PRIMARY KEY,
    agent_type      TEXT NOT NULL,
    project         TEXT NOT NULL,
    repo            TEXT NOT NULL,
    pr_number       INTEGER NOT NULL,
    rule_matched    TEXT NOT NULL,
    merged_at       TIMESTAMPTZ NOT NULL,
    -- 24h-Check (nachtrag befüllt)
    reverted                   BOOLEAN NOT NULL DEFAULT false,
    reverted_at                TIMESTAMPTZ,
    ci_passed_after_merge      BOOLEAN,
    deployed_without_incident  BOOLEAN,
    follow_up_fix_needed       BOOLEAN NOT NULL DEFAULT false,
    checked_at                 TIMESTAMPTZ,
    created_at                 TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ame_agent_rule ON auto_merge_outcomes(agent_type, rule_matched);
CREATE INDEX IF NOT EXISTS idx_ame_pending_check
    ON auto_merge_outcomes(merged_at)
    WHERE checked_at IS NULL;

-- Bestehende jules_pr_reviews Tabelle um agent_type erweitern
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'jules_pr_reviews' AND column_name = 'agent_type'
    ) THEN
        ALTER TABLE jules_pr_reviews ADD COLUMN agent_type TEXT NOT NULL DEFAULT 'jules';
        CREATE INDEX idx_jpr_agent_type ON jules_pr_reviews(agent_type);
    END IF;
END $$;
```

**Step 2: Schema live anwenden**

```bash
cd /home/cmdshadow/shadowops-bot
.venv/bin/python -c "
import psycopg2, sys; sys.path.insert(0, 'src')
from utils.config import Config
conn = psycopg2.connect(Config().security_analyst_dsn)
conn.autocommit = True
cur = conn.cursor()
with open('src/integrations/github_integration/agent_review_schema.sql') as f:
    cur.execute(f.read())
cur.execute(\"SELECT table_name FROM information_schema.tables WHERE table_name IN ('agent_task_queue','auto_merge_outcomes')\")
print('Tables created:', [r[0] for r in cur.fetchall()])
cur.execute(\"SELECT column_name FROM information_schema.columns WHERE table_name='jules_pr_reviews' AND column_name='agent_type'\")
print('agent_type column:', cur.fetchall())
"
```

Expected: `Tables created: ['agent_task_queue', 'auto_merge_outcomes']` + `agent_type column: [('agent_type',)]`.

**Step 3: Commit**

```bash
git add src/integrations/github_integration/agent_review_schema.sql
git commit -m "feat: agent_review Schema — Queue + Outcomes + agent_type Spalte"
```

---

### Task 0.2: Config-Block `agent_review` hinzufügen

**Files:**
- Modify: `config/config.example.yaml`

**Step 1: Block am Ende anhängen**

```yaml
agent_review:
  enabled: false                       # Master-Switch, default off bis Phase 6
  dry_run: false

  # Queue für Jules-Session-Starts (einzige Queue im System)
  jules_queue:
    max_new_sessions_per_24h: 100
    max_concurrent_sessions: 15
    retry_interval_seconds: 60
    scheduler_interval_seconds: 60

  # Claude-Review Capacity (kein Queue, nur Cap)
  claude_review:
    max_concurrent_calls: 8

  # Jules Suggestions Poller
  suggestions_poller:
    enabled: false                     # separat aktivierbar
    interval_hours: 8                  # 3x taeglich (08:00, 16:00, 00:00)
    repos:
      - "Commandershadow9/ZERODOX"
      - "Commandershadow9/GuildScout"
      - "Commandershadow9/shadowops-bot"
      - "Commandershadow9/ai-agent-framework"
      - "Commandershadow9/mayday-sim"

  # Auto-Merge Policies per Projekt
  auto_merge:
    enabled: false                     # separat aktivierbar
    default_method: "squash"
    projects:
      ZERODOX:            { allowed: true,  trivial_threshold: 100 }
      GuildScout:         { allowed: true,  trivial_threshold: 150 }
      mayday-sim:         { allowed: true,  trivial_threshold: 500 }
      shadowops-bot:      { allowed: true,  trivial_threshold: 50 }
      sicherheitsdienst:  { allowed: false }
      ai-agent-framework: { allowed: true,  trivial_threshold: 100 }

  # Discord Channels (alle existierend außer agent_reviews)
  discord:
    jules_reviews:     "🔧-code-fixes"
    seo_reviews:       "seo-fixes"
    codex_reviews:     "🤖-agent-reviews"
    escalations:       "✋-approvals"
    daily_digest:      "🧠-ai-learning"
    daily_digest_hour: 8
    daily_digest_minute: 15

  # Adapter-Toggle
  adapters:
    jules: true                        # Phase 1
    seo: false                         # Phase 2
    codex: false                       # Phase 2
```

**Step 2: Config-Loader-Access verifizieren**

```bash
.venv/bin/python -c "
import sys; sys.path.insert(0, 'src')
from utils.config import Config
c = Config()
ar = c._config.get('agent_review', {})
print('enabled:', ar.get('enabled'))
print('jules queue limits:', ar.get('jules_queue'))
print('adapters:', ar.get('adapters'))
"
```

Expected: Alle drei Werte geprintet, keine KeyErrors.

**Step 3: Commit**

```bash
git add config/config.example.yaml
git commit -m "feat: agent_review Config-Block (disabled default)"
```

---

## Phase 1: Adapter-Struktur + JulesAdapter

### Task 1.1: `AgentAdapter` Base-Klasse

**Files:**
- Create: `src/integrations/github_integration/agent_review/__init__.py`
- Create: `src/integrations/github_integration/agent_review/adapters/__init__.py`
- Create: `src/integrations/github_integration/agent_review/adapters/base.py`
- Create: `tests/unit/agent_review/__init__.py`
- Create: `tests/unit/agent_review/test_adapter_base.py`

**Step 1: Failing Tests schreiben**

```python
# tests/unit/agent_review/test_adapter_base.py
import pytest
from src.integrations.github_integration.agent_review.adapters.base import (
    AgentAdapter, AgentDetection, MergeDecision,
)


def test_agent_detection_defaults():
    d = AgentDetection(matched=False, confidence=0.0)
    assert d.matched is False
    assert d.confidence == 0.0
    assert d.metadata is None


def test_agent_detection_with_metadata():
    d = AgentDetection(matched=True, confidence=0.9, metadata={"key": "val"})
    assert d.metadata == {"key": "val"}


def test_merge_decision_enum_values():
    assert MergeDecision.AUTO.value == "auto"
    assert MergeDecision.MANUAL.value == "manual"
    assert MergeDecision.BLOCKED.value == "blocked"


def test_agent_adapter_is_abstract():
    with pytest.raises(TypeError):
        AgentAdapter()  # ABC, cannot instantiate
```

**Step 2: Tests ausführen — FAIL erwartet**

```bash
.venv/bin/python -m pytest tests/unit/agent_review/test_adapter_base.py -x -v
```

Expected: ImportError (base.py existiert noch nicht).

**Step 3: Implementation**

```python
# src/integrations/github_integration/agent_review/__init__.py
"""Multi-Agent PR-Review Pipeline."""
```

```python
# src/integrations/github_integration/agent_review/adapters/__init__.py
from .base import AgentAdapter, AgentDetection, MergeDecision

__all__ = ["AgentAdapter", "AgentDetection", "MergeDecision"]
```

```python
# src/integrations/github_integration/agent_review/adapters/base.py
"""AgentAdapter Base-Klasse + Datentypen."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class MergeDecision(Enum):
    AUTO = "auto"
    MANUAL = "manual"
    BLOCKED = "blocked"


@dataclass
class AgentDetection:
    matched: bool
    confidence: float           # 0.0 - 1.0
    metadata: Optional[Dict[str, Any]] = None


class AgentAdapter(ABC):
    """Base-Klasse fuer Agent-spezifische Review-Logik."""

    @property
    @abstractmethod
    def agent_name(self) -> str:
        """z.B. 'jules', 'seo', 'codex'"""

    @abstractmethod
    def detect(self, pr_payload: Dict[str, Any]) -> AgentDetection:
        """Prueft ob dieser Adapter den PR verarbeiten soll."""

    @abstractmethod
    def build_prompt(
        self, *,
        diff: str,
        pr_payload: Dict[str, Any],
        finding_context: Dict[str, Any],
        iteration: int,
        few_shot: List[Dict[str, Any]],
        knowledge: List[str],
        project: str,
    ) -> str:
        """Baut den Claude-Prompt fuer diesen Agent-Typ."""

    @abstractmethod
    def model_preference(
        self, pr_payload: Dict[str, Any], diff_len: int,
    ) -> Tuple[str, str]:
        """Returns (primary_model_class, fallback_model_class)."""

    @abstractmethod
    def merge_policy(
        self, review: Dict[str, Any], pr_payload: Dict[str, Any], project: str,
    ) -> MergeDecision:
        """Entscheidet ueber Auto-Merge nach Claude-Approval."""

    @abstractmethod
    def discord_channel(self, verdict: str) -> str:
        """Discord-Channel fuer Review-Embed."""

    def iteration_mention(self) -> Optional[str]:
        """Optional: @mention fuer Revision-Comments."""
        return None
```

**Step 4: Tests PASS**

```bash
.venv/bin/python -m pytest tests/unit/agent_review/test_adapter_base.py -x -v
```

Expected: 4 passed.

**Step 5: Commit**

```bash
git add src/integrations/github_integration/agent_review/ tests/unit/agent_review/
git commit -m "feat: AgentAdapter ABC + AgentDetection + MergeDecision"
```

---

### Task 1.2: `JulesAdapter` — wrappt existierenden Jules-Code

**Files:**
- Create: `src/integrations/github_integration/agent_review/adapters/jules.py`
- Create: `tests/unit/agent_review/test_jules_adapter.py`

**Step 1: Tests schreiben**

```python
# tests/unit/agent_review/test_jules_adapter.py
import pytest
from src.integrations.github_integration.agent_review.adapters.jules import JulesAdapter
from src.integrations.github_integration.agent_review.adapters.base import MergeDecision


@pytest.fixture
def adapter():
    return JulesAdapter()


def test_agent_name(adapter):
    assert adapter.agent_name == "jules"


def test_detect_label(adapter):
    pr = {"labels": [{"name": "jules"}, {"name": "security"}],
          "user": {"login": "Commandershadow9"}, "body": ""}
    d = adapter.detect(pr)
    assert d.matched is True
    assert d.confidence >= 0.9


def test_detect_bot_author(adapter):
    pr = {"labels": [], "user": {"login": "google-labs-jules[bot]"}, "body": ""}
    d = adapter.detect(pr)
    assert d.matched is True
    assert d.confidence >= 0.9


def test_detect_body_marker(adapter):
    pr = {"labels": [], "user": {"login": "Commandershadow9"},
          "body": "Fixes #42\n\n---\n*PR created automatically by Jules for task [1234]...*"}
    d = adapter.detect(pr)
    assert d.matched is True
    assert d.confidence >= 0.8


def test_detect_non_jules(adapter):
    pr = {"labels": [{"name": "seo"}], "user": {"login": "Commandershadow9"},
          "body": "## 🔍 SEO Audit"}
    d = adapter.detect(pr)
    assert d.matched is False


def test_model_preference_security(adapter):
    pr = {"title": "Fix XSS in blog renderer"}
    primary, fallback = adapter.model_preference(pr, diff_len=500)
    assert primary == "thinking"
    assert fallback == "standard"


def test_model_preference_trivial(adapter):
    pr = {"title": "Replace console.log with logger"}
    primary, fallback = adapter.model_preference(pr, diff_len=200)
    assert primary == "standard"
    assert fallback == "thinking"


def test_model_preference_large_diff(adapter):
    pr = {"title": "Refactor"}
    primary, fallback = adapter.model_preference(pr, diff_len=5000)
    assert primary == "thinking"


def test_merge_policy_manual_for_security(adapter):
    review = {"verdict": "approved"}
    pr = {"title": "Security fix", "additions": 50,
          "labels": [{"name": "security"}]}
    assert adapter.merge_policy(review, pr, "ZERODOX") == MergeDecision.MANUAL


def test_merge_policy_auto_for_tests_only(adapter):
    review = {"verdict": "approved"}
    pr = {"title": "Add tests",
          "additions": 150,
          "files_changed_paths": ["tests/unit/test_foo.py", "tests/unit/test_bar.py"]}
    assert adapter.merge_policy(review, pr, "ZERODOX") == MergeDecision.AUTO


def test_merge_policy_manual_when_project_blocked(adapter):
    review = {"verdict": "approved"}
    pr = {"title": "Tests",
          "additions": 10,
          "files_changed_paths": ["tests/test_x.py"]}
    # Manuelle Override — sicherheitsdienst hat allowed=false
    assert adapter.merge_policy(review, pr, "sicherheitsdienst") == MergeDecision.MANUAL


def test_discord_channel(adapter):
    assert adapter.discord_channel("approved") == "🔧-code-fixes"


def test_iteration_mention(adapter):
    assert adapter.iteration_mention() == "@google-labs-jules"
```

**Step 2: Tests FAIL**

```bash
.venv/bin/python -m pytest tests/unit/agent_review/test_jules_adapter.py -x -v
```

**Step 3: Implementation**

```python
# src/integrations/github_integration/agent_review/adapters/jules.py
"""JulesAdapter — wrappt existierenden Jules-Review-Logic."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .base import AgentAdapter, AgentDetection, MergeDecision


SECURITY_KEYWORDS = ("xss", "cve", "injection", "dos", "security", "auth", "csrf", "rce")


class JulesAdapter(AgentAdapter):
    agent_name = "jules"

    def detect(self, pr_payload: Dict[str, Any]) -> AgentDetection:
        labels = [l.get("name", "").lower() for l in pr_payload.get("labels") or []]
        if "jules" in labels:
            return AgentDetection(matched=True, confidence=1.0)

        author = (pr_payload.get("user") or {}).get("login", "").lower()
        if author.startswith("google-labs-jules"):
            return AgentDetection(matched=True, confidence=1.0)

        body = pr_payload.get("body") or ""
        if "PR created automatically by Jules" in body:
            return AgentDetection(matched=True, confidence=0.9)
        if "jules.google.com/task/" in body:
            return AgentDetection(matched=True, confidence=0.85)

        return AgentDetection(matched=False, confidence=0.0)

    def build_prompt(
        self, *, diff, pr_payload, finding_context, iteration, few_shot, knowledge, project,
    ) -> str:
        # Reuse bestehender Jules-Prompt-Builder
        from src.integrations.github_integration.jules_review_prompt import build_review_prompt
        return build_review_prompt(
            finding=finding_context, project=project, diff=diff,
            iteration=iteration, project_knowledge=knowledge,
            few_shot_examples=few_shot,
        )

    def model_preference(self, pr_payload, diff_len) -> Tuple[str, str]:
        title = (pr_payload.get("title") or "").lower()
        is_security = any(k in title for k in SECURITY_KEYWORDS)
        if is_security or diff_len > 3000:
            return ("thinking", "standard")
        return ("standard", "thinking")

    def merge_policy(self, review, pr_payload, project) -> MergeDecision:
        # Gelesen aus config, aber fuer Tests: hardcoded Mapping
        # Tatsaechlich spaeter: von aussen per Config injizierbar
        project_blocked = project == "sicherheitsdienst"
        if project_blocked:
            return MergeDecision.MANUAL

        if review.get("verdict") != "approved":
            return MergeDecision.MANUAL

        # Security-PRs nie auto
        labels = [l.get("name", "").lower() for l in pr_payload.get("labels") or []]
        title = (pr_payload.get("title") or "").lower()
        if "security" in labels or any(k in title for k in SECURITY_KEYWORDS):
            return MergeDecision.MANUAL

        # Tests-only + klein: auto
        paths = pr_payload.get("files_changed_paths") or []
        if paths and all(p.startswith(("tests/", "test/")) for p in paths):
            if pr_payload.get("additions", 0) < 200:
                return MergeDecision.AUTO

        return MergeDecision.MANUAL

    def discord_channel(self, verdict: str) -> str:
        return "🔧-code-fixes"

    def iteration_mention(self) -> Optional[str]:
        return "@google-labs-jules"
```

**Step 4: Tests PASS**

```bash
.venv/bin/python -m pytest tests/unit/agent_review/test_jules_adapter.py -x -v
```

Expected: 13 passed.

**Step 5: Commit**

```bash
git add src/integrations/github_integration/agent_review/adapters/jules.py tests/unit/agent_review/test_jules_adapter.py
git commit -m "feat: JulesAdapter — wrappt Jules-Review-Logic in Adapter-Interface"
```

---

### Task 1.3: `AgentDetector` — Confidence-basierter Dispatcher

**Files:**
- Create: `src/integrations/github_integration/agent_review/detector.py`
- Create: `tests/unit/agent_review/test_detector.py`

**Step 1: Tests**

```python
# tests/unit/agent_review/test_detector.py
import pytest
from src.integrations.github_integration.agent_review.detector import AgentDetector
from src.integrations.github_integration.agent_review.adapters.jules import JulesAdapter


def test_detects_jules():
    d = AgentDetector([JulesAdapter()])
    pr = {"labels": [{"name": "jules"}], "user": {"login": "x"}, "body": ""}
    adapter = d.detect(pr)
    assert adapter is not None
    assert adapter.agent_name == "jules"


def test_returns_none_when_no_match():
    d = AgentDetector([JulesAdapter()])
    pr = {"labels": [], "user": {"login": "dependabot[bot]"}, "body": ""}
    assert d.detect(pr) is None


def test_highest_confidence_wins():
    # Spaeter relevant mit mehreren Adaptern — hier nur Struktur
    from src.integrations.github_integration.agent_review.adapters.base import (
        AgentAdapter, AgentDetection, MergeDecision,
    )

    class AlwaysHighAdapter(AgentAdapter):
        agent_name = "high"
        def detect(self, pr): return AgentDetection(True, 0.95)
        def build_prompt(self, **kw): return ""
        def model_preference(self, pr, diff_len): return ("standard", "thinking")
        def merge_policy(self, review, pr, project): return MergeDecision.MANUAL
        def discord_channel(self, verdict): return "test"

    class MediumAdapter(AgentAdapter):
        agent_name = "medium"
        def detect(self, pr): return AgentDetection(True, 0.85)
        def build_prompt(self, **kw): return ""
        def model_preference(self, pr, diff_len): return ("standard", "thinking")
        def merge_policy(self, review, pr, project): return MergeDecision.MANUAL
        def discord_channel(self, verdict): return "test"

    d = AgentDetector([MediumAdapter(), AlwaysHighAdapter()])
    adapter = d.detect({"labels": []})
    assert adapter.agent_name == "high"


def test_below_threshold_ignored():
    from src.integrations.github_integration.agent_review.adapters.base import (
        AgentAdapter, AgentDetection, MergeDecision,
    )

    class LowConfAdapter(AgentAdapter):
        agent_name = "low"
        def detect(self, pr): return AgentDetection(True, 0.5)
        def build_prompt(self, **kw): return ""
        def model_preference(self, pr, diff_len): return ("standard", "thinking")
        def merge_policy(self, review, pr, project): return MergeDecision.MANUAL
        def discord_channel(self, verdict): return "test"

    d = AgentDetector([LowConfAdapter()])
    assert d.detect({}) is None  # Below 0.8 threshold
```

**Step 2: FAIL → Implementation → PASS**

```python
# src/integrations/github_integration/agent_review/detector.py
"""AgentDetector — waehlt den richtigen Adapter via Confidence-Ranking."""
from __future__ import annotations
from typing import List, Optional

from .adapters.base import AgentAdapter


class AgentDetector:
    """First-match-wins mit Confidence-Schwelle von 0.8."""

    CONFIDENCE_THRESHOLD = 0.8

    def __init__(self, adapters: List[AgentAdapter]):
        self.adapters = adapters

    def detect(self, pr_payload: dict) -> Optional[AgentAdapter]:
        matches = []
        for adapter in self.adapters:
            d = adapter.detect(pr_payload)
            if d.matched and d.confidence >= self.CONFIDENCE_THRESHOLD:
                matches.append((d.confidence, adapter))
        if not matches:
            return None
        matches.sort(key=lambda x: -x[0])
        return matches[0][1]
```

**Step 3: Tests PASS + Commit**

```bash
.venv/bin/python -m pytest tests/unit/agent_review/test_detector.py -x -v
# 4 passed

git add src/integrations/github_integration/agent_review/detector.py tests/unit/agent_review/test_detector.py
git commit -m "feat: AgentDetector mit Confidence-basiertem Dispatcher"
```

---

### Task 1.4: Dispatcher-Integration in `jules_workflow_mixin.py`

**Files:**
- Modify: `src/integrations/github_integration/jules_workflow_mixin.py`
- Modify: `tests/unit/test_jules_workflow_mixin.py` (Regression sicherstellen)

**Step 1: Existing Tests müssen weiter grün sein**

```bash
.venv/bin/python -m pytest tests/unit/test_jules_workflow_mixin.py tests/unit/test_jules_pr123_regression.py -x -v
```

Note: Wenn einer fehlschlägt, Task abbrechen — NICHTS ändern bis sie grün sind.

**Step 2: Mixin-Methode `_detect_adapter` hinzufügen**

In `jules_workflow_mixin.py`, ganz am Anfang der Klasse:

```python
    def _get_agent_detector(self):
        """Lazy init — Adapters aus Config."""
        if getattr(self, '_agent_detector', None) is not None:
            return self._agent_detector
        from .agent_review.detector import AgentDetector
        from .agent_review.adapters.jules import JulesAdapter
        adapters = [JulesAdapter()]
        # Phase 2 wird SeoAdapter + CodexAdapter dazupacken
        self._agent_detector = AgentDetector(adapters)
        return self._agent_detector
```

**Step 3: Existierendes `_jules_is_jules_pr` behalten, aber Detector-Path zusätzlich**

Suche die Stelle in `handle_jules_pr_event` die den PR als "Jules-PR" klassifiziert (`_jules_is_jules_pr`). Füge daneben ein optionales Detector-Routing:

```python
        # Bestehender Check bleibt als Fallback
        is_jules = await self._jules_is_jules_pr(pr, repo)

        # Neuer Adapter-Detector (Phase 1 nur Jules, ergibt gleiche Antwort)
        detector = self._get_agent_detector()
        adapter = detector.detect(pr)

        # Regression-Safety: wenn Detector anders entscheidet, loggen aber Bestandspfad nutzen
        if adapter and adapter.agent_name != "jules":
            logger.warning(f"[agent-review] Detector fand {adapter.agent_name}, aber Phase-1 nur Jules aktiv")
            adapter = None
        if adapter is None and is_jules:
            # Existierende Jules-Logik verwenden (sollte in Phase 1 nie passieren ausser Edge-Cases)
            pass
        if not is_jules:
            return
```

Hinweis: In Phase 1 verändert diese Integration nichts am Verhalten — der Detector liefert für Jules-PRs das gleiche wie `_jules_is_jules_pr`.

**Step 4: Tests grün**

```bash
.venv/bin/python -m pytest tests/unit/test_jules_workflow_mixin.py tests/unit/test_jules_pr123_regression.py -x -v
```

Expected: Alle bestehenden Tests weiter grün.

**Step 5: Commit**

```bash
git add src/integrations/github_integration/jules_workflow_mixin.py
git commit -m "feat: AgentDetector im Mixin integriert (Phase 1, nur Jules aktiv)"
```

---

## Phase 2: SEO + Codex Adapter

### Task 2.1: SEO-Prompt-Template

**Files:**
- Create: `src/integrations/github_integration/agent_review/prompts/__init__.py`
- Create: `src/integrations/github_integration/agent_review/prompts/seo_prompt.py`
- Create: `tests/unit/agent_review/test_seo_prompt.py`

**Step 1: Tests**

```python
# tests/unit/agent_review/test_seo_prompt.py
from src.integrations.github_integration.agent_review.prompts.seo_prompt import (
    build_seo_review_prompt,
)


def test_prompt_has_all_sections():
    p = build_seo_review_prompt(
        diff="diff --git a/x b/x",
        project="ZERODOX",
        iteration=1,
        files_changed=["web/src/content/blog.md"],
        knowledge=["ZERODOX nutzt Prisma"],
        few_shot=[],
    )
    assert "Senior SEO-Reviewer" in p
    assert "ZERODOX" in p
    assert "Iteration: 1" in p
    assert "GSC" in p or "Search Console" in p
    assert "Scope" in p
    assert "blockers" in p
    assert "scope_check" in p


def test_prompt_mentions_geo_aeo():
    p = build_seo_review_prompt(
        diff="", project="X", iteration=1,
        files_changed=[], knowledge=[], few_shot=[],
    )
    # SEO-Agent deckt mehr ab als nur SEO
    assert "GEO" in p or "Local" in p
    assert "AEO" in p or "AI" in p or "Answer" in p


def test_prompt_includes_files_list():
    p = build_seo_review_prompt(
        diff="", project="X", iteration=1,
        files_changed=["web/src/content/blog-1.md", "web/sitemap.ts"],
        knowledge=[], few_shot=[],
    )
    assert "blog-1.md" in p
    assert "sitemap.ts" in p


def test_truncates_long_diff():
    long_diff = "x" * 20000
    p = build_seo_review_prompt(
        diff=long_diff, project="X", iteration=1,
        files_changed=[], knowledge=[], few_shot=[], max_diff_chars=500,
    )
    assert len(p) < 10000
    assert "abgeschnitten" in p or "truncated" in p.lower()
```

**Step 2: FAIL → Implementation**

```python
# src/integrations/github_integration/agent_review/prompts/__init__.py
```

```python
# src/integrations/github_integration/agent_review/prompts/seo_prompt.py
"""SEO-Review Prompt Builder — deckt SEO + GSC + GEO + AEO ab."""
from __future__ import annotations
from typing import Dict, List


def build_seo_review_prompt(
    *,
    diff: str,
    project: str,
    iteration: int,
    files_changed: List[str],
    knowledge: List[str],
    few_shot: List[Dict],
    max_diff_chars: int = 8000,
) -> str:
    diff_short = diff[:max_diff_chars]
    if len(diff) > max_diff_chars:
        diff_short += f"\n\n[... {len(diff) - max_diff_chars} Zeichen abgeschnitten ...]"

    files_block = "\n".join(f"- `{f}`" for f in files_changed[:40]) or "(nicht verfuegbar)"
    knowledge_block = "\n".join(f"- {k}" for k in knowledge) if knowledge else "(keine)"
    examples_block = _format_examples(few_shot)

    return f"""Du bist ein Senior SEO-Reviewer. Deine Aufgabe: pruefe diesen Pull-Request
vom SEO-Agent auf Qualitaet, Scope und Sicherheit.

Der SEO-Agent deckt ab: **SEO, Google Search Console (GSC), GEO (Local Search) und
AEO (Answer Engine Optimization fuer Perplexity/ChatGPT/Claude)**.

- **Projekt:** {project}
- **Iteration:** {iteration} of 5

---

## Geaenderte Dateien ({len(files_changed)})

{files_block}

---

## Projekt-Konventionen

{knowledge_block}

---

## Beispiele

{examples_block}

---

## Diff

```diff
{diff_short}
```

---

## Pruefe:

**SEO-Qualitaet:**
- Meta-Descriptions unique, 120-160 Zeichen?
- Canonical-Tags korrekt?
- Sitemap-Aenderungen konsistent?
- Interne Links sinnvoll, keine Footer-Spam-Pattern?

**GSC-Konformitaet:**
- Strukturierte Daten (Schema.org) korrekt?
- Robots.txt nicht kaputt gemacht?
- Index-Signale nicht widerspruechlich?

**GEO (Local):**
- Falls Local-SEO betroffen: Strukturierte Business-Daten korrekt?

**AEO (AI Engine Optimization):**
- Content fuer AI-Search lesbar strukturiert?
- FAQ/How-To-Schema wo sinnvoll?

**Scope-Sicherheit:**
- NUR Content/Metadata-Aenderungen? (.md, .mdx, .json, blog-data, sitemap, robots)
- KEINE Aenderungen an Build-Configs, package.json, Layout-Komponenten?
- KEINE Datenbank-Schema-Aenderungen?
- Existierende Tests bleiben stabil?

**Groesse:**
- Wie viele Files? Bei >50 File-Aenderungen: als BLOCKER markieren (zu grosser Batch).

---

## Ausgabe

Gib JSON zurueck (kein Fence, kein Extra-Text):

```json
{{
  "verdict": "approved" | "revision_requested",
  "summary": "1-3 Saetze",
  "blockers": [...],
  "suggestions": [...],
  "nits": [...],
  "scope_check": {{
    "in_scope": true|false,
    "explanation": "..."
  }}
}}
```

severity in Issues: critical|high|medium|low
"""


def _format_examples(examples: List[Dict]) -> str:
    if not examples:
        return "(keine Beispiele)"
    return "\n".join(
        f"- [{ex.get('outcome','?')}] {ex.get('diff_summary','')[:80]}"
        for ex in examples[:3]
    )
```

**Step 3: Tests PASS + Commit**

```bash
.venv/bin/python -m pytest tests/unit/agent_review/test_seo_prompt.py -x -v
git add src/integrations/github_integration/agent_review/prompts/ tests/unit/agent_review/test_seo_prompt.py
git commit -m "feat: SEO-Review-Prompt (SEO+GSC+GEO+AEO)"
```

---

### Task 2.2: `SeoAdapter`

**Files:**
- Create: `src/integrations/github_integration/agent_review/adapters/seo.py`
- Create: `tests/unit/agent_review/test_seo_adapter.py`

**Step 1: Tests** (analog zu JulesAdapter-Tests, aber für SEO-Detection-Patterns)

```python
# tests/unit/agent_review/test_seo_adapter.py
import pytest
from src.integrations.github_integration.agent_review.adapters.seo import SeoAdapter
from src.integrations.github_integration.agent_review.adapters.base import MergeDecision


@pytest.fixture
def adapter():
    return SeoAdapter()


def test_agent_name(adapter):
    assert adapter.agent_name == "seo"


def test_detect_audit_body(adapter):
    pr = {"labels": [], "user": {"login": "Commandershadow9"},
          "body": "## 🔍 SEO Audit — Automatische Fixes\n\nWebsite: guildscout",
          "head": {"ref": "main"}, "title": "x"}
    d = adapter.detect(pr)
    assert d.matched is True
    assert d.confidence >= 0.9


def test_detect_branch_pattern(adapter):
    pr = {"labels": [], "user": {"login": "Commandershadow9"},
          "body": "", "head": {"ref": "seo/zerodox/2026-04-14"}, "title": "x"}
    d = adapter.detect(pr)
    assert d.matched is True
    assert d.confidence >= 0.9


def test_detect_title_prefix(adapter):
    pr = {"labels": [], "user": {"login": "x"},
          "body": "", "head": {"ref": "main"},
          "title": "[SEO] Blog-Artikel: c/o adresse"}
    d = adapter.detect(pr)
    assert d.matched is True


def test_detect_seo_title_prefix(adapter):
    pr = {"labels": [], "user": {"login": "x"},
          "body": "", "head": {"ref": "main"},
          "title": "SEO: Automatische Optimierungen fuer zerodox"}
    d = adapter.detect(pr)
    assert d.matched is True


def test_detect_non_seo(adapter):
    pr = {"labels": [], "user": {"login": "x"},
          "body": "This PR fixes a security issue",
          "head": {"ref": "fix/security"},
          "title": "Fix XSS"}
    d = adapter.detect(pr)
    assert d.matched is False


def test_model_preference_sonnet_default(adapter):
    pr = {"title": "[SEO] Blog"}
    primary, _ = adapter.model_preference(pr, diff_len=500)
    assert primary == "standard"


def test_merge_policy_manual_out_of_scope(adapter):
    pr = {"files_changed_paths": ["web/src/content/x.md"], "additions": 50}
    review = {"verdict": "approved", "scope_check": {"in_scope": False}}
    assert adapter.merge_policy(review, pr, "ZERODOX") == MergeDecision.MANUAL


def test_merge_policy_manual_too_many_files(adapter):
    paths = [f"web/src/content/blog-{i}.md" for i in range(60)]
    pr = {"files_changed_paths": paths, "additions": 2000}
    review = {"verdict": "approved", "scope_check": {"in_scope": True}}
    assert adapter.merge_policy(review, pr, "ZERODOX") == MergeDecision.MANUAL


def test_merge_policy_manual_touches_config(adapter):
    pr = {"files_changed_paths": ["web/package.json", "web/src/content/x.md"],
          "additions": 50}
    review = {"verdict": "approved", "scope_check": {"in_scope": True}}
    assert adapter.merge_policy(review, pr, "ZERODOX") == MergeDecision.MANUAL


def test_merge_policy_auto_content_only(adapter):
    paths = ["web/src/content/blog-a.md", "web/src/content/blog-b.mdx"]
    pr = {"files_changed_paths": paths, "additions": 200}
    review = {"verdict": "approved", "scope_check": {"in_scope": True}}
    assert adapter.merge_policy(review, pr, "ZERODOX") == MergeDecision.AUTO


def test_discord_channel(adapter):
    assert adapter.discord_channel("approved") == "seo-fixes"


def test_iteration_mention_none(adapter):
    # SEO-Agent erstellt PR direkt, kein Jules dazwischen
    assert adapter.iteration_mention() is None
```

**Step 2: FAIL → Implementation**

```python
# src/integrations/github_integration/agent_review/adapters/seo.py
"""SeoAdapter — SEO-Agent PR-Reviews."""
from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple

from .base import AgentAdapter, AgentDetection, MergeDecision


SAFE_CONTENT_EXTENSIONS = (".md", ".mdx", ".txt")
SAFE_METADATA_PATHS = ("sitemap", "robots.txt", "blog-data", "schema.json")
DANGEROUS_PATHS = (
    "package.json", "package-lock.json", "next.config.", "tsconfig.",
    "eslint.config.", ".eslintrc", "prisma/schema.prisma", "layout.tsx",
    "middleware.ts",
)


class SeoAdapter(AgentAdapter):
    agent_name = "seo"

    def detect(self, pr_payload: Dict[str, Any]) -> AgentDetection:
        body = (pr_payload.get("body") or "").strip()
        title = pr_payload.get("title") or ""
        branch = (pr_payload.get("head") or {}).get("ref", "")

        if body.startswith("## 🔍 SEO Audit"):
            return AgentDetection(matched=True, confidence=1.0)
        if branch.startswith("seo/"):
            return AgentDetection(matched=True, confidence=0.95)
        if title.startswith("[SEO]"):
            return AgentDetection(matched=True, confidence=0.9)
        if title.startswith("SEO:") or "SEO Agent" in body:
            return AgentDetection(matched=True, confidence=0.85)

        return AgentDetection(matched=False, confidence=0.0)

    def build_prompt(self, *, diff, pr_payload, finding_context,
                     iteration, few_shot, knowledge, project) -> str:
        from ..prompts.seo_prompt import build_seo_review_prompt
        return build_seo_review_prompt(
            diff=diff, project=project, iteration=iteration,
            files_changed=pr_payload.get("files_changed_paths") or [],
            knowledge=knowledge, few_shot=few_shot,
        )

    def model_preference(self, pr_payload, diff_len) -> Tuple[str, str]:
        # SEO typischerweise simpel; Sonnet reicht
        return ("standard", "thinking")

    def merge_policy(self, review, pr_payload, project) -> MergeDecision:
        if project == "sicherheitsdienst":
            return MergeDecision.MANUAL
        if review.get("verdict") != "approved":
            return MergeDecision.MANUAL

        scope = review.get("scope_check", {})
        if not scope.get("in_scope", False):
            return MergeDecision.MANUAL

        paths = pr_payload.get("files_changed_paths") or []
        if len(paths) > 50:
            return MergeDecision.MANUAL

        # Gefährliche Pfade -> manual
        if any(any(dangerous in p for dangerous in DANGEROUS_PATHS) for p in paths):
            return MergeDecision.MANUAL

        # Nur Content/Metadata -> auto
        def is_safe(p: str) -> bool:
            if any(p.endswith(ext) for ext in SAFE_CONTENT_EXTENSIONS):
                return True
            if any(meta in p for meta in SAFE_METADATA_PATHS):
                return True
            return False

        if paths and all(is_safe(p) for p in paths):
            return MergeDecision.AUTO

        return MergeDecision.MANUAL

    def discord_channel(self, verdict: str) -> str:
        return "seo-fixes"

    def iteration_mention(self) -> Optional[str]:
        return None
```

**Step 3: Tests PASS + Commit**

```bash
.venv/bin/python -m pytest tests/unit/agent_review/test_seo_adapter.py -x -v
# 12 passed

git add src/integrations/github_integration/agent_review/adapters/seo.py tests/unit/agent_review/test_seo_adapter.py
git commit -m "feat: SeoAdapter mit Multi-Domain-Detection und Content-Only Auto-Merge"
```

---

### Task 2.3: `CodexAdapter` + Codex-Prompt

**Files:**
- Create: `src/integrations/github_integration/agent_review/prompts/codex_prompt.py`
- Create: `src/integrations/github_integration/agent_review/adapters/codex.py`
- Create: `tests/unit/agent_review/test_codex_adapter.py`

Analog zu Task 2.1 + 2.2 für Codex. Codex-Prompt fokussiert auf Code-Quality + Finding-Verifikation. `CodexAdapter.merge_policy` IMMER MANUAL (Security-Kontext).

Detect-Patterns für Codex:
- Body startet mit `## Summary` oder `This PR addresses` oder `I have implemented`
- Body enthält `Finding #` oder `Security Finding`
- Nicht Jules (kein `jules.google.com/task/` Marker)

Discord-Channel: `🤖-agent-reviews`.

Commit:
```bash
git commit -m "feat: CodexAdapter (Code-Health/Security) — immer MANUAL Merge"
```

---

### Task 2.4: SEO + Codex in Detector aktivieren

**Files:**
- Modify: `src/integrations/github_integration/jules_workflow_mixin.py`

**Step 1: Config-Flag prüfen, Adapter laden**

Im `_get_agent_detector()`:

```python
    def _get_agent_detector(self):
        if getattr(self, '_agent_detector', None) is not None:
            return self._agent_detector
        from .agent_review.detector import AgentDetector
        from .agent_review.adapters.jules import JulesAdapter

        cfg = getattr(self.config, 'agent_review', None)
        adapters_cfg = getattr(cfg.adapters, '__dict__', {}) if cfg else {}

        adapters = [JulesAdapter()]  # Jules immer an
        if adapters_cfg.get('seo', False):
            from .agent_review.adapters.seo import SeoAdapter
            adapters.append(SeoAdapter())
        if adapters_cfg.get('codex', False):
            from .agent_review.adapters.codex import CodexAdapter
            adapters.append(CodexAdapter())

        self._agent_detector = AgentDetector(adapters)
        return self._agent_detector
```

**Step 2: Mixin-Integration vertiefen** — verwende `adapter.build_prompt()` + `adapter.merge_policy()` statt hardcoded Jules-Calls. Siehe Design-Doc §5.

**Step 3: Alle Tests müssen grün bleiben**

```bash
.venv/bin/python -m pytest tests/unit/test_jules_workflow_mixin.py tests/unit/test_jules_pr123_regression.py tests/unit/agent_review/ -x -v
```

**Step 4: Commit**

```bash
git add src/integrations/github_integration/jules_workflow_mixin.py
git commit -m "feat: SEO+Codex Adapter im Mixin-Detector (per Config aktivierbar)"
```

---

## Phase 3: Jules Suggestions Poller + Queue

### Task 3.1: Queue-Layer (asyncpg)

**Files:**
- Create: `src/integrations/github_integration/agent_review/queue.py`
- Create: `tests/unit/agent_review/test_queue.py`

**Step 1: Tests** — CRUD-Operations: `enqueue`, `dequeue` mit Priority-Sort, `mark_released`, `mark_failed`. Use testcontainer-ähnlichen Ansatz wie `test_jules_state.py`.

**Step 2: Implementation**

Skeleton:

```python
# queue.py
import asyncpg, json
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional


@dataclass
class QueuedTask:
    id: int
    source: str
    priority: int
    payload: dict
    project: Optional[str]
    retry_count: int


class TaskQueue:
    def __init__(self, dsn: str):
        self._dsn = dsn
        self._pool: Optional[asyncpg.Pool] = None

    async def connect(self):
        self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=3)

    async def enqueue(self, source, priority, payload, project=None) -> int:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO agent_task_queue(source, priority, payload, project)
                   VALUES ($1,$2,$3::jsonb,$4) RETURNING id""",
                source, priority, json.dumps(payload), project,
            )
            return row["id"]

    async def get_next_batch(self, limit: int) -> List[QueuedTask]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT id, source, priority, payload, project, retry_count
                   FROM agent_task_queue
                   WHERE status='queued' AND scheduled_for <= now()
                   ORDER BY priority ASC, created_at ASC
                   LIMIT $1""",
                limit,
            )
            return [QueuedTask(id=r["id"], source=r["source"], priority=r["priority"],
                               payload=json.loads(r["payload"]) if isinstance(r["payload"], str) else r["payload"],
                               project=r["project"], retry_count=r["retry_count"]) for r in rows]

    async def mark_released(self, task_id: int, external_id: str):
        async with self._pool.acquire() as conn:
            await conn.execute(
                """UPDATE agent_task_queue SET status='released', released_at=now(),
                   released_as=$1, updated_at=now() WHERE id=$2""",
                external_id, task_id,
            )

    async def mark_failed(self, task_id: int, reason: str, retry: bool = False):
        async with self._pool.acquire() as conn:
            if retry:
                await conn.execute(
                    """UPDATE agent_task_queue SET retry_count=retry_count+1,
                       failure_reason=$1, scheduled_for=now()+interval '5 minutes',
                       updated_at=now() WHERE id=$2""",
                    reason, task_id,
                )
            else:
                await conn.execute(
                    """UPDATE agent_task_queue SET status='failed',
                       failure_reason=$1, updated_at=now() WHERE id=$2""",
                    reason, task_id,
                )

    async def count_by_status(self) -> dict:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT status, COUNT(*) FROM agent_task_queue GROUP BY status"
            )
            return {r["status"]: r["count"] for r in rows}
```

**Step 3: Tests PASS + Commit**

```bash
git commit -m "feat: TaskQueue asyncpg-Layer (enqueue, get_next_batch, mark_*)"
```

---

### Task 3.2: Jules API-Client Helper

**Files:**
- Create: `src/integrations/github_integration/agent_review/jules_api.py`
- Create: `tests/unit/agent_review/test_jules_api.py`

**Step 1: Tests mit mocked httpx/aiohttp**

Tests für:
- `create_session(prompt, repo)` → POST /sessions
- `count_concurrent_sessions()` → GET /sessions?state=IN_PROGRESS
- `get_suggestions(repo)` → stub (API-Endpoint noch nicht final dokumentiert)

**Step 2: Implementation**

```python
# jules_api.py
import aiohttp, logging
from typing import Optional, List, Dict

logger = logging.getLogger(__name__)


class JulesAPIError(Exception):
    pass


class JulesAPIClient:
    BASE_URL = "https://jules.googleapis.com/v1alpha"

    def __init__(self, api_key: str):
        self._api_key = api_key

    @property
    def _headers(self):
        return {
            "X-Goog-Api-Key": self._api_key,
            "Content-Type": "application/json",
        }

    async def create_session(self, prompt: str, owner: str, repo: str,
                             title: str = "", branch: str = "main") -> str:
        body = {
            "title": title or prompt[:80],
            "prompt": prompt,
            "sourceContext": {
                "source": f"sources/github/{owner}/{repo}",
                "githubRepoContext": {"startingBranch": branch},
            },
            "automationMode": "AUTO_CREATE_PR",
        }
        async with aiohttp.ClientSession() as http:
            async with http.post(f"{self.BASE_URL}/sessions", json=body, headers=self._headers) as r:
                if r.status == 429:
                    raise JulesAPIError("rate_limited")
                if r.status != 200:
                    text = await r.text()
                    raise JulesAPIError(f"http {r.status}: {text[:200]}")
                data = await r.json()
                return data.get("id", "")

    async def count_concurrent_sessions(self) -> int:
        async with aiohttp.ClientSession() as http:
            async with http.get(f"{self.BASE_URL}/sessions?pageSize=50",
                                headers=self._headers) as r:
                if r.status != 200:
                    return 0
                data = await r.json()
                sessions = data.get("sessions", [])
                return sum(1 for s in sessions if s.get("state") == "IN_PROGRESS")
```

**Step 3: Tests PASS + Commit**

```bash
git commit -m "feat: JulesAPIClient (create_session, count_concurrent)"
```

---

### Task 3.3: Suggestions-Poller

**Files:**
- Create: `src/integrations/github_integration/agent_review/suggestions_poller.py`
- Create: `tests/unit/agent_review/test_suggestions_poller.py`

**Step 1: Implementation**

```python
# suggestions_poller.py
class JulesSuggestionsPoller:
    def __init__(self, queue, jules_api, repos: list, max_per_run: int = 20):
        self.queue = queue
        self.jules_api = jules_api
        self.repos = repos
        self.max_per_run = max_per_run

    async def poll_and_queue(self) -> int:
        """Laeuft 3x/Tag. Holt Suggestions pro Repo, queued sie."""
        total = 0
        for full_repo in self.repos:
            owner, repo = full_repo.split("/", 1)
            try:
                # Note: Exakter Suggestions-Endpoint noch nicht dokumentiert.
                # Fallback: GET /sessions filter nach state=SUGGESTED (wenn verfuegbar)
                # Alternative: Top-Suggestions aus Dashboard scrapen (noch nicht implementiert)
                # Phase-3.3: Platzhalter mit Warnung
                logger.info(f"[suggestions-poller] {full_repo}: API noch nicht verfuegbar, skipping")
            except Exception:
                logger.exception(f"[suggestions-poller] {full_repo} failed")
        return total
```

**Hinweis:** Jules Suggestions-API ist noch im Alpha. Phase 3.3 implementiert das Skeleton; volle Integration wartet auf stabilen Endpoint. Priorität: Queue + API-Client zuerst, Suggestions-Poll als Stub.

**Step 2: Tests + Commit**

```bash
git commit -m "feat: Suggestions-Poller Skeleton (wartet auf stabilen Jules-API-Endpoint)"
```

---

### Task 3.4: Queue-Scheduler in `bot.py`

**Files:**
- Modify: `src/bot.py` — neuer `@tasks.loop` Task

**Step 1: Scheduler-Task hinzufügen**

Nach dem bestehenden `jules_nightly_batch_task`:

```python
    @tasks.loop(seconds=60)
    async def agent_task_queue_scheduler(self):
        """Released Jules-Tasks aus Queue respektiert 100/24h + 15 concurrent."""
        try:
            gh = getattr(self, "github_integration", None)
            if not gh or not getattr(gh, "_agent_review_enabled", False):
                return
            queue = gh.agent_task_queue
            jules_api = gh.jules_api_client

            concurrent = await jules_api.count_concurrent_sessions()
            if concurrent >= 15:
                return

            started_24h = await self._count_started_last_24h()  # neue Helper-Methode
            budget = min(15 - concurrent, 100 - started_24h)
            if budget <= 0:
                return

            tasks = await queue.get_next_batch(limit=budget)
            for task in tasks:
                try:
                    sid = await jules_api.create_session(
                        prompt=task.payload["prompt"],
                        owner=task.payload["owner"],
                        repo=task.payload["repo"],
                        title=task.payload.get("title", ""),
                    )
                    await queue.mark_released(task.id, sid)
                    logger.info(f"[queue] Task {task.id} -> Jules-Session {sid}")
                except Exception as e:
                    await queue.mark_failed(task.id, str(e), retry=True)

        except Exception:
            logger.exception("[queue] scheduler crashed")
```

**Step 2: Helper `_count_started_last_24h`:**

```python
    async def _count_started_last_24h(self) -> int:
        pool = self.github_integration.agent_task_queue._pool
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT COUNT(*) FROM agent_task_queue WHERE released_at > now() - interval '24 hours'"
            )
            return int(row[0] or 0)
```

**Step 3: Scheduler-Start in Startup-Flow + Commit**

```bash
git commit -m "feat: agent_task_queue_scheduler (60s loop, 100/24h + 15 concurrent limits)"
```

---

## Phase 4: Merge-Policy + Outcome-Tracker

### Task 4.1: Auto-Merge-Execution im Mixin

**Files:**
- Modify: `src/integrations/github_integration/jules_workflow_mixin.py`

Nach Claude-Approval und vor Label-Setzen:

```python
            # Adapter-basierte Merge-Policy
            decision = adapter.merge_policy(review, pr_payload, project=repo)

            if decision == MergeDecision.AUTO and self._auto_merge_enabled():
                merged = await self._gh_auto_merge_squash(owner, repo, pr_number)
                if merged:
                    await self.outcome_tracker.record_auto_merge(
                        row.id, repo, pr_number, "adapter_rule", agent_type=adapter.agent_name,
                    )
                    await self._send_review_embed(..., auto_merged=True)
            else:
                await self._apply_label_and_notify(owner, repo, pr_number, row)
```

**Commit:**
```bash
git commit -m "feat: Auto-Merge-Execution nach Claude-Approval (per Adapter)"
```

---

### Task 4.2: `OutcomeTracker`

**Files:**
- Create: `src/integrations/github_integration/agent_review/outcome_tracker.py`
- Create: `tests/unit/agent_review/test_outcome_tracker.py`

**Skeleton:**

```python
class OutcomeTracker:
    async def record_auto_merge(self, review_id, repo, pr_number, rule_matched, agent_type):
        # Insert row in auto_merge_outcomes mit checked_at=NULL
        ...

    async def check_pending_outcomes(self):
        """Laeuft stuendlich. Fuer Merges > 24h alt: Outcome pruefen."""
        # 1. Hole alle rows wo checked_at IS NULL AND merged_at < now() - 24h
        # 2. Fuer jeden: pruefe Git fuer Revert-Commit, CI-Status
        # 3. Update row
        ...
```

**Scheduled Task in `bot.py`:** `@tasks.loop(minutes=60)` ruft `check_pending_outcomes()`.

**Commit:**
```bash
git commit -m "feat: OutcomeTracker + stuendlicher Check fuer Auto-Merges"
```

---

## Phase 5: Daily-Digest + Discord-Embeds

### Task 5.1: Review-Embed-Formatter

**Files:**
- Create: `src/integrations/github_integration/agent_review/discord_embed.py`

Baut `discord.Embed` aus Review + PR. Farbkodiert (Grün Approved, Gelb Revision, Rot Escalated).

**Commit:**
```bash
git commit -m "feat: Review-Embed-Formatter mit Farbkodierung"
```

### Task 5.2: Daily-Digest

**Files:**
- Create: `src/integrations/github_integration/agent_review/daily_digest.py`
- Modify: `src/bot.py` — neuer `@tasks.loop(time=time(hour=8, minute=15))` Task

Query DB für:
- Reviews letzte 24h (by agent_type + verdict)
- Auto-Merges + Reverts
- Queue-Status
- Offene PRs wartend auf manuellen Merge
- Trends 7 Tage

Poste als Markdown in `🧠-ai-learning`.

**Commit:**
```bash
git commit -m "feat: Daily-Digest Task (08:15 in AI-Learning)"
```

---

## Phase 6: Rollout

### Task 6.1: Config umstellen auf Phase 1 live

**Files:**
- Modify: `config/config.yaml` (LIVE)

```yaml
agent_review:
  enabled: true
  dry_run: false
  adapters:
    jules: true
    seo: false       # noch aus
    codex: false
```

**Restart + Monitoring.** Alle 106 bestehenden Jules-Tests müssen weiter funktionieren. Regression-Check auf Live-Traffic 24h.

**Commit:** kein Git-Commit (config.yaml in .gitignore).

### Task 6.2: SEO-Adapter aktivieren (Phase 3 im Rollout)

```yaml
  adapters:
    seo: true
```

Beobachten, ob SEO-PRs reviewt werden. Manuelle Verifikation der ersten 3 SEO-Reviews.

### Task 6.3: Codex + Auto-Merge aktivieren

```yaml
  adapters:
    codex: true
  auto_merge:
    enabled: true
```

Stündliches Monitoring der ersten 48h.

### Task 6.4: Dokumentation

**Files:**
- Modify: `CLAUDE.md` (neuer Module-Block)
- Modify: `.claude/rules/safety.md` (Multi-Agent-Regeln)
- Modify: `docs/API.md` (neue Endpoints)
- Create: `docs/adr/008-multi-agent-review-pipeline.md`

**Commit:**
```bash
git commit -m "docs: Multi-Agent Review Pipeline — ADR 008 + CLAUDE.md"
```

---

## Execution Summary

**Tasks total:** ~25 (über 6 Phasen)
**Aktive Entwicklungszeit:** ~20h geschätzt
**Neue Tests:** ~40
**Bestehende Tests:** 106 Jules-Tests müssen durchgehend grün bleiben
**Rollback-Zeit:** 30s (Config-Flag)

**Abbruch-Bedingungen:**
- Ein bestehender Jules-Test schlägt fehl → sofort stoppen, reverten, analysieren
- Regression-Test PR #123 rot → hart abbrechen
- SEO-Agent-PR wird falsch detected als Jules → Detector-Priority prüfen

---

Plan complete and saved to `docs/plans/2026-04-14-multi-agent-review.md`. Two execution options:

1. **Subagent-Driven (this session)** — I dispatch fresh subagent per task, review between tasks, fast iteration
2. **Parallel Session (separate)** — Open new session with executing-plans, batch execution with checkpoints

Which approach?
