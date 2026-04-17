"""AgentDetector — waehlt den richtigen Adapter via Confidence-Ranking.

Entscheidet welcher Adapter einen PR uebernimmt. Mehrere Adapter koennen sich
"matched" melden (z.B. ein PR mit `## Summary` Body von Codex-Pattern + manuell
gesetztem `jules` Label) — der mit der hoechsten Confidence gewinnt.

Schwelle: 0.8 — niedrigere Confidence wird ignoriert (kein PR > kein Adapter).
"""
from __future__ import annotations

from typing import List, Optional

from .adapters.base import AgentAdapter


class AgentDetector:
    """First-match-wins mit Confidence-Schwelle."""

    CONFIDENCE_THRESHOLD = 0.8

    def __init__(self, adapters: List[AgentAdapter]):
        """
        Args:
            adapters: Liste von AgentAdapter-Instanzen.
                Reihenfolge ist nicht entscheidend (Confidence-Ranking).
        """
        self.adapters = adapters

    def detect(self, pr_payload: dict) -> Optional[AgentAdapter]:
        """Findet den passenden Adapter fuer einen PR.

        Returns:
            Adapter-Instanz mit hoechster Confidence ueber Schwelle, oder None.
        """
        matches = []
        for adapter in self.adapters:
            d = adapter.detect(pr_payload)
            if d.matched and d.confidence >= self.CONFIDENCE_THRESHOLD:
                matches.append((d.confidence, adapter))

        if not matches:
            return None

        # Hoechste Confidence gewinnt
        matches.sort(key=lambda x: -x[0])
        return matches[0][1]

    def detect_all(self, pr_payload: dict) -> List[tuple]:
        """Debug-Helper: liefert alle Adapter-Detections (auch unter Schwelle).

        Returns:
            Liste von (adapter_name, confidence, matched) tuples.
        """
        return [
            (a.agent_name, a.detect(pr_payload).confidence, a.detect(pr_payload).matched)
            for a in self.adapters
        ]
