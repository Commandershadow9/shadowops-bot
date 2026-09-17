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

# deploy.sh beendet sich mit EX_TEMPFAIL (75), wenn bereits ein anderer Deploy
# die Sperre haelt — ein Wartegrund, kein Fehlschlag. Der Marker taucht so im
# Fehlertext von _run_post_deploy_command auf ("Post-deploy command failed
# (exit=75): ..."). Ueber den Code statt ueber deutsche Meldungstexte zu gehen
# haelt die Erkennung stabil, wenn jemand den Hinweis umformuliert.
_DEPLOY_TEMPFAIL_MARKER = "exit=75"

# ZERODOX#1720: Default-Obergrenze fuer Re-Poll-Runden nach einem erfolgreichen
# Deploy (Schleifen-Schutz). Ueberschreibbar per Projekt via
# deploy.repoll_max_rounds.
_DEFAULT_REPOLL_MAX_ROUNDS = 2

# ZERODOX#1985: Muss mit der Docs-only-Allowlist in ZERODOX/scripts/deploy.sh
# uebereinstimmen. Nur diese Pfade veraendern die Runtime garantiert nicht.
_COMMIT_FILES_PER_PAGE = 100
_COMMIT_FILES_MAX_PAGES = 30


def _paths_are_docs_only(paths: list[str]) -> bool:
    """Return True only for a non-empty, entirely non-runtime path list."""
    normalized_paths = [str(path).strip() for path in paths if str(path).strip()]
    if not normalized_paths:
        return False

    return all(
        path.startswith(("docs/", ".claude/"))
        or ("/" not in path and path.endswith(".md"))
        for path in normalized_paths
    )


def _klassifiziere_workflow_runs(
    all_runs: List[Dict],
    workflow_names_lower: List[str],
) -> tuple:
    """Relevanz-Filterung + "neuester Run pro Workflow-Name"-Klassifikation
    (Welle 9.10) fuer eine Liste von workflow_runs.

    ZERODOX#3328 Task 4 (12.09.2026): Ausgelagert aus der Polling-Schleife in
    `_wait_for_ci_completion`, damit der Tree-SHA-Reuse-Kurzschluss (siehe
    dort) dieselbe Klassifikations-Strenge auf den zweiten Parent-Commit
    anwenden kann statt sie ein zweites Mal zu implementieren — EINE Quelle
    fuer "was zaehlt als vollstaendig gruen", nicht zwei Kopien, die
    auseinanderlaufen koennten.

    Returns:
        (relevant, latest_per_workflow, all_completed, any_failed, failed_run,
         pending_names)
    """
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
        return relevant, {}, False, False, None, []

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

    return relevant, latest_per_workflow, all_completed, any_failed, failed_run, pending_names


class CIMixin:

    def _schedule_ci_success_reconcile(
        self,
        repo_name: str,
        branch: str,
        successful_sha: str,
        repo_full_name: str,
        project_config: Dict,
    ) -> bool:
        """Startet genau einen Reconcile pro Repo/Branch/SHA im Hintergrund."""
        if not successful_sha or not repo_full_name:
            self.logger.warning(
                "⚠️ CI-Reconcile uebersprungen: repo_full_name oder head_sha fehlt "
                f"({repo_name}/{branch})."
            )
            return False

        key = f"{self._normalize_repo_name(repo_name)}:{branch}:{successful_sha}"
        existing = self._ci_reconcile_tasks.get(key)
        if existing and not existing.done():
            self.logger.info(
                f"ℹ️ CI-Reconcile {repo_name}@{successful_sha[:7]} laeuft bereits."
            )
            return False

        task = asyncio.create_task(
            self._reconcile_ci_success_deployment(
                repo_name=repo_name,
                branch=branch,
                successful_sha=successful_sha,
                repo_full_name=repo_full_name,
                project_config=project_config,
            )
        )
        self._ci_reconcile_tasks[key] = task

        def _cleanup(finished: asyncio.Task) -> None:
            if self._ci_reconcile_tasks.get(key) is finished:
                self._ci_reconcile_tasks.pop(key, None)

        task.add_done_callback(_cleanup)
        return True

    async def _fetch_branch_head_sha(
        self,
        repo_full_name: str,
        branch: str,
    ) -> Optional[str]:
        """Liest den aktuellen Branch-HEAD ueber GitHub, ohne den Deploy-Tree anzufassen."""
        if not repo_full_name or not branch:
            return None
        headers = {"Accept": "application/vnd.github+json"}
        token = self._get_github_token()
        if token:
            headers["Authorization"] = f"token {token}"
        url = f"https://api.github.com/repos/{repo_full_name}/commits/{branch}"
        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(url, timeout=20) as resp:
                    if resp.status != 200:
                        self.logger.warning(
                            f"⚠️ Branch-HEAD fuer {repo_full_name}/{branch} nicht lesbar "
                            f"(HTTP {resp.status})."
                        )
                        return None
                    payload = await resp.json()
                    sha = str(payload.get('sha') or '')
                    return sha or None
        except Exception as e:
            self.logger.warning(
                f"⚠️ Branch-HEAD fuer {repo_full_name}/{branch} nicht lesbar: {e}"
            )
            return None

    async def _fetch_live_build_sha(self, health_url: str) -> Optional[str]:
        """Liest buildSha aus dem produktiven Health-Endpoint (fail-open)."""
        if not health_url:
            return None
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(health_url, timeout=15) as resp:
                    if resp.status != 200:
                        self.logger.warning(
                            f"⚠️ CI-Reconcile: Health-Endpoint HTTP {resp.status}."
                        )
                        return None
                    payload = await resp.json()
                    sha = str(payload.get('buildSha') or '')
                    if not sha or sha == 'unknown':
                        return None
                    return sha
        except Exception as e:
            self.logger.warning(f"⚠️ CI-Reconcile: buildSha nicht lesbar: {e}")
            return None

    def _deployment_is_active(self, repo_name: str) -> bool:
        manager = self.deployment_manager
        if not manager:
            return False
        active = getattr(manager, 'active_deployments', {}) or {}
        normalized = repo_name.lower().replace('-', '_')
        return any(
            bool(value)
            for key, value in active.items()
            if key.lower() == repo_name.lower() or key.lower().replace('-', '_') == normalized
        )

    async def _reconcile_ci_success_deployment(
        self,
        repo_name: str,
        branch: str,
        successful_sha: str,
        repo_full_name: str,
        project_config: Dict,
    ) -> None:
        """Deployt einen gruenen main-HEAD nach, falls Produktion hinterherlaeuft.

        Der Reconcile wartet zunaechst auf den normalen PR-/Push-Deploy. Bleibt
        live danach hinter dem Branch-HEAD, wird maximal zweimal ueber die
        normale CI-/Deploy-Pipeline nachgezogen.

        Zieht `main` waehrenddessen weiter (Merge-Serie), gilt der Auftrag dem
        neuen HEAD — nicht mehr dem Commit, fuer den der Reconcile gestartet
        wurde. Bis zum 17.08.2026 wurde hier abgebrochen mit der Begruendung,
        der neuere CI-Lauf sei zustaendig; scheitert dessen Deploy, ist danach
        niemand mehr zustaendig und der Stand bleibt liegen.
        """
        deploy_config = project_config.get('deploy') or {}
        delay_sec = max(0, int(deploy_config.get('ci_success_reconcile_delay_sec', 120)))
        poll_sec = max(1, int(deploy_config.get('ci_success_reconcile_poll_sec', 30)))
        timeout_sec = max(poll_sec, int(deploy_config.get('ci_success_reconcile_timeout_sec', 1800)))
        max_attempts = max(1, int(deploy_config.get('ci_success_reconcile_max_attempts', 2)))
        health_url = (project_config.get('monitor') or {}).get('url') or ''
        deadline = time.monotonic() + timeout_sec
        attempts = 0
        head_wechsel_gemeldet = False

        if delay_sec:
            await asyncio.sleep(delay_sec)

        while time.monotonic() < deadline:
            branch_sha = await self._fetch_branch_head_sha(repo_full_name, branch)
            if not branch_sha:
                await asyncio.sleep(poll_sec)
                continue
            if branch_sha != successful_sha and not head_wechsel_gemeldet:
                # Frueher wurde hier mit "der neuere CI-Lauf ist zustaendig"
                # ausgestiegen. Die Annahme traegt nur, solange jener Lauf auch
                # deployt — scheitert er (Sperre, rote CI, API-Stoerung), ist
                # anschliessend niemand mehr zustaendig. Am 17.08.2026 blieb der
                # Live-Stand deshalb drei Commits hinter main zurueck.
                #
                # Weiterlaufen ist gefahrlos: Die Schleife arbeitet ohnehin mit
                # dem AKTUELLEN branch_sha, _trigger_deployment wartet fuer den
                # auf dessen eigene CI, und _reserve_deploy verhindert, dass
                # zwei Reconciles denselben Stand doppelt ausliefern.
                self.logger.info(
                    f"ℹ️ CI-Reconcile {repo_name}@{successful_sha[:7]}: {branch} steht "
                    f"inzwischen auf {branch_sha[:7]} — nachgezogen wird der aktuelle "
                    f"Stand. Der neuere CI-Lauf darf zuvorkommen (Reservierung)."
                )
                head_wechsel_gemeldet = True

            live_sha = await self._fetch_live_build_sha(health_url)
            if not live_sha:
                await asyncio.sleep(poll_sec)
                continue
            if live_sha == branch_sha:
                self.logger.info(
                    f"✅ CI-Reconcile: {repo_name} live bereits aktuell ({live_sha[:7]})."
                )
                return

            if self._deployment_is_active(repo_name):
                await asyncio.sleep(poll_sec)
                continue

            if not self._reserve_deploy(repo_name, branch_sha):
                # Der normale PR-/Push-Trigger wartet oder deployt noch. Sobald
                # er scheitert, gibt _trigger_deployment die Reservierung frei.
                await asyncio.sleep(poll_sec)
                continue

            self.logger.warning(
                f"🔁 CI-Reconcile: live {live_sha[:7]} != {branch} {branch_sha[:7]} "
                f"nach gruener CI — Nachhol-Deploy {attempts + 1}/{max_attempts}."
            )
            ergebnis = await self._trigger_deployment(
                repo_name=repo_name,
                branch=branch,
                commit_sha=branch_sha[:7],
                repo_full_name=repo_full_name,
                full_sha=branch_sha,
            )
            # Der Reconcile selbst dedupliziert Tasks. Fuer einen zweiten,
            # tatsaechlich noetigen Versuch muss die generische 1h-Reservierung
            # nach Abschluss dieses Versuchs wieder frei sein; vor dem naechsten
            # Deploy werden Branch-HEAD und live buildSha erneut geprueft.
            self._release_deploy(repo_name, branch_sha)
            # "transient" heisst: der Deploy hat gar nicht stattgefunden (belegte
            # Sperre, unlesbare CI-Lage). Das gegen die Versuche zu rechnen, hat
            # am 17.08.2026 einen Stand liegen lassen, den blosses Abwarten
            # ausgeliefert haette. Begrenzt bleibt es trotzdem — ueber die
            # Deadline (timeout_sec), nicht ueber max_attempts.
            if ergebnis != "transient":
                attempts += 1
                if attempts >= max_attempts:
                    break
            await asyncio.sleep(poll_sec)

        self.logger.warning(
            f"⚠️ CI-Reconcile fuer {repo_name}@{successful_sha[:7]} ohne Gleichstand beendet; "
            "der buildSha-Drift-Waechter bleibt als Alarm-Backstop aktiv."
        )

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

    async def _fetch_commit_files(
        self,
        repo_full_name: str,
        head_sha: str,
    ) -> Optional[list[str]]:
        """Load every changed path for a commit, failing closed on API errors."""
        if not repo_full_name or not head_sha:
            return None

        headers = {"Accept": "application/vnd.github+json"}
        token = self._get_github_token()
        if token:
            headers["Authorization"] = f"token {token}"

        changed_paths: list[str] = []
        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                for page in range(1, _COMMIT_FILES_MAX_PAGES + 1):
                    url = (
                        f"https://api.github.com/repos/{repo_full_name}/commits/{head_sha}"
                        f"?per_page={_COMMIT_FILES_PER_PAGE}&page={page}"
                    )
                    async with session.get(url, timeout=20) as resp:
                        if resp.status != 200:
                            body = await resp.text()
                            self.logger.warning(
                                f"⚠️ Commit-Dateien fuer {repo_full_name}@{head_sha[:7]} "
                                f"konnten nicht geladen werden ({resp.status}): {body[:200]}"
                            )
                            return None

                        payload = await resp.json()
                        files = payload.get("files")
                        if not isinstance(files, list):
                            self.logger.warning(
                                f"⚠️ Commit-Dateien fuer {repo_full_name}@{head_sha[:7]} "
                                "fehlen in der GitHub-Antwort."
                            )
                            return None

                        page_paths = [
                            str(item.get("filename") or "").strip()
                            for item in files
                            if isinstance(item, dict)
                        ]
                        if any(not path for path in page_paths) or len(page_paths) != len(files):
                            self.logger.warning(
                                f"⚠️ Commit-Dateien fuer {repo_full_name}@{head_sha[:7]} "
                                "enthalten ungueltige Eintraege."
                            )
                            return None
                        changed_paths.extend(page_paths)

                        if len(files) < _COMMIT_FILES_PER_PAGE:
                            return changed_paths

            self.logger.warning(
                f"⚠️ Commit-Dateiliste fuer {repo_full_name}@{head_sha[:7]} "
                f"ueberschreitet {_COMMIT_FILES_MAX_PAGES * _COMMIT_FILES_PER_PAGE} Dateien."
            )
            return None
        except Exception as e:
            self.logger.error(
                f"❌ Fehler beim Laden der Commit-Dateien fuer "
                f"{repo_full_name}@{head_sha[:7]}: {e}",
                exc_info=True,
            )
            return None

    async def _fetch_commit_tree_info(
        self,
        repo_full_name: str,
        sha: str,
    ) -> Optional[Dict]:
        """Tree-SHA + Parent-SHAs eines einzelnen Commits laden (ZERODOX#3328
        Task 4 — Tree-SHA-Reuse).

        Ein EINZELNER, nicht-paginierter GET-Aufruf gegen denselben Endpunkt
        wie `_fetch_commit_files` (`GET /repos/{repo}/commits/{sha}`), hier
        aber nur `commit.tree.sha` und `parents[].sha` ausgewertet — die
        Datei-Liste selbst wird hier nicht gebraucht.

        Fail-closed wie `_fetch_commit_files`: JEDE Unklarheit (fehlender
        Token nicht erforderlich, aber HTTP-Fehler, fehlendes Feld, falscher
        Typ, Exception) liefert None. Der Aufrufer in `_wait_for_ci_completion`
        behandelt None als "kein Tree-SHA-Reuse moeglich" und faellt auf das
        normale Polling zurueck — niemals als Beleg fuer Gleichheit.

        Returns:
            {"tree_sha": str, "parent_shas": list[str]} oder None bei jedem
            Fehler/jeder Unklarheit.
        """
        if not repo_full_name or not sha:
            return None

        url = f"https://api.github.com/repos/{repo_full_name}/commits/{sha}"
        try:
            # Token-Beschaffung bewusst INNERHALB des try-Blocks (anders als
            # eine fruehere Zwischenfassung): Ein Harness/Subklasse ohne
            # _get_github_token() (z.B. testfremde Alt-Harnesses, die vor
            # ZERODOX#3328 Task 4 entstanden) darf den Aufruf nicht mit einer
            # unbehandelten AttributeError zum Absturz bringen -- fail-closed
            # bedeutet auch hier: JEDE Unklarheit liefert None, nie eine
            # durchschlagende Exception.
            headers = {"Accept": "application/vnd.github+json"}
            token = self._get_github_token()
            if token:
                headers["Authorization"] = f"token {token}"

            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(url, timeout=20) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        self.logger.warning(
                            f"⚠️ Tree-Info fuer {repo_full_name}@{sha[:7]} "
                            f"konnte nicht geladen werden ({resp.status}): {body[:200]}"
                        )
                        return None

                    payload = await resp.json()
                    commit_obj = payload.get("commit")
                    if not isinstance(commit_obj, dict):
                        self.logger.warning(
                            f"⚠️ Tree-Info fuer {repo_full_name}@{sha[:7]} "
                            "fehlt das 'commit'-Feld in der GitHub-Antwort."
                        )
                        return None

                    tree_obj = commit_obj.get("tree")
                    tree_sha = tree_obj.get("sha") if isinstance(tree_obj, dict) else None
                    if not isinstance(tree_sha, str) or not tree_sha:
                        self.logger.warning(
                            f"⚠️ Tree-Info fuer {repo_full_name}@{sha[:7]} "
                            "enthaelt keine gueltige tree.sha."
                        )
                        return None

                    parents = payload.get("parents")
                    if not isinstance(parents, list):
                        self.logger.warning(
                            f"⚠️ Tree-Info fuer {repo_full_name}@{sha[:7]} "
                            "enthaelt kein gueltiges parents-Array."
                        )
                        return None

                    parent_shas = [
                        str(p.get("sha") or "").strip()
                        for p in parents
                        if isinstance(p, dict)
                    ]
                    if any(not p for p in parent_shas) or len(parent_shas) != len(parents):
                        self.logger.warning(
                            f"⚠️ Tree-Info fuer {repo_full_name}@{sha[:7]} "
                            "enthaelt ungueltige parent-Eintraege."
                        )
                        return None

                    return {"tree_sha": tree_sha, "parent_shas": parent_shas}
        except Exception as e:
            self.logger.error(
                f"❌ Fehler beim Laden der Tree-Info fuer "
                f"{repo_full_name}@{sha[:7]}: {e}",
                exc_info=True,
            )
            return None

    async def _fetch_pull_head_shas(
        self,
        repo_full_name: str,
        sha: str,
    ) -> Optional[List[str]]:
        """HEAD-SHAs der Pull Requests laden, zu denen dieser Commit gehoert.

        ZERODOX#3328 Paket A. Ergaenzt `_fetch_commit_tree_info` um den Fall,
        den die Eltern-Kette NICHT abdeckt: **Squash-Merges**.

        Warum das noetig ist -- gemessen auf der main-Linie von ZERODOX ueber
        30 Tage (Stand 17.09.2026):

            387 Merge-Commits  (zwei Eltern, merge^2 == PR-HEAD)
             68 Ein-Eltern-Commits, davon 51 mit Code

        Die 51 sind Squash-Merges. Sie haben KEINEN zweiten Elternteil -- nur
        GitHubs `(#PR)`-Suffix im Betreff. Eine Wiederverwendung, die
        ausschliesslich ueber `merge^2` geht, laesst sie durchfallen; nachdem
        `push: main` aus `web-quality.yml` entfernt ist, existiert fuer sie
        dann ueberhaupt kein CI-Lauf mehr, und der Bot wartet die vollen
        `max_wait_min` ab, um danach fail-closed NICHT auszuliefern. Das waere
        rund 1,7-mal taeglich passiert -- schlimmer als das Problem, das
        Paket A loest.

        Von denselben 51 Commits hatten **51** einen zugeordneten PR und
        **null** waren echte Direkt-Pushes. Dieser Endpunkt ist damit der
        verlaessliche Weg, und der Direkt-Push bleibt bewusst fail-closed:
        ungeprueft auf `main` geschobener Code soll NICHT ohne Lauf raus.

        Fail-closed wie die Schwestermethoden: JEDE Unklarheit (HTTP-Fehler,
        falscher Typ, fehlendes Feld, Exception) liefert None. None heisst fuer
        den Aufrufer "keine Wiederverwendung moeglich" -- niemals "kein PR
        vorhanden". Eine LEERE Liste dagegen heisst nachweislich "kein PR".

        Returns:
            Liste der HEAD-SHAs (moeglicherweise leer) oder None bei Fehler.
        """
        if not repo_full_name or not sha:
            return None

        url = f"https://api.github.com/repos/{repo_full_name}/commits/{sha}/pulls"
        try:
            # Token-Beschaffung innerhalb des try-Blocks -- Begruendung siehe
            # _fetch_commit_tree_info: Ein Harness ohne _get_github_token()
            # darf hier keine durchschlagende AttributeError ausloesen.
            headers = {"Accept": "application/vnd.github+json"}
            token = self._get_github_token()
            if token:
                headers["Authorization"] = f"token {token}"

            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(url, timeout=20) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        self.logger.warning(
                            f"⚠️ Zugehoerige PRs fuer {repo_full_name}@{sha[:7]} "
                            f"nicht ladbar ({resp.status}): {body[:200]}"
                        )
                        return None

                    payload = await resp.json()
                    if not isinstance(payload, list):
                        self.logger.warning(
                            f"⚠️ Unerwartete Antwortform fuer die PRs zu "
                            f"{repo_full_name}@{sha[:7]}: {type(payload).__name__}"
                        )
                        return None

                    head_shas: List[str] = []
                    for eintrag in payload:
                        if not isinstance(eintrag, dict):
                            continue
                        head = eintrag.get("head")
                        if not isinstance(head, dict):
                            continue
                        head_sha = head.get("sha")
                        # Nur gemergte PRs zaehlen. Ein offener PR, der
                        # denselben Commit enthaelt, sagt nichts darueber, ob
                        # DIESER Stand auf main geprueft ist.
                        if isinstance(head_sha, str) and head_sha and eintrag.get("merged_at"):
                            head_shas.append(head_sha)

                    return head_shas
        except Exception as e:
            self.logger.error(
                f"❌ Fehler beim Laden der zugehoerigen PRs fuer "
                f"{repo_full_name}@{sha[:7]}: {e}",
                exc_info=True,
            )
            return None

    async def _checks_sind_vollstaendig_gruen(
        self,
        repo_full_name: str,
        sha: str,
        workflow_names_lower,
    ) -> Optional[int]:
        """Prueft, ob auf `sha` alle geforderten Workflows gruen abgeschlossen sind.

        Returns:
            Zahl der gruenen Workflows, oder None wenn nicht (Fehler, kein
            relevanter Lauf, noch nicht fertig, oder mindestens einer rot).
            Fail-closed: None ist NIE ein Beleg fuer Gruen.
        """
        alle_runs = await self._fetch_workflow_runs_for_sha(repo_full_name, sha)
        if alle_runs is None:
            return None
        runs = alle_runs.get("workflow_runs")
        if not isinstance(runs, list):
            return None

        (
            _relevant,
            latest_per_workflow,
            all_completed,
            any_failed,
            _failed_run,
            _pending_names,
        ) = _klassifiziere_workflow_runs(runs, workflow_names_lower)

        if latest_per_workflow and all_completed and not any_failed:
            return len(latest_per_workflow)
        return None

    async def _wait_for_ci_completion(
        self,
        repo_full_name: str,
        merged_sha: str,
        workflow_names: List[str],
        max_wait_min: int = 30,
        admin_merge_grace_min: int = 5,
        poll_interval_sec: int = 20,
        tree_sha_reuse_enabled: bool = True,
        pr_head_reuse_enabled: bool = True,
        push_commit_shas: Optional[List[str]] = None,
    ) -> Literal[
        "success",
        "failure",
        "timeout",
        "missing",
        "docs_only",
        "no_workflows",
        "api_unavailable",
    ]:
        """
        Wait for required CI workflows on a given commit to complete.

        Welle 9.10 (2026-05-11): Verhindert den Race Condition aus dem
        58h-Vorfall: Bot triggert deploy.sh sofort bei PR-merge → deploy.sh
        Pre-Flight-Gate sieht pending CI auf dem neuen SHA → exit 1.

        ZERODOX#1985: Wenn nach `admin_merge_grace_min` Minuten kein relevanter
        Workflow sichtbar ist, darf nur ein nachweislich reiner Docs-Commit
        ohne Deployment weiterlaufen. Code- oder unklare Commits warten bis
        `max_wait_min` und werden danach fail-closed als "missing" gemeldet.

        ZERODOX#3230 (12.09.2026): Der Docs-only-Check aus #1985 lief bisher
        ERST nach admin_merge_grace_min und nur einmal. Ein docs-only Commit
        wartete dadurch sinnlos die volle Gnadenfrist ab, obwohl die
        geänderten Pfade sofort feststehen und sich während des Wartens
        nicht ändern. Der Check laeuft jetzt VOR Beginn der Polling-Schleife:
        Ist der Commit nachweislich docs-only, kommt "docs_only" zurueck,
        ohne einen einzigen Workflow-Poll abzusetzen. Fail-closed bleibt
        erhalten (None aus _fetch_commit_files gilt NIEMALS als docs-only,
        siehe api_unavailable-Vorfall unten), und die Gnadenfrist für den
        unklaren Fall (Code-Commit ohne bisher sichtbaren Workflow) ist davon
        unberührt.

        Grenze (ZERODOX#3331): Der Vorzug erkennt nur, was _paths_are_docs_only
        hartcodiert als docs-only kennt — eine Kopie der `paths-ignore`-Muster
        aus `web-quality.yml`. Weicht diese Kopie vom Workflow ab, fällt der
        Check das falsche Urteil, ohne dass hier neue Muster ergänzt werden.

        ZERODOX#3328 Task 4 (12.09.2026): Tree-SHA-Reuse fuer Merge-Commits.
        Ist `merged_sha` ein Merge-Commit (zwei Parents) und ist der Tree
        dieses Merge-Commits BIT-IDENTISCH mit dem Tree seines zweiten
        Parents (`parent_shas[1]` — das ist bei GitHubs "Merge pull request"
        immer der PR-HEAD-Commit, nicht der Ziel-Branch), dann hat der Merge
        selbst inhaltlich NICHTS am Baum veraendert: Jede bereits auf dem
        zweiten Parent gelaufene und gruene CI ist damit ebenso gueltig fuer
        den Merge-Commit. In diesem Fall wird die CI des zweiten Parents
        EINMALIG geprueft (kein Poll, kein sleep) — ist sie vollstaendig
        gruen, kommt sofort "success" zurueck, ohne die Polling-Schleife
        ueberhaupt zu betreten. Das spart die volle CI-Laufzeit bei jedem
        Merge, dessen Tree bit-identisch ist (6 von 15 gemessenen Faellen,
        12.09.2026).

        Fail-closed an jeder Stelle: Kein zweiter Parent, ein API-Fehler beim
        Laden der Tree-/Parent-Info, unterschiedliche Tree-SHAs, oder eine
        NICHT vollstaendig gruene CI auf dem zweiten Parent — jeweils faellt
        der Ablauf durch zum normalen Polling (auf dem Merge-Commit selbst).
        Der Kurzschluss liefert NIEMALS direkt "failure" oder irgendein
        anderes Ergebnis ausser "success" — jede Unklarheit bedeutet warten,
        nie raten. Konfigurierbar ueber `tree_sha_reuse_enabled` /
        project_config-Key `ci_wait_tree_sha_reuse` (Default True), weil die
        Herleitung "gleicher Tree = gleiche CI-Gueltigkeit" zwar git-technisch
        korrekt ist, aber im Zweifel abschaltbar bleiben soll.

        Konstantes Poll-Intervall (Default 20s, konfigurierbar ueber
        poll_interval_sec / project_config-Key `ci_wait_poll_interval_sec`).
        Messung 12.09.2026: Ein ZERODOX-Merge-zu-Live-Deploy dauert ~21min,
        davon ~12min CI-Wait im Bot, obwohl der gemessene CI-Lauf selbst nur
        9,4min brauchte — die Differenz war blinde Zeit durch Exponential-
        Backoff (60s → 120s → 240s → cap 300s, VORHER). Die CI-Laufzeit ist
        gut bekannt (8-17min, Median 16min) — für einen derart vorhersagbaren
        Vorgang vergrößert Backoff die Blindzeit genau dann, wenn der Lauf
        typischerweise fertig wird. Ein 16min-Lauf kostet bei 20s-Intervall nur
        ~48 Requests gegen GitHubs 5000/h-Limit. Betrifft NUR diese Schleife
        (kritischer Merge-zu-Deploy-Pfad) — die zweite Warteschleife im
        Reconcile-Codepfad (ci_success_reconcile_*-Konfig) ist ein
        nachträglicher Backstop, kein kritischer Pfad, und bleibt bewusst
        unverändert.

        Args:
            repo_full_name: e.g. "Commandershadow9/ZERODOX"
            merged_sha: FULL 40-char commit SHA des Merge-Commits.
            workflow_names: Liste von erlaubten workflow-Names (z.B. ["Web Quality"]).
                            Match ist case-insensitive substring.
            max_wait_min: Hard-timeout in Minuten. Default 30.
            admin_merge_grace_min: Grace-Period in Minuten, in der NOCH KEIN
                Workflow fuer den SHA erkannt sein muss. Default 5.
            poll_interval_sec: Konstantes Poll-Intervall in Sekunden (kein
                Backoff mehr). Default 20.
            tree_sha_reuse_enabled: Tree-SHA-Reuse (ZERODOX#3328 Task 4,
                project_config-Key `ci_wait_tree_sha_reuse`) an/aus. Default
                True. Siehe Docstring-Abschnitt weiter unten.
            pr_head_reuse_enabled: PR-Head-Wiederverwendung ohne
                Tree-Bedingung (ZERODOX#3328 Paket A, project_config-Key
                `ci_wait_pr_head_reuse`) an/aus. Default True.

                ⚠️ Dieser Schalter gehoert zu `push: main` in
                `web-quality.yml`. Wer ihn auf False setzt, WAEHREND dort kein
                Merge-Lauf mehr startet, bringt #3230 zurueck: Der Bot wartet
                dann `max_wait_min` auf einen Workflow, den niemand mehr
                ausloest, und verwirft solange jeden Folge-Auftrag. Beides
                gehoert zusammen zurueckgenommen oder gar nicht.

        Returns:
            "success"      — alle required Workflows haben conclusion=success
            "failure"      — mind. 1 Workflow ist failed/cancelled/timed_out
            "timeout"      — gesichtete Workflows nach max_wait_min nicht completed
            "missing"      — nach max_wait_min kein relevanter Workflow sichtbar
            "docs_only"    — nur nicht-deploy-relevante Pfade geaendert
            "no_workflows" — kein workflow_names konfiguriert → caller entscheidet
            "api_unavailable" — die GitHub-API war waehrend der gesamten Frist
                nicht lesbar; ueber die CI ist NICHTS bekannt

        Zu "api_unavailable" (17.08.2026): Bei einem GitHub-Ausfall lieferte
        jede Abfrage 404, und der Ausgang lautete trotzdem "missing" — also
        "fuer diesen Commit existiert kein Workflow". Die CI war laengst
        gelaufen. Der Unterschied ist nicht kosmetisch: "missing" gilt als
        endgueltiges Urteil und loest einen entsprechenden Alert aus, waehrend
        eine Stoerung vorbeigeht und einen neuen Versuch verdient.
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
        # 12.09.2026: konstantes Intervall statt Backoff (Begründung im
        # Docstring oben) — poll_interval_s wird danach nicht mehr verändert.
        poll_interval_s = max(1, int(poll_interval_sec))
        saw_any_relevant = False
        # 17.08.2026: Wurde die API waehrend der gesamten Frist nie gelesen, ist
        # die CI-Lage unbekannt — das darf nicht als "kein Workflow vorhanden"
        # aus der Schleife kommen. Gezaehlt werden beide Seiten, damit sich der
        # Unterschied am Ende belegen laesst statt geraten werden zu muessen.
        api_fehler_runden = 0
        api_erfolg_runden = 0

        self.logger.info(
            f"⏳ Welle 9.10: warte auf CI-Completion fuer {repo_full_name}@{merged_sha[:7]} "
            f"(workflows={workflow_names}, timeout={max_wait_min}min, "
            f"admin_merge_grace={admin_merge_grace_min}min)"
        )

        # ZERODOX#3230: Docs-only-Check VOR der Schleife, nicht erst nach
        # admin_merge_grace_min. Die geänderten Pfade eines Commits stehen
        # sofort fest und ändern sich während des Wartens nicht — ein
        # docs-only Commit muss deshalb keinen einzigen Workflow-Poll
        # abwarten. Fail-closed bleibt: `changed_paths is None` (API-Störung,
        # siehe api_unavailable-Vorfall im Docstring) gilt NIEMALS als
        # docs-only, egal was danach passiert.
        # ZERODOX#3391 (15.09.2026): Ein PR-Merge bringt genau EINEN Commit mit,
        # ein direkter Push auf main dagegen beliebig viele. Bis hierher wurde
        # immer nur `merged_sha` (der HEAD) geprüft — trug ein Push einen
        # Code-Commit und danach einen Docs-Commit, galt der GESAMTE Push als
        # docs-only. Am 15.09. wurde so CSS-Code mehrerer Produktivseiten mit
        # `--skip-e2e` ausgeliefert. `push_commit_shas` reicht deshalb die
        # vollständige Commit-Liste des Push-Events durch; ohne sie (PR-Pfad)
        # bleibt das Verhalten unverändert.
        docs_only_kandidaten = [sha for sha in (push_commit_shas or []) if sha] or [merged_sha]
        # Obergrenze: Ein Push mit sehr vielen Commits ist nie „nur
        # Dokumentation" und würde je Commit einen API-Aufruf kosten. Über der
        # Grenze fail-closed KEIN docs-only — ein überflüssiger Deploy mit
        # voller CI ist harmlos, ungetestet ausgelieferter Code nicht.
        _DOCS_ONLY_MAX_COMMITS = 20
        if len(docs_only_kandidaten) > _DOCS_ONLY_MAX_COMMITS:
            self.logger.info(
                f"ℹ️ _wait_for_ci_completion: Push mit {len(docs_only_kandidaten)} Commits "
                f"(> {_DOCS_ONLY_MAX_COMMITS}) — Docs-only-Kurzschluss übersprungen (fail-closed)."
            )
            precomputed_changed_paths = None
        else:
            precomputed_changed_paths = []
            for kandidat_sha in docs_only_kandidaten:
                pfade = await self._fetch_commit_files(repo_full_name, kandidat_sha)
                if pfade is None:
                    # Fail-closed über die GANZE Liste: Ein einzelner nicht
                    # lesbarer Commit macht den Push unbekannt, nicht
                    # „die übrigen waren ja docs-only".
                    precomputed_changed_paths = None
                    break
                precomputed_changed_paths.extend(pfade)

        if precomputed_changed_paths is not None and _paths_are_docs_only(precomputed_changed_paths):
            commit_hinweis = (
                f"{merged_sha[:7]}"
                if len(docs_only_kandidaten) == 1
                else f"{len(docs_only_kandidaten)} Commits bis {merged_sha[:7]}"
            )
            self.logger.info(
                f"ℹ️ _wait_for_ci_completion: Docs-only-Commit "
                f"{commit_hinweis} mit {len(precomputed_changed_paths)} Datei(en) erkannt — "
                "kein Runtime-Deployment nötig (Check vor der Polling-Schleife)."
            )
            return "docs_only"

        # ZERODOX#3328 Task 4: Tree-SHA-Reuse fuer Merge-Commits (voller
        # Hintergrund im Docstring oben). Muss NACH dem Docs-only-Check und
        # VOR der Polling-Schleife laufen: Ist der Tree des Merge-Commits
        # bit-identisch mit dem Tree seines zweiten Parents (merge^2, bei
        # GitHubs "Merge pull request" immer die Spitze des gemergten
        # Branches), dann hat der Merge selbst nichts am Baum veraendert —
        # jede bereits auf merge^2 gelaufene, gruene CI ist ebenso gueltig
        # fuer den Merge-Commit. Fail-closed an jeder Stelle: kein zweiter
        # Parent, ein API-Fehler, unterschiedliche Trees oder eine nicht
        # vollstaendig gruene CI auf merge^2 fallen jeweils durch zum
        # normalen Polling unten — der Kurzschluss liefert NIEMALS direkt
        # "failure", nur zusaetzliche Evidenz fuer "success".
        if tree_sha_reuse_enabled or pr_head_reuse_enabled:
            tree_info_merge = await self._fetch_commit_tree_info(repo_full_name, merged_sha)
            merge_tree_sha = tree_info_merge.get("tree_sha") if tree_info_merge else None
            parent_shas = (tree_info_merge or {}).get("parent_shas") or []
            parent2_sha = parent_shas[1] if len(parent_shas) >= 2 else None

            # --- Weg 1: strenge Tree-Gleichheit (Task 4, unveraendert) -------
            # Greift nur bei Merge-Commits, deren Baum bit-identisch mit dem
            # des PR-HEAD ist. Bleibt erhalten, damit sich Paket A ueber
            # `ci_wait_pr_head_reuse` abschalten laesst, ohne den vorherigen
            # Zustand mitzunehmen.
            if tree_sha_reuse_enabled and parent2_sha and merge_tree_sha:
                tree_info_parent2 = await self._fetch_commit_tree_info(
                    repo_full_name, parent2_sha
                )
                if (
                    tree_info_parent2 is not None
                    and merge_tree_sha == tree_info_parent2.get("tree_sha")
                ):
                    gruene = await self._checks_sind_vollstaendig_gruen(
                        repo_full_name, parent2_sha, workflow_names_lower
                    )
                    if gruene:
                        self.logger.info(
                            "✅ ZERODOX#3328 Task 4: Tree-SHA-Reuse — Merge-Commit "
                            f"{merged_sha[:7]} hat denselben Tree wie sein zweiter "
                            f"Parent {parent2_sha[:7]}; {gruene} bereits gruene "
                            "Check(s) werden wiederverwendet. Uebersprungen wird "
                            "damit der Merge-Lauf auf main, NICHT der PR-Lauf."
                        )
                        return "success"

            # --- Weg 2: gruener PR-Stand, ohne Tree-Bedingung (Paket A) ------
            #
            # Warum die Tree-Gleichheit als BEDINGUNG faellt: GitHub loest beim
            # "Merge pull request" keine Konflikte selbst auf -- bei Konflikten
            # verweigert es den Merge. Ein Merge-Commit enthaelt deshalb nur die
            # Aenderungen des PR plus die bereits auf main gepruefte Historie,
            # nichts Ungeprueftes. Sobald zwischen PR-Erstellung und Merge ein
            # anderer Commit auf main landet, weicht der Baum jedoch ab, und
            # Weg 1 faellt durch -- belegt am 16.09.2026: PR #3404 wurde nach
            # #3405 gemergt, der Riegel schlug zu, der Merge hing.
            #
            # Was NICHT abgedeckt bleibt, sind semantische Konflikte: PR A
            # aendert eine Funktion, PR B ihren Aufrufer, beide einzeln gruen,
            # zusammen kaputt. Genau die faengt der Tagesabschluss-Scan ab
            # (ZERODOX#3328 Paket B, `tagesabschluss.yml`). Wer Paket A ohne
            # dieses Netz betreibt, traegt das Risiko ungefedert.
            #
            # ⚠️ `merge^2` allein reicht NICHT: Squash-Merges haben nur EINEN
            # Elternteil. Auf der main-Linie von ZERODOX waren das in 30 Tagen
            # 51 Code-Commits -- alle mit zugeordnetem PR, keiner ein echter
            # Direkt-Push (gemessen 17.09.2026). Deshalb zusaetzlich der
            # Umweg ueber `GET /commits/{sha}/pulls`.
            if pr_head_reuse_enabled:
                # Reihenfolge ist Absicht: erst der zweite Elternteil, den wir
                # ohnehin schon in der Hand haben, DANN erst die zusaetzliche
                # PR-Abfrage. Der haeufigste Fall ist der Merge-Commit (387 von
                # 455 in 30 Tagen) — fuer ihn faellt damit kein weiterer
                # API-Aufruf an, und Testdoubles, die nur die Eltern-Kette
                # kennen, brauchen den neuen Endpunkt gar nicht erst.
                if parent2_sha:
                    gruene = await self._checks_sind_vollstaendig_gruen(
                        repo_full_name, parent2_sha, workflow_names_lower
                    )
                    if gruene:
                        self.logger.info(
                            "✅ ZERODOX#3328 Paket A: PR-Head-Wiederverwendung — "
                            f"fuer {merged_sha[:7]} sind auf dem zweiten Parent "
                            f"{parent2_sha[:7]} {gruene} Check(s) gruen "
                            "abgeschlossen (ohne Tree-Bedingung). Der Merge-Lauf "
                            "auf main entfaellt; semantische Konflikte deckt der "
                            "Tagesabschluss-Scan ab. Abschaltbar ueber "
                            "project_config `ci_wait_pr_head_reuse: false`."
                        )
                        return "success"

                # Zweiter Weg NUR fuer Commits ohne (gruenen) zweiten Elternteil
                # — praktisch: Squash-Merges.
                pr_heads = await self._fetch_pull_head_shas(repo_full_name, merged_sha)
                if pr_heads is None:
                    # Fail-closed: "nicht ermittelbar" heisst NICHT "kein PR".
                    self.logger.info(
                        "ℹ️ ZERODOX#3328 Paket A: PR-Zuordnung fuer "
                        f"{merged_sha[:7]} nicht ermittelbar — kein Kurzschluss, "
                        "es wird normal gepollt."
                    )
                else:
                    for head_sha in pr_heads:
                        if head_sha == parent2_sha:
                            continue  # oben bereits erfolglos geprueft
                        gruene = await self._checks_sind_vollstaendig_gruen(
                            repo_full_name, head_sha, workflow_names_lower
                        )
                        if gruene:
                            self.logger.info(
                                "✅ ZERODOX#3328 Paket A: PR-Head-Wiederverwendung "
                                f"— fuer {merged_sha[:7]} sind auf {head_sha[:7]} "
                                f"(HEAD des zugeordneten PR, Squash-Merge) {gruene} "
                                "Check(s) gruen abgeschlossen. Abschaltbar ueber "
                                "project_config `ci_wait_pr_head_reuse: false`."
                            )
                            return "success"
                    if not pr_heads:
                        self.logger.info(
                            "ℹ️ ZERODOX#3328 Paket A: Zu "
                            f"{merged_sha[:7]} gehoert nachweislich kein gemergter "
                            "PR (Direkt-Push auf main) — kein Kurzschluss, es wird "
                            "normal gepollt."
                        )

        admin_merge_deadline_logged = False

        while time.monotonic() < deadline:
            data = await self._fetch_workflow_runs_for_sha(repo_full_name, merged_sha)
            if data is None:
                # API-Fehler / Rate-Limit — weiter pollen
                api_fehler_runden += 1
                await asyncio.sleep(poll_interval_s)
                continue

            api_erfolg_runden += 1
            all_runs = data.get("workflow_runs") or []

            # ZERODOX#3328 Task 4: Relevanz-Filterung + Klassifikation kommen
            # jetzt aus der gemeinsamen, zustandslosen Hilfsfunktion
            # `_klassifiziere_workflow_runs` (siehe Modulebene oben) — derselbe
            # Code-Pfad, den auch der Tree-SHA-Reuse-Kurzschluss vor dieser
            # Schleife verwendet. Verhaltensgleiches Refactoring, keine neue
            # Logik hier.
            (
                relevant,
                latest_per_workflow,
                all_completed,
                any_failed,
                failed_run,
                pending_names,
            ) = _klassifiziere_workflow_runs(all_runs, workflow_names_lower)

            if not relevant:
                # ZERODOX#1985/#3230: Der Docs-only-Check laeuft inzwischen VOR
                # der Schleife (siehe oben) — hier bleibt nur noch die einmalige
                # Klassifikations-Warnung, sobald die Gnadenfrist erreicht ist.
                # Kein erneuter _fetch_commit_files-Call mehr: `precomputed_changed_paths`
                # wurde bereits vor der Schleife ermittelt und ändert sich nicht.
                if (
                    not saw_any_relevant
                    and not admin_merge_deadline_logged
                    and time.monotonic() >= admin_merge_deadline
                ):
                    admin_merge_deadline_logged = True
                    classification = (
                        "Code-Commit" if precomputed_changed_paths is not None else "unklarer Commit"
                    )
                    self.logger.warning(
                        f"⚠️ _wait_for_ci_completion: {classification} {merged_sha[:7]} "
                        f"nach {admin_merge_grace_min}min ohne relevanten Workflow — "
                        f"warte fail-closed bis zum {max_wait_min}min-Limit."
                    )
                self.logger.info(
                    f"⏳ _wait_for_ci_completion: noch keine relevanten Workflows "
                    f"fuer {merged_sha[:7]} sichtbar — weiter pollen ({poll_interval_s}s)..."
                )
                await asyncio.sleep(poll_interval_s)
                continue

            saw_any_relevant = True

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

        if not saw_any_relevant:
            # Nur wenn die API mindestens einmal geantwortet hat, ist "es gibt
            # keinen Workflow" eine Beobachtung. Sonst ist es eine Vermutung.
            if api_erfolg_runden == 0 and api_fehler_runden > 0:
                self.logger.warning(
                    f"🌐 _wait_for_ci_completion: GitHub-API nach {max_wait_min}min "
                    f"unverändert nicht lesbar ({api_fehler_runden} Versuche) fuer "
                    f"{repo_full_name}@{merged_sha[:7]} — CI-Lage unbekannt, "
                    f"kein Deploy. Prüfen: https://www.githubstatus.com"
                )
                return "api_unavailable"
            self.logger.warning(
                f"🛑 _wait_for_ci_completion: CI FEHLT nach {max_wait_min}min "
                f"fuer {repo_full_name}@{merged_sha[:7]}"
            )
            return "missing"

        self.logger.warning(
            f"⏰ _wait_for_ci_completion: TIMEOUT nach {max_wait_min}min "
            f"fuer {repo_full_name}@{merged_sha[:7]}"
        )
        return "timeout"

    async def _send_ci_wait_alert(
        self,
        outcome: Literal["failure", "timeout", "missing", "api_unavailable"],
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
            elif outcome == "timeout":
                title = f"⏰ {repo_name}: Deploy zurueckgestellt — CI nicht durch"
                color = 0xF1C40F
                description = (
                    f"Welle-9.10-Schutz: CI-Workflows ({', '.join(workflow_names) or '—'}) "
                    f"sind nach {max_wait_min} Minuten fuer Commit `{merged_sha[:7]}` "
                    f"noch nicht alle completed.\n\n"
                    f"**deploy.sh wurde NICHT getriggert.** Sobald CI gruen ist, "
                    f"deploy.sh manuell triggern."
                )
            elif outcome == "api_unavailable":
                title = f"🌐 {repo_name}: Deploy zurueckgestellt — GitHub nicht erreichbar"
                color = 0x95A5A6
                description = (
                    f"Die GitHub-API war ueber die gesamten {max_wait_min} Minuten nicht "
                    f"lesbar, deshalb ist ueber die CI von Commit `{merged_sha[:7]}` "
                    f"**nichts bekannt** — weder gruen noch rot noch fehlend.\n\n"
                    f"**deploy.sh wurde NICHT getriggert** (fail-closed). Das ist keine "
                    f"Aussage ueber den Code: Sobald die API wieder antwortet, ist der "
                    f"Deploy einen erneuten Versuch wert.\n\n"
                    f"Stoerungen pruefen: https://www.githubstatus.com"
                )
            else:  # missing
                title = f"🛑 {repo_name}: Deploy ABGEBROCHEN — CI fehlt"
                color = 0xE67E22
                description = (
                    f"Fail-closed-Schutz: Fuer den Code-Commit `{merged_sha[:7]}` ist "
                    f"nach {max_wait_min} Minuten keiner der erwarteten CI-Workflows "
                    f"({', '.join(workflow_names) or '—'}) aufgetaucht.\n\n"
                    f"**deploy.sh wurde NICHT getriggert.** Workflow-Trigger und "
                    f"Required-Checks pruefen; danach Deployment manuell anstossen."
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
        push_commit_shas: Optional[List[str]] = None,
        deployment_context: Optional[Dict] = None,
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
            return "blocked"

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
                return "blocked"

        # Welle 9.10: Wait-for-CI BEFORE deploy.sh-Call (falls Args vollstaendig).
        # Caller (handle_pr_event) MUSS repo_full_name + full_sha mitgeben um zu profitieren.
        if repo_full_name and full_sha and project_config:
            workflow_names = project_config.get('ci_workflows') or []
            if workflow_names:
                max_wait_min = int(project_config.get('ci_wait_max_min', 30))
                admin_merge_grace_min = int(
                    project_config.get('ci_wait_admin_merge_grace_min', 5)
                )
                # 12.09.2026: konstantes Poll-Intervall statt Backoff, konfigurierbar
                # je Projekt (Begründung im Docstring von _wait_for_ci_completion).
                poll_interval_sec = int(
                    project_config.get('ci_wait_poll_interval_sec', 20)
                )
                # ZERODOX#3328 Task 4: Tree-SHA-Reuse-Kurzschluss, Default AN --
                # ein Merge-Commit mit bit-identischem Tree zu seinem zweiten
                # Parent hat bereits validierte CI (Begründung + Fail-closed-
                # Verhalten im Docstring von _wait_for_ci_completion).
                tree_sha_reuse_enabled = bool(
                    project_config.get('ci_wait_tree_sha_reuse', True)
                )
                # ZERODOX#3328 Paket A: Wiederverwendung des gruenen PR-Standes
                # OHNE Tree-Bedingung — deckt zusaetzlich Squash-Merges ab, die
                # keinen zweiten Elternteil haben. Gehoert zusammen mit dem
                # entfallenen `push: main` in web-quality.yml; siehe die Warnung
                # im Docstring von _wait_for_ci_completion.
                pr_head_reuse_enabled = bool(
                    project_config.get('ci_wait_pr_head_reuse', True)
                )
                outcome = await self._wait_for_ci_completion(
                    repo_full_name=repo_full_name,
                    merged_sha=full_sha,
                    workflow_names=workflow_names,
                    max_wait_min=max_wait_min,
                    admin_merge_grace_min=admin_merge_grace_min,
                    poll_interval_sec=poll_interval_sec,
                    tree_sha_reuse_enabled=tree_sha_reuse_enabled,
                    pr_head_reuse_enabled=pr_head_reuse_enabled,
                    push_commit_shas=push_commit_shas,
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
                    self._release_deploy(repo_name, full_sha)
                    return "blocked"
                # "api_unavailable" gehoert zwingend in diese Menge: faellt der
                # Wert durch, landet er unten im Weiter-deployen-Zweig — ein
                # Deploy ohne jede CI-Pruefung. Ein Test haelt das fest.
                if outcome in {"timeout", "missing", "api_unavailable"}:
                    await self._send_ci_wait_alert(
                        outcome=outcome,
                        repo_name=repo_name,
                        repo_full_name=repo_full_name,
                        branch=branch,
                        merged_sha=full_sha,
                        workflow_names=workflow_names,
                        max_wait_min=max_wait_min,
                    )
                    self._release_deploy(repo_name, full_sha)
                    # "api_unavailable" ist ausdruecklich transient: Die CI-Lage
                    # war nur nicht lesbar. Der Reconcile soll es spaeter erneut
                    # versuchen duerfen, ohne dafuer einen Versuch zu verbrauchen.
                    return "transient" if outcome == "api_unavailable" else "blocked"
                # success/docs_only/no_workflows → weiter unten deployen. Bei docs_only
                # beendet deploy.sh selbst ohne Runtime-Aenderung (identische Allowlist).
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
            context = dict(deployment_context or {})
            context.setdefault("commit_sha", full_sha or commit_sha)
            if repo_full_name:
                repo_url = f"https://github.com/{repo_full_name}"
                context.setdefault("repo_url", repo_url)
                if full_sha:
                    context.setdefault("commit_url", f"{repo_url}/commit/{full_sha}")
            result = await self.deployment_manager.deploy_project(
                repo_name, branch, deploy_context=context
            )

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
                return "deployed"
            else:
                # 2026-08-17: Der Grund stand bereits in result['error'] und wurde
                # hier verworfen — vier Fehlschlaege an einem Tag hinterliessen im
                # Log nur "Deployment fehlgeschlagen: ZERODOX". Ohne Grund ist ein
                # Abbruch nicht von einer Infrastrukturstoerung unterscheidbar.
                reason = str(result.get('error') or '').strip()
                if not reason:
                    reason = "ohne Fehlergrund in der deploy_project-Antwort"
                self.logger.warning(
                    f"⚠️ Deployment fehlgeschlagen: {repo_name} — {reason}"
                )
                self._release_deploy(repo_name, full_sha or '')
                # deploy.sh meldet eine belegte Deploy-Sperre seit dem 17.08.2026
                # mit EX_TEMPFAIL (75). Am selben Tag scheiterten drei Auto-Deploys
                # daran, weil parallel ein manueller Lauf mit --migrate lief — die
                # Sperre arbeitete korrekt, verbrauchte aber die Nachhol-Versuche.
                if _DEPLOY_TEMPFAIL_MARKER in reason:
                    self.logger.info(
                        f"↻ {repo_name}: Deploy war vorübergehend verhindert "
                        f"(EX_TEMPFAIL) — zählt nicht als verbrauchter Versuch."
                    )
                    return "transient"
                return "failed"

        except Exception as e:
            self._release_deploy(repo_name, full_sha or '')
            self.logger.error(f"❌ Deployment Fehler: {e}", exc_info=True)
            return "failed"

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
