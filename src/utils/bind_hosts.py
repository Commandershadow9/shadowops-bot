"""
Bind-Hosts für interne HTTP-Server (Health-Check, GuildScout-Webhook).

Hintergrund (ZERODOX#3212): Beide Server banden bisher auf `0.0.0.0`. Der
ursprünglich angenommene Grund — "Docker-Container erreichen den Host nur
über 172.17.0.1" — stimmt für die echten Konsumenten NICHT: `zerodox-web`
ruft den Changelog-Endpunkt über `http://172.20.0.1:8766` (Gateway des
Compose-Netzes `zerodox-internal`, eine der vielen `br-*`-Bridges), nicht
über `docker0`. Der Host trägt zum Zeitpunkt dieses Umbaus 15 Docker-Bridges
(`docker0` + 14 `br-*`), UFW erlaubt für Port 8766 die Netze `172.18.0.0/16`
und `172.20.0.0/16`. Eine feste Adresse `172.17.0.1` hätte den Changelog auf
zerodox.de lahmgelegt (der Vorfall vom 12.09.2026) — `0.0.0.0` war insofern
zu weit, eine einzelne feste Bridge-Adresse zu eng.

`bind_hosts()` liefert deshalb `127.0.0.1` plus die IPv4-Adressen ALLER
lokalen Docker-Bridges (Interface-Name `docker0` oder Präfix `br-`) — das
deckt jedes Compose-Netz ab, unabhängig davon, welches Gateway ein
Konsument gerade benutzt, ohne auf `0.0.0.0` zurückzufallen. Ermittelt wird
das stdlib-only über `ip -4 -o addr show` (kein psutil im venv). Fail-safe:
Scheitert `ip` (fehlt, kein PATH-Zugriff, unerwartetes Format), startet der
Server trotzdem — nur mit `127.0.0.1`, dazu eine Warnung im Log. Ein nicht
startender Bot wäre schlimmer als eine fehlende Docker-Bindung.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess

logger = logging.getLogger("shadowops.bind_hosts")

LOCALHOST = "127.0.0.1"
ENV_OVERRIDE = "SHADOWOPS_BIND_HOSTS"

# Docker-Bridge-Interfaces: die Standard-Bridge heisst immer "docker0",
# jedes Compose-Netz mit eigener Bridge "br-<12-stellige-Netz-ID>".
_BRIDGE_IFACE_PATTERN = re.compile(r"^(docker0|br-[0-9a-f]+)$")

# Zeilenformat von `ip -4 -o addr show`, z. B.:
#   "3: docker0    inet 172.17.0.1/16 brd 172.17.255.255 scope global docker0\ ..."
_IP_ADDR_LINE_PATTERN = re.compile(
    r"^\d+:\s*(?P<iface>\S+)\s+inet\s+(?P<addr>\d{1,3}(?:\.\d{1,3}){3})/\d+"
)


def _docker_bridge_adressen() -> list[str]:
    """
    Liefert die IPv4-Adressen aller lokalen Docker-Bridges (docker0 + br-*).

    Leere Liste bei jedem Fehler (Kommando fehlt, Timeout, unerwartetes
    Format) — der Aufrufer entscheidet dann über den Fallback.
    """
    try:
        ergebnis = subprocess.run(
            ["ip", "-4", "-o", "addr", "show"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return []

    adressen: list[str] = []
    for zeile in ergebnis.stdout.splitlines():
        treffer = _IP_ADDR_LINE_PATTERN.match(zeile.strip())
        if not treffer:
            continue
        if not _BRIDGE_IFACE_PATTERN.match(treffer.group("iface")):
            continue
        adressen.append(treffer.group("addr"))

    return adressen


def bind_hosts() -> list[str]:
    """
    Liefert die Liste der Hosts, auf die interne Server binden sollen.

    Reihenfolge der Ermittlung:
    1. ENV-Override `SHADOWOPS_BIND_HOSTS` (kommagetrennt) — wenn gesetzt,
       gilt NUR diese Liste (kein automatisches Hinzufügen/Entfernen).
    2. Sonst: `127.0.0.1` immer, dazu die Adressen aller gefundenen
       Docker-Bridges (`docker0`, `br-*`). Findet `ip` keine oder scheitert
       es, bleibt nur `127.0.0.1` — mit Warnung im Log.

    Liefert NIEMALS `0.0.0.0` — das wäre wieder die zu weite Bindung, die
    dieser Umbau beheben soll.
    """
    override = os.environ.get(ENV_OVERRIDE, "").strip()
    if override:
        hosts = [h.strip() for h in override.split(",") if h.strip()]
        if hosts:
            return hosts

    hosts = [LOCALHOST]
    bridge_adressen = _docker_bridge_adressen()
    if bridge_adressen:
        hosts.extend(bridge_adressen)
    else:
        logger.warning(
            "Keine Docker-Bridge gefunden (docker0/br-*) — Server bindet nur "
            "auf %s. Docker-Container erreichen den Host damit nicht.",
            LOCALHOST,
        )

    return hosts
