"""
Jules SecOps Workflow Mixin.
Siehe docs/plans/2026-04-11-jules-secops-workflow-design.md §4-§6.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Dict, Optional

from .jules_gates import (
    ALLOWED_TRIGGERS,
    ReviewDecision,
    check_circuit_breaker,
    gate_cooldown,
    gate_iteration_cap,
    gate_time_cap,
    gate_trigger_whitelist,
)
from .jules_state import JulesReviewRow

logger = logging.getLogger(__name__)


class JulesWorkflowMixin:
    """Mixin fuer GitHubIntegration — Jules SecOps Review-Loop."""

    # ── Task 8.1: Gate-Pipeline ──────────────────────────────────

    async def should_review(
        self, repo: str, pr_number: int, head_sha: str, event_type: str,
    ) -> ReviewDecision:
        """7-Schichten Defense-in-Depth Gate-Pipeline."""
        cfg = self.config.jules_workflow

        # Schicht 1: Feature-Toggle
        if not cfg.enabled:
            return ReviewDecision.skip("feature_disabled")

        # Schicht 2: Trigger-Whitelist
        if (b := gate_trigger_whitelist(event_type)):
            return ReviewDecision.skip(b)

        # Schicht 5: Circuit-Breaker (Redis)
        is_open, count = await check_circuit_breaker(
            self.redis, repo,
            threshold=cfg.circuit_breaker.max_reviews_per_hour,
            ttl_seconds=cfg.circuit_breaker.pause_duration_seconds,
        )
        if is_open:
            logger.warning(f"[jules] Circuit Breaker OPEN {repo} count={count}")
            await self._jules_discord_alarm(
                f"Circuit Breaker OPEN fuer {repo}: {count}/h"
            )
            return ReviewDecision.skip("circuit_breaker_open")

        # Schicht 3: Atomic Lock-Claim + SHA-Dedupe
        row = await self.jules_state.try_claim_review(
            repo, pr_number, head_sha, self.jules_state.process_id,
        )
        if not row:
            return ReviewDecision.skip("already_reviewed_or_locked")

        # Schicht 4: Iteration-Cap
        if (r := gate_iteration_cap(row, cfg.max_iterations)):
            await self._jules_escalate(row, r)
            return ReviewDecision.skip(r)

        # Schicht 6: Time-Cap
        if (r := gate_time_cap(row, cfg.max_hours_per_pr)):
            await self._jules_escalate(row, r)
            return ReviewDecision.skip(r)

        # Schicht 7: Cooldown
        if (r := gate_cooldown(row, cfg.cooldown_seconds)):
            prev = "pending" if row.iteration_count == 0 else "revision_requested"
            await self.jules_state.release_lock(row.id, prev)
            return ReviewDecision.skip(r)

        return ReviewDecision.advance(row)

    # ── Task 8.2: PR-Event Handler ──────────────────────────────────

    async def handle_jules_pr_event(self, payload: Dict[str, Any]) -> None:
        """Eintrittspunkt fuer pull_request Events. Erkennt Jules-PRs und startet Review."""
        try:
            action = payload.get("action", "")
            pr = payload.get("pull_request") or {}
            repo = (payload.get("repository") or {}).get("name", "")
            pr_number = pr.get("number")
            head_sha = (pr.get("head") or {}).get("sha", "")
            if not (repo and pr_number and head_sha):
                return

            # PR closed → terminal state
            if action == "closed":
                if not await self._jules_is_jules_pr(pr, repo):
                    return
                existing = await self.jules_state.get(repo, pr_number)
                if existing and existing.status not in ("merged", "abandoned"):
                    terminal = "merged" if pr.get("merged") else "abandoned"
                    await self.jules_state.mark_terminal(existing.id, terminal)
                    logger.info(f"[jules] {repo}#{pr_number} → {terminal}")
                    if terminal == "merged" and existing.finding_id:
                        await self._jules_resolve_finding(existing.finding_id)
                return

            event_type = f"pull_request:{action}"
            if event_type not in ALLOWED_TRIGGERS:
                return
            if not await self._jules_is_jules_pr(pr, repo):
                return

            logger.info(f"[jules] Jules PR erkannt: {repo}#{pr_number} sha={head_sha[:7]} action={action}")

            # ensure_pending Row
            issue_number = self._jules_extract_fixes_ref(pr.get("body") or "")
            finding_id = await self._jules_lookup_finding(repo, issue_number)
            await self.jules_state.ensure_pending(repo, pr_number, issue_number, finding_id)

            decision = await self.should_review(repo, pr_number, head_sha, event_type)
            if not decision.proceed:
                logger.info(f"[jules] {repo}#{pr_number} skip={decision.reason}")
                return

            await self._jules_run_review(
                repo=repo, pr_number=pr_number, head_sha=head_sha,
                pr_payload=pr, row=decision.row,
            )
        except Exception:
            logger.exception("[jules] handle_jules_pr_event crashed")

    async def _jules_is_jules_pr(self, pr: Dict, repo: str) -> bool:
        """Prueft ob ein PR von Jules stammt (Label, Author oder Body-Marker)."""
        # 1. Label-Check (explizit)
        labels = [l.get("name", "").lower() for l in (pr.get("labels") or [])]
        if "jules" in labels:
            return True
        # 2. Author-Check (Jules Bot-Account)
        author = ((pr.get("user") or {}).get("login") or "").lower()
        if author.startswith("google-labs-jules"):
            return True
        # 3. Body-Marker (Jules erstellt PRs unter User-Account mit Signatur)
        body = (pr.get("body") or "").lower()
        if "pr created automatically by jules" in body:
            return True
        return False

    def _jules_extract_fixes_ref(self, body: str) -> Optional[int]:
        """Extrahiert Issue-Nummer aus 'Fixes #123' im PR-Body."""
        m = re.search(r"(?:Fixes|Closes|Resolves)\s+#(\d+)", body, re.IGNORECASE)
        return int(m.group(1)) if m else None

    async def _jules_lookup_finding(self, repo: str, issue_number: Optional[int]) -> Optional[int]:
        """Sucht Finding-ID anhand der GitHub-Issue-Nummer."""
        if not issue_number:
            return None
        try:
            async with self.jules_state._pool.acquire() as conn:
                rec = await conn.fetchrow(
                    "SELECT id FROM findings WHERE github_issue_number=$1 AND project=$2 LIMIT 1",
                    issue_number, repo)
                return rec["id"] if rec else None
        except Exception:
            return None

    async def _jules_resolve_finding(self, finding_id: int) -> None:
        """Setzt ein Finding auf 'resolved' wenn der Jules-PR gemerged wird."""
        try:
            async with self.jules_state._pool.acquire() as conn:
                await conn.execute(
                    "UPDATE findings SET status='resolved', resolved_at=now() WHERE id=$1",
                    finding_id)
            logger.info(f"[jules] finding {finding_id} resolved")
        except Exception:
            logger.exception("[jules] resolve finding failed")

    # ── Task 8.3: Review-Pipeline + Discord/Escalation ──────────────

    async def _jules_run_review(self, *, repo, pr_number, head_sha, pr_payload, row):
        """Fuehrt AI-Review durch, postet Comment, setzt State."""
        cfg = self.config.jules_workflow
        owner = "Commandershadow9"
        iteration = row.iteration_count + 1
        try:
            if getattr(cfg, "dry_run", False):
                logger.info(f"[jules] DRY-RUN {repo}#{pr_number} iter={iteration}")
                await self.jules_state.release_lock(row.id, "revision_requested")
                return

            diff = await self._jules_fetch_diff(owner, repo, pr_number)
            if not diff:
                await self._jules_escalate(row, "diff_fetch_failed")
                return

            finding_ctx = await self._jules_load_finding(row.finding_id)
            if not finding_ctx:
                finding_ctx = {
                    "title": pr_payload.get("title", ""), "severity": "medium",
                    "description": (pr_payload.get("body") or "")[:2000],
                    "category": "code_fix",
                }

            knowledge, examples = [], []
            try:
                knowledge = await self.jules_learning.fetch_project_knowledge(
                    repo, limit=cfg.project_knowledge_limit)
                examples = await self.jules_learning.fetch_few_shot_examples(
                    repo, limit=cfg.few_shot_examples)
            except Exception as e:
                logger.warning(f"[jules] learning context failed: {e}")

            review = await self.ai_service.review_pr(
                diff=diff, finding_context=finding_ctx, project=repo,
                iteration=iteration, project_knowledge=knowledge,
                few_shot_examples=examples, max_diff_chars=cfg.max_diff_chars)
            if not review:
                await self._jules_escalate(row, "ai_review_failed")
                return

            await self._jules_post_or_edit_comment(
                owner=owner, repo=repo, pr_number=pr_number,
                review=review, row=row, iteration=iteration)
            await self.jules_state.mark_reviewed_sha(row.id, head_sha)
            await self.jules_state.store_review_result(
                row.id, review, review.get("blockers", []), tokens=0)

            # Discord-Embed senden (egal ob approved oder revision)
            await self._jules_discord_review_embed(
                repo=repo, pr_number=pr_number, review=review,
                iteration=iteration, owner=owner,
            )

            if review["verdict"] == "approved":
                await self._jules_apply_approval(owner, repo, pr_number, row)
                await self.jules_state.release_lock(row.id, "approved")
            else:
                await self.jules_state.release_lock(row.id, "revision_requested")
        except Exception:
            logger.exception(f"[jules] review pipeline crashed {repo}#{pr_number}")
            try:
                await self.jules_state.release_lock(row.id, "revision_requested")
            except Exception:
                pass
            await self._jules_discord_alarm(f"Review crashed fuer {repo}#{pr_number}")

    async def _jules_fetch_diff(self, owner, repo, pr) -> Optional[str]:
        """Holt den PR-Diff via gh CLI."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "gh", "pr", "diff", str(pr), "--repo", f"{owner}/{repo}",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            return stdout.decode() if proc.returncode == 0 else None
        except Exception:
            return None

    async def _jules_load_finding(self, finding_id) -> Optional[Dict]:
        """Laedt Finding-Kontext aus der DB fuer den AI-Review-Prompt."""
        if not finding_id:
            return None
        try:
            async with self.jules_state._pool.acquire() as conn:
                rec = await conn.fetchrow(
                    "SELECT title, severity, description, category, cve FROM findings WHERE id=$1",
                    finding_id)
                return dict(rec) if rec else None
        except Exception:
            return None

    async def _jules_post_or_edit_comment(self, *, owner, repo, pr_number, review, row, iteration):
        """Postet oder editiert den Review-Comment auf dem PR."""
        from .jules_comment import build_review_comment_body
        cfg = self.config.jules_workflow
        body = build_review_comment_body(
            review=review, iteration=iteration, pr_number=pr_number,
            finding_id=row.finding_id or 0, max_iterations=cfg.max_iterations)
        repo_slug = f"{owner}/{repo}"
        if row.review_comment_id:
            proc = await asyncio.create_subprocess_exec(
                "gh", "api", f"repos/{repo_slug}/issues/comments/{row.review_comment_id}",
                "--method", "PATCH", "-f", f"body={body}",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            if proc.returncode != 0:
                row.review_comment_id = None
        if not row.review_comment_id:
            proc = await asyncio.create_subprocess_exec(
                "gh", "pr", "comment", str(pr_number), "--repo", repo_slug, "--body", body,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            if proc.returncode == 0:
                url = stdout.decode().strip()
                m = re.search(r"#issuecomment-(\d+)", url)
                if m:
                    await self.jules_state.update_comment_id(row.id, int(m.group(1)))

    async def _jules_apply_approval(self, owner, repo, pr_number, row):
        """Setzt claude-approved Label und sendet Discord-Nachricht."""
        repo_slug = f"{owner}/{repo}"
        try:
            # Label per REST API setzen (gh pr edit hat GraphQL-Bug bei manchen Repos)
            proc = await asyncio.create_subprocess_exec(
                "gh", "api", f"repos/{repo_slug}/issues/{pr_number}/labels",
                "--method", "POST", "-f", "labels[]=claude-approved",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
            if proc.returncode != 0:
                err = stderr.decode()[:200]
                if "not found" in err.lower() or "404" in err:
                    # Label existiert nicht — erstellen
                    await asyncio.create_subprocess_exec(
                        "gh", "api", f"repos/{repo_slug}/labels",
                        "--method", "POST",
                        "-f", "name=claude-approved",
                        "-f", "color=0e8a16",
                        "-f", "description=Approved by Claude Security Review",
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                    # Retry
                    proc2 = await asyncio.create_subprocess_exec(
                        "gh", "api", f"repos/{repo_slug}/issues/{pr_number}/labels",
                        "--method", "POST", "-f", "labels[]=claude-approved",
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                    await asyncio.wait_for(proc2.communicate(), timeout=15)
                    logger.info(f"[jules] Label claude-approved erstellt und gesetzt auf PR #{pr_number}")
                else:
                    logger.warning(f"[jules] Label-Fehler: {err}")
            else:
                logger.info(f"[jules] Label claude-approved gesetzt auf PR #{pr_number}")
        except Exception:
            logger.exception("[jules] label add failed")
        cfg = self.config.jules_workflow
        await self._jules_discord_notify(
            f"✅ **Jules PR APPROVED**\nRepo: `{repo}` · PR #{pr_number}\n"
            f"Iterations: {row.iteration_count + 1}/{cfg.max_iterations}\n"
            f"🔗 https://github.com/{repo_slug}/pull/{pr_number}\n"
            f"{cfg.role_ping_on_escalation} — bereit fuer deinen Merge.")

    async def _jules_escalate(self, row: JulesReviewRow, reason: str) -> None:
        """Markiert PR als eskaliert und sendet Discord-Alarm."""
        await self.jules_state.mark_terminal(row.id, "escalated")
        cfg = self.config.jules_workflow
        await self._jules_discord_alarm(
            f"🚨 **Jules PR Escalation**\nRepo: `{row.repo}` · PR #{row.pr_number}\n"
            f"Grund: `{reason}`\nIterations: {row.iteration_count}/{cfg.max_iterations}\n"
            f"{cfg.role_ping_on_escalation} bitte manuell pruefen.")

    async def _jules_discord_alarm(self, msg: str) -> None:
        """Sendet Alarm in den Escalation-Channel."""
        try:
            cfg = self.config.jules_workflow
            if hasattr(self.bot, "discord_logger") and self.bot.discord_logger:
                await self.bot.discord_logger._send_to_channel(cfg.escalation_channel, msg)
        except Exception:
            logger.exception("[jules] discord alarm failed")

    async def _jules_discord_notify(self, msg: str) -> None:
        """Sendet Benachrichtigung in den Notification-Channel."""
        try:
            cfg = self.config.jules_workflow
            if hasattr(self.bot, "discord_logger") and self.bot.discord_logger:
                await self.bot.discord_logger._send_to_channel(cfg.notification_channel, msg)
        except Exception:
            logger.exception("[jules] discord notify failed")

    async def _jules_discord_review_embed(
        self, *, repo: str, pr_number: int, review: Dict, iteration: int, owner: str
    ) -> None:
        """Sendet ein sauberes Discord-Embed für ein Review-Ergebnis."""
        try:
            import discord
            cfg = self.config.jules_workflow
            dl = getattr(self.bot, "discord_logger", None)
            if not dl:
                return

            verdict = review.get("verdict", "unknown")
            blockers = len(review.get("blockers", []))
            suggestions = len(review.get("suggestions", []))
            nits = len(review.get("nits", []))
            summary = review.get("summary", "")[:200]
            pr_url = f"https://github.com/{owner}/{repo}/pull/{pr_number}"

            if verdict == "approved":
                color = 0x0E8A16  # grün
                title = f"✅ Jules PR #{pr_number} — APPROVED"
            else:
                color = 0xE74C3C  # rot
                title = f"🔴 Jules PR #{pr_number} — REVISION ({blockers} Blocker)"

            embed = discord.Embed(title=title, url=pr_url, color=color)
            embed.add_field(name="Repo", value=f"`{repo}`", inline=True)
            embed.add_field(name="Iteration", value=f"{iteration}/{cfg.max_iterations}", inline=True)
            embed.add_field(name="Findings", value=f"🔴 {blockers} · 🟡 {suggestions} · ⚪ {nits}", inline=True)
            if summary:
                embed.add_field(name="Summary", value=summary, inline=False)
            embed.set_footer(text="ShadowOps SecOps · Jules Workflow")

            channel_name = cfg.notification_channel
            # _send_to_channel braucht message als Positional-Arg
            await dl._send_to_channel(channel_name, message="", embed=embed)
        except Exception:
            logger.exception("[jules] discord review embed failed")
            # Fallback auf Text
            await self._jules_discord_notify(
                f"{'✅' if review.get('verdict')=='approved' else '🔴'} Jules PR #{pr_number} "
                f"({repo}): {review.get('verdict','?')} — {blockers} Blocker"
            )
