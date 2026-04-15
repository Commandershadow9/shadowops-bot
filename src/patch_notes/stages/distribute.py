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

# Discord Limits
_EMBED_DESC_LIMIT = 4096
_EMBED_TOTAL_LIMIT = 6000


async def distribute(ctx: PipelineContext, bot=None) -> None:
    """Stufe 5: Alles verteilen — Discord, DB, Web, Learning."""
    if bot is None:
        logger.warning(f"[v6] {ctx.project}: Kein Bot — Distribution übersprungen")
        return

    changelog_url = ctx.project_config.get('patch_notes', {}).get('changelog_url', '')

    # 1. Discord Embed bauen (Summary-Mode wenn changelog_url gesetzt)
    if changelog_url:
        embed = _build_summary_embed(ctx, changelog_url)
    else:
        embed = _build_full_embed(ctx)

    # 2. Internal Channel (Preview)
    await _send_internal(bot, embed, ctx)

    # 3. Customer Channels (mit Feedback-Buttons)
    await _send_customer(bot, embed, ctx)

    # 4. External Notifications (Kunden-Guilds)
    await _send_external(bot, embed, ctx)

    # 5. Changelog-DB + Web-Export
    await _store_changelog(ctx, bot)

    # 6. Message-IDs in StateManager persistieren (für Rollback)
    _persist_message_ids(ctx, bot)

    # 7. Learning-DB: Generation aufzeichnen
    await _record_learning(ctx, bot)

    # 8. Metriken loggen
    _log_metrics(ctx)


# ── Embed Builder ──────────────────────────────────────────────


def _build_summary_embed(ctx: PipelineContext, changelog_url: str) -> discord.Embed:
    """Kurzformat für Projekte MIT Web-Changelog — TL;DR + Highlights + Link."""
    color = ctx.project_config.get('color', 0x3498DB)
    slug = ctx.version.replace('.', '-')

    embed = discord.Embed(
        title=f"v{ctx.version} — {ctx.title}",
        url=f"{changelog_url}/{slug}",
        color=color,
    )

    # TL;DR als Blockquote
    parts = []
    if ctx.tldr:
        parts.append(f"> {ctx.tldr}")
        parts.append("")

    # Max 6 Highlights aus Changes
    if ctx.changes:
        for change in ctx.changes[:6]:
            if not isinstance(change, dict):
                continue
            badge = _type_to_emoji(change.get('type', 'other'))
            desc = change.get('description', '')
            # Inline-Credit wenn vorhanden
            author = change.get('author', '')
            credit = f" · {author}" if author else ""
            parts.append(f"{badge} {desc[:200]}{credit}")
        if len(ctx.changes) > 6:
            parts.append(f"*+{len(ctx.changes) - 6} weitere Änderungen*")

    # "Alle Details" Link
    parts.append("")
    parts.append(f"**[Alle Details auf der Website →]({changelog_url}/{slug})**")

    description = '\n'.join(parts)
    embed.description = description[:_EMBED_DESC_LIMIT]

    embed.set_footer(text=_build_footer_text(ctx))
    embed.timestamp = datetime.now(timezone.utc)

    return embed


def _build_full_embed(ctx: PipelineContext) -> discord.Embed:
    """Vollformat für Projekte OHNE Web-Changelog (Discord-only).

    Baut ein reiches Description-Format mit Kategorie-Headern, Inline-Credits
    und Details — portiert von v5 _description_from_structured.
    """
    color = ctx.project_config.get('color', 0x3498DB)
    is_major = len(ctx.enriched_commits or ctx.raw_commits) >= 15

    embed = discord.Embed(
        title=f"v{ctx.version} — {ctx.title}",
        color=color,
    )

    parts: list[str] = []

    # TL;DR als Blockquote
    if ctx.tldr:
        parts.append(f"> {ctx.tldr}")
        parts.append("")

    # Summary als Einleitung (Discord-only Storytelling)
    if isinstance(ctx.ai_result, dict):
        summary = ctx.ai_result.get('summary', '')
        if summary and summary != ctx.tldr:
            parts.append(summary)
            parts.append("")

    if ctx.changes:
        # Changes nach Typ gruppieren
        features = [c for c in ctx.changes if isinstance(c, dict) and c.get('type') == 'feature']
        fixes = [c for c in ctx.changes if isinstance(c, dict) and c.get('type') == 'fix']
        improvements = [c for c in ctx.changes if isinstance(c, dict) and c.get('type') == 'improvement']
        breaking = []
        if isinstance(ctx.ai_result, dict):
            breaking = ctx.ai_result.get('breaking_changes', [])
        other = [c for c in ctx.changes if isinstance(c, dict) and c.get('type') not in ('feature', 'fix', 'improvement')]

        # Inline-Credits zeigen wenn mindestens 1 Author
        unique_authors = {c.get('author', '') for c in ctx.changes if isinstance(c, dict) and c.get('author')}
        show_author = len(unique_authors) >= 1

        # Dynamische Limits je nach Update-Größe
        max_features = 8 if is_major else 6
        max_fixes = 6
        max_improvements = 5

        if features:
            parts.append("**🆕 Neue Features**")
            for f in features[:max_features]:
                parts.append(_format_change_line(f, show_author))
                details = f.get('details', [])
                for d in details[:2]:
                    parts.append(f"  • {d}")
            if len(features) > max_features:
                parts.append(f"  *+{len(features) - max_features} weitere*")
            parts.append("")

        if breaking:
            parts.append("**⚠️ Breaking Changes**")
            for b in breaking[:3]:
                parts.append(f"⚠️ {b}")
            parts.append("")

        if fixes:
            parts.append("**🐛 Bugfixes**")
            for f in fixes[:max_fixes]:
                parts.append(_format_change_line(f, show_author))
            if len(fixes) > max_fixes:
                parts.append(f"  *+{len(fixes) - max_fixes} weitere*")
            parts.append("")

        if improvements:
            parts.append("**⚡ Verbesserungen**")
            for i in improvements[:max_improvements]:
                parts.append(_format_change_line(i, show_author))
            if len(improvements) > max_improvements:
                parts.append(f"  *+{len(improvements) - max_improvements} weitere*")
            parts.append("")

        if other:
            parts.append("**📝 Weitere Änderungen**")
            for o in other[:3]:
                parts.append(_format_change_line(o, show_author))
            parts.append("")

    elif ctx.web_content:
        parts.append(ctx.web_content)

    description = '\n'.join(parts)
    embed.description = _truncate_description(description)

    embed.set_footer(text=_build_footer_text(ctx))
    embed.timestamp = datetime.now(timezone.utc)

    return embed


def _format_change_line(change: dict, show_author: bool) -> str:
    """Formatiere eine Change-Zeile mit optionaler Inline-Attribution."""
    desc = change.get('description', '')
    author = change.get('author', '')
    if show_author and author:
        return f"→ {desc} · *{author}*"
    return f"→ {desc}"


def _build_footer_text(ctx: PipelineContext) -> str:
    """Footer-Zeile: Version · Commits · Dateien · Credits."""
    parts = [f"v{ctx.version}"]
    stats = ctx.git_stats
    if stats.get('commits'):
        parts.append(f"{stats['commits']} Commits")
    if stats.get('files_changed'):
        parts.append(f"{stats['files_changed']} Dateien")
    if stats.get('lines_added'):
        parts.append(f"+{stats['lines_added']}/-{stats.get('lines_removed', 0)}")
    if ctx.team_credits:
        names = ', '.join(c['name'] for c in ctx.team_credits[:3])
        parts.append(names)
    return ' · '.join(parts)


def _truncate_description(text: str) -> str:
    """Kürze Text auf Discord Embed-Limit."""
    if len(text) <= _EMBED_DESC_LIMIT:
        return text
    return text[:_EMBED_DESC_LIMIT - 20] + "\n\n*[gekürzt...]*"


def _split_embed_for_sending(embed: discord.Embed) -> list[discord.Embed]:
    """Splitte Embed wenn Description > 4096 Zeichen (Discord Limit).

    Returns:
        Liste mit 1-3 Embeds. Nur der erste hat Titel/URL/Footer.
    """
    desc = embed.description or ""
    if len(desc) <= _EMBED_DESC_LIMIT:
        return [embed]

    chunks = []
    while desc:
        if len(desc) <= _EMBED_DESC_LIMIT:
            chunks.append(desc)
            break
        # An Zeilenumbruch splitten
        cut = desc[:_EMBED_DESC_LIMIT].rfind('\n')
        if cut < _EMBED_DESC_LIMIT // 2:
            cut = _EMBED_DESC_LIMIT
        chunks.append(desc[:cut])
        desc = desc[cut:].lstrip('\n')

    embeds = []
    for i, chunk in enumerate(chunks):
        if i == 0:
            e = discord.Embed(
                title=embed.title, url=embed.url, color=embed.color,
                description=chunk, timestamp=embed.timestamp,
            )
            e.set_footer(text=embed.footer.text if embed.footer else "")
        else:
            e = discord.Embed(
                color=embed.color, description=chunk,
            )
        embeds.append(e)

    return embeds


def _type_to_emoji(ctype: str) -> str:
    """Change-Typ → Emoji Badge."""
    return {
        'feature': '🆕', 'content': '📦', 'gameplay': '🎮',
        'design': '🎨', 'performance': '⚡', 'multiplayer': '👥',
        'fix': '🐛', 'breaking': '💥', 'infrastructure': '🛡️',
        'improvement': '✨', 'docs': '📖', 'security': '🔒',
    }.get(ctype, '📝')


# ── Sending ────────────────────────────────────────────────────


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
            for e in _split_embed_for_sending(embed):
                await channel.send(embed=e)
    except Exception as e:
        logger.debug(f"[v6] Internal send fehlgeschlagen: {e}")


async def _send_customer(bot, embed: discord.Embed, ctx: PipelineContext) -> None:
    """Sende an Kunden-Channels mit Feedback-Buttons."""
    pn_config = ctx.project_config.get('patch_notes', {})

    # Channel-IDs werden zur Laufzeit von bot.py auf Top-Level des project_config injiziert.
    # patch_notes.* wird als Fallback behalten, falls jemand sie manuell dort pflegt.
    channel_id = pn_config.get('update_channel_id') or ctx.project_config.get('update_channel_id')
    if channel_id:
        await _send_to_channel(
            bot, int(channel_id), embed, ctx,
            role_mention=pn_config.get('update_channel_role_mention', ''),
            with_feedback=True,
        )

    internal_id = pn_config.get('internal_channel_id') or ctx.project_config.get('internal_channel_id')
    if internal_id:
        await _send_to_channel(
            bot, int(internal_id), embed, ctx,
            role_mention=pn_config.get('internal_channel_role_mention', ''),
            with_feedback=False,
        )


async def _send_external(bot, embed: discord.Embed, ctx: PipelineContext) -> None:
    """Sende an externe Kunden-Guilds (external_notifications Config)."""
    external_notifs = ctx.project_config.get('external_notifications', [])
    if not external_notifs:
        return

    for notif_config in external_notifs:
        if not notif_config.get('enabled', False):
            continue
        if not notif_config.get('notify_on', {}).get('git_push', True):
            continue

        channel_id = notif_config.get('channel_id')
        if not channel_id:
            continue

        await _send_to_channel(
            bot, int(channel_id), embed, ctx,
            with_feedback=True,
        )


async def _send_to_channel(
    bot, channel_id: int, embed: discord.Embed, ctx: PipelineContext,
    role_mention: str = '', with_feedback: bool = False,
) -> None:
    """Generische Send-Funktion mit Splitting, Feedback und Message-ID-Tracking."""
    try:
        channel = bot.get_channel(channel_id)
        if not channel:
            logger.warning(f"[v6] Channel {channel_id} nicht gefunden")
            return

        embeds = _split_embed_for_sending(embed)
        view = _get_feedback_view(ctx) if with_feedback else None

        for i, e in enumerate(embeds):
            is_first = (i == 0)
            is_last = (i == len(embeds) - 1)

            content = None
            if is_first and role_mention:
                content = f"<@&{role_mention}> Neues Update verfügbar!"

            msg = await channel.send(
                content=content,
                embed=e,
                view=view if is_last else None,
                allowed_mentions=discord.AllowedMentions(roles=True) if content else None,
            )

            # Nur erste Message tracken (für Rollback)
            if is_first:
                ctx.sent_message_ids.append([channel_id, msg.id])

    except Exception as e:
        logger.warning(f"[v6] Channel {channel_id} fehlgeschlagen: {e}")


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


# ── Persistenz & Changelog ─────────────────────────────────────


def _persist_message_ids(ctx: PipelineContext, bot) -> None:
    """Speichere sent_message_ids in StateManager (für retract_patch_notes)."""
    if not ctx.sent_message_ids:
        return
    try:
        from utils.state_manager import get_state_manager
        state = get_state_manager()
        guild_id = None
        for guild in bot.guilds:
            guild_id = guild.id
            break
        if not guild_id:
            return

        msgs = state.get_value(guild_id, 'patch_notes_messages', {})
        entry_key = f"{ctx.project}:{ctx.version}"
        msgs[entry_key] = [
            {'channel_id': cid, 'message_id': mid}
            for cid, mid in ctx.sent_message_ids
        ]

        # FIFO: Max 50 Releases behalten
        if len(msgs) > 50:
            keys = list(msgs.keys())
            for old_key in keys[:len(keys) - 50]:
                del msgs[old_key]

        state.set_value(guild_id, 'patch_notes_messages', msgs)
    except Exception as e:
        logger.debug(f"[v6] Message-ID Persistenz fehlgeschlagen: {e}")


async def retract_patch_notes(bot, project: str, version: str) -> int:
    """Lösche alle Discord-Messages einer Patch Note (Rollback).

    Returns:
        Anzahl erfolgreich gelöschter Messages.
    """
    try:
        from utils.state_manager import get_state_manager
        state = get_state_manager()
        guild_id = None
        for guild in bot.guilds:
            guild_id = guild.id
            break
        if not guild_id:
            return 0

        msgs = state.get_value(guild_id, 'patch_notes_messages', {})
        entry_key = f"{project}:{version}"
        entries = msgs.get(entry_key, [])
        if not entries:
            logger.warning(f"[v6] Keine Messages für {entry_key} gefunden")
            return 0

        retracted = 0
        for entry in entries:
            try:
                ch = bot.get_channel(entry['channel_id'])
                if ch:
                    msg = ch.get_partial_message(entry['message_id'])
                    await msg.delete()
                    retracted += 1
            except Exception as e:
                logger.warning(f"[v6] Retract fehlgeschlagen: {e}")

        if entry_key in msgs:
            del msgs[entry_key]
            state.set_value(guild_id, 'patch_notes_messages', msgs)

        logger.info(f"[v6] 🗑️ {retracted}/{len(entries)} Messages für {entry_key} retracted")
        return retracted
    except Exception as e:
        logger.error(f"[v6] Retract Fehler: {e}")
        return 0


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


# ── Metriken ───────────────────────────────────────────────────


async def _record_learning(ctx: PipelineContext, bot) -> None:
    """Zeichne Generation in Learning-DB auf (für Feedback-Loop)."""
    github = getattr(bot, 'github_integration', None)
    if not github:
        return
    learning = getattr(github, 'patch_notes_learning', None)
    if not learning:
        return

    # Erste gesendete Message-ID als Discord-Referenz
    discord_msg_id = None
    if ctx.sent_message_ids:
        discord_msg_id = str(ctx.sent_message_ids[0][1])

    try:
        await learning.record_generation(
            project=ctx.project,
            version=ctx.version,
            variant_id=ctx.variant_id or None,
            title=ctx.title,
            tldr=ctx.tldr or '',
            ai_engine=ctx.ai_engine_used or 'unknown',
            discord_message_id=discord_msg_id,
        )
        logger.debug(f"[v6] {ctx.project} v{ctx.version}: Learning-DB aufgezeichnet")
    except Exception as e:
        logger.debug(f"[v6] Learning-DB recording fehlgeschlagen: {e}")


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
