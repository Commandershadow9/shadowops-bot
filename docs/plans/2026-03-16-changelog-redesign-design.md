# Changelog Redesign — Design-Dokument

**Datum:** 2026-03-16
**Status:** Approved
**Scope:** shared-ui Changelog-Komponenten + GuildScout/ZERODOX Integration

## Ziel

Komplettes Redesign der Changelog-Seiten als wiederverwendbare shared-ui Komponenten.
Identischer Aufbau über alle Projekte, projektspezifisches Branding über CSS-Variablen.
Verbesserung von Design, SEO, Content-Darstellung und Navigation.

## Ist-Zustand

- shared-ui hat Basis-Changelog-Komponenten (funktional, aber Design-schwach)
- GuildScout hat eigene Timeline-Logik (dupliziert shared-ui teilweise)
- ZERODOX nutzt Wrapper-Pattern (sauber, aber Design-schwach)
- SEO-Daten werden generiert aber nicht voll genutzt (GuildScout ignoriert Keywords)
- Keine Hero-Sections, keine visuellen Stats, kein Sidebar-Layout
- Kein OG-Image Generator
- Markdown-Rendering ist rudimentär (kein Code, keine Tables, keine Links)

## Architektur-Entscheidung

**Ansatz A: shared-ui Komponenten-Redesign** (gewählt)

- Alle UI-Logik in shared-ui, Projekte haben nur ~10-Zeilen Wrapper
- Theming über CSS-Variablen (`--cl-*` Prefix)
- Hero-Bilder als optionale Props (ChatGPT-generierte Bilder vorhanden)
- OG-Images dynamisch generiert (Next.js ImageResponse)

Abgelehnte Ansätze:
- B) Projekt-spezifisches Redesign — zu viel Duplikation
- C) Separate Changelog-Website — zu viel Overhead

## Komponentenstruktur

```
shared-ui/components/Changelog/
├── ChangelogPage.tsx            # Komplette List-Seite
├── ChangelogDetailPage.tsx      # Komplette Detail-Seite
├── internal/
│   ├── ChangelogHero.tsx        # Hero mit Gradient, optionalem Bild, Stats-Counter
│   ├── ChangelogTimeline.tsx    # Timeline-Container mit Linie + Dots
│   ├── ChangelogCard.tsx        # Einzelne Release-Card (Glasmorphism)
│   ├── ChangelogDetailView.tsx  # Content + Sidebar Layout
│   ├── ChangelogBadge.tsx       # Überarbeitete Badges mit Glow
│   ├── ChangelogStats.tsx       # Stats als Mini-Balken-Visualisierung
│   ├── ChangelogSEO.tsx         # JSON-LD Generator (SSR-Hilfsfunktion)
│   ├── ChangelogBreadcrumbs.tsx # Breadcrumb-Navigation
│   ├── ChangelogMarkdown.tsx    # Verbesserter Markdown→JSX Parser
│   ├── ChangelogPagination.tsx  # Prev/Next + Seitenzahl
│   └── KeywordCloud.tsx         # Interaktive Keyword-Tags
├── og-image.tsx                 # OG-Image Generator Funktion
├── types.ts                     # TypeScript Interfaces (unverändert)
└── index.ts                     # Re-exports
```

Zusätzlich:
```
shared-ui/styles/changelog.css   # Changelog-spezifische CSS-Variablen + Animationen
```

## Theming

### CSS-Variablen (Defaults in shared-ui)

```css
:root {
  /* Hero */
  --cl-hero-gradient-from: var(--ui-primary);
  --cl-hero-gradient-to: var(--ui-secondary);
  --cl-hero-overlay: rgba(0, 0, 0, 0.6);

  /* Cards */
  --cl-card-bg: var(--ui-glass-bg);
  --cl-card-border: var(--ui-glass-border);
  --cl-card-hover-glow: var(--ui-primary-glow);
  --cl-card-hover-border: var(--ui-primary);

  /* Timeline */
  --cl-timeline-line: var(--ui-border);
  --cl-timeline-dot: var(--ui-primary);
  --cl-timeline-dot-glow: var(--ui-primary-glow);

  /* Badges */
  --cl-badge-feature: #10b981;
  --cl-badge-fix: #f59e0b;
  --cl-badge-improvement: #3b82f6;
  --cl-badge-breaking: #ef4444;
  --cl-badge-docs: #8b5cf6;

  /* Stats */
  --cl-stat-bar-positive: #10b981;
  --cl-stat-bar-negative: #ef4444;
  --cl-stat-bar-bg: rgba(255, 255, 255, 0.1);

  /* Misc */
  --cl-tldr-border: var(--ui-primary);
  --cl-keyword-bg: rgba(255, 255, 255, 0.05);
  --cl-keyword-border: var(--ui-primary-muted);
}
```

### GuildScout Overrides

```css
--cl-hero-gradient-from: var(--color-gold);
--cl-hero-gradient-to: var(--color-gold-dark);
--cl-timeline-dot: var(--color-gold-shimmer);
--cl-card-hover-glow: rgba(212, 175, 55, 0.3);
```

### ZERODOX Overrides

```css
--cl-hero-gradient-from: var(--primary);
--cl-hero-gradient-to: var(--secondary);
--cl-card-hover-glow: var(--primary-glow);
--cl-timeline-dot: var(--primary);
```

## Seiten-Design

### List-Seite (ChangelogPage)

**Hero-Bereich:**
- Animierter Gradient-Background (mesh-pattern)
- Optionales Hintergrundbild mit dunklem Overlay
- Optionaler Projekt-Logo Slot (ReactNode)
- Titel "Changelog" + Untertitel
- Animierte Stats-Counter (Releases, Features, Projekte)
- RSS + Sitemap Utility-Links

**Timeline:**
- Vertikale Linie mit animierten Dots pro Release
- Glasmorphism-Cards mit:
  - Version + Datum Header
  - Titel (H2)
  - TL;DR (line-clamp-2)
  - Change-Type Badges (Dot + Label, max 5)
  - Mini-Stats (3er Grid: Commits, Files, Lines)
  - "Mehr lesen" Link
- Neuestes Release: animiertes "Aktuell" Pulse-Badge
- Staggered Fade-In (IntersectionObserver, kein Dependency)
- Hover: Glow + translateY(-2px)

**Filter + Pagination:**
- Pill-Button Filter (Alle, Features, Bugfixes, Breaking)
- Kompakte Prev/Next Pagination mit Seitenzahl

**Responsive:**
- Desktop (≥1024px): Timeline links, Cards rechts
- Tablet (≥768px): Timeline zentriert, Cards volle Breite
- Mobile (<768px): Keine Timeline-Linie, Cards gestapelt, 2 Stats/Zeile

### Detail-Seite (ChangelogDetailPage)

**Header:**
- Subtilerer Hero (gleicher Gradient, weniger Höhe)
- Breadcrumbs (Changelog > Projekt > Version)
- Version + Datum + Titel
- TL;DR Box (Glass-Card, Primärfarbe left-border)

**2-Spalten Layout (Desktop):**

Content (65%):
- Gruppierte Changes nach Type (Breaking → Feature → Improvement → Fix → Docs)
- Jede Gruppe: Emoji-Header + Liste mit Details
- Vollständiger Markdown-Content (verbesserter Parser)
- Unterstützt: Headers, Bold, Listen, Code-Blöcke, Links, Inline-Code

Sidebar (35%):
- Stats als horizontale Balken (proportional, Primärfarbe)
- Contributors Liste
- Keyword-Cloud (interaktive Tags)
- Prev/Next Version Navigation
- Utility Links (RSS, JSON API)

**Footer:**
- Prev/Next Navigation (volle Breite, Titel-Preview)

**Responsive:**
- Mobile: Sidebar collapsed unter Content
- Stats werden horizontal scrollbar

## SEO

### Meta-Tags (ChangelogSEO Hilfsfunktion)

```html
<title>{version} — {title} | {project} Changelog</title>
<meta name="description" content="{tldr, max 160 chars}" />
<meta name="keywords" content="{seo_keywords joined}" />
<link rel="canonical" href="{base_url}/changelog/{slug}" />
<meta property="og:type" content="article" />
<meta property="og:image" content="/changelog/{slug}/opengraph-image" />
<meta name="twitter:card" content="summary_large_image" />
```

### JSON-LD Structured Data

```json
{
  "@context": "https://schema.org",
  "@type": "TechArticle",
  "headline": "{version} — {title}",
  "datePublished": "{published_at}",
  "description": "{tldr}",
  "author": { "@type": "Organization", "name": "{project}" },
  "about": {
    "@type": "SoftwareApplication",
    "name": "{project}",
    "softwareVersion": "{version}"
  },
  "keywords": "{seo_keywords}"
}
```

List-Seite zusätzlich: BreadcrumbList + CollectionPage Schema.

### OG-Image Generator

Funktion in shared-ui: `generateChangelogOGImage(config)`
- 1200x630px
- Projekt-Gradient als Background
- Logo, Version, Titel, Change-Type Summary, Datum
- Projekte nutzen in `opengraph-image.tsx`

### Spätere SEO/GEO-Erweiterungen (Phase 2, nicht in diesem Build)

- Marketing-Strategie für Suchmaschinen-Positionierung
- GEO (Generative Engine Optimization) für AI-Suchmaschinen
- Interlinking zwischen Projekt-Changelogs
- Blog-artige Zusammenfassungen für Major Releases
- Social Media Auto-Posts bei neuen Releases

## Hero-Bilder

Zwei ChatGPT-generierte Bilder vorhanden:

**GuildScout:** Dunkle Fantasy-Gildenhalle, warmes Goldlicht, gotische Architektur
- Quelle: `docs/ChatGPT Image 16. März 2026, 15_26_41.png`
- Ziel: `GuildScout/web/public/images/changelog-hero.webp`

**ZERODOX:** Abstrakte Cyber-Datenvisualisierung, fließende Cyan-Lichtströme
- Quelle: `docs/ChatGPT Image 16. März 2026, 15_27_20.png`
- Ziel: `ZERODOX/web/public/images/changelog-hero.webp`

Beide werden mit dunklem Gradient-Overlay eingebaut (Text-Lesbarkeit).

## Projekt-Integration (Wrapper-Pattern)

### GuildScout — ~10 Zeilen

```tsx
import { ChangelogPage } from '@cmdshadow/ui';

export default function Page() {
  return (
    <ChangelogPage
      project="guildscout"
      apiUrl={process.env.API_URL}
      heroImage="/images/changelog-hero.webp"
      linkBuilder={(v) => `/changelog/${v.replace(/\./g, '-')}`}
    />
  );
}
```

### ZERODOX — ~10 Zeilen

```tsx
import { ChangelogPage } from '@cmdshadow/ui';

export default function Page() {
  return (
    <ChangelogPage
      project="zerodox"
      apiUrl={process.env.NEXT_PUBLIC_CHANGELOG_API_URL}
      heroImage="/images/changelog-hero.webp"
      linkBuilder={(v) => `/changelog/${v.replace(/\./g, '-')}`}
    />
  );
}
```

## Props-Interface

```typescript
interface ChangelogPageProps {
  project: string;
  apiUrl: string;
  linkBuilder: (version: string) => string;
  heroImage?: string;
  heroOverlay?: boolean;         // default: true
  projectLogo?: React.ReactNode;
  title?: string;                // default: "Changelog"
  subtitle?: string;             // default: "Alle Updates und Änderungen"
  rssUrl?: string;               // default: auto-generated
  limit?: number;                // default: 10
}

interface ChangelogDetailPageProps {
  project: string;
  version: string;
  apiUrl: string;
  linkBuilder: (version: string) => string;
  heroImage?: string;
  backLink?: string;             // default: "../"
  projectName?: string;          // für Breadcrumbs/SEO
  baseUrl?: string;              // für canonical URLs
}
```

## Abhängigkeiten

- Keine neuen npm Dependencies
- Alles mit vorhandenen Tools: React, Tailwind CSS-Variablen, CSS Animations
- IntersectionObserver (nativ) für Scroll-Animationen
- requestAnimationFrame (nativ) für Counter-Animation
- Markdown-Parser bleibt inline (kein react-markdown)

## Implementierungsreihenfolge

1. shared-ui CSS (`changelog.css`) — Design-Tokens
2. shared-ui interne Komponenten (Hero, Card, Badge, Stats, Markdown, etc.)
3. shared-ui Hauptkomponenten (ChangelogPage, ChangelogDetailPage)
4. Hero-Bilder optimieren und in Projekte kopieren
5. GuildScout Integration (Wrapper + CSS-Overrides + OG-Image)
6. ZERODOX Integration (Wrapper + CSS-Overrides + OG-Image)
7. SEO-Komponente (JSON-LD, Meta-Tags)
8. Testen + Feinschliff
