"""DevOps-Template — ShadowOps Bot, AI-Agent-Framework."""
from patch_notes.templates.base import BaseTemplate


class DevOpsTemplate(BaseTemplate):
    def categories(self) -> list[str]:
        return ["Features", "Fixes", "Security", "Refactoring", "Dependencies"]

    def tone_instruction(self) -> str:
        return ("Kompakter, technischer Ton für Entwickler. "
                "Nenne konkrete Dateien/Module nur wenn relevant. "
                "Keine Marketing-Sprache. Fokus auf was sich ändert und warum.")

    def badges(self) -> list[str]:
        return ["feature", "fix", "security", "refactor"]

    def length_limits(self, update_size: str) -> dict:
        return {
            "small":  {"min": 500,  "max": 1200, "features": "1-2"},
            "normal": {"min": 1000, "max": 2000, "features": "2-4"},
            "big":    {"min": 1500, "max": 3000, "features": "3-5"},
            "major":  {"min": 2500, "max": 4000, "features": "4-6"},
        }[update_size]
