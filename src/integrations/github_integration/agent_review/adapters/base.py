"""AgentAdapter Base-Klasse + Datentypen.

Jeder Agent-Typ (Jules, SEO, Codex, ...) implementiert diesen Adapter.
Die gemeinsame Pipeline ruft die Adapter-Methoden auf, ohne den Agent-Typ zu kennen.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class MergeDecision(Enum):
    """Was soll mit einem approved PR passieren?"""
    AUTO = "auto"        # Auto-Merge sofort nach Claude-Approval
    MANUAL = "manual"    # Label setzen, auf Human warten
    BLOCKED = "blocked"  # Nie mergen (z.B. frozen Project)


@dataclass
class AgentDetection:
    """Ergebnis von detect() — passt der Adapter auf diesen PR?"""
    matched: bool
    confidence: float                          # 0.0 - 1.0
    metadata: Optional[Dict[str, Any]] = None


class AgentAdapter(ABC):
    """Base-Klasse fuer Agent-spezifische Review-Logic.

    Jede Subklasse implementiert die abstrakten Methoden. Die gemeinsame Pipeline
    in `reviewer.py` orchestriert: detect → build_prompt → AI-Call → merge_policy
    → discord_channel.
    """

    @property
    @abstractmethod
    def agent_name(self) -> str:
        """Eindeutiger Name des Agents (z.B. 'jules', 'seo', 'codex')."""

    @abstractmethod
    def detect(self, pr_payload: Dict[str, Any]) -> AgentDetection:
        """Prueft ob dieser Adapter den PR verarbeiten soll.

        Args:
            pr_payload: Vollstaendiges GitHub PR-Objekt aus dem Webhook.

        Returns:
            AgentDetection mit matched/confidence/metadata.
        """

    @abstractmethod
    def build_prompt(
        self,
        *,
        diff: str,
        pr_payload: Dict[str, Any],
        finding_context: Dict[str, Any],
        iteration: int,
        few_shot: List[Dict[str, Any]],
        knowledge: List[str],
        project: str,
    ) -> str:
        """Baut den Claude-Prompt fuer diesen Agent-Typ."""

    @abstractmethod
    def model_preference(
        self, pr_payload: Dict[str, Any], diff_len: int,
    ) -> Tuple[str, str]:
        """Returns (primary_model_class, fallback_model_class).

        Modell-Klassen aus AIEngine-Config: 'fast' | 'standard' | 'thinking'.
        """

    @abstractmethod
    def merge_policy(
        self,
        review: Dict[str, Any],
        pr_payload: Dict[str, Any],
        project: str,
    ) -> MergeDecision:
        """Entscheidet ob Auto-Merge erlaubt ist nach Claude-Approval."""

    @abstractmethod
    def discord_channel(self, verdict: str) -> str:
        """Discord-Channel-Name fuer Review-Embed."""

    def iteration_mention(self) -> Optional[str]:
        """Optional: @mention im Revision-Comment damit Agent automatisch iteriert.

        Beispiel: Jules braucht '@google-labs-jules', SEO-Agent reagiert nicht
        auf Comments (returnt None).
        """
        return None
