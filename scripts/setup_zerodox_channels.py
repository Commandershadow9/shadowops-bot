#!/usr/bin/env python3
"""
Einmaliges Setup-Script: ZERODOX Patch-Notes Channels konfigurieren.

Verschiebt die bereits erstellten Channels in die richtigen Kategorien
und setzt Berechtigungen (read-only für User, Bot darf posten).

Nutzt den ShadowOps Bot-Token via Discord REST API.
"""

import asyncio
import json
import logging
import sys
from pathlib import Path

import aiohttp
import yaml

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# === Konfiguration ===
ZERODOX_GUILD_ID = "1151330239272730755"

# Channels (bereits erstellt via Shadow Admin Bot MCP)
PATCH_NOTES_CHANNEL_ID = "1483892059596132483"   # 📋patch-notes
DEV_UPDATES_CHANNEL_ID = "1483892060963475617"   # 🔧dev-updates

# Ziel-Kategorien
LOBBY_CATEGORY_ID = "1151330240841396325"         # Lobby-Kanäle (öffentlich)
COMMUNITY_CATEGORY_ID = "1458208397936693268"     # Community-/Kundenbereich (intern)

# Rollen
CUSTOMER_ROLE_ID = "1465439304233910476"          # Verifizierte Kunden

# Permission-Bits
VIEW_CHANNEL = 1 << 10       # 0x400 = 1024
SEND_MESSAGES = 1 << 11      # 0x800 = 2048
EMBED_LINKS = 1 << 14        # 0x4000 = 16384
ATTACH_FILES = 1 << 15       # 0x8000 = 32768
ADD_REACTIONS = 1 << 6       # 0x40 = 64
READ_MESSAGE_HISTORY = 1 << 16  # 0x10000 = 65536

API_BASE = "https://discord.com/api/v10"


def load_bot_token() -> str:
    """Bot-Token aus config.yaml laden (kein Leak in stdout)."""
    config_path = Path(__file__).parent.parent / "config" / "config.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)
    token = config["discord"]["token"]
    if not token:
        raise ValueError("Kein Discord-Token in config.yaml!")
    return token


async def api_request(session: aiohttp.ClientSession, method: str, endpoint: str,
                      token: str, json_data: dict = None) -> dict:
    """Discord REST API Request."""
    headers = {
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json",
    }
    url = f"{API_BASE}{endpoint}"

    async with session.request(method, url, headers=headers, json=json_data) as resp:
        if resp.status == 204:
            return {}
        body = await resp.json()
        if resp.status >= 400:
            logger.error(f"API-Fehler {resp.status}: {json.dumps(body, indent=2)}")
            raise Exception(f"Discord API {resp.status}: {body.get('message', body)}")
        return body


async def get_category_overwrites(session: aiohttp.ClientSession, token: str,
                                   category_id: str) -> list:
    """Permission-Overwrites einer Kategorie holen."""
    data = await api_request(session, "GET", f"/channels/{category_id}", token)
    return data.get("permission_overwrites", [])


async def setup_channel(session: aiohttp.ClientSession, token: str,
                        channel_id: str, parent_id: str,
                        overwrites: list, channel_name: str):
    """Channel in Kategorie verschieben und Berechtigungen setzen."""
    payload = {
        "parent_id": parent_id,
        "permission_overwrites": overwrites,
    }

    logger.info(f"Konfiguriere #{channel_name} → Kategorie {parent_id}")
    logger.info(f"  Permission-Overwrites: {len(overwrites)} Einträge")

    result = await api_request(session, "PATCH", f"/channels/{channel_id}", token, payload)
    logger.info(f"  ✅ #{result['name']} erfolgreich verschoben (parent: {result.get('parent_id')})")
    return result


async def get_bot_user(session: aiohttp.ClientSession, token: str) -> dict:
    """Bot-User-Info holen (für Bot-ID)."""
    return await api_request(session, "GET", "/users/@me", token)


async def main():
    token = load_bot_token()
    logger.info("ShadowOps Bot-Token geladen")

    async with aiohttp.ClientSession() as session:
        # 1. Bot-User-Info holen
        bot_user = await get_bot_user(session, token)
        bot_id = bot_user["id"]
        logger.info(f"Bot: {bot_user['username']}#{bot_user.get('discriminator', '0')} (ID: {bot_id})")

        # 2. Prüfen ob Bot Zugriff auf ZERODOX-Guild hat
        try:
            guild = await api_request(session, "GET", f"/guilds/{ZERODOX_GUILD_ID}", token)
            logger.info(f"Guild-Zugriff bestätigt: {guild['name']}")
        except Exception as e:
            logger.error(f"❌ Bot hat keinen Zugriff auf ZERODOX-Server! "
                         f"Bot muss eingeladen werden: {e}")
            sys.exit(1)

        # 3. Kategorie-Berechtigungen holen
        lobby_overwrites = await get_category_overwrites(session, token, LOBBY_CATEGORY_ID)
        community_overwrites = await get_category_overwrites(session, token, COMMUNITY_CATEGORY_ID)

        logger.info(f"Lobby-Kanäle: {len(lobby_overwrites)} Permission-Overwrites")
        logger.info(f"Community-/Kundenbereich: {len(community_overwrites)} Permission-Overwrites")

        # 4. 📋patch-notes (öffentlich, read-only)
        # Kategorie-Berechtigungen übernehmen + SEND_MESSAGES für @everyone verbieten
        patch_overwrites = []
        everyone_found = False

        for ow in lobby_overwrites:
            entry = {"id": ow["id"], "type": ow["type"],
                     "allow": str(ow.get("allow", "0")),
                     "deny": str(ow.get("deny", "0"))}

            if ow["id"] == ZERODOX_GUILD_ID:
                # @everyone: bestehende Denies + SEND_MESSAGES + ADD_REACTIONS
                existing_deny = int(entry["deny"])
                entry["deny"] = str(existing_deny | SEND_MESSAGES | ADD_REACTIONS)
                everyone_found = True

            patch_overwrites.append(entry)

        if not everyone_found:
            # @everyone explizit hinzufügen
            patch_overwrites.append({
                "id": ZERODOX_GUILD_ID,
                "type": 0,  # role
                "allow": "0",
                "deny": str(SEND_MESSAGES | ADD_REACTIONS),
            })

        # Bot darf senden
        patch_overwrites.append({
            "id": bot_id,
            "type": 1,  # member
            "allow": str(SEND_MESSAGES | EMBED_LINKS | ATTACH_FILES),
            "deny": "0",
        })

        await setup_channel(session, token, PATCH_NOTES_CHANNEL_ID,
                            LOBBY_CATEGORY_ID, patch_overwrites, "📋patch-notes")

        # 5. 🔧dev-updates (intern, read-only, Kunden-Rolle kann sehen)
        dev_overwrites = []
        everyone_found = False

        for ow in community_overwrites:
            entry = {"id": ow["id"], "type": ow["type"],
                     "allow": str(ow.get("allow", "0")),
                     "deny": str(ow.get("deny", "0"))}

            if ow["id"] == ZERODOX_GUILD_ID:
                # @everyone: bestehende Denies + SEND_MESSAGES
                existing_deny = int(entry["deny"])
                entry["deny"] = str(existing_deny | SEND_MESSAGES)
                everyone_found = True

            patch_entry = True
            # Kunden-Rolle: VIEW + READ_HISTORY erlauben, SEND verbieten
            if ow["id"] == CUSTOMER_ROLE_ID:
                existing_allow = int(entry["allow"])
                entry["allow"] = str(existing_allow | VIEW_CHANNEL | READ_MESSAGE_HISTORY)
                existing_deny = int(entry["deny"])
                entry["deny"] = str(existing_deny | SEND_MESSAGES)

            dev_overwrites.append(entry)

        if not everyone_found:
            dev_overwrites.append({
                "id": ZERODOX_GUILD_ID,
                "type": 0,
                "allow": "0",
                "deny": str(SEND_MESSAGES),
            })

        # Kunden-Rolle hinzufügen falls nicht in Kategorie
        customer_role_found = any(ow["id"] == CUSTOMER_ROLE_ID for ow in community_overwrites)
        if not customer_role_found:
            dev_overwrites.append({
                "id": CUSTOMER_ROLE_ID,
                "type": 0,  # role
                "allow": str(VIEW_CHANNEL | READ_MESSAGE_HISTORY),
                "deny": str(SEND_MESSAGES),
            })

        # Bot darf senden + Rolle pingen
        dev_overwrites.append({
            "id": bot_id,
            "type": 1,  # member
            "allow": str(SEND_MESSAGES | EMBED_LINKS | ATTACH_FILES),
            "deny": "0",
        })

        await setup_channel(session, token, DEV_UPDATES_CHANNEL_ID,
                            COMMUNITY_CATEGORY_ID, dev_overwrites, "🔧dev-updates")

        # 6. Zusammenfassung
        logger.info("")
        logger.info("=" * 60)
        logger.info("✅ ZERODOX Patch-Notes Channels erfolgreich konfiguriert!")
        logger.info("=" * 60)
        logger.info(f"  📋 patch-notes  → Lobby-Kanäle (öffentlich, read-only)")
        logger.info(f"  🔧 dev-updates  → Community-/Kundenbereich (intern, read-only)")
        logger.info("")
        logger.info("Nächste Schritte:")
        logger.info("  1. config.yaml aktualisieren (Channel-IDs für ZERODOX Patch Notes)")
        logger.info("  2. Bot neustarten (scripts/restart.sh)")


if __name__ == "__main__":
    asyncio.run(main())
