"""
Notfall-Befehle für CrowdSec-Sperren (ZERODOX #3260).

WOZU DIESER COG EXISTIERT
-------------------------
Der CrowdSec-Firewall-Bouncer sperrt mit ``deny_action: DROP`` ohne
Port-Filter. Eine Sperre trifft damit auch WireGuard (51820/udp) — und weil
SSH (Port 47822) ausschliesslich über das VPN erreichbar ist, ist der
Serverzugang dann vollständig zu, nicht nur die Website.

Am 10.09.2026 um 00:40 Berlin ist genau das passiert: Der Betreiber-Anschluss
löste ``http-crawl-non_statics`` aus, weil Next.js beim Rendern der
Admin-Sidebar rund vierzig Seiten gleichzeitig vorlädt. Vier Stunden ohne
Zugang, ohne dokumentierten Weg zurück.

Dieser Bot ist der Weg zurück. Er läuft auf demselben Server, spricht aber
**ausgehend** mit Discord — eine eingehende Sperre trifft ihn nicht. Er bleibt
also erreichbar, wenn sonst nichts mehr geht, und lässt sich vom Handy aus
bedienen.

Runbook: ``ZERODOX/docs/runbooks/2026-09-10-von-crowdsec-ausgesperrt.md``

SICHERHEIT
----------
Beide Befehle sind auf den **Application-Owner** beschränkt, nicht auf
"Administrator im Server". Das ist bewusst strenger als die übrigen Cogs:
Eine Server-Administratorrolle kann vergeben werden, der Application-Owner
steht im Discord-Developer-Portal und ist der Betreiber selbst.

Ausgeführt wird ausschliesslich ``/usr/local/bin/zerodox-crowdsec-entsperren``
(root:root 0755). Dort findet die Eingabeprüfung statt und dort wird
protokolliert — nicht hier. Aufgerufen wird über ``create_subprocess_exec``
mit Argumentliste, also ohne Shell: Der Text aus dem Discord-Befehl wird
nie als Kommando interpretiert.

⚠️ Der Wrapper begrenzt **nicht die Rechte** des Bots. Das Konto ``cmdshadow``,
unter dem der Bot läuft, hat ``NOPASSWD: ALL`` — ein übernommener Bot hätte
den Server ohnehin. Was der Wrapper leistet, ist Eingabeprüfung, eine einzige
prüfbare Stelle und ein lückenloses Protokoll im Journal. Die eigentliche
Rechtebeschränkung wäre ein eigener Systembenutzer für den Bot; das ist ein
eigener Vorgang und hier ausdrücklich nicht behauptet.
"""

import asyncio
import json

import discord
from discord import app_commands
from discord.ext import commands

WRAPPER = "/usr/local/bin/zerodox-crowdsec-entsperren"
TIMEOUT_SEK = 20

# Szenarien, die auf dieser Anwendung niemals versehentlich ausgelöst werden.
# Wer sie trifft, hat /.env, /wp-admin oder Traversal-Pfade angefragt. Sie
# werden in der Liste markiert, damit ein versehentliches Entsperren eines
# echten Angreifers auffällt, bevor es passiert.
HARTE_SZENARIEN = (
    "cmdshadow/zerodox-eindeutiger-angriff",
    "cmdshadow/zerodox-koeder-pfade",
)


class CrowdSecNotfallCog(commands.Cog):
    """Sperren ansehen und aufheben, wenn der normale Zugang zu ist."""

    def __init__(self, bot):
        self.bot = bot
        self.logger = getattr(bot, "logger", None)

    async def _ist_betreiber(self, interaction: discord.Interaction) -> bool:
        """Nur der Application-Owner darf diese Befehle nutzen."""
        try:
            if await self.bot.is_owner(interaction.user):
                return True
        except Exception:  # noqa: BLE001 — im Zweifel verweigern
            pass

        if self.logger:
            self.logger.warning(
                "CrowdSec-Notfallbefehl abgelehnt: %s (%s) ist nicht der Owner",
                interaction.user,
                interaction.user.id,
            )
        await interaction.response.send_message(
            "Dieser Befehl ist dem Betreiber vorbehalten.", ephemeral=True
        )
        return False

    async def _wrapper(self, *args: str) -> tuple[str, str, int]:
        """Ruft den root-Wrapper ohne Shell auf."""
        proc = await asyncio.create_subprocess_exec(
            "sudo",
            "-n",  # niemals nach einem Passwort fragen — sonst hängt der Bot
            WRAPPER,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=TIMEOUT_SEK
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return "", "Zeitüberschreitung beim Aufruf des Wrappers.", -1

        return (
            stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
            proc.returncode if proc.returncode is not None else -1,
        )

    # ── /sperren ──────────────────────────────────────────────────────────────

    @app_commands.command(
        name="sperren",
        description="🛡️ Aktive CrowdSec-Sperren anzeigen (Betreiber)",
    )
    async def sperren_command(self, interaction: discord.Interaction) -> None:
        if not await self._ist_betreiber(interaction):
            return

        await interaction.response.defer(ephemeral=True)
        stdout, stderr, code = await self._wrapper("--liste")

        if code != 0:
            await interaction.followup.send(
                f"Konnte die Sperrliste nicht lesen.\n```\n{(stderr or 'unbekannt')[:1500]}\n```"
            )
            return

        try:
            rohdaten = json.loads(stdout or "[]")
        except json.JSONDecodeError:
            await interaction.followup.send("Die Sperrliste war nicht lesbar (kein gültiges JSON).")
            return

        eintraege = []
        for alarm in rohdaten or []:
            quelle = alarm.get("source") or {}
            for entscheidung in alarm.get("decisions") or []:
                if entscheidung.get("scope") != "Ip":
                    continue
                eintraege.append(
                    {
                        "ip": entscheidung.get("value", "?"),
                        "grund": entscheidung.get("scenario", "?"),
                        "dauer": entscheidung.get("duration", "?"),
                        "herkunft": entscheidung.get("origin", "?"),
                        "land": quelle.get("cn") or "",
                        "netz": (quelle.get("as_name") or "")[:28],
                    }
                )

        if not eintraege:
            await interaction.followup.send(
                "Keine aktive IP-Sperre. Wenn du trotzdem nicht durchkommst, "
                "liegt es **nicht** an CrowdSec — siehe Runbook, Abschnitt 1."
            )
            return

        # Die eigenen Szenarien zuletzt: Ein Massenscanner interessiert hier
        # niemanden, gesucht wird die eine Zeile, die nach Privatanschluss
        # aussieht (deutsches Land, Provider statt Rechenzentrum).
        eintraege.sort(key=lambda e: (e["grund"] in HARTE_SZENARIEN, e["grund"]))

        zeilen = []
        for e in eintraege[:20]:
            marke = "⛔" if e["grund"] in HARTE_SZENARIEN else "•"
            ort = f"{e['land']} {e['netz']}".strip() or "—"
            zeilen.append(f"{marke} `{e['ip']:<15}` {e['dauer']:>9}  {ort}\n    {e['grund']}")

        text = "\n".join(zeilen)
        rest = len(eintraege) - len(eintraege[:20])
        fuss = (
            f"\n\n… und {rest} weitere." if rest > 0 else ""
        ) + (
            "\n\n⛔ = eindeutiger Angriff (`/.env`, `/wp-admin`, Traversal). "
            "So etwas löst ein normaler Besucher nie aus — auch du nicht. "
            "Wenn dein Anschluss hier steht, ist die Sperre der Befund."
        )

        embed = discord.Embed(
            title="🛡️ Aktive CrowdSec-Sperren",
            description=(text + fuss)[:4000],
            colour=0x3498DB,
        )
        embed.set_footer(text="Aufheben mit /entsperren <ip>")
        await interaction.followup.send(embed=embed)

    # ── /entsperren ───────────────────────────────────────────────────────────

    @app_commands.command(
        name="entsperren",
        description="🔓 Eine IP aus der CrowdSec-Sperre lösen (Betreiber)",
    )
    @app_commands.describe(ip="Die zu entsperrende IP-Adresse, z.B. 82.115.116.41")
    async def entsperren_command(
        self, interaction: discord.Interaction, ip: str
    ) -> None:
        if not await self._ist_betreiber(interaction):
            return

        await interaction.response.defer(ephemeral=True)
        stdout, stderr, code = await self._wrapper(ip.strip())

        if self.logger:
            self.logger.warning(
                "CrowdSec-Entsperrung angefordert: ip=%s durch=%s (%s) exit=%s",
                ip,
                interaction.user,
                interaction.user.id,
                code,
            )

        ausgabe = (stdout or "").strip()

        if code == 65:  # EX_DATAERR — Eingabe abgewiesen
            await interaction.followup.send(
                f"`{ip[:60]}` ist keine gültige IP-Adresse. "
                "Bereiche (CIDR) sind bewusst nicht möglich — eine einzelne Adresse genügt."
            )
            return

        if code != 0:
            await interaction.followup.send(
                f"Entsperren fehlgeschlagen.\n```\n{(stderr or ausgabe or 'unbekannt')[:1500]}\n```"
            )
            return

        if ausgabe.startswith("KEINE_SPERRE"):
            await interaction.followup.send(
                f"Für `{ip}` lag keine Sperre vor. Wenn du von dort nicht durchkommst, "
                "liegt die Ursache woanders — Runbook, Abschnitt 1 trennt die Fälle."
            )
            return

        # Format: AUFGEHOBEN <ip> <anzahl> <gründe>
        teile = ausgabe.split(" ", 3)
        anzahl = teile[2] if len(teile) > 2 else "?"
        gruende = teile[3] if len(teile) > 3 else "?"

        embed = discord.Embed(
            title="🔓 Sperre aufgehoben",
            description=(
                f"**{ip}** ist wieder frei.\n\n"
                f"Aufgehoben: {anzahl} Entscheidung(en)\n"
                f"Grund war: `{gruende}`"
            ),
            colour=0x2ECC71,
        )
        embed.set_footer(
            text="Löst dasselbe Verhalten erneut aus, entsteht sofort eine neue Sperre. "
            "Beim zweiten Mal ist die Schwelle falsch, nicht dein Verhalten."
        )
        await interaction.followup.send(embed=embed)


async def setup(bot):
    await bot.add_cog(CrowdSecNotfallCog(bot))
