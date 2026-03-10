"""
CI polling and deployment methods for GitHubIntegration.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Optional

import aiohttp
import discord

logger = logging.getLogger('shadowops')


class CIMixin:

    async def _send_or_update_ci_message(
        self,
        channel: discord.abc.Messageable,
        embed: discord.Embed,
        run_key: str,
        allow_update: bool,
    ) -> None:
        """Send or update a CI notification message for a workflow run."""
        if not self.guild_id or not run_key:
            await channel.send(embed=embed)
            return

        state_key = 'ci_messages'
        ci_messages = self.state_manager.get_value(self.guild_id, state_key, {})
        channel_id = getattr(channel, 'id', None)
        if channel_id is None:
            await channel.send(embed=embed)
            return

        entry = ci_messages.get(run_key, {})
        message_id = entry.get(str(channel_id))

        if message_id and allow_update:
            try:
                if hasattr(channel, "get_partial_message"):
                    message = channel.get_partial_message(int(message_id))
                else:
                    message = await channel.fetch_message(int(message_id))
                await message.edit(embed=embed)
                return
            except Exception as e:
                self.logger.warning(f"⚠️ Konnte CI-Nachricht nicht aktualisieren: {e}")

        sent = await channel.send(embed=embed)
        entry[str(channel_id)] = sent.id
        ci_messages[run_key] = entry
        self.state_manager.set_value(self.guild_id, state_key, ci_messages)

    async def _ensure_ci_polling(self, run_key: str, repo: Dict, run_api_url: Optional[str]) -> None:
        """Start polling for CI updates (every 60s) until completed."""
        if not run_key:
            return
        existing = self._ci_polling_tasks.get(run_key)
        if existing and not existing.done():
            return

        task = asyncio.create_task(self._poll_ci_run(run_key, repo, run_api_url))
        self._ci_polling_tasks[run_key] = task

    def _cancel_ci_polling(self, run_key: str) -> None:
        task = self._ci_polling_tasks.pop(run_key, None)
        if task and not task.done():
            task.cancel()

    async def _poll_ci_run(self, run_key: str, repo: Dict, run_api_url: Optional[str]) -> None:
        """Poll workflow_run status and refresh the CI message."""
        attempts = 0
        max_attempts = 120  # ~2 hours
        try:
            while attempts < max_attempts:
                await asyncio.sleep(60)
                attempts += 1

                if not run_api_url:
                    continue

                workflow = await self._fetch_workflow_run(run_api_url)
                if not workflow:
                    continue

                status = workflow.get('status') or 'unknown'
                action = 'completed' if status == 'completed' else 'in_progress'
                payload = {
                    'workflow_run': workflow,
                    'repository': repo,
                    'action': action,
                    '_from_poll': True,
                }
                await self.handle_workflow_run_event(payload)

                if status == 'completed':
                    break
        except asyncio.CancelledError:
            return
        finally:
            self._ci_polling_tasks.pop(run_key, None)

    async def _fetch_workflow_jobs(self, jobs_url: str) -> Optional[Dict]:
        """Fetch job details for a workflow run."""
        if not jobs_url:
            return None

        headers = {
            "Accept": "application/vnd.github+json",
        }
        token = self._get_github_token()
        if token:
            headers["Authorization"] = f"token {token}"

        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(jobs_url, timeout=20) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        self.logger.warning(
                            f"⚠️ Workflow Jobs konnten nicht geladen werden ({resp.status}): {body}"
                        )
                        return None
                    return await resp.json()
        except Exception as e:
            self.logger.error(f"❌ Fehler beim Laden der Workflow Jobs: {e}", exc_info=True)
            return None

    async def _fetch_workflow_run(self, run_api_url: Optional[str]) -> Optional[Dict]:
        """Fetch workflow_run details from GitHub API."""
        if not run_api_url:
            return None
        headers = {
            "Accept": "application/vnd.github+json",
        }
        token = self._get_github_token()
        if token:
            headers["Authorization"] = f"token {token}"
        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(run_api_url, timeout=20) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        self.logger.warning(
                            f"⚠️ Workflow Run konnte nicht geladen werden ({resp.status}): {body}"
                        )
                        return None
                    return await resp.json()
        except Exception as e:
            self.logger.error(f"❌ Fehler beim Laden des Workflow Runs: {e}", exc_info=True)
            return None

    async def _trigger_deployment(self, repo_name: str, branch: str, commit_sha: str):
        """
        Trigger deployment for a repository

        Args:
            repo_name: Name of the repository
            branch: Branch to deploy
            commit_sha: Commit SHA being deployed
        """
        if not self.deployment_manager:
            self.logger.warning("⚠️ No deployment manager configured")
            return

        # Check if deployment is enabled for this project (case-insensitive lookup)
        project_config = None
        for key in self.config.projects.keys():
            if key.lower() == repo_name.lower():
                project_config = self.config.projects[key]
                break

        if project_config:
            deploy_config = project_config.get('deploy', {})
            if not deploy_config.get('enabled', True):
                self.logger.info(f"⏭️ Deployment disabled for {repo_name} - handled by CI/CD pipeline")
                return

        try:
            self.logger.info(f"🚀 Starting deployment: {repo_name}@{commit_sha}")

            # Notify Discord that deployment is starting
            channel = self.bot.get_channel(self.deployment_channel_id)
            if channel:
                embed = discord.Embed(
                    title="🚀 Deployment Started",
                    description=f"Deploying **{repo_name}** from `{branch}@{commit_sha}`",
                    color=discord.Color.blue(),
                    timestamp=datetime.now(timezone.utc)
                )
                embed.add_field(name="Repository", value=repo_name, inline=True)
                embed.add_field(name="Branch", value=branch, inline=True)
                embed.add_field(name="Commit", value=commit_sha, inline=True)
                await channel.send(embed=embed)

            # Execute deployment
            result = await self.deployment_manager.deploy_project(repo_name, branch)

            # Send result notification
            if result['success']:
                await self._send_deployment_success(repo_name, branch, commit_sha, result)
            else:
                await self._send_deployment_failure(repo_name, branch, commit_sha, result)

        except Exception as e:
            self.logger.error(f"❌ Deployment failed: {e}", exc_info=True)
            await self._send_deployment_error(repo_name, branch, commit_sha, str(e))
