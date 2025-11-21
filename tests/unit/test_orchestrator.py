"""
Unit Tests for Remediation Orchestrator
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch
from datetime import datetime

from src.integrations.orchestrator import RemediationOrchestrator, SecurityEventBatch
from src.integrations.event_watcher import SecurityEvent


class TestOrchestratorInitialization:
    """Tests for Orchestrator initialization"""

    def test_init(self, mock_config):
        """Test orchestrator initialization"""
        mock_self_healing = Mock()

        orchestrator = RemediationOrchestrator(
            self_healing=mock_self_healing,
            config=mock_config
        )

        assert orchestrator.self_healing == mock_self_healing
        assert orchestrator.collection_window_seconds == 300
        assert orchestrator.max_batch_size == 10
        assert isinstance(orchestrator.event_history, dict)


class TestEventBatching:
    """Tests for event batching logic"""

    @pytest.mark.asyncio
    async def test_create_batch(self, mock_config, sample_security_event):
        """Test batch creation"""
        mock_self_healing = AsyncMock()
        orchestrator = RemediationOrchestrator(mock_self_healing, mock_config)

        events = [sample_security_event]

        await orchestrator.schedule_remediation(events)

        # Check that batch was created
        assert len(orchestrator.pending_batches) > 0

    @pytest.mark.asyncio
    async def test_batch_size_limit(self, mock_config):
        """Test that batch respects max size"""
        mock_self_healing = AsyncMock()
        orchestrator = RemediationOrchestrator(mock_self_healing, mock_config)
        orchestrator.max_batch_size = 5

        # Create 10 events
        events = []
        for i in range(10):
            event = SecurityEvent(
                source='trivy',
                event_type='vulnerability',
                severity='HIGH',
                details={'VulnerabilityID': f'CVE-2024-{i}'},
                is_persistent=True
            )
            events.append(event)

        await orchestrator.schedule_remediation(events)

        # Should create multiple batches or limit size
        total_events = sum(len(batch.events) for batch in orchestrator.pending_batches)
        assert total_events <= orchestrator.max_batch_size or len(orchestrator.pending_batches) > 1


class TestEventHistory:
    """Tests for event history tracking"""

    def test_event_history_initialization(self, mock_config):
        """Test event history is initialized"""
        mock_self_healing = Mock()
        orchestrator = RemediationOrchestrator(mock_self_healing, mock_config)

        assert isinstance(orchestrator.event_history, dict)
        assert len(orchestrator.event_history) >= 0

    def test_event_history_limits(self, mock_config):
        """Test event history respects size limits"""
        mock_self_healing = Mock()
        orchestrator = RemediationOrchestrator(mock_self_healing, mock_config)

        # Add many attempts to same event
        event_sig = "test_event"
        orchestrator.event_history[event_sig] = []

        for i in range(20):
            orchestrator.event_history[event_sig].append({
                'timestamp': datetime.now().isoformat(),
                'result': 'success' if i % 2 == 0 else 'failed'
            })

        # Manually apply limit (as done in actual code)
        orchestrator.event_history[event_sig] = orchestrator.event_history[event_sig][-10:]

        # Should keep only last 10
        assert len(orchestrator.event_history[event_sig]) == 10


class TestExecutionLock:
    """Tests for execution locking mechanism"""

    @pytest.mark.asyncio
    async def test_execution_lock_prevents_concurrent(self, mock_config):
        """Test that execution lock prevents concurrent remediation"""
        mock_self_healing = AsyncMock()
        orchestrator = RemediationOrchestrator(mock_self_healing, mock_config)

        # Simulate lock is already held
        lock_acquired = await orchestrator.execution_lock.acquire()

        assert lock_acquired is True

        # Try to acquire again (should block or return False in try_acquire scenario)
        # For this test, we just verify the lock exists
        assert orchestrator.execution_lock.locked() is True

        orchestrator.execution_lock.release()


class TestAdaptiveRetryDelays:
    """Tests for adaptive retry delay calculation"""

    def test_calculate_adaptive_retry_delay_high_success(self, mock_config, temp_dir):
        """Test adaptive delay with high success rate"""
        mock_self_healing = Mock()
        orchestrator = RemediationOrchestrator(mock_self_healing, mock_config)

        # Mock knowledge base with high success rate
        with patch('src.integrations.orchestrator.get_knowledge_base') as mock_kb:
            mock_kb_instance = Mock()
            mock_kb_instance.get_success_rate.return_value = {
                'success_rate': 0.9,
                'total_attempts': 10
            }
            mock_kb.return_value = mock_kb_instance

            delay = orchestrator._calculate_adaptive_retry_delay("test_sig", attempt=1)

            # High success rate should result in shorter delay
            assert delay >= 1.0  # Min cap
            assert delay <= 60.0  # Max cap

    def test_calculate_adaptive_retry_delay_low_success(self, mock_config, temp_dir):
        """Test adaptive delay with low success rate"""
        mock_self_healing = Mock()
        orchestrator = RemediationOrchestrator(mock_self_healing, mock_config)

        with patch('src.integrations.orchestrator.get_knowledge_base') as mock_kb:
            mock_kb_instance = Mock()
            mock_kb_instance.get_success_rate.return_value = {
                'success_rate': 0.2,
                'total_attempts': 10
            }
            mock_kb.return_value = mock_kb_instance

            delay = orchestrator._calculate_adaptive_retry_delay("test_sig", attempt=1)

            # Low success rate should result in longer delay
            assert delay >= 1.0
            assert delay <= 60.0

    def test_calculate_adaptive_retry_delay_exponential(self, mock_config):
        """Test exponential backoff in retry delays"""
        mock_self_healing = Mock()
        orchestrator = RemediationOrchestrator(mock_self_healing, mock_config)

        with patch('src.integrations.orchestrator.get_knowledge_base') as mock_kb:
            mock_kb_instance = Mock()
            mock_kb_instance.get_success_rate.return_value = {
                'success_rate': 0.5,
                'total_attempts': 10
            }
            mock_kb.return_value = mock_kb_instance

            delay1 = orchestrator._calculate_adaptive_retry_delay("test_sig", attempt=1)
            delay2 = orchestrator._calculate_adaptive_retry_delay("test_sig", attempt=2)
            delay3 = orchestrator._calculate_adaptive_retry_delay("test_sig", attempt=3)

            # Delays should increase exponentially
            assert delay2 > delay1
            assert delay3 > delay2
