import asyncio
import asyncpg
import os

async def main():
    dsn = "postgresql://security_analyst:SICHERES_PASSWORT@127.0.0.1:5433/security_analyst"
    # Try to find the actual DSN if possible, but let's try the default first.
    try:
        conn = await asyncpg.connect(dsn)
        rows = await conn.fetch("SELECT * FROM findings WHERE id = 195")
        for row in rows:
            print(dict(row))
        await conn.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
