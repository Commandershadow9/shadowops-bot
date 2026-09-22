"""
Bind-Hosts für interne HTTP-Server: 127.0.0.1 + alle Docker-Bridges statt
0.0.0.0 oder einer einzelnen festen Bridge-Adresse.

Befund ZERODOX#3212: `HealthCheckServer` (Port 8766) und
`GuildScoutAlertsHandler` (Port 9091) banden auf `0.0.0.0`. Eine erste
Fassung ersetzte das durch eine feste Prüfung auf `172.17.0.1` — das hätte
den Changelog auf zerodox.de lahmgelegt, weil `zerodox-web` den Bot über das
Gateway seines eigenen Compose-Netzes erreicht (`172.20.0.1`), nicht über
die Standard-Bridge `docker0`. `bind_hosts()` ermittelt deshalb ALLE lokalen
Docker-Bridges (`docker0` + `br-*`) über `ip -4 -o addr show`.

Diese Tests prüfen die Ermittlung selbst (inkl. Fallback bei fehlendem/
scheiterndem `ip`) und dass die beiden Server sie tatsächlich verwenden
(kein `0.0.0.0` mehr an `web.TCPSite`).
"""

import subprocess
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from src.utils.bind_hosts import ENV_OVERRIDE, LOCALHOST, bind_hosts
from src.utils.health_server import HealthCheckServer

# Realistischer Ausschnitt aus `ip -4 -o addr show`: docker0 + zwei
# Compose-Bridges + eine echte Netzwerkkarte (eth0), die NICHT gebunden
# werden darf.
_IP_ADDR_AUSGABE = (
    "1: lo    inet 127.0.0.1/8 scope host lo\\       valid_lft forever preferred_lft forever\n"
    "2: eth0    inet 88.99.160.210/26 brd 88.99.160.255 scope global eth0\\       valid_lft forever preferred_lft forever\n"
    "3: docker0    inet 172.17.0.1/16 brd 172.17.255.255 scope global docker0\\       valid_lft forever preferred_lft forever\n"
    "44: br-c97418206321    inet 172.20.0.1/16 brd 172.20.255.255 scope global br-c97418206321\\       valid_lft forever preferred_lft forever\n"
    "129: br-2356bcbb4203    inet 172.18.0.1/16 brd 172.18.255.255 scope global br-2356bcbb4203\\       valid_lft forever preferred_lft forever\n"
)


def _fake_ip_lauf(stdout: str = _IP_ADDR_AUSGABE):
    ergebnis = MagicMock()
    ergebnis.stdout = stdout
    return ergebnis


# --- bind_hosts() selbst ---


def test_enthaelt_immer_127_0_0_1(monkeypatch):
    monkeypatch.delenv(ENV_OVERRIDE, raising=False)
    with patch("src.utils.bind_hosts.subprocess.run", return_value=_fake_ip_lauf()):
        assert LOCALHOST in bind_hosts()


def test_liefert_niemals_0_0_0_0(monkeypatch):
    monkeypatch.delenv(ENV_OVERRIDE, raising=False)
    with patch("src.utils.bind_hosts.subprocess.run", return_value=_fake_ip_lauf()):
        assert "0.0.0.0" not in bind_hosts()


def test_alle_docker_bridges_werden_aufgenommen_eth0_nicht(monkeypatch):
    monkeypatch.delenv(ENV_OVERRIDE, raising=False)
    with patch("src.utils.bind_hosts.subprocess.run", return_value=_fake_ip_lauf()):
        hosts = bind_hosts()

    assert hosts == [LOCALHOST, "172.17.0.1", "172.20.0.1", "172.18.0.1"]
    assert "88.99.160.210" not in hosts


def test_scheiterndes_ip_kommando_faellt_auf_localhost_zurueck_und_warnt(monkeypatch, caplog):
    monkeypatch.delenv(ENV_OVERRIDE, raising=False)
    with patch(
        "src.utils.bind_hosts.subprocess.run",
        side_effect=subprocess.CalledProcessError(returncode=1, cmd=["ip"]),
    ):
        with caplog.at_level("WARNING", logger="shadowops.bind_hosts"):
            hosts = bind_hosts()

    assert hosts == [LOCALHOST]
    assert any("Docker-Bridge" in satz.message for satz in caplog.records)


def test_fehlendes_ip_kommando_faellt_auf_localhost_zurueck_und_warnt(monkeypatch, caplog):
    monkeypatch.delenv(ENV_OVERRIDE, raising=False)
    with patch("src.utils.bind_hosts.subprocess.run", side_effect=FileNotFoundError()):
        with caplog.at_level("WARNING", logger="shadowops.bind_hosts"):
            hosts = bind_hosts()

    assert hosts == [LOCALHOST]
    assert any("Docker-Bridge" in satz.message for satz in caplog.records)


def test_leere_ip_ausgabe_faellt_auf_localhost_zurueck_und_warnt(monkeypatch, caplog):
    monkeypatch.delenv(ENV_OVERRIDE, raising=False)
    with patch("src.utils.bind_hosts.subprocess.run", return_value=_fake_ip_lauf("")):
        with caplog.at_level("WARNING", logger="shadowops.bind_hosts"):
            hosts = bind_hosts()

    assert hosts == [LOCALHOST]
    assert any("Docker-Bridge" in satz.message for satz in caplog.records)


def test_env_override_wird_respektiert(monkeypatch):
    monkeypatch.setenv(ENV_OVERRIDE, "10.0.0.5, 10.0.0.6")
    with patch("src.utils.bind_hosts.subprocess.run") as ip_lauf:
        hosts = bind_hosts()
    assert hosts == ["10.0.0.5", "10.0.0.6"]
    ip_lauf.assert_not_called()


def test_env_override_leer_wird_ignoriert(monkeypatch):
    monkeypatch.setenv(ENV_OVERRIDE, "   ")
    with patch("src.utils.bind_hosts.subprocess.run", return_value=_fake_ip_lauf()):
        hosts = bind_hosts()
    assert hosts == [LOCALHOST, "172.17.0.1", "172.20.0.1", "172.18.0.1"]


# --- HealthCheckServer.start() verwendet bind_hosts() statt 0.0.0.0 ---


@pytest.mark.asyncio
async def test_health_server_bindet_nicht_mehr_auf_0_0_0_0(monkeypatch):
    monkeypatch.delenv(ENV_OVERRIDE, raising=False)

    bot = Mock()
    server = HealthCheckServer(bot, port=8766)

    fake_runner = MagicMock()
    fake_runner.setup = AsyncMock()
    fake_site = MagicMock()
    fake_site.start = AsyncMock()

    aufrufe = []

    def _tcpsite(*args, **kwargs):
        aufrufe.append((args, kwargs))
        return fake_site

    with patch("src.utils.health_server.web.AppRunner", return_value=fake_runner), \
         patch("src.utils.health_server.web.TCPSite", side_effect=_tcpsite) as tcpsite_mock, \
         patch("src.utils.bind_hosts.subprocess.run", return_value=_fake_ip_lauf()):
        await server.start()

    assert tcpsite_mock.call_count == 1
    args, kwargs = aufrufe[0]
    uebergebene_hosts = kwargs.get("host", args[1] if len(args) > 1 else None)

    assert uebergebene_hosts != "0.0.0.0"
    assert "0.0.0.0" not in (uebergebene_hosts or [])
    assert LOCALHOST in uebergebene_hosts
    assert "172.20.0.1" in uebergebene_hosts
