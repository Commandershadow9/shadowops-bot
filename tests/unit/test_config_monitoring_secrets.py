"""Tests für den Single-Source-Monitoring-Secret-Loader (#277-Folge, Plan 3.5).

Der Bot lädt CRON_API_KEY/ZERODOX_AGENT_API_KEY/AKQUISE_AI_BEARER_TOKEN aus
ZERODOX/.env (Single-Source) statt aus einem Duplikat in bot/.env. FAIL-CLOSED:
fehlende Quelle/Keys → lauter Alert, nie still ein Default.
"""
import os
from unittest.mock import patch

from src.utils.config import Config


def _fresh_loader():
    """Config-Instanz ohne __init__ (umgeht config.yaml-Load), nur der Loader."""
    return Config.__new__(Config)


def test_loads_keys_from_zerodox_env(tmp_path, monkeypatch):
    src = tmp_path / "zerodox.env"
    src.write_text("CRON_API_KEY=ck\nZERODOX_AGENT_API_KEY=zk\nAKQUISE_AI_BEARER_TOKEN=ak\nUNRELATED=x\n")
    for k in Config._MONITORING_SECRET_KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ZERODOX_ENV_PATH", str(src))
    _fresh_loader()._load_monitoring_secrets()
    assert os.environ["CRON_API_KEY"] == "ck"
    assert os.environ["ZERODOX_AGENT_API_KEY"] == "zk"
    assert os.environ["AKQUISE_AI_BEARER_TOKEN"] == "ak"
    # NUR die Monitoring-Keys werden geladen, nicht beliebige andere
    assert "UNRELATED" not in os.environ


def test_existing_env_value_takes_precedence(tmp_path, monkeypatch):
    """override=False: ein bewusst in bot/.env (os.environ) gesetzter Wert
    behält Vorrang (Notfall/Test-Override)."""
    src = tmp_path / "zerodox.env"
    src.write_text("CRON_API_KEY=from_zerodox\n")
    monkeypatch.setenv("ZERODOX_ENV_PATH", str(src))
    monkeypatch.setenv("CRON_API_KEY", "from_bot_env")
    monkeypatch.delenv("ZERODOX_AGENT_API_KEY", raising=False)
    monkeypatch.delenv("AKQUISE_AI_BEARER_TOKEN", raising=False)
    with patch.object(Config, "_alert_missing_secrets"):  # die 2 fehlenden Keys nicht alarmieren
        _fresh_loader()._load_monitoring_secrets()
    assert os.environ["CRON_API_KEY"] == "from_bot_env"  # NICHT überschrieben


def test_missing_source_alerts(tmp_path, monkeypatch):
    """Fehlende Quelle → fail-closed Alert (nicht still)."""
    for k in Config._MONITORING_SECRET_KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ZERODOX_ENV_PATH", str(tmp_path / "does-not-exist.env"))
    with patch.object(Config, "_alert_missing_secrets") as alert:
        _fresh_loader()._load_monitoring_secrets()
    alert.assert_called_once()
    missing_arg = alert.call_args[0][0]
    assert set(missing_arg) == set(Config._MONITORING_SECRET_KEYS)


def test_partial_missing_key_alerts(tmp_path, monkeypatch):
    """Quelle da, aber ein Key fehlt → Alert nur für den fehlenden."""
    src = tmp_path / "zerodox.env"
    src.write_text("CRON_API_KEY=ck\nZERODOX_AGENT_API_KEY=zk\n")  # AKQUISE fehlt
    for k in Config._MONITORING_SECRET_KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ZERODOX_ENV_PATH", str(src))
    with patch.object(Config, "_alert_missing_secrets") as alert:
        _fresh_loader()._load_monitoring_secrets()
    alert.assert_called_once()
    assert alert.call_args[0][0] == ["AKQUISE_AI_BEARER_TOKEN"]


def test_all_present_no_alert(tmp_path, monkeypatch):
    src = tmp_path / "zerodox.env"
    src.write_text("CRON_API_KEY=ck\nZERODOX_AGENT_API_KEY=zk\nAKQUISE_AI_BEARER_TOKEN=ak\n")
    for k in Config._MONITORING_SECRET_KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ZERODOX_ENV_PATH", str(src))
    with patch.object(Config, "_alert_missing_secrets") as alert:
        _fresh_loader()._load_monitoring_secrets()
    alert.assert_not_called()
