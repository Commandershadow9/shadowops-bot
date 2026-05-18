"""
Slash-Command /claude — startet headless Claude-Session und postet Antwort.

Owner-only Trigger für Mobile-Workflow: vom Discord-Handy aus eine Claude-Session
auf dem Server anstoßen, ohne SSH/VPN. Antwort kommt als Discord-Nachricht zurück.

Sicherheit:
- Owner-User-ID Check (User-ID 236297772540624902, identisch zu permissions.admins
  in config.yaml). KEIN administrator-Permission-Check, weil das jeden Server-Admin
  freischalten würde.
- Project-Whitelist (Path-Traversal verhindern, kein cwd= aus User-Input).
- Timeout-Cap.
"""

import asyncio
import time
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

# Owner-User-ID (aus config.yaml permissions.admins[0])
OWNER_USER_ID = 236297772540624902

# Project-Whitelist: shortcut → absolute path
HOME = Path.home()
PROJECT_PATHS: dict[str, Path] = {
    "home": HOME,
    "zerodox": HOME / "ZERODOX",
    "guildscout": HOME / "GuildScout",
    "shadowops": HOME / "shadowops-bot",
    "agents": HOME / "agents",
    "sharedui": HOME / "libs" / "shared-ui",
    "design": HOME / "cmdshadow-design",
}

# Discord 2000-char limit, Buffer für ```...```-Marker
DISCORD_LIMIT = 1900

# Timeouts (Sekunden)
DEFAULT_TIMEOUT = 300
MAX_TIMEOUT = 600


class ClaudeCLICog(commands.Cog):
    """Slash-Command /claude für Mobile-Trigger headless Claude-Sessions."""

    def __init__(self, bot):
        self.bot = bot
        self.logger = bot.logger

    @app_commands.command(
        name="claude",
        description="Headless Claude-Session starten (owner-only)",
    )
    @app_commands.describe(
        prompt="Was Claude tun soll",
        project="Working-Directory (default: home)",
        model="Modell-Wahl (default: sonnet)",
        timeout="Timeout in Sekunden (default 300, max 600)",
    )
    @app_commands.choices(
        project=[
            app_commands.Choice(name=k, value=k) for k in PROJECT_PATHS
        ],
        model=[
            app_commands.Choice(name="sonnet (schnell, default)", value="sonnet"),
            app_commands.Choice(name="opus (smart, langsamer)", value="opus"),
            app_commands.Choice(name="haiku (sehr schnell)", value="haiku"),
        ],
    )
    async def claude_cmd(
        self,
        interaction: discord.Interaction,
        prompt: str,
        project: Optional[str] = "home",
        model: Optional[str] = "sonnet",
        timeout: Optional[int] = DEFAULT_TIMEOUT,
    ):
        # Owner-Check
        if interaction.user.id != OWNER_USER_ID:
            await interaction.response.send_message(
                "❌ Dieser Command ist owner-only.", ephemeral=True
            )
            return

        # Project-Whitelist-Check
        cwd = PROJECT_PATHS.get(project or "home")
        if cwd is None or not cwd.is_dir():
            await interaction.response.send_message(
                f"❌ Unbekanntes/fehlendes Projekt: `{project}`", ephemeral=True
            )
            return

        # Timeout-Cap
        timeout_s = min(max(timeout or DEFAULT_TIMEOUT, 30), MAX_TIMEOUT)

        await interaction.response.defer(thinking=True)

        self.logger.info(
            f"🤖 /claude triggered by {interaction.user} "
            f"(project={project}, model={model}, timeout={timeout_s}s, "
            f"prompt_len={len(prompt)})"
        )

        try:
            stdout, stderr, returncode, elapsed = await self._run_claude(
                prompt=prompt,
                cwd=str(cwd),
                model=model or "sonnet",
                timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            await interaction.followup.send(
                f"⏱️ Timeout nach {timeout_s}s. Frag Claude in kürzeren Schritten "
                "oder erhöhe `timeout:`.",
                ephemeral=True,
            )
            return
        except FileNotFoundError:
            await interaction.followup.send(
                "❌ `claude` CLI nicht gefunden. PATH prüfen "
                "(`/home/cmdshadow/.local/bin/claude` erwartet).",
                ephemeral=True,
            )
            return
        except Exception as e:
            self.logger.error(f"❌ /claude Fehler: {e}", exc_info=True)
            await interaction.followup.send(
                f"❌ Fehler beim Claude-Aufruf: `{e}`", ephemeral=True
            )
            return

        # Header-Embed
        prompt_preview = prompt if len(prompt) <= 200 else prompt[:200] + "…"
        header = discord.Embed(
            title=f"🤖 Claude → {project}",
            description=(
                f"**Prompt:** {prompt_preview}\n"
                f"**Model:** {model} · **Dauer:** {elapsed:.1f}s · "
                f"**Exit:** {returncode}"
            ),
            color=0x10A37F if returncode == 0 else 0xE63946,
        )
        await interaction.followup.send(embed=header)

        # Antwort als Code-Block-Chunks
        body = (stdout or "").strip()
        if not body:
            body = (stderr or "(keine Ausgabe)").strip()

        for chunk in _chunk_text(body, DISCORD_LIMIT):
            await interaction.followup.send(f"```\n{chunk}\n```")

    async def _run_claude(
        self, prompt: str, cwd: str, model: str, timeout: int
    ) -> tuple[str, str, int, float]:
        """Run `claude -p PROMPT --output-format text --model MODEL` in cwd."""
        start = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            "claude",
            "-p", prompt,
            "--output-format", "text",
            "--model", model,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise

        elapsed = time.monotonic() - start
        return (
            stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
            proc.returncode if proc.returncode is not None else -1,
            elapsed,
        )


def _chunk_text(text: str, size: int) -> list[str]:
    """Split text into ≤size chunks, breaking on newlines where possible."""
    if not text:
        return [""]
    chunks: list[str] = []
    current: list[str] = []
    cur_len = 0
    for line in text.split("\n"):
        # Falls eine einzelne Zeile selbst zu lang ist: hart splitten
        while len(line) > size:
            chunks.append(line[:size])
            line = line[size:]
        if cur_len + len(line) + 1 > size:
            if current:
                chunks.append("\n".join(current))
            current = [line]
            cur_len = len(line) + 1
        else:
            current.append(line)
            cur_len += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


async def setup(bot):
    await bot.add_cog(ClaudeCLICog(bot))
