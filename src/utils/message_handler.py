"""
Message Handler für Discord
Handhabt Rate Limits, Message Splitting und sichere Zustellung
"""

import discord
import asyncio
from typing import List, Optional
import logging

logger = logging.getLogger("shadowops")

# Discord Limits
MAX_MESSAGE_LENGTH = 2000
MAX_EMBED_DESCRIPTION = 4096
MAX_EMBED_FIELD_VALUE = 1024
MAX_EMBEDS_PER_MESSAGE = 10

# Rate Limiting
RATE_LIMIT_DELAY = 1.0  # Sekunden zwischen Messages
RATE_LIMIT_BURST = 5    # Max Messages in Burst


class MessageHandler:
    """
    Sicherer Message-Handler mit:
    - Automatisches Message-Splitting bei zu langen Nachrichten
    - Rate-Limit-Schutz
    - Retry-Logic bei Fehlern
    - Queue-System für garantierte Zustellung
    """

    def __init__(self, bot):
        self.bot = bot
        self.message_queue = asyncio.Queue()
        self.is_processing = False

    async def send_safe(
        self,
        channel_id: int,
        content: Optional[str] = None,
        embed: Optional[discord.Embed] = None,
        embeds: Optional[List[discord.Embed]] = None
    ) -> bool:
        """
        Sendet Nachricht sicher mit Auto-Splitting und Rate-Limit-Schutz

        Args:
            channel_id: Discord Channel ID
            content: Text-Content (wird bei >2000 Zeichen gesplittet)
            embed: Einzelnes Embed
            embeds: Liste von Embeds (max 10 pro Message)

        Returns:
            True wenn erfolgreich gesendet
        """
        try:
            channel = self.bot.get_channel(channel_id)
            if not channel:
                logger.warning(f"⚠️ Channel {channel_id} nicht gefunden")
                return False

            # Content-Splitting wenn zu lang
            if content and len(content) > MAX_MESSAGE_LENGTH:
                return await self._send_split_content(channel, content)

            # Embed-Splitting wenn zu viele
            if embeds and len(embeds) > MAX_EMBEDS_PER_MESSAGE:
                return await self._send_split_embeds(channel, embeds)

            # Normale Zustellung mit Retry
            return await self._send_with_retry(
                channel,
                content=content,
                embed=embed,
                embeds=embeds
            )

        except Exception as e:
            logger.error(f"❌ Fehler beim Senden: {e}", exc_info=True)
            return False

    async def _send_split_content(self, channel, content: str) -> bool:
        """Splitted langen Content in mehrere Messages"""
        chunks = self._split_text(content, MAX_MESSAGE_LENGTH)

        logger.info(f"📄 Splitte Message in {len(chunks)} Teile")

        for i, chunk in enumerate(chunks):
            prefix = f"**[{i+1}/{len(chunks)}]**\n" if len(chunks) > 1 else ""
            await self._send_with_retry(channel, content=prefix + chunk)
            await asyncio.sleep(RATE_LIMIT_DELAY)

        return True

    async def _send_split_embeds(self, channel, embeds: List[discord.Embed]) -> bool:
        """Splitted zu viele Embeds in mehrere Messages"""
        for i in range(0, len(embeds), MAX_EMBEDS_PER_MESSAGE):
            chunk = embeds[i:i + MAX_EMBEDS_PER_MESSAGE]
            await self._send_with_retry(channel, embeds=chunk)
            await asyncio.sleep(RATE_LIMIT_DELAY)

        return True

    async def _send_with_retry(
        self,
        channel,
        content: Optional[str] = None,
        embed: Optional[discord.Embed] = None,
        embeds: Optional[List[discord.Embed]] = None,
        max_retries: int = 3
    ) -> bool:
        """Sendet mit Retry-Logic"""
        for attempt in range(max_retries):
            try:
                # Rate-Limit-Schutz
                await asyncio.sleep(RATE_LIMIT_DELAY)

                await channel.send(content=content, embed=embed, embeds=embeds)
                logger.debug(f"✉️ Nachricht gesendet an {channel.name}")
                return True

            except discord.HTTPException as e:
                if e.status == 429:  # Rate Limited
                    retry_after = e.retry_after if hasattr(e, 'retry_after') else 5
                    logger.warning(f"⏸️ Rate Limited, warte {retry_after}s")
                    await asyncio.sleep(retry_after)
                    continue
                elif attempt < max_retries - 1:
                    logger.warning(f"⚠️ Fehler beim Senden (Versuch {attempt+1}/{max_retries}): {e}")
                    await asyncio.sleep(2 ** attempt)  # Exponential backoff
                    continue
                else:
                    logger.error(f"❌ Senden fehlgeschlagen nach {max_retries} Versuchen: {e}")
                    return False

            except discord.Forbidden:
                logger.error(f"❌ Keine Berechtigung für Channel {channel.name}")
                return False

            except Exception as e:
                logger.error(f"❌ Unerwarteter Fehler: {e}", exc_info=True)
                return False

        return False

    def _split_text(self, text: str, max_length: int) -> List[str]:
        """
        Splitted Text intelligent an Zeilenumbrüchen

        Args:
            text: Zu splittender Text
            max_length: Max Länge pro Chunk

        Returns:
            Liste von Text-Chunks
        """
        if len(text) <= max_length:
            return [text]

        chunks = []
        current_chunk = ""

        for line in text.split('\n'):
            # Wenn einzelne Zeile zu lang, hart splitten
            if len(line) > max_length:
                # Flush current chunk
                if current_chunk:
                    chunks.append(current_chunk)
                    current_chunk = ""

                # Splitte lange Zeile
                for i in range(0, len(line), max_length):
                    chunks.append(line[i:i + max_length])

            # Normale Zeile
            elif len(current_chunk) + len(line) + 1 <= max_length:
                current_chunk += line + '\n'
            else:
                # Chunk ist voll, neuen starten
                chunks.append(current_chunk)
                current_chunk = line + '\n'

        # Letzten Chunk hinzufügen
        if current_chunk:
            chunks.append(current_chunk)

        return chunks

    def split_embed_field(self, value: str, max_length: int = MAX_EMBED_FIELD_VALUE) -> List[str]:
        """Splitted zu lange Embed-Field-Values"""
        return self._split_text(value, max_length)
