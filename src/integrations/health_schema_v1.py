"""Health Schema v1 — Modell für ShadowOps Phase 5e Aggregator.

Quelle der Wahrheit: ZERODOX/docs/HEALTH_SCHEMA_V1.md

Genutzt vom phase_5e_health_aggregator.py zur Validierung der Responses
von 10.8.0.10:9100/health, https://dev.zerodox.de/api/internal/health,
https://zerodox.de/api/internal/health.

Bewusst dependency-frei (keine pydantic im shadowops-bot venv) —
verwendet stattdessen dataclasses + manuelle Parse-Validation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


HEALTH_SCHEMA_VERSION = "1.0"

HealthStatus = Literal["ok", "degraded", "critical"]
HealthRole = Literal["ci-runner", "web-prod", "web-dev"]
AlertSeverity = Literal["info", "warning", "critical"]

VALID_STATUSES: set[str] = {"ok", "degraded", "critical"}
VALID_ROLES: set[str] = {"ci-runner", "web-prod", "web-dev"}
VALID_SEVERITIES: set[str] = {"info", "warning", "critical"}


class HealthSchemaError(ValueError):
    """Wirft bei nicht parsebarem oder schema-violation Response."""


@dataclass
class HealthAlert:
    code: str
    severity: str
    component: str
    message: str

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "HealthAlert":
        if not isinstance(raw, dict):
            raise HealthSchemaError(f"alert nicht dict: {type(raw).__name__}")
        severity = raw.get("severity", "info")
        if severity not in VALID_SEVERITIES:
            raise HealthSchemaError(f"alert.severity invalid: {severity!r}")
        return cls(
            code=str(raw.get("code", "UNKNOWN")),
            severity=severity,
            component=str(raw.get("component", "unknown")),
            message=str(raw.get("message", "")),
        )


@dataclass
class HealthResponse:
    """Vollständige Schema-v1-Response.

    `components` und `alerts` werden roh als dict/list[dict] gespeichert,
    nicht in einzelne Komponenten-Klassen geparst — das macht den Aggregator
    forward-kompatibel mit zukünftigen optionalen Feldern.
    """

    schema_version: str
    host: str
    role: str
    timestamp: str
    uptime_seconds: int
    status: str
    components: dict[str, Any]
    alerts: list[HealthAlert]
    http_status: int = 0  # vom HTTP-Layer gesetzt
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any], http_status: int = 0) -> "HealthResponse":
        if not isinstance(raw, dict):
            raise HealthSchemaError(f"response nicht dict: {type(raw).__name__}")

        version = raw.get("schema_version")
        if version != HEALTH_SCHEMA_VERSION:
            raise HealthSchemaError(
                f"unexpected schema_version: {version!r} (erwartet {HEALTH_SCHEMA_VERSION!r})"
            )

        status = raw.get("status")
        if status not in VALID_STATUSES:
            raise HealthSchemaError(f"status invalid: {status!r}")

        role = raw.get("role")
        if role not in VALID_ROLES:
            raise HealthSchemaError(f"role invalid: {role!r}")

        components = raw.get("components", {})
        if not isinstance(components, dict):
            raise HealthSchemaError("components muss dict sein")

        alerts_raw = raw.get("alerts", [])
        if not isinstance(alerts_raw, list):
            raise HealthSchemaError("alerts muss list sein")
        alerts = [HealthAlert.from_dict(a) for a in alerts_raw]

        return cls(
            schema_version=version,
            host=str(raw.get("host", "")),
            role=role,
            timestamp=str(raw.get("timestamp", "")),
            uptime_seconds=int(raw.get("uptime_seconds", 0)),
            status=status,
            components=components,
            alerts=alerts,
            http_status=http_status,
            raw=raw,
        )

    @property
    def is_critical(self) -> bool:
        return self.status == "critical"

    @property
    def is_degraded(self) -> bool:
        return self.status == "degraded"

    @property
    def is_ok(self) -> bool:
        return self.status == "ok"

    @property
    def critical_alerts(self) -> list[HealthAlert]:
        return [a for a in self.alerts if a.severity == "critical"]

    @property
    def warning_alerts(self) -> list[HealthAlert]:
        return [a for a in self.alerts if a.severity == "warning"]
