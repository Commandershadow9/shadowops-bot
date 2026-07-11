"""
CI polling and deployment methods for GitHubIntegration.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Literal, Optional

import aiohttp
import discord

logger = logging.getLogger('shadowops')

# Welle 9.10 (2026-05-11): Welche Conclusions als "Failure" gelten und
# _trigger_deployment abbrechen (kein deploy.sh-Call).
_CI_FAILURE_CONCLUSIONS = frozenset({"failure", "cancelled", "timed_out", "action_required", "startup_failure"})

# Stati, die als "running" gelten — alle anderen Werte fallen durch
# `status != 'completed'` weiter in den Poll-Loop.
_CI_RUNNING_STATI = frozenset({"queued", "in_progress", "requested", "waiting", "pending"})

# ZERODOX#1720: Default-Obergrenze fuer Re-Poll-Runden nach einem erfolgreichen
# Deploy (Schleifen-Schutz). Ueberschreibbar per Projekt via
# deploy.repoll_max_rounds.
_DEFAULT_REPOLL_MAX_ROUNDS = 2


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

    async def _fetch_workflow_runs_for_sha(
        self,
        repo_full_name: str,
        head_sha: str,
    ) -> Optional[Dict]:
        """
        Fetch workflow runs filtered by head_sha via GitHub REST API.

        Welle 9.10 (2026-05-11): Wird von _wait_for_ci_completion genutzt, um
        zu erkennen ob CI fuer den gemergten Commit fertig ist, bevor deploy.sh
        getriggert wird.

        Args:
            repo_full_name: e.g. "Commandershadow9/ZERODOX"
            head_sha: Full 40-char commit SHA (NICHT die 7-char Variante).

        Returns:
            dict from GitHub API, oder None bei Fehler.
        """
        if not repo_full_name or not head_sha:
            return None

        url = f"https://api.github.com/repos/{repo_full_name}/actions/runs?head_sha={head_sha}&per_page=50"
        headers = {"Accept": "application/vnd.github+json"}
        token = self._get_github_token()
        if token:
            headers["Authorization"] = f"token {token}"

        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(url, timeout=20) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        self.logger.warning(
                            f"⚠️ Workflow Runs fuer {repo_full_name}@{head_sha[:7]} "
                            f"konnten nicht geladen werden ({resp.status}): {body[:200]}"
                        )
                        return None
                    return await resp.json()
        except Exception as e:
            self.logger.error(
                f"❌ Fehler beim Laden der Workflow Runs fuer {repo_full_name}@{head_sha[:7]}: {e}",
                exc_info=True,
            )
            return None

    async def _wait_for_ci_completion(
        self,
        repo_full_name: str,
        merged_sha: str,
        workflow_names: List[str],
        max_wait_min: int = 30,
        admin_merge_grace_min: int = 5,
    ) -> Literal["success", "failure", "timeout", "no_workflows"]:
        """
        Wait for required CI workflows on a given commit to complete.

        Welle 9.10 (2026-05-11): Verhindert den Race Condition aus dem
        58h-Vorfall: Bot triggert deploy.sh sofort bei PR-merge → deploy.sh
        Pre-Flight-Gate sieht pending CI auf dem neuen SHA → exit 1.

        Welle 9.16 (Issue #243): Admin-merged PRs triggern oft keinen CI-Run
        (Required-Checks bleiben leer). Wenn nach `admin_merge_grace_min`
        Minuten KEIN relevanter Workflow fuer den SHA gesichtet wurde,
        gilt das als "kein CI vorhanden" → return "no_workflows" (Caller
        deployt direkt). Sobald aber EIN Workflow gesehen wurde, gilt der
        normale Timeout-Pfad (max_wait_min).

        Exponential backoff: 60s → 120s → 240s → cap 300s.

        Args:
            repo_full_name: e.g. "Commandershadow9/ZERODOX"
            merged_sha: FULL 40-char commit SHA des Merge-Commits.
            workflow_names: Liste von erlaubten workflow-Names (z.B. ["Web Quality"]).
                            Match ist case-insensitive substring.
            max_wait_min: Hard-timeout in Minuten. Default 30.
            admin_merge_grace_min: Grace-Period in Minuten, in der NOCH KEIN
                Workflow fuer den SHA erkannt sein muss. Default 5.

        Returns:
            "success"      — alle required Workflows haben conclusion=success
            "failure"      — mind. 1 Workflow ist failed/cancelled/timed_out
            "timeout"      — nach max_wait_min noch nicht alle completed
            "no_workflows" — kein workflow_names konfiguriert ODER admin-merge
                             ohne CI-Trigger erkannt → caller entscheidet
        """
        if not workflow_names:
            self.logger.info(
                f"ℹ️ _wait_for_ci_completion: Keine ci_workflows fuer {repo_full_name} "
                f"konfiguriert — skip wait."
            )
            return "no_workflows"

        if not repo_full_name or not merged_sha or len(merged_sha) < 7:
            self.logger.warning(
                f"⚠️ _wait_for_ci_completion: Ungueltige Args "
                f"repo={repo_full_name!r} sha={merged_sha!r} — skip wait."
            )
            return "no_workflows"

        workflow_names_lower = [str(n).lower().strip() for n in workflow_names if n]
        started_at = time.monotonic()
        deadline = started_at + max_wait_min * 60
        admin_merge_deadline = started_at + max(0, admin_merge_grace_min) * 60
        poll_interval_s = 60
        max_poll_interval_s = 300  # 5 min cap
        saw_any_relevant = False

        self.logger.info(
            f"⏳ Welle 9.10: warte auf CI-Completion fuer {repo_full_name}@{merged_sha[:7]} "
            f"(workflows={workflow_names}, timeout={max_wait_min}min, "
            f"admin_merge_grace={admin_merge_grace_min}min)"
        )

        while time.monotonic() < deadline:
            data = await self._fetch_workflow_runs_for_sha(repo_full_name, merged_sha)
            if data is None:
                # API-Fehler / Rate-Limit — weiter pollen
                await asyncio.sleep(poll_interval_s)
                poll_interval_s = min(poll_interval_s * 2, max_poll_interval_s)
                continue

            all_runs = data.get("workflow_runs") or []

            # Filter auf relevant: name matched workflow_names (case-insensitive, substring)
            relevant = []
            for run in all_runs:
                run_name = str(run.get("name") or "").lower()
                run_path = str(run.get("path") or "").lower()
                for wf_name in workflow_names_lower:
                    if not wf_name:
                        continue
                    if (
                        wf_name == run_name
                        or f"/{wf_name}.yml" in run_path
                        or f"/{wf_name}.yaml" in run_path
                    ):
                        relevant.append(run)
                        break

            if not relevant:
                # Welle 9.16 (Issue #243): admin-merged Detection. Wenn nach
                # admin_merge_grace_min weiter KEIN Workflow fuer den SHA gesichtet
                # wurde, ist es vermutlich ein admin-merge ohne CI-Trigger →
                # weiter deployen (Caller behandelt "no_workflows" als OK).
                if not saw_any_relevant and time.monotonic() >= admin_merge_deadline:
                    self.logger.info(
                        f"ℹ️ _wait_for_ci_completion: nach {admin_merge_grace_min}min "
                        f"keine relevanten Workflows fuer {merged_sha[:7]} sichtbar — "
                        f"admin-merge ohne CI-Trigger vermutet, fahre fort."
                    )
                    return "no_workflows"
                self.logger.info(
                    f"⏳ _wait_for_ci_completion: noch keine relevanten Workflows "
                    f"fuer {merged_sha[:7]} sichtbar — weiter pollen ({poll_interval_s}s)..."
                )
                await asyncio.sleep(poll_interval_s)
                poll_interval_s = min(poll_interval_s * 2, max_poll_interval_s)
                continue

            saw_any_relevant = True

            # Bestimme Status pro workflow_name: den NEUESTEN Run zaehlen
            # (re-runs koennen mehrere Eintraege liefern).
            latest_per_workflow: Dict[str, Dict] = {}
            for run in relevant:
                rname = str(run.get("name") or "").lower()
                # Welle 9.10 Vorsicht: created_at kann fehlen; default leerer string sortiert
                # frueh -> der ECHTE neueste ueberschreibt das.
                created = run.get("created_at") or ""
                existing = latest_per_workflow.get(rname)
                if existing is None or created > (existing.get("created_at") or ""):
                    latest_per_workflow[rname] = run

            # Alle latest_per_workflow durchgehen
            all_completed = True
            any_failed = False
            failed_run = None
            pending_names = []
            for rname, run in latest_per_workflow.items():
                status = str(run.get("status") or "").lower()
                conclusion = str(run.get("conclusion") or "").lower()

                if status != "completed":
                    all_completed = False
                    pending_names.append(rname)
                    continue

                if conclusion in _CI_FAILURE_CONCLUSIONS:
                    any_failed = True
                    failed_run = run
                    break

            if any_failed:
                self.logger.warning(
                    f"❌ _wait_for_ci_completion: CI FAILED fuer {merged_sha[:7]} "
                    f"(workflow={failed_run.get('name')}, conclusion={failed_run.get('conclusion')})"
                )
                return "failure"

            if all_completed:
                self.logger.info(
                    f"✅ _wait_for_ci_completion: alle CI-Workflows fuer {merged_sha[:7]} "
                    f"erfolgreich ({list(latest_per_workflow.keys())})"
                )
                return "success"

            self.logger.info(
                f"⏳ _wait_for_ci_completion: warte weiter auf {pending_names} "
                f"fuer {merged_sha[:7]} (next poll in {poll_interval_s}s)"
            )
            await asyncio.sleep(poll_interval_s)
            poll_interval_s = min(poll_interval_s * 2, max_poll_interval_s)

        self.logger.warning(
            f"⏰ _wait_for_ci_completion: TIMEOUT nach {max_wait_min}min "
            f"fuer {repo_full_name}@{merged_sha[:7]}"
        )
        return "timeout"

    async def _send_ci_wait_alert(
        self,
        outcome: Literal["failure", "timeout"],
        repo_name: str,
        repo_full_name: str,
        branch: str,
        merged_sha: str,
        workflow_names: List[str],
        max_wait_min: int,
    ) -> None:
        """
        Welle 9.10 (2026-05-11): Discord-Alert bei abgebrochenem Deploy.
        Postet in den projekt-spezifischen ci_channel_id (falls vorhanden)
        oder fallback deployment_log channel.
        """
        try:
            # Project config lookup (case-insensitive)
            project_config = {}
            for key in self.config.projects.keys():
                if key.lower() == repo_name.lower():
                    project_config = self.config.projects[key]
                    break

            ci_channel_id = project_config.get('ci_channel_id') if project_config else None
            target_channel = None
            if ci_channel_id:
                target_channel = self.bot.get_channel(ci_channel_id)
            if not target_channel:
                target_channel = self.bot.get_channel(self.deployment_channel_id)
            if not target_channel:
                self.logger.warning(
                    f"⚠️ _send_ci_wait_alert: kein Discord-Channel verfuegbar fuer {repo_name}"
                )
                return

            if outcome == "failure":
                title = f"🛑 {repo_name}: Deploy ABGEBROCHEN — CI rot"
                color = 0xE74C3C
                description = (
                    f"Welle-9.10-Schutz: Mindestens einer der required CI-Workflows "
                    f"({', '.join(workflow_names) or '—'}) hat fuer Commit "
                    f"`{merged_sha[:7]}` mit Failure/Cancelled/TimedOut abgeschlossen.\n\n"
                    f"**deploy.sh wurde NICHT getriggert.** Manueller Check noetig."
                )
            else:  # timeout
                title = f"⏰ {repo_name}: Deploy zurueckgestellt — CI nicht durch"
                color = 0xF1C40F
                description = (
                    f"Welle-9.10-Schutz: CI-Workflows ({', '.join(workflow_names) or '—'}) "
                    f"sind nach {max_wait_min} Minuten fuer Commit `{merged_sha[:7]}` "
                    f"noch nicht alle completed.\n\n"
                    f"**deploy.sh wurde NICHT getriggert.** Sobald CI gruen ist, "
                    f"deploy.sh manuell triggern."
                )

            embed = discord.Embed(
                title=title,
                color=color,
                description=description,
                timestamp=datetime.now(timezone.utc),
            )
            embed.add_field(name="Repository", value=repo_name, inline=True)
            embed.add_field(name="Branch", value=branch, inline=True)
            embed.add_field(name="Commit", value=merged_sha[:7], inline=True)
            if repo_full_name:
                actions_url = f"https://github.com/{repo_full_name}/actions?query=branch%3A{branch}"
                embed.add_field(name="Actions", value=f"[Workflow-Runs]({actions_url})", inline=False)
            embed.set_footer(text="ShadowOps-Bot • Welle 9.10 wait-for-CI")

            await target_channel.send(embed=embed)
        except Exception as e:
            self.logger.error(
                f"❌ _send_ci_wait_alert: Fehler beim Posten: {e}",
                exc_info=True,
            )

    def _project_allows_direct_push(self, repo_name: str) -> bool:
        """True, wenn das Projekt Auto-Deploy bei DIREKTEM Push erlaubt
        (deploy.allow_direct_push: true). Default False -> nur PR-Merge deployt
        (PR-Review-Gate). Opt-in pro Projekt fuer Solo-Operator-Workflows (z.B.
        ZERODOX). Lookup case-insensitive + dash/underscore-tolerant wie in
        _trigger_deployment."""
        normalized = repo_name.lower().replace("-", "_")
        for key in self.config.projects.keys():
            key_lower = key.lower()
            if key_lower == repo_name.lower() or key_lower.replace("-", "_") == normalized:
                deploy_config = self.config.projects[key].get('deploy', {})
                return bool(deploy_config.get('allow_direct_push', False))
        return False

    async def _trigger_deployment(
        self,
        repo_name: str,
        branch: str,
        commit_sha: str,
        repo_full_name: Optional[str] = None,
        full_sha: Optional[str] = None,
        _repoll_round: int = 0,
    ):
        """
        Trigger deployment for a repository

        Welle 9.10 (2026-05-11): Wartet auf CI-Completion bevor deploy.sh
        getriggert wird. Verhindert Race Condition aus dem 58h-Vorfall.

        ZERODOX#1720: Nach einem erfolgreichen (Nicht-Self-)Deploy prueft
        `_repoll_after_deploy`, ob origin/<branch> inzwischen weiter ist als
        der gerade deployte Commit — z.B. weil waehrend des Deploy-Fensters
        ein zweiter Push/PR-Merge durch den "already in progress"-Guard in
        deploy_project() stillschweigend verworfen wurde. Falls ja, wird
        rekursiv ein weiterer Deploy angestossen (Schleifen-Schutz via
        _repoll_round / deploy.repoll_max_rounds).

        Args:
            repo_name: Name of the repository (e.g. "ZERODOX")
            branch: Branch to deploy
            commit_sha: Commit SHA being deployed (typischerweise 7-char Short-SHA fuers Log)
            repo_full_name: e.g. "Commandershadow9/ZERODOX". Wenn None, wird Wait
                            uebersprungen (Backward-Compat fuer alte Caller).
            full_sha: 40-char SHA. Wenn None, wird Wait uebersprungen.
            _repoll_round: Interner Zaehler fuer Re-Poll-Rekursion (ZERODOX#1720).
                           Nicht von aussen setzen — wird nur von
                           _repoll_after_deploy hochgezaehlt.
        """
        if not self.deployment_manager:
            self.logger.warning("⚠️ No deployment manager configured")
            return

        # Check if deployment is enabled for this project (case-insensitive lookup,
        # mit dash/underscore-Fallback fuer GitHub-Repos wie "mayday-sim" ↔ Config-Key
        # "mayday_sim". Vorfall 2026-05-25.)
        project_config = None
        normalized_repo = repo_name.lower().replace("-", "_")
        for key in self.config.projects.keys():
            key_lower = key.lower()
            if key_lower == repo_name.lower() or key_lower.replace("-", "_") == normalized_repo:
                project_config = self.config.projects[key]
                break

        if project_config:
            deploy_config = project_config.get('deploy', {})
            if not deploy_config.get('enabled', True):
                self.logger.info(f"⏭️ Deployment disabled for {repo_name} - handled by CI/CD pipeline")
                return

        # Welle 9.10: Wait-for-CI BEFORE deploy.sh-Call (falls Args vollstaendig).
        # Caller (handle_pr_event) MUSS repo_full_name + full_sha mitgeben um zu profitieren.
        if repo_full_name and full_sha and project_config:
            workflow_names = project_config.get('ci_workflows') or []
            if workflow_names:
                max_wait_min = int(project_config.get('ci_wait_max_min', 30))
                admin_merge_grace_min = int(
                    project_config.get('ci_wait_admin_merge_grace_min', 5)
                )
                outcome = await self._wait_for_ci_completion(
                    repo_full_name=repo_full_name,
                    merged_sha=full_sha,
                    workflow_names=workflow_names,
                    max_wait_min=max_wait_min,
                    admin_merge_grace_min=admin_merge_grace_min,
                )
                if outcome == "failure":
                    await self._send_ci_wait_alert(
                        outcome="failure",
                        repo_name=repo_name,
                        repo_full_name=repo_full_name,
                        branch=branch,
                        merged_sha=full_sha,
                        workflow_names=workflow_names,
                        max_wait_min=max_wait_min,
                    )
                    return
                if outcome == "timeout":
                    await self._send_ci_wait_alert(
                        outcome="timeout",
                        repo_name=repo_name,
                        repo_full_name=repo_full_name,
                        branch=branch,
                        merged_sha=full_sha,
                        workflow_names=workflow_names,
                        max_wait_min=max_wait_min,
                    )
                    return
                # outcome == "success" oder "no_workflows" → weiter unten deployen
            else:
                self.logger.info(
                    f"ℹ️ _trigger_deployment: kein ci_workflows fuer {repo_name} "
                    f"konfiguriert — kein Wait, direkt deployen."
                )
        else:
            self.logger.info(
                f"ℹ️ _trigger_deployment: repo_full_name/full_sha fehlt fuer {repo_name} "
                f"— skip Welle-9.10-Wait (Backward-Compat-Pfad)."
            )

        try:
            self.logger.info(f"🚀 Starting deployment: {repo_name}@{commit_sha}")

            # Self-Deploy: Kein "Started"-Embed (deployment_manager sendet nur 1 Success-Embed)
            is_self_deploy = (repo_name == 'shadowops-bot')

            # "Deployment Started" Embed wird vom deployment_manager gesendet
            # (nicht hier, um Doppelmeldungen zu vermeiden)

            # Execute deployment
            # Alle Discord-Benachrichtigungen (Started, Updates, Success, Failed)
            # werden vom deployment_manager gesendet — nicht hier doppeln
            result = await self.deployment_manager.deploy_project(repo_name, branch)

            if result['success']:
                self.logger.info(f"✅ Deployment erfolgreich: {repo_name}")
                # ZERODOX#1720: Re-Poll — self-deploy hat einen imminenten
                # Prozess-Restart geplant (deployment_manager), daher hier bewusst
                # ausgenommen (kein sinnvoller Folge-Check moeglich/noetig).
                if not is_self_deploy:
                    await self._repoll_after_deploy(
                        repo_name=repo_name,
                        branch=branch,
                        project_config=project_config,
                        repo_full_name=repo_full_name,
                        repoll_round=_repoll_round,
                    )
            else:
                self.logger.warning(f"⚠️ Deployment fehlgeschlagen: {repo_name}")

        except Exception as e:
            self.logger.error(f"❌ Deployment Fehler: {e}", exc_info=True)

    async def _repoll_after_deploy(
        self,
        repo_name: str,
        branch: str,
        project_config: Optional[Dict],
        repo_full_name: Optional[str],
        repoll_round: int,
    ) -> None:
        """
        ZERODOX#1720: Re-Poll nach abgeschlossenem Deploy.

        Prueft, ob origin/<branch> inzwischen weiter ist als der gerade
        deployte Commit (deploy_project() hat das lokale Repo bereits per
        `git pull` aktualisiert, HEAD ist also der deployte Stand). Ursache
        eines solchen Drifts ist typischerweise der `active_deployments`-Guard
        in deployment_manager.deploy_project(): ein zweiter Push/PR-Merge, der
        waehrend eines laufenden Deploys eintrifft, wird dort mit
        {'success': False, 'error': 'Deployment already in progress ...'}
        stillschweigend verworfen (kein Retry, kein Discord-Alert). Der
        Re-Poll heilt diesen Fall, indem er nach Abschluss des ersten Deploys
        prueft, ob origin/<branch> vorausgelaufen ist, und in diesem Fall
        einen weiteren, ganz normalen Deploy ueber _trigger_deployment
        anstoesst (inkl. CI-Wait + Per-SHA-Dedup via _reserve_deploy).

        Schleifen-Schutz: bricht nach `deploy.repoll_max_rounds` (Default
        `_DEFAULT_REPOLL_MAX_ROUNDS`) Runden in Folge ab, statt endlos zu
        re-pollen, falls origin/<branch> kontinuierlich weiterwaechst.
        """
        if not project_config:
            return

        deploy_config = project_config.get('deploy', {})
        if not deploy_config.get('repoll_enabled', True):
            return

        repo_path_raw = project_config.get('path')
        if not repo_path_raw:
            return
        repo_path = Path(repo_path_raw)
        if not repo_path.exists():
            return

        max_rounds = int(deploy_config.get('repoll_max_rounds', _DEFAULT_REPOLL_MAX_ROUNDS))
        if repoll_round >= max_rounds:
            self.logger.warning(
                f"⚠️ Re-Poll-Limit erreicht fuer {repo_name} ({max_rounds} Runde(n)) — "
                f"breche ab. Falls origin/{branch} weiterhin voraus ist, greift "
                f"spaetestens der buildSha-Drift-Watchdog als Backstop."
            )
            return

        if not self._safe_git_fetch(repo_path):
            return

        deployed_sha = self._get_commit_sha(repo_path, 'HEAD')
        remote_sha = self._get_commit_sha(repo_path, f'origin/{branch}')

        if not deployed_sha or not remote_sha or deployed_sha == remote_sha:
            return  # nichts verpasst, oder SHAs nicht ermittelbar

        self.logger.info(
            f"🔁 Re-Poll: origin/{branch} ({remote_sha[:7]}) ist weiter als der "
            f"gerade deployte Stand ({deployed_sha[:7]}) fuer {repo_name} — "
            f"starte Runde {repoll_round + 1}/{max_rounds}."
        )

        # Dieselbe Per-SHA-Dedup wie bei normalen Webhook-Triggern nutzen:
        # falls der normale push/pull_request-Handler diesen SHA parallel
        # bereits reserviert hat, hier NICHT doppelt deployen.
        if not self._reserve_deploy(repo_name, remote_sha):
            self.logger.info(
                f"ℹ️ Re-Poll: {repo_name}@{remote_sha[:7]} bereits durch einen "
                f"anderen Trigger reserviert — kein doppelter Re-Poll-Deploy."
            )
            return

        await self._trigger_deployment(
            repo_name=repo_name,
            branch=branch,
            commit_sha=remote_sha[:7],
            repo_full_name=repo_full_name,
            full_sha=remote_sha,
            _repoll_round=repoll_round + 1,
        )
