"""
Adapter: Bestehende Fixer -> FixProvider Interface
Wrappen existierende Fixer als FixProvider, inkl. No-Op-Detection wo moeglich.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

from .models import SecurityEvent, PhaseType, FixResult
from .providers import FixProvider

logger = logging.getLogger('shadowops.fixer_adapters')


class Fail2banFixerAdapter(FixProvider):
    """Adapter fuer Fail2banFixer mit No-Op-Detection."""

    def __init__(self, fixer):
        self.fixer = fixer

    async def execute(self, event, strategy, context=None):
        phase_type = context.get('phase_type', PhaseType.FIX) if context else PhaseType.FIX

        # No-Op Check: Aktuelle Config vs. Ziel vergleichen
        try:
            jail_name = event.details.get('jail', 'sshd') if hasattr(event, 'details') else 'sshd'
            current_config = await self.fixer._get_jail_config(jail_name)
            if current_config and hasattr(self.fixer, 'hardened_config'):
                target = self.fixer.hardened_config
                if (current_config.get('maxretry') == target.get('maxretry')
                        and current_config.get('bantime') == target.get('bantime')):
                    return FixResult.no_op(
                        f"Jail {jail_name} bereits gehaertet "
                        f"(maxretry={target['maxretry']}, bantime={target['bantime']})",
                        phase_type=phase_type,
                    )
        except Exception as e:
            logger.debug(f"No-Op-Check fehlgeschlagen: {e}")

        # Delegiere an Original-Fixer
        start = time.time()
        event_dict = event.to_dict() if hasattr(event, 'to_dict') else event
        result = await self.fixer.fix(event_dict, strategy)
        duration = time.time() - start

        if result.get('status') == 'success':
            return FixResult.success(
                result.get('message', 'Fail2ban fix applied'),
                phase_type=phase_type, duration_seconds=duration)
        return FixResult.failed(
            result.get('error', result.get('message', 'Fail2ban fix failed')),
            phase_type=phase_type, duration_seconds=duration)


class TrivyFixerAdapter(FixProvider):
    """Adapter fuer TrivyFixer."""

    def __init__(self, fixer):
        self.fixer = fixer

    async def execute(self, event, strategy, context=None):
        phase_type = context.get('phase_type', PhaseType.FIX) if context else PhaseType.FIX
        start = time.time()
        event_dict = event.to_dict() if hasattr(event, 'to_dict') else event
        result = await self.fixer.fix(event_dict, strategy)
        duration = time.time() - start
        if result.get('status') == 'success':
            return FixResult.success(
                result.get('message', 'Trivy fix applied'),
                phase_type=phase_type, duration_seconds=duration)
        return FixResult.failed(
            result.get('error', result.get('message', 'Trivy fix failed')),
            phase_type=phase_type, duration_seconds=duration)


class CrowdSecFixerAdapter(FixProvider):
    """Adapter fuer CrowdSecFixer."""

    def __init__(self, fixer):
        self.fixer = fixer

    async def execute(self, event, strategy, context=None):
        phase_type = context.get('phase_type', PhaseType.FIX) if context else PhaseType.FIX
        start = time.time()
        event_dict = event.to_dict() if hasattr(event, 'to_dict') else event
        result = await self.fixer.fix(event_dict, strategy)
        duration = time.time() - start
        if result.get('status') == 'success':
            return FixResult.success(
                result.get('message', 'CrowdSec fix applied'),
                phase_type=phase_type, duration_seconds=duration)
        return FixResult.failed(
            result.get('error', result.get('message', 'CrowdSec fix failed')),
            phase_type=phase_type, duration_seconds=duration)


class AideFixerAdapter(FixProvider):
    """Adapter fuer AideFixer."""

    def __init__(self, fixer):
        self.fixer = fixer

    async def execute(self, event, strategy, context=None):
        phase_type = context.get('phase_type', PhaseType.FIX) if context else PhaseType.FIX
        start = time.time()
        event_dict = event.to_dict() if hasattr(event, 'to_dict') else event
        result = await self.fixer.fix(event_dict, strategy)
        duration = time.time() - start
        if result.get('status') == 'success':
            return FixResult.success(
                result.get('message', 'AIDE fix applied'),
                phase_type=phase_type, duration_seconds=duration)
        return FixResult.failed(
            result.get('error', result.get('message', 'AIDE fix failed')),
            phase_type=phase_type, duration_seconds=duration)


class WalGFixerAdapter(FixProvider):
    """Adapter fuer WalGFixer."""

    def __init__(self, fixer):
        self.fixer = fixer

    async def execute(self, event, strategy, context=None):
        phase_type = context.get('phase_type', PhaseType.FIX) if context else PhaseType.FIX
        start = time.time()
        event_dict = event.to_dict() if hasattr(event, 'to_dict') else event
        result = await self.fixer.fix(event_dict, strategy)
        duration = time.time() - start
        if result.get('status') == 'success':
            return FixResult.success(
                result.get('message', 'WAL-G fix applied'),
                phase_type=phase_type, duration_seconds=duration)
        return FixResult.failed(
            result.get('error', result.get('message', 'WAL-G fix failed')),
            phase_type=phase_type, duration_seconds=duration)
