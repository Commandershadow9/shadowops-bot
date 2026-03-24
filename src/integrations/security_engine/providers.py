"""Fix-Provider ABC und Basis-Implementierungen."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from .models import SecurityEvent, PhaseType, FixResult


class FixProvider(ABC):
    """Basis-Klasse fuer alle Fix-Provider (wie AIProviderChain im Agent Framework)."""

    @abstractmethod
    async def execute(
        self,
        event: SecurityEvent,
        strategy: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> Optional[FixResult]:
        """
        Fuehrt Fix aus.
        Returns: FixResult bei Erfolg/Fehler, None wenn nicht zustaendig.
        None signalisiert: naechsten Provider in der Chain versuchen.
        """
        ...


class NoOpProvider(FixProvider):
    """Erkennt wenn ein Fix nicht noetig ist (Config bereits korrekt)."""

    async def execute(self, event, strategy, context=None):
        if not context:
            return None
        current = context.get('current_config')
        target = context.get('target_config')
        if current is None or target is None:
            return None
        if current == target:
            return FixResult.no_op(
                f"Config bereits korrekt: {current}",
                phase_type=PhaseType.FIX,
            )
        return None


class BashFixProvider(FixProvider):
    """Fuehrt Fixes via CommandExecutor aus (sudo-Commands)."""

    def __init__(self, command_executor):
        self.executor = command_executor

    async def execute(self, event, strategy, context=None):
        commands = strategy.get('commands', [])
        if not commands:
            return None

        import time
        start = time.time()
        errors = []

        for cmd in commands:
            result = await self.executor.execute(cmd, sudo=True, timeout=30)
            if not result.get('success', False):
                errors.append(result.get('error', f'Command failed: {cmd}'))

        duration = time.time() - start
        phase_type = context.get('phase_type', PhaseType.FIX) if context else PhaseType.FIX

        if errors:
            return FixResult.failed(
                '; '.join(errors),
                phase_type=phase_type,
                duration_seconds=duration,
            )
        return FixResult.success(
            strategy.get('description', 'Bash fix applied'),
            phase_type=phase_type,
            duration_seconds=duration,
        )
