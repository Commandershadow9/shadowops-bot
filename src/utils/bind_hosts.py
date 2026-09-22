"""
Bind-Hosts für interne HTTP-Server (Health-Check, GuildScout-Webhook).

Hintergrund (ZERODOX#3212): Beide Server banden bisher auf `0.0.0.0`, obwohl
der einzige dokumentierte Grund die Docker-Bridge `172.17.0.1` war (Docker-
Container erreichen den Host darüber, nicht über `127.0.0.1`). `0.0.0.0`
bindet zusätzlich auf JEDE weitere Netzwerkschnittstelle — UFW begrenzt das
zwar, aber die Bindung selbst war unnötig weit.

`bind_hosts()` liefert die engstmögliche Liste: immer `127.0.0.1`, dazu
`172.17.0.1` NUR wenn die Adresse auf diesem Host tatsächlich existiert
(Docker-Bridge). Fail-safe: Fehlt docker0 (z. B. lokale Entwicklung ohne
Docker), startet der Server trotzdem — nur eben ohne die Docker-Bindung,
mit einer Warnung im Log. Ein nicht startender Bot wäre schlimmer als eine
fehlende Docker-Erreichbarkeit.
"""

from __future__ import annotations

import logging
import os
import socket

logger = logging.getLogger("shadowops.bind_hosts")

LOCALHOST = "127.0.0.1"
DOCKER_BRIDGE_HOST = "172.17.0.1"
ENV_OVERRIDE = "SHADOWOPS_BIND_HOSTS"


def _adresse_verfuegbar(host: str) -> bool:
    """Prüft per Test-Bind, ob eine lokale Adresse auf diesem Host existiert."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((host, 0))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def bind_hosts() -> list[str]:
    """
    Liefert die Liste der Hosts, auf die interne Server binden sollen.

    Reihenfolge der Ermittlung:
    1. ENV-Override `SHADOWOPS_BIND_HOSTS` (kommagetrennt) — wenn gesetzt,
       gilt NUR diese Liste (kein automatisches Hinzufügen/Entfernen).
    2. Sonst: `127.0.0.1` immer, `172.17.0.1` nur wenn lokal bindbar.

    Liefert NIEMALS `0.0.0.0` — das wäre wieder die zu weite Bindung, die
    dieser Umbau beheben soll.
    """
    override = os.environ.get(ENV_OVERRIDE, "").strip()
    if override:
        hosts = [h.strip() for h in override.split(",") if h.strip()]
        if hosts:
            return hosts

    hosts = [LOCALHOST]
    if _adresse_verfuegbar(DOCKER_BRIDGE_HOST):
        hosts.append(DOCKER_BRIDGE_HOST)
    else:
        logger.warning(
            "Docker-Bridge %s nicht verfügbar — Server bindet nur auf %s. "
            "Docker-Container erreichen den Host damit nicht.",
            DOCKER_BRIDGE_HOST,
            LOCALHOST,
        )

    return hosts
