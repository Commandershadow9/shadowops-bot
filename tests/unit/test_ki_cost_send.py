"""Test fuer ki-cost-watchdog.py send_discord — 429-Resilienz (#293).

Python-Pendant zur Bash-Lib scripts/lib/discord-send.sh: bei HTTP 429 genau 1
Retry (Retry-After-respektierend), bei anderen Fehlern kein Retry. urllib wird
gemockt; Jitter via KICOST_MAX_JITTER_MS=0 deaktiviert.
"""

import importlib.util
from email.message import Message
from pathlib import Path
from urllib import error

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "ki-cost-watchdog.py"


def _load():
    spec = importlib.util.spec_from_file_location("ki_cost_watchdog_send", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Resp:
    def __init__(self, code):
        self._code = code

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def getcode(self):
        return self._code


def _http_error_429(retry_after="0"):
    h = Message()
    h["Retry-After"] = retry_after
    return error.HTTPError("http://x", 429, "Too Many Requests", h, None)


def test_429_dann_erfolg_genau_ein_retry(monkeypatch):
    mod = _load()
    monkeypatch.setenv("KICOST_MAX_JITTER_MS", "0")
    monkeypatch.setenv("DISCORD_RETRY_CAP", "2")
    calls = {"n": 0}

    def fake_urlopen(req, timeout=10):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _http_error_429("0")
        return _Resp(204)

    monkeypatch.setattr(mod.request, "urlopen", fake_urlopen)
    assert mod.send_discord("http://x", {"content": "x"}) is True
    assert calls["n"] == 2  # erster 429 + genau 1 Retry


def test_500_kein_retry(monkeypatch):
    mod = _load()
    monkeypatch.setenv("KICOST_MAX_JITTER_MS", "0")
    calls = {"n": 0}

    def fake_urlopen(req, timeout=10):
        calls["n"] += 1
        raise error.HTTPError("http://x", 500, "err", Message(), None)

    monkeypatch.setattr(mod.request, "urlopen", fake_urlopen)
    assert mod.send_discord("http://x", {"content": "x"}) is False
    assert calls["n"] == 1  # KEIN Retry bei Nicht-429


def test_dauerhaft_429_genau_ein_retry(monkeypatch):
    mod = _load()
    monkeypatch.setenv("KICOST_MAX_JITTER_MS", "0")
    monkeypatch.setenv("DISCORD_RETRY_CAP", "1")
    calls = {"n": 0}

    def fake_urlopen(req, timeout=10):
        calls["n"] += 1
        raise _http_error_429("0")

    monkeypatch.setattr(mod.request, "urlopen", fake_urlopen)
    assert mod.send_discord("http://x", {"content": "x"}) is False
    assert calls["n"] == 2  # erster Versuch + genau 1 Retry
