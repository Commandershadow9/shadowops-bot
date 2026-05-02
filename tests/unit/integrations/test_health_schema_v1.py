"""Unit-Tests fuer integrations.health_schema_v1.HealthResponse.

Verifiziert das Parsing + Validierung der Schema-v1-Responses, die der
Phase-5e-Aggregator von 3 Hosts pollt.
"""

from __future__ import annotations

import pytest

from integrations.health_schema_v1 import (
    HEALTH_SCHEMA_VERSION,
    HealthAlert,
    HealthResponse,
    HealthSchemaError,
)


def _runner_payload(
    *,
    status: str = "ok",
    alerts: list | None = None,
    schema_version: str = HEALTH_SCHEMA_VERSION,
) -> dict:
    return {
        "schema_version": schema_version,
        "host": "shadowlinux-runner",
        "role": "ci-runner",
        "timestamp": "2026-05-02T00:01:23Z",
        "uptime_seconds": 12345,
        "status": status,
        "components": {
            "wireguard": {
                "ok": True,
                "peers_active": 2,
                "peers_total": 2,
                "last_handshake_seconds_ago": 23,
            },
            "github_runners": {
                "ok": True,
                "services_active": 3,
                "services_configured": 3,
            },
            "disk": {"used_percent": 5, "free_gb": 220, "total_gb": 240},
            "memory": {"used_percent": 12, "available_mb": 14000, "total_mb": 16000},
            "load": {"1min": 0.5, "5min": 0.3, "15min": 0.2, "cpu_count": 4},
        },
        "alerts": alerts or [],
    }


def test_parse_runner_ok() -> None:
    response = HealthResponse.from_dict(_runner_payload(), http_status=200)
    assert response.is_ok
    assert not response.is_critical
    assert response.role == "ci-runner"
    assert response.host == "shadowlinux-runner"
    assert response.uptime_seconds == 12345
    assert "wireguard" in response.components
    assert response.alerts == []
    assert response.http_status == 200


def test_parse_with_warning_alert() -> None:
    payload = _runner_payload(
        status="degraded",
        alerts=[
            {
                "code": "DISK_HIGH",
                "severity": "warning",
                "component": "disk",
                "message": "Disk usage 82%",
            }
        ],
    )
    response = HealthResponse.from_dict(payload, http_status=200)
    assert response.is_degraded
    assert len(response.warning_alerts) == 1
    assert len(response.critical_alerts) == 0
    assert response.warning_alerts[0].code == "DISK_HIGH"


def test_parse_with_critical_alert() -> None:
    payload = _runner_payload(
        status="critical",
        alerts=[
            {
                "code": "DB_DOWN",
                "severity": "critical",
                "component": "database",
                "message": "connection refused",
            }
        ],
    )
    response = HealthResponse.from_dict(payload, http_status=503)
    assert response.is_critical
    assert len(response.critical_alerts) == 1


def test_reject_unknown_schema_version() -> None:
    with pytest.raises(HealthSchemaError, match="schema_version"):
        HealthResponse.from_dict(_runner_payload(schema_version="2.0"))


def test_reject_invalid_status() -> None:
    payload = _runner_payload()
    payload["status"] = "broken"
    with pytest.raises(HealthSchemaError, match="status"):
        HealthResponse.from_dict(payload)


def test_reject_invalid_role() -> None:
    payload = _runner_payload()
    payload["role"] = "frontend"
    with pytest.raises(HealthSchemaError, match="role"):
        HealthResponse.from_dict(payload)


def test_reject_non_dict_components() -> None:
    payload = _runner_payload()
    payload["components"] = "not-a-dict"
    with pytest.raises(HealthSchemaError, match="components"):
        HealthResponse.from_dict(payload)


def test_reject_non_list_alerts() -> None:
    payload = _runner_payload()
    payload["alerts"] = "not-a-list"
    with pytest.raises(HealthSchemaError, match="alerts"):
        HealthResponse.from_dict(payload)


def test_alert_invalid_severity_raises() -> None:
    payload = _runner_payload(alerts=[{
        "code": "X", "severity": "WAT", "component": "y", "message": "z",
    }])
    with pytest.raises(HealthSchemaError, match="severity"):
        HealthResponse.from_dict(payload)


def test_web_prod_payload() -> None:
    """Web-Prod hat database + redis statt wireguard + github_runners."""
    payload = {
        "schema_version": "1.0",
        "host": "zerodox-web-prod",
        "role": "web-prod",
        "timestamp": "2026-05-02T00:05:00Z",
        "uptime_seconds": 7200,
        "status": "ok",
        "components": {
            "database": {"latency_ms": 8.4, "ok": True, "pool_saturation_percent": 12},
            "redis": {"latency_ms": 0.6, "ok": True, "connected_clients": 4},
            "disk": {"used_percent": 30, "free_gb": 140, "total_gb": 200},
            "memory": {"used_percent": 45, "available_mb": 4400, "total_mb": 8000},
            "load": {"1min": 0.8, "5min": 0.6, "15min": 0.5, "cpu_count": 6},
        },
        "alerts": [],
    }
    response = HealthResponse.from_dict(payload, http_status=200)
    assert response.role == "web-prod"
    assert "database" in response.components
    assert "redis" in response.components
    assert response.is_ok


def test_health_alert_from_dict() -> None:
    raw = {
        "code": "MEMORY_HIGH",
        "severity": "warning",
        "component": "memory",
        "message": "Memory usage 87%",
    }
    alert = HealthAlert.from_dict(raw)
    assert alert.code == "MEMORY_HIGH"
    assert alert.severity == "warning"
