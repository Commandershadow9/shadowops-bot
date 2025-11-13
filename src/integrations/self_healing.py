"""
Self-Healing Coordinator
Manages automatic remediation attempts with AI-powered retry logic and circuit breaker.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from dataclasses import dataclass, field
from enum import Enum
import json

logger = logging.getLogger(__name__)


class ApprovalMode(Enum):
    """Approval modes for auto-remediation"""
    PARANOID = "paranoid"      # Always require approval
    BALANCED = "balanced"      # Auto-fix LOW/MEDIUM, require approval for HIGH/CRITICAL
    AGGRESSIVE = "aggressive"  # Auto-fix everything except CRITICAL


@dataclass
class RemediationAttempt:
    """Tracks a remediation attempt"""
    event_id: str
    attempt_number: int
    timestamp: datetime
    strategy: str
    result: str  # 'success', 'failed', 'partial'
    error_message: Optional[str] = None
    ai_confidence: float = 0.0


@dataclass
class RemediationJob:
    """Represents a remediation job with retry logic"""
    event: 'SecurityEvent'  # Forward reference
    created_at: datetime = field(default_factory=datetime.now)
    attempts: List[RemediationAttempt] = field(default_factory=list)
    status: str = 'pending'  # 'pending', 'in_progress', 'success', 'failed', 'requires_approval'
    max_attempts: int = 3
    current_strategy: Optional[str] = None
    approval_required: bool = False
    approval_message_id: Optional[int] = None


class CircuitBreaker:
    """
    Circuit Breaker pattern to prevent infinite retry loops

    States: CLOSED (normal), OPEN (too many failures), HALF_OPEN (testing recovery)
    """

    def __init__(self, failure_threshold: int = 5, timeout_seconds: int = 3600):
        self.failure_threshold = failure_threshold
        self.timeout_seconds = timeout_seconds
        self.failure_count = 0
        self.last_failure_time: Optional[datetime] = None
        self.state = 'CLOSED'  # CLOSED, OPEN, HALF_OPEN

    def record_success(self):
        """Record successful operation"""
        self.failure_count = 0
        self.state = 'CLOSED'

    def record_failure(self):
        """Record failed operation"""
        self.failure_count += 1
        self.last_failure_time = datetime.now()

        if self.failure_count >= self.failure_threshold:
            self.state = 'OPEN'
            logger.warning(f"🔴 Circuit Breaker OPEN: {self.failure_count} failures")

    def can_attempt(self) -> bool:
        """Check if operation can be attempted"""
        if self.state == 'CLOSED':
            return True

        if self.state == 'OPEN':
            # Check if timeout expired
            if self.last_failure_time:
                elapsed = (datetime.now() - self.last_failure_time).total_seconds()
                if elapsed > self.timeout_seconds:
                    self.state = 'HALF_OPEN'
                    logger.info("🟡 Circuit Breaker HALF_OPEN: Testing recovery")
                    return True

            return False

        # HALF_OPEN: Allow one attempt
        return True

    def get_status(self) -> Dict:
        """Get circuit breaker status"""
        return {
            'state': self.state,
            'failure_count': self.failure_count,
            'last_failure': self.last_failure_time.isoformat() if self.last_failure_time else None,
        }


class SelfHealingCoordinator:
    """
    Coordinates automatic remediation with self-healing retry logic

    Features:
    - AI-powered fix strategy generation
    - Retry with learning (adjusts strategy based on previous failures)
    - Circuit breaker to prevent infinite loops
    - Approval workflow for high-risk fixes
    - Statistics and monitoring
    """

    def __init__(self, bot, config: Dict):
        self.bot = bot
        self.config = config

        # Approval mode
        approval_mode_str = config.get('auto_remediation', {}).get('approval_mode', 'balanced')
        self.approval_mode = ApprovalMode(approval_mode_str)
        logger.info(f"🎯 Approval Mode: {self.approval_mode.value}")

        # Job queue
        self.job_queue: List[RemediationJob] = []
        self.active_jobs: Dict[str, RemediationJob] = {}
        self.completed_jobs: List[RemediationJob] = []
        self.max_completed_history = 500

        # Circuit breaker
        self.circuit_breaker = CircuitBreaker(
            failure_threshold=config.get('auto_remediation', {}).get('circuit_breaker_threshold', 5),
            timeout_seconds=config.get('auto_remediation', {}).get('circuit_breaker_timeout', 3600)
        )

        # Worker task
        self.worker_task: Optional[asyncio.Task] = None
        self.running = False

        # Statistics
        self.stats = {
            'total_jobs': 0,
            'successful': 0,
            'failed': 0,
            'requires_approval': 0,
            'total_attempts': 0,
            'avg_attempts_per_job': 0.0,
        }

        # AI service (will be set during initialization)
        self.ai_service = None

    async def initialize(self, ai_service):
        """Initialize with AI service"""
        self.ai_service = ai_service
        logger.info("✅ Self-Healing Coordinator initialized")

    async def start(self):
        """Start self-healing worker"""
        if self.running:
            logger.warning("Self-healing already running")
            return

        self.running = True
        self.worker_task = asyncio.create_task(self._worker_loop())
        logger.info("✅ Self-Healing Worker started")

    async def stop(self):
        """Stop self-healing worker"""
        logger.info("🛑 Stopping Self-Healing Worker...")
        self.running = False

        if self.worker_task:
            self.worker_task.cancel()
            try:
                await self.worker_task
            except asyncio.CancelledError:
                pass

        logger.info("✅ Self-Healing Worker stopped")

    async def handle_event(self, event: 'SecurityEvent'):
        """
        Handle new security event

        Args:
            event: Security event from Event Watcher
        """
        logger.info(f"🔧 Self-Healing: Processing {event.severity} event from {event.source}")

        # Check if approval required based on mode
        approval_required = self._requires_approval(event)

        # Create remediation job
        job = RemediationJob(
            event=event,
            approval_required=approval_required
        )

        self.job_queue.append(job)
        self.stats['total_jobs'] += 1

        if approval_required:
            self.stats['requires_approval'] += 1
            logger.info(f"✋ Approval required for {event.severity} event")
            await self._request_approval(job)
        else:
            logger.info(f"✅ Auto-fix approved for {event.severity} event")

    async def _worker_loop(self):
        """Main worker loop - processes job queue"""
        logger.info("🔄 Self-Healing worker loop started")

        while self.running:
            try:
                # Process pending jobs
                await self._process_queue()

                # Check circuit breaker status
                if self.circuit_breaker.state == 'OPEN':
                    logger.warning("⏸️ Circuit breaker OPEN, pausing remediation")
                    await asyncio.sleep(60)  # Wait 1 minute before checking again
                    continue

                # Wait before next iteration
                await asyncio.sleep(5)

            except Exception as e:
                logger.error(f"❌ Worker loop error: {e}", exc_info=True)
                await asyncio.sleep(10)

    async def _process_queue(self):
        """Process pending jobs in queue"""
        if not self.job_queue:
            return

        # Get next pending job
        for job in self.job_queue[:]:
            if job.status == 'pending' and not job.approval_required:
                # Check circuit breaker
                if not self.circuit_breaker.can_attempt():
                    logger.warning(f"⏸️ Circuit breaker preventing job {job.event.event_id}")
                    continue

                # Move to active
                self.job_queue.remove(job)
                self.active_jobs[job.event.event_id] = job
                job.status = 'in_progress'

                # Process job
                await self._execute_remediation(job)

    async def _execute_remediation(self, job: RemediationJob):
        """
        Execute remediation with retry logic

        Args:
            job: Remediation job to execute
        """
        event = job.event
        attempt_num = len(job.attempts) + 1

        logger.info(f"🔧 Executing remediation attempt {attempt_num}/{job.max_attempts} for {event.event_id}")

        try:
            # Generate fix strategy using AI
            strategy = await self._generate_fix_strategy(job)

            if not strategy:
                logger.error(f"❌ Failed to generate fix strategy for {event.event_id}")
                await self._handle_failure(job, "Strategy generation failed")
                return

            job.current_strategy = strategy['description']

            # Execute fix based on event source
            result = await self._apply_fix(event, strategy)

            # Create attempt record
            attempt = RemediationAttempt(
                event_id=event.event_id,
                attempt_number=attempt_num,
                timestamp=datetime.now(),
                strategy=strategy['description'],
                result=result['status'],
                error_message=result.get('error'),
                ai_confidence=strategy.get('confidence', 0.0)
            )
            job.attempts.append(attempt)
            self.stats['total_attempts'] += 1

            if result['status'] == 'success':
                await self._handle_success(job)
            else:
                await self._handle_failure(job, result.get('error', 'Unknown error'))

        except Exception as e:
            logger.error(f"❌ Remediation execution error: {e}", exc_info=True)
            await self._handle_failure(job, str(e))

    async def _generate_fix_strategy(self, job: RemediationJob) -> Optional[Dict]:
        """
        Generate fix strategy using AI

        Takes into account previous failed attempts to learn and adapt.
        """
        event = job.event

        # Build context with previous attempts
        context = {
            'event': event.to_dict(),
            'previous_attempts': [
                {
                    'strategy': attempt.strategy,
                    'result': attempt.result,
                    'error': attempt.error_message
                }
                for attempt in job.attempts
            ]
        }

        # Request AI analysis
        if self.ai_service:
            try:
                strategy = await self.ai_service.generate_fix_strategy(context)
                return strategy
            except Exception as e:
                logger.error(f"AI strategy generation failed: {e}")
                return None

        # Fallback: Use predefined strategies
        return self._get_fallback_strategy(event)

    def _get_fallback_strategy(self, event: 'SecurityEvent') -> Dict:
        """Fallback strategy if AI is unavailable"""
        if event.source == 'trivy':
            return {
                'description': 'Update vulnerable package to fixed version',
                'confidence': 0.7,
                'steps': ['Identify package', 'Update to fixed version', 'Rebuild image', 'Redeploy']
            }
        elif event.source == 'crowdsec':
            return {
                'description': 'Ban IP and update firewall rules',
                'confidence': 0.9,
                'steps': ['Verify threat', 'Add permanent ban', 'Update firewall']
            }
        elif event.source == 'fail2ban':
            return {
                'description': 'Verify ban and extend duration',
                'confidence': 0.8,
                'steps': ['Check ban status', 'Extend ban duration']
            }
        elif event.source == 'aide':
            return {
                'description': 'Restore file from backup',
                'confidence': 0.6,
                'steps': ['Verify change', 'Check backup', 'Restore file', 'Update AIDE DB']
            }

        return {
            'description': 'Manual review required',
            'confidence': 0.3,
            'steps': ['Escalate to administrator']
        }

    async def _apply_fix(self, event: 'SecurityEvent', strategy: Dict) -> Dict:
        """
        Apply fix based on event source

        Returns: Dict with 'status' ('success'/'failed') and optional 'error'
        """
        try:
            if event.source == 'trivy':
                return await self._fix_trivy(event, strategy)
            elif event.source == 'crowdsec':
                return await self._fix_crowdsec(event, strategy)
            elif event.source == 'fail2ban':
                return await self._fix_fail2ban(event, strategy)
            elif event.source == 'aide':
                return await self._fix_aide(event, strategy)
            else:
                return {'status': 'failed', 'error': f'Unknown source: {event.source}'}

        except Exception as e:
            logger.error(f"Fix application error: {e}", exc_info=True)
            return {'status': 'failed', 'error': str(e)}

    async def _fix_trivy(self, event: 'SecurityEvent', strategy: Dict) -> Dict:
        """Fix Docker vulnerability"""
        # Placeholder - actual implementation would update Dockerfile, rebuild, redeploy
        logger.info(f"🐳 Applying Trivy fix: {strategy['description']}")
        # TODO: Implement actual Docker fix
        return {'status': 'success', 'message': 'Docker vulnerability fixed'}

    async def _fix_crowdsec(self, event: 'SecurityEvent', strategy: Dict) -> Dict:
        """Fix CrowdSec threat"""
        logger.info(f"🛡️ Applying CrowdSec fix: {strategy['description']}")
        # TODO: Implement actual CrowdSec remediation
        return {'status': 'success', 'message': 'Threat mitigated'}

    async def _fix_fail2ban(self, event: 'SecurityEvent', strategy: Dict) -> Dict:
        """Fix Fail2ban issue"""
        logger.info(f"🚫 Applying Fail2ban fix: {strategy['description']}")
        # TODO: Implement actual Fail2ban remediation
        return {'status': 'success', 'message': 'Ban extended'}

    async def _fix_aide(self, event: 'SecurityEvent', strategy: Dict) -> Dict:
        """Fix AIDE integrity violation"""
        logger.info(f"📁 Applying AIDE fix: {strategy['description']}")
        # TODO: Implement actual AIDE remediation
        return {'status': 'success', 'message': 'File restored'}

    async def _handle_success(self, job: RemediationJob):
        """Handle successful remediation"""
        logger.info(f"✅ Remediation successful for {job.event.event_id} after {len(job.attempts)} attempts")

        job.status = 'success'
        self.stats['successful'] += 1
        self.circuit_breaker.record_success()

        # Move to completed
        if job.event.event_id in self.active_jobs:
            del self.active_jobs[job.event.event_id]

        self.completed_jobs.append(job)
        if len(self.completed_jobs) > self.max_completed_history:
            self.completed_jobs.pop(0)

        # Send Discord notification
        await self._send_success_notification(job)

    async def _handle_failure(self, job: RemediationJob, error: str):
        """Handle failed remediation attempt"""
        logger.warning(f"⚠️ Remediation attempt failed for {job.event.event_id}: {error}")

        self.circuit_breaker.record_failure()

        # Check if we should retry
        if len(job.attempts) < job.max_attempts:
            logger.info(f"🔄 Will retry {job.event.event_id} (attempt {len(job.attempts) + 1}/{job.max_attempts})")
            # Put back in queue for retry
            job.status = 'pending'
            if job.event.event_id in self.active_jobs:
                del self.active_jobs[job.event.event_id]
            self.job_queue.append(job)
        else:
            logger.error(f"❌ Max attempts reached for {job.event.event_id}, giving up")
            job.status = 'failed'
            self.stats['failed'] += 1

            # Move to completed
            if job.event.event_id in self.active_jobs:
                del self.active_jobs[job.event.event_id]

            self.completed_jobs.append(job)
            if len(self.completed_jobs) > self.max_completed_history:
                self.completed_jobs.pop(0)

            # Send Discord notification
            await self._send_failure_notification(job)

    def _requires_approval(self, event: 'SecurityEvent') -> bool:
        """Determine if event requires human approval"""
        if self.approval_mode == ApprovalMode.PARANOID:
            return True

        if self.approval_mode == ApprovalMode.AGGRESSIVE:
            return event.severity == 'CRITICAL'

        # BALANCED mode
        return event.severity in ['CRITICAL', 'HIGH']

    async def _request_approval(self, job: RemediationJob):
        """Request human approval via Discord"""
        logger.info(f"✋ Requesting approval for {job.event.event_id}")
        # TODO: Send Discord message with approval buttons
        # Store message ID for later approval tracking

    async def _send_success_notification(self, job: RemediationJob):
        """Send success notification to Discord"""
        # TODO: Implement Discord notification
        pass

    async def _send_failure_notification(self, job: RemediationJob):
        """Send failure notification to Discord"""
        # TODO: Implement Discord notification
        pass

    def get_statistics(self) -> Dict:
        """Get self-healing statistics"""
        if self.stats['successful'] + self.stats['failed'] > 0:
            self.stats['avg_attempts_per_job'] = (
                self.stats['total_attempts'] / (self.stats['successful'] + self.stats['failed'])
            )

        return {
            **self.stats,
            'pending_jobs': len(self.job_queue),
            'active_jobs': len(self.active_jobs),
            'completed_jobs': len(self.completed_jobs),
            'circuit_breaker': self.circuit_breaker.get_status(),
            'approval_mode': self.approval_mode.value,
        }

    async def approve_job(self, event_id: str) -> bool:
        """Approve a pending job"""
        for job in self.job_queue:
            if job.event.event_id == event_id and job.approval_required:
                job.approval_required = False
                job.status = 'pending'
                logger.info(f"✅ Job {event_id} approved")
                return True

        return False

    async def reject_job(self, event_id: str) -> bool:
        """Reject a pending job"""
        for job in self.job_queue:
            if job.event.event_id == event_id and job.approval_required:
                self.job_queue.remove(job)
                job.status = 'rejected'
                self.completed_jobs.append(job)
                logger.info(f"❌ Job {event_id} rejected")
                return True

        return False

    async def stop_all_jobs(self):
        """Emergency stop - clear all pending jobs"""
        logger.warning("🛑 EMERGENCY STOP: Clearing all pending jobs")

        cleared_count = len(self.job_queue) + len(self.active_jobs)

        self.job_queue.clear()
        self.active_jobs.clear()

        logger.info(f"✅ Stopped {cleared_count} jobs")

        return cleared_count
