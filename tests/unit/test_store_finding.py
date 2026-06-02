import pytest
from unittest.mock import AsyncMock
from src.integrations.security_engine.db import SecurityDB


def _db_with_mock_pool(return_id=42):
    db = SecurityDB("postgres://unused")
    db.pool = AsyncMock()
    db.pool.fetchrow = AsyncMock(return_value={"id": return_id})
    return db


@pytest.mark.asyncio
async def test_store_finding_minimal_profile_returns_id():
    db = _db_with_mock_pool(return_id=7)
    fid = await db.store_finding(
        severity="HIGH", category="npm_audit",
        title="t", description="d", affected_project="guildscout",
    )
    assert fid == 7
    sql = db.pool.fetchrow.call_args.args[0]
    assert "INSERT INTO findings" in sql
    assert "RETURNING id" in sql
    args = db.pool.fetchrow.call_args.args[1:]
    assert "guildscout" in args
    assert None in args  # session_id/fingerprint etc. = None


@pytest.mark.asyncio
async def test_store_finding_full_profile_passes_fingerprint():
    db = _db_with_mock_pool(return_id=99)
    fid = await db.store_finding(
        severity="info", category="code_security", title="t", description="d",
        session_id=5, affected_project="zerodox",
        affected_files=["a.ts"], fix_type="manual",
        github_issue_url="https://x/1", finding_fingerprint="abc123",
    )
    assert fid == 99
    args = db.pool.fetchrow.call_args.args[1:]
    assert "abc123" in args
    assert "zerodox" in args


@pytest.mark.asyncio
async def test_store_finding_db_error_returns_none():
    db = SecurityDB("postgres://unused")
    db.pool = AsyncMock()
    db.pool.fetchrow = AsyncMock(side_effect=RuntimeError("boom"))
    fid = await db.store_finding(
        severity="LOW", category="x", title="t", description="d",
    )
    assert fid is None


@pytest.mark.asyncio
async def test_store_finding_before_init_returns_none():
    db = SecurityDB("postgres://unused")  # pool ist noch None (kein initialize)
    fid = await db.store_finding(severity="LOW", category="x", title="t", description="d")
    assert fid is None
