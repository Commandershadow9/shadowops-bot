#!/usr/bin/env python3
"""
Get ShadowOps Bot Client ID and Invite Link
"""

import discord
import asyncio
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from utils.config import get_config

async def main():
    print("=" * 60)
    print("🗡️ ShadowOps Bot - Einladungs-Link Generator")
    print("=" * 60)
    print()

    config = get_config()

    # Get token
    import os
    token = os.getenv('DISCORD_BOT_TOKEN')
    if not token:
        # Config can be dict or object
        if isinstance(config, dict):
            token = config.get('discord', {}).get('token')
        elif hasattr(config, 'discord'):
            token = config.discord.get('token') if isinstance(config.discord, dict) else config.discord.token

    if not token:
        print("❌ Kein Bot-Token gefunden!")
        print("   Set DISCORD_BOT_TOKEN environment variable or check config.yaml")
        return

    # Create minimal client to get bot user
    intents = discord.Intents.default()
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        print(f"✅ Bot verbunden!")
        print()
        print(f"📋 Bot Information:")
        print(f"   Username: {client.user.name}")
        print(f"   User ID:  {client.user.id}")
        print(f"   Tag:      {client.user.discriminator}")
        print()
        print("=" * 60)
        print("🔗 EINLADUNGS-LINK (Kunden-Server):")
        print("=" * 60)
        print()

        # Customer server invite (with Manage Channels for auto-setup)
        invite_url = f"https://discord.com/api/oauth2/authorize?client_id={client.user.id}&permissions=268446736&scope=bot"
        print(invite_url)
        print()

        print("📝 Permissions:")
        print("   • View Channels")
        print("   • Send Messages")
        print("   • Embed Links")
        print("   • Read Message History")
        print("   • Manage Channels (für Auto-Setup)")
        print()
        print("=" * 60)
        print("💡 Nächste Schritte:")
        print("=" * 60)
        print("1. Link im Browser öffnen")
        print("2. Kunden-Server auswählen")
        print("3. Permissions bestätigen")
        print("4. Bot erstellt automatisch Channels in 🚨 | ADMIN AREA")
        print("5. Config aus Logs kopieren (siehe CUSTOMER_SERVER_SETUP.md)")
        print()

        await client.close()

    try:
        await client.start(token)
    except KeyboardInterrupt:
        print("\n⚠️ Abgebrochen")
    except Exception as e:
        print(f"❌ Fehler: {e}")

if __name__ == "__main__":
    asyncio.run(main())
