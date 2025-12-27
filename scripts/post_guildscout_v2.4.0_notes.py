"""
Script to post GuildScout v2.4.0 patch notes.

Based on comprehensive CHANGELOG.md details.
"""

import asyncio
import discord
from datetime import datetime
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils.config import Config


async def post_improved_notes():
    """Post v2.4.0 patch notes to customer Discord."""

    # Load config
    config = Config()

    # Create bot instance
    intents = discord.Intents.default()
    bot = discord.Client(intents=intents)

    @bot.event
    async def on_ready():
        print(f"✅ Logged in as {bot.user}")

        # Get GuildScout project config
        guildscout_config = config.get_project_config('guildscout')
        if not guildscout_config:
            print("❌ GuildScout project not found in config")
            await bot.close()
            return

        external_notifications = guildscout_config.get('external_notifications', [])

        # Find the Updates channel (git_push: true)
        update_channel_config = None
        for notif in external_notifications:
            if notif.get('notify_on', {}).get('git_push', False):
                update_channel_config = notif
                break

        if not update_channel_config:
            print("❌ No git_push notification channel found in config")
            await bot.close()
            return

        guild_id = update_channel_config['guild_id']
        channel_id = update_channel_config['channel_id']

        # Get guild and channel
        guild = bot.get_guild(guild_id)
        if not guild:
            print(f"❌ Guild {guild_id} not found")
            await bot.close()
            return

        channel = guild.get_channel(channel_id)
        if not channel:
            print(f"❌ Channel {channel_id} not found")
            await bot.close()
            return

        print(f"📢 Posting to {guild.name} - #{channel.name}")

        # Create improved embed
        embed = discord.Embed(
            title="✨ Updates for GuildScout",
            description="**Version 2.4.0 - Activity & Visuals Update** 🚀",
            color=0x2ECC71,  # Green
            timestamp=datetime.utcnow()
        )

        # Voice Tracking
        voice_tracking = """**🎤 Voice Tracking:**

• **Voice Activity Monitoring**: Der Bot erfasst nun automatisch die Zeit, die Nutzer in Voice-Kanälen verbringen
• **Präzise Erfassung**: Tracking startet sofort bei Channel-Beitritt und endet beim Verlassen/Wechseln
• **Konfigurierbar**: Mindestdauer (default 10s) und AFK-Channel-Ausschluss einstellbar
• **Integration**: Voice-Minuten werden im Dashboard, in `/my-score` und in der Analyse angezeigt"""

        embed.add_field(
            name="",
            value=voice_tracking,
            inline=False
        )

        # 3-Säulen-Scoring
        scoring = """**📊 3-Säulen-Scoring (Fairness Update):**

• **Neues Berechnungsmodell**: Statt nur Nachrichten und Tage gibt es nun drei gewichtete Faktoren
• **Standard-Gewichtung**:
  - **10%** Days in Server (Loyalität) - _Reduziert, damit Inaktive nicht nur durch Alter gewinnen_
  - **55%** Message Activity (Engagement)
  - **35%** Voice Activity (Präsenz)
• **Flexibel**: Gewichte sind in `config.yaml` frei anpassbar"""

        embed.add_field(
            name="",
            value=scoring,
            inline=False
        )

        # Visual Rank Cards
        rank_cards = """**🖼️ Visual Rank Cards:**

• **Grafische Auswertung**: Der Befehl `/my-score` generiert nun eine schicke PNG-Grafik (Rank Card)
• **Features**:
  - Avatar des Nutzers
  - Kreis-Diagramm für Gesamt-Score
  - Balken-Diagramme für Nachrichten, Voice und Tage
  - Modernes Dark-Theme Design mit Gitter-Hintergrund"""

        embed.add_field(
            name="",
            value=rank_cards,
            inline=False
        )

        # Interactive Dashboard
        dashboard = """**⚡ Interactive Dashboard:**

• **Action-Buttons**: Admins können "Wackelkandidaten" (inaktive User mit Rolle) nun direkt per Button verwalten
• **Smart Scanner**: Der Scanner für Wackelkandidaten ignoriert nun Exclusion-Roles korrekt, um auch "geschützte" User auf Inaktivität zu prüfen
• **Live-Status**: Anzeige der Gesamt-Voice-Stunden des Servers im Dashboard"""

        embed.add_field(
            name="",
            value=dashboard,
            inline=False
        )

        # Improvements & Fixes
        improvements = """**🔧 Improvements & Fixes:**

• **Scorer Refactoring**: Kompletter Umbau der `Scorer`-Klasse für das neue 3-Säulen-Modell
• **Config Patch**: Automatische Anpassung alter Config-Dateien auf die neuen Standardwerte
• **Bugfix**: `NameError: Optional` in `scorer.py` behoben
• **Bugfix**: Dashboard-Button fand keine User (Scanner-Logik korrigiert)"""

        embed.add_field(
            name="",
            value=improvements,
            inline=False
        )

        # Footer
        embed.set_footer(
            text="⚡ GuildScout v2.4.0 • Major Update • Voice Tracking + Visual Rank Cards"
        )

        # Send embed
        await channel.send(embed=embed)
        print("✅ v2.4.0 patch notes posted!")

        # Close bot
        await bot.close()

    # Run bot
    await bot.start(config.discord_token)


if __name__ == "__main__":
    asyncio.run(post_improved_notes())
