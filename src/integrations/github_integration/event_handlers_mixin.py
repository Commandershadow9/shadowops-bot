"""
GitHub event handler methods for GitHubIntegration.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Optional

import discord

logger = logging.getLogger('shadowops')


class EventHandlersMixin:

    async def handle_push_event(self, payload: Dict):
        """
        Handle push events from GitHub, creating detailed patch notes.
        """
        try:
            repo_name = payload['repository']['name']
            repo_url = payload['repository']['html_url']
            ref = payload['ref']
            branch = ref.split('/')[-1]
            pusher = payload['pusher']['name']

            # Don't process pushes with no commits (e.g., branch creation)
            commits = payload.get('commits', [])
            if not commits or payload.get('created', False) and payload.get('head_commit') is None:
                self.logger.info(f"Skipping push event for {repo_name}/{branch} (no commits).")
                return

            head_commit = payload.get('head_commit', {}).get('id')
            if not head_commit and commits:
                head_commit = commits[-1].get('id')
            normalized_repo = self._normalize_repo_name(repo_name)
            if head_commit and not self._reserve_commit_processing(normalized_repo, branch, head_commit):
                self.logger.info(
                    f"ℹ️ Push für {repo_name}/{branch} bereits verarbeitet ({head_commit[:7]})"
                )
                return

            self.logger.info(
                f"📌 Push to {repo_name}/{branch}: "
                f"{len(commits)} commit(s) by {pusher}"
            )

            # Send detailed patch notes notification
            try:
                await self._send_push_notification(
                    repo_name=normalized_repo,
                    repo_url=repo_url,
                    branch=branch,
                    pusher=pusher,
                    commits=commits
                )
                if head_commit:
                    self._set_last_processed_commit(normalized_repo, branch, head_commit)
            finally:
                if head_commit:
                    self._unmark_commit_inflight(normalized_repo, branch, head_commit)

            # Server Assistant: Security Review bei Push (event-getrieben)
            if hasattr(self.bot, 'server_assistant') and self.bot.server_assistant:
                try:
                    asyncio.create_task(
                        self.bot.server_assistant.review_push_security(
                            repo_name=normalized_repo, commits=commits
                        )
                    )
                except Exception as e:
                    self.logger.debug(f"Push Security Review Fehler: {e}")

            # Auto-deploy on direct push is ARCHITECTURALLY DISABLED for security (PR review gate enforcement).
            # This setting only triggers deployments via Pull Request merges (handle_pr_event).
            if self.auto_deploy_enabled and branch in self.deploy_branches:
                self.logger.warning(
                    f"⚠️ Auto-deploy for direct push to {branch} skipped. "
                    f"Deployment is restricted to PR merges to enforce human review."
                )

        except Exception as e:
            self.logger.error(f"❌ Error handling push event: {e}", exc_info=True)

    async def handle_pr_event(self, payload: Dict):
        """Handle pull request events from GitHub"""
        try:
            action = payload['action']  # opened, closed, synchronize, etc.
            pr = payload['pull_request']

            repo_name = payload['repository']['name']
            pr_number = pr['number']
            pr_title = pr['title']
            pr_author = pr['user']['login']
            pr_url = pr['html_url']
            source_branch = pr['head']['ref']
            target_branch = pr['base']['ref']

            self.logger.info(
                f"🔀 PR #{pr_number} {action} in {repo_name}: "
                f"{source_branch} → {target_branch}"
            )

            # Send Discord notification
            await self._send_pr_notification(
                action, repo_name, pr_number, pr_title,
                pr_author, source_branch, target_branch, pr_url
            )

            # If PR was merged to deployment branch, trigger deploy
            if action == 'closed' and pr.get('merged', False):
                if target_branch in self.deploy_branches and self.auto_deploy_enabled:
                    merge_commit_sha = pr['merge_commit_sha'][:7]
                    self.logger.info(
                        f"🚀 PR merged to {target_branch}, triggering deployment"
                    )
                    await self._trigger_deployment(repo_name, target_branch, merge_commit_sha)

        except Exception as e:
            self.logger.error(f"❌ Error handling PR event: {e}", exc_info=True)

    async def handle_pull_request_event(self, payload: Dict):
        """
        Compatibility wrapper for handling pull request events.

        GitHub sends the event type as `pull_request`, which is routed to
        `handle_pr_event`. This wrapper keeps the name explicit for tests and
        future callers.
        """
        await self.handle_pr_event(payload)

    async def handle_release_event(self, payload: Dict):
        """Handle release events from GitHub"""
        try:
            action = payload['action']  # published, created, deleted, etc.
            release = payload['release']

            repo_name = payload['repository']['name']
            tag_name = release['tag_name']
            release_name = release['name'] or tag_name
            release_author = release['author']['login']
            release_url = release['html_url']
            is_prerelease = release['prerelease']

            self.logger.info(
                f"🏷️ Release {action} in {repo_name}: "
                f"{tag_name} ({'prerelease' if is_prerelease else 'stable'})"
            )

            # Send Discord notification
            await self._send_release_notification(
                action, repo_name, tag_name, release_name,
                release_author, is_prerelease, release_url
            )

        except Exception as e:
            self.logger.error(f"❌ Error handling release event: {e}", exc_info=True)

    async def handle_workflow_run_event(self, payload: Dict):
        """Handle workflow_run events (CI Ergebnisse)."""
        try:
            workflow = payload.get('workflow_run', {}) or {}
            repo = payload.get('repository', {}) or {}
            action = payload.get('action') or workflow.get('action') or ''
            from_poll = payload.get('_from_poll', False)

            repo_name = repo.get('name', 'unknown')
            repo_url = repo.get('html_url')
            run_name = workflow.get('name', 'CI')
            run_path = workflow.get('path') or ''
            conclusion = workflow.get('conclusion') or payload.get('conclusion') or 'unknown'
            status = workflow.get('status') or payload.get('status') or 'unknown'
            branch = workflow.get('head_branch') or payload.get('branch') or '-'
            sha = (workflow.get('head_sha') or payload.get('sha') or '')[:7]
            run_url = workflow.get('html_url') or payload.get('url')
            run_number = workflow.get('run_number')
            run_started_at = workflow.get('run_started_at')
            updated_at = workflow.get('updated_at') or workflow.get('completed_at')
            head_commit = workflow.get('head_commit') or {}
            actor_login = (workflow.get('actor') or {}).get('login')
            event_name = workflow.get('event')
            summary = payload.get('summary')
            failed_jobs = payload.get('failed_jobs') or []
            e2e_tests = payload.get('e2e_tests') or {}
            jobs_url = workflow.get('jobs_url')
            run_id = workflow.get('id') or workflow.get('run_id') or run_number or sha or 'unknown'
            run_api_url = workflow.get('url')
            jobs = []
            jobs_summary = None
            job_details = []
            failed_steps_summary = []
            steps_total = 0
            steps_failed = 0
            steps_skipped = 0
            steps_completed = 0
            jobs_completed = 0
            jobs_total = 0
            active_job_name = None
            active_step_name = None
            is_completed = action == 'completed' or status == 'completed'

            if not summary:
                if is_completed:
                    summary = 'Alle Jobs erfolgreich.' if conclusion == 'success' else 'CI fehlgeschlagen.'
                else:
                    summary = 'CI laeuft...'

            # Project config lookup (case-insensitive)
            project_config = {}
            for key in self.config.projects.keys():
                if key.lower() == repo_name.lower():
                    project_config = self.config.projects[key]
                    break

            # Always filter out notification workflows first
            run_name_lower = str(run_name).lower()
            run_path_lower = str(run_path).lower()
            if 'notify' in run_name_lower or 'notify' in run_path_lower:
                self.logger.info(
                    f"ℹ️ Ignoriere workflow_run '{run_name}' (Notification Workflow)."
                )
                return

            allowed_workflows = project_config.get('ci_workflows')
            if allowed_workflows:
                allowed = False
                for workflow_name in allowed_workflows:
                    name_lower = str(workflow_name).lower()
                    # Exact match or workflow file contains the name
                    if name_lower and (
                        name_lower == run_name_lower
                        or f"/{name_lower}.yml" in run_path_lower
                        or f"/{name_lower}.yaml" in run_path_lower
                    ):
                        allowed = True
                        break
                if not allowed:
                    self.logger.info(
                        f"ℹ️ Ignoriere workflow_run '{run_name}' (nicht in ci_workflows erlaubt)."
                    )
                    return

            allow_jobs_fetch = jobs_url and (is_completed or status in ('in_progress', 'queued'))
            if allow_jobs_fetch:
                jobs_response = await self._fetch_workflow_jobs(jobs_url)
                if jobs_response and isinstance(jobs_response, dict):
                    jobs = jobs_response.get('jobs') or []

            if jobs:
                jobs_total = len(jobs)
                counts = {
                    'success': 0,
                    'failure': 0,
                    'cancelled': 0,
                    'skipped': 0,
                    'neutral': 0,
                    'timed_out': 0,
                    'action_required': 0,
                    'unknown': 0,
                }

                # Track if deployment was successful
                deploy_success = False
                deploy_job_name = None

                for job in jobs:
                    job_conclusion = job.get('conclusion') or job.get('status') or 'unknown'
                    job_status = job.get('status') or 'unknown'
                    job_name = job.get('name', 'Unbekannter Job')

                    if job_status == 'completed':
                        jobs_completed += 1
                    if job_conclusion in counts:
                        counts[job_conclusion] += 1
                    else:
                        counts['unknown'] += 1

                    # Check for deployment success
                    if 'deploy' in job_name.lower() and job_conclusion == 'success':
                        deploy_success = True
                        deploy_job_name = job_name

                    # Only count job as failed if it's completed with a failure conclusion
                    if job_status == 'completed' and job_conclusion in ('failure', 'cancelled', 'timed_out'):
                        if job_name not in failed_jobs:
                            failed_jobs.append(job_name)

                    emoji = '✅'
                    if job_conclusion in ('failure', 'timed_out'):
                        emoji = '❌'
                    elif job_conclusion in ('cancelled', 'action_required'):
                        emoji = '⚠️'
                    elif job_conclusion == 'skipped':
                        emoji = '⏭️'

                    job_name = job.get('name', 'Unbekannter Job')
                    job_url = job.get('html_url') or run_url or ''

                    # Skip "skipped" jobs in details (they just add noise)
                    if job_conclusion != 'skipped':
                        if job_url:
                            job_details.append(f"{emoji} [{job_name}]({job_url}) — {job_conclusion}")
                        else:
                            job_details.append(f"{emoji} {job_name} — {job_conclusion}")

                    steps = job.get('steps') or []
                    failed_steps = []
                    for step in steps:
                        step_status = step.get('status') or 'unknown'
                        step_conclusion = step.get('conclusion') or 'unknown'
                        steps_total += 1
                        if step_status == 'completed':
                            steps_completed += 1
                        if step_conclusion == 'skipped':
                            steps_skipped += 1
                        # Only count steps as failed if they're completed with a failure conclusion
                        if step_status == 'completed' and step_conclusion in ('failure', 'cancelled', 'timed_out'):
                            steps_failed += 1
                            failed_steps.append(step.get('name', 'Unbekannter Schritt'))
                        if not is_completed and step_status == 'in_progress' and not active_step_name:
                            active_step_name = step.get('name', 'Unbekannter Schritt')

                    if not is_completed and job_status == 'in_progress' and not active_job_name:
                        active_job_name = job_name

                    if failed_steps:
                        limited_steps = failed_steps[:4]
                        suffix = '' if len(failed_steps) <= 4 else '…'
                        failed_steps_summary.append(f"{job_name}: {', '.join(limited_steps)}{suffix}")

                total_jobs = len(jobs)
                # Calculate running jobs (in_progress or queued)
                running_jobs = sum(1 for j in jobs if j.get('status') in ('in_progress', 'queued'))

                if is_completed:
                    jobs_summary = (
                        f"Jobs: {total_jobs} | ✅ {counts['success']} | ❌ {counts['failure']} | "
                        f"⚠️ {counts['cancelled'] + counts['action_required']} | ⏭️ {counts['skipped']}"
                    )
                else:
                    # Show running state with progress
                    jobs_summary = (
                        f"Jobs: {jobs_completed}/{total_jobs} fertig | "
                        f"✅ {counts['success']} | 🔄 {running_jobs} laufend | ❌ {counts['failure']}"
                    )

                if steps_total:
                    if is_completed:
                        jobs_summary = f"{jobs_summary}\nSchritte: {steps_total} | ❌ {steps_failed} | ⏭️ {steps_skipped}"
                    else:
                        steps_running = steps_total - steps_completed
                        jobs_summary = f"{jobs_summary}\nSchritte: {steps_completed}/{steps_total} | 🔄 {steps_running} ausstehend | ❌ {steps_failed}"

            project_color = project_config.get('color', 0x3498DB)
            if conclusion == 'success':
                project_color = 0x2ECC71
            elif conclusion == 'failure':
                project_color = 0xE74C3C
            elif conclusion == 'cancelled':
                project_color = 0xF1C40F

            def _parse_ts(value: Optional[str]) -> Optional[datetime]:
                if not value:
                    return None
                try:
                    return datetime.fromisoformat(value.replace('Z', '+00:00'))
                except ValueError:
                    return None

            duration_text = None
            started_dt = _parse_ts(run_started_at)
            if started_dt:
                end_dt = _parse_ts(updated_at) if is_completed else datetime.now(timezone.utc)
                if end_dt:
                    delta = end_dt - started_dt
                    seconds = max(int(delta.total_seconds()), 0)
                    minutes, secs = divmod(seconds, 60)
                    hours, minutes = divmod(minutes, 60)
                    if hours:
                        duration_text = f"{hours}h {minutes}m {secs}s"
                    elif minutes:
                        duration_text = f"{minutes}m {secs}s"
                    else:
                        duration_text = f"{secs}s"

            # Include project name in title for clarity
            title = f"🧪 {repo_name}: CI {run_name}"
            if run_number:
                title = f"{title} #{run_number}"

            embed = discord.Embed(
                title=title,
                url=run_url,
                color=project_color,
                timestamp=datetime.now(timezone.utc),
                description=summary,
            )
            embed.add_field(name="Repository", value=repo_name, inline=True)
            embed.add_field(name="Branch", value=branch, inline=True)
            embed.add_field(name="Commit", value=sha or '-', inline=True)
            embed.add_field(name="Status", value=status, inline=True)
            embed.add_field(name="Ergebnis", value=conclusion, inline=True)
            if event_name:
                embed.add_field(name="Trigger", value=event_name, inline=True)
            if actor_login:
                embed.add_field(name="Actor", value=actor_login, inline=True)
            if duration_text:
                embed.add_field(name="Dauer", value=duration_text, inline=True)
            if run_path:
                embed.add_field(name="Workflow Datei", value=run_path, inline=False)
            if head_commit.get('message'):
                commit_line = str(head_commit.get('message')).splitlines()[0][:200]
                embed.add_field(name="Commit-Message", value=commit_line, inline=False)

            if jobs_summary:
                embed.add_field(name="Tests/Jobs", value=jobs_summary, inline=False)

            # Display Playwright E2E test counts if available
            e2e_total = e2e_tests.get('total', 0)
            if e2e_total > 0:
                e2e_passed = e2e_tests.get('passed', 0)
                e2e_failed = e2e_tests.get('failed', 0)
                e2e_skipped = e2e_tests.get('skipped', 0)
                e2e_text = f"✅ {e2e_passed} bestanden | ❌ {e2e_failed} fehlgeschlagen"
                if e2e_skipped > 0:
                    e2e_text += f" | ⏭️ {e2e_skipped} übersprungen"
                e2e_text += f" | 📊 {e2e_total} gesamt"
                embed.add_field(name="🎭 Playwright E2E Tests", value=e2e_text, inline=False)

            if not is_completed:
                if jobs_total and steps_total:
                    embed.add_field(
                        name="Fortschritt",
                        value=f"Jobs: {jobs_completed}/{jobs_total} | Schritte: {steps_completed}/{steps_total}",
                        inline=False,
                    )
                if active_job_name:
                    embed.add_field(name="Aktueller Job", value=active_job_name, inline=True)
                if active_step_name:
                    embed.add_field(name="Aktueller Schritt", value=active_step_name, inline=True)

            detail_embeds = []
            if job_details:
                details_text = "\n".join(job_details)
                if len(details_text) <= 950:
                    embed.add_field(name="Job-Details", value=details_text, inline=False)
                else:
                    chunk = []
                    chunk_len = 0
                    chunks = []
                    for line in job_details:
                        line_len = len(line) + 1
                        if chunk_len + line_len > 900 and chunk:
                            chunks.append("\n".join(chunk))
                            chunk = []
                            chunk_len = 0
                        chunk.append(line)
                        chunk_len += line_len
                    if chunk:
                        chunks.append("\n".join(chunk))

                    total_parts = len(chunks)
                    for index, text in enumerate(chunks, start=1):
                        detail_embed = discord.Embed(
                            title=f"🧪 CI Job-Details ({index}/{total_parts})",
                            url=run_url,
                            color=project_color,
                            timestamp=datetime.now(timezone.utc),
                        )
                        detail_embed.add_field(name="Job-Details", value=text, inline=False)
                        detail_embeds.append(detail_embed)
            elif jobs_url and is_completed:
                embed.add_field(
                    name="Job-Details",
                    value="Nicht abrufbar (GitHub Token/Rate-Limit oder Repo privat). "
                          "Optional: github.token in der Bot-Config setzen.",
                    inline=False,
                )

            if failed_jobs:
                failed_text = "\n".join(f"• {job}" for job in failed_jobs)
                if len(failed_text) > 1000:
                    failed_text = failed_text[:1000] + "…"
                embed.add_field(name="Fehlgeschlagene Jobs", value=failed_text, inline=False)

            if failed_steps_summary:
                failed_steps_text = "\n".join(f"• {entry}" for entry in failed_steps_summary)
                if len(failed_steps_text) > 1000:
                    failed_steps_text = failed_steps_text[:1000] + "…"
                embed.add_field(name="Fehlgeschlagene Schritte", value=failed_steps_text, inline=False)

            run_key_base = f"{repo_name}:{run_id}"

            # Determine target channel: use project-specific CI channel if set,
            # otherwise fall back to deployment_log (avoid sending to BOTH)
            ci_channel_id = project_config.get('ci_channel_id')
            target_channel = None

            if ci_channel_id:
                target_channel = self.bot.get_channel(ci_channel_id)
                if not target_channel:
                    self.logger.warning(f"⚠️ CI channel {ci_channel_id} nicht gefunden, fallback zu deployment_log")

            if not target_channel:
                target_channel = self.bot.get_channel(self.deployment_channel_id)

            if target_channel:
                await self._send_or_update_ci_message(
                    channel=target_channel,
                    embed=embed,
                    run_key=run_key_base,
                    allow_update=True,
                )
                # Only send detail embeds when CI is completed (not during progress updates)
                if is_completed and detail_embeds:
                    for item in detail_embeds:
                        await target_channel.send(embed=item)

                # Send final deployment success message
                if is_completed and deploy_success and conclusion == 'success':
                    deploy_embed = discord.Embed(
                        title=f"🚀 {repo_name}: Deployment Erfolgreich!",
                        color=0x2ECC71,  # Green
                        timestamp=datetime.now(timezone.utc),
                    )
                    deploy_embed.add_field(name="Repository", value=repo_name, inline=True)
                    deploy_embed.add_field(name="Branch", value=branch, inline=True)
                    if deploy_job_name:
                        deploy_embed.add_field(name="Deploy Job", value=deploy_job_name, inline=True)
                    if duration_text:
                        deploy_embed.add_field(name="Gesamtdauer", value=duration_text, inline=True)
                    if sha:
                        deploy_embed.add_field(name="Commit", value=sha[:8], inline=True)
                    if actor_login:
                        deploy_embed.add_field(name="Deployed von", value=actor_login, inline=True)
                    deploy_embed.set_footer(text="✅ Alle Tests bestanden • Production aktualisiert")
                    await target_channel.send(embed=deploy_embed)
                    self.logger.info(f"✅ Deployment success notification sent for {repo_name}")
            else:
                self.logger.warning("⚠️ Kein Channel für CI Notification gefunden.")

            if not from_poll:
                if is_completed:
                    self._cancel_ci_polling(run_key_base)
                else:
                    await self._ensure_ci_polling(
                        run_key=run_key_base,
                        repo=repo,
                        run_api_url=run_api_url,
                    )

        except Exception as e:
            self.logger.error(f"❌ Error handling workflow_run event: {e}", exc_info=True)
