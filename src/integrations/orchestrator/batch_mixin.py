"""
BatchManagerMixin — Event-Batching und Collection-Logik
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import TYPE_CHECKING

from .models import SecurityEventBatch

# Knowledge Base for AI Learning
from ..knowledge_base import get_knowledge_base

if TYPE_CHECKING:
    from .models import RemediationPlan

logger = logging.getLogger('shadowops')


class BatchManagerMixin:
    """Mixin für Event-Batching, Collection-Window und History-Management"""

    async def schedule_remediation(self, events: list) -> None:
        """
        Legacy wrapper used by older tests: create batches from incoming events.

        This method only batches events and enqueues them for processing; it does
        not execute remediation. It preserves max batch size semantics expected
        by the unit tests.
        """
        if not events:
            return

        # Split events into batches respecting max_batch_size
        for i in range(0, len(events), self.max_batch_size):
            batch_events = events[i:i + self.max_batch_size]
            batch = SecurityEventBatch(events=batch_events)
            self.pending_batches.append(batch)

    def _load_event_history(self):
        """Load event history from disk for learning"""
        try:
            if os.path.exists(self.history_file):
                with open(self.history_file, 'r') as f:
                    self.event_history = json.load(f)
                logger.info(f"📚 Loaded {len(self.event_history)} event type histories")

                # Count total attempts
                total_attempts = sum(len(attempts) for attempts in self.event_history.values())
                if total_attempts > 0:
                    logger.info(f"   📖 Total historical attempts: {total_attempts}")
            else:
                logger.info("📚 No event history found, starting fresh")
        except Exception as e:
            logger.error(f"❌ Error loading event history: {e}")
            self.event_history = {}

    def _save_event_history(self):
        """Save event history to disk for persistence"""
        try:
            os.makedirs(os.path.dirname(self.history_file), exist_ok=True)
            with open(self.history_file, 'w') as f:
                json.dump(self.event_history, f, indent=2, default=str)
            logger.debug("💾 Event history saved")
        except Exception as e:
            logger.error(f"❌ Error saving event history: {e}")

    def _calculate_adaptive_retry_delay(self, event_signature: str, attempt: int,
                                        last_error: str = None) -> float:
        """
        Calculate adaptive retry delay based on success rate and error type

        Args:
            event_signature: Event signature for KB lookup
            attempt: Current attempt number (1-based)
            last_error: Last error message for error type detection

        Returns:
            Delay in seconds (float)
        """
        # Base exponential backoff: 2^(attempt-1) seconds
        base_delay = 2 ** (attempt - 1)  # 2, 4, 8, ...

        # Get success rate from Knowledge Base
        try:
            kb = get_knowledge_base()
            stats = kb.get_success_rate(event_signature=event_signature, days=30)
            success_rate = stats.get('success_rate', 0.5)  # Default to 50%

            # Adjust delay based on success rate
            if success_rate >= 0.8:
                # High success rate → faster retries (problem is usually solvable)
                multiplier = 0.5
            elif success_rate >= 0.5:
                # Medium success rate → normal retries
                multiplier = 1.0
            else:
                # Low success rate → slower retries (problem is difficult)
                multiplier = 2.0

        except Exception as e:
            # KB not available - use default multiplier
            logger.debug(f"KB lookup failed for adaptive delay: {e}")
            multiplier = 1.0

        # Error-type specific adjustments
        if last_error:
            error_lower = last_error.lower()

            # Network errors → retry faster
            if any(keyword in error_lower for keyword in ['network', 'timeout', 'connection', 'unreachable']):
                multiplier *= 0.7

            # Permission errors → retry slower (unlikely to change quickly)
            elif any(keyword in error_lower for keyword in ['permission', 'denied', 'forbidden', 'unauthorized']):
                multiplier *= 1.5

            # Resource errors → moderate delay
            elif any(keyword in error_lower for keyword in ['resource', 'busy', 'locked', 'unavailable']):
                multiplier *= 1.2

        # Calculate final delay
        delay = base_delay * multiplier

        # Cap at minimum 1s and maximum 60s (1 minute)
        delay = max(1.0, min(60.0, delay))

        return delay

    async def submit_event(self, event):
        """
        Event zum Orchestrator hinzufügen

        Startet automatisch Batch-Collection wenn nötig
        """
        async with self.batch_lock:
            # Erstelle neuen Batch wenn nötig
            if self.current_batch is None:
                self.current_batch = SecurityEventBatch()
                logger.info(f"📦 Neuer Event-Batch gestartet: {self.current_batch.batch_id}")

                # Discord Channel Logger: New Batch Started
                if self.discord_logger:
                    self.discord_logger.log_orchestrator(
                        f"📦 **Neuer Remediation-Batch gestartet**\n"
                        f"🆔 Batch ID: `{self.current_batch.batch_id}`\n"
                        f"⏱️ Collection Window: {self.collection_window_seconds}s",
                        severity="info"
                    )

                # Starte Collection Timer
                self.collection_task = asyncio.create_task(self._close_batch_after_timeout())

                # Sende initiale Discord-Message
                status_text = f"📦 **Neuer Remediation-Batch gestartet**\n\n⏱️ Sammle Events für {self.collection_window_seconds} Sekunden..."
                await self._send_batch_status(self.current_batch, status_text, 0x3498DB)

            # Füge Event zum aktuellen Batch hinzu
            self.current_batch.add_event(event)
            logger.info(f"   ➕ Event hinzugefügt: {event.source} ({event.severity})")
            logger.info(f"   📊 Batch Status: {len(self.current_batch.events)}/{self.max_batch_size} Events")

            # Check if batch size limit reached
            if len(self.current_batch.events) >= self.max_batch_size:
                logger.info(f"⚠️ Batch Limit erreicht ({self.max_batch_size} Events) - Schließe Batch sofort")
                # Cancel collection timer and close batch immediately
                if self.collection_task:
                    self.collection_task.cancel()
                await self._close_batch_immediately()
                return

            # Update Discord-Message mit neuem Event
            event_list = "\n".join([f"• **{e.source.upper()}**: {e.severity}" for e in self.current_batch.events])
            elapsed = int(time.time() - self.current_batch.created_at)
            remaining = max(0, self.collection_window_seconds - elapsed)
            status_text = f"📦 **Sammle Security-Events**\n\n{event_list}\n\n⏱️ Verbleibend: **{remaining}s** | Events: **{len(self.current_batch.events)}/{self.max_batch_size}**"
            await self._send_batch_status(self.current_batch, status_text, 0x3498DB)

    async def _close_batch_after_timeout(self):
        """Schließt Batch nach Collection Window mit Live-Countdown-Updates"""
        update_interval = 2  # Update Discord alle 2 Sekunden
        elapsed = 0

        batch = self.current_batch  # Referenz speichern

        while elapsed < self.collection_window_seconds:
            await asyncio.sleep(update_interval)
            elapsed += update_interval

            # Update Discord mit Countdown
            async with self.batch_lock:
                if self.current_batch == batch and len(batch.events) > 0:
                    remaining = max(0, self.collection_window_seconds - elapsed)
                    event_list = "\n".join([f"• **{e.source.upper()}**: {e.severity}" for e in batch.events])

                    # Progress bar
                    progress = min(100, int((elapsed / self.collection_window_seconds) * 100))
                    bar_length = 20
                    filled = int((progress / 100) * bar_length)
                    bar = "█" * filled + "░" * (bar_length - filled)

                    status_text = f"📦 **Sammle Security-Events**\n\n{event_list}\n\n⏱️ **{remaining}s** verbleibend | Events: **{len(batch.events)}**\n\n{bar} {progress}%"
                    await self._send_batch_status(batch, status_text, 0x3498DB)

        async with self.batch_lock:
            if self.current_batch and len(self.current_batch.events) > 0:
                logger.info(f"⏰ Batch-Collection abgelaufen ({self.collection_window_seconds}s)")
                logger.info(f"   📦 Batch {self.current_batch.batch_id}: {len(self.current_batch.events)} Events")
                logger.info(f"   🔍 Quellen: {', '.join(self.current_batch.sources)}")

                # Final Discord Update
                event_list = "\n".join([f"• **{e.source.upper()}**: {e.severity}" for e in self.current_batch.events])
                status_text = f"✅ **Batch geschlossen**\n\n{event_list}\n\n📊 Total: **{len(self.current_batch.events)} Events**\n🔍 Quellen: {', '.join(self.current_batch.sources)}\n\n🧠 Starte KI-Analyse..."
                await self._send_batch_status(self.current_batch, status_text, 0xF39C12)

                # Batch zur Verarbeitung verschieben
                self.current_batch.status = "analyzing"
                self.pending_batches.append(self.current_batch)
                self.current_batch = None

                # Starte Verarbeitung
                asyncio.create_task(self._process_next_batch())

    async def _close_batch_immediately(self):
        """Schließt Batch sofort wenn Max-Size erreicht ist (Server-Schonung)"""
        if self.current_batch and len(self.current_batch.events) > 0:
            logger.info(f"📦 Batch {self.current_batch.batch_id}: {len(self.current_batch.events)} Events (LIMIT)")
            logger.info(f"   🔍 Quellen: {', '.join(self.current_batch.sources)}")

            # Final Discord Update
            event_list = "\n".join([f"• **{e.source.upper()}**: {e.severity}" for e in self.current_batch.events])
            status_text = f"⚠️ **Batch Limit erreicht** ({self.max_batch_size} Events)\n\n{event_list}\n\n📊 Total: **{len(self.current_batch.events)} Events**\n🔍 Quellen: {', '.join(self.current_batch.sources)}\n\n🧠 Starte KI-Analyse..."
            await self._send_batch_status(self.current_batch, status_text, 0xF39C12)

            # Batch zur Verarbeitung verschieben
            self.current_batch.status = "analyzing"
            self.pending_batches.append(self.current_batch)
            self.current_batch = None

            # Starte Verarbeitung
            asyncio.create_task(self._process_next_batch())
