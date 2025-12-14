"""
Knowledge Synthesizer - Extracts long-term patterns from learning data

This system ensures the AI continuously improves by:
- Extracting patterns from raw tracking data
- Storing learned rules in persistent knowledge base
- Synthesizing insights over time
- Enabling meta-learning (learning how to learn)

The knowledge base grows indefinitely while raw data is pruned.
This enables the AI to get smarter over months and years.
"""

import json
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from collections import Counter, defaultdict

logger = logging.getLogger('shadowops.knowledge')


# Knowledge Base file (grows indefinitely with compressed knowledge)
KNOWLEDGE_BASE_FILE = Path(__file__).parent.parent.parent / "data" / "knowledge_base.json"

# Tracking files (pruned to last 500-1000 entries)
AUTO_FIX_TRACKING = Path(__file__).parent.parent.parent / "data" / "auto_fix_tracking.json"
RAM_TRACKING = Path(__file__).parent.parent.parent / "data" / "ram_tracking.json"


class KnowledgeSynthesizer:
    """
    Synthesizes long-term knowledge from learning data.

    Prevents knowledge loss by extracting patterns before data is pruned.
    Enables continuous improvement over months/years.
    """

    def __init__(self, ai_service=None):
        """
        Initialize Knowledge Synthesizer

        Args:
            ai_service: AI service for advanced pattern analysis (optional)
        """
        self.ai_service = ai_service
        self.logger = logger

        # Load existing knowledge base
        self.knowledge = self._load_knowledge_base()

    def _load_knowledge_base(self) -> Dict:
        """
        Load persistent knowledge base.

        Returns:
            Knowledge base dict with learned patterns
        """
        if not KNOWLEDGE_BASE_FILE.exists():
            return {
                "version": "1.0",
                "last_synthesis": None,
                "synthesis_count": 0,

                # Auto-Fix knowledge
                "fix_patterns": {
                    # project_name: {
                    #   "success_rate": 0.85,
                    #   "best_practices": ["pattern1", "pattern2"],
                    #   "common_failures": ["reason1", "reason2"],
                    #   "recommended_tests": ["test1", "test2"]
                    # }
                },

                # RAM Management knowledge
                "ram_patterns": {
                    # model_name: {
                    #   "avg_ram_required_gb": 4.8,
                    #   "best_cleanup_method": "kill_ollama_runner",
                    #   "failure_rate": 0.1,
                    #   "optimal_conditions": {...}
                    # }
                },

                # Security knowledge
                "security_patterns": {
                    # "persistent_attackers": [
                    #   {"ip": "1.2.3.4", "first_seen": "...", "total_bans": 10}
                    # ],
                    # "attack_trends": {...},
                    # "geographic_patterns": {...}
                },

                # Meta-learning (learning about learning)
                "meta_learning": {
                    "synthesis_intervals": [],  # How often synthesis happens
                    "pattern_quality_scores": [],  # How good extracted patterns are
                    "learning_velocity": None  # How fast the system improves
                }
            }

        try:
            return json.loads(KNOWLEDGE_BASE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning("⚠️ Corrupted knowledge base - creating fresh one")
            return self._load_knowledge_base()  # Recursive call with fresh KB

    def _save_knowledge_base(self) -> None:
        """
        Save knowledge base atomically.
        Thread-safe with temp file + rename.
        """
        try:
            import tempfile
            import os

            KNOWLEDGE_BASE_FILE.parent.mkdir(parents=True, exist_ok=True)

            with tempfile.NamedTemporaryFile(mode='w', delete=False,
                                            dir=KNOWLEDGE_BASE_FILE.parent,
                                            suffix='.tmp') as tmp:
                json.dump(self.knowledge, tmp, indent=2, ensure_ascii=False)
                tmp_path = tmp.name

            os.replace(tmp_path, str(KNOWLEDGE_BASE_FILE))
            logger.info(f"💾 Knowledge base saved ({KNOWLEDGE_BASE_FILE})")

        except Exception as e:
            logger.error(f"❌ Failed to save knowledge base: {e}", exc_info=True)

    async def synthesize_knowledge(self) -> Dict[str, int]:
        """
        Main synthesis method - extracts patterns from all tracking data.

        This is the core of long-term learning. It:
        1. Loads raw tracking data
        2. Extracts high-level patterns
        3. Updates knowledge base
        4. Saves compressed knowledge

        Returns:
            Stats about synthesis (patterns_extracted, insights_generated, etc.)
        """
        logger.info("🧠 Starting knowledge synthesis...")

        stats = {
            "fix_patterns_extracted": 0,
            "ram_patterns_extracted": 0,
            "security_patterns_extracted": 0,
            "meta_insights": 0
        }

        # 1. Synthesize Auto-Fix knowledge
        fix_patterns = await self._synthesize_fix_knowledge()
        stats["fix_patterns_extracted"] = len(fix_patterns)

        # 2. Synthesize RAM Management knowledge
        ram_patterns = await self._synthesize_ram_knowledge()
        stats["ram_patterns_extracted"] = len(ram_patterns)

        # 3. Synthesize Security knowledge
        security_patterns = await self._synthesize_security_knowledge()
        stats["security_patterns_extracted"] = len(security_patterns)

        # 4. Meta-learning (learn about learning itself)
        meta_insights = self._extract_meta_learning_insights(stats)
        stats["meta_insights"] = meta_insights

        # Update synthesis metadata
        self.knowledge["last_synthesis"] = datetime.utcnow().isoformat()
        self.knowledge["synthesis_count"] += 1

        # Save knowledge base
        self._save_knowledge_base()

        logger.info(
            f"✅ Knowledge synthesis complete: "
            f"{stats['fix_patterns_extracted']} fix patterns, "
            f"{stats['ram_patterns_extracted']} RAM patterns, "
            f"{stats['security_patterns_extracted']} security patterns"
        )

        return stats

    async def _synthesize_fix_knowledge(self) -> List[Dict]:
        """
        Extract long-term patterns from Auto-Fix tracking data.

        Returns:
            List of extracted patterns
        """
        patterns = []

        if not AUTO_FIX_TRACKING.exists():
            return patterns

        try:
            tracking_data = json.loads(AUTO_FIX_TRACKING.read_text(encoding="utf-8"))
            fix_history = tracking_data.get("fix_history", [])

            if not fix_history:
                return patterns

            # Group by project
            by_project = defaultdict(list)
            for entry in fix_history:
                by_project[entry["project"]].append(entry)

            # Extract patterns per project
            for project, entries in by_project.items():
                if len(entries) < 5:  # Need at least 5 samples
                    continue

                # Calculate statistics
                total = len(entries)
                successful = sum(1 for e in entries if e["success"])
                success_rate = successful / total

                # Identify what works
                successful_entries = [e for e in entries if e["success"]]
                failed_entries = [e for e in entries if not e["success"]]

                # Extract common patterns from successful fixes
                successful_actions = []
                for e in successful_entries:
                    successful_actions.extend(e.get("actions", []))

                common_success_actions = [
                    action for action, count in Counter(successful_actions).most_common(5)
                    if count >= 2  # Mentioned at least twice
                ]

                # Extract common failure reasons
                failure_summaries = [e.get("summary", "") for e in failed_entries]

                # Build pattern
                pattern = {
                    "project": project,
                    "sample_size": total,
                    "success_rate": round(success_rate, 3),
                    "best_practices": common_success_actions,
                    "failure_count": len(failed_entries),
                    "last_updated": datetime.utcnow().isoformat()
                }

                # Update knowledge base
                self.knowledge["fix_patterns"][project] = pattern
                patterns.append(pattern)

                logger.info(
                    f"  📊 Extracted fix pattern for {project}: "
                    f"{success_rate*100:.1f}% success rate ({total} samples)"
                )

        except Exception as e:
            logger.error(f"Error synthesizing fix knowledge: {e}", exc_info=True)

        return patterns

    async def _synthesize_ram_knowledge(self) -> List[Dict]:
        """
        Extract long-term patterns from RAM tracking data.

        Returns:
            List of extracted patterns
        """
        patterns = []

        if not RAM_TRACKING.exists():
            return patterns

        try:
            tracking_data = json.loads(RAM_TRACKING.read_text(encoding="utf-8"))
            ram_events = tracking_data.get("ram_events", [])

            if not ram_events:
                return patterns

            # Group by model
            by_model = defaultdict(list)
            for event in ram_events:
                by_model[event["model"]].append(event)

            # Extract patterns per model
            for model, events in by_model.items():
                if len(events) < 3:  # Need at least 3 samples
                    continue

                # Calculate statistics
                total_failures = len(events)
                events_with_method = [e for e in events if e.get("method_used")]
                successful_cleanups = [e for e in events_with_method if e.get("success")]

                # Average RAM required
                ram_values = [e.get("ram_total_gb") for e in events if e.get("ram_total_gb")]
                avg_ram_total = sum(ram_values) / len(ram_values) if ram_values else None

                ram_avail_values = [e.get("ram_available_gb") for e in events if e.get("ram_available_gb")]
                avg_ram_available = sum(ram_avail_values) / len(ram_avail_values) if ram_avail_values else None

                # Best cleanup method
                methods_used = [e["method_used"] for e in events_with_method]
                if methods_used:
                    method_success_rate = {}
                    for method in set(methods_used):
                        method_events = [e for e in events_with_method if e["method_used"] == method]
                        successes = sum(1 for e in method_events if e.get("success"))
                        method_success_rate[method] = successes / len(method_events) if method_events else 0

                    best_method = max(method_success_rate.items(), key=lambda x: x[1])[0] if method_success_rate else None
                else:
                    best_method = None

                # Build pattern
                pattern = {
                    "model": model,
                    "total_failures": total_failures,
                    "avg_ram_total_gb": round(avg_ram_total, 2) if avg_ram_total else None,
                    "avg_ram_available_gb": round(avg_ram_available, 2) if avg_ram_available else None,
                    "best_cleanup_method": best_method,
                    "cleanup_success_rate": len(successful_cleanups) / len(events_with_method) if events_with_method else 0,
                    "last_updated": datetime.utcnow().isoformat()
                }

                # Update knowledge base
                self.knowledge["ram_patterns"][model] = pattern
                patterns.append(pattern)

                logger.info(
                    f"  📊 Extracted RAM pattern for {model}: "
                    f"Best method: {best_method}, "
                    f"{total_failures} failures tracked"
                )

        except Exception as e:
            logger.error(f"Error synthesizing RAM knowledge: {e}", exc_info=True)

        return patterns

    async def _synthesize_security_knowledge(self) -> List[Dict]:
        """
        Extract long-term security patterns from security event analysis.

        This would analyze historical security insights and extract long-term trends.
        For now, placeholder that can be extended with specific security data sources.

        Returns:
            List of extracted patterns
        """
        patterns = []

        # This would integrate with security event tracking
        # For now, we return empty list as we're building the foundation

        return patterns

    def _extract_meta_learning_insights(self, synthesis_stats: Dict) -> int:
        """
        Meta-learning: Learn about the learning process itself.

        Analyzes:
        - How often synthesis happens
        - Quality of extracted patterns
        - Learning velocity (rate of improvement)

        Returns:
            Number of meta-insights generated
        """
        insights_count = 0

        try:
            # Track synthesis interval
            if self.knowledge["last_synthesis"]:
                last_synthesis = datetime.fromisoformat(self.knowledge["last_synthesis"])
                interval_hours = (datetime.utcnow() - last_synthesis).total_seconds() / 3600

                self.knowledge["meta_learning"]["synthesis_intervals"].append({
                    "timestamp": datetime.utcnow().isoformat(),
                    "interval_hours": round(interval_hours, 2),
                    "patterns_extracted": sum(synthesis_stats.values())
                })

                # Keep only last 100 intervals
                if len(self.knowledge["meta_learning"]["synthesis_intervals"]) > 100:
                    self.knowledge["meta_learning"]["synthesis_intervals"] = \
                        self.knowledge["meta_learning"]["synthesis_intervals"][-100:]

                insights_count += 1

            # Calculate learning velocity (patterns per day)
            intervals = self.knowledge["meta_learning"]["synthesis_intervals"]
            if len(intervals) >= 5:
                recent = intervals[-5:]
                total_patterns = sum(i["patterns_extracted"] for i in recent)
                total_hours = sum(i["interval_hours"] for i in recent)

                if total_hours > 0:
                    patterns_per_day = (total_patterns / total_hours) * 24
                    self.knowledge["meta_learning"]["learning_velocity"] = round(patterns_per_day, 2)

                    logger.info(f"  🚀 Learning velocity: {patterns_per_day:.2f} patterns/day")
                    insights_count += 1

        except Exception as e:
            logger.error(f"Error in meta-learning: {e}", exc_info=True)

        return insights_count

    def get_fix_recommendations(self, project: str) -> Dict[str, Any]:
        """
        Get learned recommendations for a specific project.

        Args:
            project: Project name

        Returns:
            Dict with recommendations based on learned patterns
        """
        if project not in self.knowledge["fix_patterns"]:
            return {
                "has_data": False,
                "message": "No historical data for this project yet"
            }

        pattern = self.knowledge["fix_patterns"][project]

        return {
            "has_data": True,
            "success_rate": pattern["success_rate"],
            "sample_size": pattern["sample_size"],
            "best_practices": pattern["best_practices"],
            "confidence": "high" if pattern["sample_size"] >= 20 else "medium" if pattern["sample_size"] >= 10 else "low"
        }

    def get_ram_recommendations(self, model: str) -> Dict[str, Any]:
        """
        Get learned recommendations for RAM management of a specific model.

        Args:
            model: Model name (e.g., "llama3.1")

        Returns:
            Dict with recommendations based on learned patterns
        """
        if model not in self.knowledge["ram_patterns"]:
            return {
                "has_data": False,
                "message": "No historical RAM data for this model yet"
            }

        pattern = self.knowledge["ram_patterns"][model]

        return {
            "has_data": True,
            "best_cleanup_method": pattern["best_cleanup_method"],
            "cleanup_success_rate": pattern["cleanup_success_rate"],
            "avg_ram_required_gb": pattern["avg_ram_total_gb"],
            "total_failures_tracked": pattern["total_failures"]
        }
