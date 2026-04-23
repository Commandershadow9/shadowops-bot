"""Leichtgewichtiger Circuit Breaker (adaptiert vom SmartQueue-Pattern).

Oeffnet nach N konsekutiven Fehlern und schliesst automatisch
nach einem Timeout-Intervall.
"""

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger('shadowops')


class CircuitBreaker:

    def __init__(self, name: str, threshold: int = 5,
                 timeout_seconds: int = 3600):
        self.name = name
        self.threshold = threshold
        self.timeout_seconds = timeout_seconds
        self.consecutive_failures = 0
        self.is_open = False
        self.reset_at = None

    def record_success(self) -> None:
        """Setzt den Fehlerzähler zurück und schließt den Circuit Breaker,
        falls er offen war.
        """
        self.consecutive_failures = 0
        if self.is_open:
            self.is_open = False
            self.reset_at = None
            logger.info("CircuitBreaker[%s] geschlossen nach Erfolg", self.name)

    def record_failure(self) -> None:
        """Erhöht den Fehlerzähler und öffnet den Circuit Breaker,
        wenn der Schwellenwert erreicht ist.
        """
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.threshold and not self.is_open:
            self.is_open = True
            self.reset_at = (
                datetime.now(timezone.utc)
                + timedelta(seconds=self.timeout_seconds)
            )
            logger.warning(
                "CircuitBreaker[%s] OFFEN nach %d Fehlern — Reset um %s",
                self.name, self.consecutive_failures,
                self.reset_at.isoformat(),
            )

    def allow_request(self) -> bool:
        """Prüft, ob eine Anfrage durchgelassen werden darf (Circuit geschlossen
        oder Timeout abgelaufen).

        Returns:
            bool: True wenn der Circuit Breaker geschlossen oder der Reset-Timeout abgelaufen ist, False wenn Anfragen aktiv blockiert werden.
        """
        if not self.is_open:
            return True
        if self.reset_at and datetime.now(timezone.utc) >= self.reset_at:
            self.is_open = False
            self.consecutive_failures = 0
            self.reset_at = None
            logger.info(
                "CircuitBreaker[%s] Reset (Timeout abgelaufen)", self.name
            )
            return True
        return False
