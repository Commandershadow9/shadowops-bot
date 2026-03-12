"""
Tests fuer die zentrale Changelog-Datenbank (Task 1 — Patch Notes v3)
"""

import pytest
import json
from datetime import datetime, timezone


@pytest.fixture
async def changelog_db(temp_dir):
    """Erstellt eine ChangelogDB-Instanz mit temporaerem Pfad."""
    from src.integrations.changelog_db import ChangelogDB

    db_path = str(temp_dir / "test_changelogs.db")
    db = ChangelogDB(db_path=db_path)
    await db.initialize()
    yield db
    await db.close()


@pytest.fixture
def sample_entry():
    """Beispiel-Changelog-Eintrag."""
    return {
        "project": "guildscout",
        "version": "2.5.0",
        "title": "GuildScout v2.5.0 — Performance Update",
        "tldr": "Schnellere API-Antworten und neue Filter",
        "content": "Dieses Update bringt signifikante Performance-Verbesserungen.",
        "changes": [
            {"type": "feat", "text": "Neuer Gilden-Filter"},
            {"type": "fix", "text": "API Timeout behoben"},
        ],
        "stats": {"additions": 150, "deletions": 30, "files_changed": 12},
        "seo_keywords": ["guildscout", "performance", "api"],
        "seo_description": "GuildScout v2.5.0 bringt schnellere API-Antworten.",
        "language": "de",
        "published_at": "2026-03-10T14:00:00+00:00",
    }


# ============================================================================
# TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_upsert_and_get(changelog_db, sample_entry):
    """Eintrag erstellen und wieder lesen."""
    await changelog_db.upsert(sample_entry)

    result = await changelog_db.get("guildscout", "2.5.0")
    assert result is not None
    assert result["project"] == "guildscout"
    assert result["version"] == "2.5.0"
    assert result["title"] == "GuildScout v2.5.0 — Performance Update"
    assert result["tldr"] == "Schnellere API-Antworten und neue Filter"
    assert result["content"] == "Dieses Update bringt signifikante Performance-Verbesserungen."
    assert result["language"] == "de"
    assert result["published_at"] == "2026-03-10T14:00:00+00:00"
    assert result["id"] is not None
    assert result["created_at"] is not None


@pytest.mark.asyncio
async def test_upsert_updates_existing(changelog_db, sample_entry):
    """Upsert aktualisiert bestehenden Eintrag bei gleichem project+version."""
    await changelog_db.upsert(sample_entry)

    # Aktualisieren
    updated_entry = sample_entry.copy()
    updated_entry["title"] = "GuildScout v2.5.0 — Mega Update"
    updated_entry["tldr"] = "Jetzt noch besser"
    updated_entry["changes"] = [{"type": "feat", "text": "Alles neu"}]

    await changelog_db.upsert(updated_entry)

    result = await changelog_db.get("guildscout", "2.5.0")
    assert result is not None
    assert result["title"] == "GuildScout v2.5.0 — Mega Update"
    assert result["tldr"] == "Jetzt noch besser"
    assert len(result["changes"]) == 1
    assert result["changes"][0]["text"] == "Alles neu"


@pytest.mark.asyncio
async def test_get_nonexistent_returns_none(changelog_db):
    """get() gibt None zurueck bei nicht-existierendem Eintrag."""
    result = await changelog_db.get("nonexistent", "0.0.0")
    assert result is None


@pytest.mark.asyncio
async def test_list_by_project(changelog_db):
    """Paginierung und Sortierung (neueste zuerst)."""
    # 15 Eintraege erstellen
    for i in range(15):
        await changelog_db.upsert({
            "project": "testprojekt",
            "version": f"1.0.{i}",
            "title": f"Release v1.0.{i}",
            "published_at": f"2026-01-{i+1:02d}T12:00:00+00:00",
        })

    # Seite 1 (Standard: 10 Eintraege)
    page1 = await changelog_db.list_by_project("testprojekt", page=1, limit=10)
    assert len(page1["data"]) == 10
    assert page1["meta"]["page"] == 1
    assert page1["meta"]["per_page"] == 10
    assert page1["meta"]["total"] == 15
    assert page1["meta"]["total_pages"] == 2

    # Neueste zuerst pruefen
    assert page1["data"][0]["version"] == "1.0.14"
    assert page1["data"][1]["version"] == "1.0.13"

    # Seite 2
    page2 = await changelog_db.list_by_project("testprojekt", page=2, limit=10)
    assert len(page2["data"]) == 5
    assert page2["meta"]["page"] == 2

    # Leeres Projekt
    empty = await changelog_db.list_by_project("gibts_nicht")
    assert len(empty["data"]) == 0
    assert empty["meta"]["total"] == 0
    assert empty["meta"]["total_pages"] == 0


@pytest.mark.asyncio
async def test_list_all_projects(changelog_db):
    """Alle Projektnamen auflisten."""
    for project in ["guildscout", "zerodox", "shadowops-bot"]:
        await changelog_db.upsert({
            "project": project,
            "version": "1.0.0",
            "title": f"{project} v1.0.0",
            "published_at": "2026-03-10T12:00:00+00:00",
        })

    projects = await changelog_db.list_all_projects()
    assert len(projects) == 3
    assert "guildscout" in projects
    assert "zerodox" in projects
    assert "shadowops-bot" in projects


@pytest.mark.asyncio
async def test_json_fields_deserialized(changelog_db, sample_entry):
    """JSON-Felder (changes, stats, seo_keywords) kommen als Python-Objekte zurueck."""
    await changelog_db.upsert(sample_entry)

    result = await changelog_db.get("guildscout", "2.5.0")
    assert result is not None

    # changes ist eine Liste
    assert isinstance(result["changes"], list)
    assert len(result["changes"]) == 2
    assert result["changes"][0]["type"] == "feat"
    assert result["changes"][1]["type"] == "fix"

    # stats ist ein Dict
    assert isinstance(result["stats"], dict)
    assert result["stats"]["additions"] == 150
    assert result["stats"]["deletions"] == 30

    # seo_keywords ist eine Liste
    assert isinstance(result["seo_keywords"], list)
    assert "guildscout" in result["seo_keywords"]
    assert len(result["seo_keywords"]) == 3


@pytest.mark.asyncio
async def test_json_fields_default_values(changelog_db):
    """JSON-Felder haben korrekte Defaults wenn nicht angegeben."""
    await changelog_db.upsert({
        "project": "minimal",
        "version": "0.1.0",
        "title": "Minimaler Eintrag",
        "published_at": "2026-03-10T12:00:00+00:00",
    })

    result = await changelog_db.get("minimal", "0.1.0")
    assert result is not None
    assert result["changes"] == []
    assert result["stats"] == {}
    assert result["seo_keywords"] == []
    assert result["tldr"] == ""
    assert result["content"] == ""
    assert result["seo_description"] == ""
    assert result["language"] == "de"


@pytest.mark.asyncio
async def test_factory_function(temp_dir):
    """Factory-Funktion get_changelog_db erstellt korrekte Instanz."""
    from src.integrations.changelog_db import get_changelog_db

    db_path = str(temp_dir / "factory_test.db")
    db = get_changelog_db(db_path=db_path)

    assert db is not None
    assert isinstance(db, type(db))  # ChangelogDB-Instanz

    await db.initialize()
    await db.upsert({
        "project": "test",
        "version": "1.0.0",
        "title": "Test",
        "published_at": "2026-03-10T12:00:00+00:00",
    })

    result = await db.get("test", "1.0.0")
    assert result is not None
    assert result["title"] == "Test"

    await db.close()
