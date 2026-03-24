import pytest
import time
from src.integrations.security_engine.circuit_breaker import CircuitBreaker


class TestCircuitBreaker:
    def test_starts_closed(self):
        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=60)
        assert cb.is_closed is True
        assert cb.can_attempt is True

    def test_opens_after_threshold(self):
        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=60)
        cb.record_failure('test')
        cb.record_failure('test')
        assert cb.is_closed is True
        cb.record_failure('test')
        assert cb.is_closed is False
        assert cb.can_attempt is False

    def test_success_resets(self):
        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=60)
        cb.record_failure('test')
        cb.record_failure('test')
        cb.record_success('test')
        assert cb.failure_count == 0
        assert cb.is_closed is True

    def test_per_key_tracking(self):
        cb = CircuitBreaker(failure_threshold=2, cooldown_seconds=60)
        cb.record_failure('fail2ban')
        cb.record_failure('fail2ban')
        assert cb.is_open_for('fail2ban') is True
        assert cb.is_open_for('trivy') is False

    def test_cooldown_resets(self):
        cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=0.1)
        cb.record_failure('test')
        assert cb.can_attempt is False
        time.sleep(0.15)
        assert cb.can_attempt is True

    def test_get_status(self):
        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=60)
        cb.record_failure('test')
        status = cb.get_status()
        assert status['failure_count'] == 1
        assert status['is_open'] is False
        assert status['threshold'] == 3
