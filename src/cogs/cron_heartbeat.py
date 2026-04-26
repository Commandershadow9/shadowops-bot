"""
Cog für Cron-Heartbeat-Watching.

Überwacht ob ZERODOX-kritische Cron-Jobs (insbesondere
``scripts/synthetic-monitor.sh``) noch aktiv laufen, indem die Mtime
ihrer Log-Dateien beobachtet wird.

Hintergrund: Der ``synthetic-monitor.sh`` ist Layer-3-Defense-in-Depth gegen
CSP-Outages (siehe ``ZERODOX/docs/SECURITY_CSP.md``). Wenn dieser Cron
selbst tot ist (Cron-Daemon hängt, Skript-Fehler, RAM-OOM-Kill), gibt es
KEINE Alarmierung mehr — und damit kein Frühwarnsystem für Frontend-Bugs.

Lösung: ShadowOps-Bot prüft alle 5 Min die Mtime der Log-Datei. Wenn
älter als die erwartete Cron-Frequenz × 2 + Slack → Discord-Alarm im
Critical-Channel. Da der Bot als systemd-Service mit Restart-Logik läuft,
ist er robuster als der Cron-Daemon → "Watcher der den Watcher beobachtet".

Lehre aus dem ZERODOX-Vorfall 2026-04-13/14: damals lief der CSP-Bug
11 Tage unbemerkt. Layer 3 wäre theoretisch greifbar gewesen, aber der
Layer existierte noch nicht UND es gab keinen Heartbeat-Watcher der den
Layer beobachtet. Mit dieser Cog gibt's nun beide.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import discord
from discord.ext import commands, tasks

from utils.embeds import EmbedBuilder, Severity


@dataclass(frozen=True)
class HeartbeatTarget:
    """Definiert einen Cron-Job, dessen Heartbeat überwacht werden soll."""

    name: str
    """Menschenlesbarer Name für Discord-Alerts."""

    log_path: Path
    """Pfad zur Log-Datei. Wird auf Mtime geprüft."""

    expected_interval_minutes: int
    """Erwarteter Cron-Intervall in Minuten."""

    slack_factor: float = 2.0
    """Toleranz-Faktor: Datei darf älter sein als interval × factor + slack_minutes."""

    slack_minutes: int = 5
    """Zusätzlicher Slack über das Maximum."""

    project_tag: str = "🖥️ [ZERODOX]"
    """Project-Tag im Embed."""

    def max_age_minutes(self) -> int:
        return int(self.expected_interval_minutes * self.slack_factor) + self.slack_minutes


# Konfiguration: Welche Cron-Jobs werden beobachtet?
# Erweiterbar — bei neuen Cron-kritischen Skripts hier ergänzen.
HEARTBEAT_TARGETS = [
    HeartbeatTarget(
        name="ZERODOX Synthetic Monitor (Frontend-Smoke + CSP-Spike)",
        log_path=Path("/home/cmdshadow/ZERODOX/logs/synthetic-monitor.log"),
        expected_interval_minutes=15,
        # max_age = 15 × 2 + 5 = 35 Min → erst nach 2 verpassten Runs alarmieren
    ),
]


class CronHeartbeatCog(commands.Cog):
    """Beobachtet Cron-Job-Heartbeats via Log-File-Mtime."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.logger = bot.logger
        # Anti-Spam: pro Target merken wir uns wann wir zuletzt alarmiert haben.
        # Verhindert dass ein dauerhaft toter Cron alle 5 Min einen Alert wirft.
        self._last_alert: dict[str, datetime] = {}
        self._alert_cooldown_minutes = 60

    async def cog_load(self) -> None:
        """Beim Laden der Cog: Heartbeat-Loop starten."""
        self.heartbeat_loop.start()
        self.logger.info(
            "🫀 CronHeartbeatCog geladen — beobachtet %d Target(s)",
            len(HEARTBEAT_TARGETS),
        )

    async def cog_unload(self) -> None:
        """Beim Entladen sauber stoppen."""
        self.heartbeat_loop.cancel()

    @tasks.loop(minutes=5)
    async def heartbeat_loop(self) -> None:
        """Prüft alle 5 Min die Mtime aller Heartbeat-Targets."""
        now = datetime.now(timezone.utc)
        for target in HEARTBEAT_TARGETS:
            try:
                await self._check_target(target, now)
            except Exception as exc:
                # Niemals den Loop sterben lassen — nur loggen
                self.logger.error(
                    "Heartbeat-Check für %s fehlgeschlagen: %s",
                    target.name, exc, exc_info=True,
                )

    @heartbeat_loop.before_loop
    async def before_heartbeat_loop(self) -> None:
        """Wartet bis Bot connected ist, bevor der Loop startet."""
        await self.bot.wait_until_ready()

    async def _check_target(self, target: HeartbeatTarget, now: datetime) -> None:
        """Prüft eine einzelne Heartbeat-Datei."""
        if not target.log_path.exists():
            await self._maybe_alert(
                target,
                title=f"Heartbeat-Datei fehlt — {target.name}",
                description=(
                    f"Erwartete Log-Datei `{target.log_path}` existiert nicht. "
                    f"Wahrscheinlich hat der Cron-Job nie gelaufen oder das Logs-Verzeichnis "
                    f"ist falsch berechtigt."
                ),
                severity=Severity.HIGH,
                fields=[
                    {"name": "Erwartetes Intervall", "value": f"{target.expected_interval_minutes} Min", "inline": True},
                    {"name": "Toleranz", "value": f"{target.max_age_minutes()} Min", "inline": True},
                ],
            )
            return

        mtime = datetime.fromtimestamp(target.log_path.stat().st_mtime, tz=timezone.utc)
        age = now - mtime
        max_age = timedelta(minutes=target.max_age_minutes())

        if age > max_age:
            await self._maybe_alert(
                target,
                title=f"Cron-Heartbeat tot — {target.name}",
                description=(
                    f"Log-Datei `{target.log_path.name}` wurde seit "
                    f"**{int(age.total_seconds() // 60)} Min** nicht mehr beschrieben. "
                    f"Erwartet: alle {target.expected_interval_minutes} Min. "
                    f"\n\nMögliche Ursachen: Cron-Daemon hängt, Skript-Fehler, RAM-OOM-Kill, "
                    f"VPS-Last hoch. **Sofortige manuelle Prüfung empfohlen** — "
                    f"Defense-in-Depth-Layer 3 ist aktuell blind."
                ),
                severity=Severity.CRITICAL,
                fields=[
                    {"name": "Letzte Aktivität", "value": mtime.strftime("%Y-%m-%d %H:%M UTC"), "inline": True},
                    {"name": "Alter", "value": f"{int(age.total_seconds() // 60)} Min", "inline": True},
                    {"name": "Max. erlaubt", "value": f"{target.max_age_minutes()} Min", "inline": True},
                    {
                        "name": "Diagnose-Schritte",
                        "value": (
                            "```\nsystemctl status cron\n"
                            "tail -20 /home/cmdshadow/ZERODOX/logs/synthetic-monitor.log\n"
                            "ls -la $(dirname " + str(target.log_path) + ")\n```"
                        ),
                        "inline": False,
                    },
                ],
            )
        else:
            # Alles OK — falls vorher ein Alert war, Cooldown zurücksetzen
            # damit das nächste Failure direkt alarmiert (nicht durch Cooldown stumm bleibt).
            if target.name in self._last_alert:
                del self._last_alert[target.name]
                self.logger.info(
                    "🫀 %s: Heartbeat wieder aktiv (Alter: %d Min)",
                    target.name, int(age.total_seconds() // 60),
                )

    async def _maybe_alert(
        self,
        target: HeartbeatTarget,
        *,
        title: str,
        description: str,
        severity: Severity,
        fields: list[dict],
    ) -> None:
        """Sendet Discord-Alert wenn Cooldown abgelaufen."""
        now = datetime.now(timezone.utc)
        last = self._last_alert.get(target.name)
        if last and (now - last) < timedelta(minutes=self._alert_cooldown_minutes):
            self.logger.debug(
                "Heartbeat-Alert für %s unterdrückt (Cooldown läuft, letzter: %s)",
                target.name, last.isoformat(),
            )
            return

        # Bot-Config nach Critical-Channel fragen
        critical_channel_id = self._resolve_critical_channel_id()
        if not critical_channel_id:
            self.logger.warning(
                "Heartbeat-Alert für %s konnte nicht gesendet werden: kein Critical-Channel konfiguriert",
                target.name,
            )
            return

        channel = self.bot.get_channel(critical_channel_id)
        if not isinstance(channel, discord.TextChannel):
            self.logger.warning(
                "Heartbeat-Alert für %s: Critical-Channel %s ist kein TextChannel",
                target.name, critical_channel_id,
            )
            return

        embed = EmbedBuilder.create_alert(
            title=title,
            description=description,
            severity=severity,
            fields=fields,
            project_tag=target.project_tag,
            footer="ShadowOps Cron-Heartbeat-Watcher",
        )
        try:
            await channel.send(embed=embed)
            self._last_alert[target.name] = now
            self.logger.warning("🚨 Heartbeat-Alert gesendet: %s", target.name)
        except discord.HTTPException as exc:
            self.logger.error("Discord-Send für %s fehlgeschlagen: %s", target.name, exc)

    def _resolve_critical_channel_id(self) -> int | None:
        """Holt die Critical-Channel-ID aus der Bot-Konfiguration.

        Fallback-Reihenfolge:
        1. ``self.bot.config.channels.critical`` (typische ShadowOps-Konfig)
        2. ``CRITICAL_CHANNEL_ID`` env-var
        3. None (kein Alert)
        """
        # Versuch 1: über Bot-Config
        config = getattr(self.bot, "config", None)
        if config is not None:
            channels = getattr(config, "channels", None)
            if channels is not None:
                critical = getattr(channels, "critical", None)
                if isinstance(critical, int) and critical > 0:
                    return critical
                # config kann auch dict sein
                if isinstance(channels, dict):
                    val = channels.get("critical")
                    if isinstance(val, int) and val > 0:
                        return val

        # Versuch 2: env-var
        env_val = os.environ.get("CRITICAL_CHANNEL_ID")
        if env_val and env_val.isdigit():
            return int(env_val)

        return None


async def setup(bot: commands.Bot) -> None:
    """discord.py Cog-Loading-Hook."""
    await bot.add_cog(CronHeartbeatCog(bot))
