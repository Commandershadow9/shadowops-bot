"""Stufe 1: Collect — Commits + PR-Daten + Git-Stats anreichern."""

import logging
import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from patch_notes.context import PipelineContext

logger = logging.getLogger('shadowops')

# Noise-Patterns die aus Commit-Bodies entfernt werden
_BODY_NOISE_RE = re.compile(
    r'(?:Co-[Aa]uthored-[Bb]y|Signed-off-by|Reviewed-by|Acked-by):.*$',
    re.MULTILINE,
)


async def collect(ctx: 'PipelineContext', bot=None) -> None:
    """Stufe 1: Commits sammeln, anreichern, Git-Stats.

    Wenn raw_commits leer sind (z.B. nach Restart), holt diese Stufe
    ALLE Commits seit dem letzten Release direkt aus Git.
    So gehen nie Commits verloren — egal ob Webhook, Polling oder Restart.
    """
    project_config = ctx.project_config
    project_path = project_config.get('path', '')

    # Selbst-Heilung: Wenn keine Commits übergeben wurden,
    # alle seit dem letzten Release aus Git holen
    if not ctx.raw_commits and project_path:
        ctx.raw_commits = _gather_commits_since_last_release(ctx.project, project_path, project_config)
        if ctx.raw_commits:
            logger.info(
                f"[v6] {ctx.project}: {len(ctx.raw_commits)} Commits aus Git geholt "
                f"(seit letztem Release)"
            )

    enriched = []
    for commit in ctx.raw_commits:
        enriched_commit = dict(commit)

        # Body-Noise entfernen
        msg = enriched_commit.get('message', '')
        enriched_commit['message'] = _BODY_NOISE_RE.sub('', msg).strip()

        enriched.append(enriched_commit)

    # PR-Daten anreichern (wenn gh verfügbar und Projekt-Pfad gesetzt)
    if project_path:
        await _enrich_with_pr_data(enriched, project_path, project_config)

    # Git-Stats sammeln
    if project_path:
        ctx.git_stats = _collect_git_stats(enriched, project_path)

    ctx.enriched_commits = enriched


def _gather_commits_since_last_release(project: str, project_path: str, config: dict) -> list[dict]:
    """Hole ALLE Commits seit dem letzten Release aus Git.

    Nutzt die Changelog-DB als Referenz: Letzter Release → SHA finden → git log.
    Fallback: Letzten bekannten Tag oder letzte 30 Tage.
    """
    import json as _json

    deploy_branch = config.get('deploy', {}).get('branch', 'main')

    # 1. Letzten Release-Zeitpunkt aus Changelog-DB
    last_release_date = _get_last_release_date(project)

    # 2. Git fetch (damit origin/main aktuell ist)
    try:
        subprocess.run(
            ['git', 'fetch', 'origin', deploy_branch],
            capture_output=True, text=True, timeout=30, cwd=project_path,
        )
    except Exception:
        pass

    # 3. Commits seit letztem Release
    git_args = [
        'git', 'log', f'origin/{deploy_branch}',
        '--format=%H|%s|%an|%b',
        '--no-merges',
    ]
    if last_release_date:
        git_args.append(f'--since={last_release_date}')
    else:
        # Fallback: Letzte 30 Tage
        git_args.append('--since=30 days ago')

    try:
        result = subprocess.run(
            git_args, capture_output=True, text=True,
            timeout=15, cwd=project_path,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return []

        commits = []
        for line in result.stdout.strip().split('\n'):
            if not line.strip():
                continue
            parts = line.split('|', 3)
            if len(parts) < 3:
                continue
            body = parts[3].strip() if len(parts) > 3 else ''
            message = parts[1].strip()
            if body:
                message = f"{message}\n\n{body}"
            commits.append({
                'sha': parts[0].strip(),
                'message': message,
                'author': {'name': parts[2].strip()},
            })

        return commits
    except Exception as e:
        logger.warning(f"[v6] Git-Commits-Sammlung fehlgeschlagen für {project}: {e}")
        return []


def _get_last_release_date(project: str) -> str | None:
    """Hole Datum des letzten Releases aus Changelog-DB."""
    try:
        import sqlite3
        db_path = Path(__file__).resolve().parent.parent.parent.parent / 'data' / 'changelogs.db'
        if not db_path.exists():
            return None
        with sqlite3.connect(str(db_path)) as conn:
            row = conn.execute(
                "SELECT created_at FROM changelogs "
                "WHERE project = ? ORDER BY created_at DESC LIMIT 1",
                (project,),
            ).fetchone()
        return row[0] if row else None
    except Exception:
        return None


async def _enrich_with_pr_data(commits: list[dict], project_path: str,
                                project_config: dict) -> None:
    """Enriche Commits mit PR-Labels und Bodies via gh CLI."""
    repo = project_config.get('repo', '')
    if not repo:
        return

    for commit in commits:
        sha = commit.get('sha', '')
        if not sha:
            continue

        try:
            result = subprocess.run(
                ['gh', 'pr', 'list', '--search', sha, '--json', 'labels,body',
                 '--repo', repo, '--limit', '1'],
                capture_output=True, text=True, timeout=10, cwd=project_path,
            )
            if result.returncode == 0 and result.stdout.strip():
                import json
                prs = json.loads(result.stdout)
                if prs:
                    pr = prs[0]
                    commit['pr_labels'] = [
                        l.get('name', '') for l in pr.get('labels', [])
                    ]
                    body = pr.get('body', '') or ''
                    # Body kürzen (max 500 Zeichen)
                    commit['pr_body'] = body[:500] if body else ''
        except Exception as e:
            logger.debug(f"PR-Daten für {sha[:8]} nicht verfügbar: {e}")


_FILE_CATEGORIES = {
    'Frontend': [r'\.tsx?$', r'\.jsx?$', r'\.css$', r'\.scss$', r'\.vue$', r'components/', r'pages/', r'app/'],
    'Backend': [r'\.go$', r'\.py$', r'handlers/', r'services/', r'api/', r'server/', r'src/'],
    'Datenbank': [r'migration', r'\.sql$', r'schema', r'prisma/', r'models/'],
    'Config': [r'\.ya?ml$', r'\.toml$', r'\.env', r'\.json$', r'config/', r'\.conf$'],
    'Tests': [r'test', r'spec\.', r'__tests__/', r'_test\.go$', r'\.test\.'],
    'Dokumentation': [r'\.md$', r'docs/', r'README', r'CHANGELOG'],
    'CI/CD': [r'\.github/', r'Dockerfile', r'docker-compose', r'\.gitlab-ci', r'deploy/'],
    'Dependencies': [r'go\.(mod|sum)$', r'requirements', r'package\.json$', r'Pipfile', r'poetry\.lock'],
}


def _categorize_file(filepath: str) -> str:
    """Ordne eine Datei einer Kategorie zu."""
    for category, patterns in _FILE_CATEGORIES.items():
        for pattern in patterns:
            if re.search(pattern, filepath, re.IGNORECASE):
                return category
    return 'Sonstiges'


def _collect_git_stats(commits: list[dict], project_path: str) -> dict:
    """Sammle Git-Stats (Dateien, Zeilen) + kategorisierte Dateianalyse."""
    if not commits:
        return {}

    shas = [c.get('sha', '') for c in commits if c.get('sha')]
    if not shas:
        return {}

    try:
        oldest = shas[-1]
        newest = shas[0]
        diff_range = f'{oldest}^..{newest}'
        result = subprocess.run(
            ['git', 'diff', '--stat', '--stat-width=120', diff_range],
            capture_output=True, text=True, timeout=15, cwd=project_path,
        )
        if result.returncode != 0:
            return {"commits": len(commits)}

        lines = result.stdout.strip().split('\n')
        summary_line = lines[-1] if lines else ''
        stats = {"commits": len(commits)}

        files_m = re.search(r'(\d+) files? changed', summary_line)
        ins_m = re.search(r'(\d+) insertions?\(\+\)', summary_line)
        del_m = re.search(r'(\d+) deletions?\(-\)', summary_line)

        if files_m:
            stats['files_changed'] = int(files_m.group(1))
        if ins_m:
            stats['lines_added'] = int(ins_m.group(1))
        if del_m:
            stats['lines_removed'] = int(del_m.group(1))

        # Kategorisierte Dateianalyse
        categories: dict[str, list[str]] = {}
        for line in lines[:-1]:  # Letzte Zeile ist Summary
            match = re.match(r'\s*(.+?)\s*\|', line)
            if not match:
                continue
            filepath = match.group(1).strip()
            cat = _categorize_file(filepath)
            categories.setdefault(cat, []).append(filepath)

        if categories:
            stats['categories'] = {
                cat: len(files) for cat, files in
                sorted(categories.items(), key=lambda x: len(x[1]), reverse=True)
            }

        # Neue + gelöschte Dateien
        for filter_type, key in [('A', 'new_files'), ('D', 'deleted_files')]:
            try:
                r = subprocess.run(
                    ['git', 'diff', f'--diff-filter={filter_type}', '--name-only', diff_range],
                    capture_output=True, text=True, timeout=10, cwd=project_path,
                )
                if r.returncode == 0 and r.stdout.strip():
                    stats[key] = len(r.stdout.strip().splitlines())
            except Exception:
                pass

        # Autoren zählen
        authors = set()
        for c in commits:
            author = c.get('author', {})
            if isinstance(author, dict):
                name = author.get('name', author.get('username', ''))
            elif isinstance(author, str):
                name = author
            else:
                name = ''
            if name:
                authors.add(name)
        if authors:
            stats['authors'] = list(authors)

        return stats
    except Exception as e:
        logger.debug(f"Git-Stats Fehler: {e}")
        return {"commits": len(commits)}
