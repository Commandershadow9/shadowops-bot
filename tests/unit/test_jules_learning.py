"""Tests für JulesLearning — Few-Shot + Projekt-Knowledge-Loader.

Benötigt die live agent_learning DB. Wird übersprungen wenn DSN nicht verfügbar.
"""
import os
import sys

import pytest

# DSN aus Config laden
try:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
    from utils.config import Config
    DSN = Config().agent_learning_dsn
except Exception:
    DSN = os.environ.get("AGENT_LEARNING_DB_URL")

pytestmark = pytest.mark.skipif(not DSN, reason="agent_learning DSN nicht verfügbar")


@pytest.fixture
async def learning():
    from src.integrations.github_integration.jules_learning import JulesLearning
    jl = JulesLearning(DSN)
    await jl.connect()
    async with jl._pool.acquire() as conn:
        await conn.execute("DELETE FROM jules_review_examples WHERE project LIKE 'test_%'")
    yield jl
    async with jl._pool.acquire() as conn:
        await conn.execute("DELETE FROM jules_review_examples WHERE project LIKE 'test_%'")
    await jl.close()


@pytest.mark.asyncio
async def test_fetch_few_shot_empty(learning):
    out = await learning.fetch_few_shot_examples("test_empty", limit=3)
    assert out == []


@pytest.mark.asyncio
async def test_fetch_few_shot_orders_by_weight(learning):
    async with learning._pool.acquire() as conn:
        for i, (outcome, weight) in enumerate([
            ("good_catch", 1.0),
            ("good_catch", 2.5),
            ("false_positive", 0.8),
        ]):
            await conn.execute(
                """INSERT INTO jules_review_examples (project, diff_summary, review_json, outcome, weight)
                VALUES ($1, $2, '{}', $3, $4)""",
                "test_weight", f"example_{i}", outcome, weight,
            )
    out = await learning.fetch_few_shot_examples("test_weight", limit=10)
    assert len(out) == 3
    assert out[0]["weight"] >= out[1]["weight"] >= out[2]["weight"]


@pytest.mark.asyncio
async def test_fetch_project_knowledge_returns_list(learning):
    out = await learning.fetch_project_knowledge("test_knowledge_empty", limit=10)
    assert isinstance(out, list)
