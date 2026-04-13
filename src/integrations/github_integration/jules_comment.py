"""
Jules Review Comment Body-Builder.
Erzeugt Markdown für den einzigen PR-Comment, der bei jeder Iteration via PATCH editiert wird.
"""
from __future__ import annotations
from typing import Any, Dict, List

COMMENT_MARKER = "### 🛡️ Claude Security Review"


def build_review_comment_body(
    *, review: Dict[str, Any], iteration: int, pr_number: int,
    finding_id: int, max_iterations: int = 5,
) -> str:
    verdict = review.get("verdict", "revision_requested")
    blockers = review.get("blockers", [])
    suggestions = review.get("suggestions", [])
    nits = review.get("nits", [])
    summary = review.get("summary", "")
    scope = review.get("scope_check", {})

    status_line = "**Verdict:** 🟢 APPROVED" if verdict == "approved" else "**Verdict:** 🔴 REVISION REQUESTED"
    scope_line = (
        "**Scope-Check:** ✅ In scope" if scope.get("in_scope")
        else f"**Scope-Check:** ❌ Out of scope — {scope.get('explanation', '')}"
    )

    parts = [
        f"{COMMENT_MARKER} — Iteration {iteration} of {max_iterations}",
        "", status_line, "", f"**Summary:** {summary}", "", "---", "",
    ]

    if blockers:
        parts.append("#### 🔴 Blockers (muss gefixt werden)")
        parts.append("")
        for i, b in enumerate(blockers, 1):
            parts.extend(_format_issue(i, b))

    if suggestions:
        parts.append("#### 🟡 Suggestions (nicht blockierend)")
        parts.append("")
        for i, s in enumerate(suggestions, 1):
            parts.extend(_format_issue(i, s))

    if nits:
        parts.append("#### ⚪ Nits")
        parts.append("")
        for i, n in enumerate(nits, 1):
            parts.extend(_format_issue(i, n))

    if not (blockers or suggestions or nits):
        parts.append("_Keine Anmerkungen._")
        parts.append("")

    parts.extend(["", scope_line])

    # Bei Revision: Jules explizit ansprechen damit er iteriert
    if verdict != "approved" and blockers:
        parts.append("")
        parts.append("---")
        parts.append("")
        parts.append("@google-labs-jules Bitte arbeite die oben genannten **Blocker** ein. "
                      "Suggestions und Nits sind optional.")

    parts.extend(["", "---",
        f"*ShadowOps SecOps Workflow · PR #{pr_number} · Finding #{finding_id}*"])
    return "\n".join(parts)


def _format_issue(idx: int, issue: Dict[str, Any]) -> List[str]:
    title = issue.get("title", "Untitled")
    reason = issue.get("reason", "")
    file_ = issue.get("file", "")
    line_no = issue.get("line")
    severity = issue.get("severity", "medium")
    fix = issue.get("suggested_fix", "")
    loc = f"{file_}:{line_no}" if line_no else file_
    lines = [f"{idx}. **{title}** ({severity})", f"   - Datei: `{loc}`", f"   - Grund: {reason}"]
    if fix:
        lines.append(f"   - Fix: {fix}")
    lines.append("")
    return lines


def is_bot_comment(body: str) -> bool:
    return body.lstrip().startswith(COMMENT_MARKER)
