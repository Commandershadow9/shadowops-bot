"""
AI patch notes generation methods for GitHubIntegration.
"""

import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Dict, Optional

from integrations.git_history_analyzer import GitHistoryAnalyzer

logger = logging.getLogger('shadowops')


class AIPatchNotesMixin:

    # =============================================
    # Team-Mapping: Git-Autor → Display-Name + Rolle
    # =============================================
    # Erweiterbar für neue Teammitglieder (Game Designer etc.)
    # Key: Git-Autorname (case-insensitive Match)
    # Value: (Display-Name, Rolle)

    TEAM_MAPPING: Dict[str, tuple[str, str]] = {
        # MayDay Sim Core Team
        'commandershadow9': ('Shadow', 'Founder & Lead Dev'),
        'cmdshadow': ('Shadow', 'Founder & Lead Dev'),
        'shadow': ('Shadow', 'Founder & Lead Dev'),
        'renjihoshida': ('Mapu', 'Co-Founder & Dev'),
        'mapu': ('Mapu', 'Co-Founder & Dev'),
        # GuildScout / ZERODOX
        'commandershadow': ('Shadow', 'Founder & Lead Dev'),
    }

    # Git-Autoren die NICHT als eigenständige Credits erscheinen sollen
    # (Co-Authored-By wird bereits in _build_classified_commits_text gefiltert)
    AI_AUTHOR_NAMES: set[str] = {
        'claude', 'claude opus', 'claude sonnet', 'claude haiku',
        'github-actions', 'github-actions[bot]', 'dependabot', 'dependabot[bot]',
        'noreply', 'copilot',
    }

    def _resolve_team_member(self, git_author: str) -> tuple[str, str] | None:
        """
        Löse einen Git-Autornamen zu Team-Member auf.

        Returns:
            (display_name, rolle) oder None wenn unbekannt/AI
        """
        name_lower = git_author.lower().strip()

        # AI-Autoren rausfiltern
        if name_lower in self.AI_AUTHOR_NAMES:
            return None

        # Exakter Match im Team-Mapping
        if name_lower in self.TEAM_MAPPING:
            return self.TEAM_MAPPING[name_lower]

        # Partial Match (z.B. "Commandershadow9 via GitHub")
        for key, value in self.TEAM_MAPPING.items():
            if key in name_lower or name_lower in key:
                return value

        # Unbekannter Autor → Display-Name = Git-Name, Rolle = "Contributor"
        return (git_author, 'Contributor')

    def _build_team_credits(self, commits: list) -> Dict[str, Dict]:
        """
        Gruppiere Commits nach Team-Mitglied für Credits-Sektion.

        Returns:
            {
                "Shadow": {"rolle": "Backend", "commits": 12, "features": ["feat: ...", ...]},
                "Mapu": {"rolle": "Frontend", "commits": 5, "features": [...]},
                "__autonomous__": {"commits": 3, "types": ["SEO-AUTO", "DEPS-AUTO"]},
            }
        """
        credits: Dict[str, Dict] = {}
        autonomous_types: set[str] = set()
        autonomous_count = 0

        for commit in commits:
            author_name = commit.get('author', {}).get('name') or \
                          commit.get('author', {}).get('username', 'Unknown')

            tag, auto_group = self._classify_commit(commit)

            # Merge-Commits überspringen
            if tag == 'MERGE':
                continue

            # Autonome Commits separat sammeln
            if auto_group:
                autonomous_count += 1
                autonomous_types.add(auto_group)
                continue

            # Team-Member auflösen
            member = self._resolve_team_member(author_name)
            if member is None:
                continue  # AI-Autor, wird dem Committer zugeordnet

            display_name, rolle = member

            if display_name not in credits:
                credits[display_name] = {
                    'rolle': rolle,
                    'commits': 0,
                    'features': [],
                }

            credits[display_name]['commits'] += 1

            # Feature-Titel sammeln (nur FEATURE/BUGFIX/IMPROVEMENT, max 5)
            title = commit.get('message', '').split('\n')[0]
            if tag in ('FEATURE', 'BUGFIX', 'IMPROVEMENT', 'PERFORMANCE', 'SECURITY'):
                if len(credits[display_name]['features']) < 5:
                    credits[display_name]['features'].append(title)

        # Autonome Agent-Arbeit
        if autonomous_count > 0:
            credits['__autonomous__'] = {
                'commits': autonomous_count,
                'types': sorted(autonomous_types),
            }

        return credits

    def _format_credits_section(self, credits: Dict[str, Dict], language: str = 'de') -> str:
        """Formatiere Credits als Author-Mapping-Hilfe für den AI-Prompt.

        Gibt der AI eine Zuordnung: Git-Autor → Display-Name,
        damit sie das 'author' Feld pro Change korrekt füllen kann.
        """
        if not credits:
            return ""

        if language == 'de':
            section = "# AUTOR-MAPPING (für das 'author' Feld pro Change)\n"
            section += "# Nutze den Display-Namen (NICHT den Git-Usernamen) im author-Feld:\n"
        else:
            section = "# AUTHOR MAPPING (for the 'author' field per change)\n"
            section += "# Use the display name (NOT the Git username) in the author field:\n"

        for name, info in credits.items():
            if name == '__autonomous__':
                continue  # AI-Agents bekommen keinen Author-Eintrag
            section += f"# → Commits von '{name}' → author: \"{name}\"\n"

        return section

    def _collect_git_stats(self, commits: list, project_path: Optional[Path]) -> Dict:
        """
        Sammle Git-Statistiken aus Commits und Repository.

        Returns:
            Dict mit commits, files_changed, lines_added, lines_removed, contributors
        """
        stats = {
            'commits': len(commits),
            'files_changed': 0,
            'lines_added': 0,
            'lines_removed': 0,
            'contributors': [],
            'tests_passed': None,
            'tests_total': None,
            'coverage_percent': None,
        }

        # Contributors aus Commits — nur Display-Namen (Inline-Credits sind jetzt pro Change)
        contributor_names: set[str] = set()
        for commit in commits:
            author = commit.get('author', {})
            name = author.get('name') or author.get('username', '')
            if not name:
                continue
            member = self._resolve_team_member(name)
            if member is None:
                continue  # AI-Autor
            display_name, _ = member
            contributor_names.add(display_name)

        stats['contributors'] = sorted(contributor_names)

        # Git diff stats berechnen
        if project_path and project_path.exists():
            try:
                # Ältesten und neuesten Commit-SHA finden
                shas = []
                for commit in commits:
                    sha = commit.get('id') or commit.get('sha') or commit.get('hash')
                    if sha:
                        shas.append(sha)

                if len(shas) >= 2:
                    diff_range = f"{shas[0]}^..{shas[-1]}"
                elif len(shas) == 1:
                    diff_range = f"{shas[0]}^..{shas[0]}"
                else:
                    diff_range = None

                if diff_range:
                    result = subprocess.run(
                        ['git', 'diff', '--shortstat', diff_range],
                        cwd=str(project_path),
                        capture_output=True,
                        text=True,
                        timeout=10
                    )
                    if result.returncode == 0 and result.stdout.strip():
                        output = result.stdout.strip()
                        # Parse: "23 files changed, 847 insertions(+), 203 deletions(-)"
                        files_match = re.search(r'(\d+) files? changed', output)
                        ins_match = re.search(r'(\d+) insertions?\(\+\)', output)
                        del_match = re.search(r'(\d+) deletions?\(-\)', output)

                        if files_match:
                            stats['files_changed'] = int(files_match.group(1))
                        if ins_match:
                            stats['lines_added'] = int(ins_match.group(1))
                        if del_match:
                            stats['lines_removed'] = int(del_match.group(1))
            except Exception as e:
                logger.debug(f"Git stats collection failed: {e}")

        # Test-Ergebnisse laden (falls vorhanden)
        test_results_path = Path(__file__).parent.parent.parent / 'data' / 'test_results.json'
        if test_results_path.exists():
            try:
                with open(test_results_path, 'r', encoding='utf-8') as f:
                    test_data = json.load(f)
                if test_data.get('status') in ('passed', 'failed'):
                    stats['tests_passed'] = test_data.get('tests_passed', 0)
                    stats['tests_total'] = test_data.get('tests_total', 0)
                    coverage = test_data.get('coverage_percent')
                    if coverage is not None and coverage != 'null':
                        stats['coverage_percent'] = float(coverage)
            except Exception as e:
                logger.debug(f"Test results loading failed: {e}")

        return stats

    def _format_stats_line(self, stats: Dict, language: str = 'de') -> str:
        """Formatiere Stats als einzeilige Zusammenfassung für Discord."""
        parts = []
        commits = stats.get('commits', 0)
        files = stats.get('files_changed', 0)
        added = stats.get('lines_added', 0)

        if commits > 0:
            parts.append(f"{commits} Commits")
        if files > 0:
            parts.append(f"{files} Dateien" if language == 'de' else f"{files} files")
        if added > 0:
            removed = stats.get('lines_removed', 0)
            parts.append(f"+{added}/-{removed} Zeilen" if language == 'de' else f"+{added}/-{removed} lines")

        tests_total = stats.get('tests_total')
        tests_passed = stats.get('tests_passed')
        if tests_total is not None and tests_total > 0:
            parts.append(f"{tests_passed}/{tests_total} Tests ✅")

        coverage = stats.get('coverage_percent')
        if coverage is not None:
            parts.append(f"{coverage:.0f}% Coverage")

        if not parts:
            return ""

        return "📊 " + " · ".join(parts)

    def _format_stats_section(self, stats: Dict, language: str = 'de') -> str:
        """Formatiere Stats als Kontext-Sektion für den AI-Prompt."""
        if stats.get('commits', 0) < 5:
            return ""

        if language == 'de':
            section = "# RELEASE-STATISTIKEN\n"
        else:
            section = "# RELEASE STATS\n"

        section += f"- {stats.get('commits', 0)} Commits\n"
        section += f"- {stats.get('files_changed', 0)} Dateien geändert\n"
        section += f"- +{stats.get('lines_added', 0)} / -{stats.get('lines_removed', 0)} Zeilen\n"

        contributors = stats.get('contributors', [])
        if contributors:
            section += f"- Contributors: {', '.join(contributors)}\n"

        return section

    def _split_embed_description(self, description: str, max_length: int = 4096) -> list[str]:
        """
        Split a long description into multiple chunks that fit Discord's limits.

        Args:
            description: The full description text
            max_length: Maximum length per chunk (Discord limit: 4096)

        Returns:
            List of description chunks
        """
        if len(description) <= max_length:
            return [description]

        chunks = []
        current_chunk = ""

        # Split by paragraphs first (double newline)
        paragraphs = description.split('\n\n')

        for paragraph in paragraphs:
            # If adding this paragraph would exceed limit, save current chunk
            if len(current_chunk) + len(paragraph) + 2 > max_length:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                    current_chunk = ""

                # If single paragraph is too long, split by lines
                if len(paragraph) > max_length:
                    lines = paragraph.split('\n')
                    for line in lines:
                        if len(current_chunk) + len(line) + 1 > max_length:
                            if current_chunk:
                                chunks.append(current_chunk.strip())
                            current_chunk = line + "\n"
                        else:
                            current_chunk += line + "\n"
                else:
                    current_chunk = paragraph + "\n\n"
            else:
                current_chunk += paragraph + "\n\n"

        # Add remaining chunk
        if current_chunk.strip():
            chunks.append(current_chunk.strip())

        return chunks

    # Conventional-Commit-Prefix → Tag-Mapping
    _COMMIT_TYPE_TAGS = {
        'feat': 'FEATURE',
        'fix': 'BUGFIX',
        'docs': 'DOCS',
        'doc': 'DOCS',
        'chore': 'CHORE',
        'refactor': 'REFACTOR',
        'test': 'TEST',
        'perf': 'PERFORMANCE',
        'ci': 'CI',
        'build': 'BUILD',
        'style': 'STYLE',
        'improve': 'IMPROVEMENT',
        'update': 'UPDATE',
        'revert': 'REVERT',
    }

    # Muster die auf Design-Docs / Planungs-Dokumente hindeuten
    _DESIGN_DOC_PATTERNS = [
        r'docs?[:/]\s*.*(?:design|plan|spec|rfc|adr|proposal|konzept)',
        r'docs?[:/]\s*.*(?:Design-Doc|Implementierungsplan|Spezifikation)',
        r'(?:design|plan|spec|rfc|proposal)[\s-](?:doc|document|dokument)',
    ]

    # Muster fuer automatisierte Commits (werden gruppiert)
    _AUTO_COMMIT_PATTERNS = {
        'SEO-AUTO': [
            r'^SEO:\s*Automatische\s+Optimierungen',
            r'^fix\(seo\):\s*\d+\s+SEO-Verbesserungen',
        ],
        'DEPS-AUTO': [
            r'^chore\(deps\):',
            r'^build\(deps\):',
            r'^fix\(deps\):',
            r'^\[?dependabot\]?',
            r'^\[?renovate\]?',
        ],
    }

    def _classify_commit(self, commit: dict) -> tuple:
        """
        Klassifiziere einen Commit anhand von Prefix, Pfaden und Mustern.

        Returns:
            (tag: str, auto_group: str|None)
            auto_group ist z.B. 'SEO-AUTO' oder 'DEPS-AUTO' fuer gruppierbare Commits,
            None fuer normale Commits.
        """
        full_msg = commit.get('message', '')
        title = full_msg.split('\n')[0]

        # 1. Merge-Commits erkennen (branch + PR)
        if title.startswith('Merge branch') or title.startswith('Merge pull request'):
            return ('MERGE', None)

        # 2. Revert-Commits erkennen
        if title.startswith('Revert "') or title.startswith('Revert \''):
            return ('REVERT', None)

        # 3. Design-Doc erkennen (hoechste Prioritaet bei docs)
        for pattern in self._DESIGN_DOC_PATTERNS:
            if re.search(pattern, title, re.IGNORECASE):
                return ('DESIGN-DOC', None)

        # 4. Automatisierte Commits erkennen (SEO, Dependabot, Renovate)
        for group_name, patterns in self._AUTO_COMMIT_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, title, re.IGNORECASE):
                    return (group_name, group_name)

        # 5. PR-Label hat Vorrang vor Commit-Prefix (zuverlaessiger)
        pr_label_tag = commit.get('pr_label_tag')
        if pr_label_tag:
            # DEPS-AUTO als Auto-Gruppe behandeln
            if pr_label_tag == 'DEPS-AUTO':
                return (pr_label_tag, pr_label_tag)
            return (pr_label_tag, None)

        # 6. Conventional Commit Prefix parsen
        prefix_match = re.match(r'^(\w+)(?:\([^)]*\))?[!:]', title)
        if prefix_match:
            prefix = prefix_match.group(1).lower()
            tag = self._COMMIT_TYPE_TAGS.get(prefix, prefix.upper())
            return (tag, None)

        # 7. Fallback
        return ('OTHER', None)

    def _build_classified_commits_text(self, commits: list, compact: bool = False) -> str:
        """
        Baue annotierten Commit-Text mit Typ-Tags fuer den AI-Prompt.

        Args:
            compact: Bei True werden Bodies auf 5 Zeilen limitiert (fuer viele Commits).

        - Merge-Commits werden uebersprungen
        - Design-Doc-Bodies werden abgeschnitten (kein Halluzinations-Material)
        - Automatisierte Commits (SEO, Deps) werden pro Gruppe zusammengefasst
        """
        classified_lines = []
        # Zaehler fuer automatisierte Commit-Gruppen: {group: (count, sample_title)}
        auto_groups: dict = {}

        for commit in commits:
            full_msg = commit.get('message', '')
            lines = full_msg.split('\n')
            title = lines[0]
            author = commit.get('author', {}).get('name', 'Unknown')

            tag, auto_group = self._classify_commit(commit)

            # Merge-Commits komplett ueberspringen
            if tag == 'MERGE':
                continue

            # Automatisierte Commits zaehlen und spaeter zusammenfassen
            if auto_group:
                if auto_group not in auto_groups:
                    auto_groups[auto_group] = (0, title)
                count, sample = auto_groups[auto_group]
                auto_groups[auto_group] = (count + 1, sample)
                continue

            # Design-Docs: Nur Titel, Body NICHT an AI (verhindert Halluzination)
            if tag == 'DESIGN-DOC':
                classified_lines.append(
                    f"- [DESIGN-DOC: GEPLANT, NICHT IMPLEMENTIERT] {title} (by {author})"
                )
                continue

            # Normale Commits: Body bereinigt (max 30 Zeilen, kein Git-Metadata-Noise)
            body_lines = [
                line for line in lines[1:]
                if line.strip()
                and not line.strip().startswith('Co-Authored-By:')
                and not line.strip().startswith('Co-authored-by:')
                and not line.strip().startswith('Signed-off-by:')
                and not re.match(r'^\s*(Fixes?|Closes?|Resolves?)\s+#\d+', line, re.IGNORECASE)
            ]

            # PR-Beschreibung als zusaetzlichen Kontext anfuegen (wenn vorhanden)
            pr_body = commit.get('pr_body', '')
            if pr_body:
                body_lines.append(f"PR-Beschreibung: {pr_body}")

            if body_lines:
                max_body = 5 if compact else 30
                body = '\n'.join(body_lines[:max_body])
                classified_lines.append(
                    f"- [{tag}] {title}\n  {body}\n  (by {author})"
                )
            else:
                classified_lines.append(f"- [{tag}] {title} (by {author})")

        # Automatisierte Commit-Gruppen als Zusammenfassung anfuegen
        _AUTO_LABELS = {
            'SEO-AUTO': 'automatisierte SEO-Commits',
            'DEPS-AUTO': 'automatisierte Dependency-Updates',
        }
        for group_name, (count, sample) in auto_groups.items():
            label = _AUTO_LABELS.get(group_name, f'automatisierte {group_name} Commits')
            classified_lines.append(
                f"- [{group_name}: {count} {label}] Beispiel: {sample}"
            )

        return "\n".join(classified_lines)

    def _format_user_friendly_commit(self, message: str) -> str:
        """Convert technical commit message to user-friendly text."""
        # Remove conventional commit prefixes
        message = message.replace('feat:', '').replace('fix:', '').replace('chore:', '')
        message = message.replace('docs:', '').replace('style:', '').replace('refactor:', '')
        message = message.replace('perf:', '').replace('test:', '').replace('build:', '')
        message = message.replace('ci:', '').replace('improve:', '').replace('update:', '')

        # Remove issue references for cleaner look (keep in internal)
        message = re.sub(r'\(#\d+\)', '', message)
        message = re.sub(r'#\d+', '', message)
        message = re.sub(r'Fixes? #\d+', '', message, flags=re.IGNORECASE)
        message = re.sub(r'Closes? #\d+', '', message, flags=re.IGNORECASE)

        # Clean up whitespace
        message = ' '.join(message.split())
        message = message.strip().strip(':').strip()

        # Capitalize first letter
        if message:
            message = message[0].upper() + message[1:]

        return message

    # PR-Label → Commit-Tag Mapping (ueberschreibt Commit-Prefix wenn gesetzt)
    _PR_LABEL_TAG_MAP = {
        'feature': 'FEATURE',
        'enhancement': 'FEATURE',
        'bug': 'BUGFIX',
        'bugfix': 'BUGFIX',
        'fix': 'BUGFIX',
        'security': 'SECURITY',
        'breaking': 'BREAKING',
        'breaking-change': 'BREAKING',
        'documentation': 'DOCS',
        'docs': 'DOCS',
        'internal': 'INTERNAL',
        'dependencies': 'DEPS-AUTO',
        'maintenance': 'CHORE',
        'performance': 'PERFORMANCE',
        'refactor': 'REFACTOR',
    }

    def _enrich_commits_with_pr_data(self, commits: list,
                                      project_path: Optional[Path]) -> list:
        """
        Reichere Commits mit PR-Beschreibungen und Labels an.

        Holt PR-Body + Labels in einem gh-Call. Labels ueberschreiben
        die Commit-Prefix-Klassifizierung wenn vorhanden.
        Max 8 PRs um API-Calls zu begrenzen.
        """
        if not project_path or not Path(project_path).exists():
            return commits

        # Alle PR-Nummern sammeln (Deduplizierung)
        pr_commits = {}  # pr_number → list of commit indices
        for i, commit in enumerate(commits):
            title = commit.get('message', '').split('\n')[0]
            pr_match = re.search(r'#(\d+)', title)
            if pr_match:
                pr_num = pr_match.group(1)
                if pr_num not in pr_commits:
                    pr_commits[pr_num] = []
                pr_commits[pr_num].append(i)

        if not pr_commits:
            return commits

        # Batch-Abfrage: Alle PRs auf einmal holen (max 8)
        pr_numbers = list(pr_commits.keys())[:8]
        for pr_number in pr_numbers:
            try:
                result = subprocess.run(
                    ['gh', 'pr', 'view', pr_number,
                     '--json', 'body,labels,title'],
                    capture_output=True, text=True, cwd=str(project_path),
                    timeout=10
                )
                if result.returncode != 0:
                    continue

                pr_data = json.loads(result.stdout)

                # Labels extrahieren und auf Tag mappen
                labels = pr_data.get('labels', [])
                label_names = [
                    lbl.get('name', '').lower()
                    for lbl in labels if isinstance(lbl, dict)
                ]
                pr_tag = None
                for label_name in label_names:
                    if label_name in self._PR_LABEL_TAG_MAP:
                        pr_tag = self._PR_LABEL_TAG_MAP[label_name]
                        break

                # Body extrahieren
                body = (pr_data.get('body') or '').strip()
                # Nur nutzen wenn substantiell (>50 Zeichen, kein Template)
                pr_body = None
                if body and len(body) > 50 and not body.startswith('<!--'):
                    if len(body) > 500:
                        body = body[:500] + '...'
                    pr_body = body

                # Auf alle Commits dieser PR anwenden
                for idx in pr_commits.get(pr_number, []):
                    if pr_body:
                        commits[idx]['pr_body'] = pr_body
                    if pr_tag:
                        commits[idx]['pr_label_tag'] = pr_tag
                    if label_names:
                        commits[idx]['pr_labels'] = label_names

            except (json.JSONDecodeError, Exception):
                continue

        return commits

    # Datei-Kategorie-Erkennung fuer Smart Diff
    _FILE_CATEGORIES = {
        'Frontend': [r'\.tsx?$', r'\.jsx?$', r'\.css$', r'\.scss$', r'\.vue$', r'\.svelte$',
                     r'/components/', r'/pages/', r'/app/', r'/web/', r'/styles/'],
        'Backend/API': [r'\.go$', r'\.py$', r'\.rs$', r'/api/', r'/server/', r'/routes/',
                        r'/handlers/', r'/middleware/', r'/services/'],
        'Datenbank': [r'migration', r'\.sql$', r'prisma/', r'schema\.prisma', r'/db/'],
        'Konfiguration': [r'\.ya?ml$', r'\.toml$', r'\.json$', r'\.env', r'docker',
                          r'Dockerfile', r'compose', r'nginx', r'traefik'],
        'Tests': [r'test[_s]', r'spec[_s]', r'__tests__', r'\.test\.', r'\.spec\.'],
        'Dokumentation': [r'\.md$', r'docs/', r'README', r'CHANGELOG', r'LICENSE'],
        'CI/CD': [r'\.github/', r'workflows/', r'\.gitlab-ci', r'Jenkinsfile'],
        'Dependencies': [r'package\.json$', r'go\.mod$', r'go\.sum$', r'requirements',
                         r'pyproject\.toml$', r'Cargo\.toml$', r'\.lock$'],
    }

    def _categorize_file(self, filepath: str) -> str:
        """Ordne eine Datei einer Kategorie zu."""
        for category, patterns in self._FILE_CATEGORIES.items():
            for pattern in patterns:
                if re.search(pattern, filepath, re.IGNORECASE):
                    return category
        return 'Sonstiges'

    def _build_code_changes_context(self, commits: list, project_path: Optional[Path]) -> str:
        """
        Strukturierte Diff-Analyse: Dateien nach Kategorie gruppiert.

        Statt roher Diffs gibt es eine Uebersicht welche Bereiche
        des Projekts betroffen sind — das hilft der AI bessere Patch Notes
        zu schreiben ohne Token fuer Diff-Details zu verschwenden.
        """
        if not self.patch_notes_include_diffs or not commits or not project_path:
            return ""

        try:
            repo_path = str(Path(project_path))
        except Exception:
            return ""

        # Alle geaenderten Dateien ueber den gesamten Commit-Range sammeln
        first_sha = None
        last_sha = None
        for commit in commits:
            sha = commit.get('id') or commit.get('sha') or commit.get('hash')
            if sha:
                if not first_sha:
                    first_sha = sha
                last_sha = sha

        if not first_sha:
            return ""

        # git diff --stat ueber den gesamten Range
        try:
            diff_range = f"{first_sha}^..{last_sha}" if first_sha != last_sha else f"{first_sha}^..{first_sha}"
            result = subprocess.run(
                ['git', 'diff', '--stat', '--stat-width=120', diff_range],
                capture_output=True, text=True, cwd=repo_path, timeout=15
            )
            if result.returncode != 0 or not result.stdout.strip():
                return ""
        except Exception:
            return ""

        # Dateien parsen und kategorisieren
        categories: dict = {}  # category → {'files': [], 'added': 0, 'removed': 0}
        new_files = []
        deleted_files = []

        for line in result.stdout.strip().splitlines():
            # Format: " path/to/file.py | 42 +++---"
            match = re.match(r'\s*(.+?)\s*\|\s*(\d+)\s*([+-]*)', line)
            if not match:
                continue

            filepath = match.group(1).strip()
            changes = int(match.group(2))
            change_str = match.group(3)
            added = change_str.count('+')
            removed = change_str.count('-')

            category = self._categorize_file(filepath)

            if category not in categories:
                categories[category] = {'files': [], 'added': 0, 'removed': 0}
            categories[category]['files'].append(filepath)
            categories[category]['added'] += added
            categories[category]['removed'] += removed

        # Neue/geloeschte Dateien erkennen
        try:
            new_result = subprocess.run(
                ['git', 'diff', '--diff-filter=A', '--name-only', diff_range],
                capture_output=True, text=True, cwd=repo_path, timeout=10
            )
            if new_result.returncode == 0:
                new_files = [f for f in new_result.stdout.strip().splitlines() if f.strip()]

            del_result = subprocess.run(
                ['git', 'diff', '--diff-filter=D', '--name-only', diff_range],
                capture_output=True, text=True, cwd=repo_path, timeout=10
            )
            if del_result.returncode == 0:
                deleted_files = [f for f in del_result.stdout.strip().splitlines() if f.strip()]
        except Exception:
            pass

        if not categories:
            return ""

        # Strukturierte Zusammenfassung bauen
        parts = ["CODE-ÄNDERUNGEN (strukturierte Übersicht):\n"]

        # Sortiert nach Anzahl Dateien (wichtigste Kategorie zuerst)
        for cat, data in sorted(categories.items(), key=lambda x: len(x[1]['files']), reverse=True):
            file_count = len(data['files'])
            parts.append(f"  {cat}: {file_count} Dateien geändert")
            # Top-3 Dateien pro Kategorie anzeigen
            for f in data['files'][:3]:
                parts.append(f"    - {Path(f).name}")
            if file_count > 3:
                parts.append(f"    - ... und {file_count - 3} weitere")

        if new_files:
            parts.append(f"\n  Neue Dateien: {len(new_files)}")
            for f in new_files[:3]:
                parts.append(f"    + {f}")

        if deleted_files:
            parts.append(f"\n  Gelöschte Dateien: {len(deleted_files)}")
            for f in deleted_files[:3]:
                parts.append(f"    - {f}")

        total_files = sum(len(d['files']) for d in categories.values())
        parts.append(f"\n  Gesamt: {total_files} Dateien in {len(categories)} Bereichen")

        return "\n".join(parts)

    def _collect_feature_branch_teasers(self, project_path: Optional[Path],
                                        deploy_branch: str = 'main') -> str:
        """Sammle aktive Feature-Branches als Teaser für 'Demnächst'-Sektion.

        Scannt alle feat/* und fix/*-Branches, holt die letzten Commit-Messages
        und formatiert sie als Kontext für die AI.
        """
        if not project_path:
            return ""

        import subprocess
        repo_path = str(project_path)

        try:
            # Alle Remote-Branches holen
            result = subprocess.run(
                ['git', 'branch', '-r', '--list', 'origin/feat/*', 'origin/fix/*'],
                capture_output=True, text=True, cwd=repo_path, timeout=5
            )
            if result.returncode != 0 or not result.stdout.strip():
                return ""

            branches = [b.strip() for b in result.stdout.strip().splitlines() if b.strip()]
            if not branches:
                return ""

            teasers = []
            for branch in branches[:8]:  # Max 8 Branches
                # Branch-Name extrahieren (origin/feat/referral-system → Referral System)
                short_name = branch.replace('origin/', '')
                display_name = short_name.split('/')[-1].replace('-', ' ').replace('_', ' ').title()
                branch_type = 'Feature' if '/feat/' in branch else 'Fix'

                # Letzte 3 Commits auf diesem Branch (die nicht auf deploy_branch sind)
                commits_result = subprocess.run(
                    ['git', 'log', branch, f'--not=origin/{deploy_branch}',
                     '--oneline', '-3', '--format=%s'],
                    capture_output=True, text=True, cwd=repo_path, timeout=5
                )
                commit_msgs = []
                if commits_result.returncode == 0 and commits_result.stdout.strip():
                    commit_msgs = [m.strip() for m in commits_result.stdout.strip().splitlines()
                                   if m.strip() and not m.strip().startswith('Merge')]

                if commit_msgs:
                    teasers.append(f"- [{branch_type}] {display_name}: {'; '.join(commit_msgs[:2])}")
                else:
                    teasers.append(f"- [{branch_type}] {display_name}")

            if not teasers:
                return ""

            return (
                "FEATURE BRANCHES (NICHT LIVE — in aktiver Entwicklung, NICHT als fertig darstellen!):\n"
                "Nutze diese Infos für einen '🔮 Demnächst / In Entwicklung'-Absatz.\n"
                "Formuliere es als spannende Vorschau, z.B. 'Wir arbeiten an...' oder 'Bald verfügbar...'\n"
                "WICHTIG: Klar kennzeichnen dass diese Features NOCH NICHT LIVE sind!\n\n"
                + "\n".join(teasers)
            )

        except Exception as e:
            self.logger.debug(f"Feature-Branch-Teasers: {e}")
            return ""

    def _load_release_guide(self, project_path: Optional[Path]) -> str:
        """Lade manuell geschriebene Feature-Anleitungen aus release_guide.md.

        Sucht in: release_guide.md, docs/release_guide.md, RELEASE_GUIDE.md
        Der Inhalt wird WÖRTLICH in den Prompt übernommen (kein AI-Rewrite).
        Nach dem Laden wird die Datei NICHT gelöscht — der Entwickler räumt sie
        manuell auf wenn das Release durch ist.
        """
        if not project_path:
            return ""

        candidates = [
            project_path / 'release_guide.md',
            project_path / 'docs' / 'release_guide.md',
            project_path / 'RELEASE_GUIDE.md',
        ]

        for path in candidates:
            if path.exists():
                try:
                    content = path.read_text(encoding='utf-8').strip()
                    if not content or len(content) < 20:
                        continue

                    self.logger.info(f"📋 Release-Guide geladen: {path} ({len(content)} Zeichen)")
                    return (
                        "FEATURE-ANLEITUNGEN (vom Entwickler geschrieben — WÖRTLICH übernehmen!):\n"
                        "Füge diese Anleitungen als '📖 So funktioniert's'-Absatz ein.\n"
                        "NICHT umschreiben oder halluzinieren — der Text ist verifiziert!\n\n"
                        + content[:2000]
                    )
                except Exception:
                    continue

        return ""

    def _load_patch_notes_context(self, project_config: Optional[Dict],
                                  project_path: Optional[Path]) -> str:
        """Load optional context files + project description for richer prompts."""
        if not project_config:
            return ""

        patch_config = project_config.get('patch_notes', {})

        # Projekt-Beschreibung und Zielgruppe (fuer zielgruppengerechte Patch Notes)
        sections = []
        project_desc = patch_config.get('project_description', '')
        target_audience = patch_config.get('target_audience', '')
        if project_desc or target_audience:
            meta_parts = []
            if project_desc:
                meta_parts.append(f"Projekt: {project_desc}")
            if target_audience:
                meta_parts.append(f"Zielgruppe: {target_audience}")
            sections.append(
                "PROJEKT-KONTEXT (schreibe Patch Notes passend fuer diese Zielgruppe):\n"
                + "\n".join(meta_parts)
            )

        # Release-Guide laden (manuell geschriebene Feature-Anleitungen)
        # Sucht automatisch nach release_guide.md — keine Config nötig
        release_guide = self._load_release_guide(project_path)
        if release_guide:
            sections.append(release_guide)

        context_files = patch_config.get('context_files') or patch_config.get('context_file')
        if not context_files and not sections:
            return ""

        # Context-Dateien laden (wenn vorhanden)
        if context_files:
            if isinstance(context_files, str):
                context_files = [context_files]
            if not isinstance(context_files, list):
                context_files = []
        else:
            context_files = []

        base_path = project_path
        if not base_path:
            base = project_config.get('path', '')
            base_path = Path(base) if base else None

        per_file_limit = int(patch_config.get('context_max_chars', 1500))
        total_limit = int(patch_config.get('context_total_max_chars', 4000))
        total_chars = 0

        for entry in context_files:
            if not entry:
                continue
            entry_path = Path(entry)
            if not entry_path.is_absolute() and base_path:
                entry_path = base_path / entry_path
            if not entry_path.exists():
                continue
            try:
                content = entry_path.read_text(encoding='utf-8', errors='ignore').strip()
            except Exception:
                continue

            if not content:
                continue

            if per_file_limit > 0 and len(content) > per_file_limit:
                head_len = max(1, per_file_limit // 2)
                tail_len = per_file_limit - head_len
                content = (
                    content[:head_len].rstrip()
                    + "\n... (snip) ...\n"
                    + content[-tail_len:].lstrip()
                )

            section = f"PROJECT CONTEXT FILE: {entry_path.name}\n{content}"
            total_chars += len(section)
            if total_limit > 0 and total_chars > total_limit:
                break
            sections.append(section)

        if not sections:
            return ""

        return "PROJECT CONTEXT (REFERENCE):\n\n" + "\n\n".join(sections)

    def _is_patch_notes_too_short(self, response: str, commits: list) -> bool:
        """Heuristic to detect underspecified AI output."""
        if not response:
            return True

        bullet_count = sum(
            1 for line in response.splitlines()
            if line.strip().startswith('•')
        )
        commit_detail = any(
            len([l for l in (c.get('message') or '').splitlines() if l.strip()]) >= 5
            for c in commits
        )

        if len(commits) > 1 or commit_detail:
            min_bullets = 3
        else:
            min_bullets = 1

        if bullet_count < min_bullets:
            return True

        if commit_detail and len(response) < 300:
            return True

        return False

    def _validate_ai_output(self, ai_result, commits: list) -> dict:
        """
        Post-Generierungs-Validierung: Pruefe ob AI-Output zu den Commits passt.

        Returns:
            dict mit 'valid' (bool), 'warnings' (list), 'fixes_applied' (list)
        """
        warnings = []
        fixes = []

        if not ai_result or not isinstance(ai_result, dict):
            return {'valid': True, 'warnings': [], 'fixes_applied': []}

        # 1. Feature-Count-Check: AI darf nicht mehr Features nennen als [FEATURE] Commits
        feature_commits = sum(
            1 for c in commits
            if re.match(r'^feat(?:\([^)]*\))?[!:]', c.get('message', '').split('\n')[0])
        )
        ai_features = [
            ch for ch in ai_result.get('changes', [])
            if isinstance(ch, dict) and ch.get('type') == 'feature'
        ]
        if len(ai_features) > max(feature_commits * 2, feature_commits + 2):
            warnings.append(
                f"AI nennt {len(ai_features)} Features, aber nur {feature_commits} feat:-Commits vorhanden"
            )

        # 2. Design-Doc-Leak-Check: Themen aus Design-Docs duerfen nicht als Features auftauchen
        design_doc_keywords = []
        for c in commits:
            tag, _ = self._classify_commit(c)
            if tag == 'DESIGN-DOC':
                title = c.get('message', '').split('\n')[0].lower()
                # Extrahiere Schluesselwoerter aus dem Design-Doc-Titel
                for word in re.findall(r'[a-zäöü]{4,}', title):
                    if word not in ('docs', 'design', 'system', 'creator', 'vollständige',
                                    'spezifikation', 'empfehlung', 'dokument'):
                        design_doc_keywords.append(word)

        if design_doc_keywords and ai_features:
            # Pruefe ob Design-Doc-Keywords in Feature-Beschreibungen auftauchen
            for feature in ai_features:
                desc = (feature.get('description', '') + ' '.join(feature.get('details', []))).lower()
                matched = [kw for kw in design_doc_keywords if kw in desc]
                if matched:
                    warnings.append(
                        f"Design-Doc-Thema '{matched[0]}' als Feature erkannt — wird entfernt"
                    )
                    # Feature entfernen (halluziniert aus Design-Doc)
                    ai_result['changes'] = [
                        ch for ch in ai_result['changes']
                        if ch is not feature
                    ]
                    fixes.append(f"Feature '{feature.get('description', '')[:50]}' entfernt (Design-Doc-Leak)")

        # 3. Web-Content-Check: Pruefe ob web_content Design-Doc-Themen enthaelt
        web_content = ai_result.get('web_content', '') or ai_result.get('content', '')
        if web_content and design_doc_keywords:
            for kw in design_doc_keywords:
                # Nur warnen wenn das Keyword im Feature-Kontext steht
                pattern = rf'(?:feature|neu|new|eingeführt|implementiert|hinzugefügt).*{re.escape(kw)}'
                if re.search(pattern, web_content, re.IGNORECASE):
                    warnings.append(
                        f"web_content erwähnt Design-Doc-Thema '{kw}' als Feature"
                    )

        # 4. Leere-Changes-Check
        if not ai_result.get('changes'):
            warnings.append("AI hat keine changes generiert")

        return {
            'valid': len(warnings) == 0,
            'warnings': warnings,
            'fixes_applied': fixes,
        }

    # Basis-Regelblock der IMMER an jeden Prompt angehaengt wird (A/B-Varianten-sicher)
    _CLASSIFICATION_RULES_DE = """
COMMIT-TYP-REGELN (IMMER BEACHTEN):
- [FEATURE] = Implementiertes Feature → als "Neues Feature" listen
- [BUGFIX] = Behobener Bug → als "Bugfix" listen
- [SECURITY] = Sicherheitsfix → vage beschreiben (kein WIE)
- [DESIGN-DOC: GEPLANT, NICHT IMPLEMENTIERT] = NUR ein Planungsdokument → NIEMALS als Feature!
- [SEO-AUTO] / [DEPS-AUTO] = Automatisiert → kurz zusammenfassen
- [DOCS] / [CHORE] / [CI] / [TEST] = Intern → nur erwaehnen wenn nutzerrelevant
- [REVERT] = Rueckgaengig gemacht → erwaehnen wenn nutzerrelevant
- [BREAKING] = Breaking Change → IMMER prominent erwaehnen
- PR-Label-Tags (pr_label_tag) haben Vorrang vor Commit-Prefix-Tags
ERFINDE KEINE Features die nicht als [FEATURE] getaggt sind!"""

    _CLASSIFICATION_RULES_EN = """
COMMIT TYPE RULES (ALWAYS OBSERVE):
- [FEATURE] = Implemented feature → list as "New Feature"
- [BUGFIX] = Fixed bug → list as "Bug Fix"
- [SECURITY] = Security fix → describe vaguely (not HOW)
- [DESIGN-DOC: GEPLANT, NICHT IMPLEMENTIERT] = Planning doc only → NEVER list as feature!
- [SEO-AUTO] / [DEPS-AUTO] = Automated → summarize briefly
- [DOCS] / [CHORE] / [CI] / [TEST] = Internal → mention only if user-relevant
- [REVERT] = Reverted → mention if user-relevant
- [BREAKING] = Breaking change → ALWAYS mention prominently
- PR label tags (pr_label_tag) take precedence over commit prefix tags
Do NOT invent features that are not tagged [FEATURE]!"""

    # Pfad zur Changelog-DB (relativ zum Projekt-Root)
    _CHANGELOGS_DB = Path(__file__).resolve().parent.parent.parent.parent / 'data' / 'changelogs.db'

    def _load_previous_version_context(self, repo_name: str) -> str:
        """
        Lade den Content der letzten Version aus der Changelog-DB.

        Wird als BEREITS-ABGEDECKT Kontext in den Prompt injiziert,
        damit die KI keine Duplikate erzeugt.
        """
        try:
            import sqlite3
            if not self._CHANGELOGS_DB.exists():
                return ""

            with sqlite3.connect(str(self._CHANGELOGS_DB)) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT version, title, content FROM changelogs "
                    "WHERE project = ? ORDER BY created_at DESC LIMIT 1",
                    (repo_name,)
                )
                row = cursor.fetchone()

            if not row or not row[2]:
                return ""

            version, title, content = row
            # Content auf max 1500 Zeichen kuerzen
            if len(content) > 1500:
                content = content[:1500] + "\n... (gekuerzt)"

            return (
                f"BEREITS ABGEDECKT IN VORHERIGER VERSION (v{version}: {title}):\n"
                f"{content}\n"
                "→ Diese Punkte NICHT erneut erwaehnen! Nur NEUE Aenderungen beschreiben."
            )
        except Exception as e:
            logger.debug(f"Konnte vorherige Version nicht laden: {e}")
            return ""

    def _scan_dev_branch_teasers(self, project_path: Optional[Path],
                                  min_commits: int = 5) -> str:
        """
        Scanne aktive feat/* Branches fuer Coming-Soon Teaser.

        Sammelt Fortschritt, Feature-Highlights und Beschreibungen.
        Gibt formatierten Kontext fuer den AI-Prompt zurueck.
        """
        if not project_path or not Path(project_path).exists():
            return ""

        try:
            # Alle Remote feat/* Branches holen
            result = subprocess.run(
                ['git', 'branch', '-r', '--list', 'origin/feat/*'],
                capture_output=True, text=True, cwd=str(project_path),
                timeout=10
            )
            if result.returncode != 0 or not result.stdout.strip():
                return ""

            teasers = []
            for line in result.stdout.strip().splitlines():
                branch = line.strip()
                if not branch or '->' in branch:
                    continue

                # Commits ahead of main zaehlen
                count_result = subprocess.run(
                    ['git', 'rev-list', '--count', f'origin/main..{branch}'],
                    capture_output=True, text=True, cwd=str(project_path),
                    timeout=10
                )
                if count_result.returncode != 0:
                    continue
                try:
                    ahead_count = int(count_result.stdout.strip())
                except (ValueError, AttributeError):
                    continue
                if ahead_count < min_commits:
                    continue

                # Feature-Highlights: Alle feat:-Commits sammeln (max 3)
                feat_result = subprocess.run(
                    ['git', 'log', '--oneline', '--grep=^feat',
                     f'origin/main..{branch}'],
                    capture_output=True, text=True, cwd=str(project_path),
                    timeout=10
                )
                feat_titles = []
                if feat_result.stdout.strip():
                    for feat_line in feat_result.stdout.strip().splitlines()[:3]:
                        # Hash entfernen, Prefix bereinigen
                        title = feat_line.split(' ', 1)[-1] if ' ' in feat_line else feat_line
                        title = re.sub(r'^feat(?:\([^)]*\))?:\s*', '', title)
                        feat_titles.append(title)

                # Branch-Name bereinigen
                feature_name = branch.replace('origin/feat/', '').replace('-', ' ').title()

                # Fortschritts-Indikator
                if ahead_count >= 20:
                    progress = "weit fortgeschritten"
                elif ahead_count >= 10:
                    progress = "in aktiver Entwicklung"
                else:
                    progress = "in fruehen Phasen"

                teaser = f"- **{feature_name}** ({ahead_count} Commits, {progress})"
                if feat_titles:
                    teaser += "\n  Highlights: " + ", ".join(feat_titles[:3])
                teasers.append(teaser)

            if not teasers:
                return ""

            return (
                "FEATURES IN ENTWICKLUNG (aktive Feature-Branches, NICHT auf main):\n"
                + "\n".join(teasers) + "\n\n"
                "TEASER-ANWEISUNGEN:\n"
                "→ Fuege am Ende IMMER eine '🔮 Demnächst' oder '🔮 Coming Soon' Sektion ein.\n"
                "→ Formuliere spannend und vorfreudig, aber EHRLICH — das Feature ist noch NICHT fertig.\n"
                "→ Nutze Formulierungen wie: 'Wir arbeiten bereits an...', 'Demnächst erwartet euch...', "
                "'Freut euch auf...', 'Stay tuned für...'\n"
                "→ Erwähne die Highlights kurz, aber verspreche NICHTS Konkretes.\n"
                "→ Maximal 2-3 Sätze pro Feature. Mache Lust auf das nächste Update!"
            )
        except Exception as e:
            logger.debug(f"Dev-Branch-Scan fehlgeschlagen: {e}")
            return ""

    async def _generate_ai_patch_notes(self, commits: list, language: str, repo_name: str,
                                       project_config: Optional[Dict] = None):
        """
        Generate professional, user-friendly patch notes using AI.

        Returns:
            Tuple (result, git_stats) wobei result ein Dict (strukturiert),
            str (Raw-Text Fallback) oder None bei Fehler ist.
        """
        if not self.ai_service or not commits:
            return None, {}

        # Try to get CHANGELOG content
        changelog_content = ""
        version = None
        version_data = None

        project_path = None
        if project_config:
            project_path = Path(project_config.get('path', ''))
            changelog_path = project_path / 'CHANGELOG.md'

            if changelog_path.exists():
                try:
                    # Detect version from commits
                    for commit in commits:
                        msg = commit.get('message', '')
                        match = re.search(
                            r'v?(?:ersion|elease)?\s*(?<![0-9.])([0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,4})(?!\.[0-9])',
                            msg, re.IGNORECASE
                        )
                        if match:
                            version = match.group(1)
                            break

                    from utils.changelog_parser import get_changelog_parser
                    parser = get_changelog_parser(project_path)

                    # Get CHANGELOG section if version found
                    if version:
                        version_data = parser.get_version_section(version)

                        if version_data:
                            changelog_content = version_data['content']
                            self.logger.info(f"📖 Using CHANGELOG.md section for v{version} ({len(changelog_content)} chars)")
                        else:
                            self.logger.info(f"⚠️ Version {version} not found in CHANGELOG, using commits only")
                    else:
                        # Kein Version-Bump in Commits erkannt.
                        # [Unreleased]-Sektion bevorzugen (enthält laufende Änderungen)
                        unreleased = parser.get_unreleased_section()
                        if unreleased:
                            changelog_content = unreleased
                            self.logger.info(
                                f"📖 Using [Unreleased] CHANGELOG section as context "
                                f"({len(changelog_content)} chars)"
                            )
                        else:
                            # Fallback: letzte Released-Version
                            latest = parser.get_latest_version()
                            if latest:
                                version_data = parser.get_version_section(latest)
                                if version_data:
                                    changelog_content = version_data['content']
                                    self.logger.info(
                                        f"📖 Using latest CHANGELOG.md as context (v{latest}), "
                                        f"but NOT setting version ({len(changelog_content)} chars)"
                                    )
                        if not changelog_content:
                            self.logger.info("⚠️ No version detected in commits, using commits only")

                except Exception as e:
                    self.logger.warning(f"⚠️ Could not parse CHANGELOG: {e}")

        project_context = self._load_patch_notes_context(project_config, project_path)

        # PR-Daten anreichern (Body + Labels in einem Call)
        if project_path:
            commits = self._enrich_commits_with_pr_data(commits, project_path)
            enriched_body = sum(1 for c in commits if c.get('pr_body'))
            enriched_labels = sum(1 for c in commits if c.get('pr_label_tag'))
            if enriched_body or enriched_labels:
                self.logger.info(
                    f"📝 PR-Enrichment: {enriched_body} Bodies, {enriched_labels} Labels"
                )

        # Duplikat-Vermeidung: Vorherige Version als Kontext laden
        previous_version_context = self._load_previous_version_context(repo_name)
        if previous_version_context:
            project_context = (project_context + "\n\n" + previous_version_context).strip()
            self.logger.info(f"📋 Vorherige Version als Duplikat-Guard geladen")

        # Dev-Branch Teaser: Aktive Feature-Branches scannen
        dev_teasers = self._scan_dev_branch_teasers(project_path)
        if dev_teasers:
            project_context = (project_context + "\n\n" + dev_teasers).strip()
            self.logger.info(f"🔮 Dev-Branch-Teaser fuer Prompt geladen")

        # Collect git stats + team credits
        git_stats = self._collect_git_stats(commits, project_path)
        stats_line = self._format_stats_line(git_stats, language)
        stats_section = self._format_stats_section(git_stats, language)
        team_credits = self._build_team_credits(commits)
        credits_section = self._format_credits_section(team_credits, language)

        # Build enhanced prompt with A/B Testing
        selected_variant = None
        variant_id = None

        code_changes_context = self._build_code_changes_context(commits, project_path)

        if self.patch_notes_trainer and self.prompt_ab_testing and (changelog_content or project_config):
            try:
                # Select prompt variant — DB-basiert (Learning) oder Datei-Fallback
                db_variant_id = None
                try:
                    from integrations.patch_notes_learning import PatchNotesLearning
                    learning = PatchNotesLearning()
                    await learning.connect()
                    db_variant_id = await learning.get_best_variant(repo_name)
                    await learning.close()
                except Exception:
                    pass

                # 1. Config-Preferred-Variant (höchste Prio — Projekt pinnt Variante)
                patch_config = project_config.get('patch_notes', {}) if project_config else {}
                config_variant_id = patch_config.get('preferred_variant')

                if config_variant_id and config_variant_id in self.prompt_ab_testing.variants:
                    variant_id = config_variant_id
                    selected_variant = self.prompt_ab_testing.variants[variant_id]
                    self.logger.info(f"📌 Config: Gepinnte Variante '{variant_id}' für {repo_name}")
                elif db_variant_id:
                    # 2. DB hat genug Daten → bevorzugte Variante nutzen
                    variant_id = db_variant_id
                    selected_variant = self.prompt_ab_testing.variants.get(variant_id)
                    if not selected_variant:
                        # Fallback wenn Variante nicht existiert
                        selected_variant = self.prompt_ab_testing.select_variant(
                            project=repo_name, strategy='weighted_random'
                        )
                        variant_id = selected_variant.id
                    self.logger.info(f"🧪 Learning-DB: Beste Variante '{variant_id}' für {repo_name}")
                else:
                    # 3. Nicht genug DB-Daten → klassisches A/B Testing
                    selected_variant = self.prompt_ab_testing.select_variant(
                        project=repo_name, strategy='weighted_random'
                    )
                    variant_id = selected_variant.id
                    self.logger.info(f"🧪 A/B Test: Variante '{selected_variant.name}' (ID: {variant_id})")

                # Build prompt from variant template (language-specific)
                variant_template = self.prompt_ab_testing.get_variant_template(
                    variant_id=variant_id,
                    language=language
                )
                # format_map mit DefaultDict um KeyError bei alten Templates zu vermeiden
                from collections import defaultdict

                # Klassifizierte Commits fuer Trainer-Prompt (mit Typ-Tags)
                classified_commits_text = self._build_classified_commits_text(
                    commits[:25], compact=len(commits) > 15
                )
                format_values = defaultdict(str, {
                    'project': repo_name,
                    'changelog': changelog_content or "No CHANGELOG available",
                    'commits': classified_commits_text,
                    'stats_section': stats_section,
                    'credits_section': credits_section,
                    'stats_line': stats_line if git_stats.get('commits', 0) >= 5 else '',
                })
                prompt = variant_template.format_map(format_values)

                # Add examples — DB-basiert (feedback-gewichtet) oder Legacy (auto-score)
                examples_added = False
                try:
                    from integrations.patch_notes_learning import PatchNotesLearning
                    learning = PatchNotesLearning()
                    await learning.connect()
                    db_examples = await learning.get_best_examples(repo_name, limit=2)
                    await learning.close()
                    if db_examples:
                        prompt += "\n\n# EXAMPLES OF HIGH-QUALITY PATCH NOTES (feedback-ranked)\n\n"
                        for i, ex in enumerate(db_examples, 1):
                            prompt += f"## Example {i} ({ex['project']} v{ex['version']}, Score: {ex['combined_score']:.0f}):\n"
                            prompt += f"```\n{ex['content'][:400]}...\n```\n\n"
                        examples_added = True
                except Exception:
                    pass

                # Fallback: Legacy-Beispiele
                if not examples_added and self.patch_notes_trainer.good_examples:
                    prompt += "\n\n# EXAMPLES OF HIGH-QUALITY PATCH NOTES\n\n"
                    for i, example in enumerate(self.patch_notes_trainer.good_examples[:2], 1):
                        prompt += f"## Example {i} ({example['project']} v{example['version']}):\n"
                        prompt += f"```\n{example['generated_notes'][:400]}...\n```\n\n"

                # Projekt-Kontext VOR Regeln (damit AI die Zielgruppe kennt)
                if project_context:
                    prompt += f"\n\n{project_context}"

                if code_changes_context:
                    prompt += f"\n\n{code_changes_context}"

                # Feature-Branch-Teasers (für "Demnächst"-Sektion)
                deploy_branch = project_config.get('deploy', {}).get('branch', 'main') if project_config else 'main'
                feature_teasers = self._collect_feature_branch_teasers(project_path, deploy_branch)
                if feature_teasers:
                    prompt += f"\n\n{feature_teasers}"

                # Basis-Regelblock IMMER anhaengen (A/B-Varianten-sicher)
                rules = self._CLASSIFICATION_RULES_DE if language == 'de' else self._CLASSIFICATION_RULES_EN
                prompt += f"\n\n{rules}"

            except Exception as e:
                self.logger.warning(f"⚠️ A/B Testing failed, using enhanced prompt: {e}")
                try:
                    # Klassifizierten Text an Trainer uebergeben statt rohe Commits
                    classified_for_trainer = self._build_classified_commits_text(commits[:10])
                    prompt = self.patch_notes_trainer.build_enhanced_prompt(
                        changelog_content=changelog_content,
                        commits=classified_for_trainer,
                        language=language,
                        project=repo_name
                    )
                    if code_changes_context:
                        prompt += f"\n\n{code_changes_context}"
                    if project_context:
                        prompt += f"\n\n{project_context}"
                    self.logger.info(f"🎯 Using enhanced AI prompt with training examples")
                except Exception as e2:
                    self.logger.warning(f"⚠️ Enhanced prompt failed, using fallback: {e2}")
                    prompt = self._build_fallback_prompt(
                        commits,
                        language,
                        repo_name,
                        changelog_content,
                        code_changes_context,
                        project_context
                    )
        else:
            # Fallback to original prompt if no trainer available
            prompt = self._build_fallback_prompt(
                commits,
                language,
                repo_name,
                changelog_content,
                code_changes_context,
                project_context
            )

        # Log commits being processed
        num_commits = len(commits)
        self.logger.info(f"🔍 AI Processing {num_commits} commit(s) for {repo_name}:")
        for i, commit in enumerate(commits[:5], 1):
            msg = commit.get('message', '').split('\n')[0]
            self.logger.info(f"   {i}. {msg}")
        if num_commits > 5:
            self.logger.info(f"   ... and {num_commits - 5} more commits")

        # Call AI Service
        patch_config = project_config.get('patch_notes', {}) if project_config else {}
        use_critical_model = patch_config.get('use_critical_model', True)

        # === VERSUCH 1: Strukturierter Output (Dual-Format) ===
        try:
            structured_prompt = self._build_structured_prompt(
                prompt, language, repo_name, len(commits)
            )
            structured_result = await self.ai_service.generate_structured_patch_notes(
                prompt=structured_prompt,
                use_critical_model=use_critical_model,
            )

            if structured_result and isinstance(structured_result, dict):
                # Echte Git-Stats einsetzen (AI-Stats sind unzuverlaessig)
                structured_result['stats'] = git_stats
                if version:
                    structured_result['version'] = version
                structured_result['language'] = language

                # Learning DB: Generation aufzeichnen
                try:
                    from integrations.patch_notes_learning import PatchNotesLearning
                    learning = PatchNotesLearning()
                    await learning.connect()
                    gen_version = structured_result.get('version', version or 'unknown')
                    auto_quality = 70.0  # Basis-Score fuer strukturierten Output
                    content = structured_result.get('web_content', '')
                    # Einfaches Quality-Scoring
                    if len(content) > 500:
                        auto_quality += 10
                    if len(structured_result.get('changes', [])) >= 3:
                        auto_quality += 10
                    if structured_result.get('tldr'):
                        auto_quality += 5
                    await learning.record_generation(
                        project=repo_name,
                        version=gen_version,
                        variant_id=variant_id,
                        title=structured_result.get('title', ''),
                        content=content[:2000],
                        auto_quality=min(100, auto_quality),
                        commits_count=len(commits),
                    )
                    await learning.close()
                except Exception as learn_err:
                    self.logger.debug("Learning-DB Record fehlgeschlagen: %s", learn_err)

                self.logger.info(
                    f"✅ Strukturierte Patch Notes fuer {repo_name}: "
                    f"'{structured_result.get('title')}' "
                    f"({len(structured_result.get('discord_highlights', []))} Highlights, "
                    f"{len(structured_result.get('web_content', ''))} Zeichen Web)"
                )
                return structured_result, git_stats

        except Exception as e:
            self.logger.warning(f"⚠️ Strukturierter Output fehlgeschlagen, Fallback auf Raw-Text: {e}")

        # === VERSUCH 2: Raw-Text Fallback (bestehender Flow) ===
        # WICHTIG: Eigenen Fallback-Prompt verwenden, NICHT den strukturierten A/B-Prompt!
        # Der strukturierte Prompt fordert JSON-Felder (changes, discord_teaser etc.) an,
        # aber get_raw_ai_response() erzwingt kein JSON → AI gibt Hybrid-Müll zurück.
        fallback_prompt = self._build_fallback_prompt(
            commits, language, repo_name,
            changelog_content, code_changes_context, project_context
        )
        try:
            ai_response = await self.ai_service.get_raw_ai_response(
                prompt=fallback_prompt,
                use_critical_model=use_critical_model
            )

            if not ai_response:
                return None, git_stats

            response = ai_response.strip()

            # Sicherstellen dass es mit einer Kategorie anfaengt
            if not response.startswith('**'):
                lines = response.split('\n')
                start_idx = 0
                for i, line in enumerate(lines):
                    if line.startswith('**'):
                        start_idx = i
                        break
                response = '\n'.join(lines[start_idx:])

            if self._is_patch_notes_too_short(response, commits):
                self.logger.info("⚠️ AI Patch Notes zu kurz, zweiter Durchlauf")
                strict_prompt = self._build_fallback_prompt(
                    commits, language, repo_name,
                    changelog_content, code_changes_context, project_context,
                    strict=True
                )
                retry = await self.ai_service.get_raw_ai_response(
                    prompt=strict_prompt, use_critical_model=True
                )
                if retry:
                    retry = retry.strip()
                    if not retry.startswith('**'):
                        for i, line in enumerate(retry.split('\n')):
                            if line.startswith('**'):
                                retry = '\n'.join(retry.split('\n')[i:])
                                break
                    if retry and len(retry) >= len(response):
                        response = retry

            # Stats-Zeile anhaengen
            if stats_line and git_stats.get('commits', 0) >= 5:
                if '📊' not in response:
                    response = response.rstrip() + f"\n\n{stats_line}"

            self.logger.info(f"✅ AI Raw-Text Patch Notes fuer {repo_name} ({len(response)} Zeichen)")
            return (response if response else None), git_stats

        except Exception as e:
            self.logger.error(f"AI Patch Notes Generierung fehlgeschlagen: {e}")
            return None, git_stats

    def _build_structured_prompt(self, base_prompt: str, language: str,
                                  project_name: str, num_commits: int) -> str:
        """Erweitere den Base-Prompt um strukturierte Feld-Anweisungen."""
        if language == 'de':
            prefix = f"""Du generierst strukturierte Patch Notes als JSON fuer "{project_name}".

SICHERHEITSREGELN (STRIKT — bei Verstoß wird der gesamte Output verworfen):
- NIEMALS Dateipfade, Server-Pfade oder Verzeichnisstrukturen erwaehnen
- NIEMALS IP-Adressen, Ports oder Netzwerk-Konfigurationen nennen
- Security-Fixes NUR vage beschreiben: WAS verbessert wurde, nicht WIE oder welche Schwachstelle
- KEINE alten verwundbaren Dependency-Versionen nennen (nur die neue Version, wenn relevant)
- KEINE Config-Dateien, deren Pfade oder Inhalte referenzieren
- KEINE internen Methodennamen, Klassennamen oder Code-Strukturen offenlegen

WICHTIG — KONSISTENZ-REGELN:
- discord_highlights und web_content muessen die GLEICHEN Aenderungen beschreiben
- discord_highlights sind KURZE Versionen der wichtigsten Punkte aus web_content
- Erfinde NICHTS was nicht in den Commits steht
- Alle Felder beziehen sich auf denselben Release

COMMIT-TYP-TAGS — STRIKT BEACHTEN:
- Commits mit [DESIGN-DOC: GEPLANT, NICHT IMPLEMENTIERT] sind Planungsdokumente fuer ZUKUENFTIGE Features!
  → NIEMALS als implementiertes Feature in title/tldr/summary/web_content/changes auflisten!
  → Hoechstens unter einer "Geplant"-Sektion erwaehnen oder ganz weglassen
- Commits mit [SEO-AUTO] sind automatisierte SEO-Optimierungen → kurz zusammenfassen
- Commits mit [DOCS] sind Dokumentationsaenderungen → kein Feature!
- Nur [FEATURE] Commits sind tatsaechlich neue Features

FELD-ANWEISUNGEN:
- title: Kurzer, praegananter Titel (z.B. "Performance & Security Update")
- tldr: EIN praegnanter Satz, der die wichtigste Aenderung zusammenfasst
- summary: 2-3 Saetze Zusammenfassung fuer die Webseite
- discord_highlights: 3-5 kurze Bullet-Points fuer Discord (je max 120 Zeichen, mit Emojis)
  → Das sind die HIGHLIGHTS aus web_content, nicht andere Informationen!
- web_content: Ausfuehrlicher Markdown-Text mit allen Details (1000-5000 Zeichen)
  → Subheadings (##), Bullet-Points, technische Details, Nutzer-Impact
  → Zielgruppe: Interessierte Community-Mitglieder die alles wissen wollen
- changes: Strukturierte Liste aller Aenderungen mit type (feature/fix/improvement/breaking/docs), description und details-Array
- breaking_changes: Liste von Breaking Changes (leeres Array wenn keine)
- stats: Wird nachtraeglich mit echten Git-Stats befuellt, setze commits auf {num_commits}
- version: Die erkannte Versionsnummer (oder "patch" wenn keine erkannt)
- language: "{language}"

SEO-KEYWORDS:
- Generiere 5-10 spezifische, suchrelevante Keywords fuer dieses Release
- Keywords muessen zum tatsaechlichen Inhalt passen (nicht generisch wie "update" oder "patch")
- Mix aus Deutsch und Englisch erlaubt (je nach Zielgruppe)
- Technische Begriffe bevorzugt (z.B. "oauth2-integration", "api-performance", "rate-limiting")
- Feld: "seo_keywords" — Array von Strings

SEO-KATEGORIE:
- Waehle die Hauptkategorie: feature, security, performance, bugfix, maintenance
- Feld: "seo_category" — ein String

"""
        else:
            prefix = f"""You are generating structured patch notes as JSON for "{project_name}".

SECURITY RULES (STRICT — violation causes the entire output to be discarded):
- NEVER mention file paths, server paths or directory structures
- NEVER mention IP addresses, ports or network configurations
- Describe security fixes ONLY vaguely: WHAT was improved, not HOW or which vulnerability
- Do NOT mention old vulnerable dependency versions (only the new version, if relevant)
- Do NOT reference config files, their paths or contents
- Do NOT expose internal method names, class names or code structures

IMPORTANT — CONSISTENCY RULES:
- discord_highlights and web_content MUST describe the SAME changes
- discord_highlights are SHORT versions of the most important points from web_content
- Do NOT invent anything not in the commits
- All fields refer to the same release

COMMIT TYPE TAGS — STRICTLY OBSERVE:
- Commits tagged [DESIGN-DOC: GEPLANT, NICHT IMPLEMENTIERT] are planning documents for FUTURE features!
  → NEVER list as implemented features in title/tldr/summary/web_content/changes!
  → At most mention under a "Planned" section or omit entirely
- Commits tagged [SEO-AUTO] are automated SEO optimizations → summarize briefly
- Commits tagged [DOCS] are documentation changes → not a feature!
- Only [FEATURE] commits are actually new features

FIELD INSTRUCTIONS:
- title: Short, catchy title (e.g. "Performance & Security Update")
- tldr: ONE concise sentence summarizing the most important change
- summary: 2-3 sentence summary for the website
- discord_highlights: 3-5 short bullet points for Discord (max 120 chars each, with emojis)
  → These are the HIGHLIGHTS from web_content, not different information!
- web_content: Detailed markdown text with all details (1000-5000 chars)
  → Subheadings (##), bullet points, technical details, user impact
  → Audience: Interested community members who want to know everything
- changes: Structured list of all changes with type (feature/fix/improvement/breaking/docs), description and details array
- breaking_changes: List of breaking changes (empty array if none)
- stats: Will be filled with real git stats afterwards, set commits to {num_commits}
- version: The detected version number (or "patch" if none detected)
- language: "{language}"

SEO KEYWORDS:
- Generate 5-10 specific, search-relevant keywords for this release
- Keywords must match the actual content (not generic like "update" or "patch")
- Technical terms preferred (e.g. "oauth2-integration", "api-performance", "rate-limiting")
- Field: "seo_keywords" — Array of strings

SEO CATEGORY:
- Choose the main category: feature, security, performance, bugfix, maintenance
- Field: "seo_category" — a string

"""

        return prefix + base_prompt

    def _build_fallback_prompt(self, commits: list, language: str, repo_name: str,
                               changelog_content: str = "", code_changes_context: str = "",
                               project_context: str = "", strict: bool = False) -> str:
        """Build fallback prompt when trainer is not available."""
        # Build classified commit summary for AI (mit Typ-Tags)
        # Max 50 Commits im Prompt um Token-Budget nicht zu sprengen
        commits_text = self._build_classified_commits_text(commits[:50])
        num_commits = len(commits)
        detail_instruction = ""

        extra_sections = []
        if changelog_content:
            extra_sections.append(f"CHANGELOG INFORMATION:\n{changelog_content}")
        if code_changes_context:
            extra_sections.append(code_changes_context)
        if project_context:
            extra_sections.append(project_context)
        extra_context = "\n\n".join(extra_sections).strip()
        if extra_context:
            extra_context = f"\n\n{extra_context}\n"

        if num_commits > 30:
            # Many commits - ask for high-level overview
            if language == 'de':
                detail_instruction = f"\n\n⚠️ WICHTIG: Es gibt {num_commits} Commits! Erstelle eine HIGH-LEVEL Übersicht. Gruppiere ähnliche Commits und beschreibe große Features detailliert, aber fasse Kleinigkeiten zusammen."
            else:
                detail_instruction = f"\n\n⚠️ IMPORTANT: There are {num_commits} commits! Create a HIGH-LEVEL overview. Group similar commits and describe major features in detail, but summarize minor changes."
        elif num_commits > 15:
            # Medium amount - balanced approach, but RECOGNIZE major features
            if language == 'de':
                detail_instruction = f"\n\n⚠️ Es gibt {num_commits} Commits. Gruppiere verwandte Commits (z.B. alle zum gleichen Feature-Namen) zu EINEM detaillierten Feature-Punkt. Release-Features sind GROSS und benötigen detaillierte Erklärung!"
            else:
                detail_instruction = f"\n\n⚠️ There are {num_commits} commits. Group related commits (e.g., all commits for the same feature) into ONE detailed feature point. Release features are MAJOR and need detailed explanation!"

        # Build prompt based on language
        strict_rules = ""
        if strict:
            min_bullets = max(3, len(commits))
            min_chars = 400 if len(commits) > 1 else 250
            if language == 'de':
                strict_rules = (
                    "\n\nSTRICTE QUALITAETSREGELN:\n"
                    f"- Nutze mindestens {min_bullets} Bulletpoints (bei vorhandenen Detail-Infos).\n"
                    f"- Ziel: mindestens {min_chars} Zeichen, wenn Commit-Body oder Diff Details enthalten.\n"
                    "- Erzeuge KEINE Einzeiler-Ausgabe.\n"
                )
            else:
                strict_rules = (
                    "\n\nSTRICT QUALITY RULES:\n"
                    f"- Use at least {min_bullets} bullet points when detailed info exists.\n"
                    f"- Target at least {min_chars} characters if commit body or diff includes details.\n"
                    "- Do NOT return a single-line answer.\n"
                )

        if language == 'de':
            prompt = f"""Du bist ein professioneller Technical Writer. Erstelle benutzerfreundliche Patch Notes für das Projekt "{repo_name}".

SICHERHEITSREGELN (STRIKT — bei Verstoß wird der gesamte Output verworfen):
- NIEMALS Dateipfade, Server-Pfade oder Verzeichnisstrukturen erwähnen
- NIEMALS IP-Adressen, Ports oder Netzwerk-Konfigurationen nennen
- Security-Fixes NUR vage beschreiben: WAS verbessert wurde, nicht WIE oder welche Schwachstelle
- KEINE alten verwundbaren Dependency-Versionen nennen (nur die neue Version, wenn relevant)
- KEINE Config-Dateien, deren Pfade oder Inhalte referenzieren
- KEINE internen Methodennamen, Klassennamen oder Code-Strukturen offenlegen

COMMITS (VOLLSTÄNDIGE LISTE):
{commits_text}
{extra_context}

KRITISCHE REGELN:
⚠️ BESCHREIBE NUR ÄNDERUNGEN DIE WIRKLICH IN DEN COMMITS OBEN STEHEN!
⚠️ ERFINDE KEINE FEATURES ODER FIXES DIE NICHT IN DER COMMIT-LISTE SIND!
⚠️ Wenn ein Commit unklar ist, überspringe ihn lieber als zu raten!
⚠️ Nutze CHANGELOG INFORMATION und CODE CHANGES (falls vorhanden) für Details.
⚠️ Wenn Texte "offen", "todo", "still open" oder "risiken" nennen, markiere sie NICHT als abgeschlossen.

COMMIT-TYP-TAGS — SO INTERPRETIERST DU SIE:
📋 Jeder Commit hat einen Tag in eckigen Klammern. Beachte diese STRIKT:
- [FEATURE] = Tatsächlich implementiertes neues Feature → als "Neues Feature" auflisten
- [BUGFIX] = Tatsächlich behobener Bug → als "Bugfix" auflisten
- [IMPROVEMENT] / [REFACTOR] / [PERFORMANCE] = Verbesserung → als "Verbesserung" auflisten
- [DOCS] = Reine Dokumentationsänderung → nur erwähnen wenn für Nutzer relevant, KEIN Feature!
- [DESIGN-DOC: GEPLANT, NICHT IMPLEMENTIERT] = Design-Dokument für ZUKÜNFTIGES Feature!
  → NIEMALS als implementiertes Feature auflisten!
  → Höchstens kurz unter "📋 Geplant" erwähnen oder ganz weglassen
  → Der Body wurde absichtlich entfernt — die Details sind NICHT implementiert!
- [SEO-AUTO] = Automatisierte SEO-Optimierungen → kurz als "SEO-Verbesserungen" zusammenfassen
- [CHORE] / [CI] / [BUILD] / [TEST] / [STYLE] = Interne Wartung → nur erwähnen wenn nutzerrelevant
- [MERGE] = Ignorieren

WICHTIG - ZUSAMMENHÄNGENDE FEATURES ERKENNEN:
🔍 Suche nach VERWANDTEN Commits die zusammengehören (z.B. mehrere [BUGFIX] Commits für das gleiche Feature)
🔍 Release-Commits (feat: Release v...) enthalten oft GROSSE Änderungen - beschreibe diese DETAILLIERT!
🔍 Commit-Serien mit gleichem Feature-Namen sind EINZELNE Features, nicht getrennte Punkte!
🔍 Bei großen Refactorings: Erkenne die GESAMTBEDEUTUNG, nicht nur Einzelschritte!
🔍 Wenn Commit-Bodies Abschnitte enthalten (z.B. "Rate Limiting:", "Monitoring:"), nutze pro Abschnitt einen Bulletpoint.
🔍 Bei reinen Doku-/Status-Updates: als Status-Update zusammenfassen, keine Features erfinden.

AUFGABE:
Fasse diese Commits zu professionellen, DETAILLIERTEN Patch Notes zusammen:{detail_instruction}{strict_rules}

1. GRUPPIERE verwandte Commits zu EINEM ausführlichen Bulletpoint
2. Kategorisiere in: 🆕 Neue Features, 🐛 Bugfixes, ⚡ Verbesserungen
3. Verwende einfache, klare Sprache aber sei AUSFÜHRLICH
4. Beginne mit Nutzer-Nutzen, danach technische Details
5. Bei großen Features: 3-5 Sätze oder Sub-Bulletpoints mit Details
6. Entferne Jargon und technische Präfixe
7. Zielgruppe: Endkunden die verstehen wollen was sich verbessert hat
8. Maximal 8000 Zeichen - nutze den Platz aus!
9. Erfinde keine Details und verwende keine Beispiel-Formulierungen aus dem Prompt.

FORMAT:
Verwende Markdown mit ** für Kategorien und • für Hauptpunkte.
Bei komplexen Features: Nutze Sub-Bulletpoints (Einrückung mit 2 Leerzeichen).

FORMAT-BEISPIEL:
**🆕 Neue Features:**
• **Feature-Name**: Detaillierte Beschreibung was das Feature macht und warum es wichtig ist.
  - Erster Nutzen oder technisches Detail
  - Zweiter Nutzen oder technisches Detail
  - Dritter Nutzen oder technisches Detail

**🐛 Bugfixes:**
• **Bug-Kategorie**: Was wurde gefixt und welches Problem hatte es verursacht

**⚡ Verbesserungen:**
• **Verbesserung**: Detaillierte Beschreibung der Verbesserung

Erstelle JETZT die DETAILLIERTEN Patch Notes basierend auf den ECHTEN Commits oben (nur die Kategorien + Bulletpoints, keine Einleitung):"""
        else:  # English
            prompt = f"""You are a professional Technical Writer. Create user-friendly patch notes for the project "{repo_name}".

SECURITY RULES (STRICT — violation causes the entire output to be discarded):
- NEVER mention file paths, server paths or directory structures
- NEVER mention IP addresses, ports or network configurations
- Describe security fixes ONLY vaguely: WHAT was improved, not HOW or which vulnerability
- Do NOT mention old vulnerable dependency versions (only the new version, if relevant)
- Do NOT reference config files, their paths or contents
- Do NOT expose internal method names, class names or code structures

COMMITS (COMPLETE LIST):
{commits_text}
{extra_context}

CRITICAL RULES:
⚠️ ONLY DESCRIBE CHANGES THAT ARE ACTUALLY IN THE COMMITS ABOVE!
⚠️ NEVER INVENT FEATURES OR FIXES THAT ARE NOT IN THE COMMIT LIST!
⚠️ If a commit is unclear, skip it rather than guessing!
⚠️ Use CHANGELOG INFORMATION and CODE CHANGES (if present) for details.
⚠️ If text says "open", "todo", "still open", or "risks", do NOT mark it as completed.

COMMIT TYPE TAGS — HOW TO INTERPRET THEM:
📋 Each commit has a tag in square brackets. STRICTLY observe:
- [FEATURE] = Actually implemented new feature → list as "New Feature"
- [BUGFIX] = Actually fixed bug → list as "Bug Fix"
- [IMPROVEMENT] / [REFACTOR] / [PERFORMANCE] = Improvement → list as "Improvement"
- [DOCS] = Documentation change only → mention only if user-relevant, NOT a feature!
- [DESIGN-DOC: GEPLANT, NICHT IMPLEMENTIERT] = Planning document for a FUTURE feature!
  → NEVER list as an implemented feature!
  → At most briefly mention under "📋 Planned" or omit entirely
  → The body was intentionally removed — the details are NOT implemented!
- [SEO-AUTO] = Automated SEO optimizations → briefly summarize as "SEO improvements"
- [CHORE] / [CI] / [BUILD] / [TEST] / [STYLE] = Internal maintenance → mention only if user-relevant
- [MERGE] = Ignore

IMPORTANT - RECOGNIZE RELATED FEATURES:
🔍 Look for RELATED commits that belong together (e.g., multiple [BUGFIX] commits for the same feature)
🔍 Release commits (feat: Release v...) often contain MAJOR changes - describe these in DETAIL!
🔍 Commit series with the same feature name are SINGLE features, not separate items!
🔍 For large refactorings: Recognize the OVERALL SIGNIFICANCE, not just individual steps!
🔍 If commit bodies include sections (e.g., "Rate Limiting:"), use one bullet per section.
🔍 For doc/status-only updates: summarize as status updates; do not invent features.

TASK:
Summarize these commits into professional, DETAILED patch notes:{detail_instruction}{strict_rules}

1. GROUP related commits into ONE comprehensive bulletpoint
2. Categorize into: 🆕 New Features, 🐛 Bug Fixes, ⚡ Improvements
3. Use simple, clear language but be COMPREHENSIVE
4. Lead with user impact, then technical details
5. For major features: 3-5 sentences or sub-bulletpoints with details
6. Remove jargon and technical prefixes
7. Target audience: End customers who want to understand what improved
8. Maximum 8000 characters - use the space!
9. Do not invent details and do not reuse example wording from the prompt.

FORMAT:
Use Markdown with ** for categories and • for main points.
For complex features: Use sub-bulletpoints (indented with 2 spaces).

FORMAT EXAMPLE:
**🆕 New Features:**
• **Feature Name**: Detailed description of what the feature does and why it's important.
  - First benefit or technical detail
  - Second benefit or technical detail
  - Third benefit or technical detail

**🐛 Bug Fixes:**
• **Bug Category**: What was fixed and what problem it caused

**⚡ Improvements:**
• **Improvement**: Detailed description of the improvement

Create the DETAILED patch notes NOW based on the REAL commits above (only categories + bulletpoints, no introduction):"""

        return prompt
