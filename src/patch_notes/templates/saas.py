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
            "mega":   {"min": 4500, "max": 7500, "features": "6-10"},
        }[update_size]

    def audience_address(self) -> str:
        return "Team"

    def few_shot_example(self) -> str:
        """Ziel-Tonalität für SaaS-Mega-Releases — sachlich aber mit Story-Bogen."""
        return """# Rechnungsworkflow v2 — Was sich geändert hat

Team,

wer bei ZERODOX im letzten Monat Rechnungen verarbeitet hat, kennt das Gefühl:
zwischen PDF-Upload, manueller Zuordnung und Steuer-Check ging zu viel Zeit verloren.
Dieses Release räumt genau damit auf.

## Die Leitidee: PDF-Upload endet bei der Buchung, nicht beim Upload

Statt jeden Schritt einzeln zu klicken, soll die Pipeline von Upload bis zur
gebuchten Rechnung in einem Flow laufen — mit automatischen Zwischenständen,
aber ohne dass du etwas manuell „anstossen" musst.

## Drei konkrete Änderungen

**Drag & Drop setzt jetzt die komplette Kette in Gang.** Vorher: Upload, dann
„Verarbeiten" klicken, dann Warteschleife. Jetzt: Drop, OCR + Matching laufen
inline, du siehst das Ergebnis in ~8 Sekunden.

**Steuersatz-Prüfung läuft gegen deine Historie.** Wenn du 2024 für Lieferant X
immer 7% gebucht hast, schlägt das System diesen Satz beim neuen PDF vor. Kein
starres Default mehr.

**Mehrere Rechnungen auf einmal.** Bis zu 50 PDFs parallel, mit Progress-Bar und
automatischer Fehler-Queue bei Problem-PDFs.

## Was dahinter steckt

[DEV-KONTEXT aus release_notes.md wird hier eingebettet.]
"""
