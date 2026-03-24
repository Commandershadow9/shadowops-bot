"""
ActivityMonitor — Erkennt ob der User aktiv auf dem Server arbeitet.

Prueft SSH-Sessions, Git-Aktivitaet, AI-Prozesse und Discord-Praesenz.
Der SecurityScanAgent wartet, bis der User idle ist, bevor er loslegt.

Kopiert von analyst/activity_monitor.py — gleiche Logik, neuer Pfad.
"""

import asyncio
import logging
import time
from typing import Optional

import discord

logger = logging.getLogger('shadowops.security_engine.activity_monitor')

# Git-Projekte auf dem Server
PROJECTS = [
    '/home/cmdshadow/GuildScout',
    '/home/cmdshadow/ZERODOX',
    '/home/cmdshadow/libs/shared-ui',
    '/home/cmdshadow/agents',
    '/home/cmdshadow/shadowops-bot',
]

# 30 Minuten Cooldown nach letzter Aktivitaet
COOLDOWN_SECONDS = 1800

# Pruef-Intervall in Sekunden
CHECK_INTERVAL = 60

# Git-Log Zeitfenster in Minuten
GIT_RECENT_MINUTES = 30


class ActivityMonitor:
    """Ueberwacht User-Aktivitaet auf dem Server.

    Kombiniert 4 Signale (SSH, Git, AI-Prozesse, Discord-Praesenz)
    um zu entscheiden, ob der User gerade aktiv arbeitet.
    Nach der letzten erkannten Aktivitaet laeuft ein 30-Minuten-Cooldown.
    """

    def __init__(self, bot):
        self.bot = bot
        self.last_activity = time.time()  # Startet als "aktiv"
        self._owner_id: Optional[int] = None
        self._was_active = True  # Fuer State-Change-Logging

    # ─────────────────────────────────────────────
    # Oeffentliche Methoden
    # ─────────────────────────────────────────────

    async def is_user_active(self) -> bool:
        """Hauptmethode: Prueft ob der User aktiv auf dem Server arbeitet.

        Fuehrt 4 Checks parallel aus. Wenn mindestens einer True ergibt,
        gilt der User als aktiv. Nach der letzten Aktivitaet laeuft ein
        30-Minuten-Cooldown bevor der Agent starten darf.

        Returns:
            True wenn User aktiv oder im Cooldown, False wenn idle
        """
        # Alle 4 Checks parallel ausfuehren
        results = await asyncio.gather(
            self._check_ssh(),
            self._check_git_activity(),
            self._check_ai_processes(),
            self._check_discord_presence(),
            return_exceptions=True,
        )

        # Exceptions als False werten
        checks = [r if isinstance(r, bool) else False for r in results]
        check_names = ['SSH', 'Git', 'AI-Prozesse', 'Discord']

        # Aktive Checks loggen (nur auf DEBUG)
        active_signals = [name for name, val in zip(check_names, checks) if val]
        if active_signals:
            logger.debug("Aktive Signale: %s", ', '.join(active_signals))

        if any(checks):
            # Mindestens ein Signal aktiv → User arbeitet
            self.last_activity = time.time()
            if not self._was_active:
                logger.info("User ist wieder aktiv (Signale: %s)", ', '.join(active_signals))
                self._was_active = True
            return True

        # Kein Signal aktiv → Cooldown pruefen
        elapsed = time.time() - self.last_activity
        if elapsed < COOLDOWN_SECONDS:
            remaining = COOLDOWN_SECONDS - elapsed
            logger.debug(
                "Kein aktives Signal, aber Cooldown laeuft noch (%.0f Min verbleibend)",
                remaining / 60,
            )
            return True

        # Cooldown abgelaufen → User ist idle
        if self._was_active:
            logger.info(
                "User ist idle (kein Signal seit %.0f Min)",
                elapsed / 60,
            )
            self._was_active = False
        return False

    async def is_user_on_discord(self) -> Optional[str]:
        """Discord-Praesenz des Owners abfragen.

        Returns:
            'online', 'idle', 'dnd', 'offline', oder None wenn nicht ermittelbar
        """
        owner_id = self._get_owner_id()
        if not owner_id:
            return None

        # Owner in allen Guilds suchen
        for guild in self.bot.guilds:
            member = guild.get_member(owner_id)
            if member:
                return str(member.status)

        return None

    async def wait_for_idle(self):
        """Blockiert bis der User idle ist.

        Prueft alle CHECK_INTERVAL Sekunden ob der User noch aktiv ist.
        Kehrt erst zurueck wenn is_user_active() False liefert.
        """
        logger.info("Warte auf User-Idle...")
        while await self.is_user_active():
            await asyncio.sleep(CHECK_INTERVAL)
        logger.info("User ist idle — Agent kann starten")

    # ─────────────────────────────────────────────
    # Private Check-Methoden
    # ─────────────────────────────────────────────

    async def _check_ssh(self) -> bool:
        """Prueft ob aktive SSH-Sessions existieren."""
        try:
            proc = await asyncio.create_subprocess_shell(
                "who | wc -l",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            count = int(stdout.decode().strip())
            return count > 0
        except Exception:
            return False

    async def _check_git_activity(self) -> bool:
        """Prueft ob in den letzten 30 Minuten Git-Commits in einem Projekt waren."""
        for project in PROJECTS:
            try:
                cmd = (
                    f"git -C {project} log --oneline "
                    f"--since='{GIT_RECENT_MINUTES} minutes ago' 2>/dev/null | wc -l"
                )
                proc = await asyncio.create_subprocess_shell(
                    cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
                count = int(stdout.decode().strip())
                if count > 0:
                    return True
            except Exception:
                continue
        return False

    async def _check_ai_processes(self) -> bool:
        """Prueft ob der User interaktive Claude Code Sessions laufen hat.

        Erkennt NUR: claude --session-id (interaktive User-Sessions)
        Ignoriert: codex exec (Agents), codex mcp-server, claude -p (Analyst), serena
        """
        try:
            proc = await asyncio.create_subprocess_shell(
                "pgrep -a claude 2>/dev/null | grep -c -- '--session-id' || echo 0",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            count = int(stdout.decode().strip())
            return count > 0
        except Exception:
            return False

    async def _check_discord_presence(self) -> bool:
        """Prueft ob der Owner auf Discord online oder beschaeftigt ist."""
        try:
            status = await self.is_user_on_discord()
            return status in ('online', 'dnd')
        except Exception:
            return False

    # ─────────────────────────────────────────────
    # Hilfsmethoden
    # ─────────────────────────────────────────────

    def _get_owner_id(self) -> Optional[int]:
        """Owner-ID lazy aus Bot-Config oder Application laden."""
        if self._owner_id:
            return self._owner_id

        # Versuch 1: bot.config (YAML)
        try:
            config = self.bot.config
            if hasattr(config, 'owner_id'):
                self._owner_id = int(config.owner_id)
                return self._owner_id
            if hasattr(config, '_config'):
                discord_cfg = config._config.get('discord', {})
                if 'owner_id' in discord_cfg:
                    self._owner_id = int(discord_cfg['owner_id'])
                    return self._owner_id
        except Exception:
            pass

        # Versuch 2: bot.owner_id (discord.py built-in)
        try:
            if self.bot.owner_id:
                self._owner_id = self.bot.owner_id
                return self._owner_id
        except Exception:
            pass

        # Versuch 3: bot.application.owner.id
        try:
            if self.bot.application and self.bot.application.owner:
                self._owner_id = self.bot.application.owner.id
                return self._owner_id
        except Exception:
            pass

        logger.warning("Owner-ID konnte nicht ermittelt werden")
        return None
