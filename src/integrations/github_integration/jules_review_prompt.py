"""
Jules Review Prompt Builder.

Baut den Claude-Prompt fuer strukturierte PR-Reviews mit Learning-Kontext.
Siehe docs/plans/2026-04-11-jules-secops-workflow-design.md §8.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

MAX_DIFF_CHARS_DEFAULT = 8000


def truncate_diff(diff: str, max_chars: int = MAX_DIFF_CHARS_DEFAULT) -> str:
    if len(diff) <= max_chars:
        return diff
    cut = diff[:max_chars]
    remaining = len(diff) - max_chars
    return cut + f"\n\n[... {remaining} Zeichen abgeschnitten ...]"


def compute_verdict(review: Dict[str, Any]) -> str:
    """
    Deterministische Verdict-Regel — ueberschreibt Claudes verdict.
    approved nur wenn (0 blockers) AND (scope in_scope=True).
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
    knowledge_block = (
        "\n".join(f"- {k}" for k in project_knowledge)
        if project_knowledge else "(noch keine gelernten Konventionen)"
    )
    examples_block = _format_examples(few_shot_examples)
    diff_short = truncate_diff(diff, max_diff_chars)

    return f"""Du bist ein Senior Security-Reviewer. Dein Job: einen Pull-Request von
Jules (Googles AI-Coding-Agent) strukturiert pruefen und Blocker/Suggestions/Nits
klassifizieren.

**Grundregeln:**
- Sei STRIKT bei Security (CVEs, Credentials, Injection, Secrets).
- Sei PRAGMATISCH bei Stil (Nits blockieren NIE das Approval).
- Pruefe STRENG, dass der PR genau das Original-Finding loest und NICHTS anderes.

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

Gib ausschliesslich JSON zurueck (ohne Markdown-Fence, ohne Text davor/danach):

{{
  "verdict": "approved" oder "revision_requested",
  "summary": "1-3 Saetze was der PR macht",
  "blockers": [
    {{
      "title": "Kurze Zusammenfassung",
      "reason": "Warum Blocker",
      "file": "web/package.json",
      "line": 23,
      "severity": "critical|high|medium|low",
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

**Definitionen:**
- **BLOCKER:** Security-Risk, Breaking Change, Out-of-Scope, fehlende Acceptance-Criteria.
- **SUGGESTION:** Code-Qualitaet, Performance (nicht blockierend).
- **NIT:** Reiner Stil (nicht blockierend).
"""


def _format_examples(examples: List[Dict[str, Any]]) -> str:
    if not examples:
        return "(noch keine Beispiele fuer dieses Projekt)"
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
