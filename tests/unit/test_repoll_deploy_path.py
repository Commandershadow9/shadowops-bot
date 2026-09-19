"""Der Re-Poll muss den DEPLOY-Baum messen, nicht den Arbeitsbaum (ZERODOX#1720/#2344).

`_repoll_after_deploy` heilt den Fall, dass waehrend eines laufenden Deploys
weitere Merges eintreffen: Der `active_deployments`-Guard verwirft sie
stillschweigend, und der Re-Poll zieht sie danach nach.

Dafuer vergleicht er HEAD gegen origin/<branch>. Welchen HEAD, ist der ganze
Punkt: Seit ZERODOX#2344 deployt der Bot aus einem eigenen Baum (`deploy_path`)
und fasst den Arbeitsbaum (`path`) nicht mehr an. Wer dort misst, liest, was
der Entwickler gerade ausgecheckt hat.

Belegt am 19.09.2026: Zwei Merges (7df6f87, f35f855) wurden waehrend eines
laufenden Deploys verworfen. Der Re-Poll lief nach dessen Erfolg um 14:32 und
meldete nichts — der Arbeitsbaum war zufaellig aktuell, also galt
`deployed_sha == remote_sha`. Live blieb 25 Minuten ein Stand hinter
origin/main, darunter unausgelieferter Billing-Code.

⚠️ Der Fehler war unsichtbar, weil er wie Erfolg aussieht: Ein Re-Poll, der
nichts findet, loggt nichts. "Nichts zu tun" und "an der falschen Stelle
nachgesehen" sind ohne Test nicht zu unterscheiden.
"""
import re
from pathlib import Path

CI_MIXIN = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "integrations"
    / "github_integration"
    / "ci_mixin.py"
)


def _quelle() -> str:
    return CI_MIXIN.read_text(encoding="utf-8")


def _ohne_kommentare(text: str) -> str:
    """Kommentare zu Leerzeichen — sie nennen beide Feldnamen im Klartext."""
    return re.sub(r"(^|\s)#.*$", lambda m: m.group(0).replace("#", " "), text, flags=re.M)


def _repoll_koerper() -> str:
    """Nur der Rumpf von _repoll_after_deploy, ohne Docstring."""
    quelle = _ohne_kommentare(_quelle())
    start = quelle.index("async def _repoll_after_deploy")
    rest = quelle[start:]
    # Docstring entfernen: er nennt beide Felder in der Erklaerung.
    ohne_doc = re.sub(r'"""[\s\S]*?"""', "", rest, count=1)
    ende = ohne_doc.find("\n    async def ", 1)
    return ohne_doc if ende == -1 else ohne_doc[:ende]


def test_deploy_path_wird_bevorzugt():
    koerper = _repoll_koerper()
    assert "project_config.get('deploy_path')" in koerper, (
        "Der Re-Poll liest `deploy_path` nicht. Dann misst er den Arbeitsbaum, "
        "in dem der Bot seit ZERODOX#2344 gar nicht mehr deployt — und "
        "uebersieht jeden Merge, den der active_deployments-Guard verworfen hat."
    )


def test_path_bleibt_als_rueckfall():
    """Ohne `deploy_path` muss alles bleiben wie bisher.

    Die anderen Projekte des Bots (GuildScout, mayday-sim) haben kein
    `deploy_path`. Ein harter Wechsel auf ein nicht gesetztes Feld machte den
    Re-Poll dort wirkungslos — derselbe Fehler, nur andersherum.
    """
    koerper = _repoll_koerper()
    assert re.search(
        r"project_config\.get\('deploy_path'\)\s+or\s+project_config\.get\('path'\)",
        koerper,
    ), (
        "Der Rueckfall auf `path` fehlt oder die Reihenfolge stimmt nicht. "
        "Erwartet: deploy_path ODER path, in dieser Reihenfolge."
    )


def test_reihenfolge_nicht_vertauscht():
    """Gegenprobe: `path or deploy_path` waere wirkungslos.

    `path` ist bei ZERODOX gesetzt. Stuende es vorn, gewaenne es immer — der
    Fix saehe im Diff richtig aus und aenderte nichts.
    """
    koerper = _repoll_koerper()
    assert not re.search(
        r"project_config\.get\('path'\)\s+or\s+project_config\.get\('deploy_path'\)",
        koerper,
    ), "deploy_path steht hinten und wird nie erreicht — `path` ist immer gesetzt."


def test_deployment_manager_nutzt_dieselbe_aufloesung():
    """Zwei Stellen, eine Wahrheit.

    `deployment_manager._deploy_path()` loest denselben Pfad auf. Laufen die
    beiden auseinander, deployt der Bot aus dem einen Baum und prueft den
    anderen — genau die Klasse Fehler, die dieser Test verhindert.
    """
    manager = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "integrations"
        / "deployment_manager.py"
    ).read_text(encoding="utf-8")
    assert re.search(
        r"project\.get\('deploy_path'\)\s+or\s+project\['path'\]",
        _ohne_kommentare(manager),
    ), (
        "deployment_manager._deploy_path() wurde umgebaut. Der Re-Poll folgt "
        "dessen Aufloesung — beide muessen denselben Baum meinen."
    )
