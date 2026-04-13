"""Stufe 4: Validate — Safety-Checks + Content-Extraction."""

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from patch_notes.context import PipelineContext

logger = logging.getLogger('shadowops')

# Generisches SemVer-Pattern zum Entfernen von AI-erfundenen Versionen
_VERSION_RE = re.compile(
    r'(?:GuildScout|ZERODOX|ShadowOps|mayday_sim)?\s*v?\d+\.\d+\.\d+[:\s\u2014-]*',
    re.IGNORECASE,
)

# Deutsche Umlaut-Normalisierung
_UMLAUT_MAP = {
    'ae': 'ä', 'oe': 'ö', 'ue': 'ü',
    'Ae': 'Ä', 'Oe': 'Ö', 'Ue': 'Ü',
}
# Nur freistehende Doppelvokale ersetzen (nicht in Wörtern wie "blue", "queue")
_UMLAUT_RE = re.compile(r'\b(\w*?)(ae|oe|ue|Ae|Oe|Ue)(\w*)\b')


async def validate(ctx: 'PipelineContext', bot=None) -> None:
    """Stufe 4: Alle Safety-Checks + Content-Extraction."""
    if ctx.ai_result is None:
        ctx.warnings.append("Kein AI-Result vorhanden")
        return

    check_feature_count(ctx)
    check_design_doc_leaks(ctx)
    strip_ai_version(ctx)
    sanitize_content(ctx)
    normalize_umlauts(ctx)
    extract_display_content(ctx)
    enrich_changes_with_authors(ctx)


def check_feature_count(ctx: 'PipelineContext') -> None:
    """Check 1: AI darf nicht mehr Features nennen als echte feat-Gruppen x 2."""
    if not isinstance(ctx.ai_result, dict):
        return
    feature_groups = [g for g in ctx.groups if g.get('tag') == 'FEATURE']
    ai_features = [
        ch for ch in ctx.ai_result.get('changes', [])
        if isinstance(ch, dict) and ch.get('type') == 'feature'
    ]
    max_allowed = max(len(feature_groups) * 2, len(feature_groups) + 3)
    if len(ai_features) > max_allowed:
        ctx.warnings.append(
            f"AI nennt {len(ai_features)} Features, aber nur {len(feature_groups)} Feature-Gruppen"
        )


def check_design_doc_leaks(ctx: 'PipelineContext') -> None:
    """Check 2: Design-Doc Keywords duerfen nicht in AI-Features auftauchen."""
    if not isinstance(ctx.ai_result, dict):
        return
    design_groups = [g for g in ctx.groups if g.get('tag') == 'DESIGN_DOC']
    if not design_groups:
        return

    # Keywords aus Design-Doc-Gruppen sammeln
    design_keywords = set()
    for g in design_groups:
        for word in g.get('theme', '').lower().split():
            if len(word) > 4:
                design_keywords.add(word)

    # Keywords die AUCH in Feature-Gruppen vorkommen -> False-Positive-Schutz
    feature_text = ' '.join(
        g.get('theme', '').lower() + ' ' + g.get('summary', '').lower()
        for g in ctx.groups if g.get('tag') == 'FEATURE'
    )
    safe_keywords = {kw for kw in design_keywords if kw in feature_text}
    suspect_keywords = design_keywords - safe_keywords

    if not suspect_keywords:
        return

    # AI-Changes auf verdaechtige Keywords pruefen
    changes = ctx.ai_result.get('changes', [])
    for i, change in enumerate(changes):
        if not isinstance(change, dict) or change.get('type') != 'feature':
            continue
        desc = change.get('description', '').lower()
        for kw in suspect_keywords:
            if kw in desc:
                ctx.fixes_applied.append(
                    f"Design-Doc-Leak entfernt: '{kw}' in Feature '{desc[:50]}...'"
                )
                changes[i] = None
                break
    ctx.ai_result['changes'] = [c for c in changes if c is not None]


def strip_ai_version(ctx: 'PipelineContext') -> None:
    """Check 3: Entferne JEDE SemVer-Version aus AI-generiertem Titel."""
    if isinstance(ctx.ai_result, dict):
        title = ctx.ai_result.get('title', '')
        title = _VERSION_RE.sub('', title).strip(' :\u2014-')
        if not title:
            title = 'Update'
        ctx.ai_result['title'] = title


def sanitize_content(ctx: 'PipelineContext') -> None:
    """Check 4: Sensible Informationen entfernen (Pfade, IPs, Secrets)."""
    try:
        from integrations.content_sanitizer import ContentSanitizer
        sanitizer = ContentSanitizer(enabled=True)

        if isinstance(ctx.ai_result, dict):
            ctx.ai_result = sanitizer.sanitize_dict(ctx.ai_result)
            for change in ctx.ai_result.get('changes', []):
                if isinstance(change, dict):
                    if 'description' in change:
                        change['description'] = sanitizer.sanitize(change['description'])
                    if 'details' in change and isinstance(change['details'], list):
                        change['details'] = [
                            sanitizer.sanitize(d) if isinstance(d, str) else d
                            for d in change['details']
                        ]
        elif isinstance(ctx.ai_result, str):
            ctx.ai_result = sanitizer.sanitize(ctx.ai_result)
    except ImportError:
        logger.debug("ContentSanitizer nicht verfuegbar — Sanitization uebersprungen")


def normalize_umlauts(ctx: 'PipelineContext') -> None:
    """Check 5: ae->ae, oe->oe, ue->ue (nur bei deutschen Patch Notes)."""
    lang = ctx.project_config.get('patch_notes', {}).get('language', 'de')
    if lang != 'de':
        return

    def _replace_umlauts(text: str) -> str:
        if not text:
            return text
        for old, new in [('ae', '\u00e4'), ('oe', '\u00f6'), ('ue', '\u00fc'),
                          ('Ae', '\u00c4'), ('Oe', '\u00d6'), ('Ue', '\u00dc')]:
            # Nur am Wortanfang oder nach Konsonanten (vermeidet "blue" -> "bl\u00fc")
            text = re.sub(rf'(?<=[bcdfghjklmnpqrstvwxyzBCDFGHJKLMNPQRSTVWXYZ]){re.escape(old)}\b', new, text)
            text = re.sub(rf'\b{re.escape(old)}', new, text)
        return text

    if isinstance(ctx.ai_result, dict):
        for key in ('title', 'tldr', 'web_content', 'summary'):
            if key in ctx.ai_result and isinstance(ctx.ai_result[key], str):
                ctx.ai_result[key] = _replace_umlauts(ctx.ai_result[key])
    elif isinstance(ctx.ai_result, str):
        ctx.ai_result = _replace_umlauts(ctx.ai_result)


def extract_display_content(ctx: 'PipelineContext') -> None:
    """Extrahiere Titel, TL;DR, Web-Content aus AI-Result."""
    if isinstance(ctx.ai_result, dict):
        ctx.title = ctx.ai_result.get('title', f'{ctx.project} Update')
        ctx.tldr = ctx.ai_result.get('tldr', '')
        ctx.web_content = ctx.ai_result.get('web_content', ctx.ai_result.get('summary', ''))
        ctx.changes = ctx.ai_result.get('changes', [])
        ctx.seo_keywords = ctx.ai_result.get('seo_keywords', [])
    elif isinstance(ctx.ai_result, str) and ctx.ai_result.strip():
        ctx.title = f'{ctx.project} Update'
        ctx.web_content = ctx.ai_result
        ctx.tldr = ctx.ai_result[:200]
    else:
        ctx.title = f'{ctx.project} Update'


def enrich_changes_with_authors(ctx: 'PipelineContext') -> None:
    """Post-Processing: Matche AI-Changes gegen Git-Commits per Keyword-Overlap → author-Feld."""
    if not ctx.changes or not ctx.enriched_commits:
        return

    from patch_notes.stages.classify import TEAM_MAPPING, _AI_AUTHORS

    # Commit-Index: Keywords → Autor
    commit_authors: list[tuple[set[str], str]] = []
    for c in ctx.enriched_commits:
        author = c.get('author', {})
        if isinstance(author, dict):
            name = author.get('name', author.get('username', ''))
        elif isinstance(author, str):
            name = author
        else:
            continue
        if not name or name.lower().strip() in _AI_AUTHORS:
            continue
        msg = c.get('message', '').split('\n')[0].lower()
        keywords = {w for w in re.findall(r'[a-zäöü]{4,}', msg) if w not in (
            'feat', 'fix', 'chore', 'docs', 'refactor', 'perf', 'test', 'build',
            'style', 'update', 'implement', 'added', 'removed', 'fixed',
        )}
        if keywords:
            commit_authors.append((keywords, name))

    # Changes matchen
    for change in ctx.changes:
        if not isinstance(change, dict) or change.get('author'):
            continue
        desc = change.get('description', '').lower()
        details_text = ' '.join(change.get('details', [])).lower()
        change_text = desc + ' ' + details_text
        change_words = set(re.findall(r'[a-zäöü]{4,}', change_text))

        best_match = ''
        best_overlap = 0
        for keywords, author_name in commit_authors:
            overlap = len(keywords & change_words)
            if overlap > best_overlap:
                best_overlap = overlap
                best_match = author_name

        if best_match and best_overlap >= 2:
            team = TEAM_MAPPING.get(best_match.lower().strip())
            change['author'] = team[0] if team else best_match
