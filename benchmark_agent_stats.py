import asyncio
import time
import asyncpg

async def setup_db():
    conn = await asyncpg.connect('postgresql://postgres@localhost/postgres')
    await conn.execute("CREATE TABLE IF NOT EXISTS pn_generations (id SERIAL PRIMARY KEY)")
    await conn.execute("CREATE TABLE IF NOT EXISTS agent_feedback (id SERIAL PRIMARY KEY, agent TEXT)")
    await conn.execute("CREATE TABLE IF NOT EXISTS pn_examples (id SERIAL PRIMARY KEY, is_active BOOLEAN)")

    # insert some dummy data
    for _ in range(10):
        await conn.execute("INSERT INTO pn_generations DEFAULT VALUES")
        await conn.execute("INSERT INTO agent_feedback (agent) VALUES ('patch_notes')")
        await conn.execute("INSERT INTO pn_examples (is_active) VALUES (TRUE)")

    await conn.close()

async def run_baseline():
    conn = await asyncpg.connect('postgresql://postgres@localhost/postgres')
    start_time = time.time()

    for _ in range(100):
        gen_count = await conn.fetchval("SELECT COUNT(*) FROM pn_generations")
        fb_count = await conn.fetchval("SELECT COUNT(*) FROM agent_feedback WHERE agent='patch_notes'")
        examples = await conn.fetchval("SELECT COUNT(*) FROM pn_examples WHERE is_active=TRUE")

    end_time = time.time()
    await conn.close()
    return end_time - start_time

async def run_optimized():
    conn = await asyncpg.connect('postgresql://postgres@localhost/postgres')
    start_time = time.time()

    for _ in range(100):
        query = """
            SELECT
                (SELECT COUNT(*) FROM pn_generations) as gen_count,
                (SELECT COUNT(*) FROM agent_feedback WHERE agent='patch_notes') as fb_count,
                (SELECT COUNT(*) FROM pn_examples WHERE is_active=TRUE) as examples
        """
        row = await conn.fetchrow(query)
        gen_count = row['gen_count']
        fb_count = row['fb_count']
        examples = row['examples']

    end_time = time.time()
    await conn.close()
    return end_time - start_time

async def main():
    await setup_db()
    baseline = await run_baseline()
    optimized = await run_optimized()
    print(f"Baseline: {baseline:.4f}s")
    print(f"Optimized: {optimized:.4f}s")
    print(f"Improvement: {(baseline - optimized) / baseline * 100:.2f}%")

if __name__ == '__main__':
    asyncio.run(main())
