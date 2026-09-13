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

# Lesbare Themen, wenn nach Typ statt nach Scope gebündelt wird.
TAG_TO_THEME = {
    'BREAKING': 'Grundlegende Änderungen',
    'FEATURE': 'Neue Funktionen',
    'BUGFIX': 'Fehlerbehebungen',
    'IMPROVEMENT': 'Verbesserungen',
    'INFRASTRUCTURE': 'Infrastruktur',
    'TEST': 'Tests',
    'DOCS': 'Dokumentation',
    'DESIGN_DOC': 'Planung & Entwurf',
    'DEPS': 'Abhängigkeiten',
    'REVERT': 'Zurückgenommene Änderungen',
    'OTHER': 'Sonstiges',
}

# Englische Commit-Titel ohne Conventional-Präfix. Kommen zustande, wenn ein
# Werkzeug (Cursor, Copilot) die Nachricht schreibt oder ein Beitragender die
# Konvention nicht kennt. Bei avunex-neustart betraf das 28 von 60 Commits,
# die dadurch sämtlich als OTHER galten.
# Bewusst konservativ: Was hier nicht steht, bleibt OTHER — eine falsche
# Einordnung wäre schlechter als eine neutrale.
_ENGLISCHE_VERBEN = {
    'FEATURE': (
        'add', 'added', 'adds', 'introduce', 'introduces', 'implement',
        'implements', 'create', 'creates', 'embed', 'embeds', 'enable',
        'enables', 'expand', 'expands', 'include', 'includes', 'support',
    ),
    'BUGFIX': (
        'fix', 'fixed', 'fixes', 'correct', 'corrects', 'resolve', 'resolves',
        'repair', 'repairs', 'prevent', 'prevents',
    ),
    'IMPROVEMENT': (
        'remove', 'removes', 'removed', 'delete', 'deletes', 'drop', 'drops',
        'disable', 'disables', 'update', 'updates', 'updated', 'improve',
        'improves', 'refine', 'refines', 'tighten', 'tightens', 'polish',
        'refactor', 'refactors', 'rename', 'renames', 'move', 'moves',
        'switch', 'switches', 'rebuild', 'rebuilds', 'reorient', 'reorients',
        'scale', 'scales', 'simplify', 'reduce', 'reduces', 'adjust',
        'adjusts', 'tune', 'restore', 'restores', 'keep', 'keeps', 'pull',
        'fill', 'give', 'present', 'replace', 'replaces',
    ),
    'DOCS': ('document', 'documents', 'documented',),
}
_VERB_ZU_TAG = {
    verb: tag for tag, verben in _ENGLISCHE_VERBEN.items() for verb in verben
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

    # `git revert` schreibt 'Revert "<urspruenglicher Titel>"' — ohne
    # Conventional-Präfix, obwohl die Absicht eindeutig ist.
    if msg.startswith('Revert "') or msg.startswith("Revert '"):
        return 'REVERT'

    m = _CONVENTIONAL_RE.match(msg)
    if not m:
        # Kein Conventional-Präfix: erstes Wort gegen die Verbliste prüfen.
        erstes = msg.strip().split(' ')[0].rstrip(':,.').lower()
        aus_verb = _VERB_ZU_TAG.get(erstes)
        if aus_verb:
            return aus_verb
        # Zuletzt eine KI-Einordnung, falls eine vorliegt (ki_einordnung.py).
        # Bewusst NACH allem Deterministischen: Präfix, Label und Verbliste
        # sind nachvollziehbar und kostenlos, die KI ist beides nicht.
        ki = commit.get('_ki_tag')
        if ki:
            return str(ki)
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
    if ctype == 'security':
        # Konsistent zu LABEL_TO_TAG, das 'security' bereits auf BUGFIX
        # abbildet — dort aber nur für PR-Labels, nicht für Präfixe.
        return 'BUGFIX'
    if ctype in ('refactor', 'perf', 'style', 'chore', 'build', 'ci'):
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

    # Commits ohne Scope nach ihrem Typ bündeln statt alle in einen Topf.
    # Gruppiert wird primär nach Scope ("fix(auth):"); Projekte, die keine
    # Scopes schreiben, hatten dadurch GENAU EINE Gruppe namens "Misc" — bei
    # avunex-neustart 60 Commits in einer. Den nachgelagerten Stufen fehlt
    # damit jede Gliederung: Aus den Gruppen entstehen die Hero-Kandidaten
    # und die Themenordnung des fertigen Textes.
    for c in commits:
        if c['_scope'] == '_misc':
            c['_scope'] = f"_{c['_tag'].lower()}"

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
            'theme': (
                TAG_TO_THEME.get(dominant, scope.lstrip('_').title())
                if scope.startswith('_')
                else SCOPE_TO_THEME.get(scope, scope.replace('_', ' ').title())
            ),
            'tag': dominant,
            'scope': scope,
            'commits': bucket,
            'summary': _build_summary(bucket),
            'is_player_facing': is_pf,
            'pr_labels': list(set(all_labels)),
        })

    groups.sort(key=lambda g: (not g['is_player_facing'], -len(g['commits'])))
    return groups
