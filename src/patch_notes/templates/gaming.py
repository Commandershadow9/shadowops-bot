"""Gaming-Template — MayDay Sim, Community-Spiele."""
from patch_notes.templates.base import BaseTemplate


class GamingTemplate(BaseTemplate):
    def categories(self) -> list[str]:
        return ["Neuer Content", "Gameplay-Verbesserungen", "Design & Look",
                "Stabilität & Performance", "So funktioniert's", "Demnächst"]

    def tone_instruction(self) -> str:
        return ("Schreibe aus der Perspektive eines begeisterten Spielers. "
                "Beschreibe Features mit konkreten Mini-Szenarien: "
                "'Stell dir vor, drei Einsätze laufen parallel...'. "
                "Nutze Hype-Sprache für große Features. "
                "Verwende BOS-Fachbegriffe wenn passend.")

    def badges(self) -> list[str]:
        return ["feature", "content", "gameplay", "design", "performance",
                "multiplayer", "fix", "breaking", "infrastructure",
                "improvement", "docs", "security"]

    def length_limits(self, update_size: str) -> dict:
        return {
            "small":  {"min": 1500, "max": 2500, "features": "2-3"},
            "normal": {"min": 2500, "max": 4000, "features": "3-5"},
            "big":    {"min": 3500, "max": 5500, "features": "4-7"},
            "major":  {"min": 4500, "max": 7000, "features": "5-8"},
            "mega":   {"min": 5500, "max": 9000, "features": "7-12"},
        }[update_size]

    def audience_address(self) -> str:
        return "Dispatcher"

    def few_shot_example(self) -> str:
        """Ziel-Tonalität für MayDay-Sim-Mega-Releases — Gaming-Dev-Commentary."""
        return """# Operation Lifecycle — Der Leitstellen-Umbau

Dispatcher,

was sich in den letzten drei Wochen verändert hat: Ein typischer Brandeinsatz in
MAYDAY SIM hatte bisher drei Zustände — alarmiert, in Arbeit, resolved. Linear.
Berechenbar. Langweilig, wenn du länger als 20 Min spielst. Das ändert sich jetzt.

## Die Leitidee: BOS-Lifecycle end-to-end

Vom Alarm über die Einsatzphasen bis zur Klinik-Übergabe soll jeder Schritt als
eigener nachvollziehbarer Zustand existieren — nicht als hart verdrahteter Status.
Das klingt nach Unterbau, und das ist es auch. Aber die Auswirkung spürst du sofort.

## Drei Momente, die sich im Spielgefühl verändert haben

**Der Moment, wo die Lage NICHT vorbei ist.** Vorher: Brandeinsatz resolved, du
klickst weiter. Jetzt: Ein WindingDown-Event feuert, und per Timer kann eine
Rückzündung die Lage reanimieren. Plötzlich haben grosse Einsätze echten Druck.

**Der Moment, wo du siehst wohin der RTW fährt.** Vorher: Blackbox zwischen
Einsatzstelle und „Fahrzeug wieder frei". Jetzt: Klinik-Marker, Transport-Events,
sauberer Übergabe-Timer. Du dispatcherst statt zu warten.

**Der Moment, wo du den Disponenten-Fehler korrigierst.** Vorher: Fahrzeug fährt
zur falschen Adresse, du musst warten bis es ankommt. Jetzt: Direkt im EN_ROUTE-
Fenster umleiten. Das ist der Unterschied zwischen Stress-Simulation und echter
Handlungsfähigkeit unter Druck.

## Was dahinter steckt

[Hier wird später der DEV-KONTEXT aus release_notes.md wörtlich eingebettet.]

## Warum das alles zusammen?

Diese drei Dinge funktionieren nicht isoliert: Transport braucht Lifecycle-Events,
Kaskaden brauchen Phasen, Umleiten braucht saubere Transitions. Musste zusammen
passieren. Deshalb ein echter Umbau, kein Patch.
"""
