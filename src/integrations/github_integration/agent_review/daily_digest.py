"""Daily-Digest — taeglicher Report in 🧠-ai-learning (08:15).

Aggregiert aus security_analyst DB:
- Reviews letzte 24h (nach agent_type + verdict)
- Auto-Merges + Reverts (via outcome_tracker)
- Queue-Status (queued / released / failed)
- Offene PRs wartend auf manuellen Merge (approved aber nicht auto-merged)
- 7-Tage-Trend: Revert-Rate

Output: Markdown-String (kein Embed — fuer Lesbarkeit im Channel).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class DigestData:
    """Rohes Aggregat, vor dem Rendern."""
    reviews_24h: List[Dict[str, Any]]   # [{agent_type, verdict, count}]
    auto_merges_24h: Dict[str, int]     # {total, reverted, pending}
    queue_status: Dict[str, int]        # {queued, released, failed, cancelled}
    pending_manual_merges: int
    revert_trend_7d: List[Dict[str, Any]]  # [{rule, total, reverted, rate_pct}]


async def collect_digest_data(
    *,
    jules_state_pool,          # asyncpg.Pool fuer jules_pr_reviews
    task_queue,                # TaskQueue
    outcome_tracker,           # OutcomeTracker
) -> DigestData:
    """Fuehrt alle Queries aus, gibt DigestData zurueck.

    Alle Queries sind einzeln geschuetzt — fehlt eine Quelle, wird ein
    leerer Default genutzt statt zu crashen (Digest soll posten, selbst
    wenn eine Stat nicht verfuegbar ist).
    """
    reviews_24h = await _query_reviews_24h(jules_state_pool)
    auto_merges = await _safe(outcome_tracker.last_24h_summary, default={
        "total": 0, "reverted": 0, "pending": 0,
    })
    queue_status = await _safe(task_queue.count_by_status, default={})
    pending = await _query_pending_manual_merges(jules_state_pool)
    trend = await _safe(
        lambda: outcome_tracker.revert_rate_by_rule(days=7),
        default=[],
    )
    return DigestData(
        reviews_24h=reviews_24h,
        auto_merges_24h=auto_merges,
        queue_status=queue_status,
        pending_manual_merges=pending,
        revert_trend_7d=trend,
    )


async def _safe(coro_fn, *, default):
    """Ruft coro_fn() auf, returnt default bei Exception."""
    try:
        return await coro_fn()
    except Exception:
        logger.exception("[daily-digest] query failed, using default")
        return default


async def _query_reviews_24h(pool) -> List[Dict[str, Any]]:
    """Gruppiert jules_pr_reviews der letzten 24h nach agent_type + verdict.

    Verdict wird aus last_review_json extrahiert — es gibt keine separate
    verdict-Spalte (Migration hat nur agent_type hinzugefuegt).
    """
    if pool is None:
        return []
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT agent_type,
                          last_review_json->>'verdict' AS verdict,
                          COUNT(*)::int AS cnt
                   FROM jules_pr_reviews
                   WHERE updated_at > now() - interval '24 hours'
                     AND last_review_json IS NOT NULL
                     AND last_review_json->>'verdict' IS NOT NULL
                   GROUP BY agent_type, last_review_json->>'verdict'
                   ORDER BY agent_type, verdict"""
            )
        return [
            {"agent_type": r["agent_type"], "verdict": r["verdict"], "count": r["cnt"]}
            for r in rows
        ]
    except Exception:
        logger.exception("[daily-digest] reviews_24h query failed")
        return []


async def _query_pending_manual_merges(pool) -> int:
    """Zaehlt PRs mit verdict=approved (aus JSON) aber status != merged."""
    if pool is None:
        return 0
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT COUNT(*)::int AS cnt FROM jules_pr_reviews
                   WHERE last_review_json->>'verdict' = 'approved'
                     AND status NOT IN ('merged','abandoned')
                     AND updated_at > now() - interval '7 days'"""
            )
        return int(row["cnt"] or 0)
    except Exception:
        logger.exception("[daily-digest] pending_manual query failed")
        return 0


# ─────────── Rendering ───────────

def render_digest(data: DigestData) -> str:
    """Baut den Markdown-Report aus DigestData."""
    lines = ["## 🧠 Multi-Agent Review — Daily Digest", ""]

    lines.extend(_render_reviews_section(data.reviews_24h))
    lines.append("")
    lines.extend(_render_auto_merge_section(data.auto_merges_24h))
    lines.append("")
    lines.extend(_render_pending_section(data.pending_manual_merges))
    lines.append("")
    lines.extend(_render_queue_section(data.queue_status))
    lines.append("")
    lines.extend(_render_trend_section(data.revert_trend_7d))

    return "\n".join(lines)


def _render_reviews_section(reviews: List[Dict[str, Any]]) -> List[str]:
    lines = ["### 📋 Reviews letzte 24h"]
    if not reviews:
        lines.append("_Keine Reviews in den letzten 24h._")
        return lines

    # Gruppiert als Tabelle: agent | approved | revision_requested
    agents = {}
    for r in reviews:
        a = r["agent_type"]
        agents.setdefault(a, {})[r["verdict"]] = r["count"]

    lines.append("| Agent | ✅ Approved | 🟡 Revision |")
    lines.append("|-------|-------------|-------------|")
    for agent in sorted(agents.keys()):
        verdicts = agents[agent]
        lines.append(
            f"| {agent} | {verdicts.get('approved', 0)} | "
            f"{verdicts.get('revision_requested', 0)} |"
        )
    return lines


def _render_auto_merge_section(summary: Dict[str, int]) -> List[str]:
    total = summary.get("total", 0)
    reverted = summary.get("reverted", 0)
    pending = summary.get("pending", 0)
    lines = ["### 🚀 Auto-Merges letzte 24h"]
    if total == 0:
        lines.append("_Keine Auto-Merges._")
        return lines
    lines.append(f"- **Gesamt:** {total}")
    if reverted > 0:
        rate = 100.0 * reverted / total
        lines.append(f"- **Revertet:** {reverted} ({rate:.1f}%)")
    else:
        lines.append("- **Revertet:** 0 ✅")
    if pending > 0:
        lines.append(f"- **Noch offen (24h-Check):** {pending}")
    return lines


def _render_pending_section(count: int) -> List[str]:
    lines = ["### ⏳ Wartend auf manuellen Merge"]
    if count == 0:
        lines.append("_Keine offenen Reviews._")
    else:
        lines.append(f"**{count}** PRs mit Claude-Approval, noch nicht gemerged.")
    return lines


def _render_queue_section(status: Dict[str, int]) -> List[str]:
    lines = ["### 🗂️ Queue-Status"]
    if not status:
        lines.append("_Queue leer._")
        return lines
    parts = []
    for s in ("queued", "released", "failed", "cancelled"):
        if s in status:
            parts.append(f"{s}: {status[s]}")
    lines.append(" · ".join(parts) if parts else "_Queue leer._")
    return lines


def _render_trend_section(trend: List[Dict[str, Any]]) -> List[str]:
    lines = ["### 📈 7-Tage Revert-Trend"]
    if not trend:
        lines.append("_Keine geprueften Auto-Merges in den letzten 7 Tagen._")
        return lines
    # Nur Regeln mit mind. 1 Revert zeigen
    risky = [t for t in trend if t.get("reverted", 0) > 0]
    if not risky:
        lines.append("✅ Alle Auto-Merges stabil, keine Reverts.")
        return lines
    lines.append("| Rule | Total | Reverted | Rate |")
    lines.append("|------|-------|----------|------|")
    for t in risky[:5]:  # max 5
        lines.append(
            f"| `{t['rule_matched']}` | {t['total']} | "
            f"{t['reverted']} | {t['rate_pct']:.1f}% |"
        )
    return lines
