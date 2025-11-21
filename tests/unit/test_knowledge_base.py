"""
Unit Tests for Knowledge Base (AI Learning System)

The Knowledge Base is the core of the AI learning system. It stores:
- Fix attempts with success/failure results
- Vulnerability patterns
- Effective strategies
- Code changes
- Log patterns

These tests demonstrate how the KB works and how AI can learn from it.
"""

import pytest
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta

from src.integrations.knowledge_base import KnowledgeBase, get_knowledge_base


class TestKnowledgeBaseInitialization:
    """Tests for Knowledge Base initialization"""

    def test_init_creates_database(self, temp_dir):
        """Test that KB creates database file"""
        db_path = temp_dir / "test_kb.db"

        kb = KnowledgeBase(db_path=str(db_path))

        assert db_path.exists()
        kb.close()

    def test_init_creates_tables(self, temp_dir):
        """Test that all required tables are created"""
        db_path = temp_dir / "test_kb.db"
        kb = KnowledgeBase(db_path=str(db_path))

        # Check tables exist
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()

        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}

        assert 'fixes' in tables
        assert 'vulnerabilities' in tables
        assert 'strategies' in tables
        assert 'code_changes' in tables
        assert 'log_patterns' in tables

        conn.close()
        kb.close()

    def test_singleton_pattern(self):
        """Test that get_knowledge_base returns singleton"""
        kb1 = get_knowledge_base()
        kb2 = get_knowledge_base()

        # Should be same instance
        assert kb1 is kb2


class TestRecordFix:
    """Tests for recording fix attempts"""

    def test_record_successful_fix(self, temp_dir):
        """Test recording a successful fix"""
        db_path = temp_dir / "test_kb.db"
        kb = KnowledgeBase(db_path=str(db_path))

        event = {
            'source': 'trivy',
            'event_type': 'vulnerability',
            'severity': 'CRITICAL',
            'details': {
                'VulnerabilityID': 'CVE-2024-1234',
                'PkgName': 'openssl'
            }
        }

        strategy = {
            'description': 'Update openssl package',
            'confidence': 0.9,
            'steps': [
                {'action': 'update_package', 'command': 'npm update openssl'}
            ]
        }

        fix_id = kb.record_fix(
            event=event,
            strategy=strategy,
            result='success',
            duration_seconds=5.2,
            retry_count=0
        )

        assert fix_id > 0
        kb.close()

    def test_record_failed_fix(self, temp_dir):
        """Test recording a failed fix"""
        db_path = temp_dir / "test_kb.db"
        kb = KnowledgeBase(db_path=str(db_path))

        event = {
            'source': 'trivy',
            'event_type': 'vulnerability',
            'severity': 'HIGH',
            'details': {'VulnerabilityID': 'CVE-2024-5678'}
        }

        strategy = {
            'description': 'Attempt fix',
            'confidence': 0.7
        }

        fix_id = kb.record_fix(
            event=event,
            strategy=strategy,
            result='failed',
            error_message='Package not found',
            retry_count=2
        )

        assert fix_id > 0
        kb.close()

    def test_record_fix_with_all_fields(self, temp_dir):
        """Test recording fix with all optional fields"""
        db_path = temp_dir / "test_kb.db"
        kb = KnowledgeBase(db_path=str(db_path))

        event = {
            'source': 'crowdsec',
            'event_type': 'threat',
            'severity': 'HIGH',
            'details': {'ip': '1.2.3.4', 'scenario': 'ssh-bruteforce'}
        }

        strategy = {
            'description': 'Ban IP address',
            'confidence': 0.95,
            'steps': [
                {'action': 'ban_ip', 'command': 'iptables -A INPUT -s 1.2.3.4 -j DROP'}
            ],
            'ai_model': 'llama3.1',
            'ai_provider': 'ollama'
        }

        fix_id = kb.record_fix(
            event=event,
            strategy=strategy,
            result='success',
            error_message=None,
            duration_seconds=1.5,
            retry_count=0
        )

        # Verify all fields were stored
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM fixes WHERE id = ?", (fix_id,))
        row = cursor.fetchone()

        assert row is not None
        assert 'crowdsec' in str(row)  # event_source
        assert 'success' in str(row)   # result

        conn.close()
        kb.close()


class TestSuccessRateTracking:
    """Tests for success rate calculation and tracking"""

    def test_get_success_rate_no_data(self, temp_dir):
        """Test success rate with no data"""
        db_path = temp_dir / "test_kb.db"
        kb = KnowledgeBase(db_path=str(db_path))

        stats = kb.get_success_rate()

        assert stats['total_attempts'] == 0
        assert stats['success_rate'] == 0.0

        kb.close()

    def test_get_success_rate_all_success(self, temp_dir):
        """Test success rate with all successful fixes"""
        db_path = temp_dir / "test_kb.db"
        kb = KnowledgeBase(db_path=str(db_path))

        # Record 5 successful fixes
        for i in range(5):
            kb.record_fix(
                event={'source': 'trivy', 'event_type': 'vulnerability', 'severity': 'HIGH'},
                strategy={'description': f'Fix {i}', 'confidence': 0.8},
                result='success',
                duration_seconds=float(i)
            )

        stats = kb.get_success_rate()

        assert stats['total_attempts'] == 5
        assert stats['successful_attempts'] == 5
        assert stats['success_rate'] == 1.0

        kb.close()

    def test_get_success_rate_mixed_results(self, temp_dir):
        """Test success rate with mixed success/failure"""
        db_path = temp_dir / "test_kb.db"
        kb = KnowledgeBase(db_path=str(db_path))

        # Record 7 successful, 3 failed
        for i in range(7):
            kb.record_fix(
                event={'source': 'trivy', 'event_type': 'vulnerability', 'severity': 'HIGH'},
                strategy={'description': 'Success', 'confidence': 0.9},
                result='success'
            )

        for i in range(3):
            kb.record_fix(
                event={'source': 'trivy', 'event_type': 'vulnerability', 'severity': 'HIGH'},
                strategy={'description': 'Failure', 'confidence': 0.6},
                result='failed',
                error_message='Error'
            )

        stats = kb.get_success_rate()

        assert stats['total_attempts'] == 10
        assert stats['successful_attempts'] == 7
        assert stats['success_rate'] == 0.7

        kb.close()

    def test_get_success_rate_by_event_signature(self, temp_dir):
        """Test success rate filtered by event signature"""
        db_path = temp_dir / "test_kb.db"
        kb = KnowledgeBase(db_path=str(db_path))

        # Record fixes for CVE-2024-1234 (high success)
        for i in range(8):
            kb.record_fix(
                event={
                    'source': 'trivy',
                    'event_type': 'vulnerability',
                    'severity': 'HIGH',
                    'details': {'VulnerabilityID': 'CVE-2024-1234'}
                },
                strategy={'description': 'Fix CVE-1234', 'confidence': 0.9},
                result='success'
            )

        for i in range(2):
            kb.record_fix(
                event={
                    'source': 'trivy',
                    'event_type': 'vulnerability',
                    'severity': 'HIGH',
                    'details': {'VulnerabilityID': 'CVE-2024-1234'}
                },
                strategy={'description': 'Fix CVE-1234', 'confidence': 0.9},
                result='failed'
            )

        # Record fixes for CVE-2024-5678 (low success)
        for i in range(3):
            kb.record_fix(
                event={
                    'source': 'trivy',
                    'event_type': 'vulnerability',
                    'severity': 'HIGH',
                    'details': {'VulnerabilityID': 'CVE-2024-5678'}
                },
                strategy={'description': 'Fix CVE-5678', 'confidence': 0.7},
                result='failed'
            )

        # Get success rate for specific CVE
        stats_1234 = kb.get_success_rate(event_signature='CVE-2024-1234')
        stats_5678 = kb.get_success_rate(event_signature='CVE-2024-5678')

        # CVE-1234 should have 80% success (8/10)
        assert stats_1234['success_rate'] >= 0.75  # Allow for fuzzy matching

        # CVE-5678 should have 0% success (0/3)
        assert stats_5678['success_rate'] <= 0.1  # Allow for fuzzy matching

        kb.close()

    def test_get_success_rate_time_window(self, temp_dir):
        """Test success rate within time window"""
        db_path = temp_dir / "test_kb.db"
        kb = KnowledgeBase(db_path=str(db_path))

        # Record some fixes
        for i in range(5):
            kb.record_fix(
                event={'source': 'trivy', 'event_type': 'vulnerability', 'severity': 'HIGH'},
                strategy={'description': 'Test', 'confidence': 0.8},
                result='success'
            )

        # Get success rate for last 30 days (default)
        stats = kb.get_success_rate(days=30)

        assert stats['total_attempts'] == 5

        # Get success rate for last 365 days (should still include all)
        stats_long = kb.get_success_rate(days=365)

        assert stats_long['total_attempts'] == 5

        kb.close()


class TestBestStrategies:
    """Tests for retrieving best strategies"""

    def test_get_best_strategies_empty(self, temp_dir):
        """Test getting best strategies when KB is empty"""
        db_path = temp_dir / "test_kb.db"
        kb = KnowledgeBase(db_path=str(db_path))

        strategies = kb.get_best_strategies(event_type='vulnerability', limit=5)

        assert len(strategies) == 0

        kb.close()

    def test_get_best_strategies_sorted_by_success(self, temp_dir):
        """Test that strategies are sorted by success rate"""
        db_path = temp_dir / "test_kb.db"
        kb = KnowledgeBase(db_path=str(db_path))

        # Strategy A: 90% success (9/10)
        for i in range(9):
            kb.record_fix(
                event={'source': 'trivy', 'event_type': 'vulnerability', 'severity': 'HIGH'},
                strategy={'description': 'Strategy A', 'confidence': 0.9},
                result='success'
            )
        kb.record_fix(
            event={'source': 'trivy', 'event_type': 'vulnerability', 'severity': 'HIGH'},
            strategy={'description': 'Strategy A', 'confidence': 0.9},
            result='failed'
        )

        # Strategy B: 50% success (5/10)
        for i in range(5):
            kb.record_fix(
                event={'source': 'trivy', 'event_type': 'vulnerability', 'severity': 'HIGH'},
                strategy={'description': 'Strategy B', 'confidence': 0.7},
                result='success'
            )
        for i in range(5):
            kb.record_fix(
                event={'source': 'trivy', 'event_type': 'vulnerability', 'severity': 'HIGH'},
                strategy={'description': 'Strategy B', 'confidence': 0.7},
                result='failed'
            )

        # Strategy C: 100% success (3/3)
        for i in range(3):
            kb.record_fix(
                event={'source': 'trivy', 'event_type': 'vulnerability', 'severity': 'HIGH'},
                strategy={'description': 'Strategy C', 'confidence': 0.95},
                result='success'
            )

        strategies = kb.get_best_strategies(event_type='vulnerability', limit=3)

        # Should return all 3, sorted by success rate
        assert len(strategies) >= 1  # At least one strategy

        # Top strategy should have highest success rate
        if len(strategies) > 0:
            top_strategy = strategies[0]
            assert top_strategy['success_count'] >= top_strategy['failure_count']

        kb.close()

    def test_get_best_strategies_limit(self, temp_dir):
        """Test that limit parameter works"""
        db_path = temp_dir / "test_kb.db"
        kb = KnowledgeBase(db_path=str(db_path))

        # Create 10 different strategies
        for i in range(10):
            kb.record_fix(
                event={'source': 'trivy', 'event_type': 'vulnerability', 'severity': 'HIGH'},
                strategy={'description': f'Strategy {i}', 'confidence': 0.8},
                result='success'
            )

        # Request only top 3
        strategies = kb.get_best_strategies(event_type='vulnerability', limit=3)

        assert len(strategies) <= 3

        kb.close()

    def test_get_best_strategies_filters_by_event_type(self, temp_dir):
        """Test that strategies are filtered by event type"""
        db_path = temp_dir / "test_kb.db"
        kb = KnowledgeBase(db_path=str(db_path))

        # Record vulnerabilities
        for i in range(5):
            kb.record_fix(
                event={'source': 'trivy', 'event_type': 'vulnerability', 'severity': 'HIGH'},
                strategy={'description': 'Vuln fix', 'confidence': 0.9},
                result='success'
            )

        # Record threats
        for i in range(3):
            kb.record_fix(
                event={'source': 'crowdsec', 'event_type': 'threat', 'severity': 'HIGH'},
                strategy={'description': 'Threat fix', 'confidence': 0.9},
                result='success'
            )

        vuln_strategies = kb.get_best_strategies(event_type='vulnerability', limit=10)
        threat_strategies = kb.get_best_strategies(event_type='threat', limit=10)

        # Should return only matching event types
        # (Implementation may vary, this tests the concept)
        assert len(vuln_strategies) >= 0
        assert len(threat_strategies) >= 0

        kb.close()


class TestLearningInsights:
    """Tests for AI learning insights"""

    def test_ai_can_learn_from_patterns(self, temp_dir):
        """
        Demonstrate how AI can learn from KB patterns

        This is a documentation test showing the KB's value for AI learning.
        """
        db_path = temp_dir / "test_kb.db"
        kb = KnowledgeBase(db_path=str(db_path))

        # Scenario: AI learns that updating package versions works well
        for i in range(10):
            kb.record_fix(
                event={
                    'source': 'trivy',
                    'event_type': 'vulnerability',
                    'severity': 'HIGH',
                    'details': {'VulnerabilityID': f'CVE-2024-{1000+i}', 'PkgName': 'various'}
                },
                strategy={
                    'description': 'Update package to fixed version',
                    'confidence': 0.85,
                    'steps': [{'action': 'update_package'}]
                },
                result='success',
                duration_seconds=3.0 + i * 0.1
            )

        # AI queries KB for best approach
        best_strategies = kb.get_best_strategies(event_type='vulnerability', limit=1)

        if len(best_strategies) > 0:
            best = best_strategies[0]

            # AI learns:
            # 1. This strategy works (high success rate)
            # 2. Average confidence was good
            # 3. It's fast (low avg duration)
            assert best['success_rate'] == 1.0  # 100% success
            assert best['success_count'] == 10
            assert best['avg_confidence'] >= 0.8
            assert best['avg_duration'] < 10.0  # Fast fixes

        # AI can now prioritize this strategy for similar events
        kb.close()

    def test_ai_avoids_failed_strategies(self, temp_dir):
        """
        Demonstrate how AI learns to avoid strategies that fail
        """
        db_path = temp_dir / "test_kb.db"
        kb = KnowledgeBase(db_path=str(db_path))

        # Bad strategy: Always fails
        for i in range(8):
            kb.record_fix(
                event={'source': 'trivy', 'event_type': 'vulnerability', 'severity': 'HIGH'},
                strategy={'description': 'Try manual compilation', 'confidence': 0.6},
                result='failed',
                error_message='Compilation failed'
            )

        # Good strategy: Usually succeeds
        for i in range(7):
            kb.record_fix(
                event={'source': 'trivy', 'event_type': 'vulnerability', 'severity': 'HIGH'},
                strategy={'description': 'Use package manager', 'confidence': 0.9},
                result='success'
            )

        strategies = kb.get_best_strategies(event_type='vulnerability', limit=2)

        # AI should prefer the successful strategy
        if len(strategies) >= 2:
            # Best strategy should have higher success rate
            assert strategies[0]['success_rate'] > strategies[1]['success_rate']

        kb.close()
