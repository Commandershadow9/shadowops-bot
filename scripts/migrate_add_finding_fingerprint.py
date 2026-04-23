"""
Backfill finding_fingerprint fuer alle existierenden Findings.
Idempotent: laeuft nur ueber NULL-Rows.
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import asyncpg
from integrations.security_engine.fingerprint import compute_finding_fingerprint


async def main():
    dsn = os.environ.get(
        "SECURITY_ANALYST_DB_URL", "postgresql://security_analyst:PASSWORD@127.0.0.1:5433/security_analyst"
    )
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        rows = await pool.fetch(
            "SELECT id, category, affected_project, affected_files, title "
            "FROM findings WHERE finding_fingerprint IS NULL"
        )
        print(f"Backfill {len(rows)} Findings ...")
        updated = 0
        for r in rows:
            fp = compute_finding_fingerprint(
                category=r["category"],
                affected_project=r["affected_project"] or "",
                affected_files=list(r["affected_files"] or []),
                title=r["title"],
            )
            await pool.execute(
                "UPDATE findings SET finding_fingerprint=$1 WHERE id=$2", fp, r["id"]
            )
            updated += 1
        print(f"{updated} Findings aktualisiert.")

        # Duplikats-Report (zur manuellen Review)
        dupes = await pool.fetch(
            "SELECT finding_fingerprint, COUNT(*) as c, array_agg(id ORDER BY found_at DESC) as ids "
            "FROM findings WHERE status='open' GROUP BY finding_fingerprint HAVING COUNT(*) > 1 "
            "ORDER BY c DESC LIMIT 20"
        )
        if dupes:
            print(f"\n=== {len(dupes)} Fingerprint-Duplikats-Gruppen (open) ===")
            for d in dupes:
                print(f"  fp={d['finding_fingerprint'][:12]}  count={d['c']}  ids={d['ids']}")
        else:
            print("\nKeine Duplikate in open-Findings.")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
