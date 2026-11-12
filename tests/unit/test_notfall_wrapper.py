"""
Tests fuer notfall-wrapper.sh — die einzige Tuer des Notfall-Agenten von HEL1
in den Hauptserver (ZERODOX #5627, Stufe 2).

Der Wrapper ist die eigentliche Sicherheitsgrenze: Er haengt weder am Prompt
noch an --allowedTools des Agenten. Deshalb wird hier vor allem geprueft, was
er ABLEHNT — und dass jede Ablehnung protokolliert wird.
"""
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "notfall" / "notfall-wrapper.sh"


def _ruf(befehl: str):
    with tempfile.TemporaryDirectory() as tmp:
        log = Path(tmp) / "w.log"
        env = {**os.environ, "SSH_ORIGINAL_COMMAND": befehl, "NOTFALL_WRAPPER_LOG": str(log)}
        r = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=30)
        return r, (log.read_text() if log.exists() else "")


@pytest.mark.parametrize("befehl", [
    "rm -rf /",
    "status; rm -rf ~",
    "status && reboot",
    "status | sh",
    "logs $(id) 10",
    "logs `id` 10",
    "restart zerodox-web > /etc/passwd",
    "compose-up zerodox",                 # bewusst nicht freigegeben (DB-Neuerzeugung)
    "compose-up ../../etc",
    "restart zerodox-db",                 # Datenbanken nie
    "restart guildscout-postgres",
    "restart guildscout-redis",
    "dienst-neustart sshd",
    "dienst-neustart docker",
    "journal ssh",
    "logs zerodox-web 5000",              # Zeilenlimit
    "docker rm -f zerodox-web",
    "apt upgrade -y",
    "ufw disable",
    "logs ../x 10",
    "restart A B C D",
    "status\nrestart zerodox-db",        # Steuerzeichen / Log-Injection
    "restart 0",                          # ID-Präfix statt Name
])
def test_gefaehrliches_wird_abgelehnt_und_protokolliert(befehl):
    r, log = _ruf(befehl)
    assert r.returncode == 64, (befehl, r.stdout, r.stderr)
    assert "ABGELEHNT" in log, "auch Ablehnungen muessen im Protokoll stehen"


def test_hilfe_listet_die_positivliste():
    r, log = _ruf("hilfe")
    assert r.returncode == 0
    assert "HANDELN" in r.stdout and "Nicht erlaubt" in r.stdout
    assert " OK " in log


def test_leerer_befehl_zeigt_hilfe():
    r, _ = _ruf("")
    assert r.returncode == 0 and "LESEN" in r.stdout


STARTER = Path(__file__).resolve().parents[2] / "scripts" / "notfall" / "hel1" / "notfall-ssh"


@pytest.mark.parametrize("args", [
    ["-oProxyCommand=sh -c id", "status"],
    ["status", "-oProxyCommand=id"],          # GNU-getopt parst Optionen auch nach dem Host
    ["status", "-F/tmp/x"],
    ["status;id"],
    ["a", "b", "c", "d"],
])
def test_starter_laesst_keine_ssh_optionen_durch(args):
    r = subprocess.run(["bash", str(STARTER), *args], capture_output=True, text=True, timeout=10)
    assert r.returncode == 64, (args, r.stderr)
