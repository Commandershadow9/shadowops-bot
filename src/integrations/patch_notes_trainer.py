"""
AI Training System for Patch Notes Generation.

Improves AI patch notes quality through:
1. Enhanced input (CHANGELOG.md instead of just commits)
2. Few-shot learning (examples of good patch notes)
3. Feedback collection (Discord reactions, manual ratings)
4. Prompt optimization (automatic prompt tuning)
5. Quality scoring (automatic evaluation)
"""

import logging
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime
import re

logger = logging.getLogger('shadowops')


class PatchNotesTrainer:
    """
    Manages AI training for better patch notes generation.
    """

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.training_data_file = self.data_dir / 'patch_notes_training.jsonl'
        self.feedback_file = self.data_dir / 'patch_notes_feedback.jsonl'
        self.examples_file = self.data_dir / 'good_examples.json'

        # Load existing examples
        self.good_examples = self._load_examples()

    def _load_examples(self) -> List[Dict]:
        """Load curated examples of good patch notes."""
        if not self.examples_file.exists():
            return []

        try:
            with open(self.examples_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load examples: {e}")
            return []

    def save_example(self, version: str, changelog_content: str,
                     generated_notes: str, quality_score: float,
                     project: str) -> None:
        """
        Save a good example for future training.

        Args:
            version: Version number
            changelog_content: Full CHANGELOG section
            generated_notes: AI-generated patch notes
            quality_score: Quality score (0-100)
            project: Project name
        """
        example = {
            'version': version,
            'project': project,
            'changelog': changelog_content,
            'generated_notes': generated_notes,
            'quality_score': quality_score,
            'timestamp': datetime.utcnow().isoformat(),
        }

        # Append to training data
        with open(self.training_data_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(example) + '\n')

        # If high quality, add to good examples
        if quality_score >= 80:
            self.good_examples.append(example)
            self._save_examples()
            logger.info(f"✅ Added high-quality example: {project} v{version} (score: {quality_score})")

    def _save_examples(self) -> None:
        """Save good examples to file."""
        try:
            # Keep only top 10 examples
            sorted_examples = sorted(
                self.good_examples,
                key=lambda x: x['quality_score'],
                reverse=True
            )[:10]

            with open(self.examples_file, 'w', encoding='utf-8') as f:
                json.dump(sorted_examples, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save examples: {e}")

    def record_feedback(self, version: str, project: str,
                        feedback_type: str, feedback_data: Dict) -> None:
        """
        Record user feedback on patch notes.

        Args:
            version: Version number
            project: Project name
            feedback_type: Type of feedback ('reaction', 'manual_rating', 'edit')
            feedback_data: Feedback details
        """
        feedback = {
            'version': version,
            'project': project,
            'type': feedback_type,
            'data': feedback_data,
            'timestamp': datetime.utcnow().isoformat()
        }

        with open(self.feedback_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(feedback) + '\n')

        logger.info(f"📊 Feedback recorded: {project} v{version} - {feedback_type}")

    def build_enhanced_prompt(self, changelog_content: str, commits: List[Dict],
                              language: str, project: str) -> str:
        """
        Build enhanced AI prompt with examples and full context.

        Args:
            changelog_content: Full CHANGELOG.md section for this version
            commits: Commit messages
            language: Language for patch notes
            project: Project name

        Returns:
            Enhanced prompt for AI
        """
        # Get relevant examples (same project if available)
        project_examples = [e for e in self.good_examples if e['project'] == project]
        if not project_examples and self.good_examples:
            project_examples = self.good_examples[:2]  # Use best examples from any project

        # Build prompt
        if language == 'de':
            prompt = self._build_german_prompt(
                changelog_content, commits, project, project_examples
            )
        else:
            prompt = self._build_english_prompt(
                changelog_content, commits, project, project_examples
            )

        return prompt

    def _build_german_prompt(self, changelog: str, commits: List[Dict],
                             project: str, examples: List[Dict]) -> str:
        """Build German prompt with examples."""
        prompt = f"""Du bist ein Experte für Patch Notes und Release Notes.

Deine Aufgabe: Erstelle professionelle, detaillierte Patch Notes für {project}.

# WICHTIG: Qualitätsstandards

1. **Vollständigkeit**: ALLE wichtigen Änderungen müssen erwähnt werden
2. **Details beibehalten**: Nicht zusammenfassen, sondern alle Features/Fixes auflisten
3. **Struktur**: Klare Kategorien mit Bullet Points
4. **Emojis sparsam**: Nur zur Kategorisierung (🆕, 🐛, ⚡, 📚)
5. **Technische Details**: Bei neuen Features Details zu Funktionsweise
6. **Sub-Points**: Bei komplexen Features Unterpunkte verwenden

"""

        # Add examples if available
        if examples:
            prompt += "# BEISPIELE FÜR GUTE PATCH NOTES\n\n"
            for i, example in enumerate(examples[:2], 1):
                prompt += f"## Beispiel {i} ({example['project']} v{example['version']}):\n\n"
                prompt += f"```\n{example['generated_notes'][:500]}...\n```\n\n"

        # Add current context
        prompt += f"# VOLLSTÄNDIGE CHANGELOG-INFORMATION\n\n{changelog}\n\n"

        # Add commits for reference
        if commits:
            prompt += "# ZUSÄTZLICHE COMMIT-INFORMATIONEN\n\n"
            for commit in commits[:10]:
                prompt += f"- {commit.get('message', '')}\n"
            prompt += "\n"

        # Instructions
        prompt += """# ANWEISUNGEN

Erstelle jetzt professionelle Patch Notes basierend auf dem CHANGELOG und den Commits.

Anforderungen:
- Nutze die CHANGELOG-Information als Hauptquelle (sie ist am vollständigsten)
- Erstelle klare Kategorien: 🆕 Neue Features, 🐛 Bugfixes, ⚡ Verbesserungen, 📚 Dokumentation
- Verwende Bullet Points und Sub-Points für Struktur
- Behalte alle wichtigen Details (Zahlen, Namen, Features)
- Maximal 3900 Zeichen (Discord Limit)

Format:
**🆕 Neue Features:**

• **Feature Name**: Beschreibung
  - Detail 1
  - Detail 2

**🐛 Bugfixes:**

• **Fix Name**: Was wurde behoben
  - Konkretes Problem
  - Lösung

[etc.]
"""

        return prompt

    def _build_english_prompt(self, changelog: str, commits: List[Dict],
                              project: str, examples: List[Dict]) -> str:
        """Build English prompt with examples."""
        prompt = f"""You are an expert at creating professional patch notes and release notes.

Your task: Create professional, detailed patch notes for {project}.

# IMPORTANT: Quality Standards

1. **Completeness**: ALL important changes must be mentioned
2. **Preserve details**: Don't summarize, list all features/fixes
3. **Structure**: Clear categories with bullet points
4. **Emojis sparingly**: Only for categorization (🆕, 🐛, ⚡, 📚)
5. **Technical details**: For new features, include implementation details
6. **Sub-points**: Use sub-bullets for complex features

"""

        # Add examples if available
        if examples:
            prompt += "# EXAMPLES OF GOOD PATCH NOTES\n\n"
            for i, example in enumerate(examples[:2], 1):
                prompt += f"## Example {i} ({example['project']} v{example['version']}):\n\n"
                prompt += f"```\n{example['generated_notes'][:500]}...\n```\n\n"

        # Add current context
        prompt += f"# COMPLETE CHANGELOG INFORMATION\n\n{changelog}\n\n"

        # Add commits for reference
        if commits:
            prompt += "# ADDITIONAL COMMIT INFORMATION\n\n"
            for commit in commits[:10]:
                prompt += f"- {commit.get('message', '')}\n"
            prompt += "\n"

        # Instructions
        prompt += """# INSTRUCTIONS

Now create professional patch notes based on the CHANGELOG and commits.

Requirements:
- Use CHANGELOG information as primary source (it's most complete)
- Create clear categories: 🆕 New Features, 🐛 Bug Fixes, ⚡ Improvements, 📚 Documentation
- Use bullet points and sub-bullets for structure
- Keep all important details (numbers, names, features)
- Maximum 3900 characters (Discord limit)

Format:
**🆕 New Features:**

• **Feature Name**: Description
  - Detail 1
  - Detail 2

**🐛 Bug Fixes:**

• **Fix Name**: What was fixed
  - Specific problem
  - Solution

[etc.]
"""

        return prompt

    def calculate_quality_score(self, generated_notes: str,
                                 changelog_content: str) -> float:
        """
        Automatically score patch notes quality.

        Metrics:
        - Length appropriateness (not too short, not too long)
        - Structure (has categories, bullet points)
        - Detail preservation (mentions key terms from CHANGELOG)
        - Formatting (proper markdown)

        Returns:
            Score from 0-100
        """
        score = 0.0

        # 1. Length check (20 points)
        length = len(generated_notes)
        if 500 <= length <= 4000:
            score += 20
        elif 300 <= length < 500:
            score += 10
        elif length > 4000:
            score += 5

        # 2. Structure check (30 points)
        # Has category headers
        category_patterns = [
            r'\*\*.*?(?:New Features|Neue Features|Features).*?\*\*',
            r'\*\*.*?(?:Bug Fixes?|Bugfixes?).*?\*\*',
            r'\*\*.*?(?:Improvements?|Verbesserungen).*?\*\*',
        ]
        categories_found = sum(1 for pattern in category_patterns
                              if re.search(pattern, generated_notes, re.IGNORECASE))
        score += min(categories_found * 10, 20)

        # Has bullet points
        bullet_count = generated_notes.count('•') + generated_notes.count('- ')
        if bullet_count >= 5:
            score += 10
        elif bullet_count >= 3:
            score += 5

        # 3. Detail preservation (30 points)
        # Extract key terms from CHANGELOG (capitalized words, numbers, etc.)
        changelog_keywords = set(re.findall(r'\b[A-Z][a-z]+\b', changelog_content))
        changelog_keywords.update(re.findall(r'\b\d+\b', changelog_content))

        # Check how many are preserved in generated notes
        preserved_count = sum(1 for keyword in changelog_keywords
                             if keyword in generated_notes)
        preservation_rate = preserved_count / max(len(changelog_keywords), 1)
        score += preservation_rate * 30

        # 4. Formatting check (20 points)
        # Has emojis for visual structure
        if any(emoji in generated_notes for emoji in ['🆕', '🐛', '⚡', '📚', '🔧']):
            score += 10

        # Has sub-bullets (proper hierarchy)
        if '  -' in generated_notes or '  •' in generated_notes:
            score += 5

        # Has bold text for emphasis
        if '**' in generated_notes:
            score += 5

        return min(score, 100)

    def get_statistics(self) -> Dict:
        """Get training statistics."""
        stats = {
            'total_examples': 0,
            'good_examples': len(self.good_examples),
            'total_feedback': 0,
            'avg_quality_score': 0.0,
            'projects': set(),
        }

        # Count training data
        if self.training_data_file.exists():
            with open(self.training_data_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                stats['total_examples'] = len(lines)

                scores = []
                for line in lines:
                    try:
                        data = json.loads(line)
                        scores.append(data.get('quality_score', 0))
                        stats['projects'].add(data.get('project', 'unknown'))
                    except:
                        continue

                if scores:
                    stats['avg_quality_score'] = sum(scores) / len(scores)

        # Count feedback
        if self.feedback_file.exists():
            with open(self.feedback_file, 'r', encoding='utf-8') as f:
                stats['total_feedback'] = len(f.readlines())

        stats['projects'] = list(stats['projects'])

        return stats


def get_patch_notes_trainer(data_dir: Path = None) -> PatchNotesTrainer:
    """Get PatchNotesTrainer instance."""
    if data_dir is None:
        data_dir = Path.home() / '.shadowops' / 'patch_notes_training'

    return PatchNotesTrainer(data_dir)
