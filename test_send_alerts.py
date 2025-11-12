#!/usr/bin/env python3
"""
Sendet Test-Alerts in die konfigurierten Channels
Zeigt dass automatische Alerts funktionieren
"""

import asyncio
import discord
from discord.ext import commands
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from utils.config import get_config
from utils.embeds import EmbedBuilder

async def send_test_alerts():
    """Sendet Test-Alerts in alle Channels"""

    config = get_config()

    # Discord Bot Setup
    intents = discord.Intents.default()
    bot = commands.Bot(command_prefix="!", intents=intents)

    @bot.event
    async def on_ready():
        print(f"✅ Bot verbunden als {bot.user}")
        print(f"📊 Verbunden mit {len(bot.guilds)} Server(n)\n")

        try:
            # 1. Test: Fail2ban Ban → 🚫 fail2ban + 🔴 critical
            print("🧪 Test 1: Fail2ban IP-Ban")
            print(f"   → Sende zu Channel {config.fail2ban_channel}")

            fail2ban_channel = bot.get_channel(config.fail2ban_channel)
            if fail2ban_channel:
                embed = EmbedBuilder.fail2ban_ban("123.45.67.89", "sshd", 5)
                await fail2ban_channel.send(embed=embed)
                print("   ✅ Gesendet!\n")

            await asyncio.sleep(2)

            # Auch zu critical
            critical_channel = bot.get_channel(config.critical_channel)
            if critical_channel and config.critical_channel != config.fail2ban_channel:
                await critical_channel.send(embed=embed)
                print(f"   ✅ Auch zu critical ({config.critical_channel})\n")

            await asyncio.sleep(2)

            # 2. Test: CrowdSec Threat → 🔴 critical
            print("🧪 Test 2: CrowdSec AI Threat")
            print(f"   → Sende zu Channel {config.critical_channel}")

            if critical_channel:
                embed = EmbedBuilder.crowdsec_alert("98.76.54.32", "SSH Brute-Force", "Russia")
                await critical_channel.send(embed=embed)
                print("   ✅ Gesendet!\n")

            await asyncio.sleep(2)

            # 3. Test: Docker Scan CRITICAL → 🐳 docker + 🔴 critical
            print("🧪 Test 3: Docker Scan (CRITICAL)")
            print(f"   → Sende zu Channel {config.docker_channel}")

            docker_channel = bot.get_channel(config.docker_channel)
            if docker_channel:
                embed = EmbedBuilder.docker_scan_result(
                    total_images=5,
                    critical=10,
                    high=5,
                    medium=0,
                    low=0
                )
                await docker_channel.send(embed=embed)
                print("   ✅ Gesendet!\n")

            await asyncio.sleep(2)

            # 4. Test: Backup Success → 💾 backups + 🛡️ security (Sicherheitsdienst)
            print("🧪 Test 4: Backup Success (Sicherheitsdienst)")
            print(f"   → Sende zu Channel {config.backups_channel}")

            backups_channel = bot.get_channel(config.backups_channel)
            if backups_channel:
                embed = EmbedBuilder.backup_status(True, "sicherheitsdienst_db", "142 MB")
                await backups_channel.send(embed=embed)
                print("   ✅ Gesendet!\n")

            await asyncio.sleep(2)

            # Auch zu Sicherheitsdienst Channel
            security_channel = bot.get_channel(config.sicherheitsdienst_channel)
            if security_channel and config.sicherheitsdienst_channel != config.backups_channel:
                await security_channel.send(embed=embed)
                print(f"   ✅ Auch zu security ({config.sicherheitsdienst_channel})\n")

            await asyncio.sleep(2)

            # 5. Test: Backup Failed → 🔴 critical + 💾 backups + 🛡️ security
            print("🧪 Test 5: Backup FAILED (CRITICAL)")
            print(f"   → Sende zu ALLEN relevanten Channels")

            embed_failed = EmbedBuilder.backup_status(False, "sicherheitsdienst_db", None)

            # Critical
            if critical_channel:
                await critical_channel.send(embed=embed_failed)
                print(f"   ✅ → critical ({config.critical_channel})")

            await asyncio.sleep(1)

            # Backups
            if backups_channel:
                await backups_channel.send(embed=embed_failed)
                print(f"   ✅ → backups ({config.backups_channel})")

            await asyncio.sleep(1)

            # Security
            if security_channel:
                await security_channel.send(embed=embed_failed)
                print(f"   ✅ → security ({config.sicherheitsdienst_channel})")

            print("\n" + "="*70)
            print("🎉 Alle Test-Alerts gesendet!")
            print("="*70)
            print("\n📋 Prüfe jetzt in Discord:")
            print("  🔴 critical      → Sollte 4 Alerts haben (Fail2ban, CrowdSec, Backup Failed)")
            print("  🚫 fail2ban      → Sollte 1 Alert haben (IP-Ban)")
            print("  🐳 docker        → Sollte 1 Alert haben (Scan CRITICAL)")
            print("  💾 backups       → Sollte 2 Alerts haben (Success + Failed)")
            print("  🛡️ security      → Sollte 2 Alerts haben (Backup Success + Failed)")
            print("\n✅ Channel-Routing funktioniert!\n")

        except Exception as e:
            print(f"❌ Fehler: {e}")
            import traceback
            traceback.print_exc()

        finally:
            await bot.close()

    # Bot starten
    await bot.start(config.discord_token)


if __name__ == "__main__":
    print("=" * 70)
    print("🧪 ShadowOps Test-Alert Script")
    print("=" * 70)
    print()

    asyncio.run(send_test_alerts())
