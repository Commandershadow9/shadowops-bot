"""Editorial-Layer fuer Patch Notes v7.

Leitet aus deterministischen Pipeline-Daten einen Redaktionsbrief ab:
Hero-Kandidaten, Kanalplan und konkrete Before/After-Fragen. Die AI bekommt
dadurch eine klare Rangfolge, ohne dass wir Fakten erfinden muessen.
"""

from __future__ import annotations

import re
from typing import Any


_TYPE_BY_TAG = {
    'FEATURE': 'feature',
    'BUGFIX': 'fix',
    'BREAKING': 'breaking',
    'IMPROVEMENT': 'improvement',
    'INFRASTRUCTURE': 'infrastructure',
    'DOCS': 'docs',
    'DESIGN_DOC': 'docs',
    'DEPS': 'infrastructure',
    'REVERT': 'fix',
    'TEST': 'improvement',
    'OTHER': 'improvement',
}

_STOPWORDS = {
    'feat', 'fix', 'docs', 'chore', 'test', 'build', 'style', 'refactor',
    'perf', 'update', 'add', 'added', 'adds', 'remove', 'removed', 'change',
    'changes', 'implement', 'implemented', 'improve', 'improved', 'initial',
}


def build_editorial_context(ctx: Any) -> dict:
    """Baue einen redaktionellen Kontext fuer Prompt, Discord und Web.

    Das Ergebnis ist rein aus Gruppen, Commit-Nachrichten und Git-Stats abgeleitet.
    Es ist bewusst konservativ formuliert: harte Fakten bleiben die Commit-Titel,
    die Briefing-Texte sind Leitfragen fuer die AI.
    """
    groups = [g for g in (ctx.groups or []) if isinstance(g, dict)]
    project_type = ctx.project_config.get('patch_notes', {}).get('type', 'devops')
    language = ctx.project_config.get('patch_notes', {}).get('language', 'de')

    candidates = [_build_candidate(g, project_type, language) for g in groups]
    candidates = [c for c in candidates if c]
    candidates.sort(key=lambda c: c['priority_score'], reverse=True)

    hero_limit = 4 if ctx.update_size in ('major', 'mega') else 3
    hero_candidates = candidates[:hero_limit]
    supporting = candidates[hero_limit:hero_limit + 8]

    must_call_out = [
        c for c in candidates
        if c.get('type') in ('breaking', 'security')
        or c.get('source_tag') in ('BREAKING',)
    ][:5]

    return {
        'version': 7,
        'release_angle': _release_angle(hero_candidates, project_type, language),
        'hero_candidates': hero_candidates,
        'supporting_changes': supporting,
        'must_call_out': must_call_out,
        'channel_plan': _channel_plan(project_type, ctx.update_size, language),
        'quality_bar': _quality_bar(language),
    }


def _build_candidate(group: dict, project_type: str, language: str) -> dict:
    commits = [c for c in group.get('commits', []) if isinstance(c, dict)]
    if not commits:
        return {}

    tag = group.get('tag', 'OTHER')
    theme = (group.get('theme') or group.get('scope') or 'Update').strip()
    scope = group.get('scope') or ''
    is_player = bool(group.get('is_player_facing'))
    ctype = _change_type(tag, scope, project_type, is_player)
    messages = [_clean_commit_title(c.get('message', '')) for c in commits]
    messages = [m for m in messages if m][:6]
    keywords = _keywords(' '.join(messages + [theme]))

    return {
        'theme': theme,
        'type': ctype,
        'source_tag': tag,
        'audience': _audience(project_type, is_player),
        'priority_score': _priority_score(group, ctype, is_player),
        'commit_count': len(commits),
        'source_commits': messages,
        'keywords': keywords[:8],
        'editor_questions': _editor_questions(theme, ctype, project_type, language),
        'suggested_change_fields': _suggested_fields(theme, ctype, project_type, language),
    }


def _change_type(tag: str, scope: str, project_type: str, is_player: bool) -> str:
    if project_type == 'gaming' and is_player and tag == 'FEATURE':
        if scope in ('ui', 'play', 'gameplay', 'einsatz', 'fahrzeug', 'leitstelle'):
            return 'gameplay'
        return 'content'
    return _TYPE_BY_TAG.get(tag, 'improvement')


def _priority_score(group: dict, ctype: str, is_player: bool) -> int:
    commits = len(group.get('commits') or [])
    score = commits
    if ctype in ('feature', 'content', 'gameplay', 'breaking'):
        score += 20
    if ctype in ('security', 'fix'):
        score += 10
    if is_player:
        score += 15
    if group.get('pr_labels'):
        score += 3
    return score


def _audience(project_type: str, is_player: bool) -> str:
    if project_type == 'gaming':
        return 'players' if is_player else 'developers/operators'
    if project_type == 'saas':
        return 'end users' if is_player else 'admins/operators'
    return 'operators/developers'


def _editor_questions(theme: str, ctype: str, project_type: str, language: str) -> list[str]:
    if language == 'en':
        return [
            f"What was annoying, slow, risky, or unclear around {theme} before this release?",
            f"What can the user do faster, safer, or with less guesswork now?",
            "Is there an action required, migration, limitation, or known issue?",
        ]
    noun = 'Spieler' if project_type == 'gaming' else 'Nutzer'
    if ctype in ('infrastructure', 'security'):
        noun = 'Ops/Team'
    return [
        f"Was war rund um {theme} vorher langsam, riskant, unklar oder umstaendlich?",
        f"Was kann {noun} jetzt konkreter, schneller oder sicherer tun?",
        "Gibt es eine notwendige Aktion, Migration, Einschraenkung oder Known Issue?",
    ]


def _suggested_fields(theme: str, ctype: str, project_type: str, language: str) -> dict:
    if language == 'en':
        return {
            'title': theme,
            'impact': 'Explain the concrete user or operator outcome, not the implementation.',
            'before': 'Describe the previous friction only if supported by commits or dev context.',
            'after': 'Describe the new workflow or behavior in one concrete sentence.',
            'why': 'Explain why this belongs in the release.',
            'user_action': 'Use "None" unless migration, re-login, config, or manual action is required.',
        }

    actor = 'Spieler' if project_type == 'gaming' else 'Nutzer'
    if ctype in ('infrastructure', 'security'):
        actor = 'Ops'
    return {
        'title': theme,
        'impact': f'Erklaere den konkreten Nutzen fuer {actor}, nicht die Implementierung.',
        'before': 'Beschreibe die vorherige Reibung nur, wenn Commits oder Dev-Kontext sie belegen.',
        'after': 'Beschreibe den neuen Ablauf oder das neue Verhalten in einem konkreten Satz.',
        'why': 'Erklaere, warum diese Aenderung Teil dieses Releases ist.',
        'user_action': 'Nutze "Keine", ausser Migration, Re-Login, Config oder manuelle Aktion ist noetig.',
    }


def _release_angle(hero_candidates: list[dict], project_type: str, language: str) -> str:
    themes = [c.get('theme', '') for c in hero_candidates[:3] if c.get('theme')]
    if not themes:
        return 'Maintenance-focused release.' if language == 'en' else 'Wartungsorientiertes Release.'
    joined = ', '.join(themes)
    if language == 'en':
        if project_type == 'gaming':
            return f"Frame the release around the player moments unlocked by: {joined}."
        if project_type == 'saas':
            return f"Frame the release around workflow value and reduced manual effort: {joined}."
        return f"Frame the release around operational reliability and clearer control: {joined}."
    if project_type == 'gaming':
        return f"Rahme das Release um die Spieler-Momente, die durch {joined} entstehen."
    if project_type == 'saas':
        return f"Rahme das Release um Workflow-Nutzen und weniger manuelle Arbeit: {joined}."
    return f"Rahme das Release um Betriebssicherheit, Kontrolle und klarere Ablaeufe: {joined}."


def _channel_plan(project_type: str, update_size: str, language: str) -> dict:
    if language == 'en':
        return {
            'discord': 'Lead with TL;DR and the top 3-6 outcomes. No implementation detail unless it changes user action.',
            'web': 'Use a narrative intro, then hero changes, then grouped details, fixes, breaking changes, and known issues.',
            'ops': 'Call out migrations, config changes, downtime, security-sensitive notes, and test/verification status.',
        }
    detail = 'Spieler-Momente' if project_type == 'gaming' else 'Nutzerwert'
    return {
        'discord': f'TL;DR plus 3-6 wichtigste Ergebnisse. Fokus auf {detail}, keine Commitliste.',
        'web': 'Narrativer Einstieg, dann Hero-Changes, Detailgruppen, Fixes, Breaking Changes und Known Issues.',
        'ops': 'Migrationen, Config-Aenderungen, Downtime, Security-Hinweise und Teststatus klar nennen.',
        'size': update_size,
    }


def _quality_bar(language: str) -> list[str]:
    if language == 'en':
        return [
            'Every hero change has title, impact, before, after, why, and user_action.',
            'Do not use vague claims like improved UX/performance without a concrete example or metric.',
            'Discord highlights must be a subset of the web hero changes.',
            'No PR number listing, no commit-count listing as the main story.',
        ]
    return [
        'Jeder Hero-Change hat title, impact, before, after, why und user_action.',
        'Keine vagen Aussagen wie bessere UX/Performance ohne Beispiel oder Zahl.',
        'Discord-Highlights sind Kurzfassungen der Web-Hero-Changes, keine anderen Themen.',
        'Keine PR- oder Commit-Listen als Hauptstory.',
    ]


def _clean_commit_title(message: str) -> str:
    first = (message or '').split('\n', 1)[0].strip()
    m = re.match(r'^\w+(?:\([^)]+\))?!?:\s*(.+)$', first)
    return (m.group(1).strip() if m else first)[:140]


def _keywords(text: str) -> list[str]:
    words = []
    for raw in re.findall(r'[A-Za-zÄÖÜäöüß0-9_-]{4,}', text.lower()):
        word = raw.strip('_-')
        if word and word not in _STOPWORDS and word not in words:
            words.append(word)
    return words
