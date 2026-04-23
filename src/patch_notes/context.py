"""PipelineContext — Zentraler Datencontainer für die Patch Notes Pipeline v6."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import IntEnum


class PipelineState(IntEnum):
    """Zustandsmaschine der Pipeline — aufsteigende Reihenfolge."""

    PENDING = 0
    COLLECTING = 1
    CLASSIFYING = 2
    GENERATING = 3
    VALIDATING = 4
    DISTRIBUTING = 5
    COMPLETED = 6
    FAILED = 7


@dataclass
class PipelineContext:
    """Durchläuft alle Stufen der Pipeline und sammelt Ergebnisse."""

    # --- Input (Pflichtfelder) ---
    project: str
    project_config: dict
    raw_commits: list[dict]
    trigger: str  # "webhook" | "cron" | "manual" | "polling"

    # --- Stufe 1: COLLECT ---
    enriched_commits: list[dict] = field(default_factory=list)
    git_stats: dict = field(default_factory=dict)

    # --- Stufe 2: CLASSIFY ---
    groups: list[dict] = field(default_factory=list)
    version: str = ""
    version_source: str = ""
    team_credits: list[dict] = field(default_factory=list)
    update_size: str = "normal"
    previous_version_content: str = ""

    # --- Stufe 3: GENERATE ---
    prompt: str = ""
    ai_result: dict | str | None = None
    ai_engine_used: str = ""
    variant_id: str = ""
    generation_time_s: float = 0.0

    # --- Stufe 4: VALIDATE ---
    title: str = ""
    tldr: str = ""
    web_content: str = ""
    changes: list[dict] = field(default_factory=list)
    seo_keywords: list[str] = field(default_factory=list)
    fixes_applied: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    # --- Stufe 5: DISTRIBUTE ---
    sent_message_ids: list[list] = field(default_factory=list)

    # --- State Machine ---
    state: PipelineState = PipelineState.PENDING
    error: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    # Monotonic-Timestamp fuer Wall-Clock-Messung (Pipeline-Total-Time).
    # Nicht JSON-persistiert — wird beim Resume aus dem State neu gesetzt.
    pipeline_start_monotonic: float | None = None

    # --- Metriken ---
    metrics: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """JSON-serialisierbares Dict. Enum → int."""
        d = asdict(self)
        d["state"] = self.state.value
        return d

    @classmethod
    def from_dict(cls, data: dict) -> PipelineContext:
        """Reconstruct from JSON dict."""
        data = dict(data)
        data["state"] = PipelineState(data.get("state", 0))
        # Nur bekannte Felder akzeptieren
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
