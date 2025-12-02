"""
A/B Testing System for Prompt Optimization.

Tests multiple prompt variants and tracks which performs best.
"""

import json
import logging
import random
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime
from dataclasses import dataclass, asdict

logger = logging.getLogger('shadowops')


@dataclass
class PromptVariant:
    """A prompt variant for A/B testing."""
    id: str
    name: str
    description: str
    template: str  # Template with {changelog}, {commits}, {project} placeholders
    created_at: str
    active: bool = True


@dataclass
class PromptTestResult:
    """Result of using a prompt variant."""
    variant_id: str
    project: str
    version: str
    quality_score: float
    user_feedback_score: float  # From reactions
    timestamp: str


class PromptABTesting:
    """
    Manages A/B testing of different prompt variants.
    """

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.variants_file = self.data_dir / 'prompt_variants.json'
        self.results_file = self.data_dir / 'prompt_test_results.jsonl'

        # Load variants
        self.variants: Dict[str, PromptVariant] = self._load_variants()

        # Create default variants if none exist
        if not self.variants:
            self._create_default_variants()

        logger.info(f"✅ Prompt A/B Testing initialized with {len(self.variants)} variants")

    def _load_variants(self) -> Dict[str, PromptVariant]:
        """Load prompt variants from file."""
        if not self.variants_file.exists():
            return {}

        try:
            with open(self.variants_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return {v['id']: PromptVariant(**v) for v in data}
        except Exception as e:
            logger.error(f"Failed to load prompt variants: {e}")
            return {}

    def _save_variants(self) -> None:
        """Save prompt variants to file."""
        try:
            data = [asdict(v) for v in self.variants.values()]
            with open(self.variants_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save prompt variants: {e}")

    def _create_default_variants(self) -> None:
        """Create default prompt variants.

        Note: Templates are stored with default language (de).
        Use get_variant_template() to get language-specific version.
        """
        variants = [
            PromptVariant(
                id='detailed_v1',
                name='Detailed Grouping',
                description='Emphasizes grouping related commits into detailed feature descriptions',
                template=self._get_detailed_template('de'),  # Default German
                created_at=datetime.utcnow().isoformat(),
                active=True
            ),
            PromptVariant(
                id='concise_v1',
                name='Concise Overview',
                description='Focuses on concise, high-level overview with key points',
                template=self._get_concise_template('de'),  # Default German
                created_at=datetime.utcnow().isoformat(),
                active=True
            ),
            PromptVariant(
                id='benefit_focused_v1',
                name='Benefit-Focused',
                description='Emphasizes user benefits and impact rather than technical details',
                template=self._get_benefit_focused_template('de'),  # Default German
                created_at=datetime.utcnow().isoformat(),
                active=True
            ),
        ]

        for variant in variants:
            self.variants[variant.id] = variant

        self._save_variants()
        logger.info(f"Created {len(variants)} default prompt variants")

    def _get_detailed_template(self, language: str = 'de') -> str:
        """Get detailed grouping template.

        Args:
            language: 'de' for German, 'en' for English
        """
        if language == 'en':
            return """You are an expert technical writer creating patch notes for {project}.

IMPORTANT: Group related commits into comprehensive feature descriptions!

# CHANGELOG INFORMATION
{changelog}

# COMMIT MESSAGES
{commits}

INSTRUCTIONS:
1. GROUP related commits into single, detailed bullet points
2. For each major feature, write 3-5 sub-points explaining:
   - What it does
   - Why it matters
   - Technical details
3. Use categories: 🆕 New Features, 🐛 Bug Fixes, ⚡ Improvements
4. Maximum detail while staying under 3900 characters

FORMAT:
**🆕 New Features:**
• **Feature Name**: Comprehensive description
  - Key benefit 1
  - Key benefit 2
  - Technical detail"""
        else:  # German
            return """Du bist ein professioneller Technical Writer und erstellst Patch Notes für {project}.

WICHTIG: Gruppiere verwandte Commits in umfassende Feature-Beschreibungen!

# CHANGELOG INFORMATIONEN
{changelog}

# COMMIT NACHRICHTEN
{commits}

ANWEISUNGEN:
1. GRUPPIERE verwandte Commits in einzelne, detaillierte Stichpunkte
2. Für jedes große Feature, schreibe 3-5 Unterpunkte die erklären:
   - Was es tut
   - Warum es wichtig ist
   - Technische Details
3. Verwende Kategorien: 🆕 Neue Features, 🐛 Bugfixes, ⚡ Verbesserungen
4. Maximales Detail bei unter 3900 Zeichen

FORMAT:
**🆕 Neue Features:**
• **Feature Name**: Umfassende Beschreibung
  - Wichtiger Vorteil 1
  - Wichtiger Vorteil 2
  - Technisches Detail"""

    def _get_concise_template(self, language: str = 'de') -> str:
        """Get concise overview template.

        Args:
            language: 'de' for German, 'en' for English
        """
        if language == 'en':
            return """You are an expert technical writer creating patch notes for {project}.

IMPORTANT: Be concise but informative!

# CHANGELOG INFORMATION
{changelog}

# COMMIT MESSAGES
{commits}

INSTRUCTIONS:
1. ONE LINE per feature/fix (no sub-bullets unless critical)
2. Focus on WHAT changed, not WHY
3. Use categories: 🆕 New Features, 🐛 Bug Fixes, ⚡ Improvements
4. Maximum 2500 characters

FORMAT:
**🆕 New Features:**
• **Feature Name**: Brief description of what it does"""
        else:  # German
            return """Du bist ein professioneller Technical Writer und erstellst Patch Notes für {project}.

WICHTIG: Sei prägnant aber informativ!

# CHANGELOG INFORMATIONEN
{changelog}

# COMMIT NACHRICHTEN
{commits}

ANWEISUNGEN:
1. EINE ZEILE pro Feature/Fix (keine Unterpunkte außer kritisch)
2. Fokus auf WAS sich geändert hat, nicht WARUM
3. Verwende Kategorien: 🆕 Neue Features, 🐛 Bugfixes, ⚡ Verbesserungen
4. Maximum 2500 Zeichen

FORMAT:
**🆕 Neue Features:**
• **Feature Name**: Kurze Beschreibung was es tut"""

    def _get_benefit_focused_template(self, language: str = 'de') -> str:
        """Get benefit-focused template.

        Args:
            language: 'de' for German, 'en' for English
        """
        if language == 'en':
            return """You are an expert technical writer creating patch notes for {project}.

IMPORTANT: Focus on USER BENEFITS and IMPACT!

# CHANGELOG INFORMATION
{changelog}

# COMMIT MESSAGES
{commits}

INSTRUCTIONS:
1. For each change, explain HOW IT HELPS USERS
2. Lead with benefits, follow with technical details
3. Use categories: 🆕 New Features, 🐛 Bug Fixes, ⚡ Improvements
4. Maximum 3500 characters

FORMAT:
**🆕 New Features:**
• **Feature Name**: [Benefit statement]. This means [user impact]. Technical: [how it works]"""
        else:  # German
            return """Du bist ein professioneller Technical Writer und erstellst Patch Notes für {project}.

WICHTIG: Fokussiere auf NUTZERVORTEILE und AUSWIRKUNGEN!

# CHANGELOG INFORMATIONEN
{changelog}

# COMMIT NACHRICHTEN
{commits}

ANWEISUNGEN:
1. Erkläre für jede Änderung WIE ES NUTZERN HILFT
2. Beginne mit Vorteilen, gefolgt von technischen Details
3. Verwende Kategorien: 🆕 Neue Features, 🐛 Bugfixes, ⚡ Verbesserungen
4. Maximum 3500 Zeichen

FORMAT:
**🆕 Neue Features:**
• **Feature Name**: [Vorteil]. Das bedeutet [Nutzerauswirkung]. Technisch: [wie es funktioniert]"""

    def get_variant_template(self, variant_id: str, language: str = 'de') -> str:
        """Get the template for a specific variant in the requested language.

        Args:
            variant_id: ID of the variant (e.g., 'detailed_v1')
            language: 'de' for German, 'en' for English

        Returns:
            Template string in the requested language
        """
        if variant_id == 'detailed_v1':
            return self._get_detailed_template(language)
        elif variant_id == 'concise_v1':
            return self._get_concise_template(language)
        elif variant_id == 'benefit_focused_v1':
            return self._get_benefit_focused_template(language)
        else:
            # For custom variants, return stored template (may not have language support)
            variant = self.variants.get(variant_id)
            if variant:
                return variant.template
            else:
                raise ValueError(f"Unknown variant ID: {variant_id}")

    def select_variant(self, project: str, strategy: str = 'weighted_random') -> PromptVariant:
        """
        Select a prompt variant for testing.

        Args:
            project: Project name (for project-specific weighting)
            strategy: Selection strategy ('random', 'weighted_random', 'best_performing')

        Returns:
            Selected PromptVariant
        """
        active_variants = [v for v in self.variants.values() if v.active]

        if not active_variants:
            raise ValueError("No active prompt variants available")

        if strategy == 'random':
            return random.choice(active_variants)

        elif strategy == 'weighted_random':
            # Weight by performance
            stats = self.get_variant_statistics(project)
            weights = []

            for variant in active_variants:
                variant_stats = stats.get(variant.id, {})
                avg_score = variant_stats.get('avg_total_score', 50)  # Default 50
                # Weight = score / 100, minimum 0.1
                weight = max(avg_score / 100, 0.1)
                weights.append(weight)

            return random.choices(active_variants, weights=weights)[0]

        elif strategy == 'best_performing':
            stats = self.get_variant_statistics(project)
            best_variant = max(
                active_variants,
                key=lambda v: stats.get(v.id, {}).get('avg_total_score', 0)
            )
            return best_variant

        else:
            return random.choice(active_variants)

    def record_result(self, variant_id: str, project: str, version: str,
                      quality_score: float, user_feedback_score: float = 0.0) -> None:
        """
        Record the result of using a prompt variant.

        Args:
            variant_id: ID of the prompt variant used
            project: Project name
            version: Version number
            quality_score: Automatic quality score (0-100)
            user_feedback_score: User feedback score from reactions
        """
        result = PromptTestResult(
            variant_id=variant_id,
            project=project,
            version=version,
            quality_score=quality_score,
            user_feedback_score=user_feedback_score,
            timestamp=datetime.utcnow().isoformat()
        )

        # Append to results file
        with open(self.results_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(asdict(result)) + '\n')

        logger.info(f"📊 A/B Test Result: variant={variant_id}, quality={quality_score:.1f}, feedback={user_feedback_score:+.1f}")

    def get_variant_statistics(self, project: Optional[str] = None) -> Dict:
        """
        Get statistics for all prompt variants.

        Args:
            project: Optional project filter

        Returns:
            Dict of {variant_id: stats}
        """
        if not self.results_file.exists():
            return {}

        variant_stats = {}

        try:
            with open(self.results_file, 'r', encoding='utf-8') as f:
                for line in f:
                    try:
                        result = json.loads(line)

                        # Filter by project if specified
                        if project and result.get('project') != project:
                            continue

                        variant_id = result.get('variant_id')
                        if variant_id not in variant_stats:
                            variant_stats[variant_id] = {
                                'count': 0,
                                'total_quality': 0,
                                'total_feedback': 0,
                                'quality_scores': [],
                                'feedback_scores': [],
                            }

                        stats = variant_stats[variant_id]
                        stats['count'] += 1
                        stats['total_quality'] += result.get('quality_score', 0)
                        stats['total_feedback'] += result.get('user_feedback_score', 0)
                        stats['quality_scores'].append(result.get('quality_score', 0))
                        stats['feedback_scores'].append(result.get('user_feedback_score', 0))
                    except:
                        continue

            # Calculate averages and combined scores
            for variant_id, stats in variant_stats.items():
                if stats['count'] > 0:
                    stats['avg_quality_score'] = stats['total_quality'] / stats['count']
                    stats['avg_feedback_score'] = stats['total_feedback'] / stats['count']
                    # Combined score: 70% quality, 30% user feedback
                    stats['avg_total_score'] = (
                        stats['avg_quality_score'] * 0.7 +
                        stats['avg_feedback_score'] * 0.3
                    )

        except Exception as e:
            logger.error(f"Failed to calculate variant statistics: {e}")

        return variant_stats

    def get_best_variant(self, project: Optional[str] = None, min_samples: int = 3) -> Optional[PromptVariant]:
        """
        Get the best performing prompt variant.

        Args:
            project: Optional project filter
            min_samples: Minimum number of samples required

        Returns:
            Best performing PromptVariant or None
        """
        stats = self.get_variant_statistics(project)

        # Filter variants with enough samples
        eligible = {
            vid: vstats for vid, vstats in stats.items()
            if vstats['count'] >= min_samples
        }

        if not eligible:
            return None

        best_id = max(eligible.keys(), key=lambda vid: eligible[vid]['avg_total_score'])
        return self.variants.get(best_id)

    def add_variant(self, name: str, description: str, template: str) -> str:
        """
        Add a new prompt variant.

        Returns:
            variant_id of the created variant
        """
        import uuid
        variant_id = f"custom_{uuid.uuid4().hex[:8]}"

        variant = PromptVariant(
            id=variant_id,
            name=name,
            description=description,
            template=template,
            created_at=datetime.utcnow().isoformat(),
            active=True
        )

        self.variants[variant_id] = variant
        self._save_variants()

        logger.info(f"✅ Added new prompt variant: {variant_id} ({name})")
        return variant_id

    def deactivate_variant(self, variant_id: str) -> bool:
        """Deactivate a prompt variant."""
        if variant_id in self.variants:
            self.variants[variant_id].active = False
            self._save_variants()
            logger.info(f"Deactivated prompt variant: {variant_id}")
            return True
        return False


def get_prompt_ab_testing(data_dir: Path = None) -> PromptABTesting:
    """Get PromptABTesting instance."""
    if data_dir is None:
        data_dir = Path.home() / '.shadowops' / 'patch_notes_training'

    return PromptABTesting(data_dir)
