---
title: Jules Workflow — Review Pipeline
status: active
version: v1
last_reviewed: 2026-04-15
owner: CommanderShadow9
related:
  - ../../adr/007-jules-secops-workflow.md
  - ../../design/jules-workflow.md
---

# Jules Workflow — Review Pipeline

## Phase 4: JSON-Schema für Claude-Reviews

### Task 4.1: `jules_review.json` anlegen

**Files:**
- Create: `src/schemas/jules_review.json`

**Step 1: Schema-Datei schreiben**

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "JulesReview",
  "type": "object",
  "required": ["verdict", "summary", "blockers", "suggestions", "nits", "scope_check"],
  "properties": {
    "verdict": {
      "type": "string",
      "enum": ["approved", "revision_requested"]
    },
    "summary": {
      "type": "string",
      "maxLength": 800
    },
    "blockers": {
      "type": "array",
      "items": { "$ref": "#/$defs/issue" }
    },
    "suggestions": {
      "type": "array",
      "items": { "$ref": "#/$defs/issue" }
    },
    "nits": {
      "type": "array",
      "items": { "$ref": "#/$defs/issue" }
    },
    "scope_check": {
      "type": "object",
      "required": ["in_scope", "explanation"],
      "properties": {
        "in_scope": { "type": "boolean" },
        "explanation": { "type": "string", "maxLength": 500 }
      }
    }
  },
  "$defs": {
    "issue": {
      "type": "object",
      "required": ["title", "reason", "file", "severity"],
      "properties": {
        "title":         { "type": "string", "maxLength": 200 },
        "reason":        { "type": "string", "maxLength": 1000 },
        "file":          { "type": "string", "maxLength": 300 },
        "line":          { "type": ["integer", "null"] },
        "severity":      { "type": "string", "enum": ["critical", "high", "medium"] },
        "suggested_fix": { "type": "string", "maxLength": 1000 }
      }
    }
  }
}
```

**Step 2: jsonschema-Validierung testen**

Erstelle `tests/unit/test_jules_review_schema.py`:

```python
import json
import pathlib
import pytest
import jsonschema


SCHEMA_PATH = pathlib.Path("src/schemas/jules_review.json")


@pytest.fixture
def schema():
    return json.loads(SCHEMA_PATH.read_text())


def test_schema_loads(schema):
    jsonschema.Draft7Validator.check_schema(schema)


def test_valid_minimal_review_passes(schema):
    review = {
        "verdict": "approved",
        "summary": "Clean dependency bump.",
        "blockers": [],
        "suggestions": [],
        "nits": [],
        "scope_check": {"in_scope": True, "explanation": "Matches finding"},
    }
    jsonschema.validate(review, schema)  # raises on fail


def test_valid_revision_with_blocker_passes(schema):
    review = {
        "verdict": "revision_requested",
        "summary": "Scope violation detected.",
        "blockers": [{
            "title": "defu removal out of scope",
            "reason": "Finding was picomatch only.",
            "file": "web/package.json",
            "line": 23,
            "severity": "high",
            "suggested_fix": "Revert defu removal",
        }],
        "suggestions": [],
        "nits": [],
        "scope_check": {"in_scope": False, "explanation": "Extra removal"},
    }
    jsonschema.validate(review, schema)


def test_missing_scope_check_fails(schema):
    review = {
        "verdict": "approved",
        "summary": "x",
        "blockers": [],
        "suggestions": [],
        "nits": [],
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(review, schema)


def test_invalid_verdict_fails(schema):
    review = {
        "verdict": "maybe",
        "summary": "x",
        "blockers": [],
        "suggestions": [],
        "nits": [],
        "scope_check": {"in_scope": True, "explanation": "x"},
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(review, schema)
```

**Step 3: Tests ausführen**

```bash
pytest tests/unit/test_jules_review_schema.py -x -v
```

Erwartet: 5 passed.

**Step 4: Commit**

```bash
git add src/schemas/jules_review.json tests/unit/test_jules_review_schema.py
git commit -m "feat: jules_review.json Schema + Validierungstests"
```

---

## Phase 5: Prompt-Builder

### Task 5.1: `jules_review_prompt.py` Skeleton + `compute_verdict()`

**Files:**
- Create: `src/integrations/github_integration/jules_review_prompt.py`
- Create: `tests/unit/test_jules_review_prompt.py`

**Step 1: Tests für `compute_verdict` (reine Funktion, einfach zu testen)**

```python
# tests/unit/test_jules_review_prompt.py
import pytest
from src.integrations.github_integration.jules_review_prompt import (
    compute_verdict,
    build_review_prompt,
    truncate_diff,
)


def _base_review():
    return {
        "verdict": "approved",
        "summary": "x",
        "blockers": [],
        "suggestions": [],
        "nits": [],
        "scope_check": {"in_scope": True, "explanation": "x"},
    }


def test_compute_verdict_empty_blockers_in_scope_approved():
    assert compute_verdict(_base_review()) == "approved"


def test_compute_verdict_with_blockers_revision():
    r = _base_review()
    r["blockers"] = [{"title": "x", "reason": "y", "file": "z", "severity": "high"}]
    assert compute_verdict(r) == "revision_requested"


def test_compute_verdict_out_of_scope_revision():
    r = _base_review()
    r["scope_check"]["in_scope"] = False
    assert compute_verdict(r) == "revision_requested"


def test_compute_verdict_ignores_suggestions_and_nits():
    r = _base_review()
    r["suggestions"] = [{"title": "s", "reason": "r", "file": "f", "severity": "medium"}]
    r["nits"] = [{"title": "n", "reason": "r", "file": "f", "severity": "medium"}]
    assert compute_verdict(r) == "approved"
```

**Step 2: Implementation**

```python
# src/integrations/github_integration/jules_review_prompt.py
"""
Jules Review Prompt Builder.

Baut den Claude-Prompt für strukturierte PR-Reviews mit Learning-Kontext
aus agent_learning DB (few-shot examples + project knowledge).

Siehe docs/design/jules-workflow.md §8.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

MAX_DIFF_CHARS_DEFAULT = 8000


def truncate_diff(diff: str, max_chars: int = MAX_DIFF_CHARS_DEFAULT) -> str:
    """Schneidet den Diff auf max_chars, mit Marker am Ende wenn gekürzt."""
    if len(diff) <= max_chars:
        return diff
    cut = diff[:max_chars]
    remaining = len(diff) - max_chars
    return cut + f"\n\n[... {remaining} Zeichen abgeschnitten ...]"


def compute_verdict(review: Dict[str, Any]) -> str:
    """
    Deterministische Verdict-Regel — überschreibt Claudes selbst gesetzten
    verdict nach der AI-Antwort. Verhindert Confidence-Oszillation.

    Regel: approved nur wenn (0 blockers) AND (scope in_scope=True).
    """
    if review.get("blockers"):
        return "revision_requested"
    scope = review.get("scope_check") or {}
    if not scope.get("in_scope", False):
        return "revision_requested"
    return "approved"


def build_review_prompt(
    *,
    finding: Dict[str, Any],
    project: str,
    diff: str,
    iteration: int,
    project_knowledge: List[str],
    few_shot_examples: List[Dict[str, Any]],
    max_diff_chars: int = MAX_DIFF_CHARS_DEFAULT,
) -> str:
    """
    Baut den kompletten Review-Prompt.

    Args:
        finding: Dict aus security_analyst.findings (title, severity, description, ...)
        project: z.B. "ZERODOX"
        diff: git diff Output
        iteration: aktuelle Review-Iteration (1-indexed)
        project_knowledge: List[str] aus agent_knowledge
        few_shot_examples: List[dict] aus jules_review_examples
    """
    knowledge_block = (
        "\n".join(f"- {k}" for k in project_knowledge)
        if project_knowledge else "(noch keine gelernten Konventionen)"
    )

    examples_block = _format_examples(few_shot_examples)
    diff_short = truncate_diff(diff, max_diff_chars)

    return f"""Du bist ein Senior Security-Reviewer. Dein Job: einen Pull-Request von
Jules (Googles AI-Coding-Agent) strukturiert prüfen und Blocker/Suggestions/Nits
klassifizieren.

**Grundregeln:**
- Sei STRIKT bei Security (CVEs, Credentials, Injection, Secrets).
- Sei PRAGMATISCH bei Stil (Nits blockieren NIE das Approval).
- Prüfe STRENG, dass der PR genau das Original-Finding löst und NICHTS anderes.
- Du darfst NICHT den Fix selbst schreiben — nur bewerten.

---

## Original-Finding

- **Projekt:** {project}
- **Iteration:** {iteration} of 5
- **Titel:** {finding.get('title', 'n/a')}
- **Severity:** {finding.get('severity', 'n/a')}
- **Kategorie:** {finding.get('category', 'n/a')}
- **CVE:** {finding.get('cve') or 'n/a'}

**Beschreibung:**
{finding.get('description', '(keine Beschreibung)')}

**Erwarteter Scope:** Nur die betroffenen Dateien/Module des Findings — kein Refactoring, keine unrelated Dependency-Changes, keine "Drive-by" Verbesserungen.

---

## Projekt-Konventionen (gelernt aus vorigen Reviews)

{knowledge_block}

---

## Beispiele (aus echten vergangenen Reviews dieses Projekts)

{examples_block}

---

## Diff des aktuellen Pull-Requests

```diff
{diff_short}
```

---

## Deine Aufgabe

Gib ausschliesslich folgendes JSON zurück (ohne Markdown-Fence, ohne Kommentare, ohne Text davor/danach):

```json
{{
  "verdict": "approved" oder "revision_requested",
  "summary": "1-3 Sätze was der PR macht",
  "blockers": [
    {{
      "title": "Kurze Zusammenfassung des Problems",
      "reason": "Warum ist das ein Blocker",
      "file": "web/package.json",
      "line": 23,
      "severity": "critical|high|medium",
      "suggested_fix": "Wie Jules es fixen soll"
    }}
  ],
  "suggestions": [ ... gleiches Format ... ],
  "nits": [ ... gleiches Format ... ],
  "scope_check": {{
    "in_scope": true/false,
    "explanation": "Bleibt der PR im Scope des Findings?"
  }}
}}
```

**Definitionen:**
- **BLOCKER:** Security-Risk, Breaking Change, Out-of-Scope Refactoring, fehlende Acceptance-Criteria, neue CVEs, gelöschte Tests.
- **SUGGESTION:** Verbesserungsvorschlag ohne Blocker-Charakter (Dep-Dedup, Logging-Qualität, Performance).
- **NIT:** Reiner Stil (Naming, Formatierung, Trailing-Whitespace).

**Wichtig:**
- Blockers leer + in_scope=true -> approved. Sonst revision_requested.
- Dein `verdict`-Feld wird nach der Antwort von einer deterministischen Regel überschrieben — du musst es trotzdem korrekt setzen.
- Erwähne KEIN Confidence-Score (das war Teil des PR #123 Problems).
"""


def _format_examples(examples: List[Dict[str, Any]]) -> str:
    if not examples:
        return "(noch keine Beispiele für dieses Projekt)"
    lines = []
    for ex in examples[:4]:
        outcome = ex.get("outcome", "unknown")
        summary = ex.get("diff_summary", "")
        review = ex.get("review_json", {})
        if isinstance(review, str):
            try:
                review = json.loads(review)
            except Exception:
                review = {}
        verdict = review.get("verdict", "unknown")
        blockers_n = len(review.get("blockers", []))
        lines.append(
            f"- **[{outcome}]** {summary} -> verdict={verdict}, blockers={blockers_n}"
        )
    return "\n".join(lines)
```

**Step 3: Tests laufen — PASS**

```bash
pytest tests/unit/test_jules_review_prompt.py -x -v
```

Erwartet: 4 passed.

**Step 4: Commit**

```bash
git add src/integrations/github_integration/jules_review_prompt.py tests/unit/test_jules_review_prompt.py
git commit -m "feat: Jules Review Prompt-Builder + compute_verdict"
```

---

### Task 5.2: Prompt-Builder Integration-Test mit echtem Finding

**Files:**
- Modify: `tests/unit/test_jules_review_prompt.py`

**Step 1: Test hinzufügen**

```python
def test_build_review_prompt_contains_all_blocks():
    finding = {
        "title": "ReDoS in picomatch",
        "severity": "high",
        "category": "npm_audit",
        "cve": "CVE-2024-45296",
        "description": "Vulnerable regex in picomatch <4.0.4",
    }
    prompt = build_review_prompt(
        finding=finding,
        project="ZERODOX",
        diff="diff --git a/x b/x\n+new line\n",
        iteration=2,
        project_knowledge=["ZERODOX nutzt Prisma, Schema-Änderungen brauchen migrate"],
        few_shot_examples=[{
            "outcome": "good_catch",
            "diff_summary": "Dep bump mit Drive-by removal",
            "review_json": {"verdict": "revision_requested", "blockers": [{"x": 1}]},
        }],
    )
    assert "ReDoS in picomatch" in prompt
    assert "CVE-2024-45296" in prompt
    assert "Iteration: 2 of 5" in prompt
    assert "Prisma" in prompt
    assert "good_catch" in prompt
    assert "verdict" in prompt
    assert "diff --git" in prompt


def test_truncate_diff_cuts_and_marks():
    long = "x" * 10000
    out = truncate_diff(long, max_chars=100)
    assert len(out) < 200
    assert "abgeschnitten" in out


def test_truncate_diff_short_unchanged():
    short = "abc"
    assert truncate_diff(short) == "abc"
```

**Step 2: Tests — PASS**

```bash
pytest tests/unit/test_jules_review_prompt.py -x -v
```

Erwartet: 7 passed.

**Step 3: Commit**

```bash
git add tests/unit/test_jules_review_prompt.py
git commit -m "test: jules_review_prompt Integration-Assertions"
```

---

### Task 5.3: Learning-Context-Loader (`jules_learning.py`)

**Files:**
- Create: `src/integrations/github_integration/jules_learning.py`
- Create: `tests/unit/test_jules_learning.py`

**Step 1: Test schreiben**

```python
# tests/unit/test_jules_learning.py
import os
import pytest
from src.integrations.github_integration.jules_learning import JulesLearning


DSN = os.environ.get("AGENT_LEARNING_DB_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="AGENT_LEARNING_DB_URL nicht gesetzt")


@pytest.fixture
async def learning():
    l = JulesLearning(DSN)
    await l.connect()
    async with l._pool.acquire() as conn:
        await conn.execute("DELETE FROM jules_review_examples WHERE project LIKE 'test_%'")
    yield l
    async with l._pool.acquire() as conn:
        await conn.execute("DELETE FROM jules_review_examples WHERE project LIKE 'test_%'")
    await l.close()


@pytest.mark.asyncio
async def test_fetch_few_shot_empty_when_no_examples(learning):
    out = await learning.fetch_few_shot_examples("test_empty", limit=3)
    assert out == []


@pytest.mark.asyncio
async def test_fetch_few_shot_orders_by_weight(learning):
    async with learning._pool.acquire() as conn:
        for i, (outcome, weight) in enumerate([
            ("good_catch", 1.0),
            ("good_catch", 2.5),
            ("false_positive", 0.8),
        ]):
            await conn.execute(
                """
                INSERT INTO jules_review_examples (project, diff_summary, review_json, outcome, weight)
                VALUES ($1, $2, '{}', $3, $4)
                """,
                "test_weight", f"example_{i}", outcome, weight,
            )

    out = await learning.fetch_few_shot_examples("test_weight", limit=10)
    assert len(out) == 3
    assert out[0]["weight"] >= out[1]["weight"] >= out[2]["weight"]


@pytest.mark.asyncio
async def test_fetch_project_knowledge_returns_strings(learning):
    # Wir gehen davon aus dass agent_knowledge bereits existiert (teil von agent_learning)
    # Hier testen wir nur, dass leere Projekte leere Listen zurückgeben
    out = await learning.fetch_project_knowledge("test_knowledge_empty", limit=10)
    assert isinstance(out, list)
```

**Step 2: Implementation**

```python
# src/integrations/github_integration/jules_learning.py
"""
Jules Learning — Kontext-Loader aus agent_learning DB.

Stellt few-shot-Examples und Projekt-Knowledge für den Review-Prompt bereit.
Schreibt später (Phase 14) klassifizierte Outcomes zurück.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

import asyncpg

logger = logging.getLogger(__name__)


class JulesLearning:
    def __init__(self, dsn: str):
        self._dsn = dsn
        self._pool: Optional[asyncpg.Pool] = None

    async def connect(self) -> None:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                self._dsn, min_size=1, max_size=3, command_timeout=10
            )
            logger.info("JulesLearning connected to agent_learning DB")

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None

    async def fetch_few_shot_examples(
        self, project: str, limit: int = 3
    ) -> List[Dict[str, Any]]:
        """Liefert die besten Examples nach weight DESC, created_at DESC."""
        sql = """
            SELECT project, pr_ref, diff_summary, review_json, outcome, weight, created_at
            FROM jules_review_examples
            WHERE project = $1
            ORDER BY weight DESC, created_at DESC
            LIMIT $2
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, project, limit)
        out = []
        for r in rows:
            d = dict(r)
            # review_json ist JSONB — asyncpg gibt String zurück wenn nicht decoded
            if isinstance(d.get("review_json"), str):
                try:
                    d["review_json"] = json.loads(d["review_json"])
                except Exception:
                    d["review_json"] = {}
            out.append(d)
        return out

    async def fetch_project_knowledge(
        self, project: str, limit: int = 10
    ) -> List[str]:
        """
        Liefert Strings aus agent_knowledge für agent_name='jules_reviewer' und
        project=$1. Returns [] wenn Tabelle fehlt (Soft-Fail).
        """
        sql = """
            SELECT content FROM agent_knowledge
            WHERE agent_name = 'jules_reviewer'
              AND project = $1
            ORDER BY confidence DESC NULLS LAST, updated_at DESC
            LIMIT $2
        """
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(sql, project, limit)
            return [r["content"] for r in rows if r["content"]]
        except asyncpg.UndefinedTableError:
            logger.warning("agent_knowledge-Tabelle fehlt — Learning-Context leer")
            return []
        except asyncpg.UndefinedColumnError as e:
            # Falls Schema leicht anders: Soft-Fail, log, leer zurück
            logger.warning(f"agent_knowledge Schema-Mismatch: {e}")
            return []
```

**Step 3: Tests — PASS**

```bash
export AGENT_LEARNING_DB_URL=$(python -c "from src.utils.config import Config; print(Config().agent_learning_dsn)")
pytest tests/unit/test_jules_learning.py -x -v
```

Erwartet: 3 passed.

**Step 4: Commit**

```bash
git add src/integrations/github_integration/jules_learning.py tests/unit/test_jules_learning.py
git commit -m "feat: JulesLearning — Few-Shot + Projekt-Knowledge-Loader"
```

---

## Phase 6: AI-Engine `review_pr()` Methode

### Task 6.1: Neue Methode in `ai_engine.py` mit Schema-Validierung

**Files:**
- Modify: `src/integrations/ai_engine.py`
- Create: `tests/unit/test_ai_engine_review_pr.py`

**Step 1: Test mit gemocktem Provider**

```python
# tests/unit/test_ai_engine_review_pr.py
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.integrations.ai_engine import AIEngine


@pytest.fixture
def engine_with_mock_claude():
    """AIEngine mit gemocktem ClaudeProvider, der festes JSON zurückgibt."""
    engine = MagicMock(spec=AIEngine)
    # Wir testen nur die neue Methode — reale AIEngine-Instanzierung umgehen
    return engine


def _valid_review_json():
    return {
        "verdict": "approved",
        "summary": "Clean upgrade",
        "blockers": [],
        "suggestions": [],
        "nits": [],
        "scope_check": {"in_scope": True, "explanation": "matches finding"},
    }


@pytest.mark.asyncio
async def test_review_pr_returns_validated_dict(monkeypatch):
    from src.integrations import ai_engine as aie

    engine = aie.AIEngine.__new__(aie.AIEngine)  # Bypass __init__
    engine.logger = __import__("logging").getLogger("test")

    # Mock den Claude-Call
    async def fake_query_raw(prompt, model=None, timeout=None):
        return json.dumps(_valid_review_json())
    engine.claude = MagicMock()
    engine.claude.query_raw = AsyncMock(side_effect=fake_query_raw)

    result = await engine.review_pr(
        diff="diff --git a/x b/x",
        finding_context={"title": "t", "severity": "high", "description": "d"},
        project="test_project",
        iteration=1,
        project_knowledge=[],
        few_shot_examples=[],
    )

    assert result is not None
    assert result["verdict"] == "approved"
    assert result["scope_check"]["in_scope"] is True


@pytest.mark.asyncio
async def test_review_pr_invalid_json_returns_none():
    from src.integrations import ai_engine as aie

    engine = aie.AIEngine.__new__(aie.AIEngine)
    engine.logger = __import__("logging").getLogger("test")

    async def fake_bad(prompt, model=None, timeout=None):
        return "not json at all"
    engine.claude = MagicMock()
    engine.claude.query_raw = AsyncMock(side_effect=fake_bad)

    result = await engine.review_pr(
        diff="d", finding_context={}, project="p", iteration=1,
        project_knowledge=[], few_shot_examples=[],
    )
    assert result is None


@pytest.mark.asyncio
async def test_review_pr_schema_invalid_returns_none():
    from src.integrations import ai_engine as aie

    engine = aie.AIEngine.__new__(aie.AIEngine)
    engine.logger = __import__("logging").getLogger("test")

    async def fake_missing_fields(prompt, model=None, timeout=None):
        return json.dumps({"verdict": "approved"})  # fehlende felder
    engine.claude = MagicMock()
    engine.claude.query_raw = AsyncMock(side_effect=fake_missing_fields)

    result = await engine.review_pr(
        diff="d", finding_context={}, project="p", iteration=1,
        project_knowledge=[], few_shot_examples=[],
    )
    assert result is None


@pytest.mark.asyncio
async def test_review_pr_verdict_overridden_deterministic():
    """Claude sagt approved, aber scope_check.in_scope=False -> überschreiben auf revision."""
    from src.integrations import ai_engine as aie

    engine = aie.AIEngine.__new__(aie.AIEngine)
    engine.logger = __import__("logging").getLogger("test")

    bad = _valid_review_json()
    bad["scope_check"]["in_scope"] = False  # aber verdict bleibt approved

    async def fake(prompt, model=None, timeout=None):
        return json.dumps(bad)
    engine.claude = MagicMock()
    engine.claude.query_raw = AsyncMock(side_effect=fake)

    result = await engine.review_pr(
        diff="d", finding_context={}, project="p", iteration=1,
        project_knowledge=[], few_shot_examples=[],
    )
    assert result["verdict"] == "revision_requested"
```

**Step 2: Implementation in `ai_engine.py`**

Suche das Ende der `AIEngine`-Klasse (nach `verify_fix` oder ähnlich) und füge folgende Methode an. Die Imports `json`, `jsonschema`, `pathlib`, `List`, `Dict`, `Any`, `Optional` müssen oben existieren.

```python
    async def review_pr(
        self,
        *,
        diff: str,
        finding_context: Dict[str, Any],
        project: str,
        iteration: int,
        project_knowledge: List[str],
        few_shot_examples: List[Dict[str, Any]],
        max_diff_chars: int = 8000,
    ) -> Optional[Dict[str, Any]]:
        """
        Strukturiertes PR-Review via Claude Opus.

        Der Prompt wird via jules_review_prompt.build_review_prompt() gebaut,
        das Ergebnis gegen src/schemas/jules_review.json validiert, und der
        verdict-Feld deterministisch via compute_verdict() überschrieben.

        Returns:
            Validiertes Review-Dict mit forciertem verdict, oder None bei:
            - AI-Call fehlgeschlagen / Timeout
            - Non-JSON Response (auch nach Fence-Strip)
            - jsonschema-Validation Fail
        """
        from src.integrations.github_integration.jules_review_prompt import (
            build_review_prompt,
            compute_verdict,
        )
        import json as _json
        import pathlib as _pl
        import jsonschema as _js

        prompt = build_review_prompt(
            finding=finding_context,
            project=project,
            diff=diff,
            iteration=iteration,
            project_knowledge=project_knowledge,
            few_shot_examples=few_shot_examples,
            max_diff_chars=max_diff_chars,
        )

        try:
            raw = await self.claude.query_raw(
                prompt, model="thinking", timeout=300
            )
        except Exception as e:
            self.logger.error(f"[jules] Claude-Call failed: {e}")
            return None

        if not raw:
            self.logger.error("[jules] Claude returned empty response")
            return None

        # Strip markdown fences falls vorhanden
        clean = raw.strip()
        if clean.startswith("```"):
            lines = clean.split("\n")
            clean = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])

        try:
            review = _json.loads(clean)
        except _json.JSONDecodeError as e:
            self.logger.error(f"[jules] JSON parse failed: {e} / raw[:200]={raw[:200]!r}")
            return None

        # Schema-Validierung
        schema_path = _pl.Path(__file__).parent.parent / "schemas" / "jules_review.json"
        try:
            schema = _json.loads(schema_path.read_text())
            _js.validate(review, schema)
        except _js.ValidationError as e:
            self.logger.error(f"[jules] Schema validation failed: {e.message}")
            return None
        except FileNotFoundError:
            self.logger.error(f"[jules] Schema not found at {schema_path}")
            return None

        # Deterministischer Verdict-Override
        review["verdict"] = compute_verdict(review)

        self.logger.info(
            f"[jules] review ok: verdict={review['verdict']} "
            f"blockers={len(review['blockers'])} "
            f"suggestions={len(review['suggestions'])} "
            f"nits={len(review['nits'])} "
            f"in_scope={review['scope_check']['in_scope']}"
        )
        return review
```

**Step 3: Tests — PASS**

```bash
pytest tests/unit/test_ai_engine_review_pr.py -x -v
```

Erwartet: 4 passed.

**Step 4: Commit**

```bash
git add src/integrations/ai_engine.py tests/unit/test_ai_engine_review_pr.py
git commit -m "feat: ai_engine.review_pr — strukturiertes PR-Review mit Schema-Validierung"
```

---

*Phase 6 abgeschlossen.*

---

## Phase 9: Comment-Management (Single-Comment-Edit-Strategie)

### Task 9.1: Comment-Body-Builder

**Files:**
- Create: `src/integrations/github_integration/jules_comment.py`
- Create: `tests/unit/test_jules_comment.py`

**Step 1: Tests**

```python
# tests/unit/test_jules_comment.py
from src.integrations.github_integration.jules_comment import (
    build_review_comment_body,
)


def _review(verdict="approved", blockers=None, suggestions=None, nits=None, in_scope=True):
    return {
        "verdict": verdict,
        "summary": "Test summary",
        "blockers": blockers or [],
        "suggestions": suggestions or [],
        "nits": nits or [],
        "scope_check": {"in_scope": in_scope, "explanation": "exp"},
    }


def test_comment_approved_has_green_marker():
    body = build_review_comment_body(
        review=_review(), iteration=1, pr_number=123, finding_id=42,
    )
    assert "APPROVED" in body.upper()
    assert "Iteration 1 of 5" in body
    assert "PR #123" in body
    assert "Finding #42" in body


def test_comment_revision_lists_blockers():
    blockers = [{
        "title": "Scope violation",
        "reason": "defu removed",
        "file": "web/package.json",
        "line": 23,
        "severity": "high",
        "suggested_fix": "Revert",
    }]
    body = build_review_comment_body(
        review=_review(verdict="revision_requested", blockers=blockers, in_scope=False),
        iteration=2, pr_number=123, finding_id=42,
    )
    assert "REVISION" in body.upper()
    assert "Scope violation" in body
    assert "web/package.json" in body
    assert "Out of scope" in body


def test_comment_suggestions_shown_but_not_blocking():
    body = build_review_comment_body(
        review=_review(suggestions=[{
            "title": "Dedup",
            "reason": "nicer",
            "file": "x",
            "severity": "medium",
            "suggested_fix": "npm dedupe",
        }]),
        iteration=1, pr_number=1, finding_id=1,
    )
    assert "Dedup" in body
    assert "APPROVED" in body.upper()  # Suggestions blocken NICHT


def test_comment_has_marker_prefix_for_self_filter():
    """PR #123 Fix: Bot-Comments müssen erkennbar sein am Body-Prefix."""
    body = build_review_comment_body(
        review=_review(), iteration=1, pr_number=1, finding_id=1,
    )
    assert body.startswith("### ")
```

**Step 2: Implementation**

```python
# src/integrations/github_integration/jules_comment.py
"""
Jules Review Comment Body-Builder.

Erzeugt das Markdown für den einzigen PR-Comment, der bei jeder
Iteration via PATCH editiert wird.

Siehe Design-Doc §8.4.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

# Marker — Body-Prefix für Self-Comment-Filter
COMMENT_MARKER = "### Claude Security Review"


def build_review_comment_body(
    *,
    review: Dict[str, Any],
    iteration: int,
    pr_number: int,
    finding_id: int,
    max_iterations: int = 5,
) -> str:
    verdict = review.get("verdict", "revision_requested")
    blockers = review.get("blockers", [])
    suggestions = review.get("suggestions", [])
    nits = review.get("nits", [])
    summary = review.get("summary", "")
    scope = review.get("scope_check", {})

    if verdict == "approved":
        status_line = "**Verdict:** APPROVED"
    else:
        status_line = "**Verdict:** REVISION REQUESTED"

    scope_line = (
        "**Scope-Check:** In scope" if scope.get("in_scope")
        else f"**Scope-Check:** Out of scope — {scope.get('explanation', '')}"
    )

    parts = [
        f"{COMMENT_MARKER} — Iteration {iteration} of {max_iterations}",
        "",
        status_line,
        "",
        f"**Summary:** {summary}",
        "",
        "---",
        "",
    ]

    if blockers:
        parts.append("#### Blockers (muss gefixt werden)")
        parts.append("")
        for i, b in enumerate(blockers, 1):
            parts.extend(_format_issue(i, b))
        parts.append("")

    if suggestions:
        parts.append("#### Suggestions (nicht blockierend)")
        parts.append("")
        for i, s in enumerate(suggestions, 1):
            parts.extend(_format_issue(i, s))
        parts.append("")

    if nits:
        parts.append("#### Nits")
        parts.append("")
        for i, n in enumerate(nits, 1):
            parts.extend(_format_issue(i, n))
        parts.append("")

    if not (blockers or suggestions or nits):
        parts.append("_Keine Anmerkungen._")
        parts.append("")

    parts.append(scope_line)
    parts.append("")
    parts.append("---")
    parts.append(
        f"*ShadowOps SecOps Workflow - PR #{pr_number} - Finding #{finding_id}*"
    )

    return "\n".join(parts)


def _format_issue(idx: int, issue: Dict[str, Any]) -> List[str]:
    lines = []
    title = issue.get("title", "Untitled")
    reason = issue.get("reason", "")
    file_ = issue.get("file", "")
    line_no = issue.get("line")
    severity = issue.get("severity", "medium")
    fix = issue.get("suggested_fix", "")

    loc = f"{file_}:{line_no}" if line_no else file_
    lines.append(f"{idx}. **{title}** ({severity})")
    lines.append(f"   - Datei: `{loc}`")
    lines.append(f"   - Grund: {reason}")
    if fix:
        lines.append(f"   - Fix: {fix}")
    lines.append("")
    return lines


def is_bot_comment(body: str) -> bool:
    """Self-Comment-Filter — erkennt Bot-eigene Reviews am Marker."""
    return body.lstrip().startswith(COMMENT_MARKER)
```

**Step 3: Tests — PASS**

```bash
pytest tests/unit/test_jules_comment.py -x -v
```

Erwartet: 4 passed.

**Step 4: Commit**

```bash
git add src/integrations/github_integration/jules_comment.py tests/unit/test_jules_comment.py
git commit -m "feat: jules_comment — Review-Body-Builder + Self-Filter-Marker"
```

---

### Task 9.2: Comment Post/Edit via gh CLI

**Files:**
- Modify: `src/integrations/github_integration/jules_workflow_mixin.py`

**Step 1: Implementation — ersetze den STUB `_jules_post_or_edit_review_comment`**

```python
    async def _jules_post_or_edit_review_comment(
        self, *, owner: str, repo: str, pr_number: int,
        review: Dict[str, Any], row: JulesReviewRow, iteration: int,
    ) -> None:
        """
        Postet oder editiert den Single-Review-Comment.

        - Erster Review: gh pr comment -> Body, dann comment_id aus Response parsen
        - Zweite+ Reviews: gh api ... --method PATCH auf existierende comment_id
          (PATCH erzeugt kein issue_comment:created Event -> kein Webhook-Loop)
        """
        from .jules_comment import build_review_comment_body
        import re

        cfg = self.config.jules_workflow
        max_iter = cfg.max_iterations

        body = build_review_comment_body(
            review=review,
            iteration=iteration,
            pr_number=pr_number,
            finding_id=row.finding_id or 0,
            max_iterations=max_iter,
        )

        repo_slug = f"{owner}/{repo}"

        if row.review_comment_id:
            # EDIT: PATCH
            proc = await asyncio.create_subprocess_exec(
                "gh", "api",
                f"repos/{repo_slug}/issues/comments/{row.review_comment_id}",
                "--method", "PATCH",
                "-f", f"body={body}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            if proc.returncode != 0:
                logger.error(f"[jules] comment PATCH failed: {stderr.decode()[:200]}")
                # Fallback: neu posten
                row.review_comment_id = None

        if not row.review_comment_id:
            # POST: gh pr comment
            proc = await asyncio.create_subprocess_exec(
                "gh", "pr", "comment", str(pr_number),
                "--repo", repo_slug, "--body", body,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            if proc.returncode != 0:
                logger.error(f"[jules] gh pr comment failed: {stderr.decode()[:200]}")
                return

            # Parse Comment-ID aus der URL in stdout
            # Format: https://github.com/o/r/pull/N#issuecomment-12345678
            url = stdout.decode().strip()
            m = re.search(r"#issuecomment-(\d+)", url)
            if m:
                comment_id = int(m.group(1))
                await self.jules_state.update_comment_id(row.id, comment_id)
                logger.info(f"[jules] posted review comment id={comment_id}")
            else:
                logger.warning(f"[jules] couldn't parse comment id from: {url}")
```

**Step 2: Smoke-Import-Test**

```bash
python -c "from src.integrations.github_integration.jules_workflow_mixin import JulesWorkflowMixin; print('OK')"
pytest tests/unit/test_jules_workflow_mixin.py -x -v
```

Erwartet: OK + 10 passed (Tests existieren nur für should_review + handle_pr_event).

**Step 3: Commit**

```bash
git add src/integrations/github_integration/jules_workflow_mixin.py
git commit -m "feat: Single-Comment-Edit-Strategie via gh api PATCH"
```

---

## Phase 10: Escalation + Approval

### Task 10.1: `_jules_escalate_to_human`

**Files:**
- Modify: `src/integrations/github_integration/jules_workflow_mixin.py`

**Step 1: Implementation — ersetze den STUB**

```python
    async def _jules_escalate_to_human(
        self, row: JulesReviewRow, reason: str
    ) -> None:
        """
        Setzt den PR in 'escalated' Terminal-State, postet einmaligen
        Eskalations-Kommentar, pingt Discord-Alerts.
        """
        cfg = self.config.jules_workflow

        # DB: terminal setzen
        await self.jules_state.mark_terminal(row.id, "escalated")

        # Discord Ping
        msg = (
            f"**Jules PR Escalation**\n"
            f"Repo: `{row.repo}` - PR #{row.pr_number}\n"
            f"Grund: `{reason}`\n"
            f"Iterations: {row.iteration_count}/{cfg.max_iterations}\n"
            f"Finding-ID: {row.finding_id or 'n/a'}\n"
            f"{cfg.role_ping_on_escalation} bitte manuell prüfen."
        )
        await self._jules_notify_discord_alarm(msg)

        # GitHub Comment (einmalig)
        try:
            body = (
                f"### Jules SecOps — Human Approval Needed\n\n"
                f"**Grund:** `{reason}`\n"
                f"**Iterationen:** {row.iteration_count} of {cfg.max_iterations}\n\n"
                f"Der automatische Review-Workflow wurde gestoppt. Bitte prüft den PR manuell.\n\n"
                f"Letzte bekannte Blockers:\n```json\n{row.last_blockers}\n```\n\n"
                f"---\n*ShadowOps SecOps Workflow - Escalated*"
            )
            owner = "Commandershadow9"  # TODO: dynamisch via PR-Payload, wenn verfügbar
            proc = await asyncio.create_subprocess_exec(
                "gh", "pr", "comment", str(row.pr_number),
                "--repo", f"{owner}/{row.repo}", "--body", body,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=30)
        except Exception:
            logger.exception("[jules] escalation comment failed (ignoring)")

    async def _jules_notify_discord_alarm(self, msg: str) -> None:
        """Postet Alert in den configured escalation_channel."""
        try:
            cfg = self.config.jules_workflow
            channel_name = cfg.escalation_channel
            if hasattr(self.bot, "discord_logger") and self.bot.discord_logger:
                # DiscordChannelLogger hat eine generische send-Methode — Pattern aus anderen Mixins
                await self.bot.discord_logger.send_to_channel(channel_name, msg)
        except Exception:
            logger.exception("[jules] discord alarm failed")
```

**Step 2: Smoke-Test**

```bash
pytest tests/unit/test_jules_workflow_mixin.py -x -v
```

Erwartet: 10 passed (die existing tests schon abdecken, dass `mark_terminal` bei Escalate aufgerufen wird).

**Step 3: Commit**

```bash
git add src/integrations/github_integration/jules_workflow_mixin.py
git commit -m "feat: Jules Human-Escalation mit Discord-Ping und einmaligem PR-Comment"
```

---

### Task 10.2: `_jules_apply_approval` — Label + Ping

**Files:**
- Modify: `src/integrations/github_integration/jules_workflow_mixin.py`

**Step 1: Implementation — ersetze den STUB**

```python
    async def _jules_apply_approval(
        self, owner: str, repo: str, pr_number: int, row: JulesReviewRow
    ) -> None:
        """
        Bei Approval: setze Label claude-approved, pinge Discord.
        Kein Auto-Merge — Shadow merged manuell.
        """
        repo_slug = f"{owner}/{repo}"

        # Label setzen (erstellt wenn fehlt)
        for cmd in (
            ("gh", "pr", "edit", str(pr_number), "--repo", repo_slug,
             "--add-label", "claude-approved"),
        ):
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
                if proc.returncode != 0:
                    err = stderr.decode()[:200]
                    if "not found" in err.lower():
                        # Label fehlt -> anlegen, dann Retry
                        await self._jules_ensure_label(repo_slug, "claude-approved", "0e8a16")
                        proc2 = await asyncio.create_subprocess_exec(
                            *cmd,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                        )
                        await asyncio.wait_for(proc2.communicate(), timeout=30)
                    else:
                        logger.warning(f"[jules] label-add failed: {err}")
            except Exception:
                logger.exception("[jules] label add crashed")

        # Discord Ping
        cfg = self.config.jules_workflow
        msg = (
            f"**Jules PR APPROVED**\n"
            f"Repo: `{repo}` - PR #{pr_number}\n"
            f"Iterations: {row.iteration_count + 1}/{cfg.max_iterations}\n"
            f"Finding-ID: {row.finding_id or 'n/a'}\n"
            f"Link: https://github.com/{repo_slug}/pull/{pr_number}\n"
            f"{cfg.role_ping_on_escalation} — bereit für deinen Merge."
        )
        try:
            if hasattr(self.bot, "discord_logger") and self.bot.discord_logger:
                await self.bot.discord_logger.send_to_channel(
                    cfg.notification_channel, msg
                )
        except Exception:
            logger.exception("[jules] discord approval ping failed")

    async def _jules_ensure_label(
        self, repo_slug: str, name: str, color: str = "0e8a16"
    ) -> None:
        """Erstellt ein Label wenn es nicht existiert (idempotent)."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "gh", "label", "create", name,
                "--repo", repo_slug,
                "--color", color,
                "--description", "Claude security review approved this PR",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=15)
        except Exception:
            pass  # Label exists already — non-fatal
```

**Step 2: Smoke-Test**

```bash
pytest tests/unit/test_jules_workflow_mixin.py -x -v
```

Erwartet: 10 passed.

**Step 3: Commit**

```bash
git add src/integrations/github_integration/jules_workflow_mixin.py
git commit -m "feat: Jules Approval — claude-approved Label + Discord-Ping"
```

---

### Task 10.3: PR-Close-Handler (`merged` vs `abandoned`)

**Files:**
- Modify: `src/integrations/github_integration/jules_workflow_mixin.py`
- Modify: `tests/unit/test_jules_workflow_mixin.py`

**Step 1: Test**

```python
@pytest.mark.asyncio
async def test_handle_pr_close_marks_merged(harness):
    harness._jules_is_jules_pr = AsyncMock(return_value=True)
    harness.jules_state.get = AsyncMock(return_value=_row(status="approved"))
    harness.jules_state.mark_terminal = AsyncMock()
    harness._jules_resolve_finding = AsyncMock()

    payload = {
        "action": "closed",
        "pull_request": {
            "number": 1, "head": {"sha": "x"},
            "user": {"login": "google-labs-jules[bot]"},
            "body": "Fixes #42", "labels": [{"name": "jules"}],
            "merged": True,
        },
        "repository": {"name": "X", "owner": {"login": "o"}},
    }
    await harness.handle_jules_pr_event(payload)
    harness.jules_state.mark_terminal.assert_called_once_with(1, "merged")
    harness._jules_resolve_finding.assert_called_once()


@pytest.mark.asyncio
async def test_handle_pr_close_marks_abandoned(harness):
    harness._jules_is_jules_pr = AsyncMock(return_value=True)
    harness.jules_state.get = AsyncMock(return_value=_row(status="revision_requested"))
    harness.jules_state.mark_terminal = AsyncMock()

    payload = {
        "action": "closed",
        "pull_request": {
            "number": 1, "head": {"sha": "x"},
            "user": {"login": "google-labs-jules[bot]"},
            "body": "", "labels": [{"name": "jules"}],
            "merged": False,
        },
        "repository": {"name": "X", "owner": {"login": "o"}},
    }
    await harness.handle_jules_pr_event(payload)
    harness.jules_state.mark_terminal.assert_called_once_with(1, "abandoned")
```

**Step 2: Erweitere `handle_jules_pr_event` um closed-Branch**

Am Anfang der existing `handle_jules_pr_event`, nach dem `is_jules`-Check, füge ein:

```python
            # PR closed: Terminal-State setzen
            if action == "closed":
                existing = await self.jules_state.get(repo, pr_number)
                if existing and existing.status not in ("merged", "abandoned"):
                    terminal = "merged" if pr.get("merged") else "abandoned"
                    await self.jules_state.mark_terminal(existing.id, terminal)
                    logger.info(f"[jules] {repo}#{pr_number} -> {terminal}")
                    if terminal == "merged" and existing.finding_id:
                        await self._jules_resolve_finding(existing.finding_id)
                return

            # Event-Type für Gate 1
            event_type = f"pull_request:{action}"
            ...
```

Und füge die `_jules_resolve_finding`-Methode hinzu:

```python
    async def _jules_resolve_finding(self, finding_id: int) -> None:
        """Setzt das zugehörige Finding auf 'resolved' in security_analyst.findings."""
        try:
            async with self.jules_state._pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE findings
                    SET status = 'resolved',
                        resolved_at = now()
                    WHERE id = $1
                    """,
                    finding_id,
                )
            logger.info(f"[jules] finding {finding_id} marked resolved")
        except Exception:
            logger.exception("[jules] resolve finding failed")
```

**Step 3: Tests — PASS**

```bash
pytest tests/unit/test_jules_workflow_mixin.py -x -v
```

Erwartet: 12 passed.

**Step 4: Commit**

```bash
git add src/integrations/github_integration/jules_workflow_mixin.py tests/unit/test_jules_workflow_mixin.py
git commit -m "feat: PR-Close-Handler — merged -> finding resolved, abandoned -> terminal"
```

---
