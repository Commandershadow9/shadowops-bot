"""Start-Abgleich: verlorene Merge-Aufträge nach einem Bot-Neustart nachholen.

ZERODOX#3447, Fall 3: Trifft ein Merge-Webhook ein, während der Bot neu
startet (Startphase ~130 s, der Webhook-Server lauscht erst in Schritt 5/6),
geht der Auftrag verloren. GitHub stellt ihn nicht erneut zu, und keiner der
bestehenden Nachhol-Wege greift: Der Re-Poll (ZERODOX#1720) läuft nur nach
einem Deploy, der CI-Reconcile (ZERODOX#2267) nur nach einem grünen
``workflow_run`` — beide Ereignisse können ebenfalls in die Startphase fallen.
Live bleibt dann der alte Stand, bis zufällig der nächste Merge kommt.

Der Abgleich läuft deshalb EINMAL nach dem Start, verzögert, und vergleicht
den Deploy-Baum mit dem Remote-HEAD von ``main``.
"""
import asyncio
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

# Verzögerung nach Bereitschaft des Webhook-Servers. Webhooks, die GitHub in
# der Startphase noch zustellt (oder nach einem Timeout erneut versucht),
# sollen zuerst greifen — der Abgleich ist nur das Netz darunter.
_DEFAULT_START_ABGLEICH_DELAY_SEC = 105

# Ein Bot, der sich beim Start selbst neu deployt, startet sich neu, gleicht
# wieder ab und deployt wieder — eine Neustart-Schleife. Nur loggen.
_SELF_DEPLOY_REPOS = {'shadowops_bot'}

# ZERODOX#2891: Verzögerung des Nachhol-Abgleichs nach einem CI-Timeout.
# Der Timeout heißt "CI war nach max_wait_min noch nicht durch", nicht "rot" —
# 15 min später ist der Lauf meist fertig und der Stand deploybar.
_DEFAULT_NACHHOL_ABGLEICH_DELAY_SEC = 900


class StartAbgleichMixin:

    def schedule_start_abgleich(self) -> bool:
        """Startet den Abgleich genau einmal als Hintergrund-Task (fail-soft)."""
        try:
            existing = getattr(self, '_start_abgleich_task', None)
            if existing is not None:
                return False
            if not getattr(self, 'auto_deploy_enabled', False):
                self.logger.info("ℹ️ Start-Abgleich übersprungen: auto_deploy ist aus.")
                return False
            delay_sec = max(
                0,
                int(getattr(self, 'start_abgleich_delay_sec', _DEFAULT_START_ABGLEICH_DELAY_SEC)),
            )
            self._start_abgleich_task = asyncio.create_task(self._start_abgleich(delay_sec))
            return True
        except Exception as e:
            self.logger.warning(f"⚠️ Start-Abgleich konnte nicht geplant werden: {e}")
            return False

    def nachhol_abgleich_delay_sec_effektiv(self) -> int:
        try:
            return max(0, int(getattr(
                self, 'nachhol_abgleich_delay_sec', _DEFAULT_NACHHOL_ABGLEICH_DELAY_SEC
            )))
        except (TypeError, ValueError):
            return _DEFAULT_NACHHOL_ABGLEICH_DELAY_SEC

    def plane_nachhol_abgleich(
        self,
        repo_key: str,
        delay_sec: Optional[int] = None,
        anlass: str = "CI-Timeout",
    ) -> bool:
        """Plant EINEN verzögerten Abgleich für genau dieses Projekt (fail-soft).

        ZERODOX#2891: Endete das CI-Warten mit "timeout", gab der Deploy die
        Reservierung frei und meldete Discord-Alarm — danach holte nichts den
        Stand nach, live blieb der alte Stand bis zum nächsten Merge. Gemessen:
        36 Timeouts in 30 Tagen, davon 27 mit einem später GRÜNEN Lauf. Der
        Nachhol-Abgleich nutzt denselben Kern wie der Start-Abgleich
        (Deploy-Baum-HEAD gegen Remote-HEAD, Per-SHA-Sperre, Einstieg über
        `_trigger_deployment` mit erneutem CI-Warten).

        Begrenzung: je Projekt höchstens EIN ausstehender Task. Ein zweiter
        Timeout — auch der des Nachhol-Deploys selbst — plant keinen weiteren;
        so entsteht bei dauerhaft hängender CI keine Endlos-Kette.
        Rückgabe: True, wenn ein Task eingeplant wurde.
        """
        try:
            if not getattr(self, 'auto_deploy_enabled', False):
                return False
            normalisiert = str(repo_key or '').lower().replace('-', '_')
            if not normalisiert or normalisiert in _SELF_DEPLOY_REPOS:
                return False
            tasks = getattr(self, '_nachhol_abgleich_tasks', None)
            if not isinstance(tasks, dict):
                tasks = {}
                self._nachhol_abgleich_tasks = tasks
            bestehend = tasks.get(normalisiert)
            if bestehend is not None and not bestehend.done():
                self.logger.info(
                    f"ℹ️ Nachhol-Abgleich {repo_key}: bereits eingeplant — kein zweiter Task."
                )
                return False
            kandidat = None
            for repo_name, repo_full_name, project_config in self._start_abgleich_kandidaten():
                if repo_name.lower().replace('-', '_') == normalisiert:
                    kandidat = (repo_name, repo_full_name, project_config)
                    break
            if kandidat is None:
                self.logger.info(
                    f"ℹ️ Nachhol-Abgleich {repo_key}: kein Projekt mit deploy_path — nicht eingeplant."
                )
                return False
            if delay_sec is None:
                delay_sec = self.nachhol_abgleich_delay_sec_effektiv()
            delay_sec = max(0, int(delay_sec))
            tasks[normalisiert] = asyncio.create_task(
                self._nachhol_abgleich(normalisiert, *kandidat, delay_sec, anlass)
            )
            self.logger.info(
                f"⏳ Nachhol-Abgleich {repo_key} in {delay_sec} s eingeplant ({anlass})."
            )
            return True
        except Exception as e:
            self.logger.warning(f"⚠️ Nachhol-Abgleich {repo_key} konnte nicht geplant werden: {e}")
            return False

    async def _nachhol_abgleich(
        self,
        task_key: str,
        repo_name: str,
        repo_full_name: str,
        project_config: Dict,
        delay_sec: int,
        anlass: str,
    ) -> None:
        try:
            if delay_sec:
                await asyncio.sleep(delay_sec)
            await self._start_abgleich_projekt(
                repo_name, repo_full_name, project_config,
                anlass=f"Nachhol-Abgleich ({anlass})",
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.logger.warning(f"⚠️ Nachhol-Abgleich {repo_name} fehlgeschlagen: {e}")
        finally:
            # Erst NACH dem Trigger freigeben: Läuft der Nachhol-Deploy selbst
            # in einen Timeout, sieht plane_nachhol_abgleich diesen Task noch
            # als ausstehend und plant keinen weiteren (keine Kette).
            tasks = getattr(self, '_nachhol_abgleich_tasks', None)
            if isinstance(tasks, dict) and tasks.get(task_key) is asyncio.current_task():
                tasks.pop(task_key, None)

    def _start_abgleich_kandidaten(self) -> List[Tuple[str, str, Dict]]:
        """(repo_name, repo_full_name, project_config) je Projekt mit eigenem Deploy-Baum.

        Nur Projekte mit ``deploy_path``: Deren Baum fasst ausschließlich der
        Deploy an, sein HEAD ist also der ausgelieferte Stand. Der Arbeitsbaum
        ``path`` taugt dafür nicht (ZERODOX#2344) — dort steht, was gerade
        ausgecheckt ist; ein Feature-Branch darin ergäbe bei jedem Start einen
        falschen Deploy-Auftrag.

        Nicht abgedeckt: Nach einem Rollback (rsync ohne ``.git``) bleibt der
        HEAD des Deploy-Baums auf dem neuen Stand, obwohl der alte läuft —
        diesen Fall erkennt der Abgleich bewusst nicht.
        """
        kandidaten = []
        projects = getattr(self.config, 'projects', {}) or {}
        for key, project_config in projects.items():
            if not isinstance(project_config, dict):
                continue
            if project_config.get('enabled') is False:
                continue
            if not project_config.get('deploy_path'):
                continue
            # SSH-Form (git@github.com:owner/repo.git) zerlegt `urlparse` nicht
            # in einen Pfad — vorher auf https normalisieren.
            repo_url = self._normalize_repo_url(
                str(project_config.get('repo_url') or '')
            ) or ''
            teile = [t for t in urlparse(repo_url).path.strip('/').split('/') if t]
            if len(teile) < 2:
                continue
            repo_name = teile[1].removesuffix('.git')
            kandidaten.append((repo_name, f"{teile[0]}/{repo_name}", project_config))
        return kandidaten

    async def _start_abgleich(self, delay_sec: int) -> None:
        try:
            if delay_sec:
                await asyncio.sleep(delay_sec)
            for repo_name, repo_full_name, project_config in self._start_abgleich_kandidaten():
                try:
                    await self._start_abgleich_projekt(repo_name, repo_full_name, project_config)
                except Exception as e:
                    self.logger.warning(f"⚠️ Start-Abgleich {repo_name} fehlgeschlagen: {e}")
        except Exception as e:
            self.logger.warning(f"⚠️ Start-Abgleich abgebrochen: {e}")

    async def _start_abgleich_projekt(
        self,
        repo_name: str,
        repo_full_name: str,
        project_config: Dict,
        anlass: str = "Start-Abgleich",
    ) -> Optional[str]:
        """Gleicht ein Projekt ab. Rückgabe nur für Tests/Logs.

        Gemeinsamer Kern von Start- und Nachhol-Abgleich; `anlass` steht nur
        im Log.
        """
        branch = 'main'
        if branch not in (getattr(self, 'deploy_branches', None) or []):
            return None

        remote_sha = await self._fetch_branch_head_sha(repo_full_name, branch)
        if not remote_sha:
            self.logger.warning(
                f"⚠️ {anlass} {repo_name}: Remote-HEAD von {branch} nicht lesbar — übersprungen."
            )
            return None

        deploy_path = Path(project_config['deploy_path'])
        if not deploy_path.exists():
            self.logger.warning(f"⚠️ {anlass} {repo_name}: {deploy_path} fehlt — übersprungen.")
            return None
        # Bewusst kein `git fetch`: Verglichen wird der ausgelieferte Stand
        # (HEAD) mit GitHub, nicht mit einem lokalen Remote-Ref.
        deployed_sha = self._get_commit_sha(deploy_path, 'HEAD')
        if not deployed_sha:
            self.logger.warning(f"⚠️ {anlass} {repo_name}: HEAD in {deploy_path} nicht lesbar.")
            return None

        # Kein Fehlalarm nach Docs-only-Merges: Auch dann deployt der Bot
        # (deploy.sh erkennt Docs-only selbst), und deployment_manager zieht den
        # Deploy-Baum per `reset --hard origin/main` nach (deployment_manager.py,
        # Deploy-Baum-Synchronisation). Nach JEDEM erfolgreich angestossenen
        # Deploy steht dessen HEAD also auf `main` — eine Abweichung heisst
        # "Auftrag nicht ausgeführt", nicht "Doku-Commit ohne Neubau".
        # (Der buildSha des Containers weicht nach Docs-only ab, #1262 — deshalb
        # misst dieser Abgleich den Baum und nicht den Health-Endpoint.)
        if deployed_sha == remote_sha:
            self.logger.info(
                f"✅ {anlass} {repo_name}: Deploy-Baum steht auf {branch} ({remote_sha[:7]})."
            )
            return "aktuell"

        if self._normalize_repo_name(repo_name).replace('-', '_') in _SELF_DEPLOY_REPOS:
            self.logger.warning(
                f"⚠️ {anlass} {repo_name}: {deployed_sha[:7]} != {branch} {remote_sha[:7]}, "
                "Self-Deploy wird bewusst nicht angestossen (Neustart-Schleife)."
            )
            return "self"

        if self._deployment_is_active(repo_name):
            # Ein laufender Deploy zieht vor dem Bau origin/main nach, der
            # Re-Poll danach holt einen noch neueren Stand.
            self.logger.info(
                f"ℹ️ {anlass} {repo_name}: Deploy läuft bereits — kein Nachholen."
            )
            return "aktiv"

        # Dieselbe Per-SHA-Sperre wie Push-/PR-Webhook, Re-Poll und Reconcile:
        # Hat ein Webhook diese SHA schon angenommen (auch wenn er noch im
        # CI-Wartefenster steht und deshalb nicht als aktiv gilt), deployt
        # hier niemand zum zweiten Mal. Umgekehrt überspringt ein später
        # eintreffender Webhook die hier reservierte SHA.
        if not self._reserve_deploy(repo_name, remote_sha):
            self.logger.info(
                f"ℹ️ {anlass} {repo_name}@{remote_sha[:7]}: bereits durch einen anderen "
                "Trigger reserviert — kein doppelter Deploy."
            )
            return "reserviert"

        self.logger.warning(
            f"🔁 {anlass} {repo_name}: Deploy-Baum {deployed_sha[:7]} != {branch} "
            f"{remote_sha[:7]} — Auftrag nicht ausgeführt, Nachhol-Deploy."
        )
        # Einstieg ist _trigger_deployment — derselbe, den der Push-Webhook und
        # der Re-Poll benutzen. Damit gelten CI-Wartefenster, Sammel-Zug
        # (superseded), Docs-only-Erkennung und Sperre wie gewohnt; bei einem
        # Fehlschlag gibt er die Reservierung selbst wieder frei.
        # _reconcile_ci_success_deployment passt hier nicht: Er misst den
        # buildSha des Health-Endpoints, der nach Docs-only-Merges dauerhaft
        # abweicht (#1262), und wartet vorab 120 s auf einen normalen Deploy,
        # den es nach einem verlorenen Webhook gerade nicht gibt.
        await self._trigger_deployment(
            repo_name=repo_name,
            branch=branch,
            commit_sha=remote_sha[:7],
            repo_full_name=repo_full_name,
            full_sha=remote_sha,
        )
        return "angestossen"
