"""
Tests für JulesState asyncpg-Layer.

Benötigt eine echte PostgreSQL-Verbindung (security_analyst DB).
Test-Rows verwenden repo='test_*' und werden nach jedem Test aufgeräumt.
"""
import asyncio
import pytest
import pytest_asyncio

# DSN aus Config laden — skip wenn nicht verfügbar
try:
    from src.utils.config import Config
    _DSN = Config().security_analyst_dsn
    if not _DSN:
        raise RuntimeError("Kein DSN")
except Exception:
    _DSN = None

from src.integrations.github_integration.jules_state import JulesState, JulesReviewRow

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(_DSN is None, reason="Keine security_analyst DB-Verbindung verfügbar"),
]


@pytest_asyncio.fixture
async def state():
    """JulesState-Instanz mit Verbindung, räumt test_*-Rows auf."""
    js = JulesState(_DSN)
    await js.connect()
    # Cleanup vor dem Test
    async with js._pool.acquire() as conn:
        await conn.execute("DELETE FROM jules_pr_reviews WHERE repo LIKE 'test_%'")
    yield js
    # Cleanup nach dem Test
    async with js._pool.acquire() as conn:
        await conn.execute("DELETE FROM jules_pr_reviews WHERE repo LIKE 'test_%'")
    await js.close()


async def _seed_pending(state: JulesState, repo: str = "test_repo/bot", pr: int = 999) -> int:
    """Seed eine pending Row und gib die ID zurück."""
    async with state._pool.acquire() as conn:
        row_id = await conn.fetchval(
            "INSERT INTO jules_pr_reviews (repo, pr_number, status) VALUES ($1, $2, 'pending') RETURNING id",
            repo, pr,
        )
    return row_id


# ── Task 3.2: try_claim_review ──────────────────────────────

class TestTryClaimReview:
    """Atomic Lock-Claim + SHA-Dedupe Tests."""

    async def test_first_claim_succeeds(self, state: JulesState):
        """Erster Claim auf pending Row gibt Row mit status=reviewing zurück."""
        await _seed_pending(state)
        row = await state.try_claim_review("test_repo/bot", 999, "abc123", "worker-1")
        assert row is not None
        assert isinstance(row, JulesReviewRow)
        assert row.status == "reviewing"
        assert row.lock_owner == "worker-1"
        assert row.lock_acquired_at is not None

    async def test_second_claim_while_locked_returns_none(self, state: JulesState):
        """Zweiter Claim während Lock aktiv gibt None zurück (Race Protection)."""
        await _seed_pending(state)
        row1 = await state.try_claim_review("test_repo/bot", 999, "abc123", "worker-1")
        assert row1 is not None
        # Zweiter Worker versucht den gleichen PR
        row2 = await state.try_claim_review("test_repo/bot", 999, "abc123", "worker-2")
        assert row2 is None

    async def test_same_sha_after_review_returns_none(self, state: JulesState):
        """Gleicher SHA nach abgeschlossenem Review gibt None (Dedupe)."""
        row_id = await _seed_pending(state)
        row = await state.try_claim_review("test_repo/bot", 999, "sha_v1", "worker-1")
        assert row is not None
        # Review abschließen: SHA markieren + Lock freigeben
        await state.mark_reviewed_sha(row.id, "sha_v1")
        await state.release_lock(row.id, "approved")
        # Gleicher SHA → None (bereits reviewed)
        row2 = await state.try_claim_review("test_repo/bot", 999, "sha_v1", "worker-1")
        assert row2 is None

    async def test_new_sha_after_review_succeeds(self, state: JulesState):
        """Neuer SHA nach abgeschlossenem Review wird erfolgreich geclaimed."""
        await _seed_pending(state)
        row = await state.try_claim_review("test_repo/bot", 999, "sha_v1", "worker-1")
        assert row is not None
        await state.mark_reviewed_sha(row.id, "sha_v1")
        await state.release_lock(row.id, "approved")
        # Neuer SHA → Claim erfolgreich
        row2 = await state.try_claim_review("test_repo/bot", 999, "sha_v2", "worker-1")
        assert row2 is not None
        assert row2.status == "reviewing"


class TestReleaseLock:
    """release_lock Tests."""

    async def test_release_clears_lock(self, state: JulesState):
        """Release setzt status und entfernt Lock-Felder."""
        await _seed_pending(state)
        row = await state.try_claim_review("test_repo/bot", 999, "sha1", "worker-1")
        await state.release_lock(row.id, "approved")
        async with state._pool.acquire() as conn:
            rec = await conn.fetchrow("SELECT * FROM jules_pr_reviews WHERE id=$1", row.id)
        assert rec["status"] == "approved"
        assert rec["lock_owner"] is None
        assert rec["lock_acquired_at"] is None

    async def test_release_invalid_status_raises(self, state: JulesState):
        """Ungültiger Status bei release_lock wirft ValueError."""
        await _seed_pending(state)
        row = await state.try_claim_review("test_repo/bot", 999, "sha1", "worker-1")
        with pytest.raises(ValueError, match="Ungültiger Status"):
            await state.release_lock(row.id, "bogus_status")


class TestMarkReviewedSha:
    """mark_reviewed_sha Tests."""

    async def test_mark_sets_sha_and_increments(self, state: JulesState):
        """mark_reviewed_sha setzt SHA, last_review_at und inkrementiert iteration_count."""
        await _seed_pending(state)
        row = await state.try_claim_review("test_repo/bot", 999, "sha1", "worker-1")
        await state.mark_reviewed_sha(row.id, "sha1")
        async with state._pool.acquire() as conn:
            rec = await conn.fetchrow("SELECT * FROM jules_pr_reviews WHERE id=$1", row.id)
        assert rec["last_reviewed_sha"] == "sha1"
        assert rec["iteration_count"] == 1
        assert rec["last_review_at"] is not None
