"""
Tests fuer die fingerprint-basierte Finding-Dedup im SecurityScanAgent.

Ersetzt die alte Titel-Match-Logik (_find_similar_open_finding) durch
einen deterministischen Fingerprint-Lookup (category, project, files,
title-keywords).
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from integrations.security_engine.scan_agent import SecurityScanAgent


@pytest.mark.asyncio
async def test_fingerprint_based_dedup_catches_semantic_dupes():
    """
    Zwei Findings mit anderem Titel aber gleichem (category, project, files)
    muessen als Duplikat erkannt werden — Fingerprint-Lookup liefert die
    existierende Finding-ID zurueck.

    Return ist (fp, row_or_None) — der Fingerprint wird ausserhalb fuer das
    INSERT wiederverwendet, damit Lookup-FP und INSERT-FP nicht divergieren.
    """
    agent = SecurityScanAgent.__new__(SecurityScanAgent)
    agent.db = MagicMock()
    agent.db.pool = MagicMock()
    agent.db.pool.fetchrow = AsyncMock(return_value={
        "id": 123,
        "title": "Altes Finding",
        "github_issue_url": "https://github.com/x/1",
        "finding_fingerprint": "deadbeef",
    })

    fp, result = await agent._find_similar_open_finding_by_fingerprint(
        category="dependencies",
        affected_project="infrastructure",
        affected_files=["Dockerfile"],
        title="Neuer Wording-Titel ueber ImageMagick",
    )
    assert result is not None
    assert result["id"] == 123
    # Fingerprint ist SHA1 hex -> 40 Zeichen
    assert isinstance(fp, str)
    assert len(fp) == 40


@pytest.mark.asyncio
async def test_fingerprint_dedup_returns_none_when_no_match():
    """Kein Fingerprint-Treffer -> (fp, None). Kein Fallback auf Titel-Match."""
    agent = SecurityScanAgent.__new__(SecurityScanAgent)
    agent.db = MagicMock()
    agent.db.pool = MagicMock()
    agent.db.pool.fetchrow = AsyncMock(return_value=None)

    fp, result = await agent._find_similar_open_finding_by_fingerprint(
        category="x",
        affected_project="y",
        affected_files=[],
        title="z",
    )
    assert result is None
    assert isinstance(fp, str)
    assert len(fp) == 40


@pytest.mark.asyncio
async def test_fingerprint_dedup_uses_compute_finding_fingerprint():
    """
    Die Methode MUSS compute_finding_fingerprint nutzen, damit identische
    Findings denselben FP berechnen wie beim INSERT (sonst kein Match).
    Der zurueckgegebene fp MUSS exakt dem DB-Query-Parameter entsprechen.
    """
    agent = SecurityScanAgent.__new__(SecurityScanAgent)
    agent.db = MagicMock()
    agent.db.pool = MagicMock()
    agent.db.pool.fetchrow = AsyncMock(return_value=None)

    fp, _ = await agent._find_similar_open_finding_by_fingerprint(
        category="dependencies",
        affected_project="infrastructure",
        affected_files=["Dockerfile"],
        title="ImageMagick Update noetig",
    )

    # Der DB-Call muss den Fingerprint als Parameter erhalten haben
    call_args = agent.db.pool.fetchrow.call_args
    assert call_args is not None
    sql = call_args.args[0]
    fp_param = call_args.args[1]
    assert "finding_fingerprint" in sql
    assert "status='open'" in sql
    # Fingerprint ist SHA1 hex -> 40 Zeichen
    assert isinstance(fp_param, str)
    assert len(fp_param) == 40
    # Rueckgabe-fp MUSS exakt dem DB-Query-Parameter entsprechen (DRY: kein Re-Compute)
    assert fp == fp_param
