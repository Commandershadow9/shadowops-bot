# Patch Notes System v3 — Zukunftssicheres Design

**Datum:** 2026-03-12
**Status:** Approved
**Scope:** ShadowOps Bot + shared-ui + ZERODOX (+ alle zukünftigen Projekte)

## Motivation

Das bestehende Patch Notes System v2 hat mehrere Schwächen:
- Discord-Embeds sind visuell unattraktiv (kahler Link, kein Thumbnail, keine Wiedererkennung)
- Bewertungsfunktion in Discord funktioniert nicht (Import-Fehler in `ai_learning` → `feedback_collector = None`)
- Changelog-Seite (ZERODOX) ist minimalistisch — `changes`-Daten werden nicht gerendert
- SEO ist oberflächlich (generische Keywords, kein JSON-LD, keine Sitemap)
- Sensible Informationen (Dateipfade, IPs, Security-Fix-Details) können in Patch Notes landen
- System ist ZERODOX-spezifisch statt projekt-übergreifend nutzbar

## Architektur

### Zentrale Changelog-API (ShadowOps Bot)

Der ShadowOps Bot wird zum zentralen Changelog-Service. Die API läuft auf dem bestehenden
Health-Check-Server (Port 8766).

```
┌─────────────────┐     POST /api/changelogs
│  ShadowOps Bot  │◄────────────────────────────┐
│  (Generator +   │     GET  /api/changelogs     │
│   API-Server)   │─────────────────────┐        │
└────────┬────────┘                     │        │
         │ schreibt                     ▼        │
         ▼                       ┌──────────────┐│
┌─────────────────┐              │  Projekte    ││
│  SQLite DB      │              │  (Frontend)  ││
│  changelogs     │              │  ZERODOX     ││
│  Tabelle        │              │  GuildScout  ││
└─────────────────┘              │  Projekt X   ││
                                 └──────────────┘│
                                       │         │
                                ┌──────────────┐ │
                                │  shared-ui   │─┘
                                │ <Changelog/> │
                                └──────────────┘
```

### API-Endpoints

| Method | Endpoint | Auth | Beschreibung |
|--------|----------|------|-------------|
| GET | `/api/changelogs?project=x&page=1&limit=10` | - | Liste (paginiert) |
| GET | `/api/changelogs/{project}/{version}` | - | Detail |
| POST | `/api/changelogs` | X-API-Key | Neuer Eintrag |
| GET | `/api/changelogs/feed?project=x&format=rss` | - | RSS/Atom Feed |
| GET | `/api/changelogs/sitemap?project=x` | - | Sitemap-Fragment |

### Datenbank-Schema (SQLite)

```sql
CREATE TABLE changelogs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT NOT NULL,
    version TEXT NOT NULL,
    title TEXT NOT NULL,
    tldr TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    changes TEXT DEFAULT '[]',
    stats TEXT DEFAULT '{}',
    seo_keywords TEXT DEFAULT '[]',
    seo_description TEXT DEFAULT '',
    language TEXT DEFAULT 'de',
    published_at TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project, version)
);

CREATE INDEX idx_changelogs_project_date ON changelogs(project, published_at DESC);
```

## Discord Embed — Redesign

### Design-Prinzipien
- Discord = **Teaser** — nur Highlights, Neugier erzeugen
- Features ausgeschrieben, Fixes/Improvements nur als Zähler
- Expliziter CTA-Block vor dem Footer
- Buttons direkt am Embed (View), keine separate Nachricht
- Author-Feld mit Projekt-Icon für Wiedererkennung

### Embed-Struktur

```
[Author: ZERODOX + Projekt-Icon]               [Thumbnail: Logo]

🚀 v2.9.1 — Security & Performance Update      ← Titel → Link zur Changelog-Seite

> Neue Auth-Middleware und schnellere            ← TL;DR als Blockquote
> API-Responses für alle Nutzer

🆕 Neue Features                                ← Nur Top-Features ausgeschrieben
╰ OAuth2-Integration für Single Sign-On
╰ Dashboard Dark-Mode komplett überarbeitet

🐛 2 Bugfixes · ⚡ 3 Verbesserungen              ← Nur Zähler → Neugier

┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄
📖 Alle Details, technische Hintergründe        ← Expliziter CTA-Text
und die vollständige Änderungsliste
findest du im Changelog →

📊 v2.9.1 · 15 Commits · +320/-45       12:34  ← Footer

[👍 Gefällt mir] [⭐ Bewerten] [🔗 Changelog]   ← Buttons (discord.ui.View)
```

### Buttons
- `👍 Gefällt mir` (ButtonStyle.success) — Quick-Reaction, Zähler im Label
- `⭐ Bewerten` (ButtonStyle.secondary) — Öffnet Modal mit 1-5 Sterne + Textfeld
- `🔗 Changelog` (ButtonStyle.link) — URL-Button, öffnet Browser zur Changelog-Seite

### Feedback-System Entkopplung
Der `feedback_collector` wird unabhängig vom `ai_learning`-System initialisiert.
Aktuell scheitert die Initialisierung weil `ai_learning/continuous_learning_agent.py`
einen Import-Fehler hat und der gesamte Block in einem try/except liegt.

## Content Sanitizer (Security)

### Zweistufiger Filter

**Stufe 1 — Regex-Filter** (immer aktiv, läuft nach AI-Generierung):
- Dateipfade: `/home/...`, `src/...`, `~/...`, Windows-Pfade
- IP-Adressen: `\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}`
- Port-Nummern: `Port \d{4,5}`, `:\d{4,5}`
- Config-Referenzen: `config.yaml`, `.env`, `credentials`, `secrets`
- Interne URLs: `127.0.0.1`, `localhost`, `10.x.x.x`, `172.x.x.x`
- Server-Pfade und Usernamen

**Stufe 2 — AI-Prompt Security-Regeln:**
```
SICHERHEITSREGELN (STRIKT):
- NIEMALS Dateipfade, Server-Pfade oder Verzeichnisstrukturen
- NIEMALS IP-Adressen, Ports oder Netzwerk-Details
- Security-Fixes vage beschreiben: WAS verbessert, nicht WIE
- KEINE alten verwundbaren Dependency-Versionen nennen
- KEINE Config-Dateien oder deren Pfade
```

**Konfigurierbar pro Projekt:**
```yaml
projects:
  zerodox:
    patch_notes:
      security:
        sanitize: true
        redact_paths: true
        redact_ips: true
        vague_security_fixes: true
        custom_redact_patterns: []
```

## Changelog-Seite (shared-ui Komponenten)

### Komponenten

| Komponente | Props | Beschreibung |
|------------|-------|-------------|
| `<Changelog />` | `apiUrl, project, seoConfig?` | Liste mit Pagination + Filter |
| `<ChangelogDetail />` | `apiUrl, project, version` | Detail mit Stats + Changes |
| `<ChangelogBadge />` | `type` | Kategorie-Badge (feature/fix/improvement/breaking) |
| `<ChangelogStats />` | `stats` | Stats-Cards Grid |
| `<KeywordTags />` | `keywords` | SEO-Keyword-Tags (klickbar) |

### Listen-Seite Features
- Kategorie-Filter: Alle / Features / Fixes / Improvements
- Category Badges pro Eintrag
- SEO Keywords als Tags sichtbar
- Pagination mit Seitennummern

### Detail-Seite Features
- Hero-Bereich mit Version + Titel
- TL;DR als hervorgehobenes Blockquote
- Stats als Card-Grid (Commits, Dateien, Lines, Coverage)
- Strukturierte Changes nach Kategorie (aus `changes`-Array)
- Keyword-Tags am Ende
- Lesezeit-Anzeige

### Styling
- Nutzt bestehende `--ui-*` CSS-Variablen
- Glasmorphism Cards (konsistent mit bestehendem Design)
- Responsive (sm/md/lg Breakpoints)
- Dark-Mode ready

## SEO-Strategie

### Keywords
- **Basis-Keywords:** Pro Projekt konfigurierbar (`seo.base_keywords`)
- **Release-Keywords:** KI-generiert pro Release (neues Schema-Feld `seo_keywords`)
- **Fokus-Thema:** Pro Projekt setzbar (`seo.focus: "security"`)

### Structured Data (JSON-LD)
```json
{
  "@context": "https://schema.org",
  "@type": "TechArticle",
  "headline": "ZERODOX v2.9.1 — Security & Performance Update",
  "datePublished": "2026-03-11",
  "author": { "@type": "Organization", "name": "ZERODOX" },
  "about": {
    "@type": "SoftwareApplication",
    "name": "ZERODOX",
    "softwareVersion": "2.9.1"
  },
  "keywords": "oauth2, sso, api-performance"
}
```

### RSS/Atom Feed
- Endpoint: `GET /api/changelogs/feed?project=x&format=rss`
- Auto-Discovery: `<link rel="alternate" type="application/rss+xml">` im Head
- Ermöglicht Abonnement + automatische Suchmaschinen-Indexierung

### Sitemap
- Endpoint: `GET /api/changelogs/sitemap?project=x`
- Liefert XML-Sitemap-Fragment
- Projekte binden es in ihre `sitemap.xml` ein

### Semantisches HTML
- `<article>` mit `<time datetime="...">`
- `<section>` pro Kategorie
- Heading-Hierarchie: h1 → Version, h2 → Kategorie
- Canonical URLs pro Changelog-Eintrag

### Performance
- SSR/ISR statt Client-Side Fetch (wo möglich)
- Lesezeit-Berechnung aus Content-Länge
- Open Graph mit Projekt-Logo als og:image

## Migration

### ZERODOX
- Bestehende `changelogs`-Tabelle in ZERODOX-DB bleibt vorerst
- Neue Einträge gehen an die zentrale API
- Frontend migriert auf shared-ui `<Changelog />` Komponente
- ZERODOX-API-Routen werden zu Proxies oder entfallen

### GuildScout
- Noch keine Changelog-Seite vorhanden
- Bindet shared-ui `<Changelog />` ein wenn bereit

## Betroffene Dateien

### ShadowOps Bot (neu/geändert)
- `src/utils/health_server.py` — API-Endpoints erweitern
- `src/integrations/changelog_db.py` — Neue zentrale Changelog-DB Klasse
- `src/integrations/content_sanitizer.py` — Neuer Security-Filter
- `src/integrations/github_integration/notifications_mixin.py` — Embed Redesign
- `src/integrations/patch_notes_feedback.py` — Feedback-Buttons Redesign
- `src/integrations/patch_notes_web_exporter.py` — Migration auf zentrale API
- `src/schemas/patch_notes.json` — `seo_keywords` Feld hinzufügen
- `src/bot.py` — Feedback-Initialisierung entkoppeln
- `src/integrations/github_integration/ai_patch_notes_mixin.py` — Security-Prompt-Regeln

### shared-ui (neu)
- `src/components/Changelog/Changelog.tsx`
- `src/components/Changelog/ChangelogDetail.tsx`
- `src/components/Changelog/ChangelogBadge.tsx`
- `src/components/Changelog/ChangelogStats.tsx`
- `src/components/Changelog/KeywordTags.tsx`
- `src/components/Changelog/index.ts`
- `src/components/Changelog/types.ts`

### ZERODOX (geändert)
- `web/src/app/changelog/page.tsx` — Migration auf shared-ui
- `web/src/app/changelog/[version]/page.tsx` — Migration auf shared-ui
- `web/src/app/changelog/ChangelogClient.tsx` — Entfernen (ersetzt durch shared-ui)
- `web/src/app/changelog/[version]/ChangelogDetailClient.tsx` — Entfernen
