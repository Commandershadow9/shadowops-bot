"""Cog für Phase-5e Health-Aggregator.

Pollt alle 60s die drei Health-Endpoints (Schema v1):
- http://10.8.0.10:9100/health         → ci-runner (WireGuard, GitHub Runners, ...)
- https://dev.zerodox.de/api/internal/health → web-dev
- https://zerodox.de/api/internal/health     → web-prod

Quelle der Wahrheit für Schema: ZERODOX/docs/HEALTH_SCHEMA_V1.md

Output:
- 5-Min-Status-Embed in 📊-dashboard (1479615549356114124)
- Drift-Detail-Alert in 🚨-critical (1441655480840617994) bei
  status-Wechsel (z.B. ok → degraded → critical)
- Recovery-Alert bei critical/degraded → ok
- Trend-Report täglich 09:00 in 📊-dashboard mit Top-3 Drift-Events der
  letzten 24h

Persistenz: SQLite ~/shadowops-bot/data/health_history.db
- Schema: (timestamp INT, host TEXT, status TEXT, components_json TEXT, alerts_json TEXT)
- Retention: 90 Tage rolling DELETE

Hintergrund: Phase 7 PR-4 — vollständige Pre-Deploy-Pipeline auf Runner-VM
verlagert die Test-Last vom ZERODOX-VPS. Damit das Monitoring lückenlos
bleibt, bekommt jeder Server einen einheitlichen Health-Endpoint und
ShadowOps aggregiert + alertiert zentral.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path
from typing import Optional

import aiohttp
import discord
from discord.ext import commands, tasks

from integrations.health_schema_v1 import (
    HealthResponse,
    HealthSchemaError,
)
from utils.alert_humanizer import (
    STATUS_COLOR,
    STATUS_EMOJI,
    Urgency,
    humanize_alert,
    humanize_transition,
    runbook_for,
    urgency_line,
)


logger = logging.getLogger(__name__)


# ---------- Konstanten ----------

POLL_INTERVAL_SECONDS = 60
EMBED_INTERVAL_MINUTES = 5
TREND_REPORT_HOUR = 9  # 09:00 lokal

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "health_history.db"
RETENTION_DAYS = 90

DASHBOARD_CHANNEL_ID = 1479615549356114124  # 📊-dashboard
CRITICAL_CHANNEL_ID = 1441655480840617994   # 🚨-critical

# STATUS_COLOR / STATUS_EMOJI sind ab jetzt Single-Source in
# utils.alert_humanizer (Konsistenz-Anker, siehe Design-Doc 2026-05-28).
# Re-Import oben — Werte 1:1 identisch zu den frueheren lokalen Kopien.


@dataclass(frozen=True)
class HealthTarget:
    name: str       # menschenlesbar, z.B. "Runner-VM"
    url: str
    role_hint: str  # erwartete role im Schema, für Cross-Check


TARGETS: list[HealthTarget] = [
    HealthTarget(
        name="Runner-VM",
        url="http://10.8.0.10:9100/health",
        role_hint="ci-runner",
    ),
    HealthTarget(
        name="ZERODOX Production",
        url="https://zerodox.de/api/internal/health",
        role_hint="web-prod",
    ),
    HealthTarget(
        name="ZERODOX Dev",
        url="https://dev.zerodox.de/api/internal/health",
        role_hint="web-dev",
    ),
]


@dataclass
class PollResult:
    """Ein Snapshot des letzten Pollings pro Host."""

    target: HealthTarget
    polled_at: datetime
    response: Optional[HealthResponse] = None
    error: Optional[str] = None

    @property
    def status(self) -> str:
        if self.response is not None:
            return self.response.status
        return "unreachable"


@dataclass
class OpenIncident:
    """Ein offener Vorfall pro Host — bündelt aufeinanderfolgende Drifts.

    Solange ein Host nicht auf ``ok`` zurückkehrt, editieren weitere Drifts die
    bestehende Discord-Message (statt neue Posts), Verlauf als Timeline.
    """

    host: str
    started_at: datetime
    message_id: Optional[int]
    transitions: list[tuple[int, str, str]]  # [(ts, prev, new), ...]
    worst_status: str
    manual_action_seen: bool = False  # für "selbst-erholt"-Heuristik (YAGNI: bleibt False)


# ---------- SQLite-Persistenz ----------


def _init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS health_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp INTEGER NOT NULL,
                host TEXT NOT NULL,
                status TEXT NOT NULL,
                http_status INTEGER NOT NULL,
                components_json TEXT NOT NULL,
                alerts_json TEXT NOT NULL,
                error TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_health_history_host_ts ON health_history(host, timestamp DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_health_history_status ON health_history(status, timestamp DESC)"
        )
        conn.commit()


def _persist(result: PollResult) -> None:
    timestamp = int(result.polled_at.timestamp())
    host = result.target.name
    status = result.status
    if result.response is not None:
        http_status = result.response.http_status
        components_json = json.dumps(result.response.components, separators=(",", ":"))
        alerts_json = json.dumps([a.__dict__ for a in result.response.alerts], separators=(",", ":"))
    else:
        http_status = 0
        components_json = "{}"
        alerts_json = "[]"

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO health_history (timestamp, host, status, http_status, components_json, alerts_json, error)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (timestamp, host, status, http_status, components_json, alerts_json, result.error),
        )
        conn.commit()


def _purge_old() -> int:
    """Löscht Einträge älter als RETENTION_DAYS, gibt Anzahl zurück."""
    cutoff = int(time.time()) - RETENTION_DAYS * 86400
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            "DELETE FROM health_history WHERE timestamp < ?", (cutoff,)
        )
        conn.commit()
        return cursor.rowcount or 0


def _query_drift_events_24h() -> list[tuple[str, str, str, int]]:
    """Findet status-Wechsel der letzten 24h.

    Returns: [(host, from_status, to_status, timestamp), ...]
    """
    cutoff = int(time.time()) - 86400
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT host, status, timestamp FROM health_history
            WHERE timestamp >= ?
            ORDER BY host, timestamp ASC
            """,
            (cutoff,),
        ).fetchall()

    events: list[tuple[str, str, str, int]] = []
    last_status_per_host: dict[str, str] = {}
    for host, status, ts in rows:
        prev = last_status_per_host.get(host)
        if prev is not None and prev != status:
            events.append((host, prev, status, ts))
        last_status_per_host[host] = status
    return events


# ---------- Polling ----------


async def _poll_one(session: aiohttp.ClientSession, target: HealthTarget) -> PollResult:
    polled_at = datetime.now(timezone.utc)
    try:
        async with session.get(target.url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            http_status = resp.status
            try:
                payload = await resp.json(content_type=None)
            except (aiohttp.ContentTypeError, json.JSONDecodeError) as exc:
                return PollResult(
                    target=target, polled_at=polled_at,
                    error=f"non-json response (HTTP {http_status}): {exc!s}",
                )
            try:
                response = HealthResponse.from_dict(payload, http_status=http_status)
            except HealthSchemaError as exc:
                return PollResult(
                    target=target, polled_at=polled_at,
                    error=f"schema-violation: {exc!s}",
                )
            if response.role != target.role_hint:
                logger.warning(
                    "[5e] role mismatch fuer %s: erwartet=%s, geliefert=%s",
                    target.name, target.role_hint, response.role,
                )
            return PollResult(target=target, polled_at=polled_at, response=response)
    except asyncio.TimeoutError:
        return PollResult(target=target, polled_at=polled_at, error="timeout (>10s)")
    except aiohttp.ClientError as exc:
        return PollResult(target=target, polled_at=polled_at, error=f"client-error: {exc!s}")
    except Exception as exc:  # noqa: BLE001
        logger.exception("[5e] unerwarteter Fehler beim Pollen %s", target.url)
        return PollResult(target=target, polled_at=polled_at, error=f"unexpected: {exc!s}")


# ---------- Embed-Builder ----------


def _build_status_embed(results: list[PollResult]) -> discord.Embed:
    overall_status = "ok"
    for r in results:
        if r.status == "critical" or r.status == "unreachable":
            overall_status = "critical"
            break
        if r.status == "degraded" and overall_status == "ok":
            overall_status = "degraded"

    color = STATUS_COLOR.get(overall_status, 0x95A5A6)
    embed = discord.Embed(
        title=f"{STATUS_EMOJI.get(overall_status, '⚪')} ShadowOps Phase 5e — Health-Aggregator",
        description=f"Status: **{overall_status.upper()}** — pollt 3 Hosts alle {POLL_INTERVAL_SECONDS}s",
        color=color,
        timestamp=datetime.now(timezone.utc),
    )

    for r in results:
        emoji = STATUS_EMOJI.get(r.status, "⚪")
        if r.response is not None:
            host_line = f"`{r.response.host}` ({r.response.role})"
            uptime_line = f"Uptime: {_format_uptime(r.response.uptime_seconds)}"
            # Wichtigste Alerts als Klartext (Humanizer) statt blosser Zaehler
            alert_lines: list[str] = []
            for a in r.response.critical_alerts[:2]:
                alert_lines.append(f"🔴 {humanize_alert(a)}")
            for a in r.response.warning_alerts[:2]:
                alert_lines.append(f"🟡 {humanize_alert(a)}")
            if alert_lines:
                body = "\n".join(alert_lines)
            else:
                body = "Keine aktiven Alerts"
            value = f"{host_line}\n{uptime_line}\n{body}"
        else:
            value = f"❌ {r.error or 'unbekannter Fehler'}"

        field_name = f"{emoji} {r.target.name} — {r.status.upper()}"
        embed.add_field(name=field_name, value=value[:1024], inline=False)

    embed.set_footer(text=f"Aktualisiert alle {EMBED_INTERVAL_MINUTES} Min · Schema v1")
    return embed


def _format_uptime(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        h, m = divmod(seconds, 3600)
        return f"{h}h {m // 60}m"
    days, rem = divmod(seconds, 86400)
    h = rem // 3600
    return f"{days}d {h}h"


def _alert_components(response: Optional[HealthResponse]) -> list[str]:
    """Sammelt die Komponenten aller Alerts (fuer Runbook-Auswahl)."""
    if response is None:
        return []
    comps: list[str] = []
    for a in response.alerts:
        if a.component and a.component not in comps:
            comps.append(a.component)
    return comps


def _format_downtime(seconds: float) -> str:
    """Sekunden → kurzer deutscher Dauer-Klartext, z.B. '2 Min', '1 Std 5 Min'."""
    seconds = int(max(0, seconds))
    if seconds < 60:
        return f"{seconds} Sek"
    if seconds < 3600:
        return f"{seconds // 60} Min"
    if seconds < 86400:
        h, rem = divmod(seconds, 3600)
        m = rem // 60
        return f"{h} Std {m} Min" if m else f"{h} Std"
    days, rem = divmod(seconds, 86400)
    h = rem // 3600
    return f"{days} Tg {h} Std" if h else f"{days} Tg"


def _build_drift_embed(
    target: HealthTarget,
    prev: str,
    new: str,
    response: Optional[HealthResponse],
    error: Optional[str],
    *,
    timeline: Optional[list[tuple[int, str, str]]] = None,
    downtime_seconds: Optional[float] = None,
    self_recovered: bool = False,
) -> discord.Embed:
    """Embed für Drift-Detail-Alert (status-Wechsel) — mensch-lesbar via Humanizer.

    ``timeline`` (optional) listet bisherige Übergänge eines offenen Incidents
    als ``[(ts, prev, new), ...]`` und wird als Verlauf gerendert.
    ``downtime_seconds``/``self_recovered`` werden beim Recovery-Abschluss
    gesetzt (Gesamt-Downtime + Selbst-Erholung).
    """
    info = humanize_transition(prev, new)
    title = f"{info.emoji} {target.name} {info.headline}"
    color = STATUS_COLOR["ok"] if info.is_recovery else STATUS_COLOR.get(new, 0x95A5A6)

    # Beschreibung: Klartext-Kontext zur Lage
    if info.is_recovery:
        desc_parts = [f"{target.name} ({target.url}) ist wieder im grünen Bereich."]
        if downtime_seconds is not None:
            desc_parts.append(f"Gesamt-Ausfall: **{_format_downtime(downtime_seconds)}**.")
        if self_recovered:
            desc_parts.append("Selbst-erholt (keine manuelle Aktion erkannt).")
        description = " ".join(desc_parts)
    elif error:
        description = f"{target.name} ({target.url}) — {error[:300]}"
    else:
        description = f"{target.name} ({target.url})"

    embed = discord.Embed(
        title=title[:256],
        description=description[:4096],
        color=color,
        timestamp=datetime.now(timezone.utc),
    )

    if response is not None:
        if response.critical_alerts:
            lines = [f"🔴 {humanize_alert(a)}" for a in response.critical_alerts[:5]]
            embed.add_field(name="Kritisch", value="\n".join(lines)[:1024], inline=False)
        if response.warning_alerts:
            lines = [f"🟡 {humanize_alert(a)}" for a in response.warning_alerts[:5]]
            embed.add_field(name="Warnungen", value="\n".join(lines)[:1024], inline=False)
    elif error:
        embed.add_field(name="Fehler", value=f"```{error[:1000]}```", inline=False)

    # Dringlichkeit (leeren String abfangen — kein Feld bei Urgency.NONE)
    u_line = urgency_line(info.urgency)
    runbook = runbook_for(target.role_hint, _alert_components(response))
    action_lines: list[str] = []
    if u_line:
        action_lines.append(u_line)
    if runbook is not None and not info.is_recovery:
        action_lines.append(f"→ Runbook: {runbook}")
    if action_lines:
        embed.add_field(name="​", value="\n".join(action_lines)[:1024], inline=False)

    # Verlauf (Incident-Timeline)
    if timeline:
        tl_lines = []
        for ts, p, n in timeline[-6:]:
            tl_lines.append(f"{STATUS_EMOJI.get(n, '⚪')} {p} → {n} · <t:{ts}:R>")
        embed.add_field(name="Verlauf", value="\n".join(tl_lines)[:1024], inline=False)

    embed.set_footer(text="Phase 5e · Health-Aggregator")
    return embed


def _build_trend_embed(events: list[tuple[str, str, str, int]]) -> discord.Embed:
    """Trend-Report-Embed (Top-3 Drift-Events letzte 24h)."""
    embed = discord.Embed(
        title="📈 Phase 5e — Trend-Report (letzte 24h)",
        description=f"Erfasste Status-Wechsel: **{len(events)}**",
        color=0x3498DB,
        timestamp=datetime.now(timezone.utc),
    )
    if not events:
        embed.add_field(name="✅ Stabil", value="Keine Status-Wechsel in den letzten 24h", inline=False)
    else:
        # Top-3 nach "Schwere" der Transition: critical > degraded > ok
        severity_score = {"ok": 0, "degraded": 1, "critical": 2, "unreachable": 2}
        ranked = sorted(
            events,
            key=lambda e: severity_score.get(e[2], 0) + severity_score.get(e[1], 0),
            reverse=True,
        )[:3]
        lines = []
        for host, prev, new, ts in ranked:
            when = f"<t:{ts}:R>"
            info = humanize_transition(prev, new)
            lines.append(f"• {info.emoji} `{host}`: **{info.headline}** {when}")
        embed.add_field(name="Top-Events", value="\n".join(lines), inline=False)

        # Pro-Host-Statistik
        per_host: dict[str, int] = {}
        for e in events:
            per_host[e[0]] = per_host.get(e[0], 0) + 1
        if per_host:
            stats_lines = [f"• `{host}`: {count} Wechsel" for host, count in sorted(per_host.items(), key=lambda x: -x[1])]
            embed.add_field(name="Pro Host", value="\n".join(stats_lines), inline=False)

    embed.set_footer(text="Täglicher Trend-Report · 09:00")
    return embed


# ---------- Cog ----------


class Phase5eHealthAggregator(commands.Cog):
    """ShadowOps Phase 5e: Health-Aggregator nach einheitlichem Schema v1."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.logger = getattr(bot, "logger", logger)
        self._last_status_per_host: dict[str, str] = {}
        self._latest_results: list[PollResult] = []
        self._dashboard_message: Optional[discord.Message] = None
        # Incident-Grouping: offene Vorfälle pro Host (in-memory; Restart = leer)
        self._open_incidents: dict[str, OpenIncident] = {}
        _init_db()

    async def cog_load(self) -> None:
        self.poll_loop.start()
        self.embed_loop.start()
        self.trend_report_loop.start()
        self.purge_loop.start()
        self.logger.info("[5e] Phase-5e Health-Aggregator gestartet (3 Hosts, 60s Polling)")

    async def cog_unload(self) -> None:
        self.poll_loop.cancel()
        self.embed_loop.cancel()
        self.trend_report_loop.cancel()
        self.purge_loop.cancel()

    # ---- Polling ----

    @tasks.loop(seconds=POLL_INTERVAL_SECONDS)
    async def poll_loop(self) -> None:
        try:
            async with aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(ssl=True, limit_per_host=2),
            ) as session:
                results = await asyncio.gather(*[_poll_one(session, t) for t in TARGETS])

            self._latest_results = list(results)
            for r in results:
                _persist(r)

            await self._handle_drifts(results)
        except Exception:  # noqa: BLE001
            self.logger.exception("[5e] poll_loop unerwarteter Fehler")

    @poll_loop.before_loop
    async def _before_poll(self) -> None:
        await self.bot.wait_until_ready()

    _STATUS_SEVERITY = {"ok": 0, "degraded": 1, "critical": 2, "unreachable": 3}

    async def _handle_drifts(self, results: list[PollResult]) -> None:
        critical_channel = self.bot.get_channel(CRITICAL_CHANNEL_ID)
        for r in results:
            host = r.target.name
            current = r.status
            previous = self._last_status_per_host.get(host)
            self._last_status_per_host[host] = current

            if previous is None:
                # erster Run, nur lernen, kein Alert
                continue
            if previous == current:
                continue

            self.logger.info("[5e] DRIFT %s: %s → %s", host, previous, current)
            if critical_channel is None:
                continue

            await self._handle_incident_drift(critical_channel, r, previous, current)

    async def _handle_incident_drift(
        self, channel, r: PollResult, previous: str, current: str
    ) -> None:
        """Bündelt aufeinanderfolgende Drifts eines Hosts zu einem Incident.

        - Erster Drift weg von ``ok`` → Incident öffnen, Message posten.
        - Weitere Drifts (Incident offen) → bestehende Message editieren, Timeline anhängen.
        - Drift zurück auf ``ok`` → Recovery, finale Edit mit Downtime, Incident schließen.
        """
        host = r.target.name
        now = datetime.now(timezone.utc)
        ts = int(now.timestamp())
        incident = self._open_incidents.get(host)

        if current == "ok":
            # Recovery — schließt einen offenen Incident ab (falls vorhanden)
            if incident is None:
                # kein offener Incident (z.B. Bot-Restart) → einfacher Recovery-Post
                embed = _build_drift_embed(r.target, previous, current, r.response, r.error)
                await self._post_or_edit_incident(channel, None, embed)
                return
            incident.transitions.append((ts, previous, current))
            downtime = (now - incident.started_at).total_seconds()
            embed = _build_drift_embed(
                r.target, previous, current, r.response, r.error,
                timeline=incident.transitions,
                downtime_seconds=downtime,
                self_recovered=not incident.manual_action_seen,
            )
            await self._post_or_edit_incident(channel, incident, embed)
            del self._open_incidents[host]
            return

        # Drift in einen Nicht-ok-Status
        if incident is None:
            # Neuer Incident
            incident = OpenIncident(
                host=host,
                started_at=now,
                message_id=None,
                transitions=[(ts, previous, current)],
                worst_status=current,
            )
            self._open_incidents[host] = incident
        else:
            incident.transitions.append((ts, previous, current))
            if self._STATUS_SEVERITY.get(current, 0) > self._STATUS_SEVERITY.get(incident.worst_status, 0):
                incident.worst_status = current

        embed = _build_drift_embed(
            r.target, previous, current, r.response, r.error,
            timeline=incident.transitions,
        )
        await self._post_or_edit_incident(channel, incident, embed)

    async def _post_or_edit_incident(self, channel, incident: Optional[OpenIncident], embed: discord.Embed) -> None:
        """Postet eine neue Incident-Message oder editiert die bestehende.

        Defensive: schlägt ``message.edit`` fehl (Message gelöscht/404), wird neu
        gepostet und die ``message_id`` aktualisiert.
        """
        # Bestehende Message editieren wenn möglich
        if incident is not None and incident.message_id is not None:
            try:
                msg = channel.get_partial_message(incident.message_id)
                await msg.edit(embed=embed)
                return
            except discord.HTTPException as exc:
                self.logger.warning("[5e] incident-edit fehlgeschlagen (%s) → neu posten", exc)
                incident.message_id = None

        # Neu posten
        try:
            new_msg = await channel.send(embed=embed)
        except discord.HTTPException as exc:
            self.logger.error("[5e] incident-embed senden fehlgeschlagen: %s", exc)
            return
        if incident is not None and new_msg is not None:
            incident.message_id = getattr(new_msg, "id", None)

    # ---- 5-Min-Status-Embed ----

    @tasks.loop(minutes=EMBED_INTERVAL_MINUTES)
    async def embed_loop(self) -> None:
        try:
            channel = self.bot.get_channel(DASHBOARD_CHANNEL_ID)
            if channel is None:
                return
            if not self._latest_results:
                # noch nie gepollt — beim ersten Loop mal kurz warten
                return

            embed = _build_status_embed(self._latest_results)

            # Editiere bestehendes Bot-Embed wenn moeglich, sonst sende neues
            if self._dashboard_message is None:
                # 1. Pinned messages durchsuchen (discord.py 2.6+: pins() ist AsyncIterator)
                try:
                    async for pin in channel.pins():
                        if (
                            pin.author.id == self.bot.user.id
                            and pin.embeds
                            and pin.embeds[0].title
                            and "Phase 5e" in pin.embeds[0].title
                        ):
                            self._dashboard_message = pin
                            break
                except (discord.HTTPException, discord.Forbidden) as exc:
                    self.logger.debug("[5e] embed_loop: pins() nicht verfuegbar: %s", exc)
                # 2. Letzte 20 Bot-Nachrichten durchsuchen
                if self._dashboard_message is None:
                    try:
                        async for msg in channel.history(limit=20):
                            if (
                                msg.author.id == self.bot.user.id
                                and msg.embeds
                                and msg.embeds[0].title
                                and "Phase 5e" in msg.embeds[0].title
                            ):
                                self._dashboard_message = msg
                                break
                    except (discord.HTTPException, discord.Forbidden) as exc:
                        self.logger.debug("[5e] embed_loop: history() nicht verfuegbar: %s", exc)

            if self._dashboard_message is not None:
                try:
                    await self._dashboard_message.edit(embed=embed)
                    return
                except discord.HTTPException:
                    self._dashboard_message = None  # neu erstellen

            new_msg = await channel.send(embed=embed)
            self._dashboard_message = new_msg
            with suppress(discord.Forbidden, discord.HTTPException):
                await new_msg.pin()
        except Exception:  # noqa: BLE001
            self.logger.exception("[5e] embed_loop unerwarteter Fehler")

    @embed_loop.before_loop
    async def _before_embed(self) -> None:
        await self.bot.wait_until_ready()
        # Warte bis erster Poll-Cycle durch ist
        await asyncio.sleep(POLL_INTERVAL_SECONDS + 5)

    # ---- Trend-Report ----

    @tasks.loop(time=dtime(hour=TREND_REPORT_HOUR, minute=0))
    async def trend_report_loop(self) -> None:
        try:
            channel = self.bot.get_channel(DASHBOARD_CHANNEL_ID)
            if channel is None:
                return
            events = _query_drift_events_24h()
            embed = _build_trend_embed(events)
            await channel.send(embed=embed)
        except Exception:  # noqa: BLE001
            self.logger.exception("[5e] trend_report_loop unerwarteter Fehler")

    @trend_report_loop.before_loop
    async def _before_trend(self) -> None:
        await self.bot.wait_until_ready()

    # ---- Retention ----

    @tasks.loop(hours=24)
    async def purge_loop(self) -> None:
        try:
            deleted = _purge_old()
            if deleted > 0:
                self.logger.info("[5e] %d alte health_history-Eintraege geloescht (>%dd)", deleted, RETENTION_DAYS)
        except Exception:  # noqa: BLE001
            self.logger.exception("[5e] purge_loop unerwarteter Fehler")

    @purge_loop.before_loop
    async def _before_purge(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Phase5eHealthAggregator(bot))
