"""Agent-Adapter — pro Agent-Typ ein Adapter mit Detect/Prompt/Merge/Channel-Logic."""
from .base import AgentAdapter, AgentDetection, MergeDecision

__all__ = ["AgentAdapter", "AgentDetection", "MergeDecision"]
