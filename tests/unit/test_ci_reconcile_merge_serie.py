"""Der CI-Reconcile darf bei einer Merge-Serie nicht die Zustaendigkeit verlieren.

Vorfall 17.08.2026: Zwischen 15:02 und 15:37 wurden drei Commits nach `main`
gemerged. Der Reconcile fuer `8b96999` stellte fest, dass `main` inzwischen auf
`1b9d27e` stand, und gab auf:

    ℹ️ CI-Reconcile ZERODOX@8b96999 veraltet: main steht bereits auf 1b9d27e.
      Der neuere CI-Lauf ist zustaendig.

Diese Annahme traegt nur, solange der neuere Lauf auch tatsaechlich deployt.
Scheitert dessen Deploy — an einer Sperre, an einer roten CI, an einer
GitHub-Stoerung —, ist anschliessend *niemand* mehr zustaendig. Genau das ist
passiert: Der Live-Stand blieb bei `47b6aac9` stehen, drei Commits hinter
`main`, darunter eine kundensichtbare Portal-Aenderung.

Die Schleife arbeitet ohnehin durchgehend mit dem *aktuellen* Branch-HEAD und
nicht mit dem Commit, fuer den sie gestartet wurde — sie deployt `branch_sha`
und prueft `live_sha == branch_sha`. Der fruehe Ausstieg war also die einzige
Stelle, die sie am Nachziehen hinderte. Ein Deploy ohne gruene CI kann daraus
nicht entstehen: `_trigger_deployment` wartet fuer den neuen Commit auf dessen
eigene CI, und `_reserve_deploy` verhindert, dass zwei Reconciles denselben
Stand doppelt ausliefern.
"""
import logging
from unittest.mock import AsyncMock, patch

import pytest

from src.integrations.github_integration.ci_mixin import CIMixin


class _ReconcileHarness(CIMixin):
    """Harness fuer _reconcile_ci_success_deployment.

    `branch_shas` und `live_shas` sind Folgen; der letzte Eintrag wiederholt
    sich. So laesst sich "main zieht weiter" und "live zieht nach" abbilden.
    """

    def __init__(self, branch_shas: list, live_shas: list, trigger_ergebnis=None):
        self.logger = logging.getLogger("test-ci-reconcile")
        self._branch_shas = list(branch_shas)
        self._live_shas = list(live_shas)
        self.trigger_calls: list = []
        self._reserviert: set = set()
        # Was _trigger_deployment meldet. None = altes Verhalten (kein Wert).
        self._trigger_ergebnis = trigger_ergebnis
        # Nach so vielen Versuchen tut der Harness so, als sei live nachgezogen —
        # damit die Schleife endet, ohne dass der Test an der Uhr haengt.
        self.trigger_grenze = 6

    @staticmethod
    def _naechster(folge: list):
        return folge.pop(0) if len(folge) > 1 else (folge[0] if folge else None)

    async def _fetch_branch_head_sha(self, repo_full_name: str, branch: str):
        return self._naechster(self._branch_shas)

    async def _fetch_live_build_sha(self, health_url: str):
        if len(self.trigger_calls) >= self.trigger_grenze:
            # Notbremse fuer den Test: live gilt als nachgezogen, die Schleife
            # beendet sich regulaer ueber den Gleichstand-Zweig.
            return self._branch_shas[-1]
        return self._naechster(self._live_shas)

    def _deployment_is_active(self, repo_name: str) -> bool:
        return False

    def _reserve_deploy(self, repo_name: str, sha: str) -> bool:
        schluessel = (repo_name, sha)
        if schluessel in self._reserviert:
            return False
        self._reserviert.add(schluessel)
        return True

    def _release_deploy(self, repo_name: str, sha: str):
        self._reserviert.discard((repo_name, sha))

    async def _trigger_deployment(self, **kwargs):
        self.trigger_calls.append(kwargs)
        return self._trigger_ergebnis


def _config(**deploy_overrides) -> dict:
    basis = {
        'ci_success_reconcile_delay_sec': 0,
        'ci_success_reconcile_poll_sec': 1,
        'ci_success_reconcile_timeout_sec': 5,
        'ci_success_reconcile_max_attempts': 2,
    }
    basis.update(deploy_overrides)
    return {'deploy': basis, 'monitor': {'url': 'https://zerodox.de/api/health'}}


async def _reconcile(h: _ReconcileHarness, successful_sha: str, **overrides):
    with patch("asyncio.sleep", new=AsyncMock(return_value=None)):
        await h._reconcile_ci_success_deployment(
            repo_name="ZERODOX",
            branch="main",
            successful_sha=successful_sha,
            repo_full_name="Commandershadow9/ZERODOX",
            project_config=_config(**overrides),
        )


ALT = "8b969995aa9d6be6d540e2ef0c20d3ffca07323f"
NEU = "1b9d27ecca050cf9a059273e34ea89068edfc644"
LIVE = "47b6aac952baec3fd7c97a5834643c7b15013211"


@pytest.mark.asyncio
async def test_ueberholter_head_wird_nachgezogen_statt_aufgegeben():
    """main ist weitergezogen, live hinkt hinterher — es MUSS deployt werden."""
    h = _ReconcileHarness(branch_shas=[NEU], live_shas=[LIVE])

    await _reconcile(h, successful_sha=ALT)

    assert h.trigger_calls, (
        "Der Reconcile hat aufgegeben, obwohl live hinter main liegt. Wenn der "
        "'neuere CI-Lauf' seinerseits scheitert, zieht dann niemand mehr nach."
    )
    assert h.trigger_calls[0]["full_sha"] == NEU, (
        "Nachgezogen werden muss der AKTUELLE Branch-HEAD, nicht der Commit, "
        "fuer den der Reconcile gestartet wurde."
    )


@pytest.mark.asyncio
async def test_gleichstand_loest_keinen_deploy_aus():
    """Gegenprobe: Steht live bereits auf dem Branch-HEAD, passiert nichts."""
    h = _ReconcileHarness(branch_shas=[NEU], live_shas=[NEU])

    await _reconcile(h, successful_sha=NEU)

    assert h.trigger_calls == []


@pytest.mark.asyncio
async def test_unveraenderter_head_verhaelt_sich_wie_bisher():
    """Der bisherige Hauptfall bleibt unberuehrt: HEAD == successful_sha."""
    h = _ReconcileHarness(branch_shas=[ALT], live_shas=[LIVE])

    await _reconcile(h, successful_sha=ALT)

    assert len(h.trigger_calls) >= 1
    assert h.trigger_calls[0]["full_sha"] == ALT


@pytest.mark.asyncio
async def test_versuche_bleiben_begrenzt():
    """Der Umbau darf die Obergrenze nicht aushebeln — sonst laeuft der
    Reconcile bei dauerhaft scheiterndem Deploy bis zur Deadline durch und
    stoesst dabei beliebig viele Deploys an."""
    h = _ReconcileHarness(branch_shas=[NEU], live_shas=[LIVE])

    await _reconcile(h, successful_sha=ALT, ci_success_reconcile_max_attempts=2)

    assert len(h.trigger_calls) <= 2, (
        f"Erwartet hoechstens 2 Versuche, waren {len(h.trigger_calls)}."
    )


# ---------------------------------------------------------------------------
# Eine belegte Deploy-Sperre ist ein Wartegrund, kein Fehlschlag.
#
# Am 17.08.2026 scheiterten drei Auto-Deploys am flock, weil parallel ein
# manueller Lauf mit --migrate lief. Die Sperre arbeitete korrekt; verbraucht
# wurden trotzdem die Nachhol-Versuche, und danach blieb der Stand liegen.
# deploy.sh meldet diesen Fall seit demselben Tag mit EX_TEMPFAIL (75).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transienter_fehlschlag_verbraucht_keinen_versuch():
    """Meldet der Deploy "vorübergehend", zaehlt das nicht gegen max_attempts."""
    h = _ReconcileHarness(
        branch_shas=[NEU], live_shas=[LIVE], trigger_ergebnis="transient"
    )

    await _reconcile(h, successful_sha=NEU, ci_success_reconcile_max_attempts=2)

    assert len(h.trigger_calls) > 2, (
        "Ein vorübergehender Hinderungsgrund (belegte Sperre) darf die "
        f"Nachhol-Versuche nicht aufbrauchen — es waren {len(h.trigger_calls)}."
    )


@pytest.mark.asyncio
async def test_echter_fehlschlag_verbraucht_weiterhin_einen_versuch():
    """Gegenprobe: Ein echter Fehlschlag bleibt begrenzt. Sonst wuerde bei
    einem dauerhaft kaputten Deploy bis zur Deadline weitergehaemmert."""
    h = _ReconcileHarness(
        branch_shas=[NEU], live_shas=[LIVE], trigger_ergebnis="failed"
    )

    await _reconcile(h, successful_sha=NEU, ci_success_reconcile_max_attempts=2)

    assert len(h.trigger_calls) <= 2, (
        f"Erwartet hoechstens 2 Versuche, waren {len(h.trigger_calls)}."
    )


@pytest.mark.asyncio
async def test_ohne_rueckmeldung_bleibt_es_beim_alten_verhalten():
    """Aeltere Aufrufer geben nichts zurueck (None). Das muss weiterhin als
    verbrauchter Versuch gelten — im Zweifel die vorsichtigere Auslegung."""
    h = _ReconcileHarness(branch_shas=[NEU], live_shas=[LIVE], trigger_ergebnis=None)

    await _reconcile(h, successful_sha=NEU, ci_success_reconcile_max_attempts=2)

    assert len(h.trigger_calls) <= 2
