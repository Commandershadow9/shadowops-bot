"""Tests für DB-basierte SemVer-Versionierung."""
import sqlite3
import pytest
from pathlib import Path
from patch_notes.versioning import calculate_version, get_last_db_version, ensure_unique

@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "changelogs.db"
    conn = sqlite3.connect(str(path))
    conn.execute("""
        CREATE TABLE changelogs (
            project TEXT, version TEXT, title TEXT, content TEXT,
            tldr TEXT, changes TEXT, stats TEXT, seo_keywords TEXT,
            seo_description TEXT, language TEXT, published_at TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (project, version)
        )
    """)
    conn.execute("INSERT INTO changelogs (project, version, title) VALUES ('test', '0.18.0', 'old')")
    conn.execute("INSERT INTO changelogs (project, version, title) VALUES ('test', '0.19.0', 'prev')")
    conn.commit()
    conn.close()
    return path

def test_get_last_version(db_path):
    v = get_last_db_version("test", db_path)
    assert v == "0.19.0"

def test_get_last_version_no_project(db_path):
    v = get_last_db_version("nonexistent", db_path)
    assert v is None

def test_feature_bump(db_path):
    groups = [{"tag": "FEATURE", "theme": "Neues Feature"}]
    version, source = calculate_version("test", groups, db_path)
    assert version == "0.20.0"
    assert source == "semver"

def test_bugfix_bump(db_path):
    groups = [{"tag": "BUGFIX", "theme": "Fix"}]
    version, source = calculate_version("test", groups, db_path)
    assert version == "0.19.1"
    assert source == "semver"

def test_breaking_bump(db_path):
    groups = [{"tag": "BREAKING", "theme": "Breaking Change"}]
    version, source = calculate_version("test", groups, db_path)
    assert version == "1.0.0"
    assert source == "semver"

def test_new_project_fallback(db_path):
    groups = [{"tag": "FEATURE"}]
    version, source = calculate_version("brand_new", groups, db_path)
    assert version == "0.1.0"
    assert source == "fallback"

def test_collision_bumps_patch(db_path):
    unique = ensure_unique("0.19.0", "test", db_path)
    assert unique == "0.19.1"

def test_infra_only_patch_bump(db_path):
    groups = [{"tag": "INFRASTRUCTURE"}, {"tag": "DOCS"}]
    version, source = calculate_version("test", groups, db_path)
    assert version == "0.19.1"
    assert source == "semver"
