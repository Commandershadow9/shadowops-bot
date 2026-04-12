"""
Tests für JulesState asyncpg-Layer.

Benötigt eine echte PostgreSQL-Verbindung (security_analyst DB).
Test-Rows verwenden repo='test_*' und werden nach jedem Test aufgeräumt.
"""
import asyncio
from datetime import datetime, timedelta, timezone
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


# ── Task 3.3: Stale-Lock-Recovery ─────────────────────────────

class TestRecoverStaleLocks:
    """Stale-Lock-Recovery Tests."""

    async def test_stale_lock_gets_freed(self, state: JulesState):
        """Lock älter als Timeout wird zurückgesetzt auf revision_requested."""
        row_id = await _seed_pending(state)
        row = await state.try_claim_review("test_repo/bot", 999, "sha1", "worker-1")
        assert row is not None
        # Lock-Zeitstempel 15 Minuten in die Vergangenheit setzen
        async with state._pool.acquire() as conn:
            await conn.execute(
                "UPDATE jules_pr_reviews SET lock_acquired_at = now() - interval '15 minutes' WHERE id=$1",
                row.id,
            )
        freed = await state.recover_stale_locks(timeout_minutes=10)
        assert freed == 1
        # Prüfen: Status ist revision_requested, Lock-Felder leer
        async with state._pool.acquire() as conn:
            rec = await conn.fetchrow("SELECT * FROM jules_pr_reviews WHERE id=$1", row.id)
        assert rec["status"] == "revision_requested"
        assert rec["lock_owner"] is None
        assert rec["lock_acquired_at"] is None

    async def test_fresh_lock_stays(self, state: JulesState):
        """Frischer Lock (< Timeout) wird NICHT zurückgesetzt."""
        await _seed_pending(state)
        row = await state.try_claim_review("test_repo/bot", 999, "sha1", "worker-1")
        assert row is not None
        # Lock ist gerade eben gesetzt — sollte nicht betroffen sein
        freed = await state.recover_stale_locks(timeout_minutes=10)
        assert freed == 0
        # Status bleibt reviewing
        async with state._pool.acquire() as conn:
            rec = await conn.fetchrow("SELECT * FROM jules_pr_reviews WHERE id=$1", row.id)
        assert rec["status"] == "reviewing"
        assert rec["lock_owner"] == "worker-1"


# ── Task 3.4: CRUD-Helpers ────────────────────────────────────

class TestEnsurePending:
    """ensure_pending — Upsert-Logik Tests."""

    async def test_create_new_row(self, state: JulesState):
        """Neue Row wird als pending erstellt."""
        row = await state.ensure_pending("test_crud/repo", 42, 100, 200)
        assert isinstance(row, JulesReviewRow)
        assert row.repo == "test_crud/repo"
        assert row.pr_number == 42
        assert row.issue_number == 100
        assert row.finding_id == 200
        assert row.status == "pending"

    async def test_idempotent_upsert(self, state: JulesState):
        """Zweiter Aufruf mit gleichem (repo, pr_number) ändert nichts am Status."""
        row1 = await state.ensure_pending("test_crud/repo", 42, 100, 200)
        row2 = await state.ensure_pending("test_crud/repo", 42, 101, 201)
        assert row1.id == row2.id  # gleiche Row
        # COALESCE: Existierende Werte bleiben erhalten
        assert row2.issue_number == 100
        assert row2.finding_id == 200

    async def test_upsert_fills_nulls(self, state: JulesState):
        """Upsert füllt NULL-Felder mit neuen Werten."""
        row1 = await state.ensure_pending("test_crud/repo2", 43, None, None)
        assert row1.issue_number is None
        assert row1.finding_id is None
        row2 = await state.ensure_pending("test_crud/repo2", 43, 55, 66)
        assert row2.id == row1.id
        assert row2.issue_number == 55
        assert row2.finding_id == 66


class TestGet:
    """get — Lookup Tests."""

    async def test_get_existing(self, state: JulesState):
        """Vorhandene Row wird zurückgegeben."""
        await state.ensure_pending("test_get/repo", 77, None, None)
        row = await state.get("test_get/repo", 77)
        assert row is not None
        assert row.pr_number == 77

    async def test_get_missing_returns_none(self, state: JulesState):
        """Fehlende Row gibt None zurück."""
        row = await state.get("test_nonexistent/repo", 99999)
        assert row is None


class TestUpdateCommentId:
    """update_comment_id Tests."""

    async def test_sets_comment_id(self, state: JulesState):
        """Comment-ID wird korrekt gesetzt."""
        row = await state.ensure_pending("test_comment/repo", 88, None, None)
        await state.update_comment_id(row.id, 123456789)
        updated = await state.get("test_comment/repo", 88)
        assert updated.review_comment_id == 123456789


class TestMarkTerminal:
    """mark_terminal Tests."""

    async def test_sets_closed_at(self, state: JulesState):
        """Terminal-Status setzt closed_at und entfernt Lock."""
        row = await state.ensure_pending("test_terminal/repo", 55, None, None)
        # Claim + dann terminal
        claimed = await state.try_claim_review("test_terminal/repo", 55, "sha1", "w-1")
        assert claimed is not None
        await state.mark_terminal(claimed.id, "merged")
        result = await state.get("test_terminal/repo", 55)
        assert result.status == "merged"
        assert result.closed_at is not None
        assert result.lock_owner is None
        assert result.lock_acquired_at is None

    async def test_invalid_terminal_status_raises(self, state: JulesState):
        """Nicht-terminaler Status wirft ValueError."""
        row = await state.ensure_pending("test_terminal2/repo", 56, None, None)
        with pytest.raises(ValueError, match="mark_terminal nur für Terminal-States"):
            await state.mark_terminal(row.id, "pending")

    async def test_all_terminal_states(self, state: JulesState):
        """Alle drei Terminal-States funktionieren."""
        for i, status in enumerate(["merged", "abandoned", "escalated"]):
            row = await state.ensure_pending(f"test_term_{status}/repo", 60 + i, None, None)
            await state.mark_terminal(row.id, status)
            result = await state.get(f"test_term_{status}/repo", 60 + i)
            assert result.status == status
            assert result.closed_at is not None


class TestStoreReviewResult:
    """store_review_result Tests."""

    async def test_stores_json_and_tokens(self, state: JulesState):
        """Review-Ergebnis wird als JSONB gespeichert, Tokens addiert."""
        row = await state.ensure_pending("test_store/repo", 70, None, None)
        review = {"verdict": "approved", "summary": "LGTM"}
        blockers = [{"title": "Fix imports"}]
        await state.store_review_result(row.id, review, blockers, 500)
        result = await state.get("test_store/repo", 70)
        assert result.last_review_json == review
        assert result.last_blockers == blockers
        assert result.tokens_consumed == 500
        # Zweiter Aufruf addiert Tokens
        await state.store_review_result(row.id, review, [], 300)
        result2 = await state.get("test_store/repo", 70)
        assert result2.tokens_consumed == 800


class TestFetchHealthStats:
    """fetch_health_stats Tests."""

    async def test_returns_valid_structure(self, state: JulesState):
        """Health-Stats haben die erwartete Struktur."""
        stats = await state.fetch_health_stats()
        assert "active_reviews" in stats
        assert "pending_prs" in stats
        assert "escalated_24h" in stats
        assert "stats_24h" in stats
        assert "last_review_at" in stats
        s24 = stats["stats_24h"]
        assert "total_reviews" in s24
        assert "approved" in s24
        assert "revisions" in s24
        assert "merged" in s24
        assert "tokens_consumed" in s24
