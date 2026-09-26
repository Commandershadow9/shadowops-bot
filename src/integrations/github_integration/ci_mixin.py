"""
CI polling and deployment methods for GitHubIntegration.
"""

import asyncio
import base64
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Literal, Optional
from collections import OrderedDict

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

# ZERODOX#3391: Obergrenze des GitHub-Compare-Endpunkts. Er liefert hoechstens
# 300 Dateien, ohne das verlaesslich zu kennzeichnen. Eine volle Liste gilt
# deshalb als moeglicherweise abgeschnitten und fuehrt zu KEINEM
# Docs-only-Kurzschluss — bei so vielen Dateien ist "nur Dokumentation" ohnehin
# unwahrscheinlich.
_COMPARE_FILES_MAX = 300

# ZERODOX#3328 Paket A: `GET /commits/{sha}/pulls` antwortet unmittelbar nach
# einem Merge oft noch mit einer leeren Liste — GitHub indiziert die Zuordnung
# Commit→PR verzoegert. Wer das als "kein PR" liest, verliert den Kurzschluss
# fuer JEDEN Squash-Merge (gemessen 19.09.2026 an ae34e13: leer nach 2 s,
# korrekt PR #3458 wenige Minuten spaeter).
#
# ⚠️ Die Obergrenze ist bewusst klein. Sie verzoegert nur den Fall, in dem
# wirklich kein PR existiert — und der kam in 30 Tagen null Mal vor. Waere sie
# gross, verschoebe sie im Gegenzug jeden echten Direkt-Push-Deploy.
# ZERODOX#3331: Wie lange die aus dem Workflow gelesenen `paths-ignore`-Muster
# gelten. Kurz genug, dass eine Aenderung an web-quality.yml binnen Minuten
# wirkt; lang genug, dass nicht jeder Merge zwei API-Aufrufe kostet.
_PATHS_IGNORE_CACHE_S = 600

_PR_ZUORDNUNG_VERSUCHE = 4
_PR_ZUORDNUNG_WARTE_S = 15


def _glob_zu_regex(muster: str) -> str:
    """Uebersetzt ein GitHub-`paths-ignore`-Muster in einen regulaeren Ausdruck.

    GitHub benutzt eine eigene Glob-Variante (nicht fnmatch):

        docs/**              alles unterhalb von docs/
        **.md                jede .md-Datei, in jeder Tiefe
        web/public/**/*.svg  svg unterhalb von web/public/, beliebig tief
        .gitignore           genau diese Datei

    ⚠️ Der Unterschied zwischen `*` und `**` ist der Schraegstrich: `*` bleibt
    innerhalb eines Pfadsegments, `**` ueberspringt beliebig viele. Wer `*` als
    `.*` uebersetzt, macht aus `web/public/*.svg` ein Muster, das auch
    `web/public/tief/verschachtelt/x.svg` trifft — und erklaert damit Dateien
    zu Doku, die keine sind.
    """
    ergebnis: list[str] = []
    i = 0
    while i < len(muster):
        zeichen = muster[i]
        if muster.startswith("**/", i):
            # Beliebig viele Verzeichnisebenen — auch null.
            ergebnis.append("(?:.*/)?")
            i += 3
        elif muster.startswith("**", i):
            ergebnis.append(".*")
            i += 2
        elif zeichen == "*":
            ergebnis.append("[^/]*")
            i += 1
        elif zeichen == "?":
            ergebnis.append("[^/]")
            i += 1
        else:
            ergebnis.append(re.escape(zeichen))
            i += 1
    return "^" + "".join(ergebnis) + "$"


def _pfad_passt_auf_muster(pfad: str, muster_liste: list[str]) -> bool:
    """Trifft mindestens eines der Muster diesen Pfad?"""
    for muster in muster_liste:
        try:
            if re.match(_glob_zu_regex(muster), pfad):
                return True
        except re.error:  # pragma: no cover - defektes Muster
            continue
    return False


def _paths_are_docs_only(
    paths: list[str],
    paths_ignore: Optional[list[str]] = None,
) -> bool:
    """Return True only for a non-empty, entirely non-runtime path list.

    ZERODOX#3331: Mit `paths_ignore` wird gegen die MUSTER DES WORKFLOWS
    geurteilt — dieselbe Quelle, aus der `hard_gate_docs_only_bypass` in
    ZERODOX/scripts/deploy.sh liest. Vorher trug diese Funktion eine
    hartkodierte Kopie von drei Mustern, waehrend `web-quality.yml` dreissig
    hat. Alles dazwischen (`maintenance/**`, `web/public/**/*.svg`, `**.md`
    ausserhalb des Roots, …) galt hier als Laufzeitcode, obwohl der Workflow
    dafuer gar nicht erst startet.

    ⚠️ Ohne `paths_ignore` bleibt die alte, ENGERE Heuristik. Sie irrt
    hoechstens in Richtung "deployen" — und das ist die harmlose Richtung.
    """
    normalized_paths = [str(path).strip() for path in paths if str(path).strip()]
    if not normalized_paths:
        return False

    if paths_ignore:
        return all(
            _pfad_passt_auf_muster(path, paths_ignore) for path in normalized_paths
        )

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


def _beschreibe_letzten_ci_zustand(gesehene_laeufe: Dict[str, Dict]) -> str:
    """ZERODOX#2891: Menschenlesbarer Satz zum zuletzt gesehenen Zustand der
    noch nicht abgeschlossenen Laeufe — fuer Wartelog und Timeout-Alarm.
    Leerer String, wenn nichts Offenes gesehen wurde."""
    teile = []
    for wf_name, lauf in gesehene_laeufe.items():
        status = str(lauf.get("status") or "").lower()
        if not status or status == "completed":
            continue
        if status == "in_progress":
            teile.append(f"{wf_name} lief zuletzt (in_progress)")
        elif status in {"queued", "waiting", "requested", "pending"}:
            teile.append(f"{wf_name} wartete zuletzt auf einen Runner ({status})")
        else:
            teile.append(f"{wf_name} zuletzt im Status {status}")
    return "; ".join(teile)


def _ist_versuchsnummer(wert) -> bool:
    """True fuer eine echte `run_attempt`-Zahl (int, nicht bool)."""
    return type(wert) is int


def _ist_veralteter_versuch(gemerkt, run: Dict) -> bool:
    """ZERODOX#2920 (Race): Ist dieses "cancelled" noch der ALTE Versuch vor
    dem automatischen Neuversuch?

    `gemerkt` ist der Wert aus `_ci_cancelled_retry_versucht` — der
    `run_attempt` zum Zeitpunkt des Neuversuchs, oder `True`, wenn er damals
    im Payload fehlte. Nur wenn beide Seiten eine echte Versuchsnummer tragen
    und die aktuelle nicht hoeher ist, gilt die Antwort als veraltet. Fehlt
    eine der Zahlen, bleibt es konservativ beim endgueltigen "failure".
    """
    aktuell = run.get("run_attempt")
    if not (_ist_versuchsnummer(gemerkt) and _ist_versuchsnummer(aktuell)):
        return False
    return aktuell <= gemerkt


def _nur_cancelled_ohne_echten_fehlschlag(
    latest_per_workflow: Dict[str, Dict],
) -> Optional[List[Dict]]:
    """ZERODOX#2920: Prueft, ob JEDER nicht-gruen abgeschlossene Lauf in
    `latest_per_workflow` die Ursache "cancelled" traegt — kein einziger
    echter "failure"/"timed_out"/... darunter ist.

    `_klassifiziere_workflow_runs` bricht bei der ERSTEN nicht-gruenen
    Klassifikation ab (`break` oben) und liefert dafuer nur EINEN
    `failed_run` zurueck. Bei mehreren relevanten Workflows koennte das
    sowohl den falschen Lauf treffen als auch einen echten Fehlschlag neben
    einem "cancelled" verdecken. Diese Funktion scannt deshalb ALLE
    Eintraege in `latest_per_workflow` selbststaendig.

    Returns:
        Liste der "cancelled"-Laeufe, wenn ALLE nicht-gruenen abgeschlossenen
        Laeufe "cancelled" sind. `None`, sobald mindestens ein Lauf mit einer
        ANDEREN Fehlschlag-Ursache darunter ist, oder wenn ueberhaupt kein
        "cancelled"-Lauf gefunden wurde — dann bleibt es beim heutigen
        Verhalten, kein Sonderfall.
    """
    cancelled_runs: List[Dict] = []
    for run in latest_per_workflow.values():
        status = str(run.get("status") or "").lower()
        if status != "completed":
            continue
        conclusion = str(run.get("conclusion") or "").lower()
        if conclusion not in _CI_FAILURE_CONCLUSIONS:
            continue
        if conclusion != "cancelled":
            return None
        cancelled_runs.append(run)
    return cancelled_runs or None


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
            #
            # "superseded" (ZERODOX#3328, Sammel-Zug) gehoert aus demselben
            # Grund dazu: branch_sha war zum Startzeitpunkt dieser Runde
            # bereits ueberholt, die naechste Runde prueft ohnehin den dann
            # aktuellen Kopf erneut — auch das kein verbrauchter Versuch.
            if ergebnis not in ("transient", "superseded"):
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

    async def _rerun_cancelled_workflow_run(
        self,
        repo_full_name: str,
        run_id,
    ) -> bool:
        """ZERODOX#2920: Stoesst fuer einen "cancelled"-Lauf bei unveraendertem
        Branch-Kopf EINEN automatischen Neuversuch an, statt den Merge sofort
        als FEHLGESCHLAGEN zu werten — Runner-Last kann Jobs abbrechen, ohne
        dass ein Test wirklich rot lief.

        Endpoint-Wahl `rerun-failed-jobs` statt des vollen `rerun`: Der
        "Re-run failed jobs"-Endpunkt fuehrt nachweislich auch Jobs mit dem
        Ergebnis "cancelled" erneut aus (nicht nur "failure") und startet dabei
        NICHT den kompletten Workflow neu, sondern nur die nicht-gruenen Jobs
        — guenstiger und schneller als `/rerun` bei einem grossen Workflow wie
        "Web Quality".

        Fail-soft wie `_fetch_workflow_run`: Ein Fehler wird geloggt und als
        `False` zurueckgegeben, NIE nach aussen geworfen — der Aufrufer bleibt
        dann beim heutigen "failure"-Verhalten.
        """
        if not repo_full_name or not run_id:
            return False
        url = (
            f"https://api.github.com/repos/{repo_full_name}/actions/runs/"
            f"{run_id}/rerun-failed-jobs"
        )
        headers = {
            "Accept": "application/vnd.github+json",
        }
        token = self._get_github_token()
        if token:
            headers["Authorization"] = f"token {token}"
        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.post(url, timeout=20) as resp:
                    if resp.status in (403, 404):
                        body = await resp.text()
                        self.logger.warning(
                            "⚠️ ZERODOX#2920: Neuversuch für Run "
                            f"{run_id} ({repo_full_name}) abgelehnt "
                            f"({resp.status}) — Token fehlt das Recht actions:write "
                            "— automatischer Neuversuch (#2920) ist damit "
                            f"wirkungslos. Antwort: {body}"
                        )
                        return False
                    if resp.status not in (200, 201):
                        body = await resp.text()
                        self.logger.warning(
                            "⚠️ ZERODOX#2920: Neuversuch fuer Run "
                            f"{run_id} ({repo_full_name}) fehlgeschlagen "
                            f"({resp.status}): {body}"
                        )
                        return False
                    return True
        except Exception as e:
            self.logger.error(
                "❌ ZERODOX#2920: Fehler beim Neuversuch fuer Run "
                f"{run_id} ({repo_full_name}): {e}",
                exc_info=True,
            )
            return False

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

    async def _lade_paths_ignore(
        self,
        repo_full_name: str,
        workflow_namen: List[str],
    ) -> Optional[list[str]]:
        """Liest `on.push.paths-ignore` aus dem CI-Workflow des Repos.

        ZERODOX#3331: Die Frage "ist dieser Merge reine Doku?" wurde an zwei
        Stellen unabhaengig beantwortet. `hard_gate_docs_only_bypass` in
        ZERODOX/scripts/deploy.sh liest die Muster zur Laufzeit aus
        `web-quality.yml` und kann deshalb nicht driften. Der Bot trug
        stattdessen eine hartkodierte Kopie von DREI Mustern, waehrend der
        Workflow DREISSIG hat.

        Nicht abgedeckt waren unter anderem `maintenance/**`,
        `web/public/**/*.svg|png|woff`, `**.md` ausserhalb des Roots,
        `.gitignore`, `LICENSE` und die `.github/`-Vorlagen. Ein Merge, der nur
        ein SEO-Bild austauscht, startet `web-quality.yml` per `paths-ignore`
        nicht — der Bot hielt ihn aber fuer Laufzeitcode und lief in den
        Fehlschlag.

        Der Workflow-PFAD wird nicht konfiguriert, sondern ueber seinen Namen
        aufgeloest (`/actions/workflows` liefert `name` und `path`). Der Bot
        bedient mehrere Projekte; ein hartkodierter Pfad waere die naechste
        Kopie.

        ⚠️ Fail-closed: Jede Unklarheit liefert None. Der Aufrufer faellt dann
        auf die alte, ENGERE Heuristik zurueck — sie irrt hoechstens in
        Richtung "deployen", und das ist die harmlose Richtung. "Keine Muster
        gelesen" darf NIEMALS "alles ist docs-only" bedeuten.
        """
        if not repo_full_name or not workflow_namen:
            return None

        zwischenspeicher = getattr(self, "_paths_ignore_cache", None)
        if zwischenspeicher is None:
            zwischenspeicher = {}
            self._paths_ignore_cache = zwischenspeicher
        schluessel = f"{repo_full_name}::{workflow_namen[0]}"
        eintrag = zwischenspeicher.get(schluessel)
        if eintrag and (time.monotonic() - eintrag[0]) < _PATHS_IGNORE_CACHE_S:
            return eintrag[1]

        try:
            headers = {"Accept": "application/vnd.github+json"}
            token = self._get_github_token()
            if token:
                headers["Authorization"] = f"token {token}"

            gesucht = workflow_namen[0].strip().lower()
            async with aiohttp.ClientSession(headers=headers) as session:
                # 1) Name -> Dateipfad
                url = f"https://api.github.com/repos/{repo_full_name}/actions/workflows?per_page=100"
                async with session.get(url, timeout=20) as resp:
                    if resp.status != 200:
                        return None
                    payload = await resp.json()
                workflows = (payload or {}).get("workflows")
                if not isinstance(workflows, list):
                    return None
                pfad = next(
                    (
                        str(w.get("path") or "")
                        for w in workflows
                        if isinstance(w, dict)
                        and str(w.get("name") or "").strip().lower() == gesucht
                    ),
                    "",
                )
                if not pfad:
                    self.logger.info(
                        f"ℹ️ Workflow '{workflow_namen[0]}' in {repo_full_name} nicht "
                        "gefunden — Docs-only-Pruefung nutzt die enge Heuristik."
                    )
                    return None

                # 2) Datei lesen
                url = f"https://api.github.com/repos/{repo_full_name}/contents/{pfad}"
                async with session.get(url, timeout=20) as resp:
                    if resp.status != 200:
                        return None
                    datei = await resp.json()

            inhalt_roh = (datei or {}).get("content")
            if not isinstance(inhalt_roh, str):
                return None
            text = base64.b64decode(inhalt_roh).decode("utf-8", errors="replace")

            import yaml  # lokal: nur dieser Pfad braucht ihn

            # ⚠️ `on:` ist in YAML 1.1 das Schluesselwort True. PyYAML liefert
            # den Block deshalb unter dem Schluessel `True`, nicht "on" — wer
            # nur nach "on" sucht, findet nie etwas und faellt still zurueck.
            daten = yaml.safe_load(text)
            if not isinstance(daten, dict):
                return None
            on_block = daten.get("on", daten.get(True))
            if not isinstance(on_block, dict):
                return None
            push_block = on_block.get("push")
            if not isinstance(push_block, dict):
                return None
            muster = push_block.get("paths-ignore")
            if not isinstance(muster, list) or not muster:
                return None

            sauber = [str(m).strip() for m in muster if str(m).strip()]
            if not sauber:
                return None

            zwischenspeicher[schluessel] = (time.monotonic(), sauber)
            return sauber
        except Exception as exc:  # pragma: no cover - Netz-/Parse-Fehler
            self.logger.warning(
                f"⚠️ `paths-ignore` fuer {repo_full_name} nicht lesbar ({exc}) — "
                "Docs-only-Pruefung nutzt die enge Heuristik."
            )
            return None

    async def _laeuft_noch_ein_workflow(
        self,
        repo_full_name: str,
        sha: str,
    ) -> Optional[bool]:
        """Ist fuer diesen Commit noch irgendein Workflow unterwegs?

        ZERODOX#3230: Der Bot wartete bis zu 30 Minuten auf `Web Quality` und
        meldete danach "Deployment fehlgeschlagen" — fuer Commits, bei denen
        dieser Workflow durch den `paths-ignore`-Filter NIE startet. Am
        09.09.2026 legte eine Docs-Serie die Auslieferung so ueber eine Stunde
        lahm, und der Fehlschlag war keiner.

        "Noch nicht angelegt" und "wird nie angelegt" sehen an einem einzelnen
        Check-Run identisch aus. Unterscheidbar werden sie erst ueber den
        GESAMTEN Lauf-Bestand des Commits: Ist dort nichts mehr offen, hat
        GitHub die Push-Events verarbeitet — was dann fehlt, kommt nicht mehr.

        Dieselbe Quelle und dieselbe Frage wie `ci_lauf_ist_unterwegs()` in
        ZERODOX/scripts/deploy.sh. Bewusst nachgebaut statt neu erfunden: Zwei
        Stellen, die ueber "darf deployt werden" verschieden urteilen, sind
        genau das Problem aus ZERODOX#3331.

        Returns:
            True  — mindestens ein Lauf ist noch nicht `completed`
            False — alle Laeufe sind abgeschlossen (oder es gibt keine)
            None  — nicht ermittelbar; der Aufrufer MUSS das wie True behandeln
        """
        if not repo_full_name or not sha:
            return None

        url = (
            f"https://api.github.com/repos/{repo_full_name}/actions/runs"
            f"?head_sha={sha}&per_page=50"
        )
        try:
            # Token-Beschaffung INNERHALB des try — gleiche Begruendung wie bei
            # _fetch_commit_tree_info: Ein Harness ohne _get_github_token()
            # darf hier keine durchschlagende AttributeError ausloesen.
            headers = {"Accept": "application/vnd.github+json"}
            token = self._get_github_token()
            if token:
                headers["Authorization"] = f"token {token}"
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(url, timeout=20) as resp:
                    if resp.status != 200:
                        self.logger.warning(
                            f"⚠️ Lauf-Bestand fuer {repo_full_name}@{sha[:7]} "
                            f"nicht ladbar ({resp.status}) — es wird weiter gewartet."
                        )
                        return None
                    payload = await resp.json()
                    runs = (payload or {}).get("workflow_runs")
                    if not isinstance(runs, list):
                        self.logger.warning(
                            f"⚠️ Unerwartete Antwortform beim Lauf-Bestand fuer "
                            f"{repo_full_name}@{sha[:7]} — es wird weiter gewartet."
                        )
                        return None
                    offen = [
                        r for r in runs
                        if isinstance(r, dict) and str(r.get("status") or "").lower() != "completed"
                    ]
                    return len(offen) > 0
        except Exception as exc:  # pragma: no cover - Netzwerkfehler
            self.logger.warning(
                f"⚠️ Lauf-Bestand fuer {repo_full_name}@{sha[:7]} "
                f"nicht abfragbar ({exc}) — es wird weiter gewartet."
            )
            return None

    async def _fetch_compare_files(
        self,
        repo_full_name: str,
        base_sha: str,
        head_sha: str,
    ) -> Optional[list[str]]:
        """Geaenderte Pfade zwischen zwei Staenden — fail-closed wie die Schwester.

        ZERODOX#3391: Fuer die Docs-only-Frage zaehlt nicht, was der letzte Push
        enthielt, sondern was zwischen dem AUSGELIEFERTEN Stand und dem neuen
        HEAD liegt. Nur das beantwortet "enthaelt das, was noch nicht live ist,
        Laufzeitcode?".

        Der Unterschied ist nicht theoretisch. Am 15.09.2026 fiel ein
        Code-Merge (#3386, drei CSS-/Testdateien) in einen laufenden Deploy und
        wurde verworfen. Zwanzig Minuten spaeter kam ein Docs-Merge; sein Push
        enthielt nur Dokumentation, also galt der Kurzschluss — und der
        Code-Merge blieb unausgeliefert. Kein Fehler, kein Alarm, eine
        INFO-Zeile. Eine Pruefung der Push-Commits haette das nicht gefunden:
        Der verworfene Commit steckte in keinem spaeteren Push.

        ⚠️ Fail-closed an jeder Stelle: HTTP-Fehler, unerwartete Antwortform,
        ungueltige Eintraege oder ein abgeschnittenes Ergebnis liefern None —
        und None heisst beim Aufrufer NIEMALS docs-only.

        ⚠️ Der Compare-Endpunkt liefert hoechstens 300 Dateien und meldet das
        ueber `files`-Laenge gegen `total_commits` nicht zuverlaessig. Deshalb
        gilt eine volle Seite als "moeglicherweise abgeschnitten" → None. Ein
        ueberfluessiger Deploy ist harmlos, ungetestet ausgelieferter Code nicht.
        """
        if not repo_full_name or not base_sha or not head_sha:
            return None
        if base_sha == head_sha:
            return []

        url = (
            f"https://api.github.com/repos/{repo_full_name}/compare/"
            f"{base_sha}...{head_sha}?per_page={_COMPARE_FILES_MAX}"
        )
        try:
            # Token-Beschaffung INNERHALB des try — gleiche Begruendung wie bei
            # _fetch_commit_tree_info: Ein Harness ohne _get_github_token()
            # darf hier keine durchschlagende AttributeError ausloesen.
            headers = {"Accept": "application/vnd.github+json"}
            token = self._get_github_token()
            if token:
                headers["Authorization"] = f"token {token}"
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(url, timeout=25) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        self.logger.warning(
                            f"⚠️ Vergleich {base_sha[:7]}...{head_sha[:7]} fuer "
                            f"{repo_full_name} nicht ladbar ({resp.status}): {body[:200]}"
                        )
                        return None

                    payload = await resp.json()
                    if not isinstance(payload, dict):
                        self.logger.warning(
                            f"⚠️ Unerwartete Antwortform beim Vergleich "
                            f"{base_sha[:7]}...{head_sha[:7]} fuer {repo_full_name}."
                        )
                        return None

                    files = payload.get("files")
                    if files is None and payload.get("status") == "identical":
                        return []
                    if not isinstance(files, list):
                        self.logger.warning(
                            f"⚠️ Dateiliste fehlt im Vergleich "
                            f"{base_sha[:7]}...{head_sha[:7]} fuer {repo_full_name}."
                        )
                        return None

                    if len(files) >= _COMPARE_FILES_MAX:
                        self.logger.info(
                            f"ℹ️ Vergleich {base_sha[:7]}...{head_sha[:7]} liefert "
                            f"{len(files)} Dateien (Obergrenze {_COMPARE_FILES_MAX}) — "
                            "moeglicherweise abgeschnitten, kein Docs-only-Kurzschluss."
                        )
                        return None

                    pfade = [
                        str(item.get("filename") or "").strip()
                        for item in files
                        if isinstance(item, dict)
                    ]
                    if any(not p for p in pfade) or len(pfade) != len(files):
                        self.logger.warning(
                            f"⚠️ Vergleich {base_sha[:7]}...{head_sha[:7]} fuer "
                            f"{repo_full_name} enthaelt ungueltige Eintraege."
                        )
                        return None
                    return pfade
        except Exception as exc:  # pragma: no cover - Netzwerkfehler
            self.logger.warning(
                f"⚠️ Vergleich {base_sha[:7]}...{head_sha[:7]} fuer "
                f"{repo_full_name} fehlgeschlagen: {exc}"
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
        cancelled_retry_enabled: bool = True,
        push_commit_shas: Optional[List[str]] = None,
        ausgelieferter_sha: Optional[str] = None,
        branch: Optional[str] = None,
    ) -> Literal[
        "success",
        "failure",
        "timeout",
        "missing",
        "docs_only",
        "no_workflows",
        "api_unavailable",
        "superseded",
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
            cancelled_retry_enabled: Automatischer Neuversuch eines
                "cancelled"-Laufs bei unverändertem Branch-Kopf (ZERODOX#2920,
                project_config-Key `ci_wait_cancelled_retry`). Default True;
                False = Verhalten vor #2920 ("cancelled" ist sofort "failure").
                Wer einen Deploy per Abbruch im GitHub-UI stoppen will:
                zweimal abbrechen oder diesen Schalter auf false.

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
        # ZERODOX#2891: Je Wartevorgang die gesehenen relevanten Laeufe merken
        # (Workflow-Name → id, API-URL, zuletzt gesehener Status). Die
        # Listenabfrage nach head_sha ist eventually consistent — ein bereits
        # gesehener Lauf kann darin wieder fehlen (31.08.2026: gruener Web-
        # Quality-Lauf 32 min unsichtbar, Timeout). Fehlt ein gemerkter Lauf,
        # wird er per Run-ID direkt nachgefragt.
        gesehene_laeufe: Dict[str, Dict] = {}
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
        # ZERODOX#3391 (19.09.2026): Vorrangig gegen den AUSGELIEFERTEN Stand
        # vergleichen, nicht gegen die Commits dieses Pushes.
        #
        # Die Push-Liste oben behebt den Fall "Code-Commit und Docs-Commit im
        # selben Push". Sie hilft aber nicht, wenn ein FRUEHERER Deploy-Auftrag
        # verworfen wurde: Dessen Commits stecken in keinem spaeteren Push.
        # Genau so blieb am 15.09.2026 der Code-Merge #3386 unausgeliefert — der
        # nachfolgende Docs-Merge sah, fuer sich betrachtet, korrekt nach
        # Dokumentation aus.
        #
        # `ausgelieferter_sha` kommt aus dem Deploy-Baum (HEAD nach dem letzten
        # `git pull` von deploy_project). Ist er bekannt, beantwortet der
        # Vergleich die richtige Frage: "Enthaelt das, was noch nicht live ist,
        # Laufzeitcode?" Ist er es nicht, bleibt alles wie bisher.
        vergleichs_pfade: Optional[list[str]] = None
        vergleich_genutzt = False
        if ausgelieferter_sha and ausgelieferter_sha != merged_sha:
            vergleichs_pfade = await self._fetch_compare_files(
                repo_full_name, ausgelieferter_sha, merged_sha
            )
            if vergleichs_pfade is not None:
                vergleich_genutzt = True
                self.logger.info(
                    f"ℹ️ Docs-only-Pruefung gegen den ausgelieferten Stand "
                    f"{ausgelieferter_sha[:7]}...{merged_sha[:7]}: "
                    f"{len(vergleichs_pfade)} geaenderte Datei(en)."
                )
            else:
                # Fail-closed: Der Vergleich ist die genauere Quelle. Konnte er
                # nicht gelesen werden, ist "nur Dokumentation" eine Behauptung
                # ohne Messung — dann lieber die Push-Liste, die hoechstens zu
                # VIEL deployt.
                self.logger.info(
                    "ℹ️ Vergleich gegen den ausgelieferten Stand nicht moeglich — "
                    "Docs-only-Pruefung faellt auf die Push-Commits zurueck."
                )

        docs_only_kandidaten = [sha for sha in (push_commit_shas or []) if sha] or [merged_sha]
        # Obergrenze: Ein Push mit sehr vielen Commits ist nie „nur
        # Dokumentation" und würde je Commit einen API-Aufruf kosten. Über der
        # Grenze fail-closed KEIN docs-only — ein überflüssiger Deploy mit
        # voller CI ist harmlos, ungetestet ausgelieferter Code nicht.
        _DOCS_ONLY_MAX_COMMITS = 20
        if vergleich_genutzt:
            # Der Vergleich deckt alles ab, was zwischen live und HEAD liegt —
            # einschliesslich verworfener Auftraege. Die Commit-Schleife
            # darunter waere dann nicht nur ueberfluessig, sondern ENGER: Sie
            # saehe die verpassten Commits nicht.
            precomputed_changed_paths = vergleichs_pfade
        elif len(docs_only_kandidaten) > _DOCS_ONLY_MAX_COMMITS:
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

        # ZERODOX#3331: Die Muster kommen aus dem Workflow selbst — dieselbe
        # Quelle, aus der `hard_gate_docs_only_bypass` in deploy.sh liest.
        # Schlägt das fehl, gilt die alte, engere Heuristik (fail-closed).
        paths_ignore_muster = None
        if precomputed_changed_paths is not None:
            paths_ignore_muster = await self._lade_paths_ignore(
                repo_full_name, workflow_names
            )

        if precomputed_changed_paths is not None and _paths_are_docs_only(
            precomputed_changed_paths, paths_ignore_muster
        ):
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
                #
                # ⚠️ Eine LEERE Antwort heisst hier NICHT "kein PR", sondern
                # meistens "GitHub hat die Zuordnung noch nicht indiziert".
                # Gemessen am 19.09.2026: Fuer den Squash-Commit ae34e13
                # lieferte `GET /commits/{sha}/pulls` zwei Sekunden nach dem
                # Merge eine leere Liste — Minuten spaeter korrekt PR #3458.
                #
                # Der Bot schloss daraus "Direkt-Push auf main", verwarf den
                # Kurzschluss ENDGUELTIG (die Pruefung liegt vor der
                # Polling-Schleife und wird nie wiederholt) und wartete dann
                # 30 Minuten auf einen Merge-Lauf, den es seit Paket A gar
                # nicht mehr gibt. Danach: Abbruch, Merge unausgeliefert.
                #
                # Aufgefallen ist es erst jetzt, weil die vorherigen Merges
                # Merge-Commits waren — fuer die greift Weg 1 ohne diese
                # Abfrage. Der erste Squash-Merge nach Paket A lief sofort
                # hinein.
                #
                # Deshalb wird eine leere Antwort wiederholt statt gedeutet.
                # Die Wartezeit traegt ausschliesslich der echte Direkt-Push
                # — und davon gab es in 30 Tagen null (gemessen 17.09.2026,
                # 51 Code-Commits, alle mit zugeordnetem PR). Ein Fehler
                # (None) wird NICHT wiederholt: Der ist bereits fail-closed
                # und eine Wiederholung verzoegerte nur.
                pr_heads = await self._fetch_pull_head_shas(repo_full_name, merged_sha)
                for versuch in range(2, _PR_ZUORDNUNG_VERSUCHE + 1):
                    if pr_heads is None or pr_heads:
                        break
                    self.logger.info(
                        "⏳ ZERODOX#3328 Paket A: PR-Zuordnung fuer "
                        f"{merged_sha[:7]} noch leer — GitHub indiziert "
                        f"verzoegert, Versuch {versuch}/{_PR_ZUORDNUNG_VERSUCHE} "
                        f"in {_PR_ZUORDNUNG_WARTE_S}s."
                    )
                    await asyncio.sleep(_PR_ZUORDNUNG_WARTE_S)
                    pr_heads = await self._fetch_pull_head_shas(
                        repo_full_name, merged_sha
                    )

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
                            f"{merged_sha[:7]} gehoert nach "
                            f"{_PR_ZUORDNUNG_VERSUCHE} Abfragen ueber "
                            f"{_PR_ZUORDNUNG_VERSUCHE * _PR_ZUORDNUNG_WARTE_S}s "
                            "kein gemergter PR (Direkt-Push auf main) — kein "
                            "Kurzschluss, es wird normal gepollt."
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

            # ZERODOX#2891: Gemerkte Laeufe, die in dieser Listenantwort fehlen,
            # einzeln nachfragen und in die Klassifikation einspeisen — die
            # bestehende Auswertung (success/failure/cancelled/superseded/
            # Neuversuch) greift dadurch unveraendert.
            nachgereicht = []
            for wf_name, gemerkt in gesehene_laeufe.items():
                if wf_name in latest_per_workflow or not gemerkt.get("url"):
                    continue
                try:
                    einzel = await self._fetch_workflow_run(gemerkt["url"])
                except Exception as e:  # fail-soft: wie bisher weiter pollen
                    einzel = None
                    self.logger.info(
                        f"ℹ️ ZERODOX#2891: Nachfrage fuer Lauf {gemerkt.get('id')} "
                        f"({wf_name}) fehlgeschlagen: {e}"
                    )
                if isinstance(einzel, dict) and einzel:
                    self.logger.info(
                        f"🔎 ZERODOX#2891: {wf_name} ({gemerkt.get('id')}) fehlt in der "
                        f"Listenabfrage fuer {merged_sha[:7]} — per Run-ID nachgefragt: "
                        f"status={einzel.get('status')}, "
                        f"conclusion={einzel.get('conclusion')}."
                    )
                    nachgereicht.append(einzel)
                else:
                    self.logger.info(
                        f"ℹ️ ZERODOX#2891: {wf_name} ({gemerkt.get('id')}) fehlt in der "
                        f"Listenabfrage fuer {merged_sha[:7]}, Nachfrage ohne Ergebnis — "
                        "weiter pollen."
                    )
            if nachgereicht:
                all_runs = list(all_runs) + nachgereicht
                (
                    relevant,
                    latest_per_workflow,
                    all_completed,
                    any_failed,
                    failed_run,
                    pending_names,
                ) = _klassifiziere_workflow_runs(all_runs, workflow_names_lower)

            for wf_name, lauf in latest_per_workflow.items():
                gesehene_laeufe[wf_name] = {
                    "id": lauf.get("id"),
                    "url": lauf.get("url") or gesehene_laeufe.get(wf_name, {}).get("url"),
                    "status": str(lauf.get("status") or "").lower(),
                }
            self._merke_letzten_ci_zustand(repo_full_name, merged_sha, gesehene_laeufe)

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
                # ZERODOX#3230: Wird der erwartete Workflow fuer DIESEN Commit
                # ueberhaupt noch starten?
                #
                # `Web Quality` hat einen `paths-ignore`-Filter. Bei einem
                # Docs-Merge legt GitHub den Lauf nie an — der Bot wartete
                # trotzdem 30 Minuten und meldete danach "Deployment
                # fehlgeschlagen". Am 09.09.2026 legte eine Docs-Serie die
                # Auslieferung so ueber eine Stunde lahm, weil nachfolgende
                # Merges waehrenddessen mit "already in progress" verworfen
                # wurden.
                #
                # Geprueft wird erst NACH dem Gnadenfenster und nur, wenn
                # nichts mehr offen ist: Dann hat GitHub die Events verarbeitet,
                # und was jetzt fehlt, kommt nicht mehr.
                #
                # ⚠️ Fail-closed: None (nicht ermittelbar) zaehlt wie "laeuft
                # noch". Und der Ausstieg ist KEIN "darf ohne CI deployen" —
                # `deploy.sh` hat ein eigenes Gate und entscheidet erneut; bei
                # einem Docs-only-Merge greift dort der Graceful-Skip (#1262).
                if (
                    admin_merge_deadline is not None
                    and time.monotonic() >= admin_merge_deadline
                    and not saw_any_relevant
                ):
                    noch_unterwegs = await self._laeuft_noch_ein_workflow(
                        repo_full_name, merged_sha
                    )
                    if noch_unterwegs is False:
                        self.logger.info(
                            f"ℹ️ _wait_for_ci_completion: fuer {merged_sha[:7]} ist kein "
                            f"Lauf mehr offen und {workflow_names} fehlt weiterhin — der "
                            "Workflow wurde per Path-Filter uebersprungen und startet "
                            "nicht mehr. Kein Wartegrund (ZERODOX#3230); deploy.sh "
                            "entscheidet mit seinem eigenen Gate."
                        )
                        return "no_workflows"

                self.logger.info(
                    f"⏳ _wait_for_ci_completion: noch keine relevanten Workflows "
                    f"fuer {merged_sha[:7]} sichtbar — weiter pollen ({poll_interval_s}s)..."
                )
                await asyncio.sleep(poll_interval_s)
                continue

            saw_any_relevant = True

            if any_failed:
                conclusion = str(failed_run.get("conclusion") or "").lower()
                # ZERODOX#3328 (Sammel-Zug, 22.09.2026): Web Quality laeuft
                # seither mit `cancel-in-progress: true` — ein neuerer Merge
                # bricht den CI-Lauf des aelteren Merges ab, geprueft und
                # ausgeliefert wird nur der neueste Stand. Ohne diese
                # Unterscheidung wertete `_klassifiziere_workflow_runs`
                # "cancelled" als FEHLGESCHLAGEN (`_CI_FAILURE_CONCLUSIONS`) —
                # jeder ueberholte Merge loeste einen Fehlalarm aus (belegt
                # 23.09.2026 01:49 fuer a50e956, ae31de6), obwohl der neuere
                # Merge den Stand ohnehin gesammelt ausliefert.
                #
                # Unterscheidung: Steht `branch` inzwischen auf einem ANDEREN
                # SHA als dem hier gewarteten, war der Abbruch der Sammel-Zug
                # — "superseded", kein Fehler. Steht `branch` noch auf
                # `merged_sha`, war der Abbruch ein echter manueller Cancel
                # (oder etwas anderes) und bleibt "failure" wie bisher. Ist
                # der Kopf nicht ermittelbar (API-Fehler) oder fehlt `branch`,
                # bleibt es fail-closed bei "failure" — lieber ein Alarm zu
                # viel als ein verschluckter echter Fehlschlag.
                if conclusion == "cancelled" and branch:
                    head_sha = await self._fetch_branch_head_sha(repo_full_name, branch)
                    if head_sha and head_sha != merged_sha:
                        self.logger.info(
                            f"↻ _wait_for_ci_completion: Stand {merged_sha[:7]} "
                            f"überholt durch {head_sha[:7]} — der neuere Merge "
                            "liefert gesammelt aus."
                        )
                        return "superseded"

                    # ZERODOX#2920 (26.09.2026): Der Branch-Kopf steht noch auf
                    # `merged_sha` (kein Sammel-Zug) — trotzdem ist "cancelled"
                    # nicht zwangslaeufig ein echter Testfehlschlag: Runner-Last
                    # bricht Jobs ab, ohne dass ein Test wirklich rot lief. Ist
                    # JEDER nicht-gruene relevante Lauf "cancelled" (kein echtes
                    # "failure"/"timed_out" darunter) und wurde fuer genau diesen
                    # Lauf noch KEIN automatischer Neuversuch unternommen, wird er
                    # EINMAL ueber `rerun-failed-jobs` angestossen und danach
                    # normal weitergepollt — das Wartefenster (`deadline`) laeuft
                    # dabei unveraendert weiter, es wird NICHT zurueckgesetzt.
                    if head_sha == merged_sha and cancelled_retry_enabled:
                        cancelled_runs = _nur_cancelled_ohne_echten_fehlschlag(
                            latest_per_workflow
                        )
                        if cancelled_runs:
                            offene_neuversuche = [
                                run
                                for run in cancelled_runs
                                if f"{repo_full_name}:{merged_sha}:{run.get('id')}"
                                not in self._ci_cancelled_retry_versucht
                            ]
                            # ZERODOX#2920 (Race): Nach `rerun-failed-jobs` behaelt
                            # der Lauf seine `id`, GitHub erhoeht nur `run_attempt`.
                            # Die Listenabfrage kann direkt danach noch den ALTEN
                            # Versuch als "cancelled" liefern. Ein gemerkter Lauf,
                            # dessen `run_attempt` nicht ueber dem beim Neuversuch
                            # gemerkten Wert liegt, ist deshalb veraltet — kein
                            # endgueltiges "failure", sondern weiter pollen.
                            veraltete_antworten = [
                                run
                                for run in cancelled_runs
                                if _ist_veralteter_versuch(
                                    self._ci_cancelled_retry_versucht.get(
                                        f"{repo_full_name}:{merged_sha}:{run.get('id')}"
                                    ),
                                    run,
                                )
                            ]
                            if offene_neuversuche:
                                neuversuch_ausgeloest = False
                                for run in offene_neuversuche:
                                    retry_key = (
                                        f"{repo_full_name}:{merged_sha}:{run.get('id')}"
                                    )
                                    # Review-Nacharbeit (#2920): Kopf unmittelbar
                                    # vor JEDEM POST erneut lesen. Ein Neuversuch
                                    # für einen alten Stand tritt in die
                                    # Concurrency-Gruppe mit cancel-in-progress ein
                                    # und bricht dort den Lauf des NEUEREN Merges
                                    # ab. Kopf weitergerückt → "superseded" wie
                                    # oben; Kopf nicht lesbar → kein POST.
                                    kopf_vor_post = await self._fetch_branch_head_sha(
                                        repo_full_name, branch
                                    )
                                    if kopf_vor_post and kopf_vor_post != merged_sha:
                                        self.logger.info(
                                            f"↻ _wait_for_ci_completion: Stand {merged_sha[:7]} "
                                            f"überholt durch {kopf_vor_post[:7]} — der neuere Merge "
                                            "liefert gesammelt aus."
                                        )
                                        return "superseded"
                                    if not kopf_vor_post:
                                        self.logger.warning(
                                            "⚠️ ZERODOX#2920: _wait_for_ci_completion: "
                                            "Branch-Kopf vor dem Neuversuch nicht lesbar — "
                                            f"kein Neuversuch für {run.get('name')} "
                                            f"({merged_sha[:7]})."
                                        )
                                        continue
                                    erfolg = await self._rerun_cancelled_workflow_run(
                                        repo_full_name, run.get("id")
                                    )
                                    if erfolg:
                                        # Wert statt True: der `run_attempt` zum
                                        # Zeitpunkt des Neuversuchs. Fehlt er im
                                        # Payload, bleibt es bei True (konservativ:
                                        # ein weiteres "cancelled" ist endgueltig).
                                        versuch = run.get("run_attempt")
                                        self._ci_cancelled_retry_versucht[retry_key] = (
                                            versuch if _ist_versuchsnummer(versuch) else True
                                        )
                                        # FIFO-Begrenzung auf ~200 Eintraege — sonst
                                        # waechst der Speicher mit jedem Merge
                                        # unbegrenzt (Bot-Laufzeit ueber Wochen/
                                        # Monate ohne Neustart).
                                        while len(self._ci_cancelled_retry_versucht) > 200:
                                            self._ci_cancelled_retry_versucht.popitem(
                                                last=False
                                            )
                                        neuversuch_ausgeloest = True
                                        self.logger.warning(
                                            "🔁 ZERODOX#2920: _wait_for_ci_completion: "
                                            f"{run.get('name')} fuer {merged_sha[:7]} war "
                                            "'cancelled' bei unveraendertem Branch-Kopf — "
                                            "automatischer Neuversuch ausgeloest "
                                            "(rerun-failed-jobs), kein Fehlalarm."
                                        )
                                    else:
                                        self.logger.warning(
                                            "⚠️ ZERODOX#2920: _wait_for_ci_completion: "
                                            f"Neuversuch fuer {run.get('name')} "
                                            f"({merged_sha[:7]}) konnte nicht ausgeloest "
                                            "werden — bleibt beim heutigen Verhalten "
                                            "(FAILED)."
                                        )
                                if neuversuch_ausgeloest:
                                    await asyncio.sleep(poll_interval_s)
                                    continue
                            elif veraltete_antworten:
                                self.logger.info(
                                    "⏳ ZERODOX#2920: _wait_for_ci_completion: "
                                    f"{[r.get('name') for r in veraltete_antworten]} fuer "
                                    f"{merged_sha[:7]} meldet noch den abgebrochenen "
                                    "Versuch vor dem Neuversuch (run_attempt nicht "
                                    "hoeher als gemerkt) — veraltete Listenantwort, "
                                    f"weiter pollen ({poll_interval_s}s)."
                                )
                                await asyncio.sleep(poll_interval_s)
                                continue
                            else:
                                self.logger.warning(
                                    "❌ _wait_for_ci_completion: CI FAILED fuer "
                                    f"{merged_sha[:7]} — abgebrochen (cancelled), "
                                    "automatischer Neuversuch bereits erfolgt "
                                    "(ZERODOX#2920)."
                                )
                                return "failure"

                self.logger.warning(
                    f"❌ _wait_for_ci_completion: CI FAILED fuer {merged_sha[:7]} "
                    f"(workflow={failed_run.get('name')}, conclusion={failed_run.get('conclusion')})"
                )
                return "failure"

            if all_completed:
                # Review-Nacharbeit (#2920): Wurde für diesen Stand ein
                # Neuversuch angestossen und ist main inzwischen weiter, ist der
                # grüne Neuversuch ein überholter Stand — "superseded" statt
                # "success", damit kein alter Stand ausgeliefert wird.
                praefix = f"{repo_full_name}:{merged_sha}:"
                if branch and any(
                    str(k).startswith(praefix)
                    for k in getattr(self, "_ci_cancelled_retry_versucht", {})
                ):
                    kopf_jetzt = await self._fetch_branch_head_sha(repo_full_name, branch)
                    if kopf_jetzt and kopf_jetzt != merged_sha:
                        self.logger.info(
                            f"↻ _wait_for_ci_completion: Stand {merged_sha[:7]} "
                            f"überholt durch {kopf_jetzt[:7]} — der neuere Merge "
                            "liefert gesammelt aus."
                        )
                        return "superseded"
                self.logger.info(
                    f"✅ _wait_for_ci_completion: alle CI-Workflows fuer {merged_sha[:7]} "
                    f"erfolgreich ({list(latest_per_workflow.keys())})"
                )
                return "success"

            zustand_text = _beschreibe_letzten_ci_zustand(gesehene_laeufe)
            self.logger.info(
                f"⏳ _wait_for_ci_completion: warte weiter auf {pending_names} "
                f"fuer {merged_sha[:7]} (next poll in {poll_interval_s}s)"
                + (f" — {zustand_text}" if zustand_text else "")
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

        zustand_text = _beschreibe_letzten_ci_zustand(gesehene_laeufe)
        self.logger.warning(
            f"⏰ _wait_for_ci_completion: TIMEOUT nach {max_wait_min}min "
            f"fuer {repo_full_name}@{merged_sha[:7]}"
            + (f" — {zustand_text}" if zustand_text else "")
        )
        return "timeout"

    def _merke_letzten_ci_zustand(
        self, repo_full_name: str, merged_sha: str, gesehene_laeufe: Dict[str, Dict]
    ) -> None:
        """ZERODOX#2891: Legt den zuletzt gesehenen Laufzustand fuer den
        Timeout-Alarm ab (begrenzt auf ~50 Eintraege, aeltester zuerst raus)."""
        speicher = getattr(self, "_ci_wait_letzter_zustand", None)
        if speicher is None:
            speicher = OrderedDict()
            self._ci_wait_letzter_zustand = speicher
        schluessel = f"{repo_full_name}:{merged_sha}"
        speicher.pop(schluessel, None)
        speicher[schluessel] = {k: dict(v) for k, v in gesehene_laeufe.items()}
        while len(speicher) > 50:
            speicher.popitem(last=False)

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
                # ZERODOX#2891: zuletzt gesehener Zustand (queued/in_progress)
                zustand_text = _beschreibe_letzten_ci_zustand(
                    getattr(self, "_ci_wait_letzter_zustand", {}).get(
                        f"{repo_full_name}:{merged_sha}", {}
                    )
                )
                title = f"⏰ {repo_name}: Deploy zurueckgestellt — CI nicht durch"
                color = 0xF1C40F
                description = (
                    f"Welle-9.10-Schutz: CI-Workflows ({', '.join(workflow_names) or '—'}) "
                    f"sind nach {max_wait_min} Minuten fuer Commit `{merged_sha[:7]}` "
                    f"noch nicht alle completed.\n\n"
                    + (f"Zuletzt gesehen: {zustand_text}.\n\n" if zustand_text else "")
                    + f"**deploy.sh wurde NICHT getriggert.** Sobald CI gruen ist, "
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
                # ZERODOX#2920: automatischer Neuversuch bei "cancelled".
                # Wer einen Deploy per Abbruch im GitHub-UI stoppen will:
                # zweimal abbrechen oder diesen Schalter auf false.
                cancelled_retry_enabled = bool(
                    project_config.get('ci_wait_cancelled_retry', True)
                )
                # ZERODOX#3391: Der zuletzt AUSGELIEFERTE Stand — HEAD des
                # Deploy-Baums, den `deploy_project` per `git pull` pflegt.
                # Damit prüft die Docs-only-Erkennung den Diff "live → HEAD"
                # statt nur die Commits dieses Pushes und übersieht keinen
                # zuvor verworfenen Auftrag mehr.
                #
                # ⚠️ Derselbe Pfad wie beim Re-Poll (`deploy_path` vor `path`):
                # Der Arbeitsbaum zeigt, was der Entwickler ausgecheckt hat,
                # nicht was live läuft.
                ausgelieferter_sha = None
                deploy_baum_raw = (
                    project_config.get('deploy_path') or project_config.get('path')
                )
                if deploy_baum_raw:
                    deploy_baum = Path(deploy_baum_raw)
                    if deploy_baum.exists():
                        ausgelieferter_sha = self._get_commit_sha(deploy_baum, 'HEAD')

                outcome = await self._wait_for_ci_completion(
                    repo_full_name=repo_full_name,
                    merged_sha=full_sha,
                    workflow_names=workflow_names,
                    max_wait_min=max_wait_min,
                    admin_merge_grace_min=admin_merge_grace_min,
                    poll_interval_sec=poll_interval_sec,
                    tree_sha_reuse_enabled=tree_sha_reuse_enabled,
                    pr_head_reuse_enabled=pr_head_reuse_enabled,
                    cancelled_retry_enabled=cancelled_retry_enabled,
                    push_commit_shas=push_commit_shas,
                    ausgelieferter_sha=ausgelieferter_sha,
                    branch=branch,
                )
                if outcome == "superseded":
                    # ZERODOX#3328: Sammel-Zug — der gewartete Stand wurde
                    # abgebrochen, WEIL ein neuerer Merge eingetroffen ist.
                    # Der INFO-Log mit beiden SHAs steht bereits in
                    # _wait_for_ci_completion; hier nur die Konsequenzen:
                    # Reservierung freigeben (der neuere Merge braucht sie),
                    # KEIN Discord-Alarm, KEIN deploy.sh-Aufruf.
                    self.logger.info(
                        f"↻ _trigger_deployment: {repo_name}@{commit_sha} überholt "
                        "durch einen neueren Merge — kein Fehler, kein Deploy fuer "
                        "diesen Stand."
                    )
                    self._release_deploy(repo_name, full_sha)
                    return "superseded"
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
            # ZERODOX#3447: Harte Zeitgrenze um den Deploy.
            #
            # Am 17.09.2026 meldete der Bot "Starting deployment: ZERODOX@224ff05"
            # und danach nichts mehr — kein Ergebnis, kein deploy.sh-Prozess,
            # kein Deploy-Log, der Deploy-Baum unveraendert, keine Ausnahme im
            # Journal. Der Auftrag war weg, und die Reservierung blieb gesetzt:
            # Erst ein Neustart des Bots loeste den Zustand.
            #
            # Ohne Grenze wartet dieser `await` unbegrenzt. Mit ihr laeuft der
            # Fall in den `except`-Block unten, der die Reservierung freigibt
            # und den Fehler SICHTBAR loggt — und `deploy_project` raeumt sein
            # `active_deployments` im eigenen `finally` auf, auch bei Abbruch.
            #
            # ⚠️ Die Grenze ist bewusst gross. Ein echter Deploy dauert ~4 min
            # (gemessen 17.09.), das CI-Warten liegt davor und zaehlt hier
            # nicht mit. 45 min ist rund das Zehnfache — sie greift also nur
            # bei einem Zustand, der ohnehin kaputt ist, und schneidet keinen
            # langsamen, aber gesunden Lauf ab. Wer sie kleiner setzt, riskiert
            # genau das.
            deploy_hard_timeout_min = int(
                (project_config or {}).get('deploy', {}).get('hard_timeout_min', 45)
            )
            result = await asyncio.wait_for(
                self.deployment_manager.deploy_project(
                    repo_name, branch, deploy_context=context
                ),
                timeout=max(1, deploy_hard_timeout_min) * 60,
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
        deployte Commit. Gemessen wird im DEPLOY-Baum (`deploy_path`, Fallback
        `path`): Dort hat `deploy_project()` per `git pull` aktualisiert, sein
        HEAD ist also der deployte Stand. Der Arbeitsbaum `path` taugt dafuer
        seit ZERODOX#2344 NICHT mehr — er zeigt, was der Entwickler gerade
        ausgecheckt hat, und liess den Re-Poll am 19.09.2026 zwei verworfene
        Merges uebersehen. Ursache
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

        # ⚠️ `deploy_path` vor `path` — sonst misst der Re-Poll den falschen Baum.
        #
        # Der Docstring oben sagt: "deploy_project() hat das lokale Repo bereits
        # per `git pull` aktualisiert, HEAD ist also der deployte Stand". Das
        # galt bis ZERODOX#2344. Seitdem deployt der Bot aus einem EIGENEN Baum
        # (`deploy_path`, bei ZERODOX ~/ZERODOX-deploy) und fasst den
        # Arbeitsbaum `path` nicht mehr an — dort steht, was der Entwickler
        # gerade ausgecheckt hat.
        #
        # Die Folge war ein Re-Poll, der zuverlaessig nichts tat: Ist der
        # Arbeitsbaum zufaellig aktuell (ein `git fetch` genuegt), gilt
        # `deployed_sha == remote_sha`, und die Funktion kehrt zurueck, ohne
        # den verpassten Deploy zu bemerken.
        #
        # Belegt am 19.09.2026: Zwei Merges (7df6f87, f35f855) trafen waehrend
        # eines laufenden Deploys ein und wurden vom `active_deployments`-Guard
        # verworfen — genau der Fall, fuer den dieser Re-Poll gebaut wurde. Er
        # lief nach dem erfolgreichen Deploy um 14:32 und meldete nichts; live
        # blieb 25 Minuten lang ein Stand hinter `origin/main`, darunter
        # unausgelieferter Billing-Code. Nachgezogen werden musste von Hand.
        #
        # Gleiche Auflösung wie `deployment_manager._deploy_path()`: Ohne
        # `deploy_path` bleibt alles wie bisher, fuer jedes andere Projekt ein
        # No-op.
        repo_path_raw = project_config.get('deploy_path') or project_config.get('path')
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
