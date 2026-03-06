"""
SmartQueue — Intelligente Auftragssteuerung fuer ShadowOps v4

Intelligente Queue mit:
- Parallelem Analyse-Pool (Semaphore-gesteuert)
- Seriellem Fix-Lock (nur ein Fix gleichzeitig)
- Circuit Breaker bei aufeinanderfolgenden Fehlern
- Batch-Modus-Erkennung bei Event-Bursts
- Prioritaets-basierter Fix-Queue (CRITICAL vor LOW)
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("shadowops")


# ============================================================================
# ENUMS & CONSTANTS
# ============================================================================

class QueueItemType(Enum):
    """Typ eines Queue-Eintrags."""
    ANALYSIS = "analysis"
    FIX = "fix"


SEVERITY_PRIORITY: Dict[str, int] = {
    "CRITICAL": 0,
    "HIGH": 1,
    "MEDIUM": 2,
    "LOW": 3,
}


# ============================================================================
# DATACLASS
# ============================================================================

@dataclass
class QueueItem:
    """Ein einzelner Auftrag in der SmartQueue.

    Attributes:
        item_type: ANALYSIS oder FIX
        event: Event-Dict mit mindestens 'severity'
        callback: Async-Funktion die mit dem Event aufgerufen wird
        priority: 0=CRITICAL, 1=HIGH, 2=MEDIUM, 3=LOW
        created_at: Erstellungszeitpunkt
        result: Ergebnis nach Abschluss
    """

    item_type: QueueItemType
    event: Dict[str, Any]
    callback: Callable
    priority: int
    created_at: datetime = field(default_factory=datetime.utcnow)
    result: Optional[Dict] = None

    @property
    def severity(self) -> str:
        """Severity aus dem Event-Dict (Default: MEDIUM)."""
        return self.event.get("severity", "MEDIUM")


# ============================================================================
# SMARTQUEUE
# ============================================================================

class SmartQueue:
    """Intelligente Queue mit Analyse-Pool und seriellem Fix-Lock.

    Config-Parameter (aus Dict):
        max_analysis_parallel: Maximale parallele Analysen (Default: 3)
        batch_threshold: Anzahl Events fuer Batch-Modus (Default: 5)
        batch_window: Zeitfenster fuer Batch-Erkennung in Sekunden (Default: 10)
        circuit_breaker_threshold: Fehler bis Circuit Breaker oeffnet (Default: 5)
        circuit_breaker_timeout: Sekunden bis Circuit Breaker resettet (Default: 3600)
    """

    def __init__(self, config: Dict[str, Any]) -> None:
        # --- Config ---
        self.max_analysis_parallel: int = config.get("max_analysis_parallel", 3)
        self.batch_threshold: int = config.get("batch_threshold", 5)
        self.batch_window: int = config.get("batch_window", 10)
        self.circuit_breaker_threshold: int = config.get("circuit_breaker_threshold", 5)
        self.circuit_breaker_timeout: int = config.get("circuit_breaker_timeout", 3600)

        # --- Analyse-Pool ---
        self.active_analyses: int = 0
        self.analysis_semaphore: asyncio.Semaphore = asyncio.Semaphore(
            self.max_analysis_parallel
        )

        # --- Fix-Lock ---
        self.fix_locked: bool = False
        self.fix_lock: asyncio.Lock = asyncio.Lock()
        self.fix_queue: List[QueueItem] = []

        # --- Batch-Modus ---
        self.recent_events: List[datetime] = []

        # --- Circuit Breaker ---
        self.consecutive_failures: int = 0
        self.circuit_breaker_open: bool = False
        self.circuit_breaker_reset_at: Optional[datetime] = None

        # --- Statistiken ---
        self.total_submitted: int = 0
        self.total_completed: int = 0
        self.total_failed: int = 0

        # --- Worker ---
        self._fix_worker_task: Optional[asyncio.Task] = None
        self._running: bool = False

        logger.info(
            "SmartQueue initialisiert: max_parallel=%d, batch=%d/%ds, cb=%d/%ds",
            self.max_analysis_parallel,
            self.batch_threshold,
            self.batch_window,
            self.circuit_breaker_threshold,
            self.circuit_breaker_timeout,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Startet den Fix-Worker als asyncio.Task."""
        if self._running:
            logger.warning("SmartQueue laeuft bereits")
            return
        self._running = True
        self._fix_worker_task = asyncio.ensure_future(self._fix_worker())
        logger.info("SmartQueue gestartet")

    async def stop(self) -> None:
        """Stoppt den Fix-Worker."""
        self._running = False
        if self._fix_worker_task and not self._fix_worker_task.done():
            self._fix_worker_task.cancel()
            try:
                await self._fix_worker_task
            except asyncio.CancelledError:
                pass
        self._fix_worker_task = None
        logger.info("SmartQueue gestoppt")

    # ------------------------------------------------------------------
    # Submit
    # ------------------------------------------------------------------

    def submit(self, item: QueueItem) -> bool:
        """Nimmt ein QueueItem entgegen.

        ANALYSIS: Wird sofort als Task gestartet (Semaphore-begrenzt).
        FIX: Wird prioritaets-sortiert in die fix_queue eingereiht.

        Returns:
            True wenn akzeptiert, False wenn Circuit Breaker offen.
        """
        # Circuit Breaker pruefen
        if self.circuit_breaker_open:
            if not self._check_circuit_breaker_reset():
                logger.warning(
                    "SmartQueue: Circuit Breaker offen — Item abgelehnt (%s)",
                    item.item_type.value,
                )
                return False

        self.total_submitted += 1

        if item.item_type == QueueItemType.ANALYSIS:
            asyncio.ensure_future(self._run_analysis(item))
        elif item.item_type == QueueItemType.FIX:
            self._enqueue_fix(item)

        return True

    # ------------------------------------------------------------------
    # Analyse-Pool
    # ------------------------------------------------------------------

    async def _run_analysis(self, item: QueueItem) -> None:
        """Fuehrt eine Analyse aus (Semaphore-begrenzt)."""
        async with self.analysis_semaphore:
            self.active_analyses += 1
            try:
                result = await item.callback(item.event)
                item.result = result
                self._record_success()
            except Exception as exc:
                logger.error("Analyse fehlgeschlagen: %s", exc)
                self._record_failure()
            finally:
                self.active_analyses -= 1

    # ------------------------------------------------------------------
    # Fix-Queue & Worker
    # ------------------------------------------------------------------

    def _enqueue_fix(self, item: QueueItem) -> None:
        """Fuegt ein Fix-Item prioritaets-sortiert in die Queue ein."""
        self.fix_queue.append(item)
        self.fix_queue.sort(key=lambda x: x.priority)

    async def _fix_worker(self) -> None:
        """Endlos-Loop: Verarbeitet Fix-Items nacheinander."""
        logger.info("Fix-Worker gestartet")
        while self._running:
            if self.fix_queue and not self.fix_locked:
                item = self.fix_queue.pop(0)
                self.fix_locked = True
                try:
                    result = await item.callback(item.event)
                    item.result = result
                    self._record_success()
                except Exception as exc:
                    logger.error("Fix fehlgeschlagen: %s", exc)
                    self._record_failure()
                finally:
                    self.fix_locked = False
            else:
                await asyncio.sleep(0.05)

    # ------------------------------------------------------------------
    # Batch-Modus
    # ------------------------------------------------------------------

    def record_event(self) -> None:
        """Registriert einen Event-Timestamp fuer Batch-Erkennung."""
        self.recent_events.append(datetime.now(timezone.utc))

    def is_batch_mode(self) -> bool:
        """True wenn mehr als batch_threshold Events in batch_window Sekunden."""
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=self.batch_window)
        recent = [ts for ts in self.recent_events if ts > cutoff]
        return len(recent) > self.batch_threshold

    # ------------------------------------------------------------------
    # Circuit Breaker
    # ------------------------------------------------------------------

    def _record_success(self) -> None:
        """Setzt consecutive_failures zurueck, zaehlt completed hoch."""
        self.consecutive_failures = 0
        self.total_completed += 1

    def _record_failure(self) -> None:
        """Zaehlt Fehler hoch, oeffnet Circuit Breaker bei Threshold."""
        self.consecutive_failures += 1
        self.total_failed += 1

        if self.consecutive_failures >= self.circuit_breaker_threshold:
            self.circuit_breaker_open = True
            self.circuit_breaker_reset_at = datetime.now(timezone.utc) + timedelta(
                seconds=self.circuit_breaker_timeout
            )
            logger.warning(
                "Circuit Breaker geoeffnet nach %d Fehlern — Reset um %s",
                self.consecutive_failures,
                self.circuit_breaker_reset_at.isoformat(),
            )

    def _check_circuit_breaker_reset(self) -> bool:
        """Prueft ob der Circuit Breaker zurueckgesetzt werden kann.

        Returns:
            True wenn Reset durchgefuehrt, False wenn noch gesperrt.
        """
        if not self.circuit_breaker_open:
            return True

        if self.circuit_breaker_reset_at and datetime.now(timezone.utc) >= self.circuit_breaker_reset_at:
            self.circuit_breaker_open = False
            self.consecutive_failures = 0
            self.circuit_breaker_reset_at = None
            logger.info("Circuit Breaker zurueckgesetzt")
            return True

        return False

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        """Liefert den aktuellen Zustand als Dict.

        Returns:
            Dict mit: active_analyses, fix_queue_length, fix_locked,
            circuit_breaker_open, total_submitted, total_completed,
            total_failed, batch_mode
        """
        return {
            "active_analyses": self.active_analyses,
            "fix_queue_length": len(self.fix_queue),
            "fix_locked": self.fix_locked,
            "circuit_breaker_open": self.circuit_breaker_open,
            "total_submitted": self.total_submitted,
            "total_completed": self.total_completed,
            "total_failed": self.total_failed,
            "batch_mode": self.is_batch_mode(),
        }
