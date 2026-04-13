"""Stufe 5: Distribute — Discord + Changelog-DB + Web-Export + Learning + Metriken."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from patch_notes.context import PipelineContext

logger = logging.getLogger('shadowops')


async def distribute(ctx: PipelineContext, bot=None) -> None:
    """Stufe 5: Alles verteilen — Discord, DB, Web, Learning."""
    if bot is None:
        logger.warning(f"[v6] {ctx.project}: Kein Bot — Distribution übersprungen")
        return

    # 1. Discord Embed bauen
    embed = _build_embed(ctx)

    # 2. Internal Channel
    await _send_internal(bot, embed, ctx)

    # 3. Customer Channels (mit Feedback-Buttons)
    await _send_customer(bot, embed, ctx)

    # 4. Changelog-DB + Web-Export
    await _store_changelog(ctx, bot)

    # 5. Metriken loggen
    _log_metrics(ctx)


def _build_embed(ctx: PipelineContext) -> discord.Embed:
    """Baue Discord Embed aus PipelineContext."""
    project_config = ctx.project_config
    color = project_config.get('color', 0x3498DB)
    changelog_url = project_config.get('patch_notes', {}).get('changelog_url', '')

    embed = discord.Embed(
        title=f"v{ctx.version} — {ctx.title}",
        color=color,
    )

    if changelog_url:
        slug = ctx.version.replace('.', '-')
        embed.url = f"{changelog_url}/{slug}"

    # TL;DR
    if ctx.tldr:
        embed.description = f"> {ctx.tldr}"

    # Changes als Felder
    if ctx.changes:
        # Player-facing Changes zuerst
        for change in ctx.changes[:8]:
            if not isinstance(change, dict):
                continue
            ctype = change.get('type', 'other')
            desc = change.get('description', '')
            badge = _type_to_emoji(ctype)

            # Details als Unterpunkte
            details = change.get('details', [])
            if details:
                detail_text = '\n'.join(f"  → {d}" for d in details[:3])
                desc = f"{desc}\n{detail_text}"

            if desc:
                embed.add_field(
                    name=f"{badge} {desc[:256]}",
                    value='',
                    inline=False,
                )
    elif ctx.web_content:
        # Fallback: Web-Content als Description (gekürzt)
        embed.description = ctx.web_content[:2000]

    # Footer mit Stats
    footer_parts = [f"v{ctx.version}"]
    stats = ctx.git_stats
    if stats.get('commits'):
        footer_parts.append(f"{stats['commits']} Commits")
    if stats.get('files_changed'):
        footer_parts.append(f"{stats['files_changed']} Dateien")
    if ctx.team_credits:
        names = ', '.join(c['name'] for c in ctx.team_credits[:3])
        footer_parts.append(names)
    embed.set_footer(text=' · '.join(footer_parts))
    embed.timestamp = datetime.now(timezone.utc)

    return embed


def _type_to_emoji(ctype: str) -> str:
    """Change-Typ → Emoji Badge."""
    return {
        'feature': '🆕', 'content': '📦', 'gameplay': '🎮',
        'design': '🎨', 'performance': '⚡', 'multiplayer': '👥',
        'fix': '🐛', 'breaking': '💥', 'infrastructure': '🛡️',
        'improvement': '✨', 'docs': '📖', 'security': '🔒',
    }.get(ctype, '📝')


async def _send_internal(bot, embed: discord.Embed, ctx: PipelineContext) -> None:
    """Sende an internen Channel (Preview)."""
    try:
        from utils.state_manager import get_state_manager
        state = get_state_manager()
        channel_id = state.get_channel('internal_updates')
        if not channel_id:
            return

        channel = bot.get_channel(channel_id)
        if channel:
            await channel.send(embed=embed)
    except Exception as e:
        logger.debug(f"[v6] Internal send fehlgeschlagen: {e}")


async def _send_customer(bot, embed: discord.Embed, ctx: PipelineContext) -> None:
    """Sende an Kunden-Channels mit Feedback-Buttons."""
    project_config = ctx.project_config
    pn_config = project_config.get('patch_notes', {})

    # Update Channel
    channel_id = pn_config.get('update_channel_id')
    if channel_id:
        try:
            channel = bot.get_channel(int(channel_id))
            if channel:
                # Feedback-Buttons anhängen (bestehende View wiederverwenden)
                view = _get_feedback_view(ctx)
                role_mention = pn_config.get('update_channel_role_mention', '')
                content = f"<@&{role_mention}> Neues Update verfügbar!" if role_mention else None

                msg = await channel.send(
                    content=content,
                    embed=embed,
                    view=view,
                    allowed_mentions=discord.AllowedMentions(roles=True) if content else None,
                )
                ctx.sent_message_ids.append([channel.id, msg.id])
        except Exception as e:
            logger.warning(f"[v6] Customer channel {channel_id} fehlgeschlagen: {e}")

    # Internal Channel
    internal_id = pn_config.get('internal_channel_id')
    if internal_id:
        try:
            channel = bot.get_channel(int(internal_id))
            if channel:
                role_mention = pn_config.get('internal_channel_role_mention', '')
                content = f"<@&{role_mention}> Neues Update verfügbar!" if role_mention else None
                msg = await channel.send(
                    content=content,
                    embed=embed,
                    allowed_mentions=discord.AllowedMentions(roles=True) if content else None,
                )
                ctx.sent_message_ids.append([channel.id, msg.id])
        except Exception as e:
            logger.warning(f"[v6] Internal customer channel {internal_id} fehlgeschlagen: {e}")


def _get_feedback_view(ctx: PipelineContext):
    """Lade bestehende Feedback-Buttons View."""
    try:
        from integrations.patch_notes_feedback import PatchNotesFeedbackView
        return PatchNotesFeedbackView(
            project=ctx.project,
            version=ctx.version,
        )
    except ImportError:
        return None


async def _store_changelog(ctx: PipelineContext, bot) -> None:
    """Speichere in Changelog-DB + Web-Export."""
    github = getattr(bot, 'github_integration', None)
    if not github:
        return

    exporter = getattr(github, 'web_exporter', None)
    if not exporter:
        return

    try:
        await exporter.export_and_store(
            project=ctx.project,
            version=ctx.version,
            title=ctx.title,
            tldr=ctx.tldr,
            content=ctx.web_content,
            stats=ctx.git_stats,
            language=ctx.project_config.get('patch_notes', {}).get('language', 'de'),
            changes=ctx.changes,
            seo_keywords=ctx.seo_keywords,
        )
        logger.info(f"[v6] {ctx.project} v{ctx.version}: Changelog gespeichert")
    except Exception as e:
        logger.warning(f"[v6] Changelog-Export fehlgeschlagen: {e}")


def _log_metrics(ctx: PipelineContext) -> None:
    """Pipeline-Metriken als JSON loggen (kompatibel mit bestehendem Monitoring)."""
    metrics = {
        'project': ctx.project,
        'version': ctx.version,
        'trigger': ctx.trigger,
        'update_size': ctx.update_size,
        'total_commits': len(ctx.enriched_commits or ctx.raw_commits),
        'groups': len(ctx.groups),
        'player_facing_groups': sum(1 for g in ctx.groups if g.get('is_player_facing')),
        'infra_groups': sum(1 for g in ctx.groups if not g.get('is_player_facing')),
        'ai_engine': ctx.ai_engine_used,
        'variant_id': ctx.variant_id,
        'generation_time_s': ctx.generation_time_s,
        'pipeline_total_time_s': ctx.metrics.get('pipeline_total_time_s', 0),
        'hallucinations_caught': len(ctx.fixes_applied),
        'warnings': len(ctx.warnings),
        'version_source': ctx.version_source,
        'messages_sent': len(ctx.sent_message_ids),
    }
    logger.info("METRICS|patch_notes_pipeline|%s", json.dumps(metrics, default=str))
