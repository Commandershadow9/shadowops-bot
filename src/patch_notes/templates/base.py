"""BaseTemplate — Shared Prompt-Builder für alle Projekt-Typen."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from patch_notes.context import PipelineContext

logger = logging.getLogger('shadowops')

# HTML-Kommentare (auch mehrzeilig) aus release_notes.md entfernen, damit das
# Template-Kommentar-File nicht als Content gewertet wird.
_HTML_COMMENT_RE = re.compile(r'<!--.*?-->', re.DOTALL)


def _compute_time_window(commits: list[dict]) -> str:
    """Zeitfenster von first-commit bis last-commit als lesbarer String.

    Sinn: Die AI darf sagen 'ueber 3 Tage gewachsen' — aber nur wenn sie die
    Zahl aus diesem Feld uebernimmt, nicht erfindet.
    """
    from datetime import datetime, timezone
    dates = []
    for c in commits or []:
        ts = c.get('timestamp') or c.get('date') or c.get('author_date')
        if not ts:
            continue
        if isinstance(ts, str):
            # ISO-Format (mit oder ohne Timezone)
            try:
                dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
            except ValueError:
                continue
        elif isinstance(ts, (int, float)):
            dt = datetime.fromtimestamp(ts, timezone.utc)
        else:
            continue
        dates.append(dt)
    if len(dates) < 2:
        return ""
    first, last = min(dates), max(dates)
    delta = last - first
    days = delta.days
    if days == 0:
        return f"alle Commits am {first.strftime('%Y-%m-%d')} (Sprint-Tag)"
    if days == 1:
        return f"ueber 2 Tage ({first.strftime('%Y-%m-%d')} bis {last.strftime('%Y-%m-%d')})"
    if days < 7:
        return f"ueber {days + 1} Tage ({first.strftime('%Y-%m-%d')} bis {last.strftime('%Y-%m-%d')})"
    if days < 30:
        weeks = (days + 3) // 7
        return f"ueber ~{weeks} Wochen ({first.strftime('%Y-%m-%d')} bis {last.strftime('%Y-%m-%d')})"
    return f"ueber ~{days // 7} Wochen ({first.strftime('%Y-%m-%d')} bis {last.strftime('%Y-%m-%d')})"


def _group_author_facts(groups: list[dict]) -> list[str]:
    """Pro Feature-Gruppe: Hauptautor + Commit-Count.

    Format: '[FEATURE] Thema: Shadow (12 Commits), Mapu (3)'
    Returnt max 6 Zeilen (AI soll nicht in Details ersaufen).
    """
    from collections import Counter
    lines = []
    for g in groups[:6]:
        commits = g.get('commits') or []
        if not commits:
            continue
        author_counts: Counter = Counter()
        for c in commits:
            author = c.get('author', {})
            if isinstance(author, dict):
                name = author.get('name', author.get('username', ''))
            elif isinstance(author, str):
                name = author
            else:
                name = ''
            if name and name.lower() not in ('codex', 'ai-bot', 'agent', 'bot'):
                author_counts[name] += 1
        if not author_counts:
            continue
        top = author_counts.most_common(3)
        attr = ', '.join(f"{n} ({c})" for n, c in top)
        tag = g.get('tag', 'FEATURE')
        theme = (g.get('theme') or '')[:60]
        lines.append(f"[{tag}] {theme}: {attr}")
    return lines


def _read_release_notes(project_base: Path) -> str:
    """Liest release_notes.md im Projekt-Root.

    Filtert HTML-Kommentare raus (damit das Template-File nicht als Content gilt).
    Returnt leeren String wenn Datei fehlt oder nach dem Filtern nichts übrig bleibt.

    Args:
        project_base: Projekt-Root-Pfad (enthält release_notes.md im Idealfall).

    Returns:
        Den gereinigten Markdown-Text, oder "" wenn keine echten Notes.
    """
    for name in ('release_notes.md', 'RELEASE_NOTES.md'):
        notes_path = project_base / name
        if not notes_path.exists():
            continue
        try:
            raw = notes_path.read_text(encoding='utf-8')
        except Exception as e:
            logger.debug(f"[v6] release_notes.md nicht lesbar: {e}")
            return ""
        stripped = _HTML_COMMENT_RE.sub('', raw).strip()
        # Truncate auf 3000 Zeichen (Prompt-Budget schützen)
        if len(stripped) < 20:  # "leeres" Template oder nur Whitespace
            return ""
        return stripped[:3000]
    return ""


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
            "small":  {"min": 800,  "max": 1500, "features": "1-3"},
            "normal": {"min": 1500, "max": 3000, "features": "2-5"},
            "big":    {"min": 2500, "max": 4000, "features": "3-6"},
            "major":  {"min": 3500, "max": 5500, "features": "4-8"},
            "mega":   {"min": 4500, "max": 7500, "features": "6-10"},
        }[update_size]

    def build_prompt(self, ctx: PipelineContext) -> str:
        """Baue den AI-Prompt aus PipelineContext."""
        sections = [
            self._system_instruction(ctx),
            self._groups_section(ctx),
            self._narrative_input_block(ctx),
            self._context_section(ctx),
            self._extra_context_section(ctx),
            self._previous_version_guard(ctx),
            self._update_size_override(ctx),
            self._rules_section(ctx),
        ]
        return "\n\n".join(s for s in sections if s)

    def _narrative_input_block(self, ctx: PipelineContext) -> str:
        """Zeitfenster + Autor-Fakten pro Feature-Gruppe (nur bei mega/major).

        Gibt der AI harte Datengrundlage fuer Storytelling-Elemente wie
        'ueber 3 Tage gewachsen' oder 'Shadow hat den Transport-Flow gebaut',
        OHNE das die AI Zahlen oder Namen erfinden darf.
        """
        if ctx.update_size not in ("mega", "major"):
            return ""

        parts: list[str] = ["RELEASE-FAKTEN (NUR diese Zahlen/Namen nutzen):"]

        # Zeitfenster aus enriched_commits
        commits = ctx.enriched_commits or ctx.raw_commits
        time_window = _compute_time_window(commits)
        if time_window:
            parts.append(f"- Zeitfenster: {time_window}")

        # Autor-Fakten pro Group
        author_lines = _group_author_facts(ctx.groups)
        if author_lines:
            parts.append("- Hauptbeitraeger pro Themen-Gruppe:")
            for line in author_lines:
                parts.append(f"  {line}")

        # Vorige Version als Vorher-Referenz (wenn vorhanden)
        if ctx.previous_version_content:
            first_line = ctx.previous_version_content.split('\n', 1)[0][:200]
            parts.append(f"- Vorherige Version als Vorher-Referenz: {first_line}")

        if len(parts) == 1:
            return ""
        return "\n".join(parts)

    def _system_instruction(self, ctx: PipelineContext) -> str:
        pc = ctx.project_config.get('patch_notes', {})
        lang = pc.get('language', 'de')
        limits = self.length_limits(ctx.update_size)
        lang_instruction = "Antworte auf Deutsch." if lang == 'de' else "Answer in English."

        return f"""Du bist Dev-Kommentator für {ctx.project}.
{lang_instruction}
Ton: {self.tone_instruction()}
Anrede deines Publikums: {self.audience_address()}
Zielgruppe: {pc.get('target_audience', 'Entwickler und Nutzer')}
Projektbeschreibung: {pc.get('project_description', ctx.project)}

Verwende diese Kategorien: {', '.join(self.categories())}
Zeichenlimit: {limits['min']}-{limits['max']} Zeichen
Features: {limits['features']} Highlights

REGELN für `changes[].description` und `details[]`:
- Jede description beantwortet in EINEM Satz: WAS hat sich geändert, WARUM
  ist das wichtig, WAS hat der Nutzer/Spieler konkret davon.
- details[] bringen konkrete Beispiele ("wenn du 3 Einsätze parallel jonglierst ...",
  "bei einer Migration mit 50M Rows ..."). Keine Marketing-Floskeln.
- KEINE Generika ("Verbesserte UX", "Bessere Performance") ohne konkreten Nutzen.
- KEINE leeren Adjektive ("massiv", "umfangreich", "spektakulär") ohne Zahl/Fakt.

Antworte als JSON mit: title, tldr, discord_highlights (3-6 Bullet-Points),
summary (1-3 Sätze Intro), web_content, changes (type/description/details),
seo_keywords.
WICHTIG: Erfinde KEINE Version im Titel. Der Titel enthält NUR den Namen des Updates."""

    # ── Stil-Hooks (Subclass-Override) ─────────────────────────────

    def audience_address(self) -> str:
        """Wen spricht das Template an? Gaming='Dispatcher', SaaS='Team', DevOps='Ops'."""
        return "Team"

    def few_shot_example(self) -> str:
        """Wörtliches Muster für mega/major web_content. Leer = Base-Fallback.

        Subklassen liefern ein gekürztes, idealtypisches Beispiel das die
        gewünschte Tonalität zeigt (LLMs imitieren Patterns besser als sie
        Regeln folgen).
        """
        return ""

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
        size = ctx.update_size
        n = len(ctx.enriched_commits or ctx.raw_commits)

        # mega/major bekommen den vollen Narrative-Block (Anti-Patterns, Structure, Few-Shot)
        if size in ("mega", "major"):
            return self._narrative_override(ctx, n)

        # Kleinere Stufen bleiben kompakt — nur dezente Größen-Hints
        if size == "big":
            return f"""═══════════════════════════════════════
📦 BIG UPDATE MODUS ({n} Commits) 📦
═══════════════════════════════════════
Umfangreich, aber nicht Mega. 3-6 Highlights mit klarer Story-Logik.
TL;DR: Ein Satz, der den roten Faden trägt."""
        if size == "normal":
            return """═══════════════════════════════════════
📝 NORMAL UPDATE MODUS
═══════════════════════════════════════
Kompakt, freundlich, strukturiert. 2-5 Highlights."""
        if size == "small":
            return """═══════════════════════════════════════
🔹 KLEINES UPDATE
═══════════════════════════════════════
Kein Hype. 1-3 Highlights, Notiz-Ton. TL;DR in einem Satz."""
        return ""

    def _narrative_override(self, ctx: PipelineContext, commit_count: int) -> str:
        """Narrative Patch Notes (Gaming-Dev + Product-Story) für mega/major.

        Enthält Anti-Patterns, 6-Sektionen-Structure, optional Few-Shot-Beispiel
        aus der Subclass. Design: docs/plans/2026-04-15-narrative-patch-notes-design.md
        """
        size = ctx.update_size.upper()
        badge = "🚀💥" if ctx.update_size == "mega" else "⚡"
        hype_label = "MEGA-UPDATE" if ctx.update_size == "mega" else "MAJOR-UPDATE"
        audience = self.audience_address()
        few_shot = self.few_shot_example().strip()

        parts = [
            "═══════════════════════════════════════",
            f"{badge} {hype_label} MODUS ({commit_count} Commits) {badge}",
            "═══════════════════════════════════════",
            "",
            "Das ist KEIN Standard-Release. Das hier ist ein Meilenstein.",
            "Schreib wie ein Dev-Commentator, der seine Community ernst nimmt.",
            f"Direkte Anrede: '{audience}'. Kein 'Hallo Team!', kein 'Wir freuen uns!'.",
            "",
            "--- ANTI-PATTERNS --- GEHT GAR NICHT ---",
            "X 'Iteration 1 (22 Commits): X. Iteration 2 (17 Commits): Y.' - Kein Statistik-Listing.",
            "X 'Massives Update', 'umfangreich', 'spektakulaer' - Marketing-Adjektive ohne Zahl/Fakt.",
            "X 'Shadow hat drei Naechte gearbeitet' - NIE ERFINDEN. Nur wenn im DEV-KONTEXT steht.",
            "X 'Ein Mega-Update mit vielen neuen Features!' - generische Hype-Phrase.",
            "X Feature-Bullet-Liste ohne Spieler-Moment - immer WARUM + WAS BEDEUTET DAS.",
            "X Listen ohne narrativen Faden zwischen den Punkten.",
            "",
            "--- STRUKTUR DES web_content (Pflicht-Sektionen) ---",
            "",
            "1. **Hook (2-4 Saetze)** - direkte Anrede, Vorher-Nachher.",
            "   Muster: 'Bis letzte Woche fuehlte sich X noch an wie Y. Das aendert sich jetzt.'",
            "   KEIN 'Hallo Community!', KEIN 'Heute freuen wir uns!'.",
            "",
            "2. **Die Leitidee (2-3 Saetze)** - die EINE inhaltliche Klammer ueber dem Release.",
            "   Nicht 'viele neue Features', sondern ein Thema. Beispiel: 'BOS-Lifecycle end-to-end'.",
            "",
            "3. **Drei Momente (3x ~60 Worte)** - konkrete Szenen aus Nutzersicht.",
            "   Muster pro Moment: '**Der Moment wo [X passiert].** Vorher: [Y]. Jetzt: [Z].'",
            "   Konkret, greifbar, kein Abstract.",
            "",
            "4. **Was dahinter steckt (OPTIONAL)** - NUR wenn DEV-KONTEXT im Input steht.",
            "   Uebernimm die Dev-Notes aus DEV-KONTEXT WOERTLICH, eingebettet in 2-3 erklaerenden Saetzen.",
            "   Wenn kein DEV-KONTEXT da ist: Sektion WEGLASSEN. Nie erfinden.",
            "",
            "5. **Warum alles zusammen?** (2-3 Saetze, NUR bei MEGA)",
            "   Erklaere die Kopplung: Warum musste es ein grosser Release werden statt mehrerer kleiner?",
            "   Muster: 'X funktioniert nicht ohne Y, und Y braucht die Grundlage von Z. Deshalb zusammen.'",
            "",
            "6. **Demnaechst (1-2 Saetze, optional)** - Teaser wenn im DEV-KONTEXT steht.",
            "",
            "--- HARTE REGELN ---",
            "- Keine Aufzaehlung von PR-Nummern, Commit-Counts pro Iteration, '4 parallele Arbeitsstraenge'.",
            "- Keine Superlative ohne Quelle. Wenn Zahlen -> dann die harten aus dem Input-Block (commits, files, lines).",
            "- web_content ist KEINE Feature-Liste mit Bullets - es ist ein zusammenhaengender Text mit ##-Headings.",
            "- `tldr` bleibt 1-2 Saetze und landet als Lead im Discord-Embed.",
            "- `discord_highlights` sind 3-6 knackige Einzeiler (keine Floskeln, keine Vagheit).",
        ]

        if few_shot:
            parts.extend([
                "",
                "━━━ REFERENZ-BEISPIEL (wörtliches Muster für Tonalität) ━━━",
                few_shot,
            ])

        return "\n".join(parts)

    def _extra_context_section(self, ctx: PipelineContext) -> str:
        """Lade Release-Notes (Dev-Kontext) + Release-Guide + Context-Files."""
        project_path = ctx.project_config.get('path', '')
        if not project_path:
            return ""
        base = Path(project_path)
        if not base.exists():
            return ""

        sections: list[str] = []
        pc = ctx.project_config.get('patch_notes', {})

        # 0. Release-Notes (Dev-Kontext, release-spezifisch) — Priorität vor Feature-Guide
        #    HTML-Kommentare werden rausgefiltert, damit das Template-File nicht als Content gilt.
        notes_content = _read_release_notes(base)
        if notes_content:
            sections.append(
                "DEV-KONTEXT (vom Entwickler zwischen Commits geschrieben - WOERTLICH uebernehmen!):\n"
                "Binde diese Notizen in einen 'Was dahinter steckt'-Absatz ein, 2-3 Saetze\n"
                "erklaerender Rahmen, dann die Notizen. NICHT erfinden was nicht dasteht.\n\n"
                + notes_content
            )
            logger.info(f"[v6] Dev-Kontext aus release_notes.md geladen ({len(notes_content)} Zeichen)")

        # 1. Release-Guide (release_guide.md) — Feature-Dokumentation, langlebig
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
