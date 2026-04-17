"""patch_notes v6 — Modulare Patch Notes Pipeline für ShadowOps Bot.

Public API:
    PatchNotePipeline    — State Machine Orchestrator
    PipelineContext      — Zentrales Datenobjekt
    generate_release     — Einfachster Einstieg: Projekt-Name → Release
    retract_patch_notes  — Rollback: Gesendete Messages löschen
"""

from patch_notes.pipeline import PatchNotePipeline
from patch_notes.context import PipelineContext, PipelineState
from patch_notes.stages.distribute import retract_patch_notes


async def generate_release(project: str, project_config: dict,
                           bot=None, trigger: str = 'manual',
                           commits: list[dict] | None = None) -> PipelineContext:
    """Einfachster Einstieg: Generiere einen Release.

    Wenn keine Commits übergeben werden, holt die Pipeline
    automatisch ALLE Commits seit dem letzten Release aus Git.

    Args:
        project: Projektname (z.B. 'shadowops-bot')
        project_config: Config-Dict aus config.yaml
        bot: Discord Bot-Instanz (für Stufe 5: Discord-Sending)
        trigger: 'manual' | 'cron' | 'webhook' | 'polling'
        commits: Explizite Commit-Liste (optional — wenn leer, aus Git geholt)

    Returns:
        PipelineContext mit allen Ergebnissen
    """
    from pathlib import Path

    ctx = PipelineContext(
        project=project,
        project_config=project_config,
        raw_commits=commits or [],
        trigger=trigger,
    )

    data_dir = Path(__file__).resolve().parent.parent.parent / 'data'
    pipeline = PatchNotePipeline(data_dir=data_dir, bot=bot)
    return await pipeline.run(ctx)


__all__ = [
    'PatchNotePipeline', 'PipelineContext', 'PipelineState',
    'generate_release', 'retract_patch_notes',
]
