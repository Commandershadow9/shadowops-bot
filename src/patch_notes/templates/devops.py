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
            "mega":   {"min": 3500, "max": 5500, "features": "6-9"},
        }[update_size]

    def audience_address(self) -> str:
        return "Ops"

    def few_shot_example(self) -> str:
        """Ziel-Tonalität für DevOps-Mega-Releases — technisch, aber mit Rahmen."""
        return """# Pipeline v6 — State-Machine-Refactor

Ops,

die alte Patch-Notes-Pipeline hatte einen Webhook-Spam-Bug, bei dem jeder Git-Push
eine eigene Mini-Version postete (v1.3.2, v1.3.3, v1.3.4 ...). Zusammen mit dem
Kanal-ID-Lookup-Mismatch endete das in 0 gesendeten Messages trotz „erfolgreicher"
Pipeline. Dieser Release räumt das komplett auf und baut v6 neu als State Machine.

## Die Leitidee: EINE Release-Quelle, fünf explizite Stufen

Statt ad-hoc Webhook-getriggerter Releases gibt es jetzt COLLECTING → CLASSIFYING →
GENERATING → VALIDATING → DISTRIBUTING, mit Persistenz nach jeder Stufe und
Resume-Fähigkeit nach Crash.

## Drei konkrete Änderungen

**Webhook sammelt statt zu releasen.** Vorher: Jeder Push generierte eine Mini-
Version. Jetzt: Batcher sammelt ALLE Commits, Release nur via Cron oder
`/release-notes`. Kein Spam mehr.

**Channel-ID-Fallback auf Top-Level.** Vorher: `pn_config['update_channel_id']`
fand nie was, weil bot.py es auf Top-Level setzt. Jetzt: Fallback liest beide
Pfade.

**Klassifizierung in 5 Stufen.** small → normal → big → major → mega (≥80 Commits
oder ≥5 FEATURE-Gruppen). Jede Stufe hat eigene Templates, Längen-Limits und
Narrative-Tonalität.

## Was dahinter steckt

[DEV-KONTEXT aus release_notes.md wird hier eingebettet.]
"""
