"""
Ollama Queue Manager - Intelligent Request Queuing for Resource Management

Manages a priority-based queue for Ollama requests to prevent resource exhaustion
and ensure security-critical tasks get processed first.

Priority Levels:
- CRITICAL (1): Security monitoring, attack detection, vulnerability response
- HIGH (2): Code fixes, error analysis
- NORMAL (3): General monitoring, routine checks
- LOW (4): Patch notes, non-critical AI generation

Features:
- Single worker processing (one request at a time)
- Priority-based queue
- Timeout handling
- Progress tracking
- Dashboard integration
"""

import asyncio
import logging
from dataclasses import dataclass, asdict
from datetime import datetime
from enum import IntEnum
from pathlib import Path
from typing import Optional, Callable, Any, Dict, List
import json

logger = logging.getLogger('shadowops')


class Priority(IntEnum):
    """Request priority levels (lower number = higher priority)."""
    CRITICAL = 1  # Security monitoring, attack detection
    HIGH = 2      # Code fixes, error analysis
    NORMAL = 3    # General monitoring, routine checks
    LOW = 4       # Patch notes, non-critical generation


@dataclass
class QueuedRequest:
    """A queued Ollama request."""
    id: str
    priority: Priority
    task_type: str  # "security_scan", "patch_notes", "code_fix", etc.
    project: str
    prompt: str
    callback: Optional[Callable] = None  # Function to call with result
    created_at: str = ""
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    status: str = "pending"  # pending, processing, completed, failed, cancelled
    error: Optional[str] = None
    result: Optional[str] = None
    timeout_seconds: int = 300  # 5 minutes default

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.utcnow().isoformat()

    def __lt__(self, other):
        """Compare by priority for queue sorting."""
        return self.priority < other.priority


class OllamaQueueManager:
    """
    Manages a priority-based queue for Ollama requests.

    Ensures only one request processes at a time and critical
    security tasks get priority over low-priority tasks like patch notes.
    """

    def __init__(self, ai_service, data_dir: Path = None):
        """
        Initialize the queue manager.

        Args:
            ai_service: The AI service instance to use for processing
            data_dir: Directory for queue persistence
        """
        self.ai_service = ai_service
        self.data_dir = data_dir or Path.home() / '.shadowops' / 'queue'
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # Priority queue (heap-based)
        self.queue: asyncio.PriorityQueue = asyncio.PriorityQueue()

        # Request tracking
        self.requests: Dict[str, QueuedRequest] = {}
        self.current_request: Optional[QueuedRequest] = None

        # Worker control
        self.worker_task: Optional[asyncio.Task] = None
        self.worker_running = False

        # Statistics
        self.stats = {
            'total_processed': 0,
            'total_failed': 0,
            'total_cancelled': 0,
            'avg_processing_time': 0.0,
            'by_priority': {p.value: 0 for p in Priority}
        }

        # Load persisted state
        self._load_state()

        logger.info("✅ Ollama Queue Manager initialized")

    def _load_state(self):
        """Load persisted queue state from disk."""
        state_file = self.data_dir / 'queue_state.json'
        if state_file.exists():
            try:
                with open(state_file, 'r', encoding='utf-8') as f:
                    state = json.load(f)
                    self.stats = state.get('stats', self.stats)
                logger.info(f"📊 Loaded queue statistics: {self.stats['total_processed']} total processed")
            except Exception as e:
                logger.warning(f"⚠️ Could not load queue state: {e}")

    def _save_state(self):
        """Persist queue state to disk."""
        state_file = self.data_dir / 'queue_state.json'
        try:
            state = {
                'stats': self.stats,
                'saved_at': datetime.utcnow().isoformat()
            }
            with open(state_file, 'w', encoding='utf-8') as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            logger.warning(f"⚠️ Could not save queue state: {e}")

    async def enqueue(
        self,
        task_type: str,
        project: str,
        prompt: str,
        priority: Priority = Priority.NORMAL,
        callback: Optional[Callable] = None,
        timeout_seconds: int = 300
    ) -> str:
        """
        Add a request to the queue.

        Args:
            task_type: Type of task (e.g., "security_scan", "patch_notes")
            project: Project name
            prompt: The prompt to send to Ollama
            priority: Priority level (default: NORMAL)
            callback: Optional callback function for result
            timeout_seconds: Timeout in seconds (default: 5 minutes)

        Returns:
            Request ID
        """
        import uuid
        request_id = f"{task_type}_{uuid.uuid4().hex[:8]}"

        request = QueuedRequest(
            id=request_id,
            priority=priority,
            task_type=task_type,
            project=project,
            prompt=prompt,
            callback=callback,
            timeout_seconds=timeout_seconds
        )

        # Add to queue and tracking
        await self.queue.put((priority.value, request))
        self.requests[request_id] = request

        logger.info(f"📥 Queued: {task_type} for {project} (Priority: {priority.name}, Queue size: {self.queue.qsize()})")

        # Start worker if not running
        if not self.worker_running:
            await self.start_worker()

        return request_id

    async def start_worker(self):
        """Start the queue worker task."""
        if self.worker_running:
            logger.warning("⚠️ Worker already running")
            return

        self.worker_running = True
        self.worker_task = asyncio.create_task(self._worker_loop())
        logger.info("🚀 Queue worker started")

    async def stop_worker(self):
        """Stop the queue worker task."""
        if not self.worker_running:
            return

        self.worker_running = False
        if self.worker_task:
            self.worker_task.cancel()
            try:
                await self.worker_task
            except asyncio.CancelledError:
                pass

        logger.info("🛑 Queue worker stopped")

    async def _worker_loop(self):
        """Main worker loop - processes requests one at a time."""
        logger.info("🔄 Worker loop started")

        while self.worker_running:
            try:
                # Get next request (blocks until available)
                priority, request = await asyncio.wait_for(
                    self.queue.get(),
                    timeout=10.0
                )

                # Process request
                await self._process_request(request)

            except asyncio.TimeoutError:
                # No requests in queue, keep waiting
                continue
            except asyncio.CancelledError:
                logger.info("🛑 Worker loop cancelled")
                break
            except Exception as e:
                logger.error(f"❌ Worker loop error: {e}", exc_info=True)
                await asyncio.sleep(5)

    async def _process_request(self, request: QueuedRequest):
        """Process a single request."""
        self.current_request = request
        request.status = "processing"
        request.started_at = datetime.utcnow().isoformat()

        logger.info(f"⚙️ Processing: {request.task_type} for {request.project} (Priority: {request.priority.name})")

        start_time = asyncio.get_event_loop().time()

        try:
            # Call AI service with timeout
            result = await asyncio.wait_for(
                self.ai_service.get_raw_ai_response(
                    prompt=request.prompt,
                    use_critical_model=True
                ),
                timeout=request.timeout_seconds
            )

            # Success
            request.status = "completed"
            request.result = result
            request.completed_at = datetime.utcnow().isoformat()

            processing_time = asyncio.get_event_loop().time() - start_time

            # Update statistics
            self.stats['total_processed'] += 1
            self.stats['by_priority'][request.priority.value] += 1

            # Update average processing time
            total = self.stats['total_processed']
            avg = self.stats['avg_processing_time']
            self.stats['avg_processing_time'] = ((avg * (total - 1)) + processing_time) / total

            logger.info(f"✅ Completed: {request.task_type} for {request.project} ({processing_time:.1f}s)")

            # Call callback if provided
            if request.callback:
                try:
                    await request.callback(result)
                except Exception as e:
                    logger.error(f"❌ Callback error: {e}", exc_info=True)

        except asyncio.TimeoutError:
            request.status = "failed"
            request.error = f"Timeout after {request.timeout_seconds}s"
            self.stats['total_failed'] += 1
            logger.error(f"⏱️ Timeout: {request.task_type} for {request.project}")

        except Exception as e:
            request.status = "failed"
            request.error = str(e)
            self.stats['total_failed'] += 1
            logger.error(f"❌ Failed: {request.task_type} for {request.project}: {e}", exc_info=True)

        finally:
            self.current_request = None
            self._save_state()

    def get_queue_status(self) -> Dict:
        """Get current queue status."""
        pending = [r for r in self.requests.values() if r.status == "pending"]
        processing = [r for r in self.requests.values() if r.status == "processing"]
        completed = [r for r in self.requests.values() if r.status == "completed"]
        failed = [r for r in self.requests.values() if r.status == "failed"]

        return {
            'queue_size': self.queue.qsize(),
            'current_request': asdict(self.current_request) if self.current_request else None,
            'pending_count': len(pending),
            'processing_count': len(processing),
            'completed_count': len(completed),
            'failed_count': len(failed),
            'worker_running': self.worker_running,
            'stats': self.stats,
            'pending_requests': [asdict(r) for r in sorted(pending, key=lambda x: x.priority)[:5]]
        }

    async def cancel_request(self, request_id: str) -> bool:
        """Cancel a pending request."""
        request = self.requests.get(request_id)
        if not request:
            return False

        if request.status == "pending":
            request.status = "cancelled"
            self.stats['total_cancelled'] += 1
            logger.info(f"🚫 Cancelled: {request.task_type} for {request.project}")
            return True

        return False

    async def clear_queue(self):
        """Clear all pending requests."""
        count = 0
        for request in list(self.requests.values()):
            if request.status == "pending":
                request.status = "cancelled"
                count += 1

        # Clear the priority queue
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        self.stats['total_cancelled'] += count
        logger.info(f"🧹 Cleared {count} pending requests from queue")
        return count

    async def pause_worker(self):
        """Pause the worker (finish current request, don't start new ones)."""
        await self.stop_worker()
        logger.info("⏸️ Worker paused")

    async def resume_worker(self):
        """Resume the worker."""
        await self.start_worker()
        logger.info("▶️ Worker resumed")
