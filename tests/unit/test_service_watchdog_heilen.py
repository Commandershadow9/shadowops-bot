"""
Tests fuer den Heilversuch bei Alarm (ZERODOX #5627).

Vorfall 12.11.2026: zerodox.de lieferte 70 Minuten lang 404. Der
zerodox-watchdog alarmierte nach 5 Minuten korrekt nach Discord, gehandelt hat
niemand, weil es Nacht war. `WATCHDOG_HEAL_CMD` laesst den Watchdog ab dem
zweiten Down-Lauf einen fest programmierten Heiler ausfuehren.

Was hier abgesichert wird:
- kein Heilen beim ersten Fehlschlag (Netz-Schluckauf startet nichts neu)
- hoechstens einmal je Sperrfrist (keine Neustart-Schleife)
- das Ergebnis kommt IMMER nach Discord, auch zwischen zwei Alarmen
- die Heiler-Ausgabe zerbricht das hand-gebaute JSON nicht
- ohne WATCHDOG_HEAL_CMD bleibt alles wie vorher
"""
import http.server
import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "service-watchdog.sh"
# Siehe test_service_watchdog_erinnerung.py: sonst Meldungen an den echten Systemstatus.
KEIN_ECHTES_MELDEN = {"ZERODOX_AGENT_API_KEY": ""}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def _server(status_code: int, empfangen: list | None = None):
    class Handler(http.server.BaseHTTPRequestHandler):
        def _antwort(self):
            laenge = int(self.headers.get("Content-Length") or 0)
            if empfangen is not None and laenge:
                empfangen.append(self.rfile.read(laenge).decode())
            body = b'{"status":"critical"}'
            self.send_response(status_code)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = _antwort
        do_POST = _antwort

        def log_message(self, *_a, **_k):
            pass

    port = _free_port()
    srv = http.server.HTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{port}/x"
    finally:
        srv.shutdown()
        srv.server_close()


def _vor(sekunden: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=sekunden)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _lauf(state: dict, heal_cmd: str | None, env_extra: dict | None = None):
    tmp = tempfile.mkdtemp(prefix="watchdog-heilen-")
    state_file = os.path.join(tmp, "state.json")
    marker = os.path.join(tmp, "geheilt")
    Path(state_file).write_text(json.dumps(state))
    webhooks: list[str] = []
    try:
        with _server(204, webhooks) as webhook_url, _server(503) as health_url:
            env = {
                **os.environ,
                "WATCHDOG_SERVICE_NAME": "test-heilen",
                "WATCHDOG_STATE_FILE": state_file,
                "WATCHDOG_WEBHOOK": webhook_url,
                "WATCHDOG_HEALTH_URL": health_url,
                "WATCHDOG_REQUIRE_BOT_READY": "0",
                "WATCHDOG_TIMEOUT_S": "5",
                "WATCHDOG_TAKT_SEK": "300",
                "HEAL_MARKER": marker,
                **KEIN_ECHTES_MELDEN,
                **(env_extra or {}),
            }
            env.pop("WATCHDOG_HEAL_CMD", None)
            if heal_cmd is not None:
                env["WATCHDOG_HEAL_CMD"] = heal_cmd
            r = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60)
        return r, json.loads(Path(state_file).read_text()), Path(marker).exists(), webhooks
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


HEILER = 'touch "$HEAL_MARKER"; echo "Proxy neu gestartet"; echo "Aussenpruefung: 200"'


def test_erster_fehlschlag_heilt_nicht():
    _, _, geheilt, _ = _lauf({"last_status": "up", "last_alert_at": "", "consecutive_failures": 0}, HEILER)
    assert not geheilt


def test_zweiter_fehlschlag_heilt_und_meldet_das_ergebnis_im_alarm():
    r, state, geheilt, webhooks = _lauf({"last_status": "up", "last_alert_at": "", "consecutive_failures": 1}, HEILER)
    assert geheilt, r.stdout + r.stderr
    assert state["last_heal_at"], "Zeitpunkt des Heilversuchs muss festgehalten werden"
    assert len(webhooks) == 1, "Heilergebnis gehoert in den Alarm, kein zweiter Post"
    beschreibung = json.loads(webhooks[0])["embeds"][0]["description"]
    assert "Heilversuch" in beschreibung and "Aussenpruefung: 200" in beschreibung


def test_sperrfrist_verhindert_neustart_schleife():
    _, state, geheilt, _ = _lauf(
        {"last_status": "down", "last_alert_at": _vor(60), "consecutive_failures": 3, "last_heal_at": _vor(120)},
        HEILER,
    )
    assert not geheilt
    assert state["last_heal_at"], "Sperrfrist darf beim stillen Lauf nicht verloren gehen"


def test_nach_ablauf_der_sperrfrist_wird_erneut_geheilt_und_eigens_gemeldet():
    """Zwischen zwei Alarmen: Ein automatischer Neustart darf nicht unbemerkt bleiben."""
    _, _, geheilt, webhooks = _lauf(
        {"last_status": "down", "last_alert_at": _vor(60), "consecutive_failures": 4, "last_heal_at": _vor(700)},
        HEILER,
    )
    assert geheilt
    assert len(webhooks) == 1
    assert "Heilversuch" in json.loads(webhooks[0])["embeds"][0]["title"]


def test_heiler_ausgabe_mit_sonderzeichen_zerbricht_das_json_nicht():
    heiler = 'echo "Fehler: \\"x\\" in C:\\\\pfad"; printf "a\\tb\\n"; echo \'```\'; exit 3'
    _, _, _, webhooks = _lauf({"last_status": "up", "last_alert_at": "", "consecutive_failures": 1}, heiler)
    assert len(webhooks) == 1
    beschreibung = json.loads(webhooks[0])["embeds"][0]["description"]  # wirft bei kaputtem JSON
    assert "Exit 3" in beschreibung


def test_haengender_heiler_wird_abgebrochen():
    r, state, _, _ = _lauf(
        {"last_status": "up", "last_alert_at": "", "consecutive_failures": 1},
        "sleep 30",
        {"WATCHDOG_HEAL_TIMEOUT_S": "1"},
    )
    assert "Exit 124" in r.stdout or state["last_heal_at"], r.stdout


def test_ohne_heilbefehl_bleibt_alles_wie_vorher():
    _, state, geheilt, webhooks = _lauf({"last_status": "up", "last_alert_at": "", "consecutive_failures": 1}, None)
    assert not geheilt
    assert state["last_heal_at"] == ""
    assert len(webhooks) == 1 and "Heilversuch" not in webhooks[0]


def test_erholung_setzt_heilzeitpunkt_zurueck():
    tmp = tempfile.mkdtemp()
    try:
        state_file = os.path.join(tmp, "s.json")
        Path(state_file).write_text(json.dumps(
            {"last_status": "down", "last_alert_at": _vor(60), "consecutive_failures": 3, "last_heal_at": _vor(60)}))
        with _server(204) as webhook_url, _server(200) as health_url:
            env = {**os.environ, **KEIN_ECHTES_MELDEN, "WATCHDOG_SERVICE_NAME": "test-heilen",
                   "WATCHDOG_STATE_FILE": state_file, "WATCHDOG_WEBHOOK": webhook_url,
                   "WATCHDOG_HEALTH_URL": health_url, "WATCHDOG_REQUIRE_BOT_READY": "0",
                   "WATCHDOG_TAKT_SEK": "300", "WATCHDOG_HEAL_CMD": "true"}
            subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=30)
        assert json.loads(Path(state_file).read_text())["last_heal_at"] == ""
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
