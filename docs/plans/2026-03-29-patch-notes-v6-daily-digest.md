# Patch Notes v6 — Täglicher Digest + Qualitätssprung

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Patch Notes für neue Projekte (MayDay Sim) auf Enterprise-Level bringen: korrekte Git-Tag-Versionierung, täglicher Release-Rhythmus, und Prompt-Qualität die manuelle Posts übertrifft.

**Architecture:** Drei Änderungsbereiche: (1) Batcher bekommt `daily` Release-Mode mit täglichem Cron-Task, (2) Version-Resolution nutzt Git-Tags aus dem Commit-Range statt nur den neuesten Tag, (3) Neues Prompt-Template `gaming_community_v2` mit Story-Telling und konkreten Spieler-Perspektiven.

**Tech Stack:** Python 3.12, discord.py 2.7, subprocess (git), sqlite3, AI Dual-Engine (Codex/Claude)

---

## Task 1: Batcher — Daily Release Mode

**Files:**
- Modify: `src/integrations/patch_notes_batcher.py:17-40` (Constructor + neue Methode)
- Test: `tests/unit/test_patch_notes_batcher.py`

**Step 1: Test schreiben — `get_daily_releasable_projects()`**

```python
# In tests/unit/test_patch_notes_batcher.py — neue Tests hinzufügen

def test_get_daily_releasable_projects_returns_projects_above_min(batcher):
    """Projekte mit ≥ daily_min_commits werden returned."""
    batcher.add_commits('mayday_sim', [
        {'id': f'abc{i}', 'message': f'feat: feature {i}', 'author': {'name': 'dev'}}
        for i in range(5)
    ])
    batcher.add_commits('other_project', [
        {'id': 'xyz1', 'message': 'fix: small fix', 'author': {'name': 'dev'}}
    ])
    result = batcher.get_daily_releasable_projects(daily_min_commits=3)
    assert 'mayday_sim' in result
    assert 'other_project' not in result


def test_get_daily_releasable_projects_respects_custom_min(batcher):
    """Custom daily_min_commits wird beachtet."""
    batcher.add_commits('mayday_sim', [
        {'id': f'abc{i}', 'message': f'feat: feature {i}', 'author': {'name': 'dev'}}
        for i in range(2)
    ])
    result = batcher.get_daily_releasable_projects(daily_min_commits=2)
    assert 'mayday_sim' in result
    result = batcher.get_daily_releasable_projects(daily_min_commits=3)
    assert 'mayday_sim' not in result
```

**Step 2: Test ausführen — muss feilen**

```bash
pytest tests/unit/test_patch_notes_batcher.py::test_get_daily_releasable_projects_returns_projects_above_min -xvs
```
Expected: FAIL — `get_daily_releasable_projects()` akzeptiert kein `daily_min_commits` Argument.

**Step 3: Implementierung — `get_daily_releasable_projects()`**

In `src/integrations/patch_notes_batcher.py` neue Methode hinzufügen:

```python
def get_daily_releasable_projects(self, daily_min_commits: int = 3) -> List[str]:
    """Projekte die beim täglichen Release freigegeben werden sollen."""
    releasable = []
    for project, batch in self.pending.items():
        count = len(batch.get('commits', []))
        if count >= daily_min_commits:
            releasable.append(project)
    return releasable
```

**Step 4: Tests ausführen**

```bash
pytest tests/unit/test_patch_notes_batcher.py -xvs
```
Expected: PASS

**Step 5: Commit**

```bash
git add src/integrations/patch_notes_batcher.py tests/unit/test_patch_notes_batcher.py
git commit -m "feat: Batcher — get_daily_releasable_projects() für täglichen Release-Mode"
```

---

## Task 2: Bot — Täglicher Cron-Task

**Files:**
- Modify: `src/bot.py:1363-1437` (neben `weekly_patch_notes_release`)

**Step 1: Täglichen Cron-Task hinzufügen**

In `src/bot.py`, neuen Task nach `weekly_patch_notes_release` einfügen:

```python
@tasks.loop(hours=1)
async def daily_patch_notes_release(self):
    """Täglicher Patch-Notes-Release für Projekte mit release_mode: daily."""
    try:
        batcher = getattr(self, 'patch_notes_batcher', None)
        gh = getattr(self, 'github_integration', None)
        if not batcher or not gh:
            return

        now = datetime.now()

        # Projekte mit daily release_mode prüfen
        for project_name, project_config in self.config.projects.items():
            if not isinstance(project_config, dict):
                continue
            pn_config = project_config.get('patch_notes', {})
            if pn_config.get('release_mode') != 'daily':
                continue

            daily_hour = pn_config.get('daily_release_hour', 22)
            if now.hour != daily_hour:
                continue

            daily_min = pn_config.get('daily_min_commits', 3)
            if not batcher.has_pending(project_name):
                continue

            pending = batcher.pending.get(project_name, {})
            commit_count = len(pending.get('commits', []))
            if commit_count < daily_min:
                continue

            # Release!
            commits = batcher.release_batch(project_name)
            if not commits:
                continue

            self.logger.info(
                f"📅 Täglicher Release für {project_name}: {len(commits)} Commits"
            )

            try:
                repo_url = (
                    project_config.get('repo_url')
                    or project_config.get('repository_url')
                    or ''
                )
                pusher = commits[-1].get('author', {}).get('name', 'daily-release')

                await gh._send_push_notification(
                    repo_name=project_name,
                    repo_url=repo_url,
                    branch='main',
                    pusher=pusher,
                    commits=commits,
                    skip_batcher=True,
                )
            except Exception as e:
                self.logger.error(
                    f"❌ Täglicher Release für {project_name} fehlgeschlagen: {e}",
                    exc_info=True
                )
    except Exception as e:
        self.logger.error(f"❌ Täglicher Patch-Notes-Cron Fehler: {e}", exc_info=True)

@daily_patch_notes_release.before_loop
async def before_daily_patch_notes(self):
    """Warte bis Bot bereit ist."""
    await self.wait_until_ready()
    # Sammle daily-Projekte für Log
    daily_projects = [
        name for name, cfg in self.config.projects.items()
        if isinstance(cfg, dict) and cfg.get('patch_notes', {}).get('release_mode') == 'daily'
    ]
    if daily_projects:
        hours = set()
        for name in daily_projects:
            h = self.config.projects[name].get('patch_notes', {}).get('daily_release_hour', 22)
            hours.add(h)
        self.logger.info(
            f"📅 Täglicher Patch-Notes-Cron gestartet "
            f"(Projekte: {', '.join(daily_projects)}, "
            f"Uhrzeiten: {', '.join(f'{h}:00' for h in sorted(hours))})"
        )
```

**Step 2: Task in `start_tasks()` starten**

In der `start_tasks()` Methode (ca. Zeile 1112-1118), hinzufügen:

```python
if not self.daily_patch_notes_release.is_running():
    self.daily_patch_notes_release.start()
```

**Step 3: Commit**

```bash
git add src/bot.py
git commit -m "feat: Täglicher Patch-Notes-Cron für release_mode: daily"
```

---

## Task 3: Git-Tag-Aware Version Resolution

**Files:**
- Modify: `src/integrations/github_integration/notifications_mixin.py:237-256` (`_resolve_version`)
- Modify: `src/integrations/github_integration/notifications_mixin.py:382-408` (`_get_last_version_from_git`)

**Step 1: Neue Methode `_get_version_from_commit_tags()`**

In `notifications_mixin.py`, neue Methode nach `_get_last_version_from_git` (Zeile 408):

```python
def _get_version_from_commit_tags(self, commits: list, repo_name: str) -> Optional[str]:
    """Suche Git-Tags auf den Commits im aktuellen Batch.

    Wenn ein Commit exakt auf einem Tag liegt (z.B. v0.15.0),
    nutze diesen Tag als Version statt Semver zu berechnen.
    """
    try:
        import subprocess
        project_config = self.bot.config.projects.get(repo_name, {})
        project_path = project_config.get('path', '')
        if not project_path:
            return None

        # Alle Commit-SHAs sammeln
        commit_shas = set()
        for commit in commits:
            sha = commit.get('id', commit.get('sha', ''))
            if sha:
                commit_shas.add(sha)

        if not commit_shas:
            return None

        # Alle Semver-Tags mit ihren Commits holen
        result = subprocess.run(
            ['git', 'tag', '-l', 'v*', '--format=%(refname:short) %(objectname:short)'],
            capture_output=True, text=True, cwd=project_path, timeout=5
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None

        # Tags auf Commits im Batch finden
        matched_tags = []
        for line in result.stdout.strip().splitlines():
            parts = line.split()
            if len(parts) != 2:
                continue
            tag_name, tag_sha = parts
            # Kurzform-Match (7 Zeichen)
            for commit_sha in commit_shas:
                if commit_sha.startswith(tag_sha) or tag_sha.startswith(commit_sha[:7]):
                    version = tag_name.lstrip('v')
                    if re.match(r'^\d+\.\d+\.\d+$', version):
                        matched_tags.append(version)

        if not matched_tags:
            return None

        # Neuesten Tag zurückgeben (höchste Version)
        matched_tags.sort(key=lambda v: [int(x) for x in v.split('.')], reverse=True)
        self.logger.info(f"🏷️ Git-Tag im Batch gefunden: v{matched_tags[0]} ({repo_name})")
        return matched_tags[0]
    except Exception as e:
        self.logger.debug(f"Git-Tag-Suche fehlgeschlagen: {e}")
        return None
```

**Step 2: `_resolve_version()` anpassen — Git-Tags als Priorität 1**

In `_resolve_version()` (Zeile 237), Git-Tag-Check VOR Semver einfügen:

```python
def _resolve_version(self, ai_result, commits: list, repo_name: str = "") -> str:
    """Bestimme Version: Git-Tags > Commit-Text > SemVer > AI > Fallback. NIE None."""
    # 1. Git-Tags auf Commits im Batch (zuverlässigste Quelle)
    tag_v = self._get_version_from_commit_tags(commits, repo_name)
    if tag_v:
        return tag_v

    # 2. Aus Commits (expliziter Version-Tag, z.B. "feat: Release v2.1.0")
    v = self._extract_version_from_commits(commits)
    if v:
        return v

    # 3. Semantic Versioning: Letzte Version + Commit-Typen → nächste Version
    sem_v = self._calculate_semver(commits, repo_name)
    if sem_v:
        return sem_v

    # 4. Aus AI-Ergebnis (nur echte Versionen)
    if isinstance(ai_result, dict):
        ai_v = ai_result.get('version')
        if ai_v and ai_v != 'patch' and not ai_v.startswith('0.0.'):
            return ai_v

    # 5. Auto-Version (Fallback, IMMER)
    return f"patch.{datetime.now(timezone.utc).strftime('%Y.%m.%d')}"
```

**Step 3: `_get_last_version_from_git` robuster machen — annotated Tags unterstützen**

In `_get_last_version_from_git()` (Zeile 382), `--sort=-v:refname` verwenden statt lexikographisch:

Die bestehende Implementierung nutzt bereits `--sort=-v:refname` — prüfen und ggf. `git describe --tags --abbrev=0` als robusteren Fallback ergänzen.

**Step 4: Pipeline-Metriken anpassen**

In `_send_push_notification()` (Zeile 194), `version_source` um `'git_tag'` erweitern:

```python
'version_source': 'git_tag' if self._get_version_from_commit_tags(commits, repo_name) else
                  ('explicit' if self._extract_version_from_commits(commits) else
                  ('semver' if self._calculate_semver(commits, repo_name) else 'fallback')),
```

**Step 5: Commit**

```bash
git add src/integrations/github_integration/notifications_mixin.py
git commit -m "feat: Git-Tag-aware Version Resolution — Tags auf Commits im Batch erkennen"
```

---

## Task 4: Prompt-Template `gaming_community_v2`

**Files:**
- Modify: `src/integrations/prompt_ab_testing.py:122-129` (neues Template registrieren)
- Modify: `src/integrations/prompt_ab_testing.py:366-509` (neues Template nach bestehendem)
- Modify: `src/integrations/prompt_ab_testing.py:529` (Routing)

**Step 1: Neues Template `gaming_community_v2` einfügen**

Nach `_get_gaming_community_template()` (Zeile 509), neue Methode:

```python
def _get_gaming_community_v2_template(self, language: str = 'de') -> str:
    """Gaming Community v2 — Story-Telling mit konkretem Spielgefühl.

    Unterschied zu v1:
    - Jedes Feature wird als Erlebnis beschrieben (wie fühlt es sich an?)
    - Konkrete Zahlen und Details aus Commit-Bodies werden erzwungen
    - → Pfeil-Format statt Bullet-Points
    - Narrativer Bogen statt trockene Liste
    - Längere Feature-Beschreibungen (3-5 Sätze pro Highlight)
    """
    if language == 'en':
        return self._get_gaming_community_v2_template_en()
    return self._get_gaming_community_v2_template_de()

def _get_gaming_community_v2_template_de(self) -> str:
    return """Du bist ein leidenschaftlicher Game-Developer der sein eigenes Update vorstellt.
Deine Zielgruppe: GAMER und BOS-Fans auf einem Discord-Server für {project}.

DU MUSST DIESE REGELN BEFOLGEN:

STIL-REGELN:
- Schreibe wie ein begeisterter Entwickler der seine Community über Neuigkeiten informiert
- Jedes Feature MUSS beschreiben WIE ES SICH ANFÜHLT, nicht nur was es tut
- Nutze konkrete Zahlen aus den Commits: "30 Szenarien", "26 Upgrades", "10 Ränge" — NIEMALS "erweitert" oder "verbessert" ohne Details
- Sprich den Leser direkt an: "du", "deine", "ihr"
- Verwende das → Pfeil-Format für jeden Punkt
- Nutze Gaming-Sprache: "Patch", "Content-Update", "Quality of Life", "Buff", "Nerf"

VERBOTEN:
- NIEMALS Code, Commits, Git, TypeScript, React, Docker, CI/CD, Refactoring, Infrastruktur erwähnen
- NIEMALS mehrere Features zu einem Punkt zusammenfassen — jedes Feature ist EINZELN
- NIEMALS generische Phrasen wie "verschiedene Verbesserungen" oder "allgemeine Optimierungen"
- NIEMALS Design-Docs oder Konzepte als implementierte Features verkaufen
- NIEMALS Features erfinden die nicht in den Commits stehen

BESCHREIBUNGS-TIEFE PRO FEATURE:
- Highlight-Features (neue Systeme, große Änderungen): 3-5 Sätze
  → Was ist es? → Wie fühlt es sich an? → Was bedeutet es für dein Gameplay? → Ein konkretes Beispiel
- Normale Features: 2-3 Sätze
  → Was ändert sich? → Warum ist das cool?
- Fixes/Improvements: 1-2 Sätze
  → Was war das Problem? → Wie ist es jetzt?

BEISPIEL für gute Feature-Beschreibung:
SCHLECHT: "Stadtsuche mit Autocomplete eingeführt"
GUT: "→ **Stadtsuche mit Autocomplete** — Tippe oben in die Suchleiste den Namen einer Stadt ein und MayDay Sim zeigt dir sofort Vorschläge. Wähle eine Stadt aus und die Kamera fliegt in einer cinematischen 3-Phasen-Fahrt dorthin: erst rauszoomen, dann hinfliegen, dann reinzoomen. Perfekt um schnell zu einer neuen Region zu wechseln."

SCHLECHT: "Karriere-System hinzugefügt"
GUT: "→ **Karriere-System mit 10 Rängen** — Vom Anwärter bis zum Branddirektor: Jeder Einsatz bringt dir XP, die deinen Rang steigern. Höhere Ränge schalten neue Fahrzeuge, Cosmetics und Boni frei. Auf der Rangliste siehst du, wie du dich gegen andere Disponenten schlägst."

# CHANGELOG INFORMATIONEN
{changelog}

# COMMIT NACHRICHTEN (mit Details!)
{commits}

{stats_section}

ANWEISUNGEN:
1. Starte mit einem packenden 1-2-Satz-Hook
2. Ordne Features in die passenden Kategorien ein (siehe Format)
3. JEDES sichtbare Feature EINZELN mit → Pfeil-Prefix beschreiben
4. Nutze die konkreten Zahlen und Details aus den Commit-Bodies
5. Pro Highlight-Feature: 3-5 Sätze (Was? Wie fühlt es sich an? Gameplay-Impact?)
6. Pro normales Feature: 2-3 Sätze
7. Leere Kategorien weglassen
8. Ende mit Teaser (falls Infos vorhanden)
9. MINDESTENS 2500, MAXIMAL 3800 Zeichen (Discord)

FORMAT:
> 🚨 **[Packender Hook — was ist die größte Neuigkeit?]**

🆕 **Neuer Content & Features**
→ **Feature-Name** — Ausführliche Beschreibung aus Spieler-Perspektive...

🎨 **Design & Look** (nur bei visuellen Änderungen)
→ **Was sich visuell geändert hat** — Wie es sich anfühlt...

🎮 **Gameplay-Verbesserungen**
→ **Verbesserung** — Was ist jetzt besser und warum...

🛡️ **Stabilität & Performance** (nur wenn relevant)
→ Zusammengefasste technische Verbesserungen aus Spieler-Sicht

🔮 **In Entwicklung** (wenn Feature-Branches existieren)
→ Teaser für kommende Features

{stats_line}"""

def _get_gaming_community_v2_template_en(self) -> str:
    return """You are a passionate game developer presenting your own update.
Your audience: GAMERS and emergency services fans on a Discord server for {project}.

YOU MUST FOLLOW THESE RULES:

STYLE RULES:
- Write like an enthusiastic developer informing their community about what's new
- Every feature MUST describe HOW IT FEELS, not just what it does
- Use concrete numbers from the commits: "30 scenarios", "26 upgrades", "10 ranks" — NEVER "improved" or "extended" without details
- Address the reader directly: "you", "your"
- Use → arrow format for each point
- Use gaming language: "Patch", "Content Update", "Quality of Life", "buff", "nerf"

FORBIDDEN:
- NEVER mention code, commits, git, TypeScript, React, Docker, CI/CD, refactoring, infrastructure
- NEVER merge multiple features into one point — each feature is SEPARATE
- NEVER use generic phrases like "various improvements" or "general optimizations"
- NEVER present design docs or concepts as implemented features
- NEVER invent features not present in the commits

DESCRIPTION DEPTH PER FEATURE:
- Highlight features (new systems, major changes): 3-5 sentences
  → What is it? → How does it feel? → What does it mean for gameplay? → A concrete example
- Normal features: 2-3 sentences
  → What changes? → Why is it cool?
- Fixes/Improvements: 1-2 sentences
  → What was the problem? → How is it now?

EXAMPLE of good feature description:
BAD: "City search with autocomplete added"
GOOD: "→ **City Search with Autocomplete** — Type a city name into the search bar and MayDay Sim instantly shows suggestions. Select a city and the camera flies there in a cinematic 3-phase journey: zoom out, fly over, zoom in. Perfect for quickly switching to a new region."

# CHANGELOG INFORMATION
{changelog}

# COMMIT MESSAGES (with details!)
{commits}

{stats_section}

INSTRUCTIONS:
1. Start with an exciting 1-2 sentence hook
2. Sort features into the right categories (see format)
3. EVERY visible feature INDIVIDUALLY described with → arrow prefix
4. Use concrete numbers and details from commit bodies
5. Per highlight feature: 3-5 sentences (What? How does it feel? Gameplay impact?)
6. Per normal feature: 2-3 sentences
7. Skip empty categories
8. End with teaser (if info available)
9. MINIMUM 2500, MAXIMUM 3800 characters (Discord)

FORMAT:
> 🚨 **[Exciting hook — what's the biggest news?]**

🆕 **New Content & Features**
→ **Feature Name** — Detailed description from player perspective...

🎨 **Design & Look** (only for visual changes)
→ **What changed visually** — How it feels...

🎮 **Gameplay Improvements**
→ **Improvement** — What's better now and why...

🛡️ **Stability & Performance** (only if relevant)
→ Summarized technical improvements from player perspective

🔮 **In Development** (if feature branches exist)
→ Teasers for upcoming features

{stats_line}"""
```

**Step 2: Template registrieren**

In `_create_default_variants()` (ca. Zeile 122), nach `gaming_community_v1`:

```python
PromptVariant(
    id='gaming_community_v2',
    name='Gaming Community Story-Telling',
    description='Spiel-Community v2 — Story-Telling mit konkretem Spielgefühl, → Pfeil-Format, ausführliche Feature-Beschreibungen',
    template=self._get_gaming_community_v2_template('de'),
    created_at=datetime.now(timezone.utc).isoformat(),
    active=True
),
```

**Step 3: Routing in `get_variant_template()`**

In `get_variant_template()` (ca. Zeile 529), nach `gaming_community_v1`:

```python
elif variant_id == 'gaming_community_v2':
    return self._get_gaming_community_v2_template(language)
```

**Step 4: Commit**

```bash
git add src/integrations/prompt_ab_testing.py
git commit -m "feat: gaming_community_v2 Template — Story-Telling mit Spielgefühl und konkreten Details"
```

---

## Task 5: Commit-Body-Extraktion verbessern

**Files:**
- Modify: `src/integrations/github_integration/ai_patch_notes_mixin.py:329-363`

**Step 1: Commit-Bodies besser nutzen**

In `_classify_and_format_commits()` (Zeile 329), die Body-Extraktion für normale Commits erweitern:

Aktuell werden Bodies nur angezeigt wenn >2 nicht-leere Zeilen. Das ist zu restriktiv — die meisten MayDay-Commits haben 1-2 Zeilen Body mit wertvollen Details (z.B. "26 Upgrades mit Slot-System").

Änderung in Zeile 344:

```python
# ALT (Zeile 344):
# if len(body_lines) > 2:

# NEU:
if body_lines:
    body = '\n'.join(body_lines[:30])
    classified_lines.append(
        f"- [{tag}] {title}\n  {body}\n  (by {author})"
    )
else:
    classified_lines.append(f"- [{tag}] {title} (by {author})")
```

Das gibt der AI ALLE verfügbaren Details aus dem Commit-Body, nicht nur bei langen Bodies.

**Step 2: Commit**

```bash
git add src/integrations/github_integration/ai_patch_notes_mixin.py
git commit -m "fix: Commit-Bodies immer an AI weitergeben — auch kurze Bodies enthalten wertvolle Details"
```

---

## Task 6: Config für MayDay Sim aktualisieren

**Files:**
- Modify: `config/config.yaml` (mayday_sim Sektion)

**Step 1: Daily-Release-Mode + v2-Template aktivieren**

Im `mayday_sim` Block unter `patch_notes:` hinzufügen/ändern:

```yaml
mayday_sim:
  # ... (bestehende Config bleibt)
  patch_notes:
    enabled: true
    language: de
    use_ai: true
    use_critical_model: false
    preferred_variant: gaming_community_v2    # NEU: v2 statt v1
    release_mode: daily                        # NEU: täglicher Release
    daily_release_hour: 22                     # NEU: um 22:00 Uhr
    daily_min_commits: 3                       # NEU: Minimum 3 Commits
    project_description: "MayDay Sim — Realistische Leitstellen-Simulation..."  # bleibt
    target_audience: "Gamer, BOS-Enthusiasten und Blaulicht-Fans..."  # bleibt
    batch_threshold: 8
    emergency_threshold: 30                    # GEÄNDERT: 30 statt 20 (daily greift vorher)
    cron_day: sunday
    cron_hour: 20
    cron_min_commits: 3
```

**Step 2: Commit**

```bash
git add config/config.yaml
git commit -m "feat: MayDay Sim — daily Release-Mode + gaming_community_v2 Template aktiviert"
```

---

## Task 7: Integration testen

**Step 1: Batcher-Tests ausführen**

```bash
pytest tests/unit/test_patch_notes_batcher.py -xvs
```

**Step 2: Bot-Startup testen**

```bash
source .venv/bin/activate
timeout 15 python -c "
from src.utils.config import Config
from src.integrations.patch_notes_batcher import PatchNotesBatcher
from pathlib import Path

config = Config()
pn = config.projects.get('mayday_sim', {}).get('patch_notes', {})
print(f'release_mode: {pn.get(\"release_mode\")}')
print(f'daily_release_hour: {pn.get(\"daily_release_hour\")}')
print(f'preferred_variant: {pn.get(\"preferred_variant\")}')
print('✅ Config lädt korrekt')
" 2>&1
```

**Step 3: Prompt-Template-Validierung**

```bash
source .venv/bin/activate
timeout 15 python -c "
from src.integrations.prompt_ab_testing import PromptABTesting
from pathlib import Path

ab = PromptABTesting(Path('/tmp/test_ab'))
t = ab.get_variant_template('gaming_community_v2', 'de')
assert '→' in t, 'Pfeil-Format fehlt'
assert 'NIEMALS' in t, 'Verbote fehlen'
assert 'Spielgefühl' in t or 'ANFÜHLT' in t, 'Erlebnis-Regel fehlt'
print(f'✅ Template OK ({len(t)} Zeichen)')
print(f'Erste 200 Zeichen: {t[:200]}...')
" 2>&1
```

**Step 4: Git-Tag-Resolution testen**

```bash
source .venv/bin/activate
timeout 15 python -c "
import subprocess, re
project_path = '/srv/leitstelle/app'

# Simuliere _get_version_from_commit_tags
result = subprocess.run(
    ['git', 'tag', '-l', 'v*', '--format=%(refname:short) %(objectname:short)'],
    capture_output=True, text=True, cwd=project_path, timeout=5
)
tags = {}
for line in result.stdout.strip().splitlines():
    parts = line.split()
    if len(parts) == 2:
        tags[parts[0]] = parts[1]

# Teste mit bekanntem Commit
result2 = subprocess.run(
    ['git', 'rev-parse', '--short', 'v0.15.0'],
    capture_output=True, text=True, cwd=project_path, timeout=5
)
v15_sha = result2.stdout.strip()
print(f'v0.15.0 Tag SHA: {v15_sha}')
print(f'Tag in Liste: {\"v0.15.0\" in tags}')
print(f'SHA Match: {tags.get(\"v0.15.0\", \"\")}')
print(f'✅ Git-Tag-Resolution funktioniert')
" 2>&1
```

**Step 5: Commit**

```bash
git add -A
git commit -m "test: Integration-Tests für Patch Notes v6 (Batcher, Config, Template, Git-Tags)"
```

---

## Task 8: CLAUDE.md aktualisieren

**Files:**
- Modify: `CLAUDE.md` (Patch Notes Safety Sektion)

**Step 1: Neue Config-Optionen dokumentieren**

In der "Patch Notes Safety" Sektion hinzufügen:

```markdown
### Täglicher Release-Mode (seit 2026-03-29)
- **release_mode: daily** — Täglicher Release statt nur wöchentlichem Cron
- **daily_release_hour: 22** — Uhrzeit für täglichen Release (Default: 22:00)
- **daily_min_commits: 3** — Minimum Commits für täglichen Release
- **Fallback:** Sonntag 20:00 wenn daily nicht getriggert hat
- **Emergency:** ≥30 Commits löst Sofort-Release aus (erhöht von 20)
- **Git-Tag-Aware:** Version wird aus Git-Tags im Commit-Batch erkannt (Priorität 1)
- **gaming_community_v2:** Story-Telling Template mit konkretem Spielgefühl (→ Pfeil-Format)
```

**Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: Patch Notes v6 — Daily Release-Mode + gaming_community_v2 dokumentiert"
```
