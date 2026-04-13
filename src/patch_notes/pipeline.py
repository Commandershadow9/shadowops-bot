"""PatchNotePipeline — State Machine Orchestrator."""
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from patch_notes.context import PipelineContext, PipelineState
from patch_notes.state import PipelineStateStore

logger = logging.getLogger('shadowops')


class PatchNotePipeline:
    def __init__(self, data_dir: Path, bot=None):
        self.state_store = PipelineStateStore(data_dir)
        self.bot = bot

    async def run(self, ctx: PipelineContext) -> PipelineContext:
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
                logger.error(f"[v6] {ctx.project} FAILED in {target_state.name}: {e}")
                raise

        ctx.state = PipelineState.COMPLETED
        ctx.completed_at = datetime.now(timezone.utc).isoformat()
        ctx.metrics["pipeline_total_time_s"] = round(time.monotonic() - pipeline_start, 2)
        self.state_store.persist(ctx)
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
