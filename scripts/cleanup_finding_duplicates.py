"""Duplikate-Aufraeumer.
Nutzung:
  --dry-run (default)  : Report
  --apply              : markiert juengere als 'duplicate_of', behaelt juengstes pro Gruppe
"""
import argparse, asyncio, os, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
import asyncpg


async def main(apply: bool):
    dsn = os.environ.get(
        "SECURITY_ANALYST_DB_URL",
        "postgresql://security_analyst:sec_analyst_2026@127.0.0.1:5433/security_analyst",
    )
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        groups = await pool.fetch("""
            SELECT finding_fingerprint,
                   array_agg(id ORDER BY found_at DESC) as ids,
                   array_agg(title ORDER BY found_at DESC) as titles
            FROM findings
            WHERE status='open' AND finding_fingerprint IS NOT NULL
            GROUP BY finding_fingerprint HAVING COUNT(*) > 1
            ORDER BY COUNT(*) DESC
        """)
        total_to_merge = 0
        for g in groups:
            ids = g["ids"]
            titles = g["titles"]
            parent, children = ids[0], ids[1:]
            print(f"\nFingerprint {g['finding_fingerprint'][:12]}")
            print(f"  BEHALTEN #{parent}: {titles[0][:70]}")
            for cid, ct in zip(children, titles[1:]):
                print(f"  merge    #{cid}: {ct[:70]}")
            total_to_merge += len(children)
            if apply:
                for cid in children:
                    await pool.execute(
                        "UPDATE findings SET status='duplicate_of', fixed_at=NOW() "
                        "WHERE id=$1",
                        cid,
                    )
        print(f"\n{len(groups)} Gruppen, {total_to_merge} Findings als Duplikat markiert." if apply
              else f"\nDRY-RUN: wuerde {total_to_merge} Findings mergen. Mit --apply ausfuehren.")
    finally:
        await pool.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.apply))
