"""Stufe 2: Classify — Gruppierung + Version + Credits + Update-Größe."""

from __future__ import annotations

import logging
from collections import Counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from patch_notes.context import PipelineContext

logger = logging.getLogger('shadowops')

# Git-Autoren die NICHT als eigenständige Credits erscheinen
_AI_AUTHORS = {
    'claude', 'claude opus', 'claude sonnet', 'claude haiku',
    'github-actions', 'github-actions[bot]', 'dependabot', 'dependabot[bot]',
    'noreply', 'copilot',
}

TEAM_MAPPING: dict[str, tuple[str, str]] = {
    'commandershadow9': ('Shadow', 'Founder & Lead Dev'),
    'cmdshadow': ('Shadow', 'Founder & Lead Dev'),
    'shadow': ('Shadow', 'Founder & Lead Dev'),
    'renjihoshida': ('Mapu', 'Co-Founder & Dev'),
    'mapu': ('Mapu', 'Co-Founder & Dev'),
    'commandershadow': ('Shadow', 'Founder & Lead Dev'),
}


async def classify(ctx: PipelineContext, bot=None) -> None:
    """Stufe 2: Gruppierung + Version + Credits + Update-Größe."""
    from patch_notes.grouping import group_commits
    from patch_notes.versioning import calculate_version

    commits = ctx.enriched_commits or ctx.raw_commits

    # 1. Commits gruppieren (ALLE — kein Cap!)
    ctx.groups = group_commits(commits)
    logger.info(
        f"[v6] {ctx.project}: {len(commits)} Commits → {len(ctx.groups)} Gruppen "
        f"({sum(1 for g in ctx.groups if g.get('is_player_facing'))} player-facing)"
    )

    # 2. Version berechnen (DB-basiert, EINMAL)
    ctx.version, ctx.version_source = calculate_version(ctx.project, ctx.groups)
    logger.info(f"[v6] {ctx.project}: Version {ctx.version} (source: {ctx.version_source})")

    # 3. Update-Größe bestimmen
    total = len(commits)
    feature_groups = [g for g in ctx.groups if g.get('tag') == 'FEATURE']
    if total >= 60 or len(feature_groups) >= 5:
        ctx.update_size = "major"
    elif total >= 30:
        ctx.update_size = "big"
    elif total < 5:
        ctx.update_size = "small"
    else:
        ctx.update_size = "normal"

    # 4. Team-Credits extrahieren
    ctx.team_credits = _extract_credits(commits)

    # 5. Vorherige Version laden (Duplikat-Guard)
    ctx.previous_version_content = _load_previous_content(ctx.project)


def _extract_credits(commits: list[dict]) -> list[dict]:
    """Extrahiere Team-Credits aus Git-Autoren."""
    author_counts: Counter = Counter()
    for c in commits:
        author = c.get('author', {})
        if isinstance(author, dict):
            name = author.get('name', author.get('username', ''))
        elif isinstance(author, str):
            name = author
        else:
            continue
        if name and name.lower().strip() not in _AI_AUTHORS:
            author_counts[name] += 1

    credits = []
    for author_name, count in author_counts.most_common():
        team_info = TEAM_MAPPING.get(author_name.lower().strip())
        if team_info:
            display, role = team_info
        else:
            display, role = author_name, "Contributor"
        existing = next((c for c in credits if c['name'] == display), None)
        if existing:
            existing['commits'] += count
        else:
            credits.append({'name': display, 'role': role, 'commits': count})

    return credits


def _load_previous_content(project: str) -> str:
    """Lade TL;DR der vorherigen Version (Duplikat-Guard)."""
    try:
        import sqlite3
        from pathlib import Path
        db_path = Path(__file__).resolve().parent.parent.parent.parent / 'data' / 'changelogs.db'
        if not db_path.exists():
            return ""
        with sqlite3.connect(str(db_path)) as conn:
            row = conn.execute(
                "SELECT tldr, title FROM changelogs "
                "WHERE project = ? ORDER BY created_at DESC LIMIT 1",
                (project,),
            ).fetchone()
        if row:
            return f"Titel: {row[1]}\nTL;DR: {row[0]}"
    except Exception:
        pass
    return ""
