"""Ein haengender Deploy darf nicht unbegrenzt blockieren (ZERODOX#3447).

Am 17.09.2026 meldete der Bot "Starting deployment: ZERODOX@224ff05" und danach
nichts mehr: kein Ergebnis, kein deploy.sh-Prozess, kein Deploy-Log, der
Deploy-Baum unveraendert, keine Ausnahme im Journal. Der Auftrag war weg, und
die Reservierung blieb gesetzt — erst ein Neustart des Bots loeste den Zustand.

Die Ursache ist bis heute offen. Die Zeitgrenze behebt sie NICHT; sie sorgt
dafuer, dass der Zustand endet, sichtbar wird und die naechste Auslieferung
nicht mitnimmt.

Geprueft wird der QUELLTEXT, nicht das Laufzeitverhalten: `_trigger_deployment`
durchlaeuft vorher das komplette CI-Warten samt Docs-only-Erkennung und
Tree-/PR-Head-Wiederverwendung. Ein Test, der das alles nachbaut, pruefte am
Ende vor allem seine eigenen Attrappen. Was hier zaehlt, ist die Verdrahtung —
dass der Aufruf ueberhaupt unter einer Grenze steht und dass der Fehlerpfad
darunter die Reservierung freigibt.
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
    """Kommentare zu Leerzeichen, damit Zeilennummern erhalten bleiben."""
    return re.sub(r"(^|\s)#.*$", lambda m: m.group(0).replace("#", " "), text, flags=re.M)


def test_deploy_aufruf_steht_unter_einer_zeitgrenze():
    code = _ohne_kommentare(_quelle())
    assert re.search(
        r"asyncio\.wait_for\(\s*\n?\s*self\.deployment_manager\.deploy_project\(",
        code,
    ), (
        "Der Aufruf von deploy_project steht nicht unter asyncio.wait_for. Ohne "
        "Grenze wartet der `await` unbegrenzt — genau der Zustand vom "
        "17.09.2026, der jede weitere Auslieferung blockierte, bis der Bot neu "
        "startete."
    )


def test_grenze_ist_grosszuegig_und_konfigurierbar():
    code = _ohne_kommentare(_quelle())
    treffer = re.search(r"hard_timeout_min['\"]\s*,\s*(\d+)\s*\)", code)
    assert treffer, "Die Grenze ist nicht ueber `hard_timeout_min` konfigurierbar."
    minuten = int(treffer.group(1))
    # Ein echter Deploy dauert ~4 min (gemessen 17.09.2026); das CI-Warten
    # liegt davor und zaehlt nicht mit. Eine knappe Grenze schnitte gesunde,
    # nur langsame Laeufe ab — genau der Fehler, den #2920 fuer CI-Timeouts
    # beschreibt ("misst die Maschinenlast, nicht den Code").
    assert minuten >= 30, (
        f"Die Vorgabe betraegt {minuten} min. Das ist zu knapp: Ein Deploy "
        "dauert ~4 min, und eine Grenze nahe der Normaldauer misst die "
        "Maschinenlast statt eines Defekts."
    )


def test_fehlerpfad_gibt_die_reservierung_frei():
    code = _ohne_kommentare(_quelle())
    # Der TimeoutError landet im allgemeinen `except Exception`. Der MUSS die
    # Reservierung freigeben, sonst endet zwar das Warten, aber der naechste
    # Deploy liefe weiter in "Deployment already in progress".
    assert re.search(
        r"except Exception as e:\s*\n\s*self\._release_deploy\(", code
    ), (
        "Der except-Block gibt die Deploy-Reservierung nicht mehr als Erstes "
        "frei. Dann beendet die Zeitgrenze zwar das Warten, der Zustand bliebe "
        "aber blockierend."
    )


def test_gegenprobe_die_pruefung_greift():
    # Ein Wachposten belegt nur, was seine Gegenprobe zeigt.
    ohne_grenze = "result = await self.deployment_manager.deploy_project(\n    repo, branch\n)"
    assert not re.search(
        r"asyncio\.wait_for\(\s*\n?\s*self\.deployment_manager\.deploy_project\(",
        ohne_grenze,
    ), "Das Muster akzeptiert einen ungeschuetzten Aufruf — es misst nichts."
    assert len(_ohne_kommentare(_quelle())) > 10000, "ci_mixin.py wirkt zu klein — falscher Pfad?"
