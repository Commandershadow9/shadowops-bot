"""Ein Deploy-Auftrag waehrend eines laufenden Deploys wird nachgeholt, nicht verworfen.

Am 21.09.2026 blieben drei fertig gebaute Staende liegen. Der Ablauf:

    17:08  Merge #3524  -> Deploy startet, laeuft 36 Minuten
    17:50  Merge #3531  -> "Deployment already in progress" -> VERWORFEN
    17:50  Merge #3532  -> "Deployment already in progress" -> VERWORFEN

Fuer alle drei Staende baute `Release Image (GHCR)` erfolgreich ein Image;
ausgeliefert wurde keiner davon. Erst ein spaeterer, zufaellig passender Merge
nahm sie mit. In der Zwischenzeit wich `live` von `origin/main` ab, und der
einzige Hinweis darauf war der `zerodox-build-drift`-Waechter.

## Warum der bestehende Nachhol-Weg nicht greift

`_schedule_ci_success_reconcile` (ci_mixin) holt verworfene Deploys nach —
aber nur, wenn ein `workflow_run` mit `event_name == 'push'` auf einem
Deploy-Branch gruen wird (event_handlers_mixin, Auslesebedingung). Seit
ZERODOX#3328 gibt es **keinen Merge-Lauf auf `main`** mehr: Der Deploy nutzt
den gruenen PR-Stand, und `Web Quality` startet bei einem Push auf main nicht.
Damit kommt das ausloesende Event nie, und der Reconcile wird nie geplant.

Beide Aenderungen sind einzeln richtig. Zusammen ergeben sie: verworfene
Auftraege bleiben verworfen. Diese Kopplung sieht kein Test auf einer der
beiden Seiten — deshalb dieser hier.

## Was die Warteschlange leistet und was nicht

Gemerkt wird **nur der neueste** Auftrag je Projekt. Drei Merges waehrend
eines Deploys ergeben genau EINEN Nachholer, und der deployt ohnehin
`origin/main` — also den Stand, der alle drei enthaelt. Eine echte Queue mit
Historie waere hier falsch: Sie wuerde denselben Endzustand dreimal
ausliefern.
"""
import asyncio
import logging
from typing import Dict, Optional

import pytest

from src.integrations.deployment_manager import DeploymentManager


class _Manager(DeploymentManager):
    """Minimaler Manager: nur Sperre, Warteschlange und ein gezaehlter Deploy-Kern.

    Der echte `deploy_project` fuehrt Backup, Pull, Tests, Post-Deploy und
    Discord-Meldungen aus. Hier wird ausschliesslich das Zusammenspiel von
    Sperre und Warteschlange geprueft — alles andere ist fuer diese Frage
    Beiwerk und wuerde den Test an Dinge binden, die er nicht meint.
    """

    def __init__(self, deploy_dauer_sek: float = 0.05):
        self.logger = logging.getLogger("test-deploy-auftrag-einreihen")
        self.projects = {"zerodox": {"branch": "main", "deploy_enabled": True}}
        self.active_deployments: Dict[str, bool] = {}
        self.pending_deployments: Dict[str, Dict] = {}
        self._deploy_dauer = deploy_dauer_sek
        self.ausgefuehrt: list[str] = []

    async def _deploy_kern(self, project_key: str, branch: str) -> None:
        """Steht fuer die eigentliche Arbeit (Backup, Pull, Build, Health)."""
        await asyncio.sleep(self._deploy_dauer)
        self.ausgefuehrt.append(branch)


@pytest.mark.unit
async def test_zweiter_auftrag_wird_gemerkt_statt_verworfen():
    """Trifft ein Auftrag bei belegter Sperre ein, wird er vorgemerkt."""
    m = _Manager()
    m.active_deployments["zerodox"] = True  # ein Deploy laeuft

    ergebnis = m.auftrag_vormerken("zerodox", branch="main", deploy_context={"sha": "abc"})

    assert ergebnis is True, "Der Auftrag muss angenommen werden"
    assert "zerodox" in m.pending_deployments
    assert m.pending_deployments["zerodox"]["branch"] == "main"


@pytest.mark.unit
async def test_nur_der_neueste_auftrag_bleibt():
    """Drei Merges waehrend eines Deploys ergeben EINEN Nachholer.

    Der Nachholer deployt `origin/main` und enthaelt damit alle drei Staende.
    Drei Nachholer wuerden denselben Endzustand dreimal ausliefern.
    """
    m = _Manager()
    m.active_deployments["zerodox"] = True

    m.auftrag_vormerken("zerodox", branch="main", deploy_context={"sha": "erster"})
    m.auftrag_vormerken("zerodox", branch="main", deploy_context={"sha": "zweiter"})
    m.auftrag_vormerken("zerodox", branch="main", deploy_context={"sha": "dritter"})

    assert len(m.pending_deployments) == 1
    assert m.pending_deployments["zerodox"]["deploy_context"]["sha"] == "dritter"


@pytest.mark.unit
async def test_ohne_laufenden_deploy_wird_nichts_vorgemerkt():
    """Ist die Sperre frei, gehoert der Auftrag nicht in die Warteschlange.

    Sonst liefe jeder gewoehnliche Deploy zweimal.
    """
    m = _Manager()
    m.active_deployments["zerodox"] = False

    ergebnis = m.auftrag_vormerken("zerodox", branch="main", deploy_context=None)

    assert ergebnis is False
    assert m.pending_deployments == {}


@pytest.mark.unit
async def test_wartender_auftrag_wird_nach_freigabe_entnommen():
    """Nach dem Deploy wird der vorgemerkte Auftrag entnommen — genau einmal."""
    m = _Manager()
    m.active_deployments["zerodox"] = True
    m.auftrag_vormerken("zerodox", branch="main", deploy_context={"sha": "neu"})

    erster = m.wartenden_auftrag_entnehmen("zerodox")
    zweiter = m.wartenden_auftrag_entnehmen("zerodox")

    assert erster is not None and erster["deploy_context"]["sha"] == "neu"
    assert zweiter is None, "Ein entnommener Auftrag darf nicht doppelt laufen"


@pytest.mark.unit
async def test_kette_bricht_nach_obergrenze_ab():
    """Eine Kette von Nachholern ist begrenzt.

    Ohne Obergrenze koennte ein Auftrag, der bei jedem Lauf erneut vorgemerkt
    wird (etwa durch einen Dauerfehler im ausloesenden System), endlos
    weiterlaufen. Die Grenze macht aus einer moeglichen Endlosschleife eine
    Meldung.
    """
    m = _Manager()

    assert m.nachhol_grenze_erreicht(0) is False
    assert m.nachhol_grenze_erreicht(m.NACHHOL_MAX - 1) is False
    assert m.nachhol_grenze_erreicht(m.NACHHOL_MAX) is True


@pytest.mark.unit
async def test_verschiedene_projekte_stoeren_sich_nicht():
    """Die Warteschlange ist pro Projekt — ein ZERODOX-Deploy blockt mayday-sim nicht."""
    m = _Manager()
    m.projects["mayday-sim"] = {"branch": "main", "deploy_enabled": True}
    m.active_deployments["zerodox"] = True
    m.active_deployments["mayday-sim"] = True

    m.auftrag_vormerken("zerodox", branch="main", deploy_context={"sha": "z"})
    m.auftrag_vormerken("mayday-sim", branch="main", deploy_context={"sha": "m"})

    assert m.pending_deployments["zerodox"]["deploy_context"]["sha"] == "z"
    assert m.pending_deployments["mayday-sim"]["deploy_context"]["sha"] == "m"


class _EchterManager(DeploymentManager):
    """Umgeht `__init__`, um `deploy_project` selbst zu pruefen.

    Bei belegter Sperre steigt `deploy_project` fruehzeitig aus — vor Backup,
    Pull und Build. Dieser Pfad braucht nur `projects`, die beiden Dicts und
    einen Logger, und genau ihn prueft der Test: dass die Integration den
    Auftrag vormerkt statt ihn zu verwerfen.
    """

    def __init__(self):
        self.logger = logging.getLogger("test-deploy-integration")
        self.projects = {"zerodox": {"branch": "main", "deploy_enabled": True}}
        self.active_deployments: Dict[str, bool] = {"zerodox": True}
        self.pending_deployments: Dict[str, Dict] = {}


@pytest.mark.unit
async def test_deploy_project_merkt_vor_statt_zu_verwerfen():
    """Der Kern des Vorfalls: kein `success: False` mehr bei belegter Sperre.

    Vor dem 21.09.2026 lieferte dieser Pfad `success: False` mit
    "Deployment already in progress" — der Auftrag war weg, und der Aufrufer
    hatte keinen Anhalt, dass etwas nachzuholen ist.
    """
    m = _EchterManager()

    ergebnis = await m.deploy_project("zerodox", branch="main", deploy_context={"sha": "neu"})

    assert ergebnis['success'] is True, "Ein vorgemerkter Auftrag ist kein Fehlschlag"
    assert ergebnis.get('queued') is True
    assert ergebnis['error'] is None
    assert m.pending_deployments["zerodox"]["deploy_context"]["sha"] == "neu"


@pytest.mark.unit
async def test_projektname_mit_bindestrich_findet_die_sperre():
    """Die Sperre haengt am normalisierten Schluessel, nicht am uebergebenen Namen.

    `deploy_project` normalisiert `mayday-sim` auf `mayday_sim`, wenn die Config
    den Unterstrich fuehrt. Wuerde die Warteschlange den ROHEN Namen nutzen,
    landete der Auftrag unter einem Schluessel, den niemand abfragt — er waere
    genauso verloren wie vorher, nur unsichtbar.
    """
    m = _EchterManager()
    m.projects = {"mayday_sim": {"branch": "main", "deploy_enabled": True}}
    m.active_deployments = {"mayday_sim": True}

    ergebnis = await m.deploy_project("mayday-sim", branch="main", deploy_context=None)

    assert ergebnis.get('queued') is True
    assert "mayday_sim" in m.pending_deployments, "Auftrag muss unter dem Config-Schluessel liegen"
