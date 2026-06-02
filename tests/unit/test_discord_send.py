"""Tests fuer scripts/lib/discord-send.sh — 429-Resilienz (#293).

Startet einen Stub-HTTP-Server und ruft die gesourcte Bash-Funktion discord_post
ueber `bash -c` auf. Verifiziert:
  - 429 dann 204  -> genau 1 Retry, finaler Code 204, Exit 0
  - immer 500      -> KEIN Retry (nur 429 wird wiederholt), Exit != 0
  - immer 429      -> genau 1 Retry (2 Requests gesamt), Exit != 0
  - leerer Webhook -> "000", Exit != 0, kein Request

Jitter ist via DISCORD_MAX_JITTER_MS=0 deaktiviert, Retry-After=0 haelt die Tests
schnell.
"""

import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

_LIB = Path(__file__).resolve().parents[2] / "scripts" / "lib" / "discord-send.sh"

# Request-Zaehler pro Pfad (ueber alle Handler-Instanzen geteilt).
_COUNTS: dict[str, int] = {}
_LOCK = threading.Lock()


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):  # Test-Output ruhig halten
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length:
            self.rfile.read(length)
        with _LOCK:
            _COUNTS[self.path] = _COUNTS.get(self.path, 0) + 1
            n = _COUNTS[self.path]

        if self.path == "/retry-once":
            if n == 1:
                self.send_response(429)
                self.send_header("Retry-After", "0")
                self.end_headers()
            else:
                self.send_response(204)
                self.end_headers()
        elif self.path == "/always-500":
            self.send_response(500)
            self.end_headers()
        elif self.path == "/always-429":
            self.send_response(429)
            self.send_header("Retry-After", "0")
            self.end_headers()
        else:
            self.send_response(204)
            self.end_headers()


@pytest.fixture()
def server():
    _COUNTS.clear()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    host, port = srv.server_address
    yield f"http://{host}:{port}"
    srv.shutdown()


def _discord_post(url: str):
    """Ruft discord_post via bash auf; gibt (stdout_code, returncode) zurueck."""
    cmd = (
        f'DISCORD_MAX_JITTER_MS=0 DISCORD_RETRY_CAP=2 '
        f'source "{_LIB}"; discord_post "{url}" \'{{"content":"x"}}\''
    )
    res = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=30)
    return res.stdout.strip(), res.returncode


def test_429_dann_204_genau_ein_retry(server):
    code, rc = _discord_post(f"{server}/retry-once")
    assert code == "204"
    assert rc == 0
    assert _COUNTS["/retry-once"] == 2  # erster 429 + ein Retry


def test_500_kein_retry(server):
    code, rc = _discord_post(f"{server}/always-500")
    assert code == "500"
    assert rc != 0
    assert _COUNTS["/always-500"] == 1  # KEIN Retry bei Nicht-429


def test_429_dauerhaft_genau_ein_retry(server):
    code, rc = _discord_post(f"{server}/always-429")
    assert code == "429"
    assert rc != 0
    assert _COUNTS["/always-429"] == 2  # erster Versuch + genau 1 Retry


def test_leerer_webhook_kein_request():
    cmd = f'source "{_LIB}"; discord_post "" \'{{"content":"x"}}\''
    res = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=10)
    assert res.stdout.strip() == "000"
    assert res.returncode != 0
