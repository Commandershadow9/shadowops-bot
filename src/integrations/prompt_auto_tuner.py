"""
Automatic Prompt Tuning System.

Analyzes feedback and automatically adjusts prompts for better performance.
"""

import logging
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from collections import Counter

logger = logging.getLogger('shadowops')


class PromptAutoTuner:
    """
    Automatically tunes prompts based on performance feedback.
    """

    def __init__(self, data_dir: Path, ab_testing, trainer):
        self.data_dir = data_dir
        self.ab_testing = ab_testing
        self.trainer = trainer

        self.tuning_log_file = self.data_dir / 'prompt_tuning_log.jsonl'

        logger.info("✅ Prompt Auto-Tuner initialized")

    def analyze_performance_patterns(self, project: Optional[str] = None,
                                     days: int = 30) -> Dict:
        """
        Analyze patterns in high vs low performing patch notes.

        Args:
            project: Optional project filter
            days: Number of days to analyze

        Returns:
            Dict with insights about what works and what doesn't
        """
        cutoff_date = datetime.utcnow() - timedelta(days=days)

        # Get training examples
        high_performers = []  # Score >= 80
        low_performers = []   # Score < 60

        if not self.trainer.training_data_file.exists():
            return {}

        try:
            with open(self.trainer.training_data_file, 'r', encoding='utf-8') as f:
                for line in f:
                    try:
                        example = json.loads(line)

                        # Filter by project and date
                        if project and example.get('project') != project:
                            continue

                        timestamp = datetime.fromisoformat(example.get('timestamp', ''))
                        if timestamp < cutoff_date:
                            continue

                        score = example.get('quality_score', 0)
                        if score >= 80:
                            high_performers.append(example)
                        elif score < 60:
                            low_performers.append(example)
                    except:
                        continue
        except Exception as e:
            logger.error(f"Failed to analyze performance patterns: {e}")
            return {}

        # Analyze patterns
        patterns = {
            'high_performers': {
                'count': len(high_performers),
                'avg_length': self._avg([len(e['generated_notes']) for e in high_performers]),
                'common_words': self._get_common_words([e['generated_notes'] for e in high_performers]),
                'structure_patterns': self._analyze_structure([e['generated_notes'] for e in high_performers]),
            },
            'low_performers': {
                'count': len(low_performers),
                'avg_length': self._avg([len(e['generated_notes']) for e in low_performers]),
                'common_words': self._get_common_words([e['generated_notes'] for e in low_performers]),
                'structure_patterns': self._analyze_structure([e['generated_notes'] for e in low_performers]),
            }
        }

        # Identify differences
        patterns['insights'] = self._generate_insights(patterns)

        return patterns

    def _avg(self, values: List[float]) -> float:
        """Calculate average."""
        return sum(values) / len(values) if values else 0

    def _get_common_words(self, texts: List[str], top_n: int = 10) -> List[Tuple[str, int]]:
        """Get most common words from texts."""
        all_words = []
        for text in texts:
            # Simple word extraction (lowercase, alphanumeric)
            words = [w.lower() for w in text.split() if w.isalnum() and len(w) > 3]
            all_words.extend(words)

        counter = Counter(all_words)
        return counter.most_common(top_n)

    def _analyze_structure(self, texts: List[str]) -> Dict:
        """Analyze structural patterns."""
        bullet_counts = [text.count('•') + text.count('- ') for text in texts]
        category_counts = [text.count('**🆕') + text.count('**🐛') + text.count('**⚡') for text in texts]
        sub_bullet_counts = [text.count('  -') + text.count('  •') for text in texts]

        return {
            'avg_bullets': self._avg(bullet_counts),
            'avg_categories': self._avg(category_counts),
            'avg_sub_bullets': self._avg(sub_bullet_counts),
        }

    def _generate_insights(self, patterns: Dict) -> List[str]:
        """Generate actionable insights from patterns."""
        insights = []

        high = patterns['high_performers']
        low = patterns['low_performers']

        # Length insights
        if high['avg_length'] > low['avg_length'] * 1.3:
            insights.append("✅ Longer, more detailed patch notes perform better")
        elif low['avg_length'] > high['avg_length'] * 1.3:
            insights.append("✅ Concise patch notes perform better")

        # Structure insights
        high_struct = high['structure_patterns']
        low_struct = low['structure_patterns']

        if high_struct['avg_sub_bullets'] > low_struct['avg_sub_bullets'] * 1.5:
            insights.append("✅ Using sub-bullets for details improves quality")

        if high_struct['avg_bullets'] > low_struct['avg_bullets'] * 1.3:
            insights.append("✅ More bullet points (more features) correlate with better scores")

        return insights

    def suggest_prompt_improvements(self, project: Optional[str] = None) -> List[Dict]:
        """
        Suggest specific prompt improvements based on analysis.

        Returns:
            List of suggested improvements with rationale
        """
        patterns = self.analyze_performance_patterns(project)

        if not patterns:
            return []

        suggestions = []

        insights = patterns.get('insights', [])

        # Convert insights to prompt modifications
        for insight in insights:
            if "longer, more detailed" in insight.lower():
                suggestions.append({
                    'type': 'emphasis',
                    'suggestion': 'Add instruction: "Provide comprehensive details for each feature (3-5 sentences or sub-bullets)"',
                    'rationale': insight
                })
            elif "concise" in insight.lower():
                suggestions.append({
                    'type': 'emphasis',
                    'suggestion': 'Add instruction: "Keep descriptions concise (1-2 sentences per feature)"',
                    'rationale': insight
                })
            elif "sub-bullets" in insight.lower():
                suggestions.append({
                    'type': 'structure',
                    'suggestion': 'Add instruction: "For complex features, use sub-bullets (  - ) to list specific details"',
                    'rationale': insight
                })

        # Check A/B test results
        best_variant = self.ab_testing.get_best_variant(project, min_samples=3)
        if best_variant:
            suggestions.append({
                'type': 'variant_recommendation',
                'suggestion': f'Consider using elements from top-performing variant: {best_variant.name}',
                'rationale': f'This variant has the highest combined score in A/B testing'
            })

        return suggestions

    def auto_tune_variant(self, variant_id: str, project: Optional[str] = None) -> Optional[str]:
        """
        Automatically tune a prompt variant based on feedback.

        Creates a new improved variant.

        Args:
            variant_id: ID of variant to tune
            project: Optional project filter for feedback

        Returns:
            ID of new tuned variant or None if no improvements found
        """
        suggestions = self.suggest_prompt_improvements(project)

        if not suggestions:
            logger.info("No prompt improvements suggested at this time")
            return None

        # Get original variant
        variant = self.ab_testing.variants.get(variant_id)
        if not variant:
            logger.error(f"Variant {variant_id} not found")
            return None

        # Apply suggestions to create new variant
        new_template = variant.template

        for suggestion in suggestions:
            if suggestion['type'] == 'emphasis':
                # Add emphasis instruction
                new_template += f"\n\n⚠️ {suggestion['suggestion']}"
            elif suggestion['type'] == 'structure':
                # Add structure instruction
                new_template += f"\n\n📐 {suggestion['suggestion']}"

        # Create new variant
        new_name = f"{variant.name} (Auto-Tuned)"
        new_desc = f"Auto-tuned version of {variant.name}. Applied {len(suggestions)} improvements."

        new_variant_id = self.ab_testing.add_variant(
            name=new_name,
            description=new_desc,
            template=new_template
        )

        # Log tuning
        tuning_log = {
            'timestamp': datetime.utcnow().isoformat(),
            'original_variant_id': variant_id,
            'new_variant_id': new_variant_id,
            'project': project,
            'suggestions_applied': suggestions,
        }

        with open(self.tuning_log_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(tuning_log) + '\n')

        logger.info(f"🎯 Auto-tuned variant {variant_id} → {new_variant_id}")
        return new_variant_id

    def schedule_auto_tuning(self, project: Optional[str] = None,
                            min_samples: int = 10, improvement_threshold: float = 5.0) -> None:
        """
        Check if auto-tuning should run and execute if conditions are met.

        Args:
            project: Optional project filter
            min_samples: Minimum samples needed before tuning
            improvement_threshold: Minimum improvement potential (score difference) to tune
        """
        stats = self.ab_testing.get_variant_statistics(project)

        # Check if we have enough data
        total_samples = sum(s['count'] for s in stats.values())

        if total_samples < min_samples:
            logger.debug(f"Not enough samples for auto-tuning ({total_samples}/{min_samples})")
            return

        # Find best and worst performing variants
        if not stats:
            return

        best_variant_id = max(stats.keys(), key=lambda vid: stats[vid].get('avg_total_score', 0))
        worst_variant_id = min(stats.keys(), key=lambda vid: stats[vid].get('avg_total_score', 0))

        best_score = stats[best_variant_id].get('avg_total_score', 0)
        worst_score = stats[worst_variant_id].get('avg_total_score', 0)

        score_gap = best_score - worst_score

        if score_gap < improvement_threshold:
            logger.debug(f"Score gap too small for tuning ({score_gap:.1f} < {improvement_threshold})")
            return

        # Auto-tune the worst performing variant
        logger.info(f"🎯 Auto-tuning triggered: score gap = {score_gap:.1f}")
        new_variant_id = self.auto_tune_variant(worst_variant_id, project)

        if new_variant_id:
            logger.info(f"✅ Created improved variant: {new_variant_id}")


def get_prompt_auto_tuner(data_dir: Path, ab_testing, trainer) -> PromptAutoTuner:
    """Get PromptAutoTuner instance."""
    return PromptAutoTuner(data_dir, ab_testing, trainer)
