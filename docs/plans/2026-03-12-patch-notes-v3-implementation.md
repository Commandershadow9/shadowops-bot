# Patch Notes v3 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Zukunftssicheres, projekt-uebergreifendes Patch Notes System mit zentraler API, Discord Embed Redesign, Security-Filterung und SEO-optimierten shared-ui Komponenten.

**Architecture:** ShadowOps Bot wird zum zentralen Changelog-Service (SQLite DB + REST API auf Port 8766). Discord-Embeds werden zum Teaser mit integrierten Bewertungs-Buttons. shared-ui liefert wiederverwendbare React-Komponenten die jedes Projekt einbindet. Content Sanitizer verhindert Leaks sensibler Informationen.

**Tech Stack:** Python 3.12 (aiohttp, aiosqlite), discord.py 2.7, React/TypeScript (shared-ui), Next.js 16 (ZERODOX), Tailwind CSS

**Design:** `docs/plans/2026-03-12-patch-notes-v3-design.md`

**Security-Hinweis:** HTML-Rendering von Markdown nutzt `sanitize-html` (bereits bestehende Praxis in ZERODOX). Nur erlaubte Tags (h2, h3, p, strong, em, ul, ol, li, a, br) und Attribute (href, target, rel) werden durchgelassen. Content stammt ausschliesslich aus der eigenen AI-Engine, nicht aus User-Input.

---

## Phase 1: Backend Foundation (ShadowOps Bot)

### Task 1: Zentrale Changelog-DB

**Files:**
- Create: `src/integrations/changelog_db.py`
- Test: `tests/unit/test_changelog_db.py`

**Step 1: Write the test file**

```python
# tests/unit/test_changelog_db.py
"""Tests fuer die zentrale Changelog-Datenbank."""
import pytest
import asyncio
from pathlib import Path

@pytest.fixture
def changelog_db(temp_dir):
    from src.integrations.changelog_db import ChangelogDB
    db = ChangelogDB(db_path=str(temp_dir / "test_changelogs.db"))
    loop = asyncio.get_event_loop()
    loop.run_until_complete(db.initialize())
    yield db
    loop.run_until_complete(db.close())

class TestChangelogDB:
    def test_upsert_and_get(self, changelog_db):
        loop = asyncio.get_event_loop()

        entry = {
            'project': 'zerodox',
            'version': '2.9.1',
            'title': 'Security Update',
            'tldr': 'Neue Auth-Middleware',
            'content': '# Changes\n- Feature A\n- Fix B',
            'changes': [{'type': 'feature', 'description': 'Feature A', 'details': []}],
            'stats': {'commits': 15, 'files_changed': 8},
            'seo_keywords': ['security', 'auth', 'oauth2'],
            'seo_description': 'ZERODOX 2.9.1: Neue Auth-Middleware',
            'language': 'de',
        }

        # Upsert
        loop.run_until_complete(changelog_db.upsert(entry))

        # Get by project+version
        result = loop.run_until_complete(
            changelog_db.get('zerodox', '2.9.1')
        )
        assert result is not None
        assert result['title'] == 'Security Update'
        assert result['seo_keywords'] == ['security', 'auth', 'oauth2']

    def test_list_by_project(self, changelog_db):
        loop = asyncio.get_event_loop()

        for v in ['1.0.0', '1.1.0', '2.0.0']:
            loop.run_until_complete(changelog_db.upsert({
                'project': 'zerodox', 'version': v,
                'title': f'v{v}', 'tldr': 'Update',
                'content': '', 'changes': [], 'stats': {},
                'seo_keywords': [], 'seo_description': '',
                'language': 'de',
            }))

        results = loop.run_until_complete(
            changelog_db.list_by_project('zerodox', page=1, limit=2)
        )
        assert len(results['data']) == 2
        assert results['meta']['total'] == 3
        assert results['data'][0]['version'] == '2.0.0'  # neueste zuerst

    def test_upsert_updates_existing(self, changelog_db):
        loop = asyncio.get_event_loop()

        base = {
            'project': 'guildscout', 'version': '1.0.0',
            'title': 'Original', 'tldr': '', 'content': '',
            'changes': [], 'stats': {}, 'seo_keywords': [],
            'seo_description': '', 'language': 'de',
        }
        loop.run_until_complete(changelog_db.upsert(base))

        base['title'] = 'Updated'
        loop.run_until_complete(changelog_db.upsert(base))

        result = loop.run_until_complete(changelog_db.get('guildscout', '1.0.0'))
        assert result['title'] == 'Updated'

    def test_get_nonexistent_returns_none(self, changelog_db):
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(changelog_db.get('nope', '0.0.0'))
        assert result is None
```

**Step 2: Run test to verify it fails**

```bash
cd /home/cmdshadow/shadowops-bot
.venv/bin/pytest tests/unit/test_changelog_db.py -x -v
```
Expected: FAIL (ModuleNotFoundError)

**Step 3: Implement ChangelogDB**

Create `src/integrations/changelog_db.py`:
- Async SQLite via `aiosqlite`
- Schema: `changelogs` table with `UNIQUE(project, version)` constraint
- Methods: `initialize()`, `close()`, `upsert(entry)`, `get(project, version)`, `list_by_project(project, page, limit)`, `list_all_projects()`
- JSON serialization for `changes`, `stats`, `seo_keywords` fields
- Default DB path: `data/changelogs.db`

**Step 4: Run tests**

```bash
.venv/bin/pytest tests/unit/test_changelog_db.py -x -v
```
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/integrations/changelog_db.py tests/unit/test_changelog_db.py
git commit -m "feat: Zentrale Changelog-DB (SQLite) fuer alle Projekte"
```

---

### Task 2: Content Sanitizer

**Files:**
- Create: `src/integrations/content_sanitizer.py`
- Test: `tests/unit/test_content_sanitizer.py`

**Step 1: Write the test file**

```python
# tests/unit/test_content_sanitizer.py
"""Tests fuer den Content Sanitizer (Security-Filter)."""
import pytest
from src.integrations.content_sanitizer import ContentSanitizer


class TestContentSanitizer:
    @pytest.fixture
    def sanitizer(self):
        return ContentSanitizer()

    def test_removes_absolute_paths(self, sanitizer):
        text = "Gefixt in /home/cmdshadow/GuildScout/src/auth.py"
        result = sanitizer.sanitize(text)
        assert '/home/' not in result
        assert 'cmdshadow' not in result

    def test_removes_relative_src_paths(self, sanitizer):
        text = "Fehler in src/integrations/ai_learning/agent.py behoben"
        result = sanitizer.sanitize(text)
        assert 'src/integrations' not in result

    def test_removes_ip_addresses(self, sanitizer):
        text = "Server 10.8.0.1 und 172.23.0.5 neu gestartet"
        result = sanitizer.sanitize(text)
        assert '10.8.0.1' not in result
        assert '172.23.0.5' not in result

    def test_removes_port_numbers(self, sanitizer):
        text = "API laeuft auf Port 5433 und Port 8766"
        result = sanitizer.sanitize(text)
        assert '5433' not in result
        assert '8766' not in result

    def test_removes_config_references(self, sanitizer):
        text = "Token aus config.yaml geladen, .env aktualisiert"
        result = sanitizer.sanitize(text)
        assert 'config.yaml' not in result
        assert '.env' not in result

    def test_removes_localhost(self, sanitizer):
        text = "Verbindung zu 127.0.0.1:6379 und localhost:5433"
        result = sanitizer.sanitize(text)
        assert '127.0.0.1' not in result
        assert 'localhost' not in result

    def test_preserves_normal_content(self, sanitizer):
        text = "Neues OAuth2-Feature implementiert. API-Performance um 40% verbessert."
        result = sanitizer.sanitize(text)
        assert result == text

    def test_vague_security_fixes(self, sanitizer):
        text = "SQL-Injection in /api/users/login gefixt"
        result = sanitizer.sanitize(text)
        assert '/api/users/login' not in result

    def test_removes_tilde_paths(self, sanitizer):
        text = "Datei unter ~/shadowops-bot/config/secrets.json"
        result = sanitizer.sanitize(text)
        assert '~/shadowops-bot' not in result

    def test_custom_patterns(self):
        sanitizer = ContentSanitizer(custom_patterns=[r'GEHEIM-\d+'])
        text = "Token GEHEIM-12345 wurde rotiert"
        result = sanitizer.sanitize(text)
        assert 'GEHEIM-12345' not in result

    def test_sanitize_dict_recursively(self, sanitizer):
        data = {
            'title': 'Update',
            'content': 'Gefixt in /home/user/app/main.py',
            'highlights': ['Server 10.0.0.1 optimiert', 'Normaler Text'],
        }
        result = sanitizer.sanitize_dict(data, keys=['content', 'highlights'])
        assert '/home/' not in result['content']
        assert '10.0.0.1' not in result['highlights'][0]
        assert result['highlights'][1] == 'Normaler Text'
```

**Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/unit/test_content_sanitizer.py -x -v
```

**Step 3: Implement ContentSanitizer**

Create `src/integrations/content_sanitizer.py`:
- Regex-basierter Filter mit Default-Patterns fuer: absolute Pfade, Tilde-Pfade, src/-Pfade, IPs, localhost, Ports, Config-Dateien, API-Endpunkte
- `sanitize(text)` — wendet alle Patterns an, bereinigt Leerzeichen
- `sanitize_dict(data, keys)` — rekursiv auf Dict/Listen anwenden
- `custom_patterns` Parameter fuer projekt-spezifische Erweiterungen
- `enabled` Flag zum Deaktivieren

**Step 4: Run tests**

```bash
.venv/bin/pytest tests/unit/test_content_sanitizer.py -x -v
```
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/integrations/content_sanitizer.py tests/unit/test_content_sanitizer.py
git commit -m "feat: Content Sanitizer filtert sensible Infos aus Patch Notes"
```

---

### Task 3: Changelog-API auf dem Health-Server

**Files:**
- Modify: `src/utils/health_server.py`
- Test: `tests/unit/test_changelog_api.py`

**Step 1: Write the test file**

Tests fuer: GET list, GET detail, POST with/without auth, CORS headers, RSS feed format, Sitemap XML format.

Mock-Setup: `mock_bot`, `mock_changelog_db` als AsyncMock.
Nutze `aiohttp.test_utils.TestClient` + `TestServer`.

**Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/unit/test_changelog_api.py -x -v
```

**Step 3: Extend HealthCheckServer**

Modify `src/utils/health_server.py`:
- Extract `_create_app()` method from `start()` (fuer Testbarkeit)
- Add CORS middleware: `Access-Control-Allow-Origin: *`
- Add constructor params: `changelog_db=None`, `api_key=''`
- New routes:
  - `GET /api/changelogs` — Query param `project` (required), `page`, `limit`
  - `GET /api/changelogs/{project}/{version}` — Detail
  - `POST /api/changelogs` — Auth via `X-API-Key`, validates required fields
  - `GET /api/changelogs/feed` — RSS 2.0 XML, Query param `project`, `format` (rss/atom)
  - `GET /api/changelogs/sitemap` — XML Sitemap Fragment, Query param `project`, `base_url`

**Step 4: Run tests**

```bash
.venv/bin/pytest tests/unit/test_changelog_api.py -x -v
```

**Step 5: Commit**

```bash
git add src/utils/health_server.py tests/unit/test_changelog_api.py
git commit -m "feat: Changelog REST API + RSS Feed + Sitemap auf Health-Server"
```

---

### Task 4: Bot-Integration (DB Init + API Key)

**Files:**
- Modify: `src/bot.py` (Changelog-DB Initialisierung)
- Modify: `config/config.example.yaml` (neue Sektion)

**Step 1: config.example.yaml erweitern**

Neue Sektion `changelog_api` hinzufuegen:
```yaml
changelog_api:
  enabled: true
  api_key: "CHANGE_ME_RANDOM_STRING"
```

**Step 2: bot.py setup_hook() erweitern**

Nach Health-Server Start, vor GitHub Integration:
- Import und Init `ChangelogDB`
- Setze `health_server.changelog_db` und `health_server.api_key`

**Step 3: Verify bot starts**

```bash
sudo systemctl restart shadowops-bot && sleep 5
curl -s http://127.0.0.1:8766/api/changelogs?project=zerodox | python3 -m json.tool
```

**Step 4: Commit**

```bash
git add src/bot.py config/config.example.yaml
git commit -m "feat: Changelog-DB und API-Key in Bot-Initialisierung"
```

---

## Phase 2: Discord Improvements (ShadowOps Bot)

### Task 5: Feedback-System entkoppeln

**Files:**
- Modify: `src/bot.py`
- Modify: `src/integrations/patch_notes_feedback.py`

**Step 1: bot.py — Feedback-Init aus ai_learning Block herausloesen**

Aktuell: Feedback-Init ist INNERHALB `if self.config.ai_learning_enabled and self.config.ai_enabled:` (Zeile 686). Das gesamte Block scheitert wegen Import-Fehler in `ai_learning`.

Aenderung: Feedback-Collector SEPARAT initialisieren, direkt nach GitHub Integration Setup, AUSSERHALB des ai_learning Blocks. Trainer ist optional (kann None sein).

**Step 2: patch_notes_feedback.py — Trainer optional machen**

`PatchNotesFeedbackCollector.__init__` akzeptiert `trainer=None`. Record-Methoden pruefen `if self.trainer:` vor Speicherung.

**Step 3: Verify**

```bash
sudo systemctl restart shadowops-bot && sleep 5
journalctl -u shadowops-bot --since "1 minute ago" | grep -i "feedback"
```
Expected: `"Feedback Collector initialisiert"`

**Step 4: Commit**

```bash
git add src/bot.py src/integrations/patch_notes_feedback.py
git commit -m "fix: Feedback-Collector unabhaengig von AI Learning initialisieren"
```

---

### Task 6: Discord Embed Redesign + Buttons

**Files:**
- Modify: `src/integrations/github_integration/notifications_mixin.py`
- Modify: `src/integrations/patch_notes_feedback.py`

**Step 1: Neues PatchNotesView in patch_notes_feedback.py**

Ersetze separate Feedback-Nachricht durch Buttons direkt am Embed:
- `PatchNotesView(ui.View)` mit 3 Buttons:
  - `👍 Gefaellt mir` (success) — Zaehler im Label, records reaction feedback
  - `⭐ Bewerten` (secondary) — oeffnet TextFeedbackModal
  - `🔗 Changelog oeffnen` (link) — URL-Button zur Changelog-Seite
- Timeout: 7 Tage (604800s)
- `track_patch_notes_message()` aendern: Keine separaten Reactions mehr, stattdessen View an die Nachricht haengen

**Step 2: Neues Embed-Format in notifications_mixin.py**

Neue Methode `_build_v3_customer_embed()`:
- Author-Feld: `repo_name.upper()` (Projekt-Wiedererkennung)
- Titel: `🚀 v{version} — {title}` verlinkt auf Changelog-Seite (NICHT GitHub Commits)
- Description: TL;DR als Blockquote (`> {tldr}`)
- Features: Ausgeschrieben mit `╰` Prefix (max 3)
- Breaking Changes: Ausgeschrieben mit `⚠️` (max 3)
- Fixes + Improvements: Nur Zaehler (`🐛 2 Bugfixes · ⚡ 3 Verbesserungen`)
- CTA-Block: Einladender Text zum Changelog
- Footer: Version, Commits, Lines Added/Removed

**Step 3: Content Sanitizer in Pipeline integrieren**

In `_send_push_notification()`: Nach AI-Generierung, vor Embed-Erstellung:
- Lade `ContentSanitizer` mit projekt-spezifischer Config
- Wende `sanitize_dict()` auf `ai_result` an

**Step 4: Sende-Methoden updaten**

`_send_to_customer_channels()` und `_send_external_git_notifications()`:
- Sende Embed MIT `view=PatchNotesView(...)` in EINER Nachricht
- ENTFERNE separaten Feedback-Button/-Nachricht
- ENTFERNE Reaction-Emoji Hinzufuegen (ersetzt durch Buttons)

**Step 5: Commit**

```bash
git add src/integrations/github_integration/notifications_mixin.py \
        src/integrations/patch_notes_feedback.py
git commit -m "feat: Discord Embed Redesign v3 — Teaser + Buttons + Sanitizer"
```

---

### Task 7: AI-Prompt Security-Regeln + SEO Keywords

**Files:**
- Modify: `src/integrations/github_integration/ai_patch_notes_mixin.py`
- Modify: `src/schemas/patch_notes.json`

**Step 1: Schema erweitern**

Neue Felder in `patch_notes.json`:
- `seo_keywords`: array of strings, 5-10 Keywords
- `seo_category`: enum (feature/security/performance/bugfix/maintenance)
- Beide in `required` aufnehmen

**Step 2: Security-Regeln in AI-Prompts**

In `_build_structured_prompt()` und `_build_fallback_prompt()` SICHERHEITSREGELN Block hinzufuegen:
- Keine Dateipfade, Server-Pfade, Verzeichnisstrukturen
- Keine IPs, Ports, Netzwerk-Details
- Security-Fixes vage beschreiben (WAS verbessert, nicht WIE)
- Keine alten verwundbaren Dependency-Versionen
- Keine Config-Dateien/Pfade

**Step 3: SEO-Keywords Anweisung**

Im strukturierten Prompt SEO_KEYWORDS Block:
- 5-10 spezifische, suchrelevante Keywords
- Passend zum tatsaechlichen Inhalt
- Mix Deutsch/Englisch erlaubt
- Keine generischen Keywords wie "update"

**Step 4: Commit**

```bash
git add src/integrations/github_integration/ai_patch_notes_mixin.py \
        src/schemas/patch_notes.json
git commit -m "feat: Security-Regeln und SEO-Keywords im AI-Prompt"
```

---

### Task 8: Web Exporter Migration auf zentrale API

**Files:**
- Modify: `src/integrations/patch_notes_web_exporter.py`
- Modify: `src/integrations/github_integration/notifications_mixin.py`
- Modify: `src/bot.py`

**Step 1: Web Exporter um zentrale DB erweitern**

Neuer Constructor-Parameter: `changelog_db=None`
Neue Methode: `export_and_store()` — schreibt PRIMAER in zentrale DB, File-Export als Backup, Projekt-API POST als optionaler Sekundaer-Modus.

**Step 2: notifications_mixin.py updaten**

`_export_structured_web_changelog()` und `_export_web_changelog()` nutzen `export_and_store()` statt `export()` + separatem `post_to_api()`.

**Step 3: bot.py — changelog_db an web_exporter weitergeben**

```python
self.web_exporter = PatchNotesWebExporter(
    default_output, api_endpoints,
    changelog_db=self.changelog_db
)
```

**Step 4: Commit**

```bash
git add src/integrations/patch_notes_web_exporter.py \
        src/integrations/github_integration/notifications_mixin.py \
        src/bot.py
git commit -m "feat: Web Exporter schreibt in zentrale Changelog-DB"
```

---

## Phase 3: Frontend (shared-ui + ZERODOX)

### Task 9: shared-ui Changelog Komponenten

**Files:**
- Create: `~/libs/shared-ui/components/Changelog/types.ts`
- Create: `~/libs/shared-ui/components/Changelog/ChangelogBadge.tsx`
- Create: `~/libs/shared-ui/components/Changelog/KeywordTags.tsx`
- Create: `~/libs/shared-ui/components/Changelog/ChangelogStats.tsx`
- Create: `~/libs/shared-ui/components/Changelog/Changelog.tsx`
- Create: `~/libs/shared-ui/components/Changelog/ChangelogDetail.tsx`
- Create: `~/libs/shared-ui/components/Changelog/index.ts`
- Modify: `~/libs/shared-ui/index.ts`

**Step 1: types.ts**

Interfaces: `ChangelogEntry`, `ChangelogChange`, `ChangelogStats`, `ChangelogListResponse`, `ChangelogDetailResponse`, `ChangelogSEOConfig`

**Step 2: ChangelogBadge.tsx**

Nutzt bestehende `Badge`-Komponente. Mapping: feature→success, fix→warning, improvement→info, breaking→danger, docs→default. Jeder Typ hat Emoji + Label.

**Step 3: KeywordTags.tsx**

Einfache Tag-Liste mit `--ui-primary`-basierten Farben. Pills-Style (`rounded-full`).

**Step 4: ChangelogStats.tsx**

`ChangelogStatsGrid` mit `StatCard` Subkomponente. Grid: 2 Spalten mobil, 4 Spalten desktop. Cards: Commits, Dateien, Lines Added (gruen), Coverage oder Lines Removed (rot).

**Step 5: Changelog.tsx (Liste)**

Client-Component mit:
- Filter-Tabs: Alle | Features | Fixes | Improvements (nutzt `changes`-Typen)
- GlassCard-Styling pro Eintrag (border-white/5, bg-white/[0.02], backdrop-blur)
- Pro Eintrag: Version-Badge, Datum, Titel, TL;DR, ChangelogBadges, KeywordTags
- Pagination mit Zurueck/Weiter
- `linkBuilder` Prop fuer projekt-spezifische URL-Patterns
- Fetcht von `{apiUrl}/api/changelogs?project={project}&page={page}`

**Step 6: ChangelogDetail.tsx**

Client-Component mit:
- Hero-Bereich: Datum, Version + Titel (h1)
- TL;DR als hervorgehobenes Blockquote
- ChangelogStatsGrid
- Strukturierte Changes nach Kategorie (aus `changes`-Array), NICHT raw Markdown
- Falls `changes` leer: Fallback auf Content-Markdown-Rendering (mit sanitize-html, XSS-geschuetzt)
- Lesezeit-Berechnung: `Math.ceil(content.length / 1000)` Minuten
- KeywordTags am Ende
- `backUrl` Prop fuer Zurueck-Link

**Step 7: index.ts + shared-ui/index.ts Exports**

Re-Exports aus `components/Changelog/index.ts`. Neue Eintraege in `~/libs/shared-ui/index.ts`.

**Step 8: Commit**

```bash
cd ~/libs/shared-ui
git add components/Changelog/ index.ts
git commit -m "feat: Changelog-Komponenten (Liste, Detail, Badges, Stats, Keywords)"
```

---

### Task 10: ZERODOX Migration auf shared-ui

**Files:**
- Modify: `~/ZERODOX/web/src/app/changelog/page.tsx`
- Create: `~/ZERODOX/web/src/app/changelog/ChangelogWrapper.tsx`
- Modify: `~/ZERODOX/web/src/app/changelog/[version]/page.tsx`
- Create: `~/ZERODOX/web/src/app/changelog/[version]/ChangelogDetailWrapper.tsx`
- Delete: `~/ZERODOX/web/src/app/changelog/ChangelogClient.tsx`
- Delete: `~/ZERODOX/web/src/app/changelog/[version]/ChangelogDetailClient.tsx`

**Step 1: ChangelogWrapper.tsx (Client-Component)**

Duenner Wrapper der `<Changelog />` aus shared-ui importiert und mit ZERODOX-spezifischen Props aufruft:
- `apiUrl`: aus env oder `/api/changelogs` (Proxy)
- `project`: `"zerodox"`
- `linkBuilder`: `(v) => '/changelog/' + v.replace(/\./g, '-')`

**Step 2: page.tsx (Server-Component)**

- Metadata + OpenGraph + Twitter Cards
- BreadcrumbStructuredData
- RSS Auto-Discovery Link
- Importiert `ChangelogWrapper`

**Step 3: ChangelogDetailWrapper.tsx (Client-Component)**

Duenner Wrapper fuer `<ChangelogDetail />` aus shared-ui:
- `apiUrl`, `project: "zerodox"`, `version`, `backUrl: "/changelog"`

**Step 4: [version]/page.tsx (Server-Component)**

- `generateMetadata()` fetcht von zentraler API fuer dynamische SEO-Daten (title, description, keywords, og)
- JSON-LD TechArticle Schema (via `<script type="application/ld+json">`, Content aus eigener API, XSS-sicher da keine User-Eingaben)
- BreadcrumbStructuredData
- Importiert `ChangelogDetailWrapper`

**Step 5: Bestehende API-Routes als Proxy beibehalten**

`/api/changelogs` Route in ZERODOX aendern: statt direkte DB-Abfrage, Proxy-Fetch an `http://127.0.0.1:8766/api/changelogs`.

**Step 6: Alte Client-Komponenten entfernen**

```bash
rm ~/ZERODOX/web/src/app/changelog/ChangelogClient.tsx
rm ~/ZERODOX/web/src/app/changelog/\[version\]/ChangelogDetailClient.tsx
```

**Step 7: Environment-Variable**

In ZERODOX docker-compose.yml oder .env:
```
CHANGELOG_API_URL=http://127.0.0.1:8766
```

**Step 8: Build testen**

```bash
cd ~/ZERODOX/web
NODE_OPTIONS="--max-old-space-size=2048" npx next build 2>&1 | tail -20
```

**Step 9: Commit**

```bash
cd ~/ZERODOX
git add web/src/app/changelog/ web/src/app/api/changelogs/
git commit -m "feat: Changelog migriert auf shared-ui + zentrale API"
```

---

### Task 11: SEO Enhancements

**Files:**
- Modify: `~/ZERODOX/web/src/app/layout.tsx` (RSS Link)
- Create/Modify: `~/ZERODOX/web/src/app/changelog/sitemap.ts`

**Step 1: RSS Auto-Discovery im Layout**

In `layout.tsx` head: `<link rel="alternate" type="application/rss+xml">` mit Changelog-Feed URL.

**Step 2: Sitemap fuer Changelogs**

Neue Datei `app/changelog/sitemap.ts`:
- Fetcht alle Changelog-Versionen von zentraler API
- Generiert Sitemap-Eintraege mit `url`, `lastModified`, `changeFrequency`, `priority`

**Step 3: Commit**

```bash
cd ~/ZERODOX
git add web/src/app/layout.tsx web/src/app/changelog/sitemap.ts
git commit -m "feat: RSS Auto-Discovery + Changelog Sitemap"
```

---

## Phase 4: Integration & Deploy

### Task 12: Daten migrieren

**Step 1: Bestehende ZERODOX-Changelogs in zentrale DB kopieren**

Python-Script das per HTTP die ZERODOX API abfragt und in die zentrale ChangelogDB schreibt.

**Step 2: Verify**

```bash
curl -s http://127.0.0.1:8766/api/changelogs?project=zerodox | python3 -m json.tool
```

---

### Task 13: Deploy + Smoke Test

**Step 1: ShadowOps Bot deployen**

```bash
sudo systemctl restart shadowops-bot && sleep 5
curl -s http://127.0.0.1:8766/health | python3 -m json.tool
curl -s http://127.0.0.1:8766/api/changelogs?project=zerodox | python3 -m json.tool
curl -s "http://127.0.0.1:8766/api/changelogs/feed?project=zerodox" | head -20
journalctl -u shadowops-bot --since "1 minute ago" | grep -i "feedback\|changelog"
```

**Step 2: ZERODOX deployen**

```bash
cd ~/ZERODOX
docker compose up -d --build zerodox-web && sleep 10
curl -s https://zerodox.de/changelog | head -5
```

**Step 3: End-to-End Smoke Test**

POST einen Test-Changelog via API, pruefen ob er auf der Seite erscheint, dann wieder loeschen.

**Step 4: Discord-Test**

Einen Test-Push in ein Repo machen um das neue Embed-Format mit Buttons zu verifizieren.

---

## Zusammenfassung

| Phase | Tasks | Neue Dateien | Geaenderte Dateien |
|-------|-------|-------------|-------------------|
| 1: Backend | 1-4 | changelog_db.py, content_sanitizer.py, 2 Tests | health_server.py, bot.py, config.example.yaml |
| 2: Discord | 5-8 | — | bot.py, notifications_mixin.py, patch_notes_feedback.py, ai_patch_notes_mixin.py, patch_notes.json, patch_notes_web_exporter.py |
| 3: Frontend | 9-11 | 7 shared-ui Dateien, 2 ZERODOX Wrapper, sitemap.ts | shared-ui/index.ts, ZERODOX pages, layout.tsx |
| 4: Deploy | 12-13 | — | — |

**Commits:** ~13
**Repos:** shadowops-bot, shared-ui, ZERODOX
