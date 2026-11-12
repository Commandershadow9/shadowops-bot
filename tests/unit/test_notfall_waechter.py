"""
Tests fuer den Zustandsautomaten von notfall-waechter.sh (ZERODOX #5627, Stufen 2+3).

curl, ping und ssh werden durch Stubs ersetzt; jeder Test faehrt EINEN Lauf
(NOTFALL_EINMAL=1) gegen einen vorbereiteten Zustand. Am wichtigsten ist der
Reset-Pfad: Ein Hardware-Reset des Hauptservers darf nur nach Alarm + Frist +
fehlender Quittung kommen, hoechstens einmal je Vorfall, und nie, wenn HEL1
selbst blind ist.
"""
import os
import subprocess
import tempfile
import time
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "notfall" / "hel1" / "notfall-waechter.sh"

CURL = r"""#!/usr/bin/env bash
echo "curl $*" >> "$FAKE_LOG"
url="${@: -1}"
case "$url" in
  *robot-ws*) echo 200; exit 0 ;;
  *-quittung/json*) [ "${FAKE_QUITTUNG:-0}" = 1 ] && echo '{"event":"message"}'; exit 0 ;;
  *ntfy.sh/v1/health*|*hetzner.com*) [ "${FAKE_BLIND:-0}" = 1 ] && exit 7; exit 0 ;;
  *ntfy.sh/*) exit 0 ;;
esac
for a in "$@"; do [ "$a" = "%{http_code}" ] && { echo "${FAKE_HTTP:-200}"; exit 0; }; done
exit 0
"""
PING = '#!/usr/bin/env bash\n[ "${FAKE_PING:-1}" = 1 ]\n'
SSH = '#!/usr/bin/env bash\necho "ssh $*" >> "$FAKE_LOG"\n[ "${FAKE_GEHEILT:-0}" = 1 ] && echo \'watchdog_state_zerodox.json: {"last_heal_at":"2026-11-12T05:00:00Z"}\'\nexit 0\n'
AGENT = '#!/usr/bin/env bash\necho "AGENT" >> "$FAKE_LOG"\necho "ERGEBNIS: GEHEILT"\n'


def _lauf(state: dict, **fake):
    tmp = Path(tempfile.mkdtemp(prefix="waechter-"))
    (tmp / "bin").mkdir()
    for name, inhalt in (("curl", CURL), ("ping", PING), ("ssh", SSH)):
        f = tmp / "bin" / name
        f.write_text(inhalt)
        f.chmod(0o755)
    basis = tmp / "basis"
    basis.mkdir()
    (basis / "notfall-agent.sh").write_text(AGENT)
    (basis / "notfall-agent.sh").chmod(0o755)
    (basis / "notfall-ssh").write_text(SSH)
    (basis / "notfall-ssh").chmod(0o755)
    st = tmp / "state"
    st.mkdir()
    for k, v in state.items():
        (st / k).write_text(str(v))
    log = tmp / "log"
    log.write_text("")
    env = {
        **os.environ,
        "PATH": f"{tmp / 'bin'}:{os.environ['PATH']}",
        "NOTFALL_STATE": str(st), "NOTFALL_BASIS": str(basis), "NOTFALL_EINMAL": "1",
        "NTFY_TOPIC": "test-topic", "DISCORD_WEBHOOK": "",
        "ROBOT_USER": "u", "ROBOT_PASS": "p", "ROBOT_SERVER_NUMBER": "123",
        "FAKE_LOG": str(log),
        **{f"FAKE_{k.upper()}": str(v) for k, v in fake.items()},
    }
    r = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=30)
    time.sleep(0.3)  # Agent laeuft im Hintergrund
    datei = lambda n: (st / n).read_text().strip() if (st / n).exists() else ""  # noqa: E731
    # Der Agent laeuft mit `env -i` (erbt keine Geheimnisse) — sein Start ist deshalb
    # am Zustand und an der abgelegten Berichtsdatei messbar, nicht am FAKE_LOG.
    agent = bool(datei("agent_gestartet")) and "agent_gestartet" not in state
    return r, log.read_text() + ("AGENT\n" if agent else ""), datei


def vor(s):
    return int(time.time()) - s


def test_alles_gruen_tut_nichts():
    _, log, d = _lauf({})
    assert d("zustand") == "OK" and "robot" not in log and "AGENT" not in log


def test_totalausfall_alarmiert_erst_nach_10_min_mit_knopf():
    _, log, d = _lauf({"vorfall_seit": vor(300)}, http="000", ping=0)
    assert d("zustand") == "TOT" and not d("tot_alarm")
    _, log, d = _lauf({"vorfall_seit": vor(620)}, http="000", ping=0)
    assert d("tot_alarm"), log
    assert "Actions: http, Ich übernehme" in log


def test_reset_erst_nach_frist_ohne_quittung():
    _, log, d = _lauf({"vorfall_seit": vor(900), "tot_alarm": vor(300)}, http="000", ping=0)
    assert "robot-ws" not in log, "vor Ablauf der Frist kein Reset"
    _, log, d = _lauf({"vorfall_seit": vor(1300), "tot_alarm": vor(620)}, http="000", ping=0)
    assert "robot-ws.your-server.de/reset/123" in log and d("reset_ausgeloest")


def test_quittung_verhindert_reset():
    _, log, d = _lauf({"vorfall_seit": vor(1300), "tot_alarm": vor(620)}, http="000", ping=0, quittung=1)
    assert "robot-ws" not in log and d("reset_ausgeloest")


def test_reset_hoechstens_einmal_je_vorfall():
    _, log, _ = _lauf({"vorfall_seit": vor(3000), "tot_alarm": vor(2000), "reset_ausgeloest": vor(1000)},
                      http="000", ping=0)
    assert "robot-ws" not in log


def test_blind_heisst_nichts_tun():
    """HEL1 ohne Internet darf den Hauptserver nie fuer tot erklaeren und resetten."""
    _, log, d = _lauf({"vorfall_seit": vor(1300), "tot_alarm": vor(620)}, http="000", ping=0, blind=1)
    assert "robot-ws" not in log and not d("reset_ausgeloest")


def test_ping_ok_ist_nie_tot_sondern_teil():
    _, log, d = _lauf({"vorfall_seit": vor(5000)}, http="404", ping=1)
    assert d("zustand") == "TEIL" and "robot-ws" not in log


def test_teilausfall_startet_agent_nach_10_min_wenn_stufe1_geheilt_hat():
    _, log, d = _lauf({"vorfall_seit": vor(300)}, http="404", geheilt=1)
    assert "AGENT" not in log
    _, log, d = _lauf({"vorfall_seit": vor(620)}, http="404", geheilt=1)
    assert "AGENT" in log and d("agent_gestartet")


def test_teilausfall_ohne_stufe1_wartet_bis_15_min():
    _, log, _ = _lauf({"vorfall_seit": vor(620)}, http="404", geheilt=0)
    assert "AGENT" not in log
    _, log, _ = _lauf({"vorfall_seit": vor(910)}, http="404", geheilt=0)
    assert "AGENT" in log


def test_agent_hoechstens_einmal_je_vorfall():
    _, log, _ = _lauf({"vorfall_seit": vor(2000), "agent_gestartet": vor(1000)}, http="404", geheilt=1)
    assert "AGENT" not in log


def test_erholung_meldet_und_raeumt_auf():
    _, log, d = _lauf({"vorfall_seit": vor(1300), "alarmiert": vor(600), "reset_ausgeloest": vor(60)})
    assert "wieder erreichbar" in log or "Title: Hauptserver wieder erreichbar" in log
    assert not d("vorfall_seit") and not d("reset_ausgeloest")
