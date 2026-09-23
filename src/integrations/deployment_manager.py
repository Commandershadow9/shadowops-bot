"""
Auto-Deployment System for ShadowOps Bot
Handles safe deployment with backups, tests, and rollback
"""

import asyncio
import logging
import shlex
import subprocess
import shutil
import time
import os
from typing import Dict, Optional, List, Tuple
from datetime import datetime, timezone
from pathlib import Path
import discord

try:  # pragma: no cover - Import-Pfad haengt von pythonpath ab
    from utils.alert_humanizer import format_downtime
except ImportError:  # pragma: no cover
    from src.utils.alert_humanizer import format_downtime  # type: ignore[no-redef]

# ⚠️ Bewusst 'shadowops.deployment' und NICHT getLogger(__name__).
#
# Die Handler des Bots haengen am Logger 'shadowops' (src/bot.py:497). Ein
# Logger namens `src.integrations.deployment_manager` liegt ausserhalb dieses
# Baums — seine Ausgaben erreichen das Journal nie. Jede Zeile dieses Moduls
# war damit unsichtbar, und ein Deploy hinterliess exakt EINE Spur: die
# Ergebniszeile, die `ci_mixin` selbst schreibt.
#
# Was das kostet, zeigte der 17.09.2026: Ein Deploy fuer 224ff05 meldete
# "Starting deployment" und danach nichts. Kein Ergebnis, kein deploy.sh-
# Prozess, kein Deploy-Log, der Deploy-Baum unveraendert. Es gab schlicht
# nichts, woran zu erkennen gewesen waere, WO er stehenblieb — 35 Minuten
# Diagnose fuer eine Frage, die eine sichtbare Log-Zeile beantwortet haette.
#
# `shadowops.deployment` erbt die Handler und benennt zugleich die Quelle,
# genau wie die bereits sichtbaren `shadowops.project_monitor` und
# `shadowops.context`.
#
# ⚠️ Dasselbe Muster steckt in weiteren Modulen (incident_manager,
# self_healing, customer_notifications, zerodox_auto_fix_gate und mehr). Sie
# sind hier bewusst NICHT mitgeaendert: Fuer den Deploy-Pfad ist der Schaden
# belegt, fuer die anderen nicht — und eine Sammeländerung ohne Befund macht
# den PR unpruefbar.
logger = logging.getLogger('shadowops.deployment')


# Marker, an denen ein gesammelter Deploy-Step als fehlgeschlagen erkannt wird.
_STEP_FAIL_MARKERS = ("❌", "fehlgeschlagen", "failed", "error", "fehler", "abort")


def _summarize_steps(steps: List[str]) -> Tuple[int, int, Optional[str]]:
    """Fasst gesammelte Deploy-Steps zusammen.

    Returns: (anzahl_ok, anzahl_gesamt, erster_fehlgeschlagener_step_oder_None).
    Ein Step gilt als fehlgeschlagen, wenn er einen der _STEP_FAIL_MARKERS
    (case-insensitiv) enthält.
    """
    total = len(steps)
    failed_step: Optional[str] = None
    ok = 0
    for step in steps:
        low = step.lower()
        if any(m in low for m in _STEP_FAIL_MARKERS):
            if failed_step is None:
                failed_step = step
        else:
            ok += 1
    return ok, total, failed_step


def _format_deploy_duration(duration: float) -> str:
    """Deploy-Dauer mit Kontext: kurze Deploys in Sekunden, lange via Klartext."""
    if duration < 90:
        return f"{duration:.1f}s"
    return format_downtime(duration)


def _format_deploy_trigger(context: Optional[Dict]) -> Optional[str]:
    """Formatiert Commit, PR und Issues als klickbaren Discord-Kontext."""
    if not context:
        return None
    parts: List[str] = []
    pr_number = context.get("pr_number")
    pr_url = context.get("pr_url")
    pr_title = str(context.get("pr_title") or "").strip()
    if pr_number:
        label = f"PR #{pr_number}"
        if pr_title:
            label += f" · {pr_title[:120]}"
        parts.append(f"[{label}]({pr_url})" if pr_url else label)

    issues = context.get("issues") or []
    issue_links = []
    repo_url = str(context.get("repo_url") or "").rstrip("/")
    for number in issues[:5]:
        label = f"Issue #{number}"
        issue_links.append(
            f"[{label}]({repo_url}/issues/{number})" if repo_url else label
        )
    if issue_links:
        parts.append("Betroffen: " + ", ".join(issue_links))

    sha = str(context.get("commit_sha") or "").strip()
    commit_url = context.get("commit_url")
    if sha:
        short_sha = sha[:7]
        parts.append(
            f"Commit [`{short_sha}`]({commit_url})" if commit_url else f"Commit `{short_sha}`"
        )
    return "\n".join(parts) or None


def _concise_deploy_error(error: str) -> str:
    """Extrahiert den aussagekräftigsten Fehler statt beliebig abzuschneiden."""
    lines = [line.strip() for line in str(error).splitlines() if line.strip()]
    if not lines:
        return "Kein Fehlergrund gemeldet."
    preferred = [
        line for line in lines
        if any(marker in line.lower() for marker in ("fehler:", "error:", "failed", "cancelled", "timeout"))
    ]
    selected = preferred[-1] if preferred else lines[-1]
    for prefix in ("stderr:", "stdout:"):
        if selected.lower().startswith(prefix):
            selected = selected[len(prefix):].strip()
    return selected[:1000]


class DeploymentManager:
    """
    Automated deployment system with safety checks

    Features:
    - Git pull automation
    - Pre-deployment tests
    - Automatic backup creation
    - Health checks after deployment
    - Automatic rollback on failure
    - Discord notifications
    """

    def __init__(self, bot, config: Dict):
        """
        Initialize deployment manager

        Args:
            bot: Discord bot instance
            config: Configuration dictionary with projects and deployment settings
        """
        self.bot = bot
        self.config = config
        self.logger = logger

        # Project configurations
        self.projects = self._load_projects()

        # Deployment settings
        deployment_config = getattr(config, 'deployment', {})
        if isinstance(deployment_config, dict):
            self.backup_dir = Path(deployment_config.get('backup_dir', 'backups')).resolve()
            self.max_backups_per_project = deployment_config.get('max_backups', 5)
            self.health_check_timeout = deployment_config.get('health_check_timeout', 30)
            self.test_timeout = deployment_config.get('test_timeout', 300)
        else:
            self.backup_dir = Path(getattr(deployment_config, 'backup_dir', 'backups')).resolve()
            self.max_backups_per_project = getattr(deployment_config, 'max_backups', 5)
            self.health_check_timeout = getattr(deployment_config, 'health_check_timeout', 30)
            self.test_timeout = getattr(deployment_config, 'test_timeout', 300)

        self.backup_dir.mkdir(parents=True, exist_ok=True)

        # Discord notification channel
        self.deployment_channel_id = config.channels.get('deployment_log', 0)

        # Track active deployments
        self.active_deployments: Dict[str, bool] = {}

        # Wartende Auftraege — je Projekt hoechstens EINER, und zwar der neueste.
        # Siehe `auftrag_vormerken` fuer die Begruendung, warum keine Historie.
        self.pending_deployments: Dict[str, Dict] = {}

        self.logger.info(f"🔧 Deployment Manager initialized for {len(self.projects)} projects")

    # Obergrenze fuer eine Kette von Nachhol-Deploys. Ohne sie koennte ein
    # Auftrag, der bei jedem Lauf erneut vorgemerkt wird, endlos weiterlaufen —
    # die Grenze macht aus einer moeglichen Endlosschleife eine Meldung.
    NACHHOL_MAX = 3

    def auftrag_vormerken(
        self,
        project_key: str,
        branch: Optional[str],
        deploy_context: Optional[Dict],
    ) -> bool:
        """Merkt einen Deploy-Auftrag vor, wenn gerade einer laeuft.

        Liefert `True`, wenn vorgemerkt wurde, `False`, wenn die Sperre frei ist
        (dann gehoert der Auftrag nicht in die Warteschlange, sondern direkt
        ausgefuehrt — sonst liefe jeder gewoehnliche Deploy zweimal).

        ⚠️ **Es wird nur der NEUESTE Auftrag gehalten, bewusst ohne Historie.**
        Drei Merges waehrend eines Deploys ergeben genau einen Nachholer, und
        der deployt `origin/main` — also den Stand, der alle drei enthaelt. Eine
        echte Queue wuerde denselben Endzustand dreimal ausliefern.
        """
        if not self.active_deployments.get(project_key, False):
            return False

        vorher = self.pending_deployments.get(project_key)
        self.pending_deployments[project_key] = {
            'branch': branch,
            'deploy_context': dict(deploy_context or {}),
            'vorgemerkt_um': datetime.now().isoformat(timespec='seconds'),
        }
        if vorher is None:
            self.logger.info(
                f"📥 Deploy-Auftrag fuer '{project_key}' vorgemerkt — laeuft nach dem aktuellen Deploy."
            )
        else:
            self.logger.info(
                f"📥 Deploy-Auftrag fuer '{project_key}' ersetzt den vorgemerkten "
                f"(nur der neueste Stand wird nachgeholt)."
            )
        return True

    def wartenden_auftrag_entnehmen(self, project_key: str) -> Optional[Dict]:
        """Entnimmt den vorgemerkten Auftrag — genau einmal.

        Entnehmen statt Lesen, damit ein Auftrag nicht doppelt laeuft, wenn
        zwei Stellen gleichzeitig nachsehen.
        """
        return self.pending_deployments.pop(project_key, None)

    def nachhol_grenze_erreicht(self, bisherige_nachholer: int) -> bool:
        """Ist die Kette von Nachhol-Deploys am Ende?"""
        return bisherige_nachholer >= self.NACHHOL_MAX

    def _load_projects(self) -> Dict[str, Dict]:
        """Load project configurations from config"""
        projects = {}
        projects_config = getattr(self.config, 'projects', {})

        for project_name, project_config in projects_config.items():
            if not project_config.get('enabled', False):
                continue

            # Check if deployment is enabled for this project
            deploy_config = project_config.get('deploy', {})
            deploy_enabled = deploy_config.get('enabled', True)  # Default to True for backwards compatibility

            projects[project_name] = {
                'name': project_name,
                'path': Path(project_config.get('path', '')),
                # Optionaler eigener Baum für den Deploy (ZERODOX #2344).
                # Ohne diesen Eintrag bleibt alles wie bisher — `_deploy_path`
                # faellt dann auf 'path' zurueck. Bewusst getrennt gehalten:
                # 'path' steuert ausserdem Backup-Monitoring, Disk-Checks,
                # Kontext, Verifikation und Polling; es umzubiegen laegte fuenf
                # Funktionen um, um eine zu reparieren.
                'deploy_path': project_config.get('deploy_path'),
                'branch': project_config.get('branch', 'main'),
                'deploy_enabled': deploy_enabled,  # NEW: Track if deploy is enabled
                'run_tests': deploy_config.get('run_tests', False),
                'test_command': deploy_config.get('test_command', 'pytest'),
                'post_deploy_command': deploy_config.get('post_deploy_command', None),
                'health_check_url': project_config.get('monitor', {}).get('url', ''),
                'service_name': deploy_config.get('service_name', None)
            }

            status = "✅" if deploy_enabled else "⏭️ (deploy disabled)"
            self.logger.info(f"{status} Loaded deployment config for: {project_name}")

        return projects

    async def deploy_project(
        self,
        project_name: str,
        branch: Optional[str] = None,
        deploy_context: Optional[Dict] = None,
        _nachhol_tiefe: int = 0,
    ) -> Dict:
        """
        Deploy a project with full safety workflow

        Args:
            project_name: Name of the project to deploy
            branch: Git branch to deploy (defaults to project config)

        Returns:
            Deployment result dictionary with success status and details
        """
        start_time = time.time()

        # Check if project exists (case-insensitive + dash/underscore fallback).
        # GitHub-Repo-Namen verwenden Bindestriche (z.B. "mayday-sim"), Config-Keys
        # oft Underscores ("mayday_sim"). Vorfall 2026-05-25 (PR #449/#450):
        # Auto-Deploy schlug fehl mit "Project 'mayday-sim' not found".
        project_key = project_name
        if project_key not in self.projects:
            normalized = project_name.lower().replace("-", "_")
            for key in self.projects.keys():
                if key.lower() == project_name.lower() or key.lower().replace("-", "_") == normalized:
                    project_key = key
                    break
        if project_key not in self.projects:
            error_msg = f"Project '{project_name}' not found in deployment config"
            self.logger.error(f"❌ {error_msg}")
            return {
                'success': False,
                'error': error_msg,
                'duration_seconds': 0
            }

        # Laeuft schon ein Deploy? Dann VORMERKEN statt verwerfen (ZERODOX#3532ff).
        #
        # Bis zum 21.09.2026 endete dieser Zweig mit `success: False` — der
        # Auftrag war weg. An jenem Tag blieben dadurch drei fertig gebaute
        # Staende liegen (Merges 17:50 und 17:50 waehrend eines Deploys, der um
        # 17:08 begann und 36 Minuten lief). Fuer alle drei hatte
        # `Release Image (GHCR)` erfolgreich ein Image gebaut; ausgeliefert
        # wurde keines.
        #
        # ⚠️ Der eingebaute Nachhol-Weg greift hier NICHT:
        # `_schedule_ci_success_reconcile` haengt an einem `workflow_run` mit
        # `event_name == 'push'` auf einem Deploy-Branch — und seit
        # ZERODOX#3328 gibt es keinen Merge-Lauf auf `main` mehr. Das
        # ausloesende Event kommt nie. Beide Aenderungen sind einzeln richtig;
        # zusammen ergaben sie: verworfene Auftraege bleiben verworfen.
        if self.active_deployments.get(project_key, False):
            self.auftrag_vormerken(project_key, branch, deploy_context)
            return {
                'success': True,
                'queued': True,
                'error': None,
                'duration_seconds': 0,
                'message': (
                    f"Deploy fuer '{project_name}' vorgemerkt — laeuft direkt nach dem "
                    f"aktuellen Deploy. Nur der neueste Stand wird nachgeholt."
                ),
            }

        # Check if deployment is disabled for this project
        if not self.projects[project_key].get('deploy_enabled', True):
            self.logger.info(f"⏭️ Deployment disabled for '{project_name}' - skipping (handled by CI)")
            return {
                'success': True,
                'skipped': True,
                'error': None,
                'duration_seconds': 0,
                'message': 'Deployment handled by GitHub Actions CI'
            }

        # Mark deployment as active
        self.active_deployments[project_key] = True

        try:
            project = self.projects[project_key]
            deploy_branch = branch or project['branch']
            context = dict(deploy_context or {})
            context['branch'] = deploy_branch

            self.logger.info(f"🚀 Starting deployment: {project_name} @ {deploy_branch}")

            result = {
                'success': False,
                'project': project_name,
                'branch': deploy_branch,
                'duration_seconds': 0,
                'tests_passed': None,
                'backup_created': False,
                'deployed': False,
                'rolled_back': False,
                'error': None,
                'deploy_context': context,
                'failed_stage': None,
            }

            # Self-Deploy: Kompakter Flow — nur git pull + 1 Embed + Restart
            is_self_deploy = (project_name == 'shadowops-bot')

            if not is_self_deploy:
                # Normale Projekte: Volles Deployment mit allen Schritten
                await self._send_deployment_started(
                    project_name, deploy_branch, deploy_context=context
                )

            current_stage = "Projektpfad prüfen"

            # Step 1: Validate project path
            if not project['path'].exists():
                raise DeploymentError(f"Project path does not exist: {project['path']}")

            # Step 2: Create backup
            current_stage = "Backup erstellen"
            self.logger.info(f"📦 Creating backup for {project_name}")
            if not is_self_deploy:
                await self._send_deployment_update(project_name, "📦 Creating backup...")
            backup_path = await self._create_backup(project)
            result['backup_created'] = True
            self.logger.info(f"✅ Backup created: {backup_path}")
            if not is_self_deploy:
                await self._send_deployment_update(project_name, f"✅ Backup created: {backup_path.name}")

            # Step 3: Pull latest code
            current_stage = "Code aktualisieren"
            self.logger.info(f"📥 Pulling latest code from {deploy_branch}")
            if not is_self_deploy:
                await self._send_deployment_update(project_name, f"📥 Pulling latest code from {deploy_branch}...")
            await self._git_pull(project, deploy_branch)
            if not is_self_deploy:
                await self._send_deployment_update(project_name, "✅ Code updated")

            # Step 4: Run tests (if configured)
            if project['run_tests']:
                current_stage = "Tests ausführen"
                self.logger.info(f"🧪 Running tests for {project_name}")
                if not is_self_deploy:
                    await self._send_deployment_update(project_name, "🧪 Running tests...")
                tests_passed = await self._run_tests(project)
                result['tests_passed'] = tests_passed

                if not tests_passed:
                    await self._send_deployment_update(project_name, "❌ Tests failed!")
                    raise DeploymentError("Tests failed")

                self.logger.info(f"✅ Tests passed")
                if not is_self_deploy:
                    await self._send_deployment_update(project_name, "✅ All tests passed")
            else:
                self.logger.info(f"⏭️ Skipping tests (not configured)")

            # Self-Deploy: 1 Success-Embed + verzögerter Restart
            if is_self_deploy:
                result['deployed'] = True
                duration = time.time() - start_time
                result['success'] = True
                result['duration_seconds'] = duration
                self.logger.info(f"✅ Self-deploy: {project_name} ({duration:.1f}s) — Restart in 5s")
                await self._send_deployment_success(project_name, deploy_branch, duration, result)
                import subprocess
                subprocess.Popen(
                    ['bash', '-c', 'sleep 5 && sudo systemctl restart shadowops-bot'],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                self.active_deployments[project_key] = False
                return result

            # Step 5: Execute post-deploy command (if configured)
            if project['post_deploy_command']:
                current_stage = "Post-Deploy ausführen"
                self.logger.info(f"⚙️ Running post-deploy command")
                await self._send_deployment_update(project_name, f"⚙️ Running post-deploy: {project['post_deploy_command']}")
                await self._run_post_deploy_command(project, project_name=project_name)
                self.logger.info(f"✅ Post-deploy command completed")
                await self._send_deployment_update(project_name, "✅ Post-deploy completed")

            # Step 6: Restart service (if configured)
            if project['service_name']:
                current_stage = "Dienst neu starten"
                self.logger.info(f"🔄 Restarting service: {project['service_name']}")
                await self._send_deployment_update(project_name, f"🔄 Restarting service: {project['service_name']}...")
                await self._restart_service(project)
                self.logger.info(f"✅ Service restarted")
                await self._send_deployment_update(project_name, "✅ Service restarted")

            result['deployed'] = True

            # Step 7: Health check
            if project['health_check_url']:
                current_stage = "Health-Check ausführen"
                self.logger.info(f"🏥 Running health check")
                await self._send_deployment_update(project_name, "🏥 Running health check...")
                health_ok = await self._health_check(project)

                if not health_ok:
                    await self._send_deployment_update(project_name, "❌ Health check failed!")
                    raise DeploymentError("Health check failed after deployment")

                self.logger.info(f"✅ Health check passed")
                await self._send_deployment_update(project_name, "✅ Health check passed")

            # Success!
            duration = time.time() - start_time
            result['success'] = True
            result['duration_seconds'] = duration

            self.logger.info(f"✅ Deployment successful: {project_name} ({duration:.1f}s)")

            # Send Discord notification: Deployment success
            await self._send_deployment_success(project_name, deploy_branch, duration, result)

            return result

        except PostDeployTempfailError as e:
            # ZERODOX#3577: EX_TEMPFAIL (75) heisst "voruebergehend
            # verhindert, kein Fehlschlag" — weder Rollback noch Fehler-
            # Alarm. Ohne diesen eigenen Zweig faellt die Ausnahme (als
            # DeploymentError-Unterklasse) sonst in den Zweig darunter und
            # loest genau das aus, was hier verhindert werden soll: einen
            # Rollback der unveraenderten Arbeitskopie und einen roten
            # Discord-Alarm fuer einen Lauf, der schlicht vom naechsten,
            # neueren Merge ueberholt wurde bzw. auf eine belegte
            # Deploy-Sperre traf.
            self.logger.info(f"↻ Deploy voruebergehend zurueckgestellt: {e}")

            result['error'] = str(e)
            result['failed_stage'] = current_stage
            duration = time.time() - start_time
            result['duration_seconds'] = duration

            await self._send_deployment_update(
                project_name,
                f"↻ {current_stage} voruebergehend zurueckgestellt — "
                f"kein Fehler, wird nachgeholt.",
            )

            # Kein Rollback: Es wurde nichts Kaputtes deployt — deploy.sh
            # ist gar nicht bis zum Ausliefern gekommen. Kein Discord-
            # Fehler-Alarm (`_send_deployment_failure`): ci_mixin wertet
            # das Ergebnis ueber den "exit=75"-Marker in `result['error']`
            # als "transient"/"superseded" und alarmiert dort bewusst nicht.
            return result

        except DeploymentError as e:
            # Deployment failed, attempt rollback
            self.logger.error(f"❌ Deployment failed: {e}")

            result['error'] = str(e)
            result['failed_stage'] = current_stage
            await self._send_deployment_update(
                project_name, f"❌ {current_stage} fehlgeschlagen"
            )
            duration = time.time() - start_time
            result['duration_seconds'] = duration

            # Rollback if backup exists
            if result['backup_created']:
                try:
                    self.logger.warning(f"🔄 Attempting rollback for {project_name}")
                    await self._send_deployment_update(project_name, "🔄 Attempting automatic rollback...")
                    await self._rollback(project, backup_path)
                    result['rolled_back'] = True
                    self.logger.info(f"✅ Rollback successful")
                    await self._send_deployment_update(project_name, "✅ Rollback successful")

                    # Restart service after rollback
                    if project['service_name']:
                        await self._restart_service(project)

                except Exception as rollback_error:
                    self.logger.error(
                        f"❌ Rollback failed: {rollback_error}",
                        exc_info=True
                    )
                    result['error'] += f" | Rollback failed: {rollback_error}"
                    await self._send_deployment_update(project_name, f"❌ Rollback failed: {rollback_error}")

            # Send Discord notification: Deployment failure
            await self._send_deployment_failure(project_name, deploy_branch, duration, result)

            return result

        except Exception as e:
            # Unexpected error
            self.logger.error(f"💥 Deployment exception: {e}", exc_info=True)

            result['error'] = str(e)
            result['failed_stage'] = current_stage
            await self._send_deployment_update(
                project_name, f"❌ {current_stage}: unerwarteter Fehler"
            )
            duration = time.time() - start_time
            result['duration_seconds'] = duration

            # Dieselbe detaillierte Abschlussmeldung wie bei erwarteten Fehlern
            # verwenden, damit keine gelbe Fortschrittsmeldung stehen bleibt.
            await self._send_deployment_failure(
                project_name, deploy_branch, duration, result
            )

            return result

        finally:
            # Mark deployment as complete
            self.active_deployments[project_key] = False

            # Wartet ein Auftrag? Dann jetzt nachholen (ZERODOX#3532ff).
            #
            # Als Hintergrundaufgabe, nicht per Rekursion: Dieser Block laeuft
            # im `finally` des gerade beendeten Deploys, und ein `await` hier
            # wuerde dessen Rueckgabe so lange verzoegern, bis die ganze Kette
            # durch ist — der Aufrufer (Discord-Handler) waere blockiert.
            wartend = self.wartenden_auftrag_entnehmen(project_key)
            if wartend is not None:
                if self.nachhol_grenze_erreicht(_nachhol_tiefe):
                    self.logger.warning(
                        f"⚠️ Nachhol-Grenze ({self.NACHHOL_MAX}) fuer '{project_name}' erreicht — "
                        f"kein weiterer Nachhol-Deploy. Der vorgemerkte Auftrag wurde verworfen; "
                        f"der buildSha-Drift-Waechter bleibt der Backstop."
                    )
                else:
                    self.logger.info(
                        f"🔁 Hole vorgemerkten Deploy fuer '{project_name}' nach "
                        f"(Kette {_nachhol_tiefe + 1}/{self.NACHHOL_MAX})."
                    )
                    asyncio.create_task(
                        self._nachhol_deploy(
                            project_name=project_name,
                            branch=wartend.get('branch'),
                            deploy_context=wartend.get('deploy_context'),
                            naechste_tiefe=_nachhol_tiefe + 1,
                        )
                    )

    async def _nachhol_deploy(
        self,
        project_name: str,
        branch: Optional[str],
        deploy_context: Optional[Dict],
        naechste_tiefe: int,
    ) -> None:
        """Fuehrt einen vorgemerkten Deploy aus und protokolliert das Ergebnis.

        Eigene Methode, damit der Hintergrund-Task eine Fehlerbehandlung hat:
        Eine Ausnahme in einem `asyncio.create_task` ohne `await` verschwindet
        sonst in der Ereignisschleife und taucht allenfalls als
        "Task exception was never retrieved" auf.
        """
        try:
            ergebnis = await self.deploy_project(
                project_name,
                branch=branch,
                deploy_context=deploy_context,
                _nachhol_tiefe=naechste_tiefe,
            )
            if ergebnis.get('success'):
                self.logger.info(f"✅ Nachhol-Deploy fuer '{project_name}' erfolgreich.")
            else:
                self.logger.warning(
                    f"⚠️ Nachhol-Deploy fuer '{project_name}' fehlgeschlagen: "
                    f"{ergebnis.get('error')}"
                )
        except Exception as fehler:  # noqa: BLE001 — ein Hintergrund-Task darf nie still sterben
            self.logger.error(
                f"❌ Nachhol-Deploy fuer '{project_name}' brach mit einer Ausnahme ab: {fehler}",
                exc_info=True,
            )

    async def _create_backup(self, project: Dict) -> Path:
        """
        Create timestamped backup of project

        Args:
            project: Project configuration

        Returns:
            Path to backup directory
        """
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_name = f"{project['name']}_{timestamp}"
        backup_path = self.backup_dir / backup_name

        ignore = shutil.ignore_patterns(
            ".git",
            "__pycache__",
            "*.pyc",
            "node_modules",
            "venv",
            "backups",
            # ZERODOX#3447: siehe BACKUP_FLUECHTIG unten. `ignore_patterns`
            # vergleicht nur Basisnamen, `.claude/worktrees` ist deshalb hier
            # nicht ausdrueckbar — dieser Zweig laeuft aber ohnehin nur ohne
            # rsync, und rsync liegt auf allen Deploy-Hosts.
            ".next",
        )

        if shutil.which("rsync"):
            # Create backup using rsync for efficiency
            # --no-perms --no-group --no-owner: Avoid chgrp/chown errors when source
            # files are owned by Docker container user (uid 1001, gid 65533)
            cmd = [
                'rsync', '-rlptD',
                '--no-perms', '--no-group', '--no-owner',
                '--exclude=.git',
                '--exclude=.env',
                '--exclude=.venv',
                '--exclude=__pycache__',
                '--exclude=*.pyc',
                '--exclude=node_modules',
                '--exclude=venv',
                '--exclude=backups',
                '--exclude=logs',
                '--exclude=uploads',
                # ZERODOX#3447: Was reproduzierbar oder fluechtig ist, gehoert
                # nicht ins Deploy-Backup.
                #
                # Gemessen am 19.09.2026 an zerodox_20260918_145640 — 22 GB:
                #
                #     18   GB  .claude/worktrees/  Arbeitskopien paralleler
                #                                  Claude-Sessions
                #      4,1 GB  web/.next/          Build-Output, den der Deploy
                #                                  ohnehin neu erzeugt
                #     ~0,2 GB                      alles Uebrige — der Code,
                #                                  also der einzige Grund fuer
                #                                  dieses Backup
                #
                # Die Folgen trug jeder Deploy: Das Backup brauchte 5m28s von
                # 10m46s Gesamtzeit (18.09., 14:56:40 bis 15:02:08), fuenf
                # Staende je Projekt belegten 112 GB, und bei rund 21 Merges am
                # Tag schrieb der Bot etwa 460 GB taeglich auf die NVMe.
                #
                # ⚠️ `.claude/worktrees` MIT Pfad, nicht nur `worktrees`: Im
                # ZERODOX-Baum liegt daneben ein unversioniertes `worktrees/`
                # mit anderem Inhalt. Ein blosser Basisname schluesse beide aus.
                '--exclude=.claude/worktrees',
                '--exclude=.next',
                str(project['path']) + '/',
                str(backup_path) + '/'
            ]

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            stdout, stderr = await process.communicate()

            # rsync exit code 23 = partial transfer (z.B. Permission Denied
            # auf Docker-Container-Dateien). Für Backup akzeptabel.
            #
            # 24 = "some files vanished before they could be transferred".
            # Am 18.09.2026 um 12:56 brach daran ein kompletter ZERODOX-Deploy
            # ab: Eine parallele Claude-Session entfernte waehrend des Backups
            # ihren Worktree (.claude/worktrees/watchdog-liste-3328). Verloren
            # ging dabei nichts Schuetzenswertes — eine Datei, die es beim
            # Kopieren nicht mehr gibt, ist per Definition keine, die gesichert
            # werden musste.
            #
            # ⚠️ Der Ausschluss der Worktrees oben macht genau diesen Fall
            # unwahrscheinlich, aber nicht unmoeglich: Auch Logdateien und
            # temporaere Dateien verschwinden waehrend eines mehrminuetigen
            # Laufs. Ein Deploy, der daran scheitert, verwechselt einen
            # Nebeneffekt mit einem Fehler.
            if process.returncode not in (0, 23, 24):
                raise DeploymentError(f"Backup failed: {stderr.decode()}")

            if process.returncode == 24:
                self.logger.info(
                    "ℹ️ Backup: einzelne Dateien verschwanden waehrend des Laufs "
                    "(rsync 24) — kein Fehler, Backup gilt als erstellt."
                )
        else:
            self.logger.warning("⚠️ rsync not found, using Python copy for backup")
            await self._send_deployment_update(
                project['name'],
                "⚠️ rsync fehlt, nutze Python-Backup (langsamer).",
            )
            await asyncio.to_thread(
                shutil.copytree,
                project['path'],
                backup_path,
                ignore=ignore,
                dirs_exist_ok=True,
            )

        try:
            os.utime(backup_path, None)
        except OSError as exc:
            self.logger.warning(f"⚠️ Backup-Zeitstempel konnte nicht aktualisiert werden: {exc}")

        # Clean up old backups
        await self._cleanup_old_backups(project['name'])

        return backup_path

    async def _cleanup_old_backups(self, project_name: str):
        """Remove old backups, keeping only the most recent N"""
        project_backups = sorted(
            [b for b in self.backup_dir.iterdir() if b.name.startswith(project_name)],
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )

        # Remove backups beyond max count
        for old_backup in project_backups[self.max_backups_per_project:]:
            self.logger.info(f"🗑️ Removing old backup: {old_backup.name}")
            shutil.rmtree(old_backup)

    def _deploy_path(self, project: Dict) -> Path:
        """
        Verzeichnis, aus dem deployt wird.

        Bewusst getrennt von `project['path']`: Dieses Feld steuert ausserdem
        Backup-Monitoring (`Path(path)/'backups'/'daily'`), Disk-Schwellwerte,
        Kontext, Verifikation und GitHub-Polling — acht Dateien insgesamt. Wer
        einfach `path` auf einen Deploy-Baum umbiegt, lenkt fuenf Funktionen um,
        um eine zu reparieren, und macht dabei das Backup-Monitoring blind.

        Ohne `deploy_path` bleibt alles wie bisher; die Umstellung ist damit
        für jedes andere Projekt ein No-op.

        Args:
            project: Project configuration

        Returns:
            Pfad, in dem Git-Schritt, Tests und post-deploy laufen sollen
        """
        return Path(project.get('deploy_path') or project['path'])

    async def _git_pull(self, project: Dict, branch: str):
        """
        Bringt den Deploy-Baum hart auf den Stand von origin/<branch>.

        Frueher: fetch + checkout + `git pull`. Das hat Merge-Semantik und
        scheitert deshalb an dem, was der Arbeitsbaum gerade ist — in der Nacht
        zum 15.08.2026 zweimal (ZERODOX #2344):

          "local changes ... would be overwritten by merge ... Aborting"
          "Not possible to fast-forward, aborting"

        Sechs Merges blieben dadurch stundenlang undeployt, ohne Selbstheilung:
        Nach fehlgeschlagenem deploy.sh gibt es keinen Retry, und der Reconcile
        scheitert an derselben Stelle.

        `fetch --prune` + `reset --hard` ist idempotent und immun gegen dirty,
        divergiert und abgebrochenen Rebase gleichermassen. Man kann nicht für
        jeden Zustand einen eigenen Guard bauen — die Abhaengigkeit muss weg.

        ⚠️ Verwirft lokale Aenderungen im Deploy-Baum. Genau deshalb gehoert
        dorthin ein Verzeichnis, in dem kein Mensch arbeitet (`deploy_path`).

        Args:
            project: Project configuration
            branch: Branch to deploy
        """
        cwd = str(self._deploy_path(project))

        # Fetch latest (--prune raeumt entfernte Branches mit ab)
        fetch_cmd = ['git', 'fetch', '--prune', 'origin', branch]
        process = await asyncio.create_subprocess_exec(
            *fetch_cmd,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await process.communicate()

        if process.returncode != 0:
            raise DeploymentError(f"Git fetch failed: {stderr.decode()}")

        # Hart auf den Remote-Stand setzen — kein checkout, kein merge.
        reset_cmd = ['git', 'reset', '--hard', f'origin/{branch}']
        process = await asyncio.create_subprocess_exec(
            *reset_cmd,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            raise DeploymentError(f"Git reset --hard origin/{branch} failed: {stderr.decode()}")

    async def _run_tests(self, project: Dict) -> bool:
        """
        Run project tests

        Args:
            project: Project configuration

        Returns:
            True if tests passed
        """
        test_cmd = project['test_command'].split()

        process = await asyncio.create_subprocess_exec(
            *test_cmd,
            cwd=str(self._deploy_path(project)),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=self.test_timeout
            )

            return process.returncode == 0

        except asyncio.TimeoutError:
            process.kill()
            raise DeploymentError(f"Tests timed out after {self.test_timeout}s")

    async def _run_post_deploy_command(
        self, project: Dict, project_name: Optional[str] = None
    ):
        """
        Run post-deployment command (e.g., npm install, pip install)

        Args:
            project: Project configuration
        """
        raw_cmd = project['post_deploy_command']
        uses_shell = any(token in raw_cmd for token in ['&&', ';', '|', '>', '<']) or raw_cmd.strip().startswith('cd ')

        if uses_shell:
            process = await asyncio.create_subprocess_exec(
                'bash',
                '-lc',
                raw_cmd,
                cwd=str(self._deploy_path(project)),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
        else:
            cmd = shlex.split(raw_cmd)
            process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(self._deploy_path(project)),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

        started = time.monotonic()
        communicate_task = asyncio.create_task(process.communicate())
        while True:
            done, _ = await asyncio.wait({communicate_task}, timeout=30)
            if done:
                stdout, stderr = communicate_task.result()
                break
            if project_name:
                elapsed = int(time.monotonic() - started)
                await self._send_deployment_update(
                    project_name,
                    f"⏳ Post-Deploy läuft weiter ({elapsed}s vergangen) …",
                )

        if process.returncode != 0:
            # Bash-Scripts (z.B. deploy.sh) schreiben Errors oft nach stdout
            # (via echo/print_fail) statt stderr. Plus: `gh api ... 2>&1` in
            # deploy.sh redirects stderr-zu-stdout — bei set -e ist stderr leer.
            # Wenn wir nur stderr loggen, geht die Diagnose verloren.
            # Vorfall 2026-05-14: "Post-deploy command failed: " ohne Reason.
            stdout_text = stdout.decode(errors='replace').strip() if stdout else ''
            stderr_text = stderr.decode(errors='replace').strip() if stderr else ''
            parts = [
                f"Post-deploy command failed (exit={process.returncode}):",
            ]
            if stdout_text:
                parts.append(f"stdout: {stdout_text}")
            if stderr_text:
                parts.append(f"stderr: {stderr_text}")
            if not stdout_text and not stderr_text:
                parts.append("(both streams empty — subprocess silent fail)")
            message = "\n".join(parts)
            if process.returncode == _POST_DEPLOY_TEMPFAIL_EXIT_CODE:
                raise PostDeployTempfailError(message)
            raise DeploymentError(message)

    async def _restart_service(self, project: Dict):
        """
        Restart systemd service

        Args:
            project: Project configuration
        """
        service_name = project['service_name']

        cmd = ['sudo', 'systemctl', 'restart', service_name]

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            raise DeploymentError(f"Service restart failed: {stderr.decode()}")

        # Wait a moment for service to start
        await asyncio.sleep(2)

    async def _health_check(self, project: Dict) -> bool:
        """
        Perform health check on deployed application with retries.
        Docker-Container (z.B. Next.js) brauchen 10-15s zum Starten.

        Args:
            project: Project configuration

        Returns:
            True if health check passed
        """
        import aiohttp

        url = project['health_check_url']
        max_retries = 5
        retry_delay = 5  # Sekunden zwischen Versuchen

        for attempt in range(1, max_retries + 1):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        url,
                        timeout=aiohttp.ClientTimeout(total=self.health_check_timeout)
                    ) as response:
                        if response.status == 200:
                            if attempt > 1:
                                self.logger.info(f"✅ Health check passed (Versuch {attempt}/{max_retries})")
                            return True
                        else:
                            self.logger.warning(f"⚠️ Health check: Status {response.status} (Versuch {attempt}/{max_retries})")

            except Exception as e:
                self.logger.warning(f"⚠️ Health check fehlgeschlagen (Versuch {attempt}/{max_retries}): {e}")

            if attempt < max_retries:
                await asyncio.sleep(retry_delay)

        self.logger.error(f"❌ Health check endgültig fehlgeschlagen nach {max_retries} Versuchen")
        return False

    async def _rollback(self, project: Dict, backup_path: Path):
        """
        Rollback to backup

        Args:
            project: Project configuration
            backup_path: Path to backup directory
        """
        if not backup_path.exists():
            raise DeploymentError(f"Backup not found: {backup_path}")

        if shutil.which("rsync"):
            # Restore from backup using rsync
            # --no-perms --no-group --no-owner: Avoid chgrp/chown errors when
            # files were originally owned by Docker container user
            #
            # WICHTIG: Gleiche Excludes wie beim Backup + .env/.venv!
            # Ohne --exclude=.git würde --delete das Git-Repo löschen,
            # weil .git NICHT im Backup enthalten ist (Vorfall 2026-03-20).
            #
            # 2026-05-14 (ZERODOX #236): --exclude=logs, --exclude=uploads
            # ergänzt. Vorher: ZERODOX-Backup hat logs/uploads exclude-d
            # (Backup-rsync line 372-373), Rollback aber NICHT → --delete
            # versuchte logs/contact-requests/*.jsonl + uploads/invoices/*.pdf
            # zu unlink-en. Diese Dateien gehören Docker-Container-User (UID
            # 1001), Bot läuft als cmdshadow (UID 1000) → Permission denied.
            # 2-Tage-Auto-Deploy-Blockade nach ZERODOX-PRs #705, #707, #708.
            cmd = [
                'rsync', '-rlptD', '--delete',
                '--exclude=.git',
                '--exclude=.env',
                '--exclude=.venv',
                '--exclude=node_modules',
                '--exclude=__pycache__',
                '--exclude=backups',
                '--exclude=logs',
                '--exclude=uploads',
                '--no-perms', '--no-group', '--no-owner',
                str(backup_path) + '/',
                str(project['path']) + '/'
            ]

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                raise DeploymentError(f"Rollback failed: {stderr.decode()}")
            return

        self.logger.warning("⚠️ rsync not found, using Python rollback (slower)")
        await self._send_deployment_update(
            project['name'],
            "⚠️ rsync fehlt, nutze Python-Rollback (langsamer).",
        )
        await asyncio.to_thread(self._purge_project_path, project['path'])
        await asyncio.to_thread(
            shutil.copytree,
            backup_path,
            project['path'],
            dirs_exist_ok=True,
        )

    def _purge_project_path(self, path: Path) -> None:
        """Remove project files while keeping the git directory."""
        for entry in path.iterdir():
            if entry.name == ".git":
                continue
            if entry.is_dir():
                shutil.rmtree(entry)
            else:
                try:
                    entry.unlink()
                except FileNotFoundError:
                    pass


    def _kanal_fuer(self, project_name: str):
        """Liefert den Meldekanal für ein Projekt.

        Ist am Projekt `deploy_channel_id` gesetzt, geht die Meldung dorthin --
        sonst in den gemeinsamen Deploy-Kanal.

        WARUM: Wer nur an einem Projekt arbeitet, soll dessen Deploys
        nachvollziehen können, ohne die aller anderen mitzulesen. Das ist vor
        allem für Mitarbeitende ohne Serverzugang der einzige Weg zu sehen, was
        mit ihrer Änderung passiert ist.
        """
        # ⚠️ Bewusst aus der LEBENDEN Konfiguration, nicht aus self.projects:
        # Letzteres ist eine Kopie aus _load_projects(), die beim Start des
        # Managers entsteht. Die Kanal-IDs setzt der Bot aber erst danach, wenn
        # er die Kanäle anlegt — in der Kopie fehlen sie für immer.
        projekte = getattr(self.config, "projects", None) or {}
        projekt = projekte.get(project_name) or {}
        if not projekt:
            for schluessel, wert in projekte.items():
                if schluessel.lower().replace("-", "_") == str(project_name).lower().replace("-", "_"):
                    projekt = wert
                    break
        eigener = projekt.get("deploy_channel_id")
        if eigener:
            kanal = self.bot.get_channel(eigener)
            if kanal:
                return kanal
            # Fällt der eigene Kanal aus, lieber im gemeinsamen melden als
            # gar nicht -- eine verschluckte Deploy-Meldung ist schlimmer als
            # eine am falschen Ort.
            self.logger.warning(
                f"Deploy-Kanal {eigener} für '{project_name}' nicht erreichbar, "
                "melde im gemeinsamen Kanal."
            )
        return self.bot.get_channel(self.deployment_channel_id)

    async def _send_deployment_started(
        self,
        project_name: str,
        branch: str,
        deploy_context: Optional[Dict] = None,
    ):
        """Erstellt eine editierbare Discord-Statusmeldung für den Deploy."""
        if not hasattr(self, '_deploy_steps'):
            self._deploy_steps: Dict[str, list] = {}
        if not hasattr(self, '_deploy_messages'):
            self._deploy_messages: Dict[str, object] = {}
        if not hasattr(self, '_deploy_contexts'):
            self._deploy_contexts: Dict[str, Dict] = {}
        self._deploy_steps[project_name] = []
        self._deploy_contexts[project_name] = deploy_context or {}
        self.logger.info(f"🚀 Deployment gestartet: {project_name} ({branch})")

        channel = self._kanal_fuer(project_name)
        if not channel:
            return
        embed = self._build_progress_embed(project_name, branch, "Deployment wird vorbereitet …")
        try:
            self._deploy_messages[project_name] = await channel.send(embed=embed)
        except Exception as exc:
            self.logger.error(
                f"❌ Deploy-Startmeldung konnte nicht gesendet werden: {exc}",
                exc_info=True,
            )

    def _build_progress_embed(
        self, project_name: str, branch: str, current: str
    ) -> discord.Embed:
        """Baut den aktuellen Zwischenstand für eine laufende Auslieferung."""
        steps = getattr(self, '_deploy_steps', {}).get(project_name, [])
        embed = discord.Embed(
            title=f"🟡 Deployment läuft: {project_name}",
            description="Die Auslieferung läuft. Diese Meldung wird automatisch aktualisiert.",
            color=discord.Color.gold(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="Projekt", value=f"`{project_name}`", inline=True)
        embed.add_field(name="Branch", value=f"`{branch}`", inline=True)
        embed.add_field(name="Aktueller Schritt", value=current[:1024], inline=False)
        trigger = _format_deploy_trigger(
            getattr(self, '_deploy_contexts', {}).get(project_name)
        )
        if trigger:
            embed.add_field(name="Auslöser", value=trigger[:1024], inline=False)
        if steps:
            embed.add_field(
                name="Fortschritt", value="\n".join(steps[-8:])[:1024], inline=False
            )
        return embed

    async def _send_deployment_update(self, project_name: str, message: str):
        """Sammelt einen Schritt und aktualisiert die laufende Discord-Meldung."""
        if not hasattr(self, '_deploy_steps'):
            self._deploy_steps: Dict[str, list] = {}
        if project_name not in self._deploy_steps:
            self._deploy_steps[project_name] = []
        timestamp = datetime.now(timezone.utc).strftime('%H:%M:%S')
        self._deploy_steps[project_name].append(f"`{timestamp}` {message}")
        self.logger.info(f"[Deploy] {project_name}: {message}")
        progress_message = getattr(self, '_deploy_messages', {}).get(project_name)
        if progress_message:
            context = getattr(self, '_deploy_contexts', {}).get(project_name, {})
            branch = str(
                context.get('branch')
                or self.projects.get(project_name, {}).get('branch')
                or 'main'
            )
            try:
                await progress_message.edit(
                    embed=self._build_progress_embed(project_name, branch, message)
                )
            except Exception as exc:
                self.logger.warning(
                    f"⚠️ Deploy-Fortschritt konnte nicht aktualisiert werden: {exc}"
                )

    async def _publish_final_embed(
        self, project_name: str, channel, embed: discord.Embed
    ) -> None:
        """Ersetzt die Fortschrittsmeldung durch das Ergebnis oder sendet neu."""
        progress_message = getattr(self, '_deploy_messages', {}).pop(project_name, None)
        try:
            if progress_message:
                await progress_message.edit(embed=embed)
            else:
                await channel.send(embed=embed)
        except Exception as exc:
            self.logger.error(
                f"❌ Failed to send Discord notification: {exc}", exc_info=True
            )
        getattr(self, '_deploy_contexts', {}).pop(project_name, None)

    async def _send_deployment_success(
        self, project_name: str, branch: str, duration: float, result: Dict
    ):
        """Send Discord notification when deployment succeeds"""
        channel = self._kanal_fuer(project_name)
        if not channel:
            return

        steps = getattr(self, '_deploy_steps', {}).get(project_name, [])
        ok, total, _ = _summarize_steps(steps)

        # Klartext-Zusammenfassung statt bloßer Erfolgsmeldung
        if total > 0:
            summary = f"**{project_name}** erfolgreich deployt — alle {total} Schritte ok."
        else:
            summary = f"**{project_name}** erfolgreich deployt."

        embed = discord.Embed(
            title=f"✅ Deployment erfolgreich: {project_name}",
            description=summary,
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc)
        )

        embed.add_field(name="Projekt", value=f"`{project_name}`", inline=True)
        embed.add_field(name="Branch", value=f"`{branch}`", inline=True)
        embed.add_field(name="Dauer", value=_format_deploy_duration(duration), inline=True)

        trigger = _format_deploy_trigger(result.get('deploy_context'))
        if trigger:
            embed.add_field(name="Auslöser", value=trigger[:1024], inline=False)

        # Gesammelte Deploy-Steps als Timeline (Detail, unter der Zusammenfassung)
        if steps:
            embed.add_field(name="Verlauf", value="\n".join(steps[-10:])[:1024], inline=False)
            self._deploy_steps.pop(project_name, None)  # Cleanup

        await self._publish_final_embed(project_name, channel, embed)

        # External-Guilds benachrichtigen (Kunden-Discord)
        await self._forward_deploy_to_external(project_name, embed)

    async def _send_deployment_failure(
        self, project_name: str, branch: str, duration: float, result: Dict
    ):
        """Send Discord notification when deployment fails"""
        channel = self._kanal_fuer(project_name)
        if not channel:
            return

        error = result.get('error', 'Unknown error')
        rolled_back = result.get('rolled_back', False)

        steps = getattr(self, '_deploy_steps', {}).get(project_name, [])
        ok, total, failed_step = _summarize_steps(steps)

        failed_stage = result.get('failed_stage')
        # Klartext-Zusammenfassung: wie weit kam das Deployment?
        if total > 0:
            summary = (
                f"**{project_name}** wurde nach {ok} erfolgreichen "
                "Statusmeldungen abgebrochen."
            )
        else:
            summary = f"**{project_name}** Deployment fehlgeschlagen."
        if failed_stage:
            summary += f" Fehler in Phase: **{failed_stage}**."

        embed = discord.Embed(
            title=f"❌ Deployment fehlgeschlagen: {project_name}",
            description=summary,
            color=discord.Color.red(),
            timestamp=datetime.now(timezone.utc)
        )

        embed.add_field(name="Projekt", value=f"`{project_name}`", inline=True)
        embed.add_field(name="Branch", value=f"`{branch}`", inline=True)
        embed.add_field(name="Dauer", value=_format_deploy_duration(duration), inline=True)

        trigger = _format_deploy_trigger(result.get('deploy_context'))
        if trigger:
            embed.add_field(name="Auslöser", value=trigger[:1024], inline=False)

        # Fehlgeschlagener Schritt klar hervorgehoben (vor der Roh-Fehlermeldung)
        if failed_step is not None:
            embed.add_field(name="⛔ Fehlgeschlagen bei", value=failed_step[:1024], inline=False)

        embed.add_field(
            name="Fehlerursache", value=_concise_deploy_error(error), inline=False
        )
        if len(error) > 1000:
            error = "..." + error[-997:]
        error = error.replace("```", "'''")
        embed.add_field(name="Technische Details", value=f"```{error}```", inline=False)

        rollback_msg = "✅ Rollback erfolgreich" if rolled_back else "❌ Kein Rollback"
        embed.add_field(name="Rollback", value=rollback_msg, inline=True)

        # Gesammelte Deploy-Steps als vollständiger Verlauf (Detail)
        if steps:
            embed.add_field(name="Verlauf", value="\n".join(steps[-10:])[:1024], inline=False)
            self._deploy_steps.pop(project_name, None)

        await self._publish_final_embed(project_name, channel, embed)

        # External-Guilds benachrichtigen (Kunden-Discord)
        await self._forward_deploy_to_external(project_name, embed)

    async def _send_deployment_exception(
        self, project_name: str, error: str, duration: float
    ):
        """Send Discord notification when deployment crashes with exception"""
        channel = self._kanal_fuer(project_name)
        if not channel:
            return

        embed = discord.Embed(
            title="💥 Deployment Exception",
            description=f"**{project_name}** deployment crashed with unexpected error",
            color=discord.Color.dark_red(),
            timestamp=datetime.now(timezone.utc)
        )

        embed.add_field(name="Project", value=project_name, inline=True)
        embed.add_field(name="Duration", value=f"{duration:.1f}s", inline=True)

        # Truncate error if too long
        if len(error) > 500:
            error = error[:497] + "..."
        embed.add_field(name="Exception", value=f"```{error}```", inline=False)

        embed.add_field(
            name="⚠️ Action Required",
            value="Manual intervention may be required. Check logs for details.",
            inline=False
        )

        try:
            await channel.send(embed=embed)
            self.logger.debug(f"📢 Sent deployment exception notification for {project_name}")
        except Exception as e:
            self.logger.error(f"❌ Failed to send Discord notification: {e}", exc_info=True)


    async def _forward_deploy_to_external(self, project_name: str, embed: discord.Embed):
        """Deployment-Embed an externe Guilds weiterleiten (Kunden-Discord)."""
        try:
            # GitHub-Repo-Namen nutzen Bindestriche ("mayday-sim"), Config-Keys oft
            # Underscores ("mayday_sim"). Gleicher dash/underscore-Fallback wie in
            # deploy_project/_trigger_deployment — sonst bleibt external_notifications
            # leer und der Kunden-Deploy-Post wird nie gesendet (Issue #504, gleicher
            # Bug-Typ wie Vorfall 2026-05-25 PR #449/#450).
            projects = self.bot.config.projects
            project_config = None
            normalized_name = project_name.lower().replace("-", "_")
            for key in projects.keys():
                key_lower = key.lower()
                if key_lower == project_name.lower() or key_lower.replace("-", "_") == normalized_name:
                    project_config = projects[key]
                    break
            if not project_config:
                return
            notifications = project_config.get('external_notifications', [])

            for notif in notifications:
                if not notif.get('enabled'):
                    continue
                if not notif.get('notify_on', {}).get('deployments'):
                    continue

                channel_id = notif.get('deploy_channel_id')
                if not channel_id:
                    continue

                channel = self.bot.get_channel(int(channel_id))
                if not channel:
                    continue

                # Embed kopieren (ohne interne Details wie Verlauf/Steps)
                ext_embed = discord.Embed(
                    title=embed.title,
                    description=embed.description,
                    color=embed.color,
                    timestamp=embed.timestamp,
                )
                # Nur Projekt, Branch, Dauer übernehmen (keine Fehlerdetails/Steps)
                for field in embed.fields:
                    if field.name in ("Projekt", "Branch", "Dauer", "Rollback"):
                        ext_embed.add_field(
                            name=field.name, value=field.value, inline=field.inline
                        )

                await channel.send(embed=ext_embed)
                self.logger.info(
                    f"📢 Deployment-Status für {project_name} an externen Channel gesendet"
                )
        except Exception as e:
            self.logger.debug(f"External deploy notification: {e}")


class DeploymentError(Exception):
    """Exception raised for deployment failures"""
    pass


class PostDeployTempfailError(DeploymentError):
    """
    deploy.sh beendet sich mit EX_TEMPFAIL (75), wenn der Lauf aus einem
    Grund abgebrochen wurde, der KEIN Fehlschlag ist — bisher "eine andere
    Deploy-Sperre ist belegt", seit ZERODOX#3328 zusaetzlich "main ist
    waehrend des Gates weitergerueckt, der naechste Merge liefert gesammelt
    aus" (Sammel-Zug).

    ZERODOX#3577: Bis hierhin behandelte `deploy_project()` JEDEN
    nicht-Null-Exitcode von `post_deploy_command` gleich — Rollback-Versuch
    aus dem Backup UND Discord-Fehlalarm, noch bevor `ci_mixin._trigger_
    deployment()` ueberhaupt sieht, dass der Grund "exit=75" war. Fuer einen
    Zustand, der laut eigenem Marker "voruebergehend" heisst, ist ein
    Rollback der Arbeitskopie und ein Fehler-Alarm falsch: Es wurde nichts
    Kaputtes deployt, der naechste Merge holt den Stand ohnehin nach.

    Eine eigene Exception-Klasse (statt eine String-Pruefung im except-Block
    von `deploy_project`) haelt den Unterschied dort sichtbar, wo er
    entsteht — in `_run_post_deploy_command`, wo der Exitcode zuerst bekannt
    ist.
    """
    pass


# ZERODOX#3577: deploy.sh's eigener Exitcode fuer "voruebergehend verhindert,
# kein Fehlschlag" — siehe PostDeployTempfailError. Muss synchron bleiben mit
# ci_mixin._DEPLOY_TEMPFAIL_MARKER ("exit=75" im Fehlertext).
_POST_DEPLOY_TEMPFAIL_EXIT_CODE = 75
