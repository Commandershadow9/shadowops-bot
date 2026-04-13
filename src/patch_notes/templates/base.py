"""BaseTemplate — Shared Prompt-Builder für alle Projekt-Typen."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from patch_notes.context import PipelineContext

logger = logging.getLogger('shadowops')


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
            self._extra_context_section(ctx),
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

        # Smart Diff: Kategorisierte Dateiübersicht
        categories = ctx.git_stats.get('categories')
        if categories:
            lines.append("## Code-Änderungen (strukturierte Übersicht)")
            for cat, count in categories.items():
                lines.append(f"  {cat}: {count} Dateien geändert")
            new_f = ctx.git_stats.get('new_files', 0)
            del_f = ctx.git_stats.get('deleted_files', 0)
            if new_f:
                lines.append(f"  Neue Dateien: {new_f}")
            if del_f:
                lines.append(f"  Gelöschte Dateien: {del_f}")
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

    def _extra_context_section(self, ctx: PipelineContext) -> str:
        """Lade Release-Guide + Context-Files aus Projekt-Verzeichnis."""
        project_path = ctx.project_config.get('path', '')
        if not project_path:
            return ""
        base = Path(project_path)
        if not base.exists():
            return ""

        sections: list[str] = []
        pc = ctx.project_config.get('patch_notes', {})

        # 1. Release-Guide (release_guide.md)
        for name in ('release_guide.md', 'docs/release_guide.md', 'RELEASE_GUIDE.md'):
            guide_path = base / name
            if guide_path.exists():
                try:
                    content = guide_path.read_text(encoding='utf-8').strip()
                    if content and len(content) >= 20:
                        sections.append(
                            "FEATURE-ANLEITUNGEN (vom Entwickler geschrieben — WÖRTLICH übernehmen!):\n"
                            "Füge diese als '📖 So funktioniert's'-Absatz ein. NICHT umschreiben!\n\n"
                            + content[:2000]
                        )
                        logger.info(f"📋 Release-Guide geladen: {guide_path}")
                        break
                except Exception:
                    continue

        # 2. Context-Files aus Config
        context_files = pc.get('context_files') or pc.get('context_file')
        if context_files:
            if isinstance(context_files, str):
                context_files = [context_files]
            per_file_limit = int(pc.get('context_max_chars', 1500))
            total_limit = int(pc.get('context_total_max_chars', 4000))
            total = 0
            for entry in context_files:
                if not entry:
                    continue
                p = Path(entry)
                if not p.is_absolute():
                    p = base / p
                if not p.exists():
                    continue
                try:
                    text = p.read_text(encoding='utf-8', errors='ignore').strip()
                except Exception:
                    continue
                if not text:
                    continue
                if per_file_limit and len(text) > per_file_limit:
                    half = per_file_limit // 2
                    text = text[:half] + "\n... (snip) ...\n" + text[-half:]
                section = f"PROJECT CONTEXT FILE: {p.name}\n{text}"
                total += len(section)
                if total_limit and total > total_limit:
                    break
                sections.append(section)

        if not sections:
            return ""
        return "PROJECT CONTEXT (REFERENCE):\n\n" + "\n\n".join(sections)

    def _rules_section(self, ctx: PipelineContext) -> str:
        lang = ctx.project_config.get('patch_notes', {}).get('language', 'de')
        if lang == 'de':
            return self._CLASSIFICATION_RULES_DE
        return self._CLASSIFICATION_RULES_EN

    # Basis-Regelblock der IMMER an jeden Prompt angehängt wird (A/B-Varianten-sicher)
    _CLASSIFICATION_RULES_DE = """
COMMIT-TYP-REGELN (IMMER BEACHTEN):
- [FEATURE] = Implementiertes Feature → als "Neues Feature" listen
- [BUGFIX] = Behobener Bug → als "Bugfix" listen
- [SECURITY] = Sicherheitsfix → vage beschreiben (kein WIE)
- [DESIGN_DOC] = NUR ein Planungsdokument → NIEMALS als Feature listen!
- [DEPS] / [OTHER] mit Auto-Gruppe = Automatisiert → kurz zusammenfassen
- [DOCS] / [IMPROVEMENT] / [TEST] = Intern → nur erwähnen wenn nutzerrelevant
- [REVERT] = Rückgängig gemacht → erwähnen wenn nutzerrelevant
- [BREAKING] = Breaking Change → IMMER prominent erwähnen
- PR-Labels haben Vorrang vor Commit-Prefix-Tags
- Erfinde KEINE Features die nicht als [FEATURE] getaggt sind!
- Erfinde KEINE Version — der Titel hat NUR den Update-Namen"""

    _CLASSIFICATION_RULES_EN = """
COMMIT TYPE RULES (ALWAYS OBSERVE):
- [FEATURE] = Implemented feature → list as "New Feature"
- [BUGFIX] = Fixed bug → list as "Bug Fix"
- [SECURITY] = Security fix → describe vaguely (not HOW)
- [DESIGN_DOC] = Planning doc only → NEVER list as feature!
- [DEPS] / [OTHER] with auto-group = Automated → summarize briefly
- [DOCS] / [IMPROVEMENT] / [TEST] = Internal → mention only if user-relevant
- [REVERT] = Reverted → mention if user-relevant
- [BREAKING] = Breaking change → ALWAYS mention prominently
- PR labels take precedence over commit prefix tags
- Do NOT invent features that are not tagged [FEATURE]!
- Do NOT invent a version — the title must contain ONLY the update name"""
