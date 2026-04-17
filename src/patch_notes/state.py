"""Persistenter Pipeline-State — crash-resilient via JSON."""
import json
import logging
from pathlib import Path
from patch_notes.context import PipelineContext, PipelineState

logger = logging.getLogger('shadowops')


class PipelineStateStore:
    def __init__(self, data_dir: Path):
        self.runs_dir = data_dir / 'pipeline_runs'
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, project: str) -> Path:
        safe = project.replace('/', '_').replace(' ', '_')
        return self.runs_dir / f"{safe}.json"

    def persist(self, ctx: PipelineContext) -> None:
        path = self._path(ctx.project)
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(ctx.to_dict(), ensure_ascii=False, default=str))
        tmp.replace(path)  # Atomic rename

    def load(self, project: str) -> PipelineContext | None:
        path = self._path(project)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            return PipelineContext.from_dict(data)
        except Exception as e:
            logger.warning(f"Pipeline-State korrupt für {project}: {e}")
            return None

    def cleanup_completed(self) -> int:
        removed = 0
        for path in self.runs_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text())
                state = data.get("state", 0)
                if state in (PipelineState.COMPLETED.value, PipelineState.FAILED.value):
                    path.unlink()
                    removed += 1
            except Exception:
                pass
        return removed

    def get_incomplete_runs(self) -> list[PipelineContext]:
        runs = []
        for path in self.runs_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text())
                state = data.get("state", 0)
                if state not in (PipelineState.COMPLETED.value, PipelineState.FAILED.value, PipelineState.PENDING.value):
                    runs.append(PipelineContext.from_dict(data))
            except Exception:
                pass
        return runs
