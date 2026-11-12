"""
Tests für den Zustandsautomaten von notbetrieb-umschalter.sh (ZERODOX #5629).

curl (Hetzner-API, Messungen, ntfy), ping und traceroute sind Stubs; jeder Test fährt
EINEN Lauf (NOTBETRIEB_EINMAL=1) gegen einen vorbereiteten Zustand.

Am wichtigsten ist, was NICHT passieren darf: umschalten, solange der Hauptserver
auch nur per Ping lebt; umschalten, wenn HEL1 selbst blind ist; einen fremden
DNS-Wert überschreiben; auf eine Notseite schalten, die HEL1 gar nicht ausliefert.
"""
import os
import subprocess
import tempfile
import time
from pathlib import Path

SCRIPT = (Path(__file__).resolve().parents[2] / "scripts" / "notfall" / "notbetrieb"
          / "notbetrieb-umschalter.sh")
HAUPT, HEL1 = "88.99.160.210", "157.180.55.95"

CURL = r"""#!/usr/bin/env bash
echo "curl $*" >> "$FAKE_LOG"
url="${@: -1}"; out=""; prev=""
for a in "$@"; do [ "$prev" = "-o" ] && out="$a"; prev="$a"; done
case "$url" in
  https://ntfy.sh/v1/health|https://api.hetzner.cloud/v1) [ "${FAKE_BLIND:-0}" = 1 ] && exit 7; exit 0 ;;
  *ntfy.sh/*) exit 0 ;;
  */rrsets/*/A/actions/set_records) echo "SET $*" >> "$FAKE_LOG"; [ -n "$out" ] && echo '{}' > "$out"; echo 201; exit 0 ;;
  */rrsets/*/A) [ -n "$out" ] && printf '{"rrset":{"records":[{"value":"%s"}]}}' "${FAKE_DNS:-88.99.160.210}" > "$out"; echo 200; exit 0 ;;
esac
case "$*" in
  *"::88.99.160.210:"*) echo "${FAKE_HAUPT_HTTP:-200}"; exit 0 ;;
  *"::157.180.55.95:"*) echo "${FAKE_LOKAL:-200}"; exit 0 ;;
esac
exit 0
"""
PING = '#!/usr/bin/env bash\n[ "${FAKE_PING:-1}" = 1 ]\n'
TRACE = r"""#!/usr/bin/env bash
echo " 6  213.239.240.9  34 ms"
if [ "${FAKE_VORPOSTEN:-1}" = 1 ]; then echo " 7  213.133.117.190  35 ms"; else echo " 7  *"; fi
[ "${FAKE_PING:-1}" = 1 ] && echo " 8  88.99.160.210  35 ms" || echo " 8  *"
"""


def _lauf(state: dict, sperre=False, spiegel_alter=60, token="t", args=(), **fake):
    tmp = Path(tempfile.mkdtemp(prefix="notbetrieb-"))
    (tmp / "bin").mkdir()
    dig = '#!/usr/bin/env bash\necho "${FAKE_DNS:-88.99.160.210}"\n'  # nie echtes Netz im Test
    for name, inhalt in (("curl", CURL), ("ping", PING), ("traceroute", TRACE), ("dig", dig)):
        f = tmp / "bin" / name
        f.write_text(inhalt)
        f.chmod(0o755)
    st = tmp / "state"
    st.mkdir()
    for k, v in state.items():
        (st / k).write_text(str(v))
    daten = tmp / "daten"
    daten.mkdir()
    (daten / "stand").write_text(str(int(time.time()) - spiegel_alter))
    sp = tmp / "sperre"
    if sperre:
        sp.write_text("")
    log = tmp / "log"
    log.write_text("")
    env = {
        **os.environ,
        "PATH": f"{tmp / 'bin'}:{os.environ['PATH']}",
        "NOTBETRIEB_STATE": str(st), "NOTBETRIEB_DATEN": str(daten), "NOTBETRIEB_SPERRE": str(sp),
        "NOTBETRIEB_EINMAL": "1", "NTFY_TOPIC": "test-topic", "DISCORD_WEBHOOK": "",
        "HCLOUD_TOKEN": token, "FAKE_LOG": str(log),
        **{f"FAKE_{k.upper()}": str(v) for k, v in fake.items()},
    }
    r = subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=30)
    datei = lambda n: (st / n).read_text().strip() if (st / n).exists() else ""  # noqa: E731
    gesetzt = [z for z in log.read_text().splitlines() if z.startswith("SET ")]
    return r, log.read_text(), gesetzt, datei


def vor(s):
    return int(time.time()) - s


def tot_seit(s):
    """Zustand eines Vorfalls; der Vorposten wurde gelernt, als der Server noch lebte."""
    return {"tot_seit": vor(s), "vorposten": "213.133.117.190"}


TOT = {"ping": 0, "haupt_http": "000"}


def test_gesund_tut_nichts():
    _, _, gesetzt, d = _lauf({})
    assert gesetzt == [] and d("modus") == "" and d("tot_seit") == ""


def test_erster_toter_lauf_merkt_nur_den_beginn():
    _, _, gesetzt, d = _lauf({}, **TOT)
    assert gesetzt == [] and d("tot_seit")


def test_nach_10_min_tot_mit_vorposten_wird_umgeschaltet():
    _, log, gesetzt, d = _lauf(tot_seit(601), **TOT)
    assert len(gesetzt) == 1 and "/rrsets/@/A/" in gesetzt[0] and HEL1 in gesetzt[0]
    assert d("modus") == "notbetrieb"
    assert "Priority: 5" in log and "Notbetrieb AKTIV" in log


def test_ohne_vorposten_wartet_er_30_min():
    _, _, gesetzt, _ = _lauf(tot_seit(900), vorposten=0, **TOT)
    assert gesetzt == []
    _, _, gesetzt, d = _lauf(tot_seit(1801), vorposten=0, **TOT)
    assert len(gesetzt) == 1 and d("modus") == "notbetrieb"


def test_ping_allein_haelt_den_server_am_leben():
    """Antwortet der Server auf Ping, ist es kein Hardwareausfall (Stufe 1/2 sind zuständig)."""
    _, _, gesetzt, d = _lauf(tot_seit(3600), ping=1, haupt_http="000")
    assert gesetzt == [] and d("tot_seit") == ""


def test_http_fehler_ohne_ping_ist_nicht_tot():
    """Ein 502 heisst: Der Rechner lebt, nur die Anwendung nicht."""
    _, _, gesetzt, _ = _lauf(tot_seit(3600), ping=0, haupt_http="502")
    assert gesetzt == []


def test_blind_tut_nichts():
    _, _, gesetzt, d = _lauf(tot_seit(3600), blind=1, **TOT)
    assert gesetzt == [] and d("modus") == ""


def test_sperre_verhindert_umschalten():
    _, log, gesetzt, _ = _lauf(tot_seit(3600), sperre=True, **TOT)
    assert gesetzt == [] and "Notbetrieb gesperrt" in log


def test_fremder_dns_wert_wird_nicht_ueberschrieben():
    _, log, gesetzt, d = _lauf(tot_seit(601), dns="1.2.3.4", **TOT)
    assert gesetzt == [] and d("modus") == "" and "DNS unerwartet" in log


def test_ohne_eigene_notseite_wird_nicht_umgeschaltet():
    _, log, gesetzt, _ = _lauf(tot_seit(601), lokal="000", **TOT)
    assert gesetzt == [] and "NICHT bereit" in log


def test_rueckschaltung_erst_nach_10_min_gesund():
    _, _, gesetzt, d = _lauf({"modus": "notbetrieb"}, dns=HEL1)
    assert gesetzt == [] and d("gesund_seit")
    _, log, gesetzt, d = _lauf({"modus": "notbetrieb", "gesund_seit": vor(601)}, dns=HEL1)
    assert len(gesetzt) == 1 and HAUPT in gesetzt[0] and d("modus") == "normal"
    assert "Notbetrieb beendet" in log


def test_rueckfall_im_notbetrieb_setzt_gesund_zurueck():
    _, _, gesetzt, d = _lauf({"modus": "notbetrieb", "gesund_seit": vor(500)}, dns=HEL1, haupt_http="000")
    assert gesetzt == [] and d("gesund_seit") == "" and d("modus") == "notbetrieb"


def test_veralteter_spiegel_wird_einmal_gemeldet():
    _, log, _, d = _lauf({}, spiegel_alter=7200)
    assert "Spiegel veraltet" in log and d("gemeldet_spiegel_alt")
    _, log, _, _ = _lauf({"gemeldet_spiegel_alt": vor(60)}, spiegel_alter=7200)
    assert "Spiegel veraltet" not in log



# ── Härtung nach Sicherheitsreview (ZERODOX #5627/#5629) ───────────────────

def _api_aufrufe(log):
    return [z for z in log.splitlines() if "/zones/" in z]


def test_ohne_token_nie_api_sondern_einmal_von_hand_melden():
    """Auf HEL1 liegt bewusst kein Token (CI-Host). Dann nie die API, nur Anleitung."""
    _, log, gesetzt, d = _lauf(tot_seit(601), token="", **TOT)
    assert gesetzt == [] and _api_aufrufe(log) == []
    assert "von Hand umschalten" in log and "Priority: 5" in log
    assert HEL1 in log and HAUPT in log and "console.hetzner.com" in log
    assert "seit 10 min tot" in log
    assert d("modus") in ("", "normal") and d("gemeldet_hand_an")
    # zweiter Lauf im selben Vorfall: keine Wiederholung
    st = {**tot_seit(700), "gemeldet_hand_an": vor(60)}
    _, log, gesetzt, _ = _lauf(st, token="", **TOT)
    assert "von Hand umschalten" not in log and _api_aufrufe(log) == []


def test_ohne_token_nicht_bereit_hat_vorrang():
    _, log, _, _ = _lauf(tot_seit(601), token="", lokal="000", **TOT)
    assert "NICHT bereit" in log and "von Hand umschalten" not in log


def test_ohne_token_erholung_setzt_flag_zurueck():
    _, log, _, d = _lauf({"gemeldet_hand_an": vor(600)}, token="")
    assert not d("gemeldet_hand_an") and _api_aufrufe(log) == []


def test_ohne_token_status_zeigt_token_nein():
    r, log, _, _ = _lauf({}, token="", args=("status",))
    assert "token=nein" in r.stdout and _api_aufrufe(log) == []


def test_ohne_token_test_dns_und_an_brechen_klar_ab():
    r, log, _, _ = _lauf({}, token="", args=("test-dns",))
    assert r.returncode != 0 and "HCLOUD_TOKEN" in r.stdout and _api_aufrufe(log) == []
    r, log, _, _ = _lauf({}, token="", args=("an",))
    assert r.returncode != 0 and "Hetzner Console" in r.stdout and _api_aufrufe(log) == []


def test_token_nie_in_prozessargumenten():
    """Befund 11: Der Header geht über stdin (-K -), nicht als Argument."""
    _, log, gesetzt, _ = _lauf(tot_seit(601), token="geheimertoken123", **TOT)
    assert len(gesetzt) == 1
    assert "geheimertoken123" not in log and "Bearer" not in log
    assert all("-K -" in z for z in _api_aufrufe(log))


def test_abgleich_dns_auf_hel1_bei_modus_normal_und_gesundem_hauptserver():
    """Befund 8: Abbruch zwischen API-Aufruf und Zustand → im nächsten Lauf zurück + Alarm."""
    _, log, gesetzt, d = _lauf({"absicht": "notbetrieb"}, dns=HEL1)
    assert len(gesetzt) == 1 and HAUPT in gesetzt[0]
    assert "Abgleich" in log and "Priority: 5" in log and d("absicht") == "normal"


def test_abgleich_nicht_bei_krankem_hauptserver():
    _, _, gesetzt, _ = _lauf({}, dns=HEL1, haupt_http="502")
    assert gesetzt == []


def test_absicht_wird_vor_dem_umschalten_geschrieben():
    _, _, gesetzt, d = _lauf(tot_seit(601), **TOT)
    assert len(gesetzt) == 1 and d("absicht") == "notbetrieb"


def test_leere_zeitstempel_datei_ist_kein_syntaxfehler():
    """Befund 9: leere/kaputte Zeitstempel gelten als nicht gesetzt."""
    r, _, gesetzt, d = _lauf({"tot_seit": "", "vorposten": "213.133.117.190"}, **TOT)
    assert "syntax error" not in r.stderr, r.stderr
    assert gesetzt == [] and d("tot_seit").isdigit()
    r, _, gesetzt, d = _lauf({"modus": "notbetrieb", "gesund_seit": "kaputt"}, dns=HEL1)
    assert "syntax error" not in r.stderr, r.stderr
    assert gesetzt == [] and d("gesund_seit").isdigit()
