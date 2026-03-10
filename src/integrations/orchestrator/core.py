"""
RemediationOrchestrator — Hauptklasse mit allen Mixins
"""

import asyncio
import logging
from typing import Dict, List, Optional

from .models import SecurityEventBatch, RemediationPlan
from .batch_mixin import BatchManagerMixin
from .planner_mixin import PlannerMixin
from .discord_mixin import DiscordUIMixin
from .executor_mixin import ExecutorMixin
from .recovery_mixin import RecoveryMixin

logger = logging.getLogger('shadowops')


class RemediationOrchestrator(BatchManagerMixin, PlannerMixin, DiscordUIMixin, ExecutorMixin, RecoveryMixin):
    """
    Master Coordinator für alle Security Remediations

    Verhindert Race Conditions durch:
    - Event Batching (sammelt Events über 10s)
    - Koordinierte KI-Analyse (ALLE Events zusammen)
    - Single Approval Flow
    - Sequentielle Ausführung mit System-Locks
    """

    def __init__(self, ai_service=None, self_healing_coordinator=None, approval_manager=None,
                 bot=None, discord_logger=None, config=None, **kwargs):
        """
        Initialize the orchestrator.

        Args:
            ai_service: AI service instance for plan generation
            self_healing_coordinator: Self healing coordinator instance
            approval_manager: Approval manager for remediation flows
            bot: Discord bot reference for messaging
            discord_logger: Discord logger helper
            config: Loaded Config object (required for channel lookups)
            **kwargs: legacy keywords (self_healing, config)
        """
        # Support legacy keyword `self_healing`
        if self_healing_coordinator is None:
            self_healing_coordinator = kwargs.get('self_healing')
        if config is None:
            config = kwargs.get('config')

        self.ai_service = ai_service
        self.self_healing = self_healing_coordinator
        self.approval_manager = approval_manager or getattr(self.self_healing, 'approval_manager', None)
        self.bot = bot  # Discord Bot für Approval Messages
        self.discord_logger = discord_logger
        self.config = config

        # Event Batching
        default_window = 10
        default_batch_size = 10
        if self.config and getattr(self.config, "auto_remediation", None):
            auto_cfg = self.config.auto_remediation or {}
            default_window = int(auto_cfg.get('collection_window_seconds', default_window) or default_window)
            default_batch_size = int(auto_cfg.get('max_batch_size', default_batch_size) or default_batch_size)

        self.collection_window_seconds = default_window  # Sammelt Events über 10 Sekunden
        self.max_batch_size = default_batch_size  # Max 10 Events pro Batch (Server-Schonung)
        self.current_batch: Optional[SecurityEventBatch] = None
        self.batch_lock = asyncio.Lock()
        self.collection_task: Optional[asyncio.Task] = None

        # Execution Lock (nur 1 Remediation zur Zeit!)
        self.execution_lock = asyncio.Lock()
        self.currently_executing: Optional[str] = None

        # Batch Queue
        self.pending_batches: List[SecurityEventBatch] = []
        self.completed_batches: List[SecurityEventBatch] = []

        # NEW: Event History for Learning
        self.event_history: Dict[str, List[Dict]] = {}  # {event_signature: [attempts]}
        self.history_file = 'logs/event_history.json'
        self._load_event_history()

        logger.info("🎯 Remediation Orchestrator initialisiert")
        logger.info(f"   📊 Batching Window: {self.collection_window_seconds}s")
        logger.info(f"   📦 Max Batch Size: {self.max_batch_size} Events (Server-Schonung)")
        logger.info("   🔒 Sequential Execution Mode: ON")
