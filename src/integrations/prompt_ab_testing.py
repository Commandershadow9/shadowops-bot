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
        else:
            # Sync: Neue Default-Varianten nachtragen die in der Datei fehlen
            self._sync_default_variants()

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
            PromptVariant(
                id='gaming_community_v2',
                name='Gaming Community Story-Telling',
                description='Spiel-Community v2 — Story-Telling mit konkretem Spielgefühl, → Pfeil-Format, ausführliche Feature-Beschreibungen',
                template=self._get_gaming_community_v2_template('de'),
                created_at=datetime.now(timezone.utc).isoformat(),
                active=True
            ),
        ]

        for variant in variants:
            self.variants[variant.id] = variant

        self._save_variants()
        logger.info(f"Created {len(variants)} default prompt variants")

    def _sync_default_variants(self) -> None:
        """Sync neue Default-Varianten in bestehende Datei nach.

        Wird aufgerufen wenn die Varianten-Datei schon existiert,
        aber neue Varianten im Code hinzugekommen sind.
        """
        # Temporär alle Defaults erzeugen ohne zu speichern
        defaults = {}
        for variant_data in [
            ('detailed_v1', 'Detailed Grouping', 'Emphasizes grouping related commits into detailed feature descriptions', self._get_detailed_template('de')),
            ('concise_v1', 'Concise Overview', 'Focuses on concise, high-level overview with key points', self._get_concise_template('de')),
            ('benefit_focused_v1', 'Benefit-Focused', 'Emphasizes user benefits and impact rather than technical details', self._get_benefit_focused_template('de')),
            ('community_v1', 'Community-Friendly', 'TL;DR + Benefit-Focus + Stats — optimiert für Community und SEO', self._get_community_template('de')),
            ('gaming_community_v1', 'Gaming Community Hype', 'Spiel-Community Patchnotes — aufregend, verständlich, hyped Features statt Code', self._get_gaming_community_template('de')),
            ('gaming_community_v2', 'Gaming Community Story-Telling', 'Spiel-Community v2 — Story-Telling mit konkretem Spielgefühl, → Pfeil-Format, ausführliche Feature-Beschreibungen', self._get_gaming_community_v2_template('de')),
        ]:
            defaults[variant_data[0]] = variant_data

        added = []
        for variant_id, (vid, name, desc, template) in defaults.items():
            if variant_id not in self.variants:
                self.variants[variant_id] = PromptVariant(
                    id=vid,
                    name=name,
                    description=desc,
                    template=template,
                    created_at=datetime.now(timezone.utc).isoformat(),
                    active=True
                )
                added.append(variant_id)

        if added:
            self._save_variants()
            logger.info(f"🔄 {len(added)} neue Default-Variante(n) nachgetragen: {', '.join(added)}")

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

📖 **How to use** (ONLY if FEATURE GUIDES exist in context!)
→ Copy the Release Guide text VERBATIM, do NOT invent instructions!
→ If no Release Guide exists → SKIP this section entirely

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

📖 **So funktioniert's** (NUR wenn FEATURE-ANLEITUNGEN im Kontext vorhanden!)
→ Den Text aus dem Release-Guide WÖRTLICH übernehmen, NICHT erfinden!
→ Wenn kein Release-Guide vorhanden → diese Sektion KOMPLETT weglassen

🔮 **In Entwicklung** (wenn Feature-Branches vorhanden)
→ "Wir arbeiten gerade an: [Feature-Name] — [1 Satz was es bringt]"
→ Klar als NICHT LIVE kennzeichnen — Vorfreude wecken, nicht versprechen!
→ Nutze die FEATURE BRANCHES Infos aus dem Kontext

{stats_line}"""

    def _get_gaming_community_v2_template(self, language: str = 'de') -> str:
        """Get gaming community v2 template — story-telling with concrete gameplay feel.

        Improvements over v1:
        - Enforces story-telling: every feature must describe HOW IT FEELS
        - Concrete numbers required (e.g. "30 scenarios", "26 upgrades")
        - → arrow format instead of bullet points
        - Highlight features get 3-5 sentences with examples
        - Good/bad examples included in template
        - Minimum 2500, maximum 3800 characters

        Args:
            language: 'de' for German, 'en' for English
        """
        if language == 'en':
            return self._get_gaming_community_v2_template_en()
        else:
            return self._get_gaming_community_v2_template_de()

    def _get_gaming_community_v2_template_de(self) -> str:
        """German gaming community v2 template — Story-Telling with gameplay feel."""
        return """Du bist ein leidenschaftlicher Game-Developer der sein eigenes Update vorstellt.
Du LIEBST dein Spiel {project} und willst, dass die Community deine Begeisterung spürt.
Deine Zielgruppe sind GAMER und BOS-Fans auf Discord — sie wollen FÜHLEN was sich geändert hat, nicht nur lesen.

═══════════════════════════════════════
KERNREGEL: JEDES Feature muss beschreiben WIE ES SICH ANFÜHLT
═══════════════════════════════════════

ABSOLUT VERBOTEN:
- Code, Commits, Git, TypeScript, React, Docker, CI/CD, Refactoring, Infrastruktur
- Generische Phrasen: "verschiedene Verbesserungen", "einige Optimierungen", "diverse Anpassungen"
- Design-Docs oder Planungsdokumente als implementierte Features ausgeben
- Features erfinden die nicht aus den Commits hervorgehen
- Vage Beschreibungen ohne konkretes Spielgefühl

PFLICHT:
- KONKRETE ZAHLEN wo immer möglich: "30 Einsatzszenarien" statt "viele Szenarien", "26 Upgrades" statt "zahlreiche Upgrades"
- → Pfeil-Format für Features (KEIN Bullet-Point-Format)
- Spieler direkt ansprechen: "du" / "ihr"
- Gaming-Sprache: "Update", "Patch", "QoL", "Performance-Boost", "neuer Content"
- Rein interne Änderungen (Code-Cleanup, Doku, Tooling) → "Unter der Haube" oder überspringen

# CHANGELOG INFORMATIONEN
{changelog}

# COMMIT NACHRICHTEN
{commits}

{stats_section}

═══════════════════════════════════════
GUTE vs SCHLECHTE Beispiele — LERNE DEN UNTERSCHIED:
═══════════════════════════════════════

SCHLECHT (v1-Stil, zu generisch):
→ "Neue Stadtsuche hinzugefügt — finde Städte schneller"
→ "Karriere-System implementiert"
→ "Die Karte wurde verbessert"
→ "Verschiedene Performance-Optimierungen durchgeführt"

GUT (v2-Stil, konkretes Spielgefühl):
→ **Stadtsuche mit Autocomplete** — Tipp "Mün" ein und München, Münster, Münsingen tauchen sofort auf. Kein langes Scrollen mehr durch endlose Stadtlisten — du bist in Sekunden in deiner Wunschstadt und kannst loslegen.
→ **Karriere-System mit 26 Aufstiegsstufen** — Starte als Disponent und arbeite dich hoch zum Leitstellenchef. Jede Stufe schaltet neue Einsatztypen, Fahrzeuge und Herausforderungen frei. Nach 3 Beförderungen darfst du erstmals Großeinsätze koordinieren — und ab Stufe 15 wartet die Einsatzleitung auf dich.
→ **Kartenperformance um 40% verbessert** — Wachen und Einsatzorte laden jetzt flüssig, auch wenn du schnell über die Karte scrollst. Kein Ruckeln mehr bei voll besetzen Großstädten.

═══════════════════════════════════════
SCHREIB-ANWEISUNGEN:
═══════════════════════════════════════

1. HOOK (1-2 Sätze): Starte mit dem aufregendsten Feature. Mach den Leser neugierig — als würdest du deinen besten Kumpel erzählen was du gebaut hast.

2. HIGHLIGHT-FEATURES (die 1-3 größten Änderungen):
   → 3-5 Sätze pro Feature mit KONKRETEM BEISPIEL
   → Beschreibe ein Mini-Szenario: "Stell dir vor, du..." oder "Wenn du jetzt..."
   → Nenne konkrete Zahlen, Namen, Spielsituationen

3. NORMALE FEATURES (weitere Änderungen):
   → 2-3 Sätze pro Feature
   → Immer mit Spielgefühl: WAS ändert sich im Spielalltag?

4. REIN INTERNE ÄNDERUNGEN:
   → In 1-2 Sätzen unter "Stabilität" zusammenfassen ODER überspringen
   → Rahme als Spielerlebnis: "Weniger Ladezeiten", "Stabilerer Server", "Flüssigeres Spielgefühl"

5. ABSCHLUSS: Kurzer Teaser was als Nächstes kommt (falls Feature-Branches vorhanden)

ZEICHENLIMIT: MINDESTENS 2500, MAXIMAL 3800 Zeichen — nutze den Platz für Story-Telling!

═══════════════════════════════════════
KATEGORIEN (→ Pfeil-Format, passende auswählen):
═══════════════════════════════════════

> 🚨 **[Packender Hook — 1-2 Sätze als wäre es das geilste Update ever]**

🆕 **Neuer Content**
→ **[Feature-Name]** — [3-5 Sätze: Was ist es? Wie fühlt es sich an? Konkretes Beispiel. Warum ist es ein Gamechanger?]
→ **[Feature-Name]** — [2-3 Sätze mit Spielgefühl]

🎨 **Design & Look**
→ **[Was sich visuell geändert hat]** — [Wie fühlt es sich an? Vorher vs Nachher]

🎮 **Gameplay-Verbesserungen**
→ **[Verbesserung]** — [Was hat sich im Spielalltag geändert? Konkretes Szenario]

🛡️ **Stabilität & Performance**
→ Zusammengefasst in 1-3 Sätzen mit Fokus auf Spielerlebnis

📖 **So funktioniert's** (NUR wenn FEATURE-ANLEITUNGEN im Kontext vorhanden!)
→ Den Text aus dem Release-Guide WÖRTLICH übernehmen, NICHT erfinden!
→ Wenn kein Release-Guide vorhanden → diese Sektion KOMPLETT weglassen

🔮 **In Entwicklung**
→ "Wir arbeiten gerade an: **[Feature-Name]** — [1-2 Sätze was es bringt und warum ihr euch freuen könnt]"
→ Klar als NICHT LIVE kennzeichnen — Vorfreude wecken, nicht versprechen!

═══════════════════════════════════════
WICHTIG — Richtige Kategorie:
═══════════════════════════════════════
- Nur WIRKLICH neue Funktionalität → "Neuer Content"
- Visuelle Überarbeitungen (Redesign, Rework) → "Design & Look"
- Bestehende Features verbessert → "Gameplay-Verbesserungen"
- Hauptsächlich Design-Rework? → "Design & Look" als ERSTE Kategorie
- Leere Kategorien → WEGLASSEN

═══════════════════════════════════════
CHANGE-TYPES FÜR DAS CHANGES-ARRAY:
═══════════════════════════════════════

Jeder Change im `changes` Array MUSS einen dieser Types haben:
- "feature" → Komplett neue Mechanik/System
- "content" → Neue Szenarien, Fahrzeuge, Karten, Wachen
- "gameplay" → Balancing, Scoring, Schwierigkeit
- "design" → UI, Animationen, Sounds, Visuals
- "performance" → Ladezeiten, Sync, Optimierung
- "multiplayer" → Lobby, Co-op, Sync-spezifisch
- "fix" → Bugfix
- "breaking" → Entfernungen, Breaking Changes
- "infrastructure" → Server, Stabilität, Backend
- "improvement" → Allgemeine Verbesserung (Fallback)
- "docs" → Nur Dokumentation

Wähle den SPEZIFISCHSTEN Type. "content" statt "feature" wenn es neue Szenarien/Fahrzeuge sind.
"gameplay" statt "improvement" wenn es Balancing betrifft.

═══════════════════════════════════════
TEAM-CREDITS (OPTIONAL — nur wenn credits_section vorhanden):
═══════════════════════════════════════

{credits_section}

Wenn TEAM-CREDITS oben vorhanden sind, füge am Ende der Patch Notes eine Credits-Zeile ein:

FORMAT (als letzte Zeile, NACH allen Features):
👥 **Dieses Update:** [Name] ([Bereich]) · [Name] ([Bereich])

REGELN:
- Nur Team-Mitglieder mit echten Commits nennen (NICHT erfinden!)
- Rollen aus den Credits übernehmen
- Wenn KI-Agents Commits haben: "🤖 Automatisiert: [was sie gemacht haben]" als separate Zeile
- KI-Agents sind ein FEATURE — zeige dass das Projekt aktive Automatisierung hat
- Die Credits-Zeile soll kurz und knapp sein (1 Zeile für Team, optional 1 für AI-Agents)
- Wenn nur 1 Person → trotzdem zeigen, z.B. "👥 **Dieses Update:** Shadow (Backend)"

BEISPIELE:
- "👥 **Dieses Update:** Shadow (Backend & Infrastruktur) · Mapu (Frontend & Design)"
- "👥 **Dieses Update:** Shadow (Backend) · Mapu (UI)"
  "🤖 **Automatisiert:** SEO-Optimierungen, Dependency-Updates"

═══════════════════════════════════════
DISCORD-TEASER (PFLICHT):
═══════════════════════════════════════

Generiere ein zusätzliches Feld "discord_teaser" (max 1000 Zeichen):

FORMAT:
🚨 {project} — v[Version]

[2-3 Hype-Sätze die erzählen was sich verändert hat — als würdest du einem Kumpel davon erzählen]

[Emoji] [Highlight 1 — 1 packender Satz]
[Emoji] [Highlight 2 — 1 packender Satz]
[Emoji] [Highlight 3 — 1 packender Satz]
[Emoji] [Highlight 4 — 1 packender Satz]
[Emoji] [Highlight 5 — 1 packender Satz]
[Emoji] [Highlight 6 — 1 packender Satz]

[Cliffhanger-Satz der neugierig macht, z.B. "Aber das war noch nicht alles..."]

EMOJIS pro Type:
🔵 feature, 🗺️ content, 🎮 gameplay, 🎨 design, ⚡ performance, 👥 multiplayer, 🔴 fix, ⚠️ breaking, 🛡️ infrastructure

{stats_line}"""

    def _get_gaming_community_v2_template_en(self) -> str:
        """English gaming community v2 template — Story-Telling with gameplay feel."""
        return """You are a passionate game developer presenting your own update.
You LOVE your game {project} and want the community to feel your excitement.
Your audience is GAMERS and emergency service fans on Discord — they want to FEEL what changed, not just read about it.

═══════════════════════════════════════
CORE RULE: EVERY feature must describe HOW IT FEELS
═══════════════════════════════════════

ABSOLUTELY FORBIDDEN:
- Code, commits, git, TypeScript, React, Docker, CI/CD, refactoring, infrastructure
- Generic phrases: "various improvements", "some optimizations", "diverse adjustments"
- Presenting design docs or planning documents as implemented features
- Inventing features that don't come from the commits
- Vague descriptions without concrete gameplay feel

MANDATORY:
- CONCRETE NUMBERS wherever possible: "30 mission scenarios" not "many scenarios", "26 upgrades" not "numerous upgrades"
- → Arrow format for features (NO bullet-point format)
- Address the reader directly: "you"
- Gaming language: "update", "patch", "QoL", "performance boost", "new content"
- Purely internal changes (code cleanup, docs, tooling) → "Under the hood" or skip

# CHANGELOG INFORMATION
{changelog}

# COMMIT MESSAGES
{commits}

{stats_section}

═══════════════════════════════════════
GOOD vs BAD examples — LEARN THE DIFFERENCE:
═══════════════════════════════════════

BAD (v1 style, too generic):
→ "Added new city search — find cities faster"
→ "Career system implemented"
→ "The map has been improved"
→ "Various performance optimizations performed"

GOOD (v2 style, concrete gameplay feel):
→ **City Search with Autocomplete** — Type "Mun" and Munich, Munster, Munsingen appear instantly. No more scrolling through endless city lists — you're in your dream city within seconds and ready to go.
→ **Career System with 26 Promotion Levels** — Start as a dispatcher and work your way up to chief of the control center. Each level unlocks new mission types, vehicles, and challenges. After 3 promotions you get to coordinate major incidents for the first time — and from level 15 the incident command awaits you.
→ **Map Performance Improved by 40%** — Stations and incident locations now load smoothly, even when you scroll across the map quickly. No more stuttering in fully staffed metropolitan areas.

═══════════════════════════════════════
WRITING INSTRUCTIONS:
═══════════════════════════════════════

1. HOOK (1-2 sentences): Start with the most exciting feature. Make the reader curious — as if you're telling your best friend what you built.

2. HIGHLIGHT FEATURES (the 1-3 biggest changes):
   → 3-5 sentences per feature with a CONCRETE EXAMPLE
   → Describe a mini scenario: "Imagine you..." or "When you now..."
   → Name concrete numbers, names, gameplay situations

3. NORMAL FEATURES (additional changes):
   → 2-3 sentences per feature
   → Always with gameplay feel: WHAT changes in daily gameplay?

4. PURELY INTERNAL CHANGES:
   → Summarize in 1-2 sentences under "Stability" OR skip
   → Frame as player experience: "Shorter load times", "More stable servers", "Smoother gameplay"

5. CLOSING: Short teaser of what's coming next (if feature branches exist)

CHARACTER LIMIT: MINIMUM 2500, MAXIMUM 3800 characters — use the space for story-telling!

═══════════════════════════════════════
CATEGORIES (→ arrow format, choose fitting ones):
═══════════════════════════════════════

> 🚨 **[Exciting hook — 1-2 sentences as if this is the greatest update ever]**

🆕 **New Content**
→ **[Feature Name]** — [3-5 sentences: What is it? How does it feel? Concrete example. Why is it a game changer?]
→ **[Feature Name]** — [2-3 sentences with gameplay feel]

🎨 **Design & Look**
→ **[What changed visually]** — [How does it feel? Before vs after]

🎮 **Gameplay Improvements**
→ **[Improvement]** — [What changed in daily gameplay? Concrete scenario]

🛡️ **Stability & Performance**
→ Summarized in 1-3 sentences focusing on player experience

📖 **How to use** (ONLY if FEATURE GUIDES exist in context!)
→ Copy the Release Guide text VERBATIM, do NOT invent instructions!
→ If no Release Guide exists → SKIP this section entirely

🔮 **In Development**
→ "We're working on: **[Feature Name]** — [1-2 sentences about what it brings and why you should be excited]"
→ Clearly mark as NOT LIVE — build hype, don't promise!

═══════════════════════════════════════
IMPORTANT — Right category:
═══════════════════════════════════════
- Only TRULY new functionality → "New Content"
- Visual overhauls (redesign, rework) → "Design & Look"
- Existing features improved → "Gameplay Improvements"
- Mainly design rework? → "Design & Look" as FIRST category
- Empty categories → OMIT

═══════════════════════════════════════
CHANGE TYPES FOR THE CHANGES ARRAY:
═══════════════════════════════════════

Every change in the `changes` array MUST have one of these types:
- "feature" → Completely new mechanic/system
- "content" → New scenarios, vehicles, maps, stations
- "gameplay" → Balancing, scoring, difficulty
- "design" → UI, animations, sounds, visuals
- "performance" → Load times, sync, optimization
- "multiplayer" → Lobby, co-op, sync-specific
- "fix" → Bugfix
- "breaking" → Removals, breaking changes
- "infrastructure" → Server, stability, backend
- "improvement" → General improvement (fallback)
- "docs" → Documentation only

Choose the MOST SPECIFIC type. "content" instead of "feature" for new scenarios/vehicles.
"gameplay" instead of "improvement" for balancing changes.

═══════════════════════════════════════
TEAM CREDITS (OPTIONAL — only if credits_section is present):
═══════════════════════════════════════

{credits_section}

If TEAM CREDITS are present above, add a credits line at the end of the patch notes:

FORMAT (as last line, AFTER all features):
👥 **This Update:** [Name] ([Area]) · [Name] ([Area])

RULES:
- Only name team members with real commits (do NOT invent!)
- Use roles from the credits
- If AI agents have commits: "🤖 Automated: [what they did]" as a separate line
- AI agents are a FEATURE — show that the project has active automation
- The credits line should be short and concise (1 line for team, optionally 1 for AI agents)
- If only 1 person → still show, e.g. "👥 **This Update:** Shadow (Backend)"

═══════════════════════════════════════
DISCORD TEASER (MANDATORY):
═══════════════════════════════════════

Generate an additional field "discord_teaser" (max 1000 characters):

FORMAT:
🚨 {project} — v[Version]

[2-3 hype sentences telling what changed — as if you're telling a friend about it]

[Emoji] [Highlight 1 — 1 exciting sentence]
[Emoji] [Highlight 2 — 1 exciting sentence]
[Emoji] [Highlight 3 — 1 exciting sentence]
[Emoji] [Highlight 4 — 1 exciting sentence]
[Emoji] [Highlight 5 — 1 exciting sentence]
[Emoji] [Highlight 6 — 1 exciting sentence]

[Cliffhanger sentence that sparks curiosity, e.g. "But that's not all..."]

EMOJIS per type:
🔵 feature, 🗺️ content, 🎮 gameplay, 🎨 design, ⚡ performance, 👥 multiplayer, 🔴 fix, ⚠️ breaking, 🛡️ infrastructure

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
        elif variant_id == 'gaming_community_v2':
            return self._get_gaming_community_v2_template(language)
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
