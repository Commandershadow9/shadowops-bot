"""Review-Embed-Formatter fuer Multi-Agent PR-Reviews.

Farbkodiert:
- GRUEN  (#0e8a16) — approved + merged (auto)
- BLAU   (#0366d6) — approved + label (manual pending)
- GELB   (#d4a017) — revision_requested
- ORANGE (#d73a49) — escalated (> max_iterations)
- ROT    (#b60205) — error / crashed

Konsumenten-API: build_review_embed() gibt `discord.Embed` zurueck oder
wirft `ImportError` wenn discord.py nicht verfuegbar (Unit-Tests mocken).
"""
from __future__ import annotations

from typing import Any, Dict, Optional


# Farbkonstanten als int (Discord erwartet int, nicht hex-string)
COLOR_AUTO_MERGED  = 0x0e8a16   # gruen
COLOR_APPROVED     = 0x0366d6   # blau (Manual-Merge pending)
COLOR_REVISION     = 0xd4a017   # gelb
COLOR_ESCALATED    = 0xd73a49   # orange
COLOR_ERROR        = 0xb60205   # rot


def pick_color(*, verdict: str, auto_merged: bool = False, escalated: bool = False) -> int:
    """Entscheidet die Embed-Farbe basierend auf Verdict + Flags."""
    if escalated:
        return COLOR_ESCALATED
    if verdict == "approved":
        return COLOR_AUTO_MERGED if auto_merged else COLOR_APPROVED
    if verdict == "revision_requested":
        return COLOR_REVISION
    if verdict == "error":
        return COLOR_ERROR
    return COLOR_APPROVED


def build_review_embed(
    *,
    agent_name: str,
    repo: str,
    pr_number: int,
    pr_url: str,
    review: Dict[str, Any],
    iteration: int,
    max_iterations: int,
    auto_merged: bool = False,
    escalated: bool = False,
    model_used: Optional[str] = None,
):
    """Baut ein discord.Embed fuer einen Review-Post.

    Raises ImportError wenn discord.py nicht verfuegbar (Tests mocken).
    """
    import discord  # local import — Tests patchen bei Bedarf

    verdict = review.get("verdict", "unknown")
    blockers = review.get("blockers") or []
    suggestions = review.get("suggestions") or []
    nits = review.get("nits") or []
    summary = (review.get("summary") or "").strip()

    color = pick_color(verdict=verdict, auto_merged=auto_merged, escalated=escalated)

    # Titel-Icon nach Status
    if escalated:
        icon = "⚠️"
    elif auto_merged:
        icon = "✅"
    elif verdict == "approved":
        icon = "🔵"
    elif verdict == "revision_requested":
        icon = "🟡"
    else:
        icon = "🔴"

    title_parts = [icon, agent_name.upper(), f"PR #{pr_number}", f"— {verdict.upper()}"]
    if auto_merged:
        title_parts.append("(auto-merged)")
    title = " ".join(title_parts)

    embed = discord.Embed(title=title, url=pr_url, color=color)
    embed.add_field(name="Repo", value=f"`{repo}`", inline=True)
    embed.add_field(
        name="Iteration",
        value=f"{iteration}/{max_iterations}",
        inline=True,
    )
    embed.add_field(
        name="Findings",
        value=f"🔴 {len(blockers)} · 🟡 {len(suggestions)} · ⚪ {len(nits)}",
        inline=True,
    )

    if summary:
        # Embed-Feld-Limit: 1024 chars
        embed.add_field(name="Summary", value=summary[:1020], inline=False)

    if blockers:
        blocker_lines = []
        for b in blockers[:3]:  # max 3 Blocker im Embed
            sev = b.get("severity", "?")
            t = (b.get("title") or b.get("reason") or "")[:80]
            blocker_lines.append(f"• **[{sev}]** {t}")
        if len(blockers) > 3:
            blocker_lines.append(f"• … +{len(blockers) - 3} weitere")
        embed.add_field(
            name="Blockers",
            value="\n".join(blocker_lines)[:1020],
            inline=False,
        )

    footer_parts = ["ShadowOps SecOps"]
    if model_used:
        footer_parts.append(f"Model: {model_used}")
    embed.set_footer(text=" · ".join(footer_parts))

    return embed
