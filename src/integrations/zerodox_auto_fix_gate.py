"""
Welle 16 P9c (ZERODOX Issue #844): ZERODOX Auto-Fix Pre-Merge-Gate.

Pollt alle 15 min PRs im ZERODOX-Repo mit Auto-Fix-Labels und mergt sie wenn:
- Beide Labels `claude-approved` + `tests-targeted-green` gesetzt sind
- PR < 1000 LOC
- Keine Whitelist-Files berührt (auth, totp, schema, middleware)
- Alle Required Checks grün
- Daily-Rate-Limit nicht überschritten (max 5/Tag)

Bei Verletzung einer Bedingung: Label `escalate-to-human` + Discord-DM an Christian.

Aktivierung via Config-Flag `zerodox.auto_fix_pipeline.enabled` in `config/config.yaml`
(Default: false). Geprüft durch die `enabled`-Property dieser Klasse — kein Slash-Command
nötig. Ein dedizierter Toggle-Command kann später nachgezogen werden (siehe Issue #270).

Memory-Lesson: ZERODOX/feedback_auto_fix_pre_merge_gate.md (post-Soak schreiben).
"""
from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from collections import defaultdict
from datetime import date
from typing import Optional

logger = logging.getLogger(__name__)

ZERODOX_REPO = "Commandershadow9/ZERODOX"
WHITELIST_FILES = {
    "web/src/lib/auth.ts",
    "web/src/lib/totp.ts",
    "web/prisma/schema.prisma",
    "web/src/middleware.ts",
}
LOC_LIMIT = 1000
DAILY_MERGE_RATE_LIMIT = 5
GH_TIMEOUT_S = 30


class AutoFixGate:
    """ZERODOX Auto-Fix-PR Pre-Merge-Gate für shadowops-bot."""

    def __init__(self, config: dict, discord_bot=None, admin_user_id: Optional[int] = None):
        self.config = config
        self.discord_bot = discord_bot
        self.admin_user_id = admin_user_id
        self._merged_today: dict[str, int] = defaultdict(int)

    @property
    def enabled(self) -> bool:
        return bool(
            self.config.get("zerodox", {})
            .get("auto_fix_pipeline", {})
            .get("enabled", False)
        )

    def _gh_json(self, args: list[str]) -> Optional[dict | list]:
        """Wrapper für gh CLI mit JSON-Output + Error-Handling."""
        try:
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=GH_TIMEOUT_S,
                check=False,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            logger.error(f"gh CLI Aufruf failed: {args} → {e}")
            return None

        if result.returncode != 0:
            logger.warning(f"gh CLI exit {result.returncode}: {result.stderr[:200]}")
            return None

        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return None

    def poll_eligible_prs(self) -> list[dict]:
        """gh pr list mit beiden Labels."""
        data = self._gh_json([
            "gh", "pr", "list",
            "--repo", ZERODOX_REPO,
            "--label", "claude-approved",
            "--label", "tests-targeted-green",
            "--state", "open",
            "--json", "number,title,labels,additions,deletions,author",
        ])
        if not isinstance(data, list):
            return []
        return data

    def get_pr_files(self, pr_number: int) -> list[str]:
        """Liste der geänderten File-Pfade in dem PR."""
        data = self._gh_json([
            "gh", "pr", "view", str(pr_number),
            "--repo", ZERODOX_REPO,
            "--json", "files",
        ])
        if not isinstance(data, dict):
            return []
        return [f.get("path", "") for f in data.get("files", [])]

    def get_pr_checks(self, pr_number: int) -> list[dict]:
        """Required Checks Status für PR."""
        data = self._gh_json([
            "gh", "pr", "checks", str(pr_number),
            "--repo", ZERODOX_REPO,
            "--json", "state,name",
        ])
        if not isinstance(data, list):
            return []
        return data

    def check_safety_constraints(self, pr: dict) -> tuple[bool, Optional[str]]:
        """Return (safe_to_merge, escalation_reason)."""
        pr_number = pr["number"]

        # LOC-Limit
        total_loc = (pr.get("additions") or 0) + (pr.get("deletions") or 0)
        if total_loc > LOC_LIMIT:
            return False, f"PR > {LOC_LIMIT} LOC ({total_loc} geändert)"

        # Whitelist-Files
        pr_files = set(self.get_pr_files(pr_number))
        whitelist_violations = pr_files & WHITELIST_FILES
        if whitelist_violations:
            return False, f"Whitelist-Files berührt: {', '.join(whitelist_violations)}"

        # Daily Rate-Limit
        today = date.today().isoformat()
        if self._merged_today[today] >= DAILY_MERGE_RATE_LIMIT:
            return False, (
                f"Daily merge rate-limit erreicht ({DAILY_MERGE_RATE_LIMIT}/Tag)"
            )

        # Required Checks grün?
        checks = self.get_pr_checks(pr_number)
        failing = [
            c for c in checks
            if c.get("state") not in ("SUCCESS", "SKIPPED", None)
        ]
        if failing:
            failing_names = [c.get("name", "?") for c in failing]
            return False, f"Required Checks nicht grün: {failing_names}"

        return True, None

    def attempt_merge(self, pr: dict) -> bool:
        """gh pr merge --squash --auto. Return True on success."""
        pr_number = pr["number"]
        try:
            result = subprocess.run(
                [
                    "gh", "pr", "merge", str(pr_number),
                    "--repo", ZERODOX_REPO,
                    "--squash", "--auto",
                ],
                capture_output=True,
                text=True,
                timeout=GH_TIMEOUT_S,
                check=False,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            logger.error(f"gh pr merge Aufruf failed: {e}")
            return False

        if result.returncode != 0:
            logger.error(
                f"gh pr merge exit {result.returncode}: {result.stderr[:200]}"
            )
            return False

        today = date.today().isoformat()
        self._merged_today[today] += 1
        return True

    async def escalate(self, pr: dict, reason: str) -> None:
        """Label `escalate-to-human` + Discord-DM an Christian."""
        pr_number = pr["number"]
        # Label setzen (best-effort)
        subprocess.run(
            [
                "gh", "pr", "edit", str(pr_number),
                "--repo", ZERODOX_REPO,
                "--add-label", "escalate-to-human",
            ],
            capture_output=True, text=True, timeout=GH_TIMEOUT_S,
        )

        # Discord-DM
        if self.discord_bot and self.admin_user_id:
            try:
                user = await self.discord_bot.fetch_user(self.admin_user_id)
                await user.send(
                    f"🚨 **ZERODOX Auto-Fix-Pipeline Eskalation**\n"
                    f"**PR:** #{pr_number} — {pr.get('title', '?')}\n"
                    f"**Grund:** {reason}\n"
                    f"**Link:** https://github.com/{ZERODOX_REPO}/pull/{pr_number}"
                )
            except Exception as e:
                logger.error(f"Discord-DM Eskalation failed: {e}")

    async def run_poll_cycle(self) -> dict:
        """Aufgerufen alle 15 min vom Scheduler."""
        if not self.enabled:
            logger.debug("auto_fix_pipeline disabled, skip poll")
            return {"enabled": False}

        prs = self.poll_eligible_prs()
        stats = {"checked": len(prs), "merged": 0, "escalated": 0, "skipped": 0}

        for pr in prs:
            safe, reason = self.check_safety_constraints(pr)
            if not safe:
                logger.info(f"PR #{pr['number']} NICHT safe: {reason}")
                await self.escalate(pr, reason)
                stats["escalated"] += 1
                continue

            ok = self.attempt_merge(pr)
            if ok:
                logger.info(f"PR #{pr['number']} Auto-Merged ✅")
                stats["merged"] += 1
            else:
                await self.escalate(pr, "gh pr merge failed (siehe Bot-Logs)")
                stats["escalated"] += 1

        logger.info(f"AutoFixGate-Zyklus: {stats}")
        return stats
