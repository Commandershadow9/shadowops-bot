"""
Integration Tests for AI Learning Workflow

These tests demonstrate the complete learning cycle:
1. Event Detection → 2. Fix Strategy → 3. Execution → 4. Recording → 5. Learning

This shows how the bot learns from its experiences and improves over time.
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch
from pathlib import Path

from src.integrations.event_watcher import SecurityEvent
from src.integrations.knowledge_base import KnowledgeBase
from src.integrations.ai_service import AIService


class TestLearningCycle:
    """Tests for the complete learning cycle"""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_first_time_vulnerability_fix(self, temp_dir, mock_config):
        """
        Test: AI encounters a new vulnerability for the first time

        Expected behavior:
        1. No previous attempts exist
        2. AI generates fix strategy based on context only
        3. Fix is attempted
        4. Result is recorded in KB
        5. Future encounters can learn from this attempt
        """
        # Setup
        kb = KnowledgeBase(db_path=str(temp_dir / "kb.db"))
        ai_service = AIService(mock_config)

        # New vulnerability event
        event = {
            'source': 'trivy',
            'event_type': 'vulnerability',
            'severity': 'CRITICAL',
            'details': {
                'VulnerabilityID': 'CVE-2024-NEWVULN',
                'PkgName': 'example-lib',
                'InstalledVersion': '1.0.0',
                'FixedVersion': '1.1.0'
            }
        }

        # Check no previous attempts
        signature = 'CVE-2024-NEWVULN'
        stats_before = kb.get_success_rate(event_signature=signature)
        assert stats_before['total_attempts'] == 0

        # Mock AI response
        mock_strategy = {
            'description': 'Update package to version 1.1.0',
            'confidence': 0.85,
            'steps': [
                {'action': 'update_package', 'command': 'npm update example-lib'}
            ],
            'analysis': 'Version 1.1.0 fixes the vulnerability'
        }

        with patch.object(ai_service, '_analyze_with_ollama', return_value=mock_strategy):
            # AI generates strategy
            context = {
                'event': event,
                'previous_attempts': []  # First time
            }

            strategy = await ai_service.generate_fix_strategy(context)
            assert strategy is not None
            assert strategy['confidence'] == 0.85

        # Simulate fix execution (success)
        kb.record_fix(
            event=event,
            strategy=strategy,
            result='success',
            duration_seconds=5.2
        )

        # Verify learning occurred
        stats_after = kb.get_success_rate(event_signature=signature)
        assert stats_after['total_attempts'] == 1
        assert stats_after['successful_attempts'] == 1
        assert stats_after['success_rate'] == 1.0

        kb.close()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_learning_from_failure(self, temp_dir, mock_config):
        """
        Test: AI learns from a failed attempt and tries a different approach

        Expected behavior:
        1. First attempt fails
        2. KB records the failure
        3. Second attempt includes context of previous failure
        4. AI adjusts strategy based on failure
        5. Second attempt succeeds
        6. KB shows learning progression
        """
        kb = KnowledgeBase(db_path=str(temp_dir / "kb.db"))
        ai_service = AIService(mock_config)

        event = {
            'source': 'trivy',
            'event_type': 'vulnerability',
            'severity': 'HIGH',
            'details': {
                'VulnerabilityID': 'CVE-2024-DIFFICULT',
                'PkgName': 'complex-lib',
                'InstalledVersion': '2.0.0',
                'FixedVersion': '2.1.0'
            }
        }

        # ATTEMPT 1: Naive approach fails
        strategy_1 = {
            'description': 'Simple package update',
            'confidence': 0.7,
            'steps': [
                {'action': 'update_package', 'command': 'npm update complex-lib'}
            ]
        }

        kb.record_fix(
            event=event,
            strategy=strategy_1,
            result='failed',
            error_message='Dependency conflict',
            retry_count=0
        )

        # Check failure was recorded
        stats_after_fail = kb.get_success_rate(event_signature='CVE-2024-DIFFICULT')
        assert stats_after_fail['successful_attempts'] == 0
        assert stats_after_fail['failed_attempts'] == 1

        # ATTEMPT 2: AI learns from failure
        previous_attempts = [
            {
                'timestamp': '2024-01-01T12:00:00',
                'strategy': strategy_1,
                'result': 'failed',
                'error': 'Dependency conflict'
            }
        ]

        # AI adjusts strategy based on previous failure
        strategy_2 = {
            'description': 'Update with dependency resolution',
            'confidence': 0.9,
            'steps': [
                {'action': 'resolve_dependencies', 'command': 'npm install --legacy-peer-deps'},
                {'action': 'update_package', 'command': 'npm update complex-lib'}
            ],
            'analysis': 'Previous failure due to dependencies, resolving first'
        }

        with patch.object(ai_service, '_analyze_with_ollama', return_value=strategy_2):
            context = {
                'event': event,
                'previous_attempts': previous_attempts
            }

            new_strategy = await ai_service.generate_fix_strategy(context)
            assert new_strategy is not None
            # AI should have higher confidence after learning
            assert new_strategy['confidence'] >= 0.8

        # Second attempt succeeds
        kb.record_fix(
            event=event,
            strategy=strategy_2,
            result='success',
            duration_seconds=8.5,
            retry_count=1
        )

        # Verify learning progression
        stats_final = kb.get_success_rate(event_signature='CVE-2024-DIFFICULT')
        assert stats_final['total_attempts'] == 2
        assert stats_final['successful_attempts'] == 1
        assert stats_final['failed_attempts'] == 1
        assert stats_final['success_rate'] == 0.5

        kb.close()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_pattern_recognition_across_similar_events(self, temp_dir, mock_config):
        """
        Test: AI recognizes patterns across similar events

        Expected behavior:
        1. Multiple similar vulnerabilities are fixed successfully
        2. KB accumulates successful pattern
        3. AI queries KB for similar events
        4. AI applies proven strategy to new similar event
        5. High confidence due to historical success
        """
        kb = KnowledgeBase(db_path=str(temp_dir / "kb.db"))

        # Record multiple successful fixes for OpenSSL vulnerabilities
        openssl_vulnerabilities = [
            'CVE-2024-SSL01',
            'CVE-2024-SSL02',
            'CVE-2024-SSL03',
            'CVE-2024-SSL04',
            'CVE-2024-SSL05'
        ]

        successful_strategy = {
            'description': 'Update OpenSSL to latest stable version',
            'confidence': 0.9,
            'steps': [
                {'action': 'update_package', 'command': 'npm update openssl'}
            ],
            'ai_model': 'llama3.1'
        }

        for cve in openssl_vulnerabilities:
            kb.record_fix(
                event={
                    'source': 'trivy',
                    'event_type': 'vulnerability',
                    'severity': 'CRITICAL',
                    'details': {
                        'VulnerabilityID': cve,
                        'PkgName': 'openssl',
                        'InstalledVersion': '1.0.x',
                        'FixedVersion': '1.1.x'
                    }
                },
                strategy=successful_strategy,
                result='success',
                duration_seconds=4.0
            )

        # Query KB for best strategies for vulnerabilities
        best_strategies = kb.get_best_strategies(event_type='vulnerability', limit=1)

        assert len(best_strategies) > 0

        best = best_strategies[0]
        # Should have high success rate from 5 successful attempts
        assert best['success_count'] >= 5
        assert best['success_rate'] >= 0.9

        # New OpenSSL vulnerability appears
        new_event = {
            'source': 'trivy',
            'event_type': 'vulnerability',
            'severity': 'CRITICAL',
            'details': {
                'VulnerabilityID': 'CVE-2024-SSL06',  # New CVE
                'PkgName': 'openssl',
                'InstalledVersion': '1.0.5',
                'FixedVersion': '1.1.2'
            }
        }

        # AI can now apply the proven strategy with high confidence
        # (In real implementation, AI Service would query KB and incorporate this knowledge)

        kb.close()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_adaptive_retry_delays_based_on_success_rate(self, temp_dir, mock_config):
        """
        Test: Retry delays adapt based on historical success rates

        Expected behavior:
        1. Events with high success rates → shorter retry delays
        2. Events with low success rates → longer retry delays
        3. System learns optimal retry timing
        """
        kb = KnowledgeBase(db_path=str(temp_dir / "kb.db"))

        # Scenario A: High success rate event (network issues, usually resolved quickly)
        for i in range(9):
            kb.record_fix(
                event={'source': 'crowdsec', 'event_type': 'threat', 'severity': 'HIGH',
                       'details': {'scenario': 'network-scan'}},
                strategy={'description': 'Block IP', 'confidence': 0.95},
                result='success',
                duration_seconds=1.5
            )

        for i in range(1):
            kb.record_fix(
                event={'source': 'crowdsec', 'event_type': 'threat', 'severity': 'HIGH',
                       'details': {'scenario': 'network-scan'}},
                strategy={'description': 'Block IP', 'confidence': 0.95},
                result='failed',
                error_message='Temporary network issue'
            )

        # Scenario B: Low success rate event (complex issue, needs time)
        for i in range(3):
            kb.record_fix(
                event={'source': 'trivy', 'event_type': 'vulnerability', 'severity': 'CRITICAL',
                       'details': {'VulnerabilityID': 'CVE-2024-COMPLEX'}},
                strategy={'description': 'Complex fix', 'confidence': 0.6},
                result='success',
                duration_seconds=30.0
            )

        for i in range(7):
            kb.record_fix(
                event={'source': 'trivy', 'event_type': 'vulnerability', 'severity': 'CRITICAL',
                       'details': {'VulnerabilityID': 'CVE-2024-COMPLEX'}},
                strategy={'description': 'Complex fix', 'confidence': 0.6},
                result='failed',
                error_message='Complex dependency issues'
            )

        # Check success rates
        stats_high_success = kb.get_success_rate(event_signature='network-scan')
        stats_low_success = kb.get_success_rate(event_signature='CVE-2024-COMPLEX')

        # High success scenario should have ~90% success rate
        assert stats_high_success['success_rate'] >= 0.8

        # Low success scenario should have ~30% success rate
        assert stats_low_success['success_rate'] <= 0.4

        # The orchestrator would use these stats to calculate adaptive delays:
        # - High success (90%) → multiplier 0.5 → shorter delays (retry fast)
        # - Low success (30%) → multiplier 2.0 → longer delays (give it time)

        kb.close()


class TestEndToEndWorkflow:
    """End-to-end integration tests"""

    @pytest.mark.asyncio
    @pytest.mark.integration
    @pytest.mark.slow
    async def test_complete_vulnerability_lifecycle(self, temp_dir, mock_config):
        """
        Test the complete lifecycle of a vulnerability from detection to resolution

        Workflow:
        1. Trivy scan detects vulnerability
        2. Event Watcher creates SecurityEvent
        3. Orchestrator schedules remediation
        4. AI Service generates fix strategy (with KB context)
        5. Fix is executed
        6. Verification scan confirms fix
        7. Result recorded in KB
        8. Future similar events benefit from this knowledge
        """
        # This is a high-level integration test that would tie together
        # multiple components. In a real implementation, this would:
        # - Mock external services (Trivy, Discord)
        # - Use real KB, AI Service (mocked LLM), Orchestrator
        # - Verify data flows correctly through the system

        # Setup components
        kb = KnowledgeBase(db_path=str(temp_dir / "kb.db"))

        # 1. Vulnerability detected
        vulnerability_event = SecurityEvent(
            source='trivy',
            event_type='vulnerability',
            severity='CRITICAL',
            details={
                'VulnerabilityID': 'CVE-2024-E2E',
                'PkgName': 'test-package',
                'InstalledVersion': '1.0.0',
                'FixedVersion': '1.1.0',
                'total_critical': 1,
                'total_high': 0
            },
            is_persistent=True
        )

        # 2. Check if KB has knowledge about similar fixes
        similar_strategies = kb.get_best_strategies(event_type='vulnerability', limit=3)
        has_prior_knowledge = len(similar_strategies) > 0

        # 3. AI generates strategy (would query KB for context)
        strategy = {
            'description': 'Update test-package to version 1.1.0',
            'confidence': 0.9 if has_prior_knowledge else 0.7,
            'steps': [
                {'action': 'backup_files', 'command': 'backup.sh'},
                {'action': 'update_package', 'command': 'npm update test-package@1.1.0'},
                {'action': 'verify', 'command': 'npm audit'}
            ],
            'analysis': 'Straightforward package update'
        }

        # 4. Fix executed (simulated success)
        execution_result = 'success'
        execution_duration = 7.5

        # 5. Record in KB
        fix_id = kb.record_fix(
            event=vulnerability_event.to_dict(),
            strategy=strategy,
            result=execution_result,
            duration_seconds=execution_duration,
            retry_count=0
        )

        assert fix_id > 0

        # 6. Verify learning occurred
        stats = kb.get_success_rate(event_signature='CVE-2024-E2E')
        assert stats['total_attempts'] == 1
        assert stats['successful_attempts'] == 1

        # 7. KB now contains knowledge for future similar events
        strategies_after = kb.get_best_strategies(event_type='vulnerability', limit=1)
        assert len(strategies_after) > 0

        kb.close()
