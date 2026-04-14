#!/usr/bin/env python3
"""Weekly Health-Check der Multi-Agent Review Pipeline.

Zeigt 6 Sektionen:
1. Pipeline-Throughput (letzte 7 Tage)
2. Claude-Reviews pro Agent
3. Auto-Merge Revert-Rate pro Rule
4. Queue-Health
5. Jules-API-Limits (24h)
6. Pending Manual-Merges

Exit-Code 0 wenn alles grün, 1 bei Warnings (mindestens eine Ampel gelb/rot).

Usage:
  cd /home/cmdshadow/shadowops-bot
  source .venv/bin/activate
  PYTHONPATH=src python scripts/weekly_review_check.py
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime

import asyncpg

# PYTHONPATH fallback
sys.path.insert(0, 'src')
from utils.config import Config  # noqa: E402


# ANSI
G = "\033[0;32m"
R = "\033[0;31m"
Y = "\033[0;33m"
C = "\033[0;36m"
B = "\033[1m"
N = "\033[0m"


def color_rate(rate: float) -> str:
    if rate >= 20:
        return R
    if rate >= 10:
        return Y
    return ""


async def section_throughput(conn) -> int:
    """Pipeline-Throughput (letzte 7 Tage). Returns warning count."""
    print(f"\n{B}📊 1. Pipeline-Throughput (letzte 7 Tage){N}\n")
    row = await conn.fetchrow("""
        SELECT
          COUNT(*) FILTER (WHERE source='scan_agent')       AS jules_delegated,
          COUNT(*) FILTER (WHERE source='manual')           AS manual_tasks,
          COUNT(*) FILTER (WHERE source='jules_suggestion') AS jules_suggestions,
          COUNT(*) FILTER (WHERE status='released')         AS released,
          COUNT(*) FILTER (WHERE status='failed')           AS failed
        FROM agent_task_queue
        WHERE created_at > now() - interval '7 days'
    """)
    print(f"  {'ScanAgent-delegiert:':<30} {row['jules_delegated']}")
    print(f"  {'Manuelle Tasks:':<30} {row['manual_tasks']}")
    print(f"  {'Jules-Suggestions:':<30} {row['jules_suggestions']}")
    print(f"  {'Released (gestartet):':<30} {row['released']}")
    warnings = 0
    if row['failed'] > 3:
        print(f"  {Y}⚠ Failed: {row['failed']} (>3, pruefen!){N}")
        warnings += 1
    else:
        print(f"  {'Failed:':<30} {row['failed']}")
    return warnings


async def section_reviews(conn) -> int:
    print(f"\n{B}📋 2. Claude-Reviews pro Agent (letzte 7 Tage){N}\n")
    rows = await conn.fetch("""
        SELECT
          agent_type,
          last_review_json->>'verdict' AS verdict,
          COUNT(*)::int AS cnt
        FROM jules_pr_reviews
        WHERE updated_at > now() - interval '7 days'
          AND last_review_json IS NOT NULL
          AND last_review_json->>'verdict' IS NOT NULL
        GROUP BY agent_type, last_review_json->>'verdict'
        ORDER BY agent_type, verdict
    """)
    if not rows:
        print("  (keine Reviews in den letzten 7 Tagen)")
        return 0
    print(f"  {'Agent':<15} {'Verdict':<25} {'Anzahl'}")
    print("  " + "─" * 50)
    for r in rows:
        print(f"  {r['agent_type']:<15} {r['verdict']:<25} {r['cnt']}")
    return 0


async def section_revert_rate(conn) -> int:
    print(f"\n{B}🚀 3. Auto-Merge Revert-Rate pro Rule (letzte 7 Tage){N}\n")
    rows = await conn.fetch("""
        SELECT
          agent_type,
          rule_matched,
          COUNT(*)::int AS total,
          SUM(CASE WHEN reverted THEN 1 ELSE 0 END)::int AS reverted,
          ROUND(100.0 * SUM(CASE WHEN reverted THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 1) AS rate
        FROM auto_merge_outcomes
        WHERE merged_at > now() - interval '7 days' AND checked_at IS NOT NULL
        GROUP BY agent_type, rule_matched
        HAVING COUNT(*) >= 2
        ORDER BY rate DESC NULLS LAST, total DESC
    """)
    if not rows:
        print("  (keine geprueften Auto-Merges in den letzten 7 Tagen)")
        return 0
    print(f"  {'Agent':<12} {'Rule':<30} {'Total':>6} {'Revert':>8} {'Rate%':>8}")
    print("  " + "─" * 66)
    warnings = 0
    for r in rows:
        rate = float(r['rate'] or 0)
        c = color_rate(rate)
        reset = N if c else ""
        print(f"  {c}{r['agent_type']:<12} {r['rule_matched']:<30} "
              f"{r['total']:>6} {r['reverted']:>8} {rate:>7.1f}%{reset}")
        if rate >= 20:
            warnings += 1
    return warnings


async def section_queue(conn) -> int:
    print(f"\n{B}🗂️  4. Queue-Health (letzte 7 Tage){N}\n")
    rows = await conn.fetch("""
        SELECT
          status,
          COUNT(*)::int AS cnt,
          EXTRACT(EPOCH FROM (now() - MIN(created_at)))::int AS oldest_sec
        FROM agent_task_queue
        WHERE created_at > now() - interval '7 days'
        GROUP BY status
        ORDER BY cnt DESC
    """)
    if not rows:
        print("  (Queue ist leer — OK)")
        return 0
    print(f"  {'Status':<12} {'Anzahl':>8} {'Ältester':<15} {'Notiz'}")
    print("  " + "─" * 55)
    warnings = 0
    for r in rows:
        oldest = r['oldest_sec'] or 0
        h, m = divmod(oldest // 60, 60)
        age_str = f"{h}h {m}min"
        note = ""
        color = ""
        if r['status'] == 'queued' and h > 2:
            note = f"{R}⚠ Scheduler haengt!{N}"
            color = R
            warnings += 1
        elif r['status'] == 'failed' and r['cnt'] > 5:
            note = f"{Y}⚠ Hohe Failure-Rate{N}"
            color = Y
            warnings += 1
        reset = N if color else ""
        print(f"  {color}{r['status']:<12} {r['cnt']:>8}  {age_str:<15}{reset} {note}")
    return warnings


async def section_jules_limits(conn) -> int:
    print(f"\n{B}⚡ 5. Jules-API-Limits (aktuell){N}\n")
    row = await conn.fetchrow("""
        SELECT COUNT(*)::int AS cnt FROM agent_task_queue
        WHERE released_at > now() - interval '24 hours'
    """)
    released = row['cnt'] or 0
    warnings = 0
    print(f"  Sessions letzte 24h: {released} / 100")
    if released >= 90:
        print(f"  {R}⚠ Nahe am 100/24h Limit — neue Tasks koennen verzoegert werden{N}")
        warnings += 1
    elif released >= 70:
        print(f"  {Y}⚠ 70%+ des Limits genutzt{N}")
    return warnings


async def section_pending(conn) -> int:
    print(f"\n{B}⏳ 6. Pending Manual-Merges (approved, nicht gemergt){N}\n")
    row = await conn.fetchrow("""
        SELECT COUNT(*)::int AS cnt FROM jules_pr_reviews
        WHERE last_review_json->>'verdict' = 'approved'
          AND status NOT IN ('merged','abandoned')
          AND updated_at > now() - interval '14 days'
    """)
    cnt = row['cnt'] or 0
    print(f"  Approved, noch nicht gemergt: {cnt}")
    warnings = 0
    if cnt > 10:
        print(f"  {Y}⚠ Viele wartende PRs — Team-Kapazitaet pruefen{N}")
        warnings += 1
    return warnings


async def main() -> int:
    print(f"\n{B}{C}━━━ ShadowOps Multi-Agent Review — Weekly Check ━━━{N}")
    print(f"Zeitraum: letzte 7 Tage  ·  Start: {datetime.now():%Y-%m-%d %H:%M:%S}\n")

    try:
        dsn = Config().security_analyst_dsn
    except Exception as e:
        print(f"{R}✗ Config-Load-Fehler: {e}{N}")
        return 1
    if not dsn:
        print(f"{R}✗ security_analyst_dsn nicht konfiguriert{N}")
        return 1

    try:
        conn = await asyncpg.connect(dsn, command_timeout=10)
    except Exception as e:
        print(f"{R}✗ DB-Verbindung fehlgeschlagen: {e}{N}")
        return 1

    warnings = 0
    try:
        warnings += await section_throughput(conn)
        warnings += await section_reviews(conn)
        warnings += await section_revert_rate(conn)
        warnings += await section_queue(conn)
        warnings += await section_jules_limits(conn)
        warnings += await section_pending(conn)
    finally:
        await conn.close()

    print(f"\n{B}{C}━━━ Zusammenfassung ━━━{N}\n")
    if warnings == 0:
        print(f"{G}{B}✅ Alle Metriken im grünen Bereich — Pipeline läuft sauber{N}\n")
        return 0
    print(f"{Y}{B}⚠ {warnings} Warnung(en) gefunden — siehe Markierungen oben{N}")
    print(f"   Runbook: docs/multi-agent-review-runbook.md\n")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
