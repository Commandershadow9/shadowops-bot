"""
Tests fuer die Alarm-Erinnerung bei anhaltendem Ausfall (ZERODOX #2425).

Hintergrund — Vorfall 16./17.08.2026: Auf der Runner-VM 10.8.0.10 lief die
Platte voll. Der `runner-vm-disk-watchdog` erkannte das korrekt und alarmierte
am 16.08. um 22:09 Uhr. Danach schwieg er 9 Stunden und 18 Minuten lang durch
19 aufeinanderfolgende Laeufe — waehrend die Belegung von 85 % auf 100 % stieg
und die gesamte CI zum Stillstand kam.

Ursache: Der Alarm feuerte nur beim UEBERGANG up→down
(`consecutive -ge 2 && last_status != down`). Ab dem zweiten Down-Lauf ist
`last_status=down`, und die Bedingung bleibt bis zur Erholung falsch.

Fuer einen *Dienst* ist das richtig gedacht — er faellt aus, man wird geweckt,
Wiederholung waere Laerm. Fuer eine *Ressource*, die sich monoton
verschlechtert, ist es fatal: Zwischen "Schwelle erreicht" und "nichts geht
mehr" liegt genau die Zeitspanne, in der man noch handeln koennte.

⚠️ Zweiter Befund, ohne den eine Erinnerung nie ausloesen wuerde:
`last_alert_at` wurde bei JEDEM Down-Lauf auf die aktuelle Zeit gesetzt, auch
ohne gesendeten Alarm. Das Feld trug damit "letzter Down-Lauf" statt "letzter
Alarm" — eine zeitbasierte Erinnerung darauf waere stillschweigend wirkungslos
geblieben.
"""
import http.server
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "service-watchdog.sh"


# Gleiches Stub-Muster wie test_service_watchdog_jq_filter.py — bewusst lokal
# statt importiert: pytest legt tests/unit nicht auf den Modulpfad, und ein
# Import ueber Dateipfad waere mehr Mechanik als die zwanzig Zeilen hier.
def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def stub_health_server(status_code: int, body):
    body_bytes = json.dumps(body).encode() if isinstance(body, dict) else str(body).encode()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body_bytes)))
            self.end_headers()
            self.wfile.write(body_bytes)

        do_POST = do_GET  # der Webhook-Stub wird per POST angesprochen

        def log_message(self, *_a, **_k):
            pass

    port = _free_port()
    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{port}/health"
    finally:
        server.shutdown()
        server.server_close()


def _vor(sekunden: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=sekunden)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _lauf(state: dict | None, env_extra: dict | None = None):
    """Watchdog gegen einen dauerhaft kaputten Endpunkt laufen lassen."""
    tmpdir = tempfile.mkdtemp(prefix="watchdog-erinnerung-")
    state_file = os.path.join(tmpdir, "state.json")
    if state is not None:
        Path(state_file).write_text(json.dumps(state))
    try:
        with stub_health_server(204, "") as webhook_url:
            with stub_health_server(503, {"status": "critical"}) as health_url:
                env = {
                    **os.environ,
                    "WATCHDOG_SERVICE_NAME": "test-erinnerung",
                    "WATCHDOG_STATE_FILE": state_file,
                    "WATCHDOG_WEBHOOK": webhook_url,
                    "WATCHDOG_HEALTH_URL": health_url,
                    "WATCHDOG_REQUIRE_BOT_READY": "0",
                    "WATCHDOG_TIMEOUT_S": "5",
                    "WATCHDOG_TAKT_SEK": "300",
                    **(env_extra or {}),
                }
                r = subprocess.run(["bash", str(SCRIPT)], env=env,
                                   capture_output=True, text=True, timeout=30)
        danach = json.loads(Path(state_file).read_text()) if Path(state_file).exists() else {}
        return r, danach
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _hat_alarmiert(r) -> bool:
    return "Discord-Alert gesendet" in (r.stdout + r.stderr)


# ---------- Bestehendes Verhalten darf sich nicht aendern ----------

def test_erster_ausfall_alarmiert_noch_nicht():
    """Ein einzelner Fehlschlag ist kein Signal — das bleibt so."""
    r, _ = _lauf({"last_status": "up", "last_alert_at": "", "consecutive_failures": 0})
    assert not _hat_alarmiert(r), r.stdout


def test_zweiter_ausfall_alarmiert():
    r, danach = _lauf({"last_status": "up", "last_alert_at": "", "consecutive_failures": 1})
    assert _hat_alarmiert(r), r.stdout
    assert danach["last_status"] == "down"
    assert danach["last_alert_at"], "Zeitpunkt des Alarms muss festgehalten werden"


def test_direkt_nach_dem_alarm_kommt_keine_wiederholung():
    """Kein Alarm-Spam: Innerhalb der Erinnerungsfrist bleibt es still."""
    r, _ = _lauf({"last_status": "down", "last_alert_at": _vor(120), "consecutive_failures": 5})
    assert not _hat_alarmiert(r), r.stdout


# ---------- Der eigentliche Fix ----------

def test_anhaltender_ausfall_erinnert_nach_ablauf_der_frist():
    """
    Der Kern des Vorfalls: Nach 4 Stunden ununterbrochenem Ausfall muss erneut
    alarmiert werden. Vor diesem Fix kam hier nichts.
    """
    r, danach = _lauf(
        {"last_status": "down", "last_alert_at": _vor(4 * 3600), "consecutive_failures": 8},
        {"WATCHDOG_ERINNERUNG_SEK": "3600"},
    )
    assert _hat_alarmiert(r), r.stdout + r.stderr
    assert danach["last_alert_at"] != "", "Erinnerung muss den Zeitstempel erneuern"


def test_erinnerung_nennt_die_dauer_des_ausfalls():
    """
    Eine Wiederholung ohne Dauer waere vom Erstalarm nicht zu unterscheiden —
    und die Dauer ist die Information, die zur Eskalation fuehrt.
    """
    r, _ = _lauf(
        {"last_status": "down", "last_alert_at": _vor(4 * 3600), "consecutive_failures": 8},
        {"WATCHDOG_ERINNERUNG_SEK": "3600"},
    )
    assert re.search(r"(seit|Dauer|andauernd|unveraendert)", r.stdout, re.I), r.stdout


def test_zeitstempel_des_alarms_wird_bei_stillem_lauf_nicht_erneuert():
    """
    ⚠️ Regressionstest fuer den Bug, der die Erinnerung wirkungslos gemacht
    haette: Ein Down-Lauf OHNE Alarm darf `last_alert_at` nicht anfassen.
    Sonst schiebt jeder Lauf die Frist vor sich her und sie laeuft nie ab.
    """
    vorher = _vor(120)
    r, danach = _lauf({"last_status": "down", "last_alert_at": vorher, "consecutive_failures": 5})
    assert not _hat_alarmiert(r)
    assert danach["last_alert_at"] == vorher, (
        f"last_alert_at wurde ohne Alarm veraendert: {vorher} -> {danach['last_alert_at']}"
    )


def test_erholung_setzt_alles_zurueck():
    """Nach der Erholung beginnt die Zaehlung von vorn."""
    tmpdir = tempfile.mkdtemp(prefix="watchdog-erholung-")
    state_file = os.path.join(tmpdir, "state.json")
    Path(state_file).write_text(json.dumps(
        {"last_status": "down", "last_alert_at": _vor(9000), "consecutive_failures": 9}))
    try:
        with stub_health_server(204, "") as webhook_url:
            with stub_health_server(200, {"status": "ok"}) as health_url:
                env = {
                    **os.environ,
                    "WATCHDOG_SERVICE_NAME": "test-erinnerung",
                    "WATCHDOG_STATE_FILE": state_file,
                    "WATCHDOG_WEBHOOK": webhook_url,
                    "WATCHDOG_HEALTH_URL": health_url,
                    "WATCHDOG_REQUIRE_BOT_READY": "0",
                    "WATCHDOG_TIMEOUT_S": "5",
                }
                r = subprocess.run(["bash", str(SCRIPT)], env=env,
                                   capture_output=True, text=True, timeout=30)
        danach = json.loads(Path(state_file).read_text())
        assert "OK — healthy" in r.stdout, r.stdout
        assert danach["last_status"] == "up"
        assert danach["consecutive_failures"] == 0
        assert danach["last_alert_at"] == ""
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
