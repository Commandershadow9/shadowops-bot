"""
Local git polling methods for GitHubIntegration.
"""

import asyncio
import logging
from pathlib import Path
from typing import Dict

logger = logging.getLogger('shadowops')


class PollingMixin:

    async def start_local_polling(self):
        """Start local git polling for push detection (fallback)."""
        if not self.local_polling_enabled:
            self.logger.info("ℹ️ Local git polling deaktiviert (config: github.local_polling_enabled=false)")
            return
        if self.local_polling_task and not self.local_polling_task.done():
            return
        self.local_polling_task = asyncio.create_task(self._local_polling_loop())
        self.logger.info(
            f"🔄 Local git polling aktiv (Interval: {self.local_polling_interval}s)"
        )

    async def stop_local_polling(self):
        """Stop local git polling task if running."""
        if not self.local_polling_task:
            return
        self.local_polling_task.cancel()
        try:
            await self.local_polling_task
        except asyncio.CancelledError:
            pass
        self.local_polling_task = None
        self.logger.info("🛑 Local git polling gestoppt")

    async def _local_polling_loop(self):
        """Background loop for local git polling."""
        while True:
            try:
                await self._poll_local_projects()
            except Exception as e:
                self.logger.error(f"❌ Local git polling Fehler: {e}", exc_info=True)
            await asyncio.sleep(self.local_polling_interval)

    async def _poll_local_projects(self):
        """Check configured local repos for new commits and send patch notes."""
        projects = self.config.projects if isinstance(self.config.projects, dict) else {}
        if not projects:
            return

        for project_name, project_config in projects.items():
            if not project_config.get('enabled', True):
                continue

            project_path = project_config.get('path')
            if not project_path:
                continue

            repo_path = Path(project_path)
            if not (repo_path / '.git').exists():
                continue

            branch = self._get_repo_branch(repo_path, project_config)
            # IMMER origin/{deploy_branch} als Referenz — nicht den lokalen Branch
            # Verhindert dass Feature-Branch-Commits als "neue Updates" erkannt werden
            if self.local_polling_fetch:
                self._safe_git_fetch(repo_path)

            head_ref = f"origin/{branch}"
            head_sha = self._get_commit_sha(repo_path, head_ref)
            if not head_sha:
                continue

            normalized_project = self._normalize_repo_name(project_name)
            last_sha = self._get_last_processed_commit(normalized_project, branch)
            if not last_sha and self.local_polling_initial_skip:
                self._set_last_processed_commit(normalized_project, branch, head_sha)
                self.logger.info(
                    f"ℹ️ Local git polling baseline gesetzt für {project_name}@{branch}"
                )
                continue

            if last_sha == head_sha:
                continue
            if self._is_commit_inflight(normalized_project, branch, head_sha):
                continue

            repo_url = (
                project_config.get('repo_url')
                or project_config.get('repository_url')
                or self._get_repo_url(repo_path)
            )
            commits = self._get_commits_between(repo_path, last_sha, head_ref, repo_url)
            if not commits:
                self.logger.info(
                    f"ℹ️ Keine Commits gefunden für {project_name}@{branch} (local polling)"
                )
                self._set_last_processed_commit(normalized_project, branch, head_sha)
                continue

            pusher = commits[-1]['author'].get('name', 'local')
            self.logger.info(
                f"📥 Local git update erkannt: {project_name}@{branch} ({len(commits)} Commit(s))"
            )
            if not self._reserve_commit_processing(normalized_project, branch, head_sha):
                continue
            try:
                await self._send_push_notification(
                    repo_name=normalized_project,
                    repo_url=repo_url or "",
                    branch=branch,
                    pusher=pusher,
                    commits=commits
                )
                self._set_last_processed_commit(normalized_project, branch, head_sha)

                # Server Assistant: Security Review bei lokalem Push
                if hasattr(self.bot, 'server_assistant') and self.bot.server_assistant:
                    try:
                        asyncio.create_task(
                            self.bot.server_assistant.review_push_security(
                                repo_name=normalized_project, commits=commits
                            )
                        )
                    except Exception as e:
                        self.logger.debug(f"Push Security Review Fehler: {e}")
            finally:
                self._unmark_commit_inflight(normalized_project, branch, head_sha)
