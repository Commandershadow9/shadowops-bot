"""Fixer-Registry — Plugin-System fuer Fix-Provider."""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from .models import PhaseType
from .providers import FixProvider, NoOpProvider


class FixerRegistry:
    """
    Registriert FixProvider pro (source, phase_type) Kombination.
    Lookup: [NoOp] + [exakte Matches] + [source-Fallbacks]
    """

    def __init__(self):
        self._providers: Dict[Tuple[str, Optional[PhaseType]], List[FixProvider]] = defaultdict(list)
        self._noop: Optional[NoOpProvider] = None

    def register(self, source: str, phase_type: Optional[PhaseType], provider: FixProvider):
        """Registriert einen Provider fuer source + optionale Phase."""
        self._providers[(source, phase_type)].append(provider)

    def register_noop(self, provider: NoOpProvider):
        """Registriert den NoOp-Provider (wird immer zuerst geprueft)."""
        self._noop = provider

    def get_providers(self, source: str, phase_type: PhaseType) -> List[FixProvider]:
        """
        Gibt die Provider-Chain zurueck:
        1. NoOp (falls registriert)
        2. Exakte Matches (source + phase_type)
        3. Source-Fallbacks (source + None)
        """
        chain: List[FixProvider] = []
        if self._noop:
            chain.append(self._noop)
        chain.extend(self._providers.get((source, phase_type), []))
        chain.extend(self._providers.get((source, None), []))
        return chain

    def list_registered(self) -> Dict[str, List[str]]:
        """Gibt alle registrierten Provider als Debug-Info zurueck."""
        result = {}
        for (source, ptype), providers in self._providers.items():
            key = f"{source}/{ptype.value if ptype else '*'}"
            result[key] = [type(p).__name__ for p in providers]
        return result
