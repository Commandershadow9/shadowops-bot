"""JulesAdapter — wrappt bestehenden Jules-Review-Code im Adapter-Interface.

Phase 1: Verhalten 1:1 wie der bestehende JulesWorkflowMixin. Keine Logik-Aenderung.
Spaeter (Phase 2+): Auto-Merge-Policy + Adapter-spezifische Erweiterungen.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .base import AgentAdapter, AgentDetection, MergeDecision


# Security-Keywords triggern Opus statt Sonnet (siehe ai_engine.review_pr)
SECURITY_KEYWORDS = (
    "xss", "cve", "injection", "dos", "security", "auth", "csrf", "rce",
)


class JulesAdapter(AgentAdapter):
    """Jules-PR-Detection + Review-Configuration."""

    agent_name = "jules"

    def detect(self, pr_payload: Dict[str, Any]) -> AgentDetection:
        """Jules-PRs erkennen via 3 Kriterien (priorisiert).

        1. Label `jules` (sicherste Kennzeichnung)
        2. Author `google-labs-jules[bot]` (Bot-Account, falls genutzt)
        3. Body-Marker (Jules postet meist unter User-Account, daher Body-Check)
        """
        labels = [
            (l.get("name") or "").lower()
            for l in (pr_payload.get("labels") or [])
        ]
        if "jules" in labels:
            return AgentDetection(matched=True, confidence=1.0, metadata={"src": "label"})

        author = ((pr_payload.get("user") or {}).get("login") or "").lower()
        if author.startswith("google-labs-jules"):
            return AgentDetection(matched=True, confidence=1.0, metadata={"src": "bot_author"})

        body = pr_payload.get("body") or ""
        if "PR created automatically by Jules" in body:
            return AgentDetection(matched=True, confidence=0.9, metadata={"src": "body_marker"})
        if "jules.google.com/task/" in body:
            return AgentDetection(matched=True, confidence=0.85, metadata={"src": "body_url"})

        return AgentDetection(matched=False, confidence=0.0)

    def build_prompt(
        self,
        *,
        diff: str,
        pr_payload: Dict[str, Any],
        finding_context: Dict[str, Any],
        iteration: int,
        few_shot: List[Dict[str, Any]],
        knowledge: List[str],
        project: str,
    ) -> str:
        """Reuse der bestehenden Jules-Prompt-Logic (kein Refactor in Phase 1)."""
        from src.integrations.github_integration.jules_review_prompt import (
            build_review_prompt,
        )
        return build_review_prompt(
            finding=finding_context,
            project=project,
            diff=diff,
            iteration=iteration,
            project_knowledge=knowledge,
            few_shot_examples=few_shot,
        )

    def model_preference(
        self, pr_payload: Dict[str, Any], diff_len: int,
    ) -> Tuple[str, str]:
        """Opus fuer Security/komplex, Sonnet fuer Tests/Code-Health.

        Mirror der Logic in ai_engine.review_pr (siehe Live-Erkenntnisse 2026-04-13).
        """
        title = (pr_payload.get("title") or "").lower()
        is_security = any(k in title for k in SECURITY_KEYWORDS)
        if is_security or diff_len > 3000:
            return ("thinking", "standard")  # Opus → Sonnet Fallback
        return ("standard", "thinking")      # Sonnet → Opus Fallback

    def merge_policy(
        self,
        review: Dict[str, Any],
        pr_payload: Dict[str, Any],
        project: str,
    ) -> MergeDecision:
        """Auto-Merge nur fuer triviale Tests-only PRs.

        Default MANUAL — Auto-Merge ist Opt-in pro Pattern.
        """
        # 1. Frozen Project: nie auto
        if project == "sicherheitsdienst":
            return MergeDecision.MANUAL

        # 2. Nicht approved: nie auto
        if review.get("verdict") != "approved":
            return MergeDecision.MANUAL

        # 3. Security-PRs: nie auto (Human-Review Pflicht)
        labels = [
            (l.get("name") or "").lower()
            for l in (pr_payload.get("labels") or [])
        ]
        title = (pr_payload.get("title") or "").lower()
        if "security" in labels or any(k in title for k in SECURITY_KEYWORDS):
            return MergeDecision.MANUAL

        # 4. Tests-only + klein: auto
        paths = pr_payload.get("files_changed_paths") or []
        if paths and all(p.startswith(("tests/", "test/")) for p in paths):
            additions = pr_payload.get("additions") or 0
            if additions < 200:
                return MergeDecision.AUTO

        # Default: manual
        return MergeDecision.MANUAL

    def discord_channel(self, verdict: str) -> str:
        return "🔧-code-fixes"

    def iteration_mention(self) -> Optional[str]:
        """Jules iteriert auf @google-labs-jules Mention."""
        return "@google-labs-jules"
