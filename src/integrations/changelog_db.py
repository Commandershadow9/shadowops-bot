"""
Zentrale Changelog-Datenbank fuer alle Projekte (Patch Notes v3)

Speichert Changelogs aller Projekte in einer SQLite-Datenbank
mit Paginierung, JSON-Feldern und Upsert-Logik.
"""

import json
import math
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any

import aiosqlite

logger = logging.getLogger('shadowops')


class ChangelogDB:
    """
    Async SQLite-Datenbank fuer zentrale Changelog-Verwaltung.

    Speichert Changelogs aller Projekte (GuildScout, ZERODOX, ShadowOps, etc.)
    mit strukturierten JSON-Feldern fuer Changes, Stats und SEO-Daten.
    """

    # Felder die als JSON gespeichert/geladen werden
    _JSON_FIELDS = ('changes', 'stats', 'seo_keywords')

    def __init__(self, db_path: Optional[str] = None):
        """
        Initialisiert die Changelog-DB.

        Args:
            db_path: Pfad zur SQLite-Datenbank. Default: data/changelogs.db
        """
        self.db_path = Path(db_path or "data/changelogs.db")
        self._db: Optional[aiosqlite.Connection] = None

    async def initialize(self) -> None:
        """Erstellt die Datenbank und das Schema falls noetig."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._db = await aiosqlite.connect(str(self.db_path))
        self._db.row_factory = aiosqlite.Row

        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS changelogs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project TEXT NOT NULL,
                version TEXT NOT NULL,
                title TEXT NOT NULL,
                tldr TEXT DEFAULT '',
                content TEXT DEFAULT '',
                changes TEXT DEFAULT '[]',
                stats TEXT DEFAULT '{}',
                seo_keywords TEXT DEFAULT '[]',
                seo_description TEXT DEFAULT '',
                language TEXT DEFAULT 'de',
                published_at TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(project, version)
            )
        """)

        await self._db.execute("""
            CREATE INDEX IF NOT EXISTS idx_changelogs_project_published
            ON changelogs (project, published_at DESC)
        """)

        await self._db.commit()
        logger.info(f"📋 Changelog-DB initialisiert: {self.db_path}")

    async def close(self) -> None:
        """Schliesst die Datenbankverbindung."""
        if self._db:
            await self._db.close()
            self._db = None

    async def upsert(self, entry: Dict[str, Any]) -> None:
        """
        Erstellt oder aktualisiert einen Changelog-Eintrag.

        Bei gleichem project+version wird der bestehende Eintrag aktualisiert.

        Args:
            entry: Dict mit mindestens project, version, title, published_at.
                   Optionale Felder: tldr, content, changes, stats,
                   seo_keywords, seo_description, language.
        """
        # JSON-Felder serialisieren
        changes = json.dumps(entry.get("changes", []), ensure_ascii=False)
        stats = json.dumps(entry.get("stats", {}), ensure_ascii=False)
        seo_keywords = json.dumps(entry.get("seo_keywords", []), ensure_ascii=False)

        await self._db.execute(
            """
            INSERT INTO changelogs
                (project, version, title, tldr, content, changes, stats,
                 seo_keywords, seo_description, language, published_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project, version) DO UPDATE SET
                title = excluded.title,
                tldr = excluded.tldr,
                content = excluded.content,
                changes = excluded.changes,
                stats = excluded.stats,
                seo_keywords = excluded.seo_keywords,
                seo_description = excluded.seo_description,
                language = excluded.language,
                published_at = excluded.published_at
            """,
            (
                entry["project"],
                entry["version"],
                entry["title"],
                entry.get("tldr", ""),
                entry.get("content", ""),
                changes,
                stats,
                seo_keywords,
                entry.get("seo_description", ""),
                entry.get("language", "de"),
                entry["published_at"],
            ),
        )
        await self._db.commit()

    async def get(self, project: str, version: str) -> Optional[Dict[str, Any]]:
        """
        Holt einen einzelnen Changelog-Eintrag.

        Args:
            project: Projektname (z.B. 'guildscout')
            version: Versionsnummer (z.B. '2.5.0')

        Returns:
            Dict mit allen Feldern oder None wenn nicht gefunden.
        """
        cursor = await self._db.execute(
            "SELECT * FROM changelogs WHERE project = ? AND version = ?",
            (project, version),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_dict(row)

    async def list_by_project(
        self, project: str, page: int = 1, limit: int = 10
    ) -> Dict[str, Any]:
        """
        Listet Changelogs eines Projekts paginiert auf (neueste zuerst).

        Args:
            project: Projektname
            page: Seitennummer (ab 1)
            limit: Eintraege pro Seite

        Returns:
            Dict mit 'data' (Liste) und 'meta' (Paginierung).
        """
        # Gesamtanzahl ermitteln
        cursor = await self._db.execute(
            "SELECT COUNT(*) FROM changelogs WHERE project = ?",
            (project,),
        )
        row = await cursor.fetchone()
        total = row[0]

        total_pages = math.ceil(total / limit) if total > 0 else 0
        offset = (page - 1) * limit

        # Eintraege holen
        cursor = await self._db.execute(
            """
            SELECT * FROM changelogs
            WHERE project = ?
            ORDER BY published_at DESC
            LIMIT ? OFFSET ?
            """,
            (project, limit, offset),
        )
        rows = await cursor.fetchall()

        return {
            "data": [self._row_to_dict(r) for r in rows],
            "meta": {
                "page": page,
                "per_page": limit,
                "total": total,
                "total_pages": total_pages,
            },
        }

    async def list_all_projects(self) -> List[str]:
        """
        Gibt alle eindeutigen Projektnamen zurueck.

        Returns:
            Sortierte Liste aller Projektnamen.
        """
        cursor = await self._db.execute(
            "SELECT DISTINCT project FROM changelogs ORDER BY project"
        )
        rows = await cursor.fetchall()
        return [row[0] for row in rows]

    def _row_to_dict(self, row: aiosqlite.Row) -> Dict[str, Any]:
        """
        Konvertiert eine DB-Row in ein Dict und deserialisiert JSON-Felder.

        Args:
            row: aiosqlite.Row aus der Datenbank.

        Returns:
            Dict mit deserialisierten JSON-Feldern.
        """
        result = dict(row)
        for field in self._JSON_FIELDS:
            if field in result and isinstance(result[field], str):
                try:
                    result[field] = json.loads(result[field])
                except (json.JSONDecodeError, TypeError):
                    # Fallback: leere Struktur
                    if field == 'stats':
                        result[field] = {}
                    else:
                        result[field] = []
        return result


def get_changelog_db(db_path: Optional[str] = None) -> ChangelogDB:
    """
    Factory-Funktion fuer die Changelog-DB.

    Args:
        db_path: Optionaler Pfad zur Datenbank.

    Returns:
        Neue ChangelogDB-Instanz (initialize() muss noch aufgerufen werden).
    """
    return ChangelogDB(db_path=db_path)
