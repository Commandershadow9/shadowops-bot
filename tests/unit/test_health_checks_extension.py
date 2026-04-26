"""
Unit-Tests fuer die Enterprise-Health-Check-Erweiterung im ProjectMonitor
(Phase 5b, Issue #278).

Getestet werden die 5 neuen async-Methoden:
- _check_disk_space
- _check_memory_usage
- _check_container_restart_count
- _check_ssl_cert_expiry
- _check_backup_freshness

sowie die zentrale Anti-Spam-Logik (_send_health_alert, Cooldown,
Recovery via _clear_health_alert_cooldown).
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from collections import namedtuple
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.integrations.project_monitor import (
    HEALTH_CHECK_DEFAULTS,
    ProjectMonitor,
    ProjectStatus,
)
from src.utils.embeds import Severity


# ════════════════════════════════════════════════════════════════════════
# Helpers
# ════════════════════════════════════════════════════════════════════════


def _build_monitor(
    *,
    project_name: str = "zerodox",
    project_path: str | None = None,
    container: str | None = "zerodox-web",
    url: str = "https://zerodox.de/api/health",
    thresholds: dict | None = None,
) -> tuple[ProjectMonitor, ProjectStatus, MagicMock]:
    """Baut einen ProjectMonitor mit gemockter Discord-Bot-Instanz."""
    monitor_cfg: dict = {
        "enabled": True,
        "url": url,
        "expected_status": 200,
        "check_interval": 60,
    }
    if container:
        monitor_cfg["container"] = container
    if thresholds:
        monitor_cfg["thresholds"] = thresholds

    project_cfg: dict = {
        "enabled": True,
        "tag": f"📘 [{project_name.upper()}]",
        "monitor": monitor_cfg,
    }
    if project_path:
        project_cfg["path"] = project_path

    config = MagicMock()
    config.projects = {project_name: project_cfg}
    config.customer_status_channel = 11111
    config.customer_alerts_channel = 22222
    config.channels = {
        "critical": 1441655480840617994,
        "bot_status": 1441655486981214309,
        "backups": 1486479593602023486,
    }

    bot = MagicMock()
    channel = AsyncMock()
    channel.send = AsyncMock()
    bot.get_channel = MagicMock(return_value=channel)

    monitor = ProjectMonitor(bot, config)
    project = monitor.projects[project_name]
    return monitor, project, channel


def _disk_usage(total_gb: float, free_gb: float):
    """Erzeugt ein shutil.disk_usage-aehnliches Tuple."""
    Usage = namedtuple("Usage", ["total", "used", "free"])
    total = int(total_gb * (1024 ** 3))
    free = int(free_gb * (1024 ** 3))
    used = total - free
    return Usage(total=total, used=used, free=free)


# ════════════════════════════════════════════════════════════════════════
# Test 1: Disk-Space < 15% triggert Alert
# ════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_disk_space_below_threshold_triggers_alert(tmp_path):
    monitor, project, channel = _build_monitor(project_path=str(tmp_path))

    # 100 GB total, 5 GB frei -> 5% frei (< 15% Default-Schwelle)
    with patch("shutil.disk_usage", return_value=_disk_usage(100.0, 5.0)):
        await monitor._check_disk_space(project)

    channel.send.assert_called_once()
    sent_embed = channel.send.call_args.kwargs["embed"]
    # Severity CRITICAL -> roter Color-Code
    assert sent_embed.color.value == Severity.CRITICAL.color
    assert "Disk-Space niedrig" in sent_embed.title


# ════════════════════════════════════════════════════════════════════════
# Test 2: Disk-Space >= 15% triggert KEIN Alert
# ════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_disk_space_above_threshold_no_alert(tmp_path):
    monitor, project, channel = _build_monitor(project_path=str(tmp_path))

    # 100 GB total, 50 GB frei -> 50% frei
    with patch("shutil.disk_usage", return_value=_disk_usage(100.0, 50.0)):
        await monitor._check_disk_space(project)

    channel.send.assert_not_called()


# ════════════════════════════════════════════════════════════════════════
# Test 3: Memory > 90% triggert Alert
# ════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_memory_usage_above_threshold_triggers_alert(tmp_path):
    monitor, project, channel = _build_monitor(project_path=str(tmp_path))

    # docker stats liefert "95.50%"
    fake_proc = MagicMock()
    fake_proc.communicate = AsyncMock(return_value=(b"95.50%\n", b""))
    fake_proc.returncode = 0

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=fake_proc)):
        await monitor._check_memory_usage(project)

    channel.send.assert_called_once()
    sent_embed = channel.send.call_args.kwargs["embed"]
    assert "Memory hoch" in sent_embed.title
    assert sent_embed.color.value == Severity.CRITICAL.color


@pytest.mark.asyncio
async def test_memory_usage_below_threshold_no_alert(tmp_path):
    monitor, project, channel = _build_monitor(project_path=str(tmp_path))

    fake_proc = MagicMock()
    fake_proc.communicate = AsyncMock(return_value=(b"42.10%\n", b""))
    fake_proc.returncode = 0

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=fake_proc)):
        await monitor._check_memory_usage(project)

    channel.send.assert_not_called()


# ════════════════════════════════════════════════════════════════════════
# Test 4: SSL-Cert < 30 Tage triggert Alert
# ════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_ssl_cert_below_threshold_triggers_alert(tmp_path):
    monitor, project, channel = _build_monitor(project_path=str(tmp_path))

    # Cert laeuft in 10 Tagen ab -> Alert (Schwelle: 30 Tage)
    expiry = datetime.now(timezone.utc) + timedelta(days=10)
    not_after_str = expiry.strftime("%b %d %H:%M:%S %Y GMT")
    fake_cert = {"notAfter": not_after_str}

    monitor._fetch_peer_cert = AsyncMock(return_value=fake_cert)
    await monitor._check_ssl_cert_expiry(project)

    channel.send.assert_called_once()
    sent_embed = channel.send.call_args.kwargs["embed"]
    assert "SSL-Zertifikat" in sent_embed.title
    # < 7 Tage waere HIGH, 10 Tage -> MEDIUM
    assert sent_embed.color.value == Severity.MEDIUM.color


# ════════════════════════════════════════════════════════════════════════
# Test 5: SSL-Cert > 30 Tage triggert KEIN Alert
# ════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_ssl_cert_above_threshold_no_alert(tmp_path):
    monitor, project, channel = _build_monitor(project_path=str(tmp_path))

    expiry = datetime.now(timezone.utc) + timedelta(days=90)
    not_after_str = expiry.strftime("%b %d %H:%M:%S %Y GMT")
    fake_cert = {"notAfter": not_after_str}

    monitor._fetch_peer_cert = AsyncMock(return_value=fake_cert)
    await monitor._check_ssl_cert_expiry(project)

    channel.send.assert_not_called()


# ════════════════════════════════════════════════════════════════════════
# Test 6: Backup aelter als 25h triggert Alert
# ════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_backup_freshness_old_triggers_alert(tmp_path):
    # Project-Path mit altem Backup-File anlegen
    backup_dir = tmp_path / "backups" / "daily"
    backup_dir.mkdir(parents=True)
    old_backup = backup_dir / "zerodox-2026-04-24.sql.gz"
    old_backup.write_text("dummy")

    # Datei auf 30h alt setzen
    thirty_hours_ago = (datetime.now(timezone.utc) - timedelta(hours=30)).timestamp()
    os.utime(old_backup, (thirty_hours_ago, thirty_hours_ago))

    monitor, project, channel = _build_monitor(project_path=str(tmp_path))

    await monitor._check_backup_freshness(project)

    channel.send.assert_called_once()
    sent_embed = channel.send.call_args.kwargs["embed"]
    assert "Backup veraltet" in sent_embed.title
    assert sent_embed.color.value == Severity.HIGH.color


@pytest.mark.asyncio
async def test_backup_freshness_recent_no_alert(tmp_path):
    backup_dir = tmp_path / "backups" / "daily"
    backup_dir.mkdir(parents=True)
    recent_backup = backup_dir / "zerodox-2026-04-26.sql.gz"
    recent_backup.write_text("dummy")
    # mtime = jetzt (frisch)

    monitor, project, channel = _build_monitor(project_path=str(tmp_path))
    await monitor._check_backup_freshness(project)

    channel.send.assert_not_called()


# ════════════════════════════════════════════════════════════════════════
# Test 7: Cooldown verhindert Spam (zweiter Trigger in 60 Min -> kein Alert)
# ════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_cooldown_prevents_spam(tmp_path):
    monitor, project, channel = _build_monitor(project_path=str(tmp_path))

    # Erster Trigger: < 15% frei -> Alert geht raus
    with patch("shutil.disk_usage", return_value=_disk_usage(100.0, 5.0)):
        await monitor._check_disk_space(project)
    assert channel.send.call_count == 1

    # Zweiter Trigger: gleicher Zustand, aber Min-Intervall (5 Min) ist noch nicht
    # erreicht -> Check wird gar nicht erst ausgefuehrt. Wir resetten den
    # Min-Intervall-State, damit der naechste Check waehrend Cooldown laeuft.
    monitor._health_check_last_run.clear()
    with patch("shutil.disk_usage", return_value=_disk_usage(100.0, 5.0)):
        await monitor._check_disk_space(project)

    # Cooldown (60 Min) verhindert zweiten Send.
    assert channel.send.call_count == 1


# ════════════════════════════════════════════════════════════════════════
# Test 8: Recovery loescht Cooldown (Wert wieder OK -> naechster Trigger feuert sofort)
# ════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_recovery_clears_cooldown(tmp_path):
    monitor, project, channel = _build_monitor(project_path=str(tmp_path))

    # 1. Trigger: < 15% frei -> Alert + Cooldown gesetzt
    with patch("shutil.disk_usage", return_value=_disk_usage(100.0, 5.0)):
        await monitor._check_disk_space(project)
    assert channel.send.call_count == 1
    cooldown_key = f"{project.name}:disk_space"
    assert cooldown_key in monitor._health_check_alerts

    # 2. Recovery: > 15% frei -> Cooldown wird geloescht
    monitor._health_check_last_run.clear()
    with patch("shutil.disk_usage", return_value=_disk_usage(100.0, 80.0)):
        await monitor._check_disk_space(project)
    assert cooldown_key not in monitor._health_check_alerts
    # Kein neuer Send (Recovery loest selbst keinen Alert aus)
    assert channel.send.call_count == 1

    # 3. Erneuter Failure: < 15% frei -> Alert feuert SOFORT (Cooldown war geloescht)
    monitor._health_check_last_run.clear()
    with patch("shutil.disk_usage", return_value=_disk_usage(100.0, 5.0)):
        await monitor._check_disk_space(project)
    assert channel.send.call_count == 2


# ════════════════════════════════════════════════════════════════════════
# Bonus: Backup-Check ueberspringt Projekte ohne backups/daily-Pfad
# ════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_backup_freshness_skip_when_no_backup_dir(tmp_path):
    # Projekt-Pfad existiert, aber backups/daily NICHT.
    monitor, project, channel = _build_monitor(project_path=str(tmp_path))
    await monitor._check_backup_freshness(project)
    channel.send.assert_not_called()


# ════════════════════════════════════════════════════════════════════════
# Bonus: SSL-Cert < 7 Tage liefert Severity HIGH
# ════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_ssl_cert_below_7_days_severity_high(tmp_path):
    monitor, project, channel = _build_monitor(project_path=str(tmp_path))

    expiry = datetime.now(timezone.utc) + timedelta(days=3)
    not_after_str = expiry.strftime("%b %d %H:%M:%S %Y GMT")
    fake_cert = {"notAfter": not_after_str}

    monitor._fetch_peer_cert = AsyncMock(return_value=fake_cert)
    await monitor._check_ssl_cert_expiry(project)

    channel.send.assert_called_once()
    sent_embed = channel.send.call_args.kwargs["embed"]
    assert sent_embed.color.value == Severity.HIGH.color


# ════════════════════════════════════════════════════════════════════════
# Bonus: Memory-Check ohne Container-Config -> skip
# ════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_memory_usage_skip_when_no_container(tmp_path):
    monitor, project, channel = _build_monitor(project_path=str(tmp_path), container=None)
    # Sollte ohne docker-stats-Aufruf stille zurueckkehren.
    await monitor._check_memory_usage(project)
    channel.send.assert_not_called()


# ════════════════════════════════════════════════════════════════════════
# Phase 5c — App-Health-Checks via internal API
# ════════════════════════════════════════════════════════════════════════


def _build_monitor_with_api(
    *,
    api_key: str | None = "test-key-12345",
    thresholds: dict | None = None,
) -> tuple[ProjectMonitor, ProjectStatus, MagicMock]:
    """Wie _build_monitor, aber mit internal_health_endpoint konfiguriert."""
    monitor_cfg: dict = {
        "enabled": True,
        "url": "https://zerodox.de/api/health",
        "expected_status": 200,
        "check_interval": 60,
        "container": "zerodox-web",
        "internal_health_endpoint": "https://zerodox.de/api/internal/health-stats",
        "health_api_key_env": "ZERODOX_AGENT_API_KEY_TEST",
    }
    if thresholds:
        monitor_cfg["thresholds"] = thresholds

    project_cfg: dict = {
        "enabled": True,
        "tag": "📘 [ZERODOX]",
        "monitor": monitor_cfg,
    }

    config = MagicMock()
    config.projects = {"zerodox": project_cfg}
    config.customer_status_channel = 11111
    config.customer_alerts_channel = 22222
    config.channels = {
        "critical": 1441655480840617994,
        "bot_status": 1441655486981214309,
        "ci_zerodox": 1463512208083521577,
        "backups": 1486479593602023486,
    }

    bot = MagicMock()
    channel = AsyncMock()
    channel.send = AsyncMock()
    bot.get_channel = MagicMock(return_value=channel)

    if api_key:
        os.environ["ZERODOX_AGENT_API_KEY_TEST"] = api_key
    elif "ZERODOX_AGENT_API_KEY_TEST" in os.environ:
        del os.environ["ZERODOX_AGENT_API_KEY_TEST"]

    monitor = ProjectMonitor(bot, config)
    project = monitor.projects["zerodox"]
    return monitor, project, channel


@pytest.mark.asyncio
async def test_db_pool_saturation_above_threshold_triggers_alert(tmp_path):
    """DB-Pool > 80% → HIGH-Alert in ci-zerodox."""
    monitor, project, channel = _build_monitor_with_api()

    fake_stats = {
        "timestamp": "2026-04-26T20:00:00Z",
        "dbPool": {"active": 85, "idle": 5, "total": 90, "max": 100, "saturationPercent": 90},
        "failedLogins": {"count": 0, "windowMinutes": 5, "uniqueEmails": 0},
    }
    monitor._fetch_app_health_stats = AsyncMock(return_value=fake_stats)

    await monitor._check_db_pool_saturation(project)

    channel.send.assert_called_once()
    sent_embed = channel.send.call_args.kwargs["embed"]
    assert "DB-Pool-Saturation hoch" in sent_embed.title
    assert sent_embed.color.value == Severity.HIGH.color


@pytest.mark.asyncio
async def test_db_pool_saturation_below_threshold_no_alert(tmp_path):
    """DB-Pool <= 80% → kein Alert."""
    monitor, project, channel = _build_monitor_with_api()

    fake_stats = {
        "dbPool": {"active": 10, "idle": 5, "total": 15, "max": 100, "saturationPercent": 15},
        "failedLogins": {"count": 0, "windowMinutes": 5, "uniqueEmails": 0},
    }
    monitor._fetch_app_health_stats = AsyncMock(return_value=fake_stats)

    await monitor._check_db_pool_saturation(project)

    channel.send.assert_not_called()


@pytest.mark.asyncio
async def test_failed_login_rate_above_threshold_triggers_alert(tmp_path):
    """Failed-Login > 100 in 5 Min → HIGH-Alert in critical."""
    monitor, project, channel = _build_monitor_with_api()

    fake_stats = {
        "dbPool": {"saturationPercent": 5},
        "failedLogins": {"count": 200, "windowMinutes": 5, "uniqueEmails": 50},
    }
    monitor._fetch_app_health_stats = AsyncMock(return_value=fake_stats)

    await monitor._check_failed_login_rate(project)

    channel.send.assert_called_once()
    sent_embed = channel.send.call_args.kwargs["embed"]
    assert "Failed-Login-Rate hoch" in sent_embed.title


@pytest.mark.asyncio
async def test_failed_login_rate_extreme_volume_critical_severity(tmp_path):
    """Failed-Login > 500 (5x Threshold) → CRITICAL-Severity."""
    monitor, project, channel = _build_monitor_with_api()

    fake_stats = {
        "dbPool": {"saturationPercent": 5},
        "failedLogins": {"count": 600, "windowMinutes": 5, "uniqueEmails": 5},
    }
    monitor._fetch_app_health_stats = AsyncMock(return_value=fake_stats)

    await monitor._check_failed_login_rate(project)

    channel.send.assert_called_once()
    sent_embed = channel.send.call_args.kwargs["embed"]
    assert sent_embed.color.value == Severity.CRITICAL.color


@pytest.mark.asyncio
async def test_failed_login_rate_below_threshold_no_alert(tmp_path):
    """Failed-Login <= 100 → kein Alert."""
    monitor, project, channel = _build_monitor_with_api()

    fake_stats = {
        "dbPool": {"saturationPercent": 5},
        "failedLogins": {"count": 50, "windowMinutes": 5, "uniqueEmails": 30},
    }
    monitor._fetch_app_health_stats = AsyncMock(return_value=fake_stats)

    await monitor._check_failed_login_rate(project)

    channel.send.assert_not_called()


@pytest.mark.asyncio
async def test_app_health_check_skip_when_no_endpoint_configured(tmp_path):
    """Kein internal_health_endpoint → graceful skip ohne Crash."""
    # _build_monitor (ohne _with_api) hat KEIN internal_health_endpoint
    monitor, project, channel = _build_monitor(project_path=str(tmp_path))

    await monitor._check_db_pool_saturation(project)
    await monitor._check_failed_login_rate(project)

    channel.send.assert_not_called()


@pytest.mark.asyncio
async def test_app_health_check_skip_when_api_key_missing(tmp_path):
    """Kein API-Key in env → graceful skip."""
    monitor, project, channel = _build_monitor_with_api(api_key=None)

    # _fetch_app_health_stats sollte None returnen wenn Key fehlt
    result = await monitor._fetch_app_health_stats(project)
    assert result is None

    # Folge: Checks senden nichts
    await monitor._check_db_pool_saturation(project)
    await monitor._check_failed_login_rate(project)
    channel.send.assert_not_called()


@pytest.mark.asyncio
async def test_app_health_check_recovery_clears_cooldown(tmp_path):
    """Nach Recovery (Wert wieder OK) sollte Cooldown geloescht werden."""
    monitor, project, channel = _build_monitor_with_api()

    # 1. Trigger: hohe Saturation → Alert + Cooldown
    monitor._fetch_app_health_stats = AsyncMock(return_value={
        "dbPool": {"saturationPercent": 95},
        "failedLogins": {"count": 0, "windowMinutes": 5, "uniqueEmails": 0},
    })
    await monitor._check_db_pool_saturation(project)
    assert channel.send.call_count == 1
    cooldown_key = f"{project.name}:db_pool_saturation"
    assert cooldown_key in monitor._health_check_alerts

    # Min-Intervall zuruecksetzen, damit Recovery-Run direkt laeuft
    if cooldown_key in monitor._health_check_last_run:
        del monitor._health_check_last_run[cooldown_key]

    # 2. Recovery: niedrige Saturation → kein Alert + Cooldown geloescht
    monitor._fetch_app_health_stats = AsyncMock(return_value={
        "dbPool": {"saturationPercent": 10},
        "failedLogins": {"count": 0, "windowMinutes": 5, "uniqueEmails": 0},
    })
    await monitor._check_db_pool_saturation(project)
    # Channel.send wurde nur 1x gerufen (initial), nicht 2x
    assert channel.send.call_count == 1
    assert cooldown_key not in monitor._health_check_alerts
