"""PatchNotePipeline — State Machine Orchestrator."""
import asyncio
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from patch_notes.context import PipelineContext, PipelineState
from patch_notes.state import PipelineStateStore

logger = logging.getLogger('shadowops')

# Concurrency Lock — verhindert parallele AI-Generierung (Webhook + Polling Race)
_pipeline_lock = asyncio.Lock()

# Circuit Breaker — stoppt bei zu vielen AI-Fehlern
_ai_failures: dict[str, list[float]] = {}  # project → [timestamps]
_AI_CB_THRESHOLD = 5
_AI_CB_TIMEOUT = 3600  # 1 Stunde


def _check_circuit_breaker(project: str) -> bool:
    """True wenn Circuit Breaker OFFEN ist (zu viele Fehler)."""
    now = time.monotonic()
    failures = _ai_failures.get(project, [])
    # Alte Failures entfernen
    failures = [t for t in failures if now - t < _AI_CB_TIMEOUT]
    _ai_failures[project] = failures
    return len(failures) >= _AI_CB_THRESHOLD


def _record_ai_failure(project: str) -> None:
    _ai_failures.setdefault(project, []).append(time.monotonic())


def _record_ai_success(project: str) -> None:
    _ai_failures.pop(project, None)


class PatchNotePipeline:
    def __init__(self, data_dir: Path, bot=None):
        self.state_store = PipelineStateStore(data_dir)
        self.bot = bot

    async def run(self, ctx: PipelineContext) -> PipelineContext:
        # Circuit Breaker Check
        if _check_circuit_breaker(ctx.project):
            raise RuntimeError(
                f"Circuit Breaker OFFEN für {ctx.project} — "
                f"{_AI_CB_THRESHOLD}+ Fehler in der letzten Stunde"
            )

        async with _pipeline_lock:
            return await self._run_locked(ctx)

    async def _run_locked(self, ctx: PipelineContext) -> PipelineContext:
        from patch_notes.stages.collect import collect
        from patch_notes.stages.classify import classify
        from patch_notes.stages.generate import generate
        from patch_notes.stages.validate import validate
        from patch_notes.stages.distribute import distribute

        stages = [
            (PipelineState.COLLECTING, collect),
            (PipelineState.CLASSIFYING, classify),
            (PipelineState.GENERATING, generate),
            (PipelineState.VALIDATING, validate),
            (PipelineState.DISTRIBUTING, distribute),
        ]

        ctx.started_at = datetime.now(timezone.utc).isoformat()
        pipeline_start = time.monotonic()
        # In den Context schreiben, damit die Distribute-Stage die bisherige
        # Laufzeit fuer den Metric-Output ableiten kann (fix: pipeline_total_time_s
        # war in Metrics immer 0, weil _log_metrics VOR dem Loop-Ende lief).
        ctx.pipeline_start_monotonic = pipeline_start

        for target_state, stage_fn in stages:
            if ctx.state >= target_state:
                logger.info(f"[v6] Skipping {target_state.name} (already at {PipelineState(ctx.state).name})")
                continue
            ctx.state = target_state
            self.state_store.persist(ctx)
            logger.info(f"[v6] {ctx.project} → {target_state.name}")
            try:
                await stage_fn(ctx, self.bot)
            except Exception as e:
                ctx.state = PipelineState.FAILED
                ctx.error = f"{target_state.name}: {e}"
                self.state_store.persist(ctx)
                if target_state == PipelineState.GENERATING:
                    _record_ai_failure(ctx.project)
                logger.error(f"[v6] {ctx.project} FAILED in {target_state.name}: {e}")
                raise

        ctx.state = PipelineState.COMPLETED
        ctx.completed_at = datetime.now(timezone.utc).isoformat()
        ctx.metrics["pipeline_total_time_s"] = round(time.monotonic() - pipeline_start, 2)
        self.state_store.persist(ctx)
        _record_ai_success(ctx.project)
        logger.info(f"[v6] {ctx.project} v{ctx.version} COMPLETED in {ctx.metrics['pipeline_total_time_s']}s")
        return ctx

    async def resume_incomplete(self) -> list[PipelineContext]:
        results = []
        for ctx in self.state_store.get_incomplete_runs():
            logger.info(f"[v6] Resuming {ctx.project} from {PipelineState(ctx.state).name}")
            try:
                result = await self.run(ctx)
                results.append(result)
            except Exception as e:
                logger.error(f"[v6] Resume failed for {ctx.project}: {e}")
        return results
