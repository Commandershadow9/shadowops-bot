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
import discord

from integrations.approval_modes import ApprovalMode, ApprovalModeManager
from integrations.command_executor import CommandExecutor, CommandExecutorConfig
from integrations.backup_manager import BackupManager, BackupConfig
from integrations.impact_analyzer import ImpactAnalyzer
from integrations.service_manager import ServiceManager
from integrations.fixers import TrivyFixer, CrowdSecFixer, Fail2banFixer, AideFixer

logger = logging.getLogger(__name__)


class ApprovalView(discord.ui.View):
    """Discord UI View for approval buttons"""

    def __init__(self, coordinator: 'SelfHealingCoordinator', job: 'RemediationJob'):
        super().__init__(timeout=3600)  # 1 hour timeout
        self.coordinator = coordinator
        self.job = job

    @discord.ui.button(label="✅ Approve", style=discord.ButtonStyle.success)
    async def approve_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Approve the remediation"""
        await interaction.response.send_message(
            f"✅ Remediation approved by {interaction.user.mention}",
            ephemeral=True
        )
        await self.coordinator.approve_job(self.job.event.event_id)

        # Update message
        embed = interaction.message.embeds[0]
        embed.color = discord.Color.green()
        embed.add_field(name="Status", value=f"✅ Approved by {interaction.user.mention}", inline=False)
        await interaction.message.edit(embed=embed, view=None)

    @discord.ui.button(label="❌ Reject", style=discord.ButtonStyle.danger)
    async def reject_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Reject the remediation"""
        await interaction.response.send_message(
            f"❌ Remediation rejected by {interaction.user.mention}",
            ephemeral=True
        )
        await self.coordinator.reject_job(self.job.event.event_id)

        # Update message
        embed = interaction.message.embeds[0]
        embed.color = discord.Color.red()
        embed.add_field(name="Status", value=f"❌ Rejected by {interaction.user.mention}", inline=False)
        await interaction.message.edit(embed=embed, view=None)


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

    def __init__(self, bot, config, discord_logger=None):
        self.bot = bot
        self.config = config
        self.discord_logger = discord_logger

        # Approval mode - Config ist ein Objekt, kein Dict
        approval_mode_str = config.auto_remediation.get('approval_mode', 'paranoid')
        self.approval_mode = ApprovalMode(approval_mode_str)

        # Approval Mode Manager (will be set during initialization with context manager)
        self.approval_manager = None
        logger.info(f"🎯 Approval Mode: {self.approval_mode.value}")

        # Job queue
        self.job_queue: List[RemediationJob] = []
        self.active_jobs: Dict[str, RemediationJob] = {}
        self.completed_jobs: List[RemediationJob] = []
        self.max_completed_history = 500

        # Circuit breaker
        self.circuit_breaker = CircuitBreaker(
            failure_threshold=config.auto_remediation.get('circuit_breaker_threshold', 5),
            timeout_seconds=config.auto_remediation.get('circuit_breaker_timeout', 3600)
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

        # Infrastructure components (will be initialized)
        self.command_executor = None
        self.backup_manager = None
        self.impact_analyzer = None
        self.service_manager = None

        # Fixer modules (will be initialized)
        self.trivy_fixer = None
        self.crowdsec_fixer = None
        self.fail2ban_fixer = None
        self.aide_fixer = None

    async def initialize(self, ai_service):
        """Initialize with AI service and context manager"""
        self.ai_service = ai_service

        # Initialize Approval Mode Manager with context from AI service
        context_manager = getattr(ai_service, 'context_manager', None)
        self.approval_manager = ApprovalModeManager(self.approval_mode, context_manager)

        # Initialize infrastructure components
        executor_config = CommandExecutorConfig(
            dry_run=self.config.auto_remediation.get('dry_run', False),
            default_timeout=300
        )
        self.command_executor = CommandExecutor(executor_config)

        backup_config = BackupConfig(
            backup_root='/tmp/shadowops_backups',
            retention_days=7
        )
        self.backup_manager = BackupManager(backup_config, self.command_executor)

        self.impact_analyzer = ImpactAnalyzer(self.command_executor)

        self.service_manager = ServiceManager(
            executor=self.command_executor,
            discord_notify_callback=self._send_discord_notification
        )

        # Initialize fixer modules
        self.trivy_fixer = TrivyFixer(self.command_executor, self.backup_manager)
        self.crowdsec_fixer = CrowdSecFixer(self.command_executor, self.backup_manager)
        self.fail2ban_fixer = Fail2banFixer(self.command_executor, self.backup_manager)
        self.aide_fixer = AideFixer(self.command_executor, self.backup_manager)

        logger.info("✅ Self-Healing Coordinator initialized with all components")
        logger.info(f"   Dry-run mode: {executor_config.dry_run}")
        logger.info(f"   Backup retention: {backup_config.retention_days} days")

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

            # CONFIDENCE-PRÜFUNG: Verhindere unsichere Fixes
            confidence = strategy.get('confidence', 0)
            if confidence < 0.85:
                logger.error(f"🚨 ABGEBROCHEN: Confidence {confidence:.0%} < 85% - zu riskant für automatische Ausführung!")
                await self._handle_failure(job, f"Confidence zu niedrig ({confidence:.0%} < 85%). Manuelle Prüfung erforderlich.")
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

    async def _generate_fix_strategy(self, job: RemediationJob, streaming_state: Optional[Dict] = None) -> Optional[Dict]:
        """
        Generate fix strategy using AI

        Takes into account previous failed attempts to learn and adapt.
        Accepts optional streaming_state for real-time Discord updates.
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

        # Add streaming state if provided (for real-time Discord updates)
        if streaming_state is not None:
            context['streaming_state'] = streaming_state

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
        """Fix Docker vulnerability using TrivyFixer"""
        logger.info(f"🐳 Applying Trivy fix: {strategy['description']}")

        # Discord Channel Logger: Fix Start
        if self.discord_logger:
            project = event.details.get('AffectedProjects', ['Unknown'])[0] if event.details.get('AffectedProjects') else 'Unknown'
            project_name = project.split('/')[-1] if '/' in project else project
            self.discord_logger.log_code_fix(
                f"🔧 **Trivy Fix gestartet**\n"
                f"📂 Projekt: **{project_name}**\n"
                f"📝 Strategy: {strategy['description'][:100]}",
                severity="info"
            )

        try:
            # Convert SecurityEvent to dict for fixer
            event_dict = event.to_dict()

            # Call TrivyFixer
            result = await self.trivy_fixer.fix(
                event=event_dict,
                strategy=strategy
            )

            # Discord Channel Logger: Fix Result
            if self.discord_logger:
                if result.get('status') == 'success':
                    self.discord_logger.log_code_fix(
                        f"✅ **Trivy Fix erfolgreich**\n"
                        f"📂 Projekt: **{project_name}**\n"
                        f"📝 {result.get('message', 'Fix applied')}",
                        severity="success"
                    )
                else:
                    self.discord_logger.log_code_fix(
                        f"❌ **Trivy Fix fehlgeschlagen**\n"
                        f"📂 Projekt: **{project_name}**\n"
                        f"⚠️ Error: {result.get('error', 'Unknown')}",
                        severity="error"
                    )

            return result

        except Exception as e:
            logger.error(f"❌ Trivy fix error: {e}", exc_info=True)

            # Discord Channel Logger: Exception
            if self.discord_logger:
                self.discord_logger.log_code_fix(
                    f"❌ **Trivy Fix Exception**\n"
                    f"⚠️ {str(e)[:150]}",
                    severity="error"
                )

            return {
                'status': 'failed',
                'error': str(e)
            }

    async def _fix_crowdsec(self, event: 'SecurityEvent', strategy: Dict) -> Dict:
        """Fix CrowdSec threat using CrowdSecFixer"""
        logger.info(f"🛡️ Applying CrowdSec fix: {strategy['description']}")

        try:
            # Convert SecurityEvent to dict for fixer
            event_dict = event.to_dict()

            # Call CrowdSecFixer
            result = await self.crowdsec_fixer.fix(
                event=event_dict,
                strategy=strategy
            )

            return result

        except Exception as e:
            logger.error(f"❌ CrowdSec fix error: {e}", exc_info=True)
            return {
                'status': 'failed',
                'error': str(e)
            }

    async def _fix_fail2ban(self, event: 'SecurityEvent', strategy: Dict) -> Dict:
        """Fix Fail2ban issue using Fail2banFixer"""
        logger.info(f"🚫 Applying Fail2ban fix: {strategy['description']}")

        try:
            # Convert SecurityEvent to dict for fixer
            event_dict = event.to_dict()

            # Call Fail2banFixer
            result = await self.fail2ban_fixer.fix(
                event=event_dict,
                strategy=strategy
            )

            return result

        except Exception as e:
            logger.error(f"❌ Fail2ban fix error: {e}", exc_info=True)
            return {
                'status': 'failed',
                'error': str(e)
            }

    async def _fix_aide(self, event: 'SecurityEvent', strategy: Dict) -> Dict:
        """Fix AIDE integrity violation using AideFixer"""
        logger.info(f"📁 Applying AIDE fix: {strategy['description']}")

        try:
            # Convert SecurityEvent to dict for fixer
            event_dict = event.to_dict()

            # Call AideFixer
            result = await self.aide_fixer.fix(
                event=event_dict,
                strategy=strategy
            )

            return result

        except Exception as e:
            logger.error(f"❌ AIDE fix error: {e}", exc_info=True)
            return {
                'status': 'failed',
                'error': str(e)
            }

    async def _send_discord_notification(self, message: str, level: str = 'info'):
        """Send Discord notification (callback for Service Manager)"""
        try:
            # Get appropriate channel based on level
            if level == 'warning' or level == 'error':
                channel_id = 1438503736220586164  # auto-remediation-alerts
            else:
                channel_id = 1438503699302957117  # bot-status

            channel = self.bot.get_channel(channel_id)

            if channel:
                # Create embed based on level
                color_map = {
                    'info': discord.Color.blue(),
                    'success': discord.Color.green(),
                    'warning': discord.Color.orange(),
                    'error': discord.Color.red()
                }

                embed = discord.Embed(
                    description=message,
                    color=color_map.get(level, discord.Color.blue()),
                    timestamp=datetime.now()
                )

                await channel.send(embed=embed)

        except Exception as e:
            logger.error(f"❌ Discord notification error: {e}")

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

    def _requires_approval(self, event: 'SecurityEvent', fix_strategy: Optional[Dict] = None) -> bool:
        """
        Determine if event requires human approval

        NOTE: This method is called twice:
        1. Initially before AI analysis (fix_strategy=None) - uses basic logic
        2. After AI analysis (fix_strategy provided) - uses ApprovalModeManager for intelligent decision

        Args:
            event: Security event
            fix_strategy: Optional AI-generated fix strategy (with confidence score)

        Returns:
            True if approval required, False if can auto-execute
        """
        # If we don't have a fix strategy yet, use basic approval logic
        if fix_strategy is None:
            # Always start by requiring approval (will be reassessed after AI analysis)
            return True

        # Use ApprovalModeManager for intelligent decision
        if self.approval_manager:
            decision = self.approval_manager.should_auto_execute(event, fix_strategy)
            logger.info(f"📊 Approval Decision: auto_execute={decision.should_auto_execute}, reason={decision.reason}")
            return not decision.should_auto_execute

        # Fallback to old logic if no approval manager
        if self.approval_mode == ApprovalMode.PARANOID:
            return True

        if self.approval_mode == ApprovalMode.AGGRESSIVE:
            return event.severity == 'CRITICAL'

        # BALANCED mode
        if event.source == 'fail2ban':
            return self._is_suspicious_fail2ban_activity(event)

        return event.severity in ['CRITICAL', 'HIGH']

    async def _generate_fix_strategy_with_live_updates(self, job: RemediationJob, channel) -> Optional[Dict]:
        """
        Generate fix strategy with live Discord updates

        Sends a status message and updates it in real-time as the AI analyzes.
        """
        import discord
        from datetime import datetime

        event = job.event

        # Create initial status embed
        status_embed = discord.Embed(
            title="🤖 KI-Analyse läuft...",
            description=f"**Event:** {event.source} - {event.severity}\n"
                       f"**Event ID:** `{event.event_id}`\n\n"
                       f"⏳ **Status:** Analyse wird vorbereitet...",
            color=0x3498DB,
            timestamp=datetime.now()
        )
        status_embed.set_footer(text="Live-Status • Updates alle paar Sekunden")

        # Send initial message
        status_message = await channel.send(embed=status_embed)

        try:
            # Phase 1: Data Collection
            await self._update_status(status_message, status_embed,
                "📊 Sammle Event-Daten...",
                progress="▰▱▱▱▱▱▱▱▱▱▱▱▱▱▱▱▱▱▱▱ 5%")

            # Phase 2: Event Parsing
            await self._update_status(status_message, status_embed,
                "🔍 Parse Event-Details...",
                progress="▰▰▱▱▱▱▱▱▱▱▱▱▱▱▱▱▱▱▱▱ 10%",
                thinking="Extrahiere CVE-IDs, Packages, Severity...")

            # Phase 3: Context Building
            await self._update_status(status_message, status_embed,
                "🗂️ Baue Kontext auf...",
                progress="▰▰▰▰▱▱▱▱▱▱▱▱▱▱▱▱▱▱▱▱ 20%",
                thinking="Sammle Previous Attempts, System-Kontext, und Infrastructure-Daten...")

            # Phase 4: RAG Context
            await self._update_status(status_message, status_embed,
                "📚 Lade RAG-Kontext...",
                progress="▰▰▰▰▰▰▱▱▱▱▱▱▱▱▱▱▱▱▱▱ 30%",
                thinking="Durchsuche Wissens-Datenbank nach relevanten Fixes...")

            # Phase 5: AI Analysis (longest part with live updates)
            await self._update_status(status_message, status_embed,
                "🧠 KI analysiert Sicherheitslücke...",
                progress="▰▰▰▰▰▰▰▰▱▱▱▱▱▱▱▱▱▱▱▱ 40%",
                thinking="Ollama/Claude untersucht CVEs, Packages, und Risiken...")

            # Actually generate strategy (this is the long part)
            # Start a heartbeat task to update Discord every 15 seconds
            import time
            start_time = time.time()
            analysis_done = False

            # Shared state for streaming progress
            streaming_state = {
                'token_count': 0,
                'last_snippet': 'Starte Analyse...',
                'phase': 'Initialisierung'
            }

            async def ai_heartbeat():
                """Update Discord status every 15s during AI analysis"""
                await asyncio.sleep(15)  # First update after 15s
                update_count = 1
                while not analysis_done:
                    elapsed = int(time.time() - start_time)
                    minutes = elapsed // 60
                    seconds = elapsed % 60

                    # Build detailed status message
                    status_details = f"⏳ **Echtzeit-Analyse läuft...**\n\n"

                    # Show which model is being used
                    model_name = self.ai_service.ollama_model_critical if job.event.severity == 'CRITICAL' else self.ai_service.ollama_model
                    status_details += f"🤖 **Modell:** {model_name}\n"

                    # Show elapsed time with progress
                    timeout_seconds = 360 if job.event.severity == 'CRITICAL' else 120
                    progress_pct = min(100, int((elapsed / timeout_seconds) * 100))
                    status_details += f"⏱️ **Zeit:** {minutes}m {seconds}s / {timeout_seconds//60}m ({progress_pct}%)\n"

                    # Show streaming stats
                    token_count = streaming_state['token_count']
                    if token_count > 0:
                        tokens_per_sec = token_count / max(elapsed, 1)
                        status_details += f"📊 **Tokens:** {token_count} ({tokens_per_sec:.1f}/s)\n\n"
                    else:
                        status_details += f"📊 **Tokens:** Warte auf Stream...\n\n"

                    # Show what AI is currently generating
                    snippet = streaming_state['last_snippet']
                    if snippet and len(snippet) > 10:
                        # Clean up snippet
                        clean_snippet = snippet.replace('\n', ' ').replace('"', '').strip()[:150]
                        status_details += f"💭 **AI schreibt gerade:**\n`{clean_snippet}...`\n\n"

                    status_details += f"🔄 **Update:** #{update_count} (alle 15s)"

                    await self._update_status(status_message, status_embed,
                        f"🧠 KI analysiert... ({minutes}m {seconds}s)",
                        progress="▰▰▰▰▰▰▰▰▱▱▱▱▱▱▱▱▱▱▱▱ 40%",
                        thinking=status_details)

                    update_count += 1
                    await asyncio.sleep(15)  # Update every 15 seconds

            heartbeat_task = asyncio.create_task(ai_heartbeat())

            try:
                # Pass streaming state to AI service
                strategy = await self._generate_fix_strategy(job, streaming_state=streaming_state)
            finally:
                analysis_done = True
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass

            if not strategy:
                await self._update_status(status_message, status_embed,
                    "❌ KI-Analyse fehlgeschlagen",
                    progress="▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰ 100%",
                    thinking="Keine Fix-Strategie konnte generiert werden.",
                    color=0xE74C3C)
                return None

            # Phase 6: Strategy Processing
            await self._update_status(status_message, status_embed,
                "⚙️ Verarbeite KI-Antwort...",
                progress="▰▰▰▰▰▰▰▰▰▰▰▰▰▰▱▱▱▱▱▱ 70%",
                thinking="Parse JSON, validiere Confidence, prüfe Steps...")

            # Phase 7: Safety Validation
            await self._update_status(status_message, status_embed,
                "🔒 Sicherheits-Validierung...",
                progress="▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▱▱▱ 85%",
                thinking=f"Prüfe Confidence ({strategy.get('confidence', 0):.0%}), Risiken, und Rollback-Plan...")

            # Phase 8: Final Preparation
            await self._update_status(status_message, status_embed,
                "✅ Fix-Strategie entwickelt",
                progress="▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰ 100%",
                thinking=f"**Confidence:** {strategy.get('confidence', 0):.0%}\n"
                        f"**Beschreibung:** {strategy.get('description', 'N/A')}\n"
                        f"**Steps:** {len(strategy.get('steps', []))} Schritte geplant",
                color=0x2ECC71)

            # Give user time to read
            await asyncio.sleep(2)

            # Delete status message (or keep it for audit trail?)
            # await status_message.delete()  # Optional

            return strategy

        except Exception as e:
            logger.error(f"Live update error: {e}", exc_info=True)
            await self._update_status(status_message, status_embed,
                f"❌ Fehler bei KI-Analyse",
                progress="▰▰▰▰▰▰▰▰▰▰ 100%",
                thinking=f"Error: {str(e)}",
                color=0xE74C3C)
            return None

    async def _update_status(self, message, embed, status: str, progress: str = "", thinking: str = "", color: int = 0x3498DB):
        """Update status message with new information"""
        try:
            # Build description parts
            parts = [f"**Event:** {message.embeds[0].description.split('**Event:**')[1].split(chr(10)+chr(10))[0]}"]
            parts.append("")  # Empty line
            parts.append(f"⏳ **Status:** {status}")

            if progress:
                parts.append(f"📊 **Progress:** `{progress}`")

            if thinking:
                parts.append(f"💭 **KI-Reasoning:**")
                parts.append(thinking)

            embed.description = "\n".join(parts)
            embed.color = color
            await message.edit(embed=embed)
            await asyncio.sleep(0.5)  # Rate limiting
        except Exception as e:
            logger.error(f"Failed to update status: {e}")

    def _is_suspicious_fail2ban_activity(self, event: 'SecurityEvent') -> bool:
        """
        Intelligente Fail2ban-Analyse: Erkennt verdächtige Aktivitäten

        Approval nur bei:
        - Massiven koordinierten Angriffen (>50 IPs gleichzeitig)
        - Gezielten SSH-Bruteforce-Attacken (>10 SSH-Bans)
        """
        details = event.details
        stats = details.get('Stats', {})
        total_bans = stats.get('total_bans', 0)

        # VERDÄCHTIG wenn:
        # 1. MASSIVER koordinierter Angriff (>50 IPs gleichzeitig = DDoS/Botnet)
        if total_bans > 50:
            logger.warning(f"🚨 VERDÄCHTIG: MASSIVER koordinierter Angriff erkannt - {total_bans} Bans!")
            return True

        # 2. Gezielte SSH-Bruteforce-Attacke (>=10 SSH-Bans = ernsthafte Bedrohung)
        bans_list = details.get('Bans', [])
        ssh_bans = sum(1 for ban in bans_list if 'sshd' in ban.get('jail', '').lower())
        if ssh_bans >= 10:
            logger.warning(f"🚨 VERDÄCHTIG: Gezielte SSH-Bruteforce-Attacke - {ssh_bans} SSH-Bans!")
            return True

        # Ansonsten: Normal, keine Approval nötig (Fail2ban hat bereits gebannt)
        logger.info(f"✅ Fail2ban: {total_bans} Bans - Bereits gebannt (keine Approval nötig)")
        return False

    async def _request_approval(self, job: RemediationJob):
        """Request human approval via Discord"""
        logger.info(f"✋ Requesting approval for {job.event.event_id}")

        try:
            # Get approval channel
            channel_id = self.config.auto_remediation.get('notifications', {}).get('approvals_channel')
            if not channel_id:
                logger.error("No approvals channel configured")
                return

            channel = self.bot.get_channel(channel_id)
            if not channel:
                logger.error(f"Approvals channel {channel_id} not found")
                return

            event = job.event

            # STEP 1: Start KI Analysis with Live Updates
            strategy = await self._generate_fix_strategy_with_live_updates(job, channel)

            if not strategy:
                logger.error(f"❌ KI-Analyse fehlgeschlagen für {event.event_id}")
                # Send error message to channel
                await channel.send(f"❌ **KI-Analyse fehlgeschlagen** für Event `{event.event_id}`\n"
                                 f"Keine Fix-Strategie konnte generiert werden.")
                return

            # Severity color mapping
            color_map = {
                'CRITICAL': 0xE74C3C,  # Red
                'HIGH': 0xE67E22,      # Orange
                'MEDIUM': 0xF39C12,    # Yellow
                'LOW': 0x3498DB,       # Blue
            }

            # Check if it's a batch event
            is_batch = 'batch' in event.event_type.lower() or 'Stats' in event.details

            # Create detailed embed with ALL event information for KI
            if is_batch:
                stats = event.details.get('Stats', {})
                title_text = f"🛡️ Batch Auto-Remediation Approval Required"

                # Source-specific summary
                if event.source == 'trivy':
                    desc_text = (f"**{event.severity}** vulnerabilities detected from **Docker/Trivy**\n\n"
                               f"📊 **Summary:**\n"
                               f"• 🔴 CRITICAL: {stats.get('critical', 0)}\n"
                               f"• 🟠 HIGH: {stats.get('high', 0)}\n"
                               f"• 🟡 MEDIUM: {stats.get('medium', 0)}\n"
                               f"• 🔵 LOW: {stats.get('low', 0)}\n"
                               f"• 📦 Images: {stats.get('images', 0)}")
                elif event.source == 'fail2ban':
                    total_bans = stats.get('total_bans', 0)
                    bans_list = event.details.get('Bans', [])
                    ssh_bans = sum(1 for ban in bans_list if 'sshd' in ban.get('jail', '').lower())

                    # Erkläre WARUM Approval nötig ist
                    if total_bans > 50:
                        reason = f"⚠️ **MASSIVER KOORDINIERTER ANGRIFF!**\n{total_bans} IPs gleichzeitig - mögliches Botnet/DDoS!"
                    elif ssh_bans >= 10:
                        reason = f"⚠️ **GEZIELTE SSH-BRUTEFORCE-ATTACKE!**\n{ssh_bans} SSH-Login-Versuche - ernsthafte Bedrohung!"
                    else:
                        reason = f"⚠️ **VERDÄCHTIGE AKTIVITÄT ERKANNT!**"

                    desc_text = (f"**{event.severity}** - **Fail2ban** hat verdächtige Aktivität erkannt\n\n"
                               f"{reason}\n\n"
                               f"📊 **Summary:**\n"
                               f"• 🚫 Banned IPs: {total_bans}\n"
                               f"• 🔐 SSH-Bans: {ssh_bans}\n\n"
                               f"💡 *Fail2ban hat die IPs bereits gebannt. Weitere Maßnahmen empfohlen:*\n"
                               f"• Permanente Firewall-Regeln für IP-Ranges\n"
                               f"• CrowdSec-Integration (IP-Listen teilen)\n"
                               f"• Security-Monitoring verschärfen")
                else:
                    # Generic batch summary
                    desc_text = f"**{event.severity}** issues detected from **{event.source}**"
            else:
                title_text = f"🛡️ Auto-Remediation Approval Required"
                desc_text = f"**{event.severity}** vulnerability detected from **{event.source}**"

            embed = discord.Embed(
                title=title_text,
                description=desc_text,
                color=color_map.get(event.severity, 0x95A5A6),
                timestamp=event.timestamp
            )

            # Event Details
            embed.add_field(
                name="📋 Event Type",
                value=f"`{event.event_type}`",
                inline=True
            )
            embed.add_field(
                name="🔍 Source",
                value=f"`{event.source}`",
                inline=True
            )
            embed.add_field(
                name="⚠️ Severity",
                value=f"`{event.severity}`",
                inline=True
            )

            # Detailed Information (for KI)
            details_text = "```json\n"
            details_text += json.dumps(event.details, indent=2, default=str)[:1000]  # Limit to 1000 chars
            details_text += "\n```"
            embed.add_field(
                name="🔬 Detailed Information (for KI Analysis)",
                value=details_text,
                inline=False
            )

            # STEP 2: Show Fix Strategy (already generated above with live updates)
            if strategy:
                confidence = strategy.get('confidence', 0)

                # Confidence-basierte Warnung
                if confidence < 0.85:
                    confidence_warning = "⚠️ **NIEDRIGE CONFIDENCE - MANUELLE PRÜFUNG ERFORDERLICH!**\n"
                    confidence_color = "🟡"
                elif confidence < 0.95:
                    confidence_warning = "✅ Ausreichende Confidence für manuelle Approval\n"
                    confidence_color = "🟢"
                else:
                    confidence_warning = "✅ Hohe Confidence - Sicher für Automatisierung\n"
                    confidence_color = "🟢"

                # Description field (with text splitting if too long)
                description_text = strategy.get('description', 'N/A')
                if len(description_text) > 1024:
                    # Split long descriptions
                    description_text = description_text[:1021] + "..."

                embed.add_field(
                    name="🔧 Vorgeschlagene Fix-Strategie",
                    value=f"{confidence_warning}"
                          f"**Beschreibung:** {description_text}\n"
                          f"**Confidence:** {confidence_color} {confidence:.0%}\n"
                          f"**Schritte:** {len(strategy.get('steps', []))} Schritte geplant",
                    inline=False
                )

                # Add AI analysis (with splitting if too long)
                analysis_text = strategy.get('analysis', '')
                if analysis_text:
                    # Discord field limit is 1024 chars
                    if len(analysis_text) > 1024:
                        # Split into multiple fields
                        analysis_parts = []
                        remaining = analysis_text
                        while remaining:
                            if len(remaining) <= 1024:
                                analysis_parts.append(remaining)
                                break
                            # Find a good break point (newline, period, or space)
                            break_point = 1000
                            for sep in ['\n\n', '\n', '. ', ' ']:
                                idx = remaining[:1024].rfind(sep)
                                if idx > break_point:
                                    break_point = idx + len(sep)
                                    break
                            analysis_parts.append(remaining[:break_point])
                            remaining = remaining[break_point:]

                        for idx, part in enumerate(analysis_parts[:3], 1):  # Max 3 parts
                            embed.add_field(
                                name=f"🧠 KI-Analyse ({idx}/{len(analysis_parts[:3])})" if len(analysis_parts) > 1 else "🧠 KI-Analyse",
                                value=part.strip(),
                                inline=False
                            )
                    else:
                        embed.add_field(
                            name="🧠 KI-Analyse",
                            value=analysis_text,
                            inline=False
                        )

                # Add steps (with splitting if too long)
                steps_list = strategy.get('steps', [])
                steps_text = "\n".join([f"{i+1}. {step}" for i, step in enumerate(steps_list[:10])])

                # Discord field limit is 1024 chars
                if len(steps_text) > 1024:
                    # Split into multiple fields
                    steps_text_1 = "\n".join([f"{i+1}. {step}" for i, step in enumerate(steps_list[:5])])
                    steps_text_2 = "\n".join([f"{i+6}. {step}" for i, step in enumerate(steps_list[5:10])])

                    embed.add_field(
                        name="📝 Remediation Steps (1/2)",
                        value=steps_text_1 or "Keine Schritte verfügbar",
                        inline=False
                    )
                    if steps_text_2:
                        embed.add_field(
                            name="📝 Remediation Steps (2/2)",
                            value=steps_text_2,
                            inline=False
                        )
                else:
                    embed.add_field(
                        name="📝 Remediation Steps",
                        value=steps_text or "Keine Schritte verfügbar",
                        inline=False
                    )

                # Zusätzliche Warnung bei sehr niedriger Confidence
                if confidence < 0.85:
                    embed.add_field(
                        name="⚠️ Sicherheitshinweis",
                        value="**Confidence <85%:** Diese Fix-Strategie ist unsicher!\n"
                              "• Nur nach gründlicher manueller Prüfung anwenden\n"
                              "• Risiko von System-Beschädigungen\n"
                              "• Alternative Lösungen in Betracht ziehen",
                        inline=False
                    )

            embed.add_field(
                name="🆔 Job ID",
                value=f"`{event.event_id}`",
                inline=False
            )

            embed.set_footer(text="Approve or reject this remediation below")

            # Send with approval buttons
            view = ApprovalView(self, job)
            message = await channel.send(embed=embed, view=view)

            job.approval_message_id = message.id
            logger.info(f"✅ Approval request sent to channel {channel_id}")

            # Also send status update to bot-status channel
            await self._send_status_update(
                f"✋ **Approval Required**\n"
                f"• Event: {event.severity} {event.event_type} from {event.source}\n"
                f"• Job ID: `{event.event_id}`\n"
                f"• Waiting for human approval in <#{channel_id}>",
                0xF39C12
            )

        except Exception as e:
            logger.error(f"Failed to send approval request: {e}", exc_info=True)

    async def _send_status_update(self, message: str, color: int = 0x3498DB):
        """Send status update to bot-status channel"""
        try:
            bot_status_channel_id = self.config.channels.get('bot_status')
            if bot_status_channel_id:
                channel = self.bot.get_channel(bot_status_channel_id)
                if channel:
                    embed = discord.Embed(
                        description=message,
                        color=color,
                        timestamp=datetime.now()
                    )
                    await channel.send(embed=embed)
        except Exception as e:
            logger.error(f"Failed to send status update: {e}")

    async def _send_success_notification(self, job: RemediationJob):
        """Send success notification to Discord"""
        try:
            # Send to alerts channel
            channel_id = self.config.auto_remediation.get('notifications', {}).get('alerts_channel')
            if not channel_id:
                return

            channel = self.bot.get_channel(channel_id)
            if not channel:
                return

            event = job.event
            embed = discord.Embed(
                title="✅ Auto-Remediation Successful",
                description=f"Successfully fixed **{event.severity}** issue from **{event.source}**",
                color=0x2ECC71,
                timestamp=datetime.now()
            )

            embed.add_field(name="Event Type", value=event.event_type, inline=True)
            embed.add_field(name="Severity", value=event.severity, inline=True)
            embed.add_field(name="Attempts", value=str(len(job.attempts)), inline=True)

            if job.attempts:
                last_attempt = job.attempts[-1]
                embed.add_field(
                    name="Strategy Used",
                    value=last_attempt.strategy or "N/A",
                    inline=False
                )

            await channel.send(embed=embed)

            # Status update
            await self._send_status_update(
                f"✅ **Remediation Successful**\n"
                f"• Event: {event.severity} {event.event_type}\n"
                f"• Attempts: {len(job.attempts)}\n"
                f"• Job ID: `{event.event_id}`",
                0x2ECC71
            )

        except Exception as e:
            logger.error(f"Failed to send success notification: {e}", exc_info=True)

    async def _send_failure_notification(self, job: RemediationJob):
        """Send failure notification to Discord"""
        try:
            # Send to alerts channel
            channel_id = self.config.auto_remediation.get('notifications', {}).get('alerts_channel')
            if not channel_id:
                return

            channel = self.bot.get_channel(channel_id)
            if not channel:
                return

            event = job.event
            embed = discord.Embed(
                title="❌ Auto-Remediation Failed",
                description=f"Failed to fix **{event.severity}** issue from **{event.source}** after {len(job.attempts)} attempts",
                color=0xE74C3C,
                timestamp=datetime.now()
            )

            embed.add_field(name="Event Type", value=event.event_type, inline=True)
            embed.add_field(name="Severity", value=event.severity, inline=True)
            embed.add_field(name="Attempts", value=str(len(job.attempts)), inline=True)

            if job.attempts:
                last_attempt = job.attempts[-1]
                embed.add_field(
                    name="Last Error",
                    value=last_attempt.error_message or "Unknown error",
                    inline=False
                )

            embed.add_field(
                name="⚠️ Action Required",
                value="Manual intervention needed",
                inline=False
            )

            await channel.send(embed=embed)

            # Status update
            await self._send_status_update(
                f"❌ **Remediation Failed**\n"
                f"• Event: {event.severity} {event.event_type}\n"
                f"• Attempts: {len(job.attempts)}\n"
                f"• Job ID: `{event.event_id}`\n"
                f"⚠️ Manual intervention required",
                0xE74C3C
            )

        except Exception as e:
            logger.error(f"Failed to send failure notification: {e}", exc_info=True)

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
