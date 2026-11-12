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
        env = {**os.environ, "SSH_ORIGINAL_COMMAND": befehl, "NOTFALL_WRAPPER_LOG": str(log),
               "NOTFALL_BUDGET_DATEI": str(Path(tmp) / "budget")}
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
    "platz-schaffen volumes",             # keine Argumente, nie Volumes
    "dienst-neustart shadowops-bot",      # Neustart im Deploy → Rollback mit rsync --delete
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


def test_inodes_meldet_distroless_als_nicht_messbar(tmp_path):
    """Ein Image ohne ls (wollomatic/socket-proxy) darf keinen Fehlertext wie
    "OCI" als Inode ausgeben, sonst hält der Agent den Socket für tot."""
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "docker").write_text(
        '#!/bin/bash\n'
        'case "$1" in\n'
        '  ps) printf "proxy-mit-ls\\nproxy-distroless\\n" ;;\n'
        '  inspect) echo x ;;\n'
        '  exec) [ "$2" = proxy-mit-ls ] && echo "4711 /var/run/docker.sock" && exit 0\n'
        '        echo "OCI runtime exec failed: exec: \\"ls\\": not found" >&2; exit 126 ;;\n'
        'esac\n'
    )
    (bin_ / "sg").write_text('#!/bin/bash\nexec bash -c "$3"\n')
    for f in bin_.iterdir():
        f.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}",
           "SSH_ORIGINAL_COMMAND": "inodes", "NOTFALL_WRAPPER_LOG": str(tmp_path / "w.log")}
    r = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=30)
    assert "proxy-mit-ls 4711" in r.stdout
    assert "proxy-distroless nicht-messbar" in r.stdout
    assert "OCI" not in r.stdout



# ── Härtung nach Sicherheitsreview (ZERODOX #5627) ─────────────────────────

DOCKER_STUB = """#!/bin/bash
echo "docker $*" >> "$FAKE_LOG"
case "$1" in
  inspect) echo "/${@: -1}" ;;
  logs) echo "Login max.mustermann@example.de von 203.0.113.7:443 an 10.8.0.1, Version 1.2.3" ;;
esac
"""
JOURNAL_STUB = '#!/bin/bash\necho "Mail an kunde+x@zerodox.de, Peer 192.168.1.20 ok"\n'
SYSTEMCTL_STUB = '#!/bin/bash\necho "systemctl $*" >> "$FAKE_LOG"\n[ "$2" = is-active ] && echo active\nexit 0\n'


def _stub_umgebung(tmp_path, befehl, **extra):
    bin_ = tmp_path / "bin"
    bin_.mkdir(exist_ok=True)
    for name, inhalt in (("docker", DOCKER_STUB), ("journalctl", JOURNAL_STUB),
                         ("systemctl", SYSTEMCTL_STUB),
                         ("sg", '#!/bin/bash\nexec bash -c "$3"\n')):
        (bin_ / name).write_text(inhalt)
        (bin_ / name).chmod(0o755)
    return {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}",
            "SSH_ORIGINAL_COMMAND": befehl, "NOTFALL_WRAPPER_LOG": str(tmp_path / "w.log"),
            "NOTFALL_BUDGET_DATEI": str(tmp_path / "budget"), "FAKE_LOG": str(tmp_path / "fake.log"),
            **extra}


def _wrapper(env):
    return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=30)


def test_aktionsbudget_vier_je_fenster(tmp_path):
    """Befund 4: Höchstens 4 HANDELN-Befehle je 30 min — im Wrapper erzwungen, nicht im Prompt."""
    for i in range(4):
        r = _wrapper(_stub_umgebung(tmp_path, "restart zerodox-web" if i % 2 else "dienst-neustart seo-agent"))
        assert r.returncode == 0, (i, r.stderr)
    r = _wrapper(_stub_umgebung(tmp_path, "start zerodox-web"))
    assert r.returncode == 64 and "ABGELEHNT: Aktionsbudget" in r.stderr
    log = (tmp_path / "w.log").read_text()
    assert log.count(" AKTION ") == 4 and log.splitlines()[-1].split()[1] == "ABGELEHNT"
    # die abgelehnte Aktion wurde nicht ausgeführt
    assert (tmp_path / "fake.log").read_text().count("docker start") == 0
    # Lesen bleibt erlaubt
    assert _wrapper(_stub_umgebung(tmp_path, "status")).returncode == 0


def test_aktionsbudget_fenster_laeuft_ab(tmp_path):
    import time
    alt = int(time.time()) - 1900
    (tmp_path / "budget").write_text("\n".join([str(alt)] * 4) + "\nkaputt\n")
    r = _wrapper(_stub_umgebung(tmp_path, "restart zerodox-web"))
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "budget").read_text().split() == [(tmp_path / "budget").read_text().split()[-1]]


def test_aktionsbudget_override_per_env(tmp_path):
    env = _stub_umgebung(tmp_path, "restart zerodox-web", NOTFALL_BUDGET_MAX="1")
    assert _wrapper(env).returncode == 0
    assert _wrapper(env).returncode == 64


def test_logs_werden_geschwaerzt(tmp_path):
    """Befund 6: E-Mail- und IPv4-Adressen verlassen den Hauptserver nicht."""
    r = _wrapper(_stub_umgebung(tmp_path, "logs zerodox-web 10"))
    assert r.returncode == 0, r.stderr
    assert "example.de" not in r.stdout and "203.0.113.7" not in r.stdout and "10.8.0.1" not in r.stdout
    assert "[E-Mail]" in r.stdout and "[IP]:443" in r.stdout and "Version 1.2.3" in r.stdout


def test_journal_wird_geschwaerzt_und_shadowops_bot_lesbar(tmp_path):
    r = _wrapper(_stub_umgebung(tmp_path, "journal shadowops-bot"))
    assert r.returncode == 0, r.stderr
    assert "zerodox.de" not in r.stdout and "192.168.1.20" not in r.stdout
    assert "[E-Mail]" in r.stdout and "[IP]" in r.stdout
