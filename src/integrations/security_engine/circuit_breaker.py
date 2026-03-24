"""Circuit Breaker — verhindert Retry-Loops (Agent Framework Pattern)"""
from __future__ import annotations
import time
from collections import defaultdict
from typing import Any, Dict


class CircuitBreaker:
    """
    Per-Key Circuit Breaker.
    Nach failure_threshold Fehlern für einen Key → Sperre für cooldown_seconds.
    Ein Erfolg resettet den Counter für den Key.
    """

    def __init__(self, failure_threshold: int = 5, cooldown_seconds: int = 3600):
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._failures: Dict[str, int] = defaultdict(int)
        self._opened_at: Dict[str, float] = {}

    def record_failure(self, key: str = '_global') -> None:
        self._failures[key] += 1
        if self._failures[key] >= self.failure_threshold:
            self._opened_at[key] = time.time()

    def record_success(self, key: str = '_global') -> None:
        self._failures[key] = 0
        self._opened_at.pop(key, None)

    def is_open_for(self, key: str) -> bool:
        if key not in self._opened_at:
            return False
        elapsed = time.time() - self._opened_at[key]
        if elapsed >= self.cooldown_seconds:
            self._failures[key] = 0
            del self._opened_at[key]
            return False
        return True

    @property
    def is_closed(self) -> bool:
        return not any(self.is_open_for(k) for k in list(self._opened_at))

    @property
    def can_attempt(self) -> bool:
        return self.is_closed

    @property
    def failure_count(self) -> int:
        return sum(self._failures.values())

    def get_status(self) -> Dict[str, Any]:
        return {
            'is_open': not self.is_closed,
            'failure_count': self.failure_count,
            'threshold': self.failure_threshold,
            'cooldown_seconds': self.cooldown_seconds,
            'open_keys': [k for k in self._opened_at if self.is_open_for(k)],
        }
