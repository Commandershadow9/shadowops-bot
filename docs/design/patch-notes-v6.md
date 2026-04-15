# Patch Notes Pipeline v6 — Design Document

**Datum:** 2026-04-13
**Status:** DESIGN
**Autor:** Shadow + Claude
**Vorgänger:** Patch Notes v5 (Unified Pipeline), 5839 Zeilen / 9 Dateien / 92 Methoden

## Motivation

Die aktuelle Pipeline (v5) hat in 6 Wochen 30+ Änderungen erfahren. Jeder Fix hat Seiteneffekte, jedes neue Feature erhöht die Kopplung. Konkrete Vorfälle:

- **Commit-Cap Bug:** Bei 150+ Commits sieht die AI nur die neuesten 50 — ältere Features (BOS-Funk, CASL-Auth) fallen komplett raus
- **Versions-Mismatch:** DB speichert `0.20.0`, Titel enthält AI-erfundenes `v0.21.0`
- **5 konkurrierende Version-Quellen** (SemVer, Git-Tag, Explicit, AI, Fallback)
- **618 if/elif/else Verzweigungen**, 47 defensive Kommentare ("NIEMALS", "NICHT")
- **Doppelte Logik:** Version wird 2x aufgelöst, Titel 2x gestripped, Prompt aus 6+ Teilen geklebt

## Ziele

1. **Zuverlässigkeit:** Keine verlorenen Features, keine Versions-Mismatches, crash-resilient
2. **Alle Projekte:** MayDay (Gaming), GuildScout (SaaS), ZERODOX (SaaS), ShadowOps (DevOps), AI-Agent-Framework (Library)
3. **Alle Features bleiben:** A/B-Testing, Learning, Team-Credits, Feature-Teasers, Big-Update-Modus, Safety-Layer, Feedback-Buttons
4. **Wartbar:** ~2500-3000 Zeilen statt 5839, klare Verantwortlichkeiten, testbar
5. **Hands-off:** Einmal konfiguriert, keine manuelle Pflege nötig

## Nicht-Ziele

- Keine neue AI-Engine (Codex/Claude Dual-Engine bleibt)
- Keine Änderung an Discord Bot Framework (discord.py)
- Keine Änderung am Batcher (patch_notes_batcher.py bleibt eigenständig)
- Keine Änderung an Health-Server/REST-API (Port 8766)

---

## Architektur

### Package-Struktur

```
src/patch_notes/
├── __init__.py              # Public API: generate_release()
├── pipeline.py              # PatchNotePipeline — State Machine Orchestrator
├── context.py               # PipelineContext Dataclass
├── stages/
│   ├── __init__.py
│   ├── collect.py           # Stufe 1: Commits + PR-Daten + Git-Stats
│   ├── classify.py          # Stufe 2: Gruppierung + Version + Credits
│   ├── generate.py          # Stufe 3: Prompt + AI-Call + Parsing
│   ├── validate.py          # Stufe 4: Safety + Halluzinations-Guard
│   └── distribute.py        # Stufe 5: Discord + Web-DB + Learning
├── templates/
│   ├── base.py              # Shared Template-Logik + Prompt-Builder
│   ├── gaming.py            # Gaming-Template (MayDay)
│   ├── saas.py              # SaaS-Template (GuildScout, ZERODOX)
│   └── devops.py            # DevOps-Template (ShadowOps, AI-Agent-Framework)
├── versioning.py            # DB-basierte SemVer (EINE Quelle)
├── grouping.py              # Deterministische Commit-Gruppierung
├── sanitizer.py             # Content-Sanitizer (portiert)
├── learning.py              # A/B-Testing + Feedback + Examples (konsolidiert)
└── state.py                 # Persistenter Pipeline-State (JSON)
```

**15 Dateien**, jede mit einer klaren Verantwortung.

### Datenfluss

```
Trigger (Webhook/Cron/Manual/Polling)
    │
    ▼
PatchNotePipeline.run(ctx)
    │
    ├── COLLECTING ─────────────────────────────────────────────┐
    │   collect.py:                                             │
    │   - Commits aus Batcher/Webhook laden                     │
    │   - PR-Daten anreichern (gh pr view --json labels,body)   │
    │   - Git-Stats sammeln (Dateien, Zeilen, Autoren)          │
    │   - Body-Noise entfernen (Co-Authored-By, Signed-off-by)  │
    │   Output: ctx.enriched_commits, ctx.git_stats             │
    │                                                           │
    ├── CLASSIFYING ────────────────────────────────────────────┤
    │   classify.py:                                            │
    │   - Jeden Commit taggen (FEATURE, BUGFIX, DOCS, etc.)    │
    │   - PR-Labels überschreiben Commit-Prefix (zuverlässiger) │
    │   - Commits nach Scope/Thema gruppieren (ALLE, kein Cap!) │
    │   - Version berechnen (DB-basiert, EINMAL)                │
    │   - Team-Credits extrahieren                              │
    │   - Update-Größe bestimmen (SMALL/NORMAL/BIG/MAJOR)      │
    │   Output: ctx.groups, ctx.version, ctx.team_credits       │
    │                                                           │
    ├── GENERATING ─────────────────────────────────────────────┤
    │   generate.py:                                            │
    │   - Template laden (gaming/saas/devops je nach Config)    │
    │   - A/B-Variante wählen (oder Gewinner wenn stabil)      │
    │   - Prompt bauen aus: Template + Gruppen + Kontext        │
    │   - Big-Update-Override wenn ctx.update_size >= BIG       │
    │   - Feature-Branch-Teasers anhängen                       │
    │   - AI-Call (Codex → Claude Fallback, mit Retry)          │
    │   - Structured Output parsen                              │
    │   Output: ctx.ai_result, ctx.prompt, ctx.ai_engine_used   │
    │                                                           │
    ├── VALIDATING ─────────────────────────────────────────────┤
    │   validate.py:                                            │
    │   - Feature-Count-Check (AI ≤ echte feat:-Commits × 2)   │
    │   - Design-Doc-Leak-Check                                 │
    │   - Version aus AI-Titel entfernen                        │
    │   - Content-Sanitizer (Pfade, IPs, Secrets)               │
    │   - Umlaut-Normalisierung (ae→ä, oe→ö)                   │
    │   - Titel + TL;DR + Web-Content extrahieren               │
    │   Output: ctx.title, ctx.tldr, ctx.web_content, ctx.valid │
    │                                                           │
    └── DISTRIBUTING ───────────────────────────────────────────┘
        distribute.py:
        - Discord Embed bauen (aus ctx, nicht nochmal resolve!)
        - Discord senden (Customer + Internal + External)
        - Changelog-DB Upsert (Version + Titel aus ctx)
        - Web-Export (JSON + Markdown Backup)
        - API POST (falls konfiguriert)
        - Message-IDs tracken (für Rollback)
        - Learning: Generation in DB speichern
        - Feedback-Buttons anhängen
        Output: ctx.sent_message_ids
```

### State Machine

```python
class PipelineState(Enum):
    PENDING      = 0   # Noch nicht gestartet
    COLLECTING   = 1   # Stufe 1 läuft
    CLASSIFYING  = 2   # Stufe 2 läuft
    GENERATING   = 3   # Stufe 3 läuft (AI-Call — teuerste Stufe)
    VALIDATING   = 4   # Stufe 4 läuft
    DISTRIBUTING = 5   # Stufe 5 läuft
    COMPLETED    = 6   # Erfolgreich
    FAILED       = 7   # Fehler — ctx.error hat Details
```

**Crash-Resilience:**
- Nach jedem State-Wechsel: `ctx` wird nach `data/pipeline_runs/{project}_{version}.json` serialisiert
- Bei Bot-Restart: Pipeline prüft ob unfertige Runs existieren und setzt fort
- GENERATING ist die kritischste Stufe (AI-Call, 10-30s). Bei Crash hier: AI-Call wird wiederholt (idempotent)
- DISTRIBUTING bei Crash: Message-IDs prüfen → nur fehlende Channels nachsenden
- Cleanup: Abgeschlossene Runs werden nach 24h gelöscht

**Retry-Logik:**
- Stufe 1-2 (deterministisch): Kein Retry nötig, Daten sind gleich
- Stufe 3 (AI): Max 2 Versuche pro Engine, dann Fallback
- Stufe 5 (Discord): Pro Channel einzeln, Fehler in einem Channel blockiert nicht die anderen

---

## PipelineContext — Zentrale Datenstruktur

```python
@dataclass
class PipelineContext:
    """Alle Daten einer Patch-Notes-Generierung."""
    
    # ── Input ──
    project: str                                    # "mayday_sim"
    project_config: dict                            # Aus config.yaml
    raw_commits: list[dict]                         # Rohe Commits
    trigger: str                                    # "webhook" | "cron" | "manual" | "polling"
    
    # ── Stufe 1: COLLECT ──
    enriched_commits: list[dict] = field(default_factory=list)
    git_stats: dict = field(default_factory=dict)   # files_changed, lines_added, lines_removed
    
    # ── Stufe 2: CLASSIFY ──
    groups: list[dict] = field(default_factory=list)  # Thematische Gruppen
    version: str = ""                                 # EINMAL berechnet
    version_source: str = ""                          # "semver" | "fallback"
    team_credits: list[dict] = field(default_factory=list)
    update_size: str = "normal"                       # "small" | "normal" | "big" | "major"
    previous_version_content: str = ""                # Für Duplikat-Guard
    
    # ── Stufe 3: GENERATE ──
    prompt: str = ""                                  # Archiviert für Debugging
    ai_result: dict | str | None = None
    ai_engine_used: str = ""
    variant_id: str = ""                              # A/B-Testing
    generation_time_s: float = 0.0
    
    # ── Stufe 4: VALIDATE ──
    title: str = ""                                   # Bereinigt, OHNE Version
    tldr: str = ""
    web_content: str = ""                             # Für Changelog-Seite
    changes: list[dict] = field(default_factory=list) # Strukturierte Änderungen
    seo_keywords: list[str] = field(default_factory=list)
    fixes_applied: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    
    # ── Stufe 5: DISTRIBUTE ──
    sent_message_ids: list[tuple] = field(default_factory=list)  # (channel_id, message_id)
    
    # ── State Machine ──
    state: int = 0                                    # PipelineState Wert
    error: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    
    # ── Metriken ──
    metrics: dict = field(default_factory=dict)       # Pipeline-Metriken für Logging
```

**Serialisierung:** Alle Felder sind JSON-serialisierbar (keine discord.Embed, keine datetime-Objekte im State). Discord-Embed wird in Stufe 5 on-the-fly gebaut.

---

## Commit-Gruppierung (grouping.py)

### Algorithmus

```python
def group_commits(commits: list[dict]) -> list[dict]:
    """
    Gruppiere Commits nach Thema. ALLE Commits, kein Cap.
    
    Gruppierungs-Hierarchie:
    1. PR-Label (zuverlässigste Quelle, z.B. "feature", "security")
    2. Conventional Commit Scope (z.B. feat(auth) → Scope "auth")
    3. Dateipfad-Heuristik (z.B. src/events/ → "Event-System")
    4. Fallback: Nach Commit-Typ (alle feat: ohne Scope zusammen)
    """
```

### Scope-Mapping (deterministisch, konfigurierbar)

```python
# Default-Mapping, überschreibbar pro Projekt in config.yaml
SCOPE_TO_THEME = {
    # Gameplay / Frontend
    'auth': 'Berechtigungen & Rollen',
    'play': 'Gameplay',
    'ui': 'Benutzeroberfläche',
    'hooks': 'Frontend-Logik',
    
    # Backend / Infrastruktur
    'events': 'Event-System',
    'cqrs': 'Daten-Architektur',
    'resilience': 'Stabilität & Ausfallsicherheit',
    'observability': 'Monitoring & Metriken',
    'docker': 'Infrastruktur',
    'ci': 'Build & Deploy',
    'db': 'Datenbank',
    
    # Content
    'content': 'Inhalte',
    'generator': 'Content-Generierung',
    'voice': 'Sprachausgabe',
}

# Dateipfad-Heuristik für Commits ohne Scope
PATH_TO_THEME = {
    'src/events/': 'Event-System',
    'src/components/': 'Benutzeroberfläche',
    'prisma/': 'Datenbank',
    'docker': 'Infrastruktur',
    'tests/': 'Tests',
}
```

### Gruppen-Output

```python
@dataclass
class CommitGroup:
    theme: str              # "Berechtigungen & Rollen"
    tag: str                # "FEATURE" | "BUGFIX" | "INFRASTRUCTURE" | "DOCS"
    scope: str              # "auth" (original scope)
    commits: list[dict]     # Alle Commits in dieser Gruppe
    summary: str            # Kompakte Zusammenfassung (1-2 Sätze)
    is_player_facing: bool  # True wenn Gameplay/UI/Content
    pr_labels: list[str]    # GitHub PR-Labels der Gruppe
```

**Wichtig:** `is_player_facing` bestimmt, ob die AI diese Gruppe als Feature hervorheben oder unter "Stabilität" zusammenfassen soll. Das löst das Problem dass CASL-Auth (spielerrelevant!) unter Infrastruktur verschwand.

### GitHub Labels für bessere Klassifizierung

Empfohlene Labels pro Projekt (können via `scripts/setup_github_labels.py` deployed werden):

| Label | Farbe | Beschreibung | Mapping |
|-------|-------|-------------|---------|
| `feature` | `#0E8A16` | Neues Feature | FEATURE + player_facing |
| `bugfix` | `#D93F0B` | Bug behoben | BUGFIX |
| `security` | `#B60205` | Sicherheits-Fix | BUGFIX + security_note |
| `performance` | `#FBCA04` | Performance-Verbesserung | IMPROVEMENT |
| `infrastructure` | `#C5DEF5` | Backend/DevOps | INFRASTRUCTURE |
| `content` | `#BFD4F2` | Content/Assets | FEATURE + player_facing |
| `design-doc` | `#D4C5F9` | Nur Planung, keine Implementierung | DOCS (nicht als Feature!) |
| `breaking` | `#B60205` | Breaking Change | BREAKING |
| `dependencies` | `#0075CA` | Dependency-Updates | DEPS |
| `seo` | `#E4E669` | SEO-Verbesserung | IMPROVEMENT |
| `gameplay` | `#0E8A16` | Gameplay-relevant | FEATURE + player_facing |
| `ui` | `#1D76DB` | UI/UX Änderung | FEATURE + player_facing |

Die Auto-Label GitHub Action (`.github/workflows/auto-label-pr.yml`) bleibt und wird erweitert.

---

## Versionierung (versioning.py)

### EINE Quelle: Changelog-DB + SemVer

```python
def calculate_version(project: str, groups: list[dict]) -> tuple[str, str]:
    """
    Berechne nächste Version. NUR aus Changelog-DB + Commit-Typen.
    
    Returns:
        (version, source) — source ist immer "semver" oder "fallback"
    """
    last_version = get_last_db_version(project)  # "0.20.0" oder None
    
    if not last_version:
        # Neues Projekt: Starte bei 0.1.0
        return ("0.1.0", "fallback")
    
    # SemVer-Bump basierend auf Gruppen-Tags
    has_breaking = any(g['tag'] == 'BREAKING' for g in groups)
    has_feature = any(g['tag'] == 'FEATURE' for g in groups)
    
    major, minor, patch = parse_semver(last_version)
    
    if has_breaking:
        new = f"{major + 1}.0.0"
    elif has_feature:
        new = f"{major}.{minor + 1}.0"
    else:
        new = f"{major}.{minor}.{patch + 1}"
    
    # Kollisionsschutz
    return (ensure_unique(new, project), "semver")
```

**Was wegfällt:**
- `_get_last_version_from_git()` — Git-Tags beeinflussen die Version nicht mehr
- `_get_version_from_commit_tags()` — Tags auf Commits werden ignoriert
- `_extract_version_from_commits()` — Keine Version aus Commit-Messages
- AI-Version — AI darf keine Version generieren (wird aus Prompt entfernt)
- Fallback-Chain mit 5 Quellen → 1 Quelle

**Ergebnis:** Eine Version, einmal berechnet, in `ctx.version` gespeichert, überall verwendet.

---

## Template-System (templates/)

### Typ-Definition in Config

```yaml
# config.yaml
projects:
  mayday_sim:
    patch_notes:
      type: gaming
      language: de
      changelog_url: https://maydaysim.de/changelog
      target_audience: "Gamer, BOS-Enthusiasten und Blaulicht-Fans"
      project_description: "Multiplayer-Leitstellensimulator"
      discord_teaser: true     # Hype-Teaser im Discord
      
  guildscout:
    patch_notes:
      type: saas
      language: de
      changelog_url: https://guildscout.gg/changelog
      target_audience: "WoW-Gildenleiter und Recruiter"
      project_description: "WoW Guild Management Platform"
      
  zerodox:
    patch_notes:
      type: saas
      language: de
      changelog_url: https://zerodox.de/changelog
      target_audience: "Handwerker, Elektriker und Industrieunternehmen"
      project_description: "Digitale Dokumentenverwaltung für Handwerksbetriebe"
      
  shadowops-bot:
    patch_notes:
      type: devops
      language: de
      target_audience: "Entwickler und Admins"
      project_description: "Security Monitoring & Patch Notes Bot"
      
  ai-agent-framework:
    patch_notes:
      type: devops
      language: de
      target_audience: "Entwickler"
      project_description: "Python AI Agent Framework"
```

### Template-Klassen

```python
# templates/base.py
class BaseTemplate:
    """Basis für alle Projekt-Typ-Templates."""
    
    def build_prompt(self, ctx: PipelineContext) -> str:
        """Baue den AI-Prompt aus Kontext."""
        sections = [
            self._system_instruction(ctx),
            self._groups_section(ctx),
            self._context_section(ctx),
            self._previous_version_guard(ctx),
            self._update_size_override(ctx),
            self._rules_section(ctx),
        ]
        return "\n\n".join(s for s in sections if s)
    
    # Überschreibbar pro Typ:
    def categories(self) -> list[str]: ...
    def tone_instruction(self) -> str: ...
    def badges(self) -> list[str]: ...
    def length_limits(self, update_size: str) -> dict: ...
    def discord_format(self, update_size: str) -> str: ...


# templates/gaming.py
class GamingTemplate(BaseTemplate):
    """MayDay Sim, Community-Spiele — emotionaler Ton, Szenarien."""
    
    def categories(self):
        return ["Neuer Content", "Gameplay-Verbesserungen", "Design & Look",
                "Stabilität & Performance", "So funktioniert's", "Demnächst"]
    
    def tone_instruction(self):
        return (
            "Schreibe aus der Perspektive eines begeisterten Spielers. "
            "Beschreibe Features mit konkreten Mini-Szenarien: "
            "'Stell dir vor, drei Einsätze laufen parallel...'. "
            "Nutze Hype-Sprache für große Features."
        )
    
    def badges(self):
        return ["feature", "content", "gameplay", "design", "performance",
                "multiplayer", "fix", "breaking", "infrastructure",
                "improvement", "docs", "security"]
    
    def length_limits(self, update_size):
        return {
            "small":  {"min": 1500, "max": 2500, "features": "2-3"},
            "normal": {"min": 2500, "max": 4000, "features": "3-5"},
            "big":    {"min": 3500, "max": 5500, "features": "4-7"},
            "major":  {"min": 4500, "max": 7000, "features": "5-8"},
        }[update_size]


# templates/saas.py
class SaaSTemplate(BaseTemplate):
    """GuildScout, ZERODOX — sachlich, feature-fokussiert."""
    
    def categories(self):
        return ["Neue Features", "Verbesserungen", "Bugfixes",
                "Sicherheit", "Performance"]
    
    def tone_instruction(self):
        return (
            "Sachlicher, professioneller Ton. "
            "Beschreibe den konkreten Nutzen für den User: "
            "'Rechnungen können jetzt direkt per Drag & Drop hochgeladen werden'. "
            "Vermeide Hype-Sprache."
        )
    
    def length_limits(self, update_size):
        return {
            "small":  {"min": 800,  "max": 1500, "features": "1-3"},
            "normal": {"min": 1500, "max": 3000, "features": "2-5"},
            "big":    {"min": 2500, "max": 4000, "features": "3-6"},
            "major":  {"min": 3500, "max": 5500, "features": "4-8"},
        }[update_size]
```

### Gruppen → Prompt (der entscheidende Schritt)

```python
def _groups_section(self, ctx: PipelineContext) -> str:
    """Konvertiere CommitGroups zu lesbarem Prompt-Input."""
    lines = [f"# Änderungen in {ctx.project} (v{ctx.version})"]
    lines.append(f"Update-Größe: {ctx.update_size.upper()} ({len(ctx.enriched_commits)} Commits)")
    lines.append("")
    
    # Player-Facing zuerst, dann Infrastruktur
    player_groups = [g for g in ctx.groups if g['is_player_facing']]
    infra_groups = [g for g in ctx.groups if not g['is_player_facing']]
    
    if player_groups:
        lines.append("## Spieler-/Nutzer-relevante Änderungen")
        for g in player_groups:
            lines.append(f"### [{g['tag']}] {g['theme']} ({len(g['commits'])} Commits)")
            lines.append(f"  Zusammenfassung: {g['summary']}")
            if g['pr_labels']:
                lines.append(f"  Labels: {', '.join(g['pr_labels'])}")
            # Maximal 5 Commit-Highlights pro Gruppe
            for c in g['commits'][:5]:
                lines.append(f"  - {c['message'].split(chr(10))[0]}")
            if len(g['commits']) > 5:
                lines.append(f"  - ... und {len(g['commits']) - 5} weitere")
            lines.append("")
    
    if infra_groups:
        lines.append("## Infrastruktur / Backend (für Stabilitäts-Sektion)")
        for g in infra_groups:
            lines.append(f"### [{g['tag']}] {g['theme']} ({len(g['commits'])} Commits)")
            lines.append(f"  Zusammenfassung: {g['summary']}")
            lines.append("")
    
    return "\n".join(lines)
```

**Das löst den Commit-Cap-Bug:** ALLE Gruppen kommen in den Prompt, aber kompakt. 150 Commits werden zu ~15-20 Gruppen mit Summaries. Die AI sieht alles, muss aber nicht 150 Messages parsen.

---

## Learning & Feedback (learning.py)

### Konsolidierte Architektur

Aktuell sind A/B-Testing, Learning, und Feedback auf 3 Dateien verteilt (prompt_ab_testing.py, patch_notes_learning.py, patch_notes_feedback.py). Neu: Alles in `learning.py`.

```python
class PatchNotesLearning:
    """A/B-Testing + Feedback + Example-Ranking — konsolidiert."""
    
    # ── A/B Testing ──
    async def select_variant(self, project: str) -> str:
        """Wähle Prompt-Variante. Gewinner-Variante wenn Konfidenz hoch genug."""
    
    async def record_generation(self, ctx: PipelineContext) -> None:
        """Speichere Generation in agent_learning.pn_generations."""
    
    # ── Feedback (Discord-Buttons) ──
    async def record_feedback(self, project: str, version: str,
                               feedback_type: str, score: float,
                               text: str = "") -> None:
        """Speichere User-Feedback (Like, Rating 1-5, Text)."""
    
    async def get_feedback_stats(self, project: str) -> dict:
        """Feedback-Statistiken für Dashboard."""
    
    # ── Examples ──
    async def get_best_examples(self, project: str, limit: int = 2) -> list:
        """Feedback-gewichtete Beispiele für Few-Shot-Prompt."""
    
    # ── Varianten-Performance ──
    async def update_variant_scores(self) -> None:
        """Nightly: Feedback → Varianten-Scores aktualisieren."""
```

### Feedback-Flow

```
User klickt Discord-Button (👍 Like / ⭐ Bewerten / 💬 Text)
    │
    ▼
PatchNotesFeedbackView (discord.py Persistent View)
    │
    ▼
learning.record_feedback(project, version, type, score, text)
    │
    ▼
agent_learning.agent_feedback (PostgreSQL)
    │
    ▼
Nightly: update_variant_scores() → pn_variants aktualisieren
    │
    ▼
Nächste Generierung: select_variant() bevorzugt bessere Varianten
```

### Discord Feedback-Buttons (bleiben)

```python
class PatchNotesFeedbackView(discord.ui.View):
    """Persistent Buttons unter jeder Patch Note."""
    
    @discord.ui.button(label="👍", style=discord.ButtonStyle.grey)
    async def like(self, interaction, button): ...
    
    @discord.ui.button(label="⭐ Bewerten", style=discord.ButtonStyle.grey)
    async def rate(self, interaction, button): ...
    
    @discord.ui.button(label="💬 Feedback", style=discord.ButtonStyle.grey)
    async def text_feedback(self, interaction, button): ...
```

---

## Safety-Validierung (validate.py)

### 5 Checks — klar getrennt, keine versteckten Abhängigkeiten

```python
async def validate(ctx: PipelineContext) -> None:
    """Stufe 4: Alle Safety-Checks auf ctx.ai_result."""
    
    # Check 1: Feature-Count (AI ≤ echte feat-Gruppen × 2)
    check_feature_count(ctx)
    
    # Check 2: Design-Doc-Leak (mit Smart False-Positive-Schutz)
    check_design_doc_leaks(ctx)
    
    # Check 3: Version aus AI-Output entfernen
    strip_ai_version(ctx)  # Setzt ctx.title OHNE Version
    
    # Check 4: Content-Sanitizer (Pfade, IPs, Secrets)
    sanitize_content(ctx)
    
    # Check 5: Umlaut-Normalisierung (ae→ä)
    normalize_umlauts(ctx)
    
    # Titel + TL;DR + Web-Content extrahieren
    extract_display_content(ctx)
```

**Verbesserung vs. heute:**
- Jeder Check ist eine eigenständige Funktion (testbar!)
- Alle Checks operieren auf `ctx` (kein Seiteneffekt auf externe State)
- Feature-Count prüft gegen `ctx.groups` (alle Commits), nicht gegen die gefiltern 50
- Version-Strip nutzt generisches Regex (nicht versionsspezifisch)

---

## Distribution (distribute.py)

### Einmal bauen, überall senden

```python
async def distribute(ctx: PipelineContext, bot) -> None:
    """Stufe 5: Discord + Web-DB + Learning."""
    
    # 1. Discord Embed bauen (aus ctx — NICHT nochmal Version auflösen!)
    embed = build_embed(ctx)
    
    # 2. Internal Channel
    await send_internal(bot, embed, ctx.project)
    
    # 3. Customer Channels (mit Feedback-Buttons)
    msg_ids = await send_customer(bot, embed, ctx)
    ctx.sent_message_ids.extend(msg_ids)
    
    # 4. External Notifications
    await send_external(bot, embed, ctx)
    
    # 5. Web-DB + File-Backup
    await store_changelog(ctx)
    
    # 6. Learning: Generation speichern
    await learning.record_generation(ctx)
    
    # 7. Metriken loggen
    log_metrics(ctx)
```

### Rollback (bleibt)

```python
async def retract_patch_notes(bot, project: str, version: str) -> int:
    """Lösche alle Discord-Messages einer Patch Note."""
    # Liest sent_message_ids aus State, löscht via get_partial_message().delete()
```

---

## Migration

### Phase 1: Neues Package bauen (neben altem Code)

- `src/patch_notes/` erstellen
- Alte Mixins bleiben funktionsfähig
- Feature-Flag in config.yaml: `patch_notes.engine: v6` (default: `v5`)

### Phase 2: Projekt für Projekt umschalten

1. **shadowops-bot** (eigenes Projekt, niedrigstes Risiko)
2. **ai-agent-framework** (wenig Traffic)
3. **guildscout** (SaaS-Template testen)
4. **zerodox** (SaaS-Template, Kunden-relevant)
5. **mayday_sim** (Gaming-Template, höchste Komplexität)

### Phase 3: Alte Mixins entfernen

- Nach 2 Wochen fehlerfreiem Betrieb auf allen Projekten
- `ai_patch_notes_mixin.py` und `notifications_mixin.py` Patch-Notes-Teile entfernen
- Notifications-Mixin behält nur non-patch-notes Logik (PR-Notifications etc.)

---

## Metriken & Observability

### Pipeline-Metriken (pro Run)

```json
{
    "project": "mayday_sim",
    "version": "0.21.0",
    "trigger": "cron",
    "update_size": "major",
    "total_commits": 152,
    "groups": 18,
    "player_facing_groups": 7,
    "infra_groups": 11,
    "ai_engine": "codex",
    "variant_id": "gaming_community_v2",
    "generation_time_s": 12.4,
    "pipeline_total_time_s": 18.7,
    "hallucinations_caught": 0,
    "version_source": "semver",
    "state_transitions": ["COLLECTING", "CLASSIFYING", "GENERATING", "VALIDATING", "DISTRIBUTING", "COMPLETED"]
}
```

Format: `METRICS|patch_notes_pipeline|{json}` — kompatibel mit bestehendem Monitoring.

---

## Zusammenfassung: v5 → v6

| Aspekt | v5 (aktuell) | v6 (neu) |
|--------|-------------|----------|
| Code-Umfang | 5839 Zeilen, 92 Methoden | ~2500-3000 Zeilen, ~40 Methoden |
| Dateien | 9 (2 Mega-Mixins) | 15 (fokussierte Module) |
| Commit-Handling | Cap bei 50, Features gehen verloren | Gruppierung ALLER Commits |
| Version | 5 Quellen (DB, Git-Tag, Explicit, AI, Fallback) | 1 Quelle (DB + SemVer) |
| Prompt | 4 Pfade × Big/Normal × DE/EN | 1 Template-System mit Typ-Config |
| Crash-Resilience | Kein State, Crash = von vorne | State Machine, Restart ab letztem Schritt |
| Testbarkeit | Kaum (alles gekoppelt) | Jede Stufe einzeln testbar |
| Migration | — | Feature-Flag, Projekt für Projekt |
