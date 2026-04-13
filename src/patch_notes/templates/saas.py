"""SaaS-Template — GuildScout, ZERODOX."""
from patch_notes.templates.base import BaseTemplate


class SaaSTemplate(BaseTemplate):
    def categories(self) -> list[str]:
        return ["Neue Features", "Verbesserungen", "Bugfixes",
                "Sicherheit", "Performance"]

    def tone_instruction(self) -> str:
        return ("Sachlicher, professioneller Ton. "
                "Beschreibe den konkreten Nutzen für den User: "
                "'Rechnungen können jetzt direkt per Drag & Drop hochgeladen werden'. "
                "Vermeide Hype-Sprache. Fokus auf Business-Value.")

    def badges(self) -> list[str]:
        return ["feature", "improvement", "fix", "security", "performance", "breaking"]

    def length_limits(self, update_size: str) -> dict:
        return {
            "small":  {"min": 800,  "max": 1500, "features": "1-3"},
            "normal": {"min": 1500, "max": 3000, "features": "2-5"},
            "big":    {"min": 2500, "max": 4000, "features": "3-6"},
            "major":  {"min": 3500, "max": 5500, "features": "4-8"},
        }[update_size]
