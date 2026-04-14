"""SeoAdapter — SEO-Agent PR-Reviews (SEO/GSC/GEO/AEO).

Detection-Patterns:
- Body startet mit `## 🔍 SEO Audit` (eindeutig, conf=1.0)
- Branch beginnt mit `seo/` (eindeutig, conf=0.95)
- Title `[SEO]` Prefix (conf=0.9)
- Title `SEO:` Prefix oder `"SEO Agent"` im Body (conf=0.85)

Merge-Policy: Auto-Merge nur fuer Content-Files (.md/.mdx/blog-data) und
sichere Metadata (sitemap, robots.txt, schema.json), und nur wenn der PR
NICHT zu groesser ist (<50 Files) und KEINE Build-Configs aendert.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .base import AgentAdapter, AgentDetection, MergeDecision


# Sichere Pfade — Auto-Merge erlaubt
SAFE_CONTENT_EXTENSIONS = (".md", ".mdx", ".txt")
SAFE_METADATA_PATHS = (
    "sitemap",          # sitemap.ts, sitemap.xml
    "robots.txt",
    "blog-data",        # blog-data.ts
    "schema.json",
    "feed.xml",
    "/seo/",            # Outreach-Tracker, SEO-Reports
)

# Gefaehrliche Pfade — IMMER manual
DANGEROUS_PATHS = (
    "package.json",
    "package-lock.json",
    "yarn.lock",
    "next.config.",
    "tsconfig.",
    "eslint.config.",
    ".eslintrc",
    "prisma/schema.prisma",
    "layout.tsx",
    "middleware.ts",
    "Dockerfile",
    "docker-compose",
)

MAX_FILES_FOR_AUTO_MERGE = 50


class SeoAdapter(AgentAdapter):
    """SEO-Agent PR-Detection + Multi-Domain Review-Configuration."""

    agent_name = "seo"

    def detect(self, pr_payload: Dict[str, Any]) -> AgentDetection:
        """SEO-Agent PRs erkennen.

        Priorisiert nach Confidence:
        1. Body-Marker `## 🔍 SEO Audit` (1.0 — der eindeutigste Indikator)
        2. Branch `seo/` (0.95)
        3. Title `[SEO]` (0.9)
        4. Title `SEO:` oder Body `SEO Agent` (0.85)
        """
        body = (pr_payload.get("body") or "").strip()
        title = pr_payload.get("title") or ""
        branch = (pr_payload.get("head") or {}).get("ref", "")

        if body.startswith("## 🔍 SEO Audit"):
            return AgentDetection(
                matched=True, confidence=1.0, metadata={"src": "audit_body"},
            )
        if branch.startswith("seo/"):
            return AgentDetection(
                matched=True, confidence=0.95, metadata={"src": "branch"},
            )
        if title.startswith("[SEO]"):
            return AgentDetection(
                matched=True, confidence=0.9, metadata={"src": "title_prefix"},
            )
        if title.startswith("SEO:") or "SEO Agent" in body:
            return AgentDetection(
                matched=True, confidence=0.85, metadata={"src": "title_or_body"},
            )

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
        """Baut SEO-spezifischen Multi-Domain-Prompt."""
        from ..prompts.seo_prompt import build_seo_review_prompt
        return build_seo_review_prompt(
            diff=diff,
            project=project,
            iteration=iteration,
            files_changed=pr_payload.get("files_changed_paths") or [],
            knowledge=knowledge,
            few_shot=few_shot,
        )

    def model_preference(
        self, pr_payload: Dict[str, Any], diff_len: int,
    ) -> Tuple[str, str]:
        """SEO ist meist simpel — Sonnet reicht. Opus als Fallback bei Komplexitaet."""
        # Bei sehr grossen Batches (z.B. neue Seiten-Serie) → Opus fuer Tiefe
        if diff_len > 6000:
            return ("thinking", "standard")
        return ("standard", "thinking")

    def merge_policy(
        self,
        review: Dict[str, Any],
        pr_payload: Dict[str, Any],
        project: str,
    ) -> MergeDecision:
        """Auto-Merge erlaubt fuer reine Content-/Metadata-Aenderungen.

        Stufen:
        1. Project frozen → MANUAL
        2. Nicht approved → MANUAL
        3. Scope-Check failed → MANUAL
        4. Zu viele Files (>50) → MANUAL
        5. Touches DANGEROUS_PATHS → MANUAL
        6. Nur Content/Metadata + alle Pfade safe → AUTO
        7. Default: MANUAL
        """
        if project == "sicherheitsdienst":
            return MergeDecision.MANUAL

        if review.get("verdict") != "approved":
            return MergeDecision.MANUAL

        scope = review.get("scope_check", {}) or {}
        if not scope.get("in_scope", False):
            return MergeDecision.MANUAL

        paths = pr_payload.get("files_changed_paths") or []
        if not paths:
            return MergeDecision.MANUAL

        if len(paths) > MAX_FILES_FOR_AUTO_MERGE:
            return MergeDecision.MANUAL

        # Gefaehrliche Pfade pruefen
        for p in paths:
            if any(d in p for d in DANGEROUS_PATHS):
                return MergeDecision.MANUAL

        # Sichere Pfade-Check (alle muessen safe sein)
        if all(self._is_safe_path(p) for p in paths):
            return MergeDecision.AUTO

        return MergeDecision.MANUAL

    def discord_channel(self, verdict: str) -> str:
        return "seo-fixes"

    def iteration_mention(self) -> Optional[str]:
        """SEO-Agent reagiert NICHT auf PR-Comments — er ist lokaler Code, kein Bot.

        Bei Revisions muss der Agent das naechste Mal beim cron-Lauf den Fix
        einbauen, nicht reaktiv auf Comments. Daher kein @mention.
        """
        return None

    @staticmethod
    def _is_safe_path(path: str) -> bool:
        """Path is safe for auto-merge wenn Content oder Metadata."""
        if any(path.endswith(ext) for ext in SAFE_CONTENT_EXTENSIONS):
            return True
        if any(meta in path for meta in SAFE_METADATA_PATHS):
            return True
        return False
