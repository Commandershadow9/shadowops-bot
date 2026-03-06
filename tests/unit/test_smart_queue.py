"""
Unit Tests fuer SmartQueue — Ersatz fuer OllamaQueueManager

Testet: Initialisierung, Analyse-Pool, Fix-Lock, Circuit Breaker,
Batch-Modus und Prioritaets-Sortierung.
"""

import asyncio
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

from src.integrations.smart_queue import (
    SmartQueue,
    QueueItem,
    QueueItemType,
    SEVERITY_PRIORITY,
)


# ============================================================================
# HELPERS
# ============================================================================

def _make_item(
    item_type: QueueItemType = QueueItemType.ANALYSIS,
    severity: str = "MEDIUM",
    priority: int | None = None,
    callback: AsyncMock | None = None,
) -> QueueItem:
    """Erzeugt ein QueueItem mit sinnvollen Defaults."""
    if priority is None:
        priority = SEVERITY_PRIORITY.get(severity, 2)
    return QueueItem(
        item_type=item_type,
        event={"severity": severity, "source": "test"},
        callback=callback or AsyncMock(),
        priority=priority,
    )


# ============================================================================
# TestSmartQueueInit
# ============================================================================

class TestSmartQueueInit:
    """Prueft, dass Default-Config korrekt uebernommen wird."""

    def test_defaults_ohne_config(self):
        """SmartQueue ohne Config nutzt Defaults."""
        q = SmartQueue({})
        assert q.max_analysis_parallel == 3
        assert q.batch_threshold == 5
        assert q.batch_window == 10
        assert q.circuit_breaker_threshold == 5
        assert q.circuit_breaker_timeout == 3600

    def test_custom_config(self):
        """SmartQueue uebernimmt uebergebene Config-Werte."""
        cfg = {
            "max_analysis_parallel": 5,
            "batch_threshold": 10,
            "batch_window": 30,
            "circuit_breaker_threshold": 3,
            "circuit_breaker_timeout": 1800,
        }
        q = SmartQueue(cfg)
        assert q.max_analysis_parallel == 5
        assert q.batch_threshold == 10
        assert q.batch_window == 30
        assert q.circuit_breaker_threshold == 3
        assert q.circuit_breaker_timeout == 1800

    def test_initial_state(self):
        """Alle Zaehler und Flags starten bei 0 / False."""
        q = SmartQueue({})
        assert q.active_analyses == 0
        assert q.fix_locked is False
        assert q.consecutive_failures == 0
        assert q.circuit_breaker_open is False
        assert q.circuit_breaker_reset_at is None
        assert q.total_submitted == 0
        assert q.total_completed == 0
        assert q.total_failed == 0
        assert len(q.fix_queue) == 0


# ============================================================================
# TestSmartQueueAnalysis
# ============================================================================

class TestSmartQueueAnalysis:
    """Prueft submit() fuer ANALYSIS-Items."""

    async def test_submit_analysis_accepted(self):
        """ANALYSIS-Item wird akzeptiert und Callback ausgefuehrt."""
        q = SmartQueue({})
        q.start()

        cb = AsyncMock(return_value={"status": "ok"})
        item = _make_item(QueueItemType.ANALYSIS, callback=cb)

        accepted = q.submit(item)
        assert accepted is True
        assert q.total_submitted == 1

        # Warten bis Task abgeschlossen
        await asyncio.sleep(0.1)

        cb.assert_awaited_once()
        assert q.total_completed == 1

        await q.stop()

    async def test_analysis_respects_semaphore(self):
        """Maximal max_analysis_parallel Analysen gleichzeitig."""
        q = SmartQueue({"max_analysis_parallel": 2})
        q.start()

        started = []
        barrier = asyncio.Event()

        async def slow_callback(event):
            started.append(True)
            await barrier.wait()
            return {"ok": True}

        # 3 Items einreichen, aber nur 2 duerfen gleichzeitig laufen
        for _ in range(3):
            item = _make_item(QueueItemType.ANALYSIS, callback=slow_callback)
            q.submit(item)

        await asyncio.sleep(0.1)
        assert len(started) == 2  # Nur 2 von 3 gestartet

        barrier.set()
        await asyncio.sleep(0.1)
        assert len(started) == 3  # Jetzt alle 3

        await q.stop()

    async def test_analysis_failure_tracked(self):
        """Fehlgeschlagene Analyse zaehlt als Failure."""
        q = SmartQueue({})
        q.start()

        async def failing_callback(event):
            raise RuntimeError("Analyse fehlgeschlagen")

        item = _make_item(QueueItemType.ANALYSIS, callback=failing_callback)
        q.submit(item)

        await asyncio.sleep(0.1)
        assert q.total_failed == 1
        assert q.consecutive_failures == 1

        await q.stop()


# ============================================================================
# TestSmartQueueFixLock
# ============================================================================

class TestSmartQueueFixLock:
    """Prueft Fix-Queue und Locking-Mechanismus."""

    async def test_fix_in_queue(self):
        """FIX-Item landet in der fix_queue."""
        q = SmartQueue({})
        # Worker NICHT starten — damit bleibt Item in Queue
        item = _make_item(QueueItemType.FIX, severity="HIGH")
        accepted = q.submit(item)

        assert accepted is True
        assert len(q.fix_queue) == 1
        assert q.total_submitted == 1

    async def test_fix_worker_processes_queue(self):
        """Fix-Worker verarbeitet Items nacheinander."""
        q = SmartQueue({})
        q.start()

        cb = AsyncMock(return_value={"fixed": True})
        item = _make_item(QueueItemType.FIX, callback=cb)
        q.submit(item)

        await asyncio.sleep(0.2)
        cb.assert_awaited_once()
        assert q.total_completed == 1
        assert len(q.fix_queue) == 0

        await q.stop()

    async def test_fix_lock_serializes(self):
        """Fixes werden nacheinander ausgefuehrt (nie parallel)."""
        q = SmartQueue({})
        q.start()

        execution_order = []

        async def tracked_callback(event, idx=0):
            execution_order.append(f"start-{idx}")
            await asyncio.sleep(0.05)
            execution_order.append(f"end-{idx}")
            return {}

        for i in range(3):
            item = _make_item(
                QueueItemType.FIX,
                severity="MEDIUM",
                callback=lambda e, i=i: tracked_callback(e, i),
            )
            q.submit(item)

        await asyncio.sleep(0.5)

        # Jeder Fix muss beendet sein bevor der naechste startet
        for i in range(3):
            start_idx = execution_order.index(f"start-{i}")
            end_idx = execution_order.index(f"end-{i}")
            if i < 2:
                next_start_idx = execution_order.index(f"start-{i+1}")
                assert end_idx < next_start_idx, (
                    f"Fix {i} endete nach Start von Fix {i+1}"
                )

        await q.stop()


# ============================================================================
# TestCircuitBreaker
# ============================================================================

class TestCircuitBreaker:
    """Prueft Circuit-Breaker-Logik."""

    async def test_opens_after_threshold(self):
        """Circuit Breaker oeffnet nach N aufeinanderfolgenden Fehlern."""
        q = SmartQueue({"circuit_breaker_threshold": 3})
        q.start()

        async def always_fail(event):
            raise RuntimeError("Boom")

        for _ in range(3):
            item = _make_item(QueueItemType.ANALYSIS, callback=always_fail)
            q.submit(item)

        await asyncio.sleep(0.2)

        assert q.circuit_breaker_open is True
        assert q.consecutive_failures == 3
        assert q.circuit_breaker_reset_at is not None

        await q.stop()

    async def test_rejects_when_open(self):
        """Submit wird abgelehnt wenn Circuit Breaker offen."""
        q = SmartQueue({"circuit_breaker_threshold": 2})
        q.start()

        # Manuell oeffnen
        q.circuit_breaker_open = True
        q.circuit_breaker_reset_at = datetime.now(timezone.utc) + timedelta(hours=1)

        item = _make_item(QueueItemType.ANALYSIS)
        accepted = q.submit(item)
        assert accepted is False

        await q.stop()

    async def test_resets_after_timeout(self):
        """Circuit Breaker schliesst nach Ablauf des Timeouts."""
        q = SmartQueue({"circuit_breaker_timeout": 1})

        q.circuit_breaker_open = True
        q.circuit_breaker_reset_at = datetime.now(timezone.utc) - timedelta(seconds=2)

        assert q._check_circuit_breaker_reset() is True
        assert q.circuit_breaker_open is False
        assert q.consecutive_failures == 0

    async def test_success_resets_failures(self):
        """Erfolgreiche Ausfuehrung setzt consecutive_failures zurueck."""
        q = SmartQueue({})
        q.start()

        q.consecutive_failures = 4

        cb = AsyncMock(return_value={"ok": True})
        item = _make_item(QueueItemType.ANALYSIS, callback=cb)
        q.submit(item)

        await asyncio.sleep(0.1)
        assert q.consecutive_failures == 0

        await q.stop()


# ============================================================================
# TestBatchMode
# ============================================================================

class TestBatchMode:
    """Prueft Batch-Modus-Erkennung."""

    def test_not_batch_initially(self):
        """Ohne Events kein Batch-Modus."""
        q = SmartQueue({"batch_threshold": 3, "batch_window": 10})
        assert q.is_batch_mode() is False

    def test_batch_after_threshold(self):
        """Nach genug Events in kurzer Zeit: Batch-Modus aktiv."""
        q = SmartQueue({"batch_threshold": 3, "batch_window": 10})
        for _ in range(4):
            q.record_event()
        assert q.is_batch_mode() is True

    def test_no_batch_old_events(self):
        """Alte Events (ausserhalb batch_window) zaehlen nicht."""
        q = SmartQueue({"batch_threshold": 3, "batch_window": 5})
        old = datetime.now(timezone.utc) - timedelta(seconds=60)
        q.recent_events = [old, old, old, old]
        assert q.is_batch_mode() is False


# ============================================================================
# TestPriority
# ============================================================================

class TestPriority:
    """Prueft Prioritaets-Sortierung in der Fix-Queue."""

    def test_critical_before_low(self):
        """CRITICAL-Items stehen vor LOW-Items in der Queue."""
        q = SmartQueue({})

        low = _make_item(QueueItemType.FIX, severity="LOW", priority=3)
        medium = _make_item(QueueItemType.FIX, severity="MEDIUM", priority=2)
        critical = _make_item(QueueItemType.FIX, severity="CRITICAL", priority=0)

        q.submit(low)
        q.submit(critical)
        q.submit(medium)

        assert len(q.fix_queue) == 3
        assert q.fix_queue[0].priority == 0  # CRITICAL
        assert q.fix_queue[1].priority == 2  # MEDIUM
        assert q.fix_queue[2].priority == 3  # LOW

    def test_severity_property(self):
        """QueueItem.severity liest aus event['severity']."""
        item = _make_item(severity="HIGH")
        assert item.severity == "HIGH"

    def test_severity_default(self):
        """Ohne severity im Event: Default MEDIUM."""
        item = QueueItem(
            item_type=QueueItemType.ANALYSIS,
            event={"source": "test"},
            callback=AsyncMock(),
            priority=2,
        )
        assert item.severity == "MEDIUM"


# ============================================================================
# TestGetStats
# ============================================================================

class TestGetStats:
    """Prueft get_stats() Ausgabe."""

    def test_stats_keys(self):
        """get_stats() liefert alle erwarteten Keys."""
        q = SmartQueue({})
        stats = q.get_stats()

        expected_keys = {
            "active_analyses",
            "fix_queue_length",
            "fix_locked",
            "circuit_breaker_open",
            "total_submitted",
            "total_completed",
            "total_failed",
            "batch_mode",
        }
        assert expected_keys == set(stats.keys())

    async def test_stats_reflect_state(self):
        """Stats spiegeln den aktuellen Zustand wider."""
        q = SmartQueue({})
        q.start()

        cb = AsyncMock(return_value={})
        q.submit(_make_item(QueueItemType.ANALYSIS, callback=cb))

        await asyncio.sleep(0.1)

        stats = q.get_stats()
        assert stats["total_submitted"] == 1
        assert stats["total_completed"] == 1
        assert stats["total_failed"] == 0

        await q.stop()
