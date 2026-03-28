"""
A/B Testing System for Prompt Optimization.

Tests multiple prompt variants and tracks which performs best.
"""

import json
import logging
import random
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timezone
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
                created_at=datetime.now(timezone.utc).isoformat(),
                active=True
            ),
            PromptVariant(
                id='concise_v1',
                name='Concise Overview',
                description='Focuses on concise, high-level overview with key points',
                template=self._get_concise_template('de'),  # Default German
                created_at=datetime.now(timezone.utc).isoformat(),
                active=True
            ),
            PromptVariant(
                id='benefit_focused_v1',
                name='Benefit-Focused',
                description='Emphasizes user benefits and impact rather than technical details',
                template=self._get_benefit_focused_template('de'),  # Default German
                created_at=datetime.now(timezone.utc).isoformat(),
                active=True
            ),
            PromptVariant(
                id='community_v1',
                name='Community-Friendly',
                description='TL;DR + Benefit-Focus + Stats — optimiert für Community und SEO',
                template=self._get_community_template('de'),  # Default German
                created_at=datetime.now(timezone.utc).isoformat(),
                active=True
            ),
            PromptVariant(
                id='gaming_community_v1',
                name='Gaming Community Hype',
                description='Spiel-Community Patchnotes — aufregend, verständlich, hyped Features statt Code',
                template=self._get_gaming_community_template('de'),  # Default German
                created_at=datetime.now(timezone.utc).isoformat(),
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

    def _get_community_template(self, language: str = 'de') -> str:
        """Get community-friendly template with TL;DR and stats.

        Args:
            language: 'de' for German, 'en' for English
        """
        if language == 'en':
            return """You are a community manager writing patch notes for {project}.
Your audience is NON-TECHNICAL end users who want to understand what improved.

# CHANGELOG INFORMATION
{changelog}

# COMMIT MESSAGES
{commits}

{stats_section}

INSTRUCTIONS:
1. START with a TL;DR (1-2 sentences summarizing the most important change)
2. List changes by BENEFIT TO USERS, not technical implementation
3. Use categories: 🆕 New Features, 🐛 Bug Fixes, ⚡ Improvements
4. For each change: What is it? Why does it matter? What does it mean for me?
5. Keep it friendly and conversational — no jargon
6. Maximum 3500 characters (Discord)

FORMAT:
> **TL;DR:** [One sentence summary of the biggest change]

**🆕 New Features:**
• **Feature Name**: What it does and why you'll love it
  - Concrete benefit for users

**🐛 Bug Fixes:**
• **Fix Name**: What was broken and how it's fixed now

**⚡ Improvements:**
• **Improvement**: How the experience got better

{stats_line}"""
        else:  # German
            return """Du bist ein Community Manager und schreibst Patch Notes für {project}.
Deine Zielgruppe sind NICHT-TECHNISCHE Endnutzer die verstehen wollen, was sich verbessert hat.

# CHANGELOG INFORMATIONEN
{changelog}

# COMMIT NACHRICHTEN
{commits}

{stats_section}

ANWEISUNGEN:
1. BEGINNE mit einem TL;DR (1-2 Sätze die die wichtigste Änderung zusammenfassen)
2. Liste Änderungen nach NUTZEN FÜR USER, nicht technischer Umsetzung
3. Verwende Kategorien: 🆕 Neue Features, 🐛 Bugfixes, ⚡ Verbesserungen
4. Pro Änderung: Was ist es? Warum ist es wichtig? Was bedeutet das für mich?
5. Halte es freundlich und verständlich — kein Fachjargon
6. Maximum 3500 Zeichen (Discord)

FORMAT:
> **TL;DR:** [Ein Satz der die größte Änderung zusammenfasst]

**🆕 Neue Features:**
• **Feature-Name**: Was es macht und warum du es lieben wirst
  - Konkreter Vorteil für Nutzer

**🐛 Bugfixes:**
• **Fix-Name**: Was kaputt war und wie es jetzt behoben ist

**⚡ Verbesserungen:**
• **Verbesserung**: Wie das Erlebnis besser wurde

{stats_line}"""

    def _get_gaming_community_template(self, language: str = 'de') -> str:
        """Get gaming community template — hypes features for game communities.

        Args:
            language: 'de' for German, 'en' for English
        """
        if language == 'en':
            return """You are the community manager for {project}, a realistic emergency dispatch simulator game.
Your audience is GAMERS and emergency services fans on Discord. They want to know what's new, exciting, and coming soon.

CRITICAL RULES:
- Write like a game studio announcing a patch — professional but exciting
- NEVER mention code, commits, git, TypeScript, React, Docker, CI/CD, refactoring, or infrastructure
- Translate ALL technical changes into GAMEPLAY IMPACT
- Use gaming language: "update", "patch", "quality of life", "performance boost", "new content"
- If a change is purely internal (code cleanup, docs, tooling), either skip it or frame it as "stability & performance improvements"
- Address the reader directly: "du" / "ihr"

# CHANGELOG INFORMATION
{changelog}

# COMMIT MESSAGES
{commits}

{stats_section}

INSTRUCTIONS:
1. Open with an exciting 1-2 sentence hook about the biggest change
2. Group changes into player-relevant categories (see format)
3. Each point explains WHAT CHANGED FOR THE PLAYER, not what devs coded
4. Describe EACH visible feature separately — do NOT merge them! A new loading overlay, a city search, a fly-in animation are THREE separate points
5. Per feature 2-3 sentences: What is it? How does it feel? Why is it cool?
6. End with a short teaser of what's coming next (if info available)
7. MINIMUM 2500 characters, maximum 3500 characters (Discord embed limit) — use the space!
8. Use emojis sparingly but effectively
9. Skip empty categories (e.g. no "Stability" section if there are only features)

EXAMPLES of translating dev → player language:
- "fix: Stale-Closures in GameMap" → "Map loads more reliably — no more disappearing stations"
- "feat: Landing Page redesigned" → 🎨 Design rework, NOT a "new feature"!
- "feat: 20 Einsatz-Templates" → "20 new emergency scenarios — from basement fires to pile-ups!"
- "chore: ESLint + TypeScript fixes" → skip entirely

IMPORTANT — Choose the right category:
- Only TRULY new functionality goes under "New Content" (e.g. city search, new missions)
- Visual overhauls (redesign, rework, new layouts) go under "Design & Look"
- Improved existing features go under "Gameplay Improvements"
- If the patch is mainly a design rework, "Design & Look" MUST be the FIRST category

FORMAT (choose fitting categories — not all are required):
> 🚨 **[Exciting one-line hook about the biggest change]**

🎨 **Design & Look** (for redesigns, visual overhauls, new UI)
→ What changed visually and how it feels

🆕 **New Content & Features** (only NEW functionality!)
→ Feature described from player perspective

🎮 **Gameplay Improvements** (existing features improved)
→ What got better for players

🛡️ **Stability & Performance** (only if relevant)
→ Grouped stability improvements

🔮 **In Development** (when feature branches exist)
→ "We're working on: [Feature-Name] — [1 sentence what it brings]"
→ Clearly mark as NOT LIVE — build hype, don't promise!
→ Use the FEATURE BRANCHES info from the context

{stats_line}"""
        else:  # German
            return """Du bist der Community-Manager für {project}, eine realistische Leitstellen-Simulation.
Deine Zielgruppe sind GAMER und BOS-Fans auf Discord. Sie wollen wissen, was neu, spannend und bald verfügbar ist.

KRITISCHE REGELN:
- Schreibe wie ein Spielestudio das einen Patch ankündigt — professionell aber aufregend
- Erwähne NIEMALS Code, Commits, Git, TypeScript, React, Docker, CI/CD, Refactoring oder Infrastruktur
- Übersetze ALLE technischen Änderungen in GAMEPLAY-AUSWIRKUNGEN
- Nutze Gaming-Sprache: "Update", "Patch", "Quality of Life", "Performance-Boost", "neuer Content"
- Wenn eine Änderung rein intern ist (Code-Cleanup, Doku, Tooling), überspringe sie oder formuliere als "Stabilitäts- und Performance-Verbesserungen"
- Sprich den Leser direkt an: "du" / "ihr"

# CHANGELOG INFORMATIONEN
{changelog}

# COMMIT NACHRICHTEN
{commits}

{stats_section}

ANWEISUNGEN:
1. Starte mit einem packenden 1-2-Satz-Hook über die größte Änderung
2. Gruppiere Änderungen in spielerrelevante Kategorien (siehe Format)
3. Jeder Punkt erklärt WAS SICH FÜR DEN SPIELER ÄNDERT, nicht was die Devs programmiert haben
4. JEDES sichtbare Feature einzeln beschreiben — NICHT zusammenfassen! Ein neues Loading-Overlay, eine Stadtsuche, eine Fly-In-Animation sind DREI separate Punkte
5. Pro Feature 2-3 Sätze: Was ist es? Wie fühlt es sich an? Warum ist es cool?
6. Ende mit einem kurzen Teaser was als Nächstes kommt (falls Info vorhanden)
7. MINDESTENS 2500 Zeichen, maximal 3500 Zeichen (Discord Embed Limit) — nutze den Platz!
8. Nutze Emojis sparsam aber wirkungsvoll
9. Leere Kategorien weglassen (z.B. keine "Stabilität"-Sektion wenn es nur Features gibt)

BEISPIELE für die Übersetzung von Dev → Spieler-Sprache:
- "fix: Stale-Closures in GameMap" → "Die Karte lädt jetzt zuverlässiger — keine fehlenden Wachen mehr beim Navigieren!"
- "feat: Rate-Limiting auf API" → überspringe (unsichtbar für Spieler) oder "Serverseitige Stabilität verbessert"
- "feat: 20 Einsatz-Templates" → "20 neue Einsatzszenarien — von Kellerbrand bis Massenkarambolage!"
- "feat: Landing Page komplett überarbeitet" → 🎨 Design-Rework, NICHT als "neues Feature" verkaufen!
- "feat: Dashboard Redesign" → "Das Dashboard wurde komplett überarbeitet — klarere Übersicht, bessere Lesbarkeit"
- "chore: ESLint + TypeScript fixes" → komplett überspringen

WICHTIG — Richtige Kategorie wählen:
- Nur WIRKLICH neue Funktionalität gehört unter "Neuer Content" (z.B. Stadtsuche, neue Einsätze)
- Visuelle Überarbeitungen (Redesign, Rework, neue Layouts) gehören unter "Design & Look"
- Verbesserte bestehende Features gehören unter "Gameplay-Verbesserungen"
- Wenn der Patch hauptsächlich ein Design-Rework ist, MUSS "Design & Look" die ERSTE Kategorie sein

FORMAT (wähle die passenden Kategorien — nicht alle sind Pflicht):
> 🚨 **[Packender Ein-Satz-Hook über die größte Änderung]**

🎨 **Design & Look** (für Redesigns, visuelle Überarbeitungen, neues UI)
→ Was sich visuell geändert hat und wie es sich anfühlt

🆕 **Neuer Content & Features** (nur NEUE Funktionalität!)
→ Feature aus Spieler-Perspektive beschrieben

🎮 **Gameplay-Verbesserungen** (bestehende Features verbessert)
→ Was für Spieler besser geworden ist

🛡️ **Stabilität & Performance** (nur wenn relevant)
→ Zusammengefasste Stabilitätsverbesserungen

🔮 **In Entwicklung** (wenn Feature-Branches vorhanden)
→ "Wir arbeiten gerade an: [Feature-Name] — [1 Satz was es bringt]"
→ Klar als NICHT LIVE kennzeichnen — Vorfreude wecken, nicht versprechen!
→ Nutze die FEATURE BRANCHES Infos aus dem Kontext

{stats_line}"""

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
        elif variant_id == 'community_v1':
            return self._get_community_template(language)
        elif variant_id == 'gaming_community_v1':
            return self._get_gaming_community_template(language)
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
            timestamp=datetime.now(timezone.utc).isoformat()
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
                    except Exception:
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
            created_at=datetime.now(timezone.utc).isoformat(),
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
