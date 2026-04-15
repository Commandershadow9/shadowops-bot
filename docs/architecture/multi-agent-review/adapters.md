---
title: Adapter-Schicht — Jules, SEO, Codex
status: active
version: v1
last_reviewed: 2026-04-15
owner: CommanderShadow9
related:
  - ../../adr/008-multi-agent-review-pipeline.md
  - ../../plans/2026-04-14-multi-agent-review-design.md
  - ../jules-workflow/README.md
---

# Adapter-Schicht — Jules, SEO, Codex

Der Adapter-Layer kapselt die Agent-spezifische Review-Logik hinter einem einheitlichen
Interface. Jeder Agent-Typ liefert eine Klasse mit `detect`, `build_prompt`,
`model_preference`, `merge_policy` und `discord_channel`. Der Detector waehlt per
Confidence-Ranking den passenden Adapter fuer einen Pull-Request.

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

**Step 2: Tests ausfuehren — FAIL erwartet**

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
          "body": "## SEO Audit"}
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
    assert adapter.discord_channel("approved") == "code-fixes"


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
        return "code-fixes"

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

**Step 2: FAIL -> Implementation -> PASS**

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

**Step 1: Existing Tests muessen weiter gruen sein**

```bash
.venv/bin/python -m pytest tests/unit/test_jules_workflow_mixin.py tests/unit/test_jules_pr123_regression.py -x -v
```

Note: Wenn einer fehlschlaegt, Task abbrechen — NICHTS aendern bis sie gruen sind.

**Step 2: Mixin-Methode `_detect_adapter` hinzufuegen**

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

**Step 3: Existierendes `_jules_is_jules_pr` behalten, aber Detector-Path zusaetzlich**

Suche die Stelle in `handle_jules_pr_event` die den PR als "Jules-PR" klassifiziert
(`_jules_is_jules_pr`). Fuege daneben ein optionales Detector-Routing:

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

Hinweis: In Phase 1 veraendert diese Integration nichts am Verhalten — der Detector liefert
fuer Jules-PRs das gleiche wie `_jules_is_jules_pr`.

**Step 4: Tests gruen**

```bash
.venv/bin/python -m pytest tests/unit/test_jules_workflow_mixin.py tests/unit/test_jules_pr123_regression.py -x -v
```

Expected: Alle bestehenden Tests weiter gruen.

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

**Step 2: FAIL -> Implementation**

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

(Weiterer Prompt-Body siehe Appendix A unten.)
"""


def _format_examples(examples: List[Dict]) -> str:
    if not examples:
        return "(keine Beispiele)"
    return "\n".join(
        f"- [{ex.get('outcome','?')}] {ex.get('diff_summary','')[:80]}"
        for ex in examples[:3]
    )
```

Der vollstaendige Prompt-Body (Geaenderte Dateien, Projekt-Konventionen, Beispiele, Diff,
Pruefe, Ausgabe) ist in **Appendix A** am Ende dieser Datei dokumentiert.

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

**Step 1: Tests** (analog zu JulesAdapter-Tests, aber fuer SEO-Detection-Patterns)

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
          "body": "## SEO Audit — Automatische Fixes\n\nWebsite: guildscout",
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

**Step 2: FAIL -> Implementation**

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

        if body.startswith("## SEO Audit"):
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

        # Gefaehrliche Pfade -> manual
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

Analog zu Task 2.1 + 2.2 fuer Codex. Codex-Prompt fokussiert auf Code-Quality +
Finding-Verifikation. `CodexAdapter.merge_policy` IMMER MANUAL (Security-Kontext).

Detect-Patterns fuer Codex:

- Body startet mit `## Summary` oder `This PR addresses` oder `I have implemented`
- Body enthaelt `Finding #` oder `Security Finding`
- Nicht Jules (kein `jules.google.com/task/` Marker)

Discord-Channel: `agent-reviews`.

Commit:

```bash
git commit -m "feat: CodexAdapter (Code-Health/Security) — immer MANUAL Merge"
```

---

### Task 2.4: SEO + Codex in Detector aktivieren

**Files:**

- Modify: `src/integrations/github_integration/jules_workflow_mixin.py`

**Step 1: Config-Flag pruefen, Adapter laden**

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

**Step 2: Mixin-Integration vertiefen** — verwende `adapter.build_prompt()` +
`adapter.merge_policy()` statt hardcoded Jules-Calls. Siehe Design-Doc §5.

**Step 3: Alle Tests muessen gruen bleiben**

```bash
.venv/bin/python -m pytest tests/unit/test_jules_workflow_mixin.py tests/unit/test_jules_pr123_regression.py tests/unit/agent_review/ -x -v
```

**Step 4: Commit**

```bash
git add src/integrations/github_integration/jules_workflow_mixin.py
git commit -m "feat: SEO+Codex Adapter im Mixin-Detector (per Config aktivierbar)"
```

---

## Appendix A: SEO-Prompt-Body (Geaenderte Dateien, Konventionen, Beispiele, Diff, Pruefe, Ausgabe)

Der vollstaendige Prompt-Text, den `build_seo_review_prompt` zusammensetzt:

```text
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

{
  "verdict": "approved" | "revision_requested",
  "summary": "1-3 Saetze",
  "blockers": [...],
  "suggestions": [...],
  "nits": [...],
  "scope_check": {
    "in_scope": true|false,
    "explanation": "..."
  }
}

severity in Issues: critical|high|medium|low
```

Die Sections `Geaenderte Dateien`, `Projekt-Konventionen`, `Beispiele`, `Diff`, `Pruefe`,
`Ausgabe` werden als f-String zur Laufzeit befuellt. Dieser Appendix dokumentiert das
Template unveraendert zur Quelle — wichtig fuer Reviews und spaetere Aenderungen am
SEO-Prompt.
