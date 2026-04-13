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
        }[update_size]
