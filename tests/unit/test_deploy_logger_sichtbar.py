"""Der Deploy-Pfad muss im Journal sichtbar sein (ZERODOX#3328-Folge).

Die Handler des Bots haengen am Logger `shadowops` (src/bot.py). Ein Modul,
das `logging.getLogger(__name__)` benutzt, landet unter
`src.integrations.…` — ausserhalb dieses Baums. Seine Ausgaben erreichen das
Journal nie.

Der `deployment_manager` war so ein Modul. Folge: Ein Deploy hinterliess genau
EINE sichtbare Spur, naemlich die Ergebniszeile, die `ci_mixin` selbst
schreibt. Blieb die aus, gab es nichts, woran zu erkennen gewesen waere, wo der
Deploy stehenblieb.

Am 17.09.2026 ist genau das passiert: "Starting deployment: ZERODOX@224ff05",
danach 35 Minuten nichts — kein Ergebnis, kein deploy.sh-Prozess, kein
Deploy-Log, der Deploy-Baum unveraendert. Die Diagnose kostete eine Stunde und
endete bei einer Vermutung, weil die Zwischenschritte schlicht nicht
aufgezeichnet waren.

Dieser Test haelt fest, dass der Deploy-Pfad sichtbar bleibt. Er prueft die
Zugehoerigkeit zum Handler-Baum, nicht den exakten Namen — `shadowops.deploy`
waere ebenso richtig wie `shadowops.deployment`.
"""
import logging

from src.integrations import deployment_manager


WURZEL = "shadowops"


def test_logger_haengt_am_handler_baum():
    name = deployment_manager.logger.name
    assert name == WURZEL or name.startswith(WURZEL + "."), (
        f"deployment_manager.logger heisst '{name}' und liegt damit ausserhalb "
        f"des Handler-Baums '{WURZEL}'. Jede Log-Zeile dieses Moduls waere "
        "unsichtbar — und ein haengender Deploy damit nicht diagnostizierbar. "
        "Siehe Modulkopf von deployment_manager.py."
    )


def test_logger_ist_nicht_der_modulname():
    # Die Gegenprobe zur eigentlichen Falle: `getLogger(__name__)` ergaebe
    # 'src.integrations.deployment_manager'. Ohne diesen Fall wuerde der Test
    # oben auch dann gruen, wenn jemand den Handler-Baum umbenennt und dabei
    # versehentlich wieder den Modulnamen einsetzt.
    assert deployment_manager.logger.name != deployment_manager.__name__, (
        "Der Logger traegt wieder den Modulnamen — genau die Fassung, die das "
        "Modul stumm gemacht hat."
    )


def test_gegenprobe_die_pruefung_greift():
    # Ein Wachposten belegt nur, was seine Gegenprobe zeigt: Ein Logger mit
    # Modulnamen MUSS von der Bedingung oben abgelehnt werden.
    fremd = logging.getLogger("src.integrations.deployment_manager")
    assert not (fremd.name == WURZEL or fremd.name.startswith(WURZEL + ".")), (
        "Die Zugehoerigkeitspruefung akzeptiert einen Modulnamen-Logger — sie "
        "wuerde die Falle also gar nicht bemerken."
    )
