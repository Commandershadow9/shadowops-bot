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
url="${@: -1}"; out=""; prev=""
for a in "$@"; do [ "$prev" = "-o" ] && out="$a"; prev="$a"; done
case "$url" in
  *robot-ws*) echo 200; exit 0 ;;
  *-quittung/json*)
    [ "${FAKE_QUITTUNG_FEHLER:-0}" = 1 ] && exit 7
    [ "${FAKE_QUITTUNG_FEHLER:-0}" = 502 ] && { echo 502; exit 0; }
    [ "${FAKE_QUITTUNG:-0}" = 1 ] && [ -n "$out" ] && echo '{"event":"message"}' > "$out"
    echo 200; exit 0 ;;
  *ntfy.sh/v1/health*|*hetzner.com*) [ "${FAKE_BLIND:-0}" = 1 ] && exit 7; exit 0 ;;
  *ntfy.sh/*) exit 0 ;;
esac
for a in "$@"; do [ "$a" = "%{http_code}" ] && { echo "${FAKE_HTTP:-200}"; exit 0; }; done
exit 0
"""
PING = '#!/usr/bin/env bash\n[ "${FAKE_PING:-1}" = 1 ]\n'
SSH = '#!/usr/bin/env bash\necho "ssh $*" >> "$FAKE_LOG"\n[ "${FAKE_GEHEILT:-0}" = 1 ] && echo \'watchdog_state_zerodox.json: {"last_heal_at":"2026-11-12T05:00:00Z"}\'\nexit 0\n'
AGENT = '#!/usr/bin/env bash\necho "AGENT" >> "$FAKE_LOG"\necho "ERGEBNIS: GEHEILT"\n'


def _lauf(state: dict, agent: str = AGENT, alte_berichte=(), discord: str = "", **fake):
    tmp = Path(tempfile.mkdtemp(prefix="waechter-"))
    (tmp / "bin").mkdir()
    for name, inhalt in (("curl", CURL), ("ping", PING), ("ssh", SSH)):
        f = tmp / "bin" / name
        f.write_text(inhalt)
        f.chmod(0o755)
    basis = tmp / "basis"
    basis.mkdir()
    (basis / "notfall-agent.sh").write_text(agent)
    (basis / "notfall-agent.sh").chmod(0o755)
    (basis / "notfall-ssh").write_text(SSH)
    (basis / "notfall-ssh").chmod(0o755)
    st = tmp / "state"
    st.mkdir()
    for k, v in state.items():
        (st / k).write_text(str(v))
    alt = time.time() - 40 * 86400
    for name in alte_berichte:
        (st / name).write_text("alt")
        os.utime(st / name, (alt, alt))
    log = tmp / "log"
    log.write_text("")
    env = {
        **os.environ,
        "PATH": f"{tmp / 'bin'}:{os.environ['PATH']}",
        "NOTFALL_STATE": str(st), "NOTFALL_BASIS": str(basis), "NOTFALL_EINMAL": "1",
        "NTFY_TOPIC": "test-topic", "DISCORD_WEBHOOK": discord,
        "ROBOT_USER": "u", "ROBOT_PASS": "p", "ROBOT_SERVER_NUMBER": "123",
        "FAKE_LOG": str(log),
        **{f"FAKE_{k.upper()}": str(v) for k, v in fake.items()},
    }
    r = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=30)
    # Agent laeuft im Hintergrund: warten, bis er fertig ist und gemeldet hat.
    if (st / "agent_gestartet").exists() and "agent_gestartet" not in state:
        for _ in range(50):
            if (st / "agent_fertig").exists() and "Notfall-Agent fertig" in log.read_text():
                break
            time.sleep(0.1)
        time.sleep(0.3)
    datei = lambda n: (st / n).read_text().strip() if (st / n).exists() else ""  # noqa: E731
    # Der Agent laeuft mit `env -i` (erbt keine Geheimnisse) — sein Start ist deshalb
    # am Zustand und an der abgelegten Berichtsdatei messbar, nicht am FAKE_LOG.
    agent_lief = bool(datei("agent_gestartet")) and "agent_gestartet" not in state
    _lauf.state_dir = st  # fuer Tests, die Dateien pruefen
    return r, log.read_text() + ("AGENT\n" if agent_lief else ""), datei


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


# ── Härtung nach Sicherheitsreview (ZERODOX #5627) ─────────────────────────


def test_leere_zeitstempel_datei_ist_kein_syntaxfehler():
    """Befund 9: Eine leere/kaputte Zeitstempel-Datei gilt als nicht gesetzt."""
    r, _, d = _lauf({"vorfall_seit": "", "tot_alarm": "kaputt"}, http="000", ping=0)
    assert "syntax error" not in r.stderr and "Syntaxfehler" not in r.stderr, r.stderr
    assert d("vorfall_seit").isdigit()
    assert d("zustand") == "TOT"


def test_leere_datei_bei_erholung_raeumt_auf():
    r, _, d = _lauf({"vorfall_seit": "", "alarmiert": vor(60)})
    assert "syntax error" not in r.stderr, r.stderr
    assert not d("vorfall_seit")


def test_quittungsabfrage_fehlgeschlagen_kein_reset_einmal_gemeldet():
    """Befund 7: Abfrage gescheitert ist nicht „keine Quittung“ → kein Reset in diesem Lauf."""
    for fehler in (1, 502):
        _, log, d = _lauf({"vorfall_seit": vor(1300), "tot_alarm": vor(620)}, http="000", ping=0,
                          quittung_fehler=fehler)
        assert "robot-ws" not in log and not d("reset_ausgeloest"), fehler
        assert "Quittungsabfrage fehlgeschlagen" in log and d("quittung_fehler")
    _, log, d = _lauf({"vorfall_seit": vor(1300), "tot_alarm": vor(620), "quittung_fehler": vor(60)},
                      http="000", ping=0, quittung_fehler=1)
    assert "robot-ws" not in log and "Quittungsabfrage fehlgeschlagen" not in log


def test_nach_fehlgeschlagener_abfrage_folgt_reset_wenn_abfrage_wieder_geht():
    _, log, d = _lauf({"vorfall_seit": vor(1400), "tot_alarm": vor(700), "quittung_fehler": vor(60)},
                      http="000", ping=0)
    assert "robot-ws.your-server.de/reset/123" in log and d("reset_ausgeloest")


def test_haengender_agent_eskaliert_nach_20_min():
    """Befund 10: agent_gestartet > 20 min, agent_fertig fehlt → wie „Agent half nicht“."""
    _, log, d = _lauf({"vorfall_seit": vor(2000), "agent_gestartet": vor(600)}, http="404")
    assert not d("teil_eskaliert") and "hängt" not in log
    _, log, d = _lauf({"vorfall_seit": vor(2000), "agent_gestartet": vor(1300)}, http="404")
    assert d("teil_eskaliert") and "Notfall-Agent hängt" in log and "Priority: 5" in log
    _, log, _ = _lauf({"vorfall_seit": vor(2000), "agent_gestartet": vor(1300), "teil_eskaliert": vor(60)},
                      http="404")
    assert "hängt" not in log


BOESER_AGENT = """#!/usr/bin/env bash
echo "@everyone klickt https://boese.example/x"
echo '```'
echo "ERGEBNIS: GEHEILT — und ruft https://boese.example an"
"""


def _zeilen(log, *teile):
    return [z for z in log.splitlines() if all(t in z for t in teile)]


def test_bericht_ist_codeblock_ohne_erwaehnungen_und_ntfy_nur_fester_wert():
    """Befund 5: Agent-Bericht ist Fremdtext — Discord nur als Codeblock ohne Mentions,
    ntfy nur GEHEILT|NICHT GEHEILT|UNKLAR."""
    _, log, _ = _lauf({"vorfall_seit": vor(620)}, http="404", geheilt=1, agent=BOESER_AGENT,
                      discord="https://discord.test/webhook")
    ntfy = _zeilen(log, "ntfy.sh/test-topic", "Notfall-Agent fertig")
    assert ntfy, log
    assert "Title: Notfall-Agent fertig — GEHEILT " in ntfy[0] and "boese" not in ntfy[0]
    disc = _zeilen(log, "discord.test", "unverifiziert")
    assert disc, log
    assert '"allowed_mentions":{"parse":[]}' in disc[0]
    assert "Bericht des Notfall-Agenten (unverifiziert)" in disc[0]
    # genau ein Codeblock (öffnend + schließend); der ``` aus dem Bericht ist entschärft
    assert disc[0].count("```") == 2 and "\x27\x27\x27" in disc[0]
    # auch normale Meldungen ohne Erwähnungen
    for z in _zeilen(log, "discord.test"):
        assert '"allowed_mentions":{"parse":[]}' in z


def test_ergebnis_ohne_muster_ist_unklar():
    agent = '#!/usr/bin/env bash\necho "ERGEBNIS: GEHEILT | NICHT GEHEILT | UNKLAR"\n'
    _, log, _ = _lauf({"vorfall_seit": vor(620)}, http="404", geheilt=1, agent=agent)
    ntfy = _zeilen(log, "ntfy.sh/test-topic", "Notfall-Agent fertig")
    assert ntfy and "Notfall-Agent fertig — UNKLAR " in ntfy[0], log
    agent = '#!/usr/bin/env bash\necho "ERGEBNIS: NICHT GEHEILT"\n'
    _, log, _ = _lauf({"vorfall_seit": vor(620)}, http="404", geheilt=1, agent=agent)
    assert "Notfall-Agent fertig — NICHT GEHEILT " in log
    agent = '#!/usr/bin/env bash\necho "Freitext ohne Ergebniszeile"\n'
    _, log, _ = _lauf({"vorfall_seit": vor(620)}, http="404", geheilt=1, agent=agent)
    assert "Notfall-Agent fertig — UNKLAR " in log


def test_alte_berichte_werden_geloescht():
    _lauf({}, alte_berichte=["agent-bericht-20260901-0300.txt"])
    assert not (_lauf.state_dir / "agent-bericht-20260901-0300.txt").exists()
