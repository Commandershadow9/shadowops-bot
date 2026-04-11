import asyncio
import asyncpg
import json

async def main():
    dsn = "postgresql://security_analyst:SICHERES_PASSWORT@127.0.0.1:5433/security_analyst"
    try:
        conn = await asyncpg.connect(dsn)
        row = await conn.fetchrow("SELECT * FROM findings WHERE id = 195")
        if row:
            print("--- Finding #195 ---")
            for key, value in dict(row).items():
                print(f"{key}: {value}")
        else:
            print("Finding #195 not found in database.")
        await conn.close()
    except Exception as e:
        print(f"Database error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
