"""
Tests fuer service-watchdog.sh — JSON-Path-Filter (Issue mayday-sim#437).

Verifiziert dass `WATCHDOG_HEALTH_JQ_FILTER` aggregierte Health-Endpoints
korrekt auf eine Komponente filtert. Use-Case: runner-health.service auf
V-Server1 meldet HTTP 503 (globaler status: critical) auch wenn die
mayday-ci-runner-Komponente selbst `ok: true` ist.

Tests starten einen lokalen HTTP-Stub-Server pro Test und prueft den
Exit-Code + Log-Output des Shell-Scripts.
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
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "service-watchdog.sh"


# ---------- HTTP-Stub-Helper ----------

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def stub_health_server(status_code: int, body: dict | str):
    """Start einen Mini-HTTP-Server der bei /health konfigurierten Status+Body liefert."""
    if isinstance(body, dict):
        body_bytes = json.dumps(body).encode()
        content_type = "application/json"
    else:
        body_bytes = body.encode() if isinstance(body, str) else body
        content_type = "text/plain"

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(status_code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body_bytes)))
            self.end_headers()
            self.wfile.write(body_bytes)

        def log_message(self, *_args, **_kwargs):  # silence
            pass

    port = _free_port()
    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{port}/health"
    finally:
        server.shutdown()
        server.server_close()


def _run_watchdog(env_extra: dict, expect_alert: bool = False) -> subprocess.CompletedProcess:
    """Run service-watchdog.sh mit isoliertem State-File und Stub-Webhook (mock)."""
    tmpdir = tempfile.mkdtemp(prefix="watchdog-test-")
    state_file = os.path.join(tmpdir, "state.json")

    # Webhook absichtlich auf Mock-Endpoint umlenken damit kein realer Discord-Call
    # passiert — Recovery/Down-Alerts werden nicht echt gesendet im Test.
    with stub_health_server(204, "") as webhook_url:
        env = {
            **os.environ,
            "WATCHDOG_SERVICE_NAME": "test-service",
            "WATCHDOG_STATE_FILE": state_file,
            "WATCHDOG_WEBHOOK": webhook_url,
            "WATCHDOG_REQUIRE_BOT_READY": "0",
            "WATCHDOG_TIMEOUT_S": "5",
            **env_extra,
        }
        try:
            return subprocess.run(
                ["bash", str(SCRIPT)],
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


# ---------- Tests ----------

@pytest.fixture(scope="module")
def script_exists():
    assert SCRIPT.exists(), f"service-watchdog.sh nicht gefunden: {SCRIPT}"
    assert shutil.which("jq"), "jq nicht installiert"
    assert shutil.which("curl"), "curl nicht installiert"


def test_jq_filter_true_makes_endpoint_healthy_despite_503(script_exists):
    """Kern-Use-Case Issue #437: HTTP 503 aber ci_runner.ok=true → UP."""
    body = {
        "status": "critical",
        "components": {
            "ci_runner": {"ok": True, "services_active": 3, "services_configured": 3},
            "github_runners": {"ok": False, "services_active": 12, "services_configured": 14},
        },
        "alerts": [
            {"code": "RUNNER_DOWN", "severity": "critical", "component": "github_runners"},
        ],
    }
    with stub_health_server(503, body) as url:
        result = _run_watchdog({
            "WATCHDOG_HEALTH_URL": url,
            "WATCHDOG_HEALTH_JQ_FILTER": ".components.ci_runner.ok",
        })
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK — healthy" in result.stdout


def test_jq_filter_false_marks_endpoint_down_despite_http_200(script_exists):
    """Edge: HTTP 200 aber Component-Filter sagt down → DOWN."""
    body = {
        "status": "ok",
        "components": {"ci_runner": {"ok": False, "services_active": 1, "services_configured": 3}},
    }
    with stub_health_server(200, body) as url:
        result = _run_watchdog({
            "WATCHDOG_HEALTH_URL": url,
            "WATCHDOG_HEALTH_JQ_FILTER": ".components.ci_runner.ok",
        })
    # consecutive=1, last_status=up → kein neuer Alert aber DOWN-Pfad
    assert "down (jq_filter_false)" in result.stdout


def test_jq_filter_missing_field_returns_invalid(script_exists):
    """Edge: Filter findet Feld nicht (null) → DOWN:jq_filter_invalid."""
    body = {"status": "ok", "components": {"other": {"ok": True}}}
    with stub_health_server(200, body) as url:
        result = _run_watchdog({
            "WATCHDOG_HEALTH_URL": url,
            "WATCHDOG_HEALTH_JQ_FILTER": ".components.ci_runner.ok",
        })
    assert "jq_filter_invalid:null" in result.stdout


def test_jq_filter_syntax_error_returns_invalid(script_exists):
    """Edge: jq-Syntax-Error → DOWN:jq_filter_invalid:ERROR."""
    body = {"status": "ok"}
    with stub_health_server(200, body) as url:
        result = _run_watchdog({
            "WATCHDOG_HEALTH_URL": url,
            "WATCHDOG_HEALTH_JQ_FILTER": "this is not valid jq syntax !!!",
        })
    assert "jq_filter_invalid" in result.stdout


def test_alert_filter_expression_works(script_exists):
    """Schema-v1-Alternative: filtere alerts[] auf component → UP wenn keine critical-Alerts."""
    body = {
        "status": "critical",
        "components": {"ci_runner": {"ok": True}},
        "alerts": [
            {"code": "RUNNER_DOWN", "severity": "critical", "component": "github_runners"},
            {"code": "LOAD_HIGH", "severity": "critical", "component": "load"},
        ],
    }
    filter_expr = (
        '[.alerts[] | select(.component == "ci_runner" and .severity == "critical")] '
        '| length == 0'
    )
    with stub_health_server(503, body) as url:
        result = _run_watchdog({
            "WATCHDOG_HEALTH_URL": url,
            "WATCHDOG_HEALTH_JQ_FILTER": filter_expr,
        })
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK — healthy" in result.stdout


def test_default_mode_unchanged_without_filter(script_exists):
    """Backward-Compat: ohne Filter zaehlt HTTP-Status weiterhin."""
    body = {"status": "ok"}
    with stub_health_server(503, body) as url:
        result = _run_watchdog({"WATCHDOG_HEALTH_URL": url})
    assert "down (http_503)" in result.stdout


def test_default_mode_up_on_http_200_without_filter(script_exists):
    """Backward-Compat: HTTP 200 ohne Filter → UP."""
    body = {"status": "ok"}
    with stub_health_server(200, body) as url:
        result = _run_watchdog({"WATCHDOG_HEALTH_URL": url})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK — healthy" in result.stdout


def test_curl_unreachable_endpoint_returns_down(script_exists):
    """Edge: curl scheitert (Port unreachable) → DOWN:curl_failed."""
    # Bind+release einen Port damit definitiv nichts dort lauscht
    port = _free_port()
    url = f"http://127.0.0.1:{port}/health"
    result = _run_watchdog({
        "WATCHDOG_HEALTH_URL": url,
        "WATCHDOG_HEALTH_JQ_FILTER": ".components.ci_runner.ok",  # auch mit Filter
    })
    assert "curl_failed" in result.stdout
