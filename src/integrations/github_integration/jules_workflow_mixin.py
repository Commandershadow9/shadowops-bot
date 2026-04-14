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
    """Mixin fuer GitHubIntegration — Jules SecOps Review-Loop.

    Phase 2 Erweiterung (2026-04-14): AgentDetector-Integration.
    Der Detector wird lazy initialisiert und liefert in Phase 1 nur JulesAdapter.
    SEO/Codex-Adapter werden in Phase 2.4 hinzugefuegt.
    """

    # ── Multi-Agent Detector (Phase 2.4: Jules + SEO + Codex) ──────

    def _get_agent_detector(self):
        """Lazy-Init des AgentDetector — Adapter-Liste aus Config.

        Adapter-Toggles kommen aus `cfg.agent_review.adapters.{jules,seo,codex}`.
        Default: nur Jules aktiv (abwaertskompatibel zum Phase-1-Verhalten).
        """
        if getattr(self, "_agent_detector", None) is not None:
            return self._agent_detector

        from .agent_review.detector import AgentDetector
        from .agent_review.adapters.jules import JulesAdapter

        toggles = self._get_adapter_toggles()
        adapters = []

        if toggles.get("jules", True):
            adapters.append(JulesAdapter())
        if toggles.get("seo", False):
            from .agent_review.adapters.seo import SeoAdapter
            adapters.append(SeoAdapter())
        if toggles.get("codex", False):
            from .agent_review.adapters.codex import CodexAdapter
            adapters.append(CodexAdapter())

        logger.info(
            "[agent-review] Detector aktiv mit %d Adapter(n): %s",
            len(adapters), [a.agent_name for a in adapters],
        )
        self._agent_detector = AgentDetector(adapters)
        return self._agent_detector

    def _get_adapter_toggles(self) -> Dict[str, bool]:
        """Liest Adapter-Toggles aus config.agent_review.adapters.

        Fehlende Config → nur Jules aktiv (Safe-Default fuer Rollout).
        """
        cfg = getattr(self.config, "agent_review", None)
        if cfg is None:
            return {"jules": True, "seo": False, "codex": False}
        adapters_cfg = getattr(cfg, "adapters", None)
        if adapters_cfg is None:
            return {"jules": True, "seo": False, "codex": False}
        # Config kann dict oder Namespace sein
        if isinstance(adapters_cfg, dict):
            return {
                "jules": adapters_cfg.get("jules", True),
                "seo": adapters_cfg.get("seo", False),
                "codex": adapters_cfg.get("codex", False),
            }
        return {
            "jules": getattr(adapters_cfg, "jules", True),
            "seo": getattr(adapters_cfg, "seo", False),
            "codex": getattr(adapters_cfg, "codex", False),
        }

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

            # Agent-Detection: Jules (Legacy) + SEO + Codex via Detector
            is_jules_legacy = await self._jules_is_jules_pr(pr, repo)
            detected_adapter = None
            try:
                detected_adapter = self._get_agent_detector().detect(pr)
            except Exception:
                logger.exception("[agent-detector] crashed (continuing with Legacy-Pfad)")

            # Routing-Entscheidung: Legacy-Pfad + Adapter-Extension
            if is_jules_legacy:
                # Klassischer Jules-Pfad, Detector-Adapter wird weitergegeben
                # (kann JulesAdapter oder None sein — beides ok)
                agent_tag = "jules"
            elif detected_adapter is not None:
                # SEO/Codex-PR: neuer Adapter-Pfad, erst wenn agent_review enabled
                if not getattr(self, "_agent_review_enabled", False):
                    logger.debug(
                        "[agent-detector] %s#%d: %s detected, aber agent_review disabled — skip",
                        repo, pr_number, detected_adapter.agent_name,
                    )
                    return
                agent_tag = detected_adapter.agent_name
                logger.info(
                    "[agent-detector] %s#%d → %s (non-jules adapter path)",
                    repo, pr_number, agent_tag,
                )
            else:
                # Weder Jules noch anderer Adapter → ignorieren
                return

            # ensure_pending Row
            issue_number = self._jules_extract_fixes_ref(pr.get("body") or "")
            finding_id = await self._jules_lookup_finding(repo, issue_number)
            await self.jules_state.ensure_pending(
                repo, pr_number, issue_number, finding_id,
            )
            # agent_type-Spalte setzen (Multi-Agent Statistik)
            try:
                await self._update_review_agent_type(repo, pr_number, agent_tag)
            except Exception:
                logger.debug("[agent-detector] agent_type update failed (non-fatal)")

            decision = await self.should_review(repo, pr_number, head_sha, event_type)
            if not decision.proceed:
                logger.info(f"[jules] {repo}#{pr_number} skip={decision.reason}")
                return

            logger.info(
                "[review] %s PR #%d (%s) sha=%s action=%s",
                agent_tag, pr_number, repo, head_sha[:7], action,
            )

            await self._jules_run_review(
                repo=repo, pr_number=pr_number, head_sha=head_sha,
                pr_payload=pr, row=decision.row,
                adapter=detected_adapter,
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

    async def _jules_run_review(
        self, *, repo, pr_number, head_sha, pr_payload, row, adapter=None,
    ):
        """Fuehrt AI-Review durch, postet Comment, setzt State.

        Args:
            adapter: Optional AgentAdapter (JulesAdapter/SeoAdapter/CodexAdapter)
                aus der Detection. Wenn gesetzt, wird adapter.build_prompt()
                statt der hardcoded Jules-Prompt-Builder genutzt und
                adapter.model_preference() uebersteuert die Modell-Wahl.
        """
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

            # Adapter-basierte Prompt-/Modell-Wahl (Phase 2.4 Step 2 + Phase 6)
            prompt_override = None
            model_pref = None
            if adapter is not None and adapter.agent_name != "jules":
                try:
                    prompt_override = adapter.build_prompt(
                        diff=diff, pr_payload=pr_payload,
                        finding_context=finding_ctx,
                        iteration=iteration,
                        few_shot=examples, knowledge=knowledge,
                        project=repo,
                    )
                    model_pref = adapter.model_preference(pr_payload, len(diff))
                    logger.info(
                        "[review] %s: adapter prompt (%d chars), model=%s",
                        adapter.agent_name, len(prompt_override), model_pref[0],
                    )
                except Exception:
                    logger.exception(
                        "[review] adapter.build_prompt crashed for %s — falling back to Jules prompt",
                        adapter.agent_name,
                    )
                    prompt_override = None
                    model_pref = None

            review = await self.ai_service.review_pr(
                diff=diff, finding_context=finding_ctx, project=repo,
                iteration=iteration, project_knowledge=knowledge,
                few_shot_examples=examples, max_diff_chars=cfg.max_diff_chars,
                prompt_override=prompt_override, model_preference=model_pref,
            )
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
                await self._handle_approval_with_adapter(
                    owner=owner, repo=repo, pr_number=pr_number,
                    pr_payload=pr_payload, review=review, row=row,
                )
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

    async def _update_review_agent_type(
        self, repo: str, pr_number: int, agent_type: str,
    ) -> None:
        """Setzt jules_pr_reviews.agent_type fuer Multi-Agent-Statistik.

        Schreibt additiv — failt non-fatal wenn DB-Pool nicht verfuegbar.
        """
        if agent_type == "jules":
            return  # default, nichts zu tun
        state = getattr(self, "jules_state", None)
        if state is None or state._pool is None:
            return
        try:
            async with state._pool.acquire() as conn:
                await conn.execute(
                    """UPDATE jules_pr_reviews SET agent_type=$1
                       WHERE repo=$2 AND pr_number=$3 AND agent_type='jules'""",
                    agent_type, repo, pr_number,
                )
        except Exception:
            logger.debug("[review] agent_type update raced (non-fatal)")

    # ── Adapter-basiertes Approval-Handling (Phase 4) ────────────

    async def _handle_approval_with_adapter(
        self, *, owner, repo, pr_number, pr_payload, review, row,
    ):
        """Entscheidet zwischen Auto-Merge und Label-Only via adapter.merge_policy().

        Fallback-Verhalten: Wenn der Detector keinen Adapter findet oder
        agent_review disabled ist, wird der alte Label-Pfad genutzt (Jules).
        """
        # Adapter via Detector bestimmen
        adapter = None
        try:
            adapter = self._get_agent_detector().detect(pr_payload)
        except Exception:
            logger.exception("[merge-policy] detector crashed — fallback to label-only")

        if adapter is None:
            # Legacy-Pfad: reines Label-Setzen (aktuelles Jules-Verhalten)
            await self._jules_apply_approval(owner, repo, pr_number, row)
            return

        try:
            decision = adapter.merge_policy(review, pr_payload, project=repo)
        except Exception:
            logger.exception(
                "[merge-policy] adapter.merge_policy crashed — fallback to label-only",
            )
            await self._jules_apply_approval(owner, repo, pr_number, row)
            return

        from .agent_review.adapters.base import MergeDecision

        if decision == MergeDecision.AUTO and self._auto_merge_enabled(repo):
            merged = await self._gh_auto_merge_squash(owner, repo, pr_number)
            if merged:
                logger.info(
                    "[merge-policy] %s/%s#%d auto-merged (agent=%s)",
                    owner, repo, pr_number, adapter.agent_name,
                )
                await self._record_auto_merge_outcome(
                    agent=adapter.agent_name, repo=repo, pr_number=pr_number,
                    rule_matched=_summarize_rule(review, adapter),
                )
                return
            logger.warning(
                "[merge-policy] %s/%s#%d auto-merge failed — fallback to label",
                owner, repo, pr_number,
            )

        # MANUAL, BLOCKED, oder Auto-Merge-Fehler -> Label-Pfad
        await self._jules_apply_approval(owner, repo, pr_number, row)

    def _auto_merge_enabled(self, project: str) -> bool:
        """Prueft config.agent_review.auto_merge.enabled + per-project allowed."""
        cfg_ar = getattr(self.config, "agent_review", None)
        if cfg_ar is None:
            return False
        am = getattr(cfg_ar, "auto_merge", None)
        if am is None or not getattr(am, "enabled", False):
            return False
        projects = getattr(am, "projects", None)
        if projects is None:
            return False
        # projects kann dict oder Namespace sein — projekt-spezifisches allowed Flag
        if isinstance(projects, dict):
            p = projects.get(project) or {}
            return bool(p.get("allowed", False)) if isinstance(p, dict) else bool(getattr(p, "allowed", False))
        p = getattr(projects, project, None)
        if p is None:
            return False
        return bool(getattr(p, "allowed", False))

    async def _gh_auto_merge_squash(self, owner, repo, pr_number) -> bool:
        """Squash-merged einen PR via gh CLI. Returns True bei Erfolg."""
        repo_slug = f"{owner}/{repo}"
        try:
            proc = await asyncio.create_subprocess_exec(
                "gh", "pr", "merge", str(pr_number),
                "--repo", repo_slug, "--squash", "--auto",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            if proc.returncode == 0:
                return True
            logger.warning(
                "[auto-merge] gh pr merge failed for %s#%d: %s",
                repo_slug, pr_number, stderr.decode()[:200],
            )
            return False
        except Exception:
            logger.exception("[auto-merge] subprocess crashed")
            return False

    async def _record_auto_merge_outcome(
        self, *, agent: str, repo: str, pr_number: int, rule_matched: str,
    ) -> None:
        """Schreibt Auto-Merge in outcome_tracker (24h-Check spaeter)."""
        tracker = getattr(self, "outcome_tracker", None)
        if tracker is None:
            return
        try:
            await tracker.record_auto_merge(
                agent_type=agent, project=repo,
                repo=repo, pr_number=pr_number, rule_matched=rule_matched,
            )
        except Exception:
            logger.exception("[auto-merge] outcome tracker insert failed")

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


# ── Module-Level Helpers ─────────────────────────────────────

def _summarize_rule(review: Dict, adapter) -> str:
    """Erzeugt ein kurzes Label fuer die rule_matched Spalte im Outcome-Log.

    Beispiele:
    - "jules_approved_0blockers"
    - "seo_content_only_10files"
    - "codex_manual_required"

    Wird spaeter fuer revert_rate_by_rule gruppiert — gleiches Label =
    gleiche Merge-Entscheidungs-Logik.
    """
    agent = getattr(adapter, "agent_name", "unknown")
    blockers = len(review.get("blockers") or [])
    verdict = review.get("verdict", "unknown")
    return f"{agent}_{verdict}_{blockers}b"
