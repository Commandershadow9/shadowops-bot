import asyncio
import time
from unittest.mock import MagicMock, AsyncMock

async def benchmark():
    # Mock pool
    class MockPool:
        async def fetchval(self, query):
            await asyncio.sleep(0.01) # simulated network latency
            return 42

        async def fetchrow(self, query):
            await asyncio.sleep(0.01) # simulated network latency
            return {'impact_count': 42, 'cross_knowledge': 42}

    pool = MockPool()

    # Original approach
    start_orig = time.time()
    for _ in range(100):
        impact_count = await pool.fetchval("SELECT COUNT(*) FROM seo_fix_impact")
        cross_knowledge = await pool.fetchval("SELECT COUNT(*) FROM agent_knowledge")
    end_orig = time.time()

    # Optimized approach
    start_opt = time.time()
    for _ in range(100):
        row = await pool.fetchrow("""
            SELECT
                (SELECT COUNT(*) FROM seo_fix_impact) as impact_count,
                (SELECT COUNT(*) FROM agent_knowledge) as cross_knowledge
        """)
        impact_count = row['impact_count']
        cross_knowledge = row['cross_knowledge']
    end_opt = time.time()

    orig_time = end_orig - start_orig
    opt_time = end_opt - start_opt

    print(f"Original Time: {orig_time:.4f}s")
    print(f"Optimized Time: {opt_time:.4f}s")
    print(f"Improvement: {(orig_time - opt_time) / orig_time * 100:.2f}%")

asyncio.run(benchmark())
