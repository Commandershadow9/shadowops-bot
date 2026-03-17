#!/usr/bin/env python3
"""
Migration: PostgreSQL Changelogs → Zentrale SQLite ChangelogDB.

Liest bestehende Einträge aus ZERODOX + GuildScout PostgreSQL-Datenbanken
und schreibt sie in die zentrale Changelog-Datenbank (SQLite) des ShadowOps Bot.

Nutzung:
    python3 scripts/migrate_changelogs.py [--db-path data/changelogs.db]
"""

import asyncio
import json
import sys
import os
from pathlib import Path

# Projekt-Root zum Path hinzufügen
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncpg


# Projekt-Konfigurationen für die Migration
PROJECTS = [
    {
        "name": "zerodox",
        "host": "127.0.0.1",
        "port": 5434,
        "user": "zerodox",
        "password_env": "ZERODOX_DB_PASSWORD",
        "database": "zerodox",
    },
    {
        "name": "guildscout",
        "host": "127.0.0.1",
        "port": 5433,
        "user": "guildscout",
        "password_env": "GUILDSCOUT_DB_PASSWORD",
        "database": "guildscout",
    },
]


async def fetch_changelogs(project_config: dict) -> list[dict]:
    """Holt alle Changelogs aus einer PostgreSQL-Datenbank."""
    name = project_config["name"]
    password = os.environ.get(project_config["password_env"])
    if not password:
        print(f"   ❌ Umgebungsvariable {project_config['password_env']} nicht gesetzt. "
              f"Bitte vor dem Ausführen exportieren.")
        return []

    try:
        conn = await asyncpg.connect(
            host=project_config["host"],
            port=project_config["port"],
            user=project_config["user"],
            password=password,
            database=project_config["database"],
        )
    except Exception as e:
        print(f"   ❌ Verbindung zu {name} fehlgeschlagen: {e}")
        return []

    # Prüfen ob changelogs-Tabelle existiert
    exists = await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name = 'changelogs')"
    )
    if not exists:
        print(f"   ⚠️  Keine changelogs-Tabelle in {name}")
        await conn.close()
        return []

    rows = await conn.fetch(
        "SELECT version, title, tldr, content, stats, seo, language, "
        "published_at, created_at FROM changelogs ORDER BY published_at"
    )

    results = []
    for row in rows:
        stats = row["stats"] or {}
        seo = row["seo"] or {}

        # JSONB kommt als dict zurück, kein Parsing nötig
        if isinstance(stats, str):
            stats = json.loads(stats)
        if isinstance(seo, str):
            seo = json.loads(seo)

        # SEO-Keywords: seo.keywords (Liste) oder leer
        seo_keywords = seo.get("keywords", [])

        # SEO-Description: meta_description bevorzugt, Fallback og_description
        seo_description = (
            seo.get("meta_description", "") or seo.get("og_description", "")
        )

        entry = {
            "project": name,
            "version": row["version"],
            "title": row["title"],
            "tldr": row["tldr"] or "",
            "content": row["content"] or "",
            "changes": [],  # PG hat keine strukturierten Changes
            "stats": stats,
            "seo_keywords": seo_keywords,
            "seo_description": seo_description,
            "language": row["language"] or "de",
            "published_at": row["published_at"].isoformat(),
        }
        results.append(entry)

    await conn.close()
    return results


async def main():
    from src.integrations.changelog_db import ChangelogDB

    db_path = "data/changelogs.db"
    if "--db-path" in sys.argv:
        idx = sys.argv.index("--db-path")
        db_path = sys.argv[idx + 1]

    print(f"📋 Zentrale ChangelogDB: {db_path}")

    db = ChangelogDB(db_path=db_path)
    await db.initialize()

    total_migrated = 0

    for project_config in PROJECTS:
        name = project_config["name"]
        port = project_config["port"]
        print(f"\n🔍 Lese {name}-Changelogs aus PostgreSQL (Port {port})...")

        entries = await fetch_changelogs(project_config)
        print(f"   Gefunden: {len(entries)} Einträge")

        for entry in entries:
            await db.upsert(entry)
            print(f"   ✅ {entry['project']} v{entry['version']}: {entry['title']}")
            total_migrated += 1

    # Verifizieren
    print(f"\n{'='*60}")
    print(f"📊 Verifikation:")
    projects = await db.list_all_projects()
    for project in projects:
        result = await db.list_by_project(project, page=1, limit=100)
        print(f"   {project}: {result['meta']['total']} Einträge")

    await db.close()
    print(f"\n✅ Migration abgeschlossen! {total_migrated} Einträge insgesamt.")


if __name__ == "__main__":
    asyncio.run(main())
