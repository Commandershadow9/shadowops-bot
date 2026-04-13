"""BaseTemplate — Shared Prompt-Builder für alle Projekt-Typen."""
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from patch_notes.context import PipelineContext


class BaseTemplate:
    """Basis-Template. Alle Typ-Templates erben hiervon."""

    def categories(self) -> list[str]:
        return ["Features", "Fixes", "Improvements", "Other"]

    def tone_instruction(self) -> str:
        return "Sachlich und klar. Beschreibe den konkreten Nutzen jeder Änderung."

    def badges(self) -> list[str]:
        return ["feature", "fix", "improvement", "breaking"]

    def length_limits(self, update_size: str) -> dict:
        return {
            "small":  {"min": 800, "max": 1500, "features": "1-3"},
            "normal": {"min": 1500, "max": 3000, "features": "2-5"},
            "big":    {"min": 2500, "max": 4000, "features": "3-6"},
            "major":  {"min": 3500, "max": 5500, "features": "4-8"},
        }[update_size]

    def build_prompt(self, ctx: PipelineContext) -> str:
        """Baue den AI-Prompt aus PipelineContext."""
        sections = [
            self._system_instruction(ctx),
            self._groups_section(ctx),
            self._context_section(ctx),
            self._previous_version_guard(ctx),
            self._update_size_override(ctx),
            self._rules_section(ctx),
        ]
        return "\n\n".join(s for s in sections if s)

    def _system_instruction(self, ctx: PipelineContext) -> str:
        pc = ctx.project_config.get('patch_notes', {})
        lang = pc.get('language', 'de')
        limits = self.length_limits(ctx.update_size)
        lang_instruction = "Antworte auf Deutsch." if lang == 'de' else "Answer in English."

        return f"""Du bist ein Patch-Notes-Schreiber für {ctx.project}.
{lang_instruction}
Ton: {self.tone_instruction()}
Zielgruppe: {pc.get('target_audience', 'Entwickler und Nutzer')}
Projektbeschreibung: {pc.get('project_description', ctx.project)}

Verwende diese Kategorien: {', '.join(self.categories())}
Zeichenlimit: {limits['min']}-{limits['max']} Zeichen
Features: {limits['features']} Highlights

Antworte als JSON mit den Feldern: title, tldr, web_content, changes (Array mit type/description/details), seo_keywords.
WICHTIG: Erfinde KEINE Version im Titel. Der Titel enthält NUR den Namen des Updates."""

    def _groups_section(self, ctx: PipelineContext) -> str:
        lines = [f"# Änderungen in {ctx.project} (v{ctx.version})"]
        lines.append(f"Update-Größe: {ctx.update_size.upper()} ({len(ctx.enriched_commits or ctx.raw_commits)} Commits)")
        lines.append("")

        player_groups = [g for g in ctx.groups if g.get('is_player_facing')]
        infra_groups = [g for g in ctx.groups if not g.get('is_player_facing')]

        if player_groups:
            lines.append("## Spieler-/Nutzer-relevante Änderungen")
            for g in player_groups:
                lines.append(f"### [{g['tag']}] {g['theme']} ({len(g['commits'])} Commits)")
                lines.append(f"  Zusammenfassung: {g['summary']}")
                if g.get('pr_labels'):
                    lines.append(f"  Labels: {', '.join(g['pr_labels'])}")
                for c in g['commits'][:5]:
                    lines.append(f"  - {c['message'].split(chr(10))[0]}")
                if len(g['commits']) > 5:
                    lines.append(f"  - ... und {len(g['commits']) - 5} weitere")
                lines.append("")

        if infra_groups:
            lines.append("## Infrastruktur / Backend (für Stabilitäts-Sektion)")
            for g in infra_groups:
                lines.append(f"### [{g['tag']}] {g['theme']} ({len(g['commits'])} Commits)")
                lines.append(f"  Zusammenfassung: {g['summary']}")
                lines.append("")

        return "\n".join(lines)

    def _context_section(self, ctx: PipelineContext) -> str:
        credits = ctx.team_credits
        if not credits:
            return ""
        lines = ["# Team-Credits"]
        for c in credits:
            lines.append(f"- {c.get('name', '?')} ({c.get('role', '?')}): {c.get('commits', 0)} Commits")
        return "\n".join(lines)

    def _previous_version_guard(self, ctx: PipelineContext) -> str:
        if not ctx.previous_version_content:
            return ""
        return f"""# BEREITS ABGEDECKT (vorherige Version)
Die folgenden Inhalte waren bereits in der letzten Patch Note enthalten.
Erwähne sie NICHT erneut:
{ctx.previous_version_content[:500]}"""

    def _update_size_override(self, ctx: PipelineContext) -> str:
        if ctx.update_size == "major":
            return """═══════════════════════════════════════
⚡ MAJOR UPDATE MODUS (60+ Commits) ⚡
═══════════════════════════════════════
Dies ist ein GROSSES UPDATE. Nutze den vollen Platz aus.
Hebe die wichtigsten 5-8 Änderungen hervor.
Fasse verwandte Commits zu thematischen Blöcken zusammen."""
        if ctx.update_size == "big":
            return """═══════════════════════════════════════
📦 BIG UPDATE MODUS (30-60 Commits) 📦
═══════════════════════════════════════
Dies ist ein umfangreiches Update. Hebe 4-7 Highlights hervor."""
        return ""

    def _rules_section(self, ctx: PipelineContext) -> str:
        return """# REGELN (IMMER befolgen)
- [DESIGN-DOC] Commits = GEPLANT, NICHT IMPLEMENTIERT → NIEMALS als Feature listen
- [DOCS] Commits = Dokumentation, nicht als Feature listen
- [MERGE] und [AUTO] Commits IGNORIEREN
- Erfinde KEINE Features die nicht in den Commits stehen
- Erfinde KEINE Version — der Titel hat NUR den Update-Namen"""
