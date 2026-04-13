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
    """Stufe 1: Enriche Commits mit PR-Daten und Git-Stats."""
    project_config = ctx.project_config
    project_path = project_config.get('path', '')

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


def _collect_git_stats(commits: list[dict], project_path: str) -> dict:
    """Sammle Git-Stats (Dateien, Zeilen) für den Commit-Bereich."""
    if not commits:
        return {}

    shas = [c.get('sha', '') for c in commits if c.get('sha')]
    if not shas:
        return {}

    try:
        # Ältester und neuester Commit
        oldest = shas[-1]
        newest = shas[0]
        result = subprocess.run(
            ['git', 'diff', '--stat', f'{oldest}^..{newest}'],
            capture_output=True, text=True, timeout=15, cwd=project_path,
        )
        if result.returncode != 0:
            return {"commits": len(commits)}

        lines = result.stdout.strip().split('\n')
        # Letzte Zeile: "N files changed, X insertions(+), Y deletions(-)"
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
