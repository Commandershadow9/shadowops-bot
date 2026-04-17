"""Deterministische Commit-Gruppierung — ALLE Commits, kein Cap."""
import re
import logging
from collections import defaultdict

logger = logging.getLogger('shadowops')

LABEL_TO_TAG = {
    'feature': 'FEATURE', 'bugfix': 'BUGFIX', 'security': 'BUGFIX',
    'performance': 'IMPROVEMENT', 'infrastructure': 'INFRASTRUCTURE',
    'content': 'FEATURE', 'design-doc': 'DESIGN_DOC', 'breaking': 'BREAKING',
    'dependencies': 'DEPS', 'seo': 'IMPROVEMENT', 'gameplay': 'FEATURE',
    'ui': 'FEATURE',
}

PLAYER_FACING_SCOPES = {
    'auth', 'play', 'ui', 'hooks', 'content', 'generator', 'voice',
    'gameplay', 'shop', 'lobby', 'notruf', 'einsatz', 'fahrzeug',
    'wache', 'leitstelle', 'szenario', 'admin', 'cosmetics',
}

SCOPE_TO_THEME = {
    'auth': 'Berechtigungen & Rollen', 'play': 'Gameplay',
    'ui': 'Benutzeroberfläche', 'hooks': 'Frontend-Logik',
    'events': 'Event-System', 'cqrs': 'Daten-Architektur',
    'resilience': 'Stabilität & Ausfallsicherheit',
    'observability': 'Monitoring & Metriken',
    'docker': 'Infrastruktur', 'ci': 'Build & Deploy',
    'db': 'Datenbank', 'content': 'Inhalte',
    'generator': 'Content-Generierung', 'voice': 'Sprachausgabe',
    'infra': 'Infrastruktur', 'security': 'Sicherheit',
    'migration': 'Daten-Migration', 'projections': 'Daten-Projektion',
}

_DESIGN_DOC_PATTERNS = re.compile(
    r'design.doc|implementierungsplan|architecture.*design|design.*architecture',
    re.IGNORECASE,
)

_CONVENTIONAL_RE = re.compile(
    r'^(?P<type>\w+)(?:\((?P<scope>[^)]+)\))?(?P<breaking>!)?:\s*(?P<desc>.+)'
)


def classify_commit(commit: dict) -> str:
    """Klassifiziere einen Commit. PR-Labels haben Vorrang."""
    labels = commit.get('pr_labels', [])
    for label in labels:
        tag = LABEL_TO_TAG.get(label.lower())
        if tag:
            return tag

    msg = commit.get('message', '').split('\n')[0]
    m = _CONVENTIONAL_RE.match(msg)
    if not m:
        return 'OTHER'

    ctype = m.group('type').lower()
    is_breaking = bool(m.group('breaking'))

    if is_breaking:
        return 'BREAKING'
    if ctype == 'feat':
        return 'FEATURE'
    if ctype == 'fix':
        return 'BUGFIX'
    if ctype == 'docs':
        return 'DESIGN_DOC' if _DESIGN_DOC_PATTERNS.search(msg) else 'DOCS'
    if ctype in ('refactor', 'perf', 'style', 'chore', 'build'):
        return 'IMPROVEMENT'
    if ctype == 'test':
        return 'TEST'
    if ctype == 'revert':
        return 'REVERT'
    return 'OTHER'


def _extract_scope(commit: dict) -> str:
    msg = commit.get('message', '').split('\n')[0]
    m = _CONVENTIONAL_RE.match(msg)
    if m and m.group('scope'):
        return m.group('scope').lower()
    return '_misc'


def _build_summary(commits: list[dict]) -> str:
    titles = []
    for c in commits[:5]:
        msg = c.get('message', '').split('\n')[0]
        m = _CONVENTIONAL_RE.match(msg)
        desc = m.group('desc').strip() if m else msg
        titles.append(desc)
    summary = '; '.join(titles)
    if len(commits) > 5:
        summary += f' (+{len(commits) - 5} weitere)'
    return summary


def group_commits(commits: list[dict]) -> list[dict]:
    """Gruppiere ALLE Commits nach Scope. Kein Cap."""
    for c in commits:
        c['_tag'] = classify_commit(c)
        c['_scope'] = _extract_scope(c)

    scope_buckets: dict[str, list[dict]] = defaultdict(list)
    for c in commits:
        scope_buckets[c['_scope']].append(c)

    tag_priority = ['BREAKING', 'FEATURE', 'BUGFIX', 'IMPROVEMENT',
                    'INFRASTRUCTURE', 'TEST', 'DOCS', 'DESIGN_DOC', 'DEPS',
                    'REVERT', 'OTHER']

    groups = []
    for scope, bucket in scope_buckets.items():
        tags = [c['_tag'] for c in bucket]
        dominant = max(set(tags), key=lambda t: (tags.count(t), -tag_priority.index(t) if t in tag_priority else -99))
        is_pf = scope in PLAYER_FACING_SCOPES
        all_labels = []
        for c in bucket:
            all_labels.extend(c.get('pr_labels', []))

        groups.append({
            'theme': SCOPE_TO_THEME.get(scope, scope.replace('_', ' ').title()),
            'tag': dominant,
            'scope': scope,
            'commits': bucket,
            'summary': _build_summary(bucket),
            'is_player_facing': is_pf,
            'pr_labels': list(set(all_labels)),
        })

    groups.sort(key=lambda g: (not g['is_player_facing'], -len(g['commits'])))
    return groups
