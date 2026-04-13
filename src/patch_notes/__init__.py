"""patch_notes v6 — Modulare Patch Notes Pipeline für ShadowOps Bot.

Public API:
    PatchNotePipeline  — State Machine Orchestrator
    PipelineContext    — Zentrales Datenobjekt
    retract_patch_notes — Rollback: Gesendete Messages löschen
"""

from patch_notes.pipeline import PatchNotePipeline
from patch_notes.context import PipelineContext, PipelineState
from patch_notes.stages.distribute import retract_patch_notes

__all__ = ['PatchNotePipeline', 'PipelineContext', 'PipelineState', 'retract_patch_notes']
