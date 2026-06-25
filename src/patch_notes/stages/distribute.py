"""Stufe 5: Distribute — Discord + Changelog-DB + Web-Export + Learning + Metriken."""

from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path
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

    # 9. release_notes.md archivieren + Template wiederherstellen
    #    NUR bei erfolgreichem Discord-Send (messages_sent > 0), damit ein halb-
    #    fehlgeschlagener Release nicht den Dev-Kontext des Users verliert.
    if ctx.sent_message_ids:
        _archive_release_notes(ctx)


# ── Embed Builder ──────────────────────────────────────────────


_HIGHLIGHT_COUNT_BY_SIZE = {
    "small": 3, "normal": 5, "big": 6, "major": 8, "mega": 10,
}
_TITLE_PREFIX_BY_SIZE = {
    "mega": "🚀 ",
    "major": "⚡ ",
}


def _build_summary_embed(ctx: PipelineContext, changelog_url: str) -> discord.Embed:
    """Kurzformat für Projekte MIT Web-Changelog — bei mega/major hypig, sonst kompakt."""
    color = ctx.project_config.get('color', 0x3498DB)
    slug = ctx.version.replace('.', '-')
    size = ctx.update_size or "normal"
    is_hype = size in ("mega", "major")

    title_prefix = _TITLE_PREFIX_BY_SIZE.get(size, "")
    embed = discord.Embed(
        title=f"{title_prefix}v{ctx.version} — {ctx.title}",
        url=f"{changelog_url}/{slug}",
        color=color,
    )

    parts: list[str] = []

    # TL;DR — bei mega/major als fetter Lead, sonst als Blockquote (dezenter)
    if ctx.tldr:
        parts.append(f"**{ctx.tldr}**" if is_hype else f"> {ctx.tldr}")
        parts.append("")

    # Hero-Stats-Zeile bei mega/major (auch visuell sichtbar, nicht im Footer begraben)
    if is_hype:
        hero = _build_hero_stats(ctx)
        if hero:
            parts.append(hero)
            parts.append("═══════════════════════════════════════")
            parts.append("")

    # Highlights: nach Typ gruppieren, Section-Header bei mega/major, Leerzeile zwischen Items
    max_n = _HIGHLIGHT_COUNT_BY_SIZE.get(size, 6)
    changes_subset = [c for c in (ctx.changes or []) if isinstance(c, dict)][:max_n]

    if changes_subset:
        if is_hype:
            # Nach Typ gruppieren, je Section eine Headline
            sections = _group_changes_for_embed(changes_subset)
            for section_title, items in sections:
                if not items:
                    continue
                parts.append(f"**{section_title}**")
                for change in items:
                    badge = _type_to_emoji(change.get('type', 'other'))
                    desc = _discord_change_summary(change)
                    parts.append(f"{badge} **{desc[:220]}**")
                    # bei mega noch 1. Detail als Sub-Bullet
                    if size == "mega":
                        subline = _discord_change_subline(change)
                        if subline:
                            parts.append(f"   ↳ {subline[:180]}")
                    # Inline-Credit: authors-Liste (Multi-Author) oder single author (legacy)
                    credit = _format_authors_line(change)
                    if credit:
                        parts.append(credit)
                    # Leerzeile zwischen Highlights — atmet
                    parts.append("")
        else:
            # Kompakte Variante: keine Sections, aber Leerzeile zwischen Items
            for change in changes_subset:
                badge = _type_to_emoji(change.get('type', 'other'))
                desc = _discord_change_summary(change)
                display = _authors_display(change)
                credit = f" · {display}" if display else ""
                parts.append(f"{badge} {desc[:200]}{credit}")

        if len(ctx.changes) > max_n:
            parts.append(f"*… +{len(ctx.changes) - max_n} weitere Änderungen auf der Website*")
            parts.append("")

    # "Alle Details" Link
    label = "📖 Alle Details auf der Website" if is_hype else "Alle Details auf der Website"
    parts.append(f"**[{label} →]({changelog_url}/{slug})**")

    description = '\n'.join(parts)
    embed.description = description[:_EMBED_DESC_LIMIT]

    embed.set_footer(text=_build_footer_text(ctx))
    embed.timestamp = datetime.now(timezone.utc)

    return embed


_SECTION_ORDER = [
    ("🆕 Neue Features", ("feature", "content")),
    ("🎮 Gameplay & UX", ("gameplay", "multiplayer")),
    ("🎨 Design & Look", ("design",)),
    ("⚡ Performance", ("performance",)),
    ("🐛 Fixes", ("fix",)),
    ("🛡️ Stabilität & Security", ("security", "infrastructure", "breaking")),
    ("📖 Weiteres", ("improvement", "docs", "refactor")),
]


def _group_changes_for_embed(changes: list[dict]) -> list[tuple[str, list[dict]]]:
    """Gruppiere Changes nach Typ für Section-Header im Embed.

    Unbekannte Typen landen in 'Weiteres'. Reihenfolge wie _SECTION_ORDER.
    """
    buckets: dict[str, list[dict]] = {title: [] for title, _ in _SECTION_ORDER}
    fallback = "📖 Weiteres"
    for c in changes:
        ctype = (c.get('type') or '').lower()
        placed = False
        for title, types in _SECTION_ORDER:
            if ctype in types:
                buckets[title].append(c)
                placed = True
                break
        if not placed:
            buckets[fallback].append(c)
    return [(title, buckets[title]) for title, _ in _SECTION_ORDER if buckets[title]]


def _build_hero_stats(ctx: PipelineContext) -> str:
    """Hero-Zeile mit den wichtigen Kennzahlen — nur bei big/major/mega sichtbar."""
    stats = ctx.git_stats or {}
    parts: list[str] = []
    commits = stats.get('commits') or len(ctx.enriched_commits or ctx.raw_commits or [])
    if commits:
        parts.append(f"🔢 **{commits} Commits**")
    files_changed = stats.get('files_changed')
    if files_changed:
        parts.append(f"📁 {files_changed} Dateien")
    added = stats.get('lines_added')
    removed = stats.get('lines_removed', 0)
    if added:
        parts.append(f"✏️ +{added:,} / -{removed:,} Zeilen".replace(",", "."))
    if ctx.groups:
        parts.append(f"🧩 {len(ctx.groups)} Themen")
    return " · ".join(parts)


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
                for d in _change_detail_lines(f)[:3]:
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
                for d in _change_detail_lines(f)[:1]:
                    parts.append(f"  • {d}")
            if len(fixes) > max_fixes:
                parts.append(f"  *+{len(fixes) - max_fixes} weitere*")
            parts.append("")

        if improvements:
            parts.append("**⚡ Verbesserungen**")
            for i in improvements[:max_improvements]:
                parts.append(_format_change_line(i, show_author))
                for d in _change_detail_lines(i)[:1]:
                    parts.append(f"  • {d}")
            if len(improvements) > max_improvements:
                parts.append(f"  *+{len(improvements) - max_improvements} weitere*")
            parts.append("")

        if other:
            parts.append("**📝 Weitere Änderungen**")
            for o in other[:3]:
                parts.append(_format_change_line(o, show_author))
                for d in _change_detail_lines(o)[:1]:
                    parts.append(f"  • {d}")
            parts.append("")

    elif ctx.web_content:
        parts.append(ctx.web_content)

    description = '\n'.join(parts)
    embed.description = _truncate_description(description)

    embed.set_footer(text=_build_footer_text(ctx))
    embed.timestamp = datetime.now(timezone.utc)

    return embed


def _format_change_line(change: dict, show_author: bool) -> str:
    """Formatiere eine Change-Zeile mit optionaler Inline-Attribution (Multi-Author)."""
    desc = _discord_change_summary(change)
    if show_author:
        credit = _authors_display(change)
        if credit:
            return f"→ {desc} · *{credit}*"
    return f"→ {desc}"


def _discord_change_summary(change: dict) -> str:
    """Kompakte Change-Zeile fuer Discord: Titel + konkreter Nutzen."""
    title = (change.get('title') or '').strip()
    impact = (change.get('impact') or '').strip()
    desc = (change.get('description') or '').strip()
    if title and impact and impact.lower() not in desc.lower():
        return f"{title}: {impact}"
    return desc or title or "Änderung"


def _discord_change_subline(change: dict) -> str:
    """Eine konkrete zweite Zeile fuer grosse Discord-Embeds."""
    after = (change.get('after') or '').strip()
    if after:
        return after
    details = change.get('details') or []
    if details and isinstance(details[0], str):
        return details[0]
    return (change.get('why') or '').strip()


def _change_detail_lines(change: dict) -> list[str]:
    """Detailzeilen fuer Discord-only Embeds, mit v7-Feldern zuerst."""
    lines: list[str] = []
    before = (change.get('before') or '').strip()
    after = (change.get('after') or '').strip()
    why = (change.get('why') or '').strip()
    action = (change.get('user_action') or '').strip()
    if before and after:
        lines.append(f"Vorher: {before} Jetzt: {after}")
    elif after:
        lines.append(after)
    if why:
        lines.append(why)
    if action and action.lower() not in ('keine', 'none', 'n/a'):
        lines.append(f"Aktion nötig: {action}")
    for detail in change.get('details') or []:
        if isinstance(detail, str) and detail.strip() and detail.strip() not in lines:
            lines.append(detail.strip())
    return lines


def _authors_display(change: dict) -> str:
    """Authors-Liste zu String: 'Shadow', 'Shadow + Mapu', 'Shadow, Mapu, Renji'."""
    authors = change.get('authors') or []
    if not authors:
        single = change.get('author', '')
        return single or ''
    if len(authors) == 1:
        return authors[0]
    if len(authors) == 2:
        return f"{authors[0]} + {authors[1]}"
    return ", ".join(authors)


def _format_authors_line(change: dict) -> str:
    """Sub-Zeile unter einem Highlight für Inline-Credits in hype-Embeds."""
    credit = _authors_display(change)
    if not credit:
        return ""
    return f"   *— {credit}*"


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

    # Channel-IDs UND Role-Mentions werden in config.yaml teils auf Top-Level
    # des project_config gepflegt (so werden sie historisch gesetzt), patch_notes.*
    # bleibt als expliziter Fallback.
    channel_id = pn_config.get('update_channel_id') or ctx.project_config.get('update_channel_id')
    update_role = (
        pn_config.get('update_channel_role_mention')
        or ctx.project_config.get('update_channel_role_mention', '')
    )
    if channel_id:
        await _send_to_channel(
            bot, int(channel_id), embed, ctx,
            role_mention=update_role,
            with_feedback=True,
        )

    internal_id = pn_config.get('internal_channel_id') or ctx.project_config.get('internal_channel_id')
    internal_role = (
        pn_config.get('internal_channel_role_mention')
        or ctx.project_config.get('internal_channel_role_mention', '')
    )
    if internal_id:
        await _send_to_channel(
            bot, int(internal_id), embed, ctx,
            role_mention=internal_role,
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
        view = _get_feedback_view(bot, ctx) if with_feedback else None

        last_msg = None
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
            if is_last:
                last_msg = msg

        # Feedback-Collector über die View informieren (Like-Zähler / Bewerten brauchen Projekt+Version)
        if view is not None and last_msg is not None:
            collector = getattr(bot, 'feedback_collector', None)
            if collector is not None:
                try:
                    changelog_url = ctx.project_config.get('patch_notes', {}).get('changelog_url', '')
                    await collector.track_patch_notes_message(
                        last_msg, ctx.project, ctx.version, changelog_url,
                    )
                except Exception as e:
                    logger.debug(f"[v6] track_patch_notes_message fehlgeschlagen: {e}")

    except Exception as e:
        logger.warning(f"[v6] Channel {channel_id} fehlgeschlagen: {e}")


def _get_feedback_view(bot, ctx: PipelineContext):
    """Like+Bewerten View über bot.feedback_collector bauen (benötigt Collector-Instanz).

    Fällt lautlos auf None zurück wenn der Collector noch nicht initialisiert ist
    oder das Modul nicht importiert werden kann.
    """
    collector = getattr(bot, 'feedback_collector', None)
    if collector is None:
        return None
    try:
        changelog_url = ctx.project_config.get('patch_notes', {}).get('changelog_url', '')
        return collector.create_view(changelog_url)
    except Exception as e:
        logger.debug(f"[v6] Feedback-View konnte nicht erstellt werden: {e}")
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
    # Wall-Clock-Time bis jetzt (Distribute-Stage laeuft noch, deshalb kann die
    # Pipeline selbst das Feld in ctx.metrics erst nach der Stage setzen). Wir
    # leiten den Wert hier live aus pipeline_start_monotonic ab.
    if ctx.pipeline_start_monotonic is not None:
        import time as _time
        pipeline_total_time_s = round(_time.monotonic() - ctx.pipeline_start_monotonic, 2)
    else:
        # Fallback: falls Resume ohne frisches Monotonic — greife auf das
        # bereits gesetzte Metrics-Feld zurueck (vom Pipeline-Finalize).
        pipeline_total_time_s = ctx.metrics.get('pipeline_total_time_s', 0)

    metrics = {
        'project': ctx.project,
        'version': ctx.version,
        'trigger': ctx.trigger,
        'update_size': ctx.update_size,
        'total_commits': len(ctx.enriched_commits or ctx.raw_commits),
        'groups': len(ctx.groups),
        'player_facing_groups': sum(1 for g in ctx.groups if g.get('is_player_facing')),
        'infra_groups': sum(1 for g in ctx.groups if not g.get('is_player_facing')),
        'ai_engine': ctx.ai_engine_used or 'unknown',
        'variant_id': ctx.variant_id,
        'generation_time_s': ctx.generation_time_s,
        'pipeline_total_time_s': pipeline_total_time_s,
        'hallucinations_caught': len(ctx.fixes_applied),
        'warnings': len(ctx.warnings),
        'version_source': ctx.version_source,
        'messages_sent': len(ctx.sent_message_ids),
    }
    logger.info("METRICS|patch_notes_pipeline|%s", json.dumps(metrics, default=str))


# ── Release-Notes Archivierung ──────────────────────────────────

_RELEASE_NOTES_TEMPLATE = """<!-- release_notes.md — Dev-Kontext fuer den naechsten Patch-Notes-Release

Fuege hier zwischen Commits 1-3 Saetze fuer wirklich interessante Sachen an:
- Warum hast du X so und nicht anders gebaut?
- Was war die Reise/der Stolperstein?
- Was sollen User wissen, das sie sonst nie erfahren?

Beispiele:
- Shadow hat drei Naechte an der State-Machine gesessen, weil Enum-Transitionen
  inkonsistent wurden. DDD-Aggregate war die Loesung.
- Die Klinik-API nutzt Overpass als Primary-Source, nicht Google Maps — Kosten-
  und ToS-Gruende.
- Ursprunglich als Polling gedacht, aber unter Last bei 5+ gleichzeitigen Einsaetzen
  hat das nicht skaliert. Event-Driven war die einzige Option.

Wird beim naechsten Release automatisch nach docs/release-history/v<version>.md
archiviert und hier wieder geleert. Du fangst also frisch an fuer den naechsten Cycle.
-->
"""


def _run_git_quiet(repo_path: Path, args: list, timeout: int = 15) -> tuple[bool, str, str]:
    """Führt ein git-Kommando still aus. Gibt (erfolg, stdout, stderr) zurück.

    Wirft NIE — git darf die Patch-Notes-Pipeline nicht crashen. Bei jeder
    Störung (Timeout, git fehlt, exit != 0) wird (False, '', <fehler>) geliefert.
    """
    try:
        result = subprocess.run(
            ['git', *args],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.returncode == 0, (result.stdout or '').strip(), (result.stderr or '').strip()
    except Exception as e:  # noqa: BLE001 — defensive: git darf nie die Pipeline abbrechen
        return False, '', str(e)


# Bot-Identität für Archiv-Commits — via `git -c` pro Commit gesetzt, damit die
# .git/config des Deploy-Repos NICHT dauerhaft verändert wird (kein Blame-Pollution).
_GIT_AUTHOR_FLAGS = ['-c', 'user.email=shadowops@local', '-c', 'user.name=ShadowOps Bot']


def _sanitize_git_error(err: str) -> str:
    """Redigiert potenziell sensible Zeilen aus git-stderr vor dem Logging.

    git kann bei Auth-Fehlern Credential-Helper-Output, Remote-URLs mit
    eingebetteten Tokens oder SSH-Key-/.netrc-Pfade emittieren. CLAUDE.md verbietet
    Secrets in Logs — daher Zeilen mit Auth-/URL-Markern komplett verwerfen.
    """
    if not err:
        return ''
    sensitive = (
        'token', 'password', 'passwd', 'credential', 'authorization',
        'ssh', 'netrc', '@', 'http://', 'https://', 'bearer', 'secret',
    )
    out = []
    for line in err.splitlines():
        low = line.lower()
        out.append('[redigiert]' if any(m in low for m in sensitive) else line)
    return ' | '.join(out)[:300]


def _commit_and_push_archive(
    base: Path, notes_path: Path, archive_file: Path, version: str, project: str
) -> None:
    """Committet + pusht NUR die beiden Archiv-Dateien (best-effort, #302).

    Lässt das Deploy-Repo nach dem Archivieren clean zurück, damit der nächste
    Auto-Deploy-`git pull` nicht an einem dirty Working-Tree scheitert (war die
    Ursache von shadowops#302). Staget ausschließlich `release_notes.md` + die
    neue Archiv-Datei (nie `git add -A`), damit fremde uncommittete Änderungen
    im Deploy-Checkout nicht mitgenommen werden.

    Defensive: jede git-Störung wird geloggt und geschluckt — das Archiv liegt
    bereits auf Platte, ein fehlgeschlagener Push ist nicht kritisch (der nächste
    Release-Lauf pusht den lokalen Commit nach). Ein direkter Push auf den
    Deploy-Branch triggert KEINEN Auto-Deploy (hardcoded Block in
    event_handlers_mixin.py).
    """
    try:
        # 1. git-Repo-Guard — project_path ist nicht zwingend ein Repo
        ok, _, _ = _run_git_quiet(base, ['rev-parse', '--git-dir'], timeout=5)
        if not ok:
            logger.debug(f"[v6] {base} ist kein git-Repo — Archiv nicht committed")
            return

        # 2. Detached-HEAD-Guard + aktuellen Branch ermitteln
        ok, branch, _ = _run_git_quiet(base, ['symbolic-ref', '-q', '--short', 'HEAD'], timeout=5)
        if not ok or not branch:
            logger.debug("[v6] detached HEAD — Archiv nicht gepusht")
            return

        # 3. Nur die zwei Dateien stagen (relative Pfade, -- trennt Pathspecs von Refs)
        try:
            rel_notes = str(notes_path.relative_to(base))
            rel_archive = str(archive_file.relative_to(base))
        except ValueError:
            logger.warning("[v6] Archiv-Pfade außerhalb des Repos — Commit übersprungen")
            return
        _run_git_quiet(base, ['add', '--', rel_notes, rel_archive])

        # 4. Idempotenz: git diff --cached --quiet → rc 0 = NICHTS gestaged → fertig.
        #    nothing_staged spiegelt genau diese (invertierte) --quiet-Semantik.
        nothing_staged, _, _ = _run_git_quiet(
            base, ['diff', '--cached', '--quiet', '--', rel_notes, rel_archive]
        )
        if nothing_staged:
            logger.debug("[v6] Archiv bereits committed — nichts zu tun")
            return

        # 5. Commit — exakt die zwei Pfade, Bot-Identität via -c (keine config-Pollution).
        ok, _, err = _run_git_quiet(
            base,
            ['commit', *_GIT_AUTHOR_FLAGS,
             '-m', f'docs: archive release notes v{version}',
             '--', rel_notes, rel_archive],
            timeout=20,
        )
        if not ok:
            logger.warning(f"[v6] Archiv-Commit fehlgeschlagen: {_sanitize_git_error(err)}")
            return

        # 6. Push — best-effort, Working-Tree ist auch ohne Push schon clean
        ok, _, err = _run_git_quiet(base, ['push', 'origin', branch], timeout=30)
        if not ok:
            logger.warning(
                f"[v6] Archiv-Push fehlgeschlagen (lokal committed): {_sanitize_git_error(err)}"
            )
            return
        logger.info(f"[v6] {project}: Archiv v{version} committed + gepusht ({branch})")
    except Exception as e:  # noqa: BLE001 — Defense-in-Depth: git crasht die Pipeline nie
        logger.warning(f"[v6] Archiv-Commit/Push unerwartet fehlgeschlagen: {e}")


def _archive_release_notes(ctx: PipelineContext) -> None:
    """Archiviert release_notes.md nach docs/release-history/v<version>.md
    und setzt das Template zurück in die Original-Datei.

    Idempotent: wenn die Datei nicht existiert oder nur Template enthält,
    passiert nichts. Läuft nur bei erfolgreichem Discord-Send (messages_sent > 0).
    Nach dem Archivieren werden die beiden Dateien committed + gepusht, damit das
    Deploy-Repo clean bleibt (#302).
    """
    import re

    project_path = ctx.project_config.get('path', '')
    if not project_path:
        return
    base = Path(project_path)
    if not base.exists():
        return

    # Finde die release_notes.md-Quelle
    notes_path = None
    for name in ('release_notes.md', 'RELEASE_NOTES.md'):
        candidate = base / name
        if candidate.exists():
            notes_path = candidate
            break
    if notes_path is None:
        return

    try:
        raw = notes_path.read_text(encoding='utf-8')
    except Exception as e:
        logger.debug(f"[v6] Archive-Read fehlgeschlagen: {e}")
        return

    # HTML-Kommentare strippen — wenn nichts übrig: nur Template, nicht archivieren
    stripped = re.sub(r'<!--.*?-->', '', raw, flags=re.DOTALL).strip()
    if len(stripped) < 20:
        logger.debug("[v6] release_notes.md enthält nur Template — kein Archiv nötig")
        return

    # Version-Guard: ctx.version fließt in Dateiname + Commit-Message. versioning.py
    # liefert zwar striktes SemVer, aber am Boundary defensiv gegen Pfad-Traversal
    # (../) und Newline-Injection in der Commit-Message absichern (#302-Review).
    if not re.match(r'^\d+\.\d+\.\d+([.\-+][0-9A-Za-z.\-]+)?$', str(ctx.version)):
        logger.warning(f"[v6] Ungültige Version, Archiv übersprungen: {ctx.version!r}")
        return

    # Archiv-Pfad: docs/release-history/v<version>.md
    archive_dir = base / 'docs' / 'release-history'
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_file = archive_dir / f'v{ctx.version}.md'

    archive_content = (
        f"# Release Notes — {ctx.project} v{ctx.version}\n"
        f"_Archiviert am {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_\n\n"
        f"{stripped}\n"
    )

    try:
        archive_file.write_text(archive_content, encoding='utf-8')
        notes_path.write_text(_RELEASE_NOTES_TEMPLATE, encoding='utf-8')
        logger.info(
            f"[v6] {ctx.project}: release_notes.md archiviert -> {archive_file.name} "
            f"(Template zurückgesetzt)"
        )
        # Deploy-Repo clean halten: die zwei Archiv-Dateien committen + pushen (#302).
        # Eigenes try/except — git darf den Archiv-Erfolgspfad nie crashen.
        _commit_and_push_archive(base, notes_path, archive_file, ctx.version, ctx.project)
    except Exception as e:
        logger.warning(f"[v6] release_notes Archivierung fehlgeschlagen: {e}")
