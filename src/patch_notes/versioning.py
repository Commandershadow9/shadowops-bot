"""Versionierung — EINE Quelle: Changelog-DB + SemVer."""
import logging
import re
import sqlite3
from pathlib import Path

logger = logging.getLogger('shadowops')

_DEFAULT_DB = Path(__file__).resolve().parent.parent.parent / 'data' / 'changelogs.db'


def get_last_db_version(project: str, db_path: Path = _DEFAULT_DB) -> str | None:
    """Letzte SemVer-Version aus Changelog-DB. Einzige Quelle."""
    if not db_path.exists():
        return None
    try:
        with sqlite3.connect(str(db_path)) as conn:
            row = conn.execute(
                "SELECT version FROM changelogs "
                "WHERE project = ? AND version NOT LIKE 'patch.%' "
                "ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (project,),
            ).fetchone()
        if row and re.match(r'^\d+\.\d+\.\d+$', row[0]):
            return row[0]
    except Exception as e:
        logger.warning(f"Version-DB-Fehler für {project}: {e}")
    return None


def ensure_unique(version: str, project: str, db_path: Path = _DEFAULT_DB) -> str:
    """Stelle sicher dass Version noch nicht in DB existiert."""
    if not db_path.exists():
        return version
    try:
        with sqlite3.connect(str(db_path)) as conn:
            existing = {
                r[0] for r in conn.execute(
                    "SELECT version FROM changelogs WHERE project = ?", (project,)
                ).fetchall()
            }
    except Exception:
        return version

    if version not in existing:
        return version

    parts = version.split('.')
    major, minor = int(parts[0]), int(parts[1])
    patch = int(parts[2])
    for _ in range(100):
        patch += 1
        candidate = f"{major}.{minor}.{patch}"
        if candidate not in existing:
            return candidate
    return version


def calculate_version(project: str, groups: list[dict], db_path: Path = _DEFAULT_DB) -> tuple[str, str]:
    """Berechne nächste Version. NUR aus Changelog-DB + Commit-Typen."""
    last = get_last_db_version(project, db_path)
    if not last:
        return ("0.1.0", "fallback")

    has_breaking = any(g.get("tag") == "BREAKING" for g in groups)
    has_feature = any(g.get("tag") == "FEATURE" for g in groups)

    parts = last.split('.')
    major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])

    if has_breaking:
        new = f"{major + 1}.0.0"
    elif has_feature:
        new = f"{major}.{minor + 1}.0"
    else:
        new = f"{major}.{minor}.{patch + 1}"

    return (ensure_unique(new, project, db_path), "semver")
