"""Weekly-Recap — taeglicher Digest hat 24h-Fokus, dieser hier 7 Tage + Ampel.

Wiederverwendet die Collect-Funktionen aus weekly_review_check.py (Single Source
of Truth), rendert das Ergebnis als discord.Embed statt Terminal-Output.

Wird vom Bot als @tasks.loop(time=time(hour=18, minute=0)) am Freitag gepostet
und ist die automatisierte Variante des Script-basierten Weekly-Checks.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# Ampel-Schwellen (identisch zum Script fuer Konsistenz)
REVERT_RATE_YELLOW = 10.0
REVERT_RATE_RED = 20.0
QUEUE_AGE_RED_HOURS = 2
FAILED_TASKS_YELLOW = 5
JULES_24H_LIMIT_YELLOW_PCT = 70
JULES_24H_LIMIT_RED_PCT = 90
PENDING_MANUAL_YELLOW = 10


@dataclass
class WeeklyRecapData:
    throughput: Dict[str, int]              # {jules_delegated, manual, suggestions, released, failed}
    reviews_by_agent: List[Dict[str, Any]]  # [{agent_type, verdict, count}]
    revert_rates: List[Dict[str, Any]]      # [{agent_type, rule, total, reverted, rate}]
    queue_status: List[Dict[str, Any]]      # [{status, cnt, oldest_hours}]
    jules_sessions_24h: int
    pending_manual: int
    warnings: int                            # Summe aller Ampel-Rot


async def collect_weekly_recap_data(pool) -> WeeklyRecapData:
    """Fuehrt alle 6 Queries gegen security_analyst DB aus."""
    data = WeeklyRecapData(
        throughput={}, reviews_by_agent=[], revert_rates=[],
        queue_status=[], jules_sessions_24h=0, pending_manual=0, warnings=0,
    )
    if pool is None:
        return data

    async with pool.acquire() as conn:
        # 1. Throughput
        try:
            row = await conn.fetchrow("""
                SELECT
                  COUNT(*) FILTER (WHERE source='scan_agent')::int        AS jules_delegated,
                  COUNT(*) FILTER (WHERE source='manual')::int            AS manual_tasks,
                  COUNT(*) FILTER (WHERE source='jules_suggestion')::int  AS jules_suggestions,
                  COUNT(*) FILTER (WHERE status='released')::int          AS released,
                  COUNT(*) FILTER (WHERE status='failed')::int            AS failed
                FROM agent_task_queue
                WHERE created_at > now() - interval '7 days'
            """)
            data.throughput = dict(row) if row else {}
        except Exception:
            logger.exception("[weekly-recap] throughput query failed")

        # 2. Reviews by agent
        try:
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
            data.reviews_by_agent = [dict(r) for r in rows]
        except Exception:
            logger.exception("[weekly-recap] reviews query failed")

        # 3. Revert rates
        try:
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
                LIMIT 10
            """)
            data.revert_rates = [dict(r) for r in rows]
            for r in data.revert_rates:
                if float(r.get('rate') or 0) >= REVERT_RATE_RED:
                    data.warnings += 1
        except Exception:
            logger.exception("[weekly-recap] revert query failed")

        # 4. Queue status
        try:
            rows = await conn.fetch("""
                SELECT
                  status,
                  COUNT(*)::int AS cnt,
                  ROUND(EXTRACT(EPOCH FROM (now() - MIN(created_at))) / 3600.0, 1) AS oldest_hours
                FROM agent_task_queue
                WHERE created_at > now() - interval '7 days'
                GROUP BY status
                ORDER BY cnt DESC
            """)
            data.queue_status = [dict(r) for r in rows]
            for r in data.queue_status:
                hours = float(r.get('oldest_hours') or 0)
                if r['status'] == 'queued' and hours > QUEUE_AGE_RED_HOURS:
                    data.warnings += 1
                elif r['status'] == 'failed' and r['cnt'] > FAILED_TASKS_YELLOW:
                    data.warnings += 1
        except Exception:
            logger.exception("[weekly-recap] queue query failed")

        # 5. Jules 24h sessions
        try:
            row = await conn.fetchrow("""
                SELECT COUNT(*)::int AS cnt FROM agent_task_queue
                WHERE released_at > now() - interval '24 hours'
            """)
            data.jules_sessions_24h = row['cnt'] if row else 0
            if data.jules_sessions_24h >= JULES_24H_LIMIT_RED_PCT:
                data.warnings += 1
        except Exception:
            logger.exception("[weekly-recap] jules_24h query failed")

        # 6. Pending manual
        try:
            row = await conn.fetchrow("""
                SELECT COUNT(*)::int AS cnt FROM jules_pr_reviews
                WHERE last_review_json->>'verdict' = 'approved'
                  AND status NOT IN ('merged','abandoned')
                  AND updated_at > now() - interval '14 days'
            """)
            data.pending_manual = row['cnt'] if row else 0
        except Exception:
            logger.exception("[weekly-recap] pending query failed")

    return data


def render_weekly_embed(data: WeeklyRecapData):
    """Baut discord.Embed mit Ampel-Farbe + allen 6 Sektionen.

    Raises ImportError wenn discord.py nicht verfuegbar.
    """
    import discord

    if data.warnings >= 2:
        color = 0xb60205  # rot
        icon = "🔴"
        status_txt = f"{data.warnings} Warnungen"
    elif data.warnings == 1:
        color = 0xd4a017  # gelb
        icon = "🟡"
        status_txt = "1 Warnung"
    else:
        color = 0x0e8a16  # gruen
        icon = "🟢"
        status_txt = "Alle Metriken gruen"

    title = f"{icon} Weekly Review Recap — {status_txt}"
    description = f"Zeitraum: letzte 7 Tage · Generiert: {datetime.now():%Y-%m-%d %H:%M}"

    embed = discord.Embed(title=title, description=description, color=color)

    # 1. Throughput
    t = data.throughput
    if t:
        throughput_txt = (
            f"🔧 ScanAgent-delegiert: **{t.get('jules_delegated', 0)}**\n"
            f"✋ Manuelle Tasks: **{t.get('manual_tasks', 0)}**\n"
            f"💡 Jules-Suggestions: **{t.get('jules_suggestions', 0)}**\n"
            f"🚀 Released: **{t.get('released', 0)}**\n"
            f"❌ Failed: **{t.get('failed', 0)}**"
        )
        embed.add_field(name="📊 Pipeline-Throughput", value=throughput_txt, inline=False)

    # 2. Reviews by agent (kompakt als Tabelle)
    if data.reviews_by_agent:
        # Gruppiere pro Agent
        agents: Dict[str, Dict[str, int]] = {}
        for r in data.reviews_by_agent:
            agents.setdefault(r['agent_type'], {})[r['verdict']] = r['cnt']
        lines = []
        for agent, verdicts in sorted(agents.items()):
            approved = verdicts.get('approved', 0)
            revision = verdicts.get('revision_requested', 0)
            lines.append(f"`{agent:<10}` ✅ {approved} · 🟡 {revision}")
        embed.add_field(name="📋 Claude-Reviews pro Agent", value="\n".join(lines), inline=False)

    # 3. Revert rates (nur mit Reverts > 0)
    risky = [r for r in data.revert_rates if (r.get('reverted') or 0) > 0]
    if risky:
        lines = []
        for r in risky[:5]:
            rate = float(r.get('rate') or 0)
            icon_r = "🔴" if rate >= REVERT_RATE_RED else "🟡" if rate >= REVERT_RATE_YELLOW else "⚪"
            lines.append(f"{icon_r} `{r['rule_matched'][:30]}` — {r['reverted']}/{r['total']} ({rate:.0f}%)")
        embed.add_field(name="🚀 Auto-Merge Reverts (7d)", value="\n".join(lines), inline=False)

    # 4. Queue status
    if data.queue_status:
        lines = []
        for r in data.queue_status:
            hours = float(r.get('oldest_hours') or 0)
            icon_q = "🔴" if (r['status'] == 'queued' and hours > QUEUE_AGE_RED_HOURS) else "✅"
            lines.append(f"{icon_q} `{r['status']:<10}` {r['cnt']} Tasks · aeltester: {hours:.1f}h")
        embed.add_field(name="🗂️ Queue-Health", value="\n".join(lines), inline=False)

    # 5. Jules-24h
    sessions = data.jules_sessions_24h
    pct = sessions  # sessions/100
    if pct >= JULES_24H_LIMIT_RED_PCT:
        jules_icon = "🔴"
    elif pct >= JULES_24H_LIMIT_YELLOW_PCT:
        jules_icon = "🟡"
    else:
        jules_icon = "✅"
    embed.add_field(
        name="⚡ Jules-API Limits (24h)",
        value=f"{jules_icon} **{sessions} / 100** Sessions",
        inline=True,
    )

    # 6. Pending manual merges
    pending = data.pending_manual
    pending_icon = "🟡" if pending > PENDING_MANUAL_YELLOW else "✅"
    embed.add_field(
        name="⏳ Pending Manual-Merges",
        value=f"{pending_icon} **{pending}** approved, nicht gemergt",
        inline=True,
    )

    embed.set_footer(text="ShadowOps · Weekly Review · naechster Post: Fr 18:00")
    return embed
