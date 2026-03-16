# Changelog Redesign — Implementierungsplan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Komplettes Redesign der Changelog-Seiten als Premium shared-ui Komponenten mit projektspezifischem Theming, verbessertem SEO und Hero-Bildern.

**Architecture:** Neue Changelog-Komponenten in shared-ui (`~/libs/shared-ui/components/Changelog/`) ersetzen die bestehenden. CSS-Variablen (`--cl-*`) steuern das Theming. GuildScout und ZERODOX nutzen dünne Wrapper (~10 Zeilen) mit projekt-spezifischen Overrides. Hero-Bilder (ChatGPT-generiert) werden als optionale Props eingebunden.

**Tech Stack:** React 18, TypeScript, Tailwind CSS v4, CSS Custom Properties, Next.js App Router (ISR), aiohttp (ShadowOps API)

**Repos:** `~/libs/shared-ui/` (shared-ui), `~/GuildScout/web/` (GuildScout), `~/ZERODOX/web/` (ZERODOX)

**Design-Dokument:** `docs/plans/2026-03-16-changelog-redesign-design.md`

---

## Task 1: CSS Design-Tokens (`changelog.css`)

**Files:**
- Create: `~/libs/shared-ui/styles/changelog.css`
- Modify: `~/libs/shared-ui/styles/index.css` (add import)

**Step 1: CSS-Datei mit allen Changelog-Variablen erstellen**

```css
/* ~/libs/shared-ui/styles/changelog.css */

/* ── Changelog Design Tokens ─────────────────────────── */
:root {
  /* Hero */
  --cl-hero-gradient-from: var(--ui-primary);
  --cl-hero-gradient-to: var(--ui-secondary);
  --cl-hero-overlay: rgba(0, 0, 0, 0.6);
  --cl-hero-min-height: 320px;

  /* Cards */
  --cl-card-bg: var(--ui-glass-bg);
  --cl-card-border: var(--ui-glass-border);
  --cl-card-hover-glow: var(--ui-primary-glow);
  --cl-card-hover-border: color-mix(in srgb, var(--ui-primary) 40%, transparent);
  --cl-card-radius: 16px;

  /* Timeline */
  --cl-timeline-line: var(--ui-border);
  --cl-timeline-dot: var(--ui-primary);
  --cl-timeline-dot-glow: var(--ui-primary-glow);
  --cl-timeline-dot-size: 14px;

  /* Badges */
  --cl-badge-feature: #10b981;
  --cl-badge-fix: #f59e0b;
  --cl-badge-improvement: #3b82f6;
  --cl-badge-breaking: #ef4444;
  --cl-badge-docs: #8b5cf6;

  /* Stats */
  --cl-stat-bar-positive: #10b981;
  --cl-stat-bar-negative: #ef4444;
  --cl-stat-bar-bg: rgba(255, 255, 255, 0.08);

  /* Detail */
  --cl-tldr-border: var(--ui-primary);
  --cl-tldr-bg: rgba(255, 255, 255, 0.03);
  --cl-sidebar-bg: var(--ui-glass-bg);

  /* Keywords */
  --cl-keyword-bg: rgba(255, 255, 255, 0.05);
  --cl-keyword-border: color-mix(in srgb, var(--ui-primary) 30%, transparent);
  --cl-keyword-text: var(--ui-fg-muted);
}

/* ── Animations ──────────────────────────────────────── */
@keyframes cl-count-up {
  from { opacity: 0; transform: translateY(10px); }
  to { opacity: 1; transform: translateY(0); }
}

@keyframes cl-card-enter {
  from { opacity: 0; transform: translateY(24px); }
  to { opacity: 1; transform: translateY(0); }
}

@keyframes cl-pulse-badge {
  0%, 100% { opacity: 1; box-shadow: 0 0 0 0 var(--cl-timeline-dot-glow); }
  50% { opacity: 0.85; box-shadow: 0 0 0 8px transparent; }
}

@keyframes cl-stat-bar-fill {
  from { width: 0; }
}

.cl-animate-card-enter {
  animation: cl-card-enter 0.5s ease-out both;
}
.cl-animate-pulse-badge {
  animation: cl-pulse-badge 2s ease-in-out infinite;
}
.cl-animate-stat-bar {
  animation: cl-stat-bar-fill 0.8s ease-out both;
}

@media (prefers-reduced-motion: reduce) {
  .cl-animate-card-enter,
  .cl-animate-pulse-badge,
  .cl-animate-stat-bar {
    animation: none;
  }
}
```

**Step 2: Import in index.css hinzufügen**

In `~/libs/shared-ui/styles/index.css` — am Ende hinzufügen:
```css
@import './changelog.css';
```

**Step 3: Commit**

```bash
cd ~/libs/shared-ui
git add styles/changelog.css styles/index.css
git commit -m "feat: changelog design tokens und animationen"
```

---

## Task 2: Types erweitern + interne Komponenten-Grundstruktur

**Files:**
- Modify: `~/libs/shared-ui/components/Changelog/types.ts`
- Create: `~/libs/shared-ui/components/Changelog/internal/` (Verzeichnis)

**Step 1: Types um Page-Props erweitern**

Am Ende von `types.ts` hinzufügen:

```typescript
/** Props für die komplette Changelog List-Seite */
export interface ChangelogPageProps {
  project: string;
  apiUrl: string;
  linkBuilder: (version: string) => string;
  heroImage?: string;
  heroOverlay?: boolean;
  projectLogo?: React.ReactNode;
  title?: string;
  subtitle?: string;
  rssUrl?: string;
  limit?: number;
  className?: string;
}

/** Props für die komplette Changelog Detail-Seite */
export interface ChangelogDetailPageProps {
  project: string;
  version: string;
  apiUrl: string;
  linkBuilder: (version: string) => string;
  heroImage?: string;
  backLink?: string;
  projectName?: string;
  baseUrl?: string;
  className?: string;
}

/** Konfiguration für Badge-Darstellung */
export interface BadgeConfig {
  color: string;
  label: string;
  emoji: string;
}

/** Props für den Hero-Bereich */
export interface ChangelogHeroProps {
  title?: string;
  subtitle?: string;
  heroImage?: string;
  heroOverlay?: boolean;
  projectLogo?: React.ReactNode;
  totalReleases?: number;
  totalFeatures?: number;
  totalFixes?: number;
  rssUrl?: string;
  className?: string;
}
```

**Step 2: internal/ Verzeichnis anlegen**

```bash
mkdir -p ~/libs/shared-ui/components/Changelog/internal
```

**Step 3: Commit**

```bash
cd ~/libs/shared-ui
git add components/Changelog/types.ts components/Changelog/internal/
git commit -m "feat: changelog types erweitert + internal/ struktur"
```

---

## Task 3: ChangelogBadge Redesign

**Files:**
- Rewrite: `~/libs/shared-ui/components/Changelog/ChangelogBadge.tsx`

**Step 1: Badge mit Glow-Effekt und Emoji komplett neu schreiben**

Bestehende 32 Zeilen ersetzen durch neues Design:

```tsx
'use client';
import React from 'react';

const BADGE_CONFIG: Record<string, { color: string; label: string; labelEn: string; emoji: string }> = {
  feature:     { color: 'var(--cl-badge-feature)',     label: 'Feature',  labelEn: 'Feature',     emoji: '🆕' },
  fix:         { color: 'var(--cl-badge-fix)',         label: 'Bugfix',   labelEn: 'Bug Fix',     emoji: '🐛' },
  improvement: { color: 'var(--cl-badge-improvement)', label: 'Update',   labelEn: 'Improvement', emoji: '⚡' },
  breaking:    { color: 'var(--cl-badge-breaking)',    label: 'Breaking', labelEn: 'Breaking',    emoji: '⚠️' },
  docs:        { color: 'var(--cl-badge-docs)',        label: 'Docs',     labelEn: 'Docs',        emoji: '📝' },
};

interface ChangelogBadgeProps {
  type: string;
  compact?: boolean;
  language?: string;
  className?: string;
}

export function ChangelogBadge({ type, compact = false, language = 'de', className = '' }: ChangelogBadgeProps) {
  const config = BADGE_CONFIG[type] || BADGE_CONFIG.improvement;
  const label = language === 'de' ? config.label : config.labelEn;

  if (compact) {
    return (
      <span
        className={`inline-flex items-center gap-1 text-xs ${className}`}
        title={label}
      >
        <span
          className="w-2 h-2 rounded-full shrink-0"
          style={{
            backgroundColor: config.color,
            boxShadow: `0 0 6px ${config.color}`,
          }}
        />
      </span>
    );
  }

  return (
    <span
      className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium
        backdrop-blur-sm transition-all duration-200 hover:scale-105 ${className}`}
      style={{
        backgroundColor: `color-mix(in srgb, ${config.color} 15%, transparent)`,
        border: `1px solid color-mix(in srgb, ${config.color} 30%, transparent)`,
        color: config.color,
      }}
    >
      <span className="text-[10px]">{config.emoji}</span>
      {label}
    </span>
  );
}

export { BADGE_CONFIG };
```

**Step 2: Commit**

```bash
cd ~/libs/shared-ui
git add components/Changelog/ChangelogBadge.tsx
git commit -m "feat: changelog badge redesign mit glow und emoji"
```

---

## Task 4: ChangelogStats Redesign (Balken-Visualisierung)

**Files:**
- Rewrite: `~/libs/shared-ui/components/Changelog/ChangelogStats.tsx`

**Step 1: Stats als visuelle Balken statt nur Zahlen**

```tsx
'use client';
import React from 'react';
import type { ChangelogStats as StatsType } from './types';

interface ChangelogStatsProps {
  stats: StatsType;
  compact?: boolean;
  className?: string;
}

function StatBar({ value, max, color, label, prefix }: {
  value: number; max: number; color: string; label: string; prefix?: string;
}) {
  const pct = max > 0 ? Math.min((value / max) * 100, 100) : 0;
  return (
    <div className="space-y-1">
      <div className="flex justify-between items-baseline text-xs">
        <span className="text-white/50">{label}</span>
        <span className="font-mono tabular-nums" style={{ color }}>
          {prefix}{value.toLocaleString('de-DE')}
        </span>
      </div>
      <div className="h-1.5 rounded-full overflow-hidden" style={{ backgroundColor: 'var(--cl-stat-bar-bg)' }}>
        <div
          className="h-full rounded-full cl-animate-stat-bar"
          style={{ width: `${pct}%`, backgroundColor: color }}
        />
      </div>
    </div>
  );
}

export function ChangelogStats({ stats, compact = false, className = '' }: ChangelogStatsProps) {
  if (!stats) return null;

  const maxVal = Math.max(
    stats.commits || 0,
    stats.files_changed || 0,
    stats.lines_added || 0,
    stats.lines_removed || 0,
    1,
  );

  if (compact) {
    return (
      <div className={`flex items-center gap-3 text-xs text-white/40 ${className}`}>
        {stats.commits != null && (
          <span className="font-mono tabular-nums">{stats.commits} Commits</span>
        )}
        {stats.files_changed != null && (
          <span className="font-mono tabular-nums">{stats.files_changed} Dateien</span>
        )}
        {stats.lines_added != null && (
          <span className="font-mono tabular-nums text-emerald-400/60">+{stats.lines_added}</span>
        )}
        {stats.lines_removed != null && stats.lines_removed > 0 && (
          <span className="font-mono tabular-nums text-red-400/60">-{stats.lines_removed}</span>
        )}
      </div>
    );
  }

  return (
    <div className={`space-y-3 ${className}`}>
      {stats.commits != null && stats.commits > 0 && (
        <StatBar value={stats.commits} max={maxVal} color="var(--ui-primary)" label="Commits" />
      )}
      {stats.files_changed != null && stats.files_changed > 0 && (
        <StatBar value={stats.files_changed} max={maxVal} color="var(--ui-secondary, #F4B24D)" label="Dateien" />
      )}
      {stats.lines_added != null && stats.lines_added > 0 && (
        <StatBar value={stats.lines_added} max={maxVal} color="var(--cl-stat-bar-positive)" label="Hinzugefügt" prefix="+" />
      )}
      {stats.lines_removed != null && stats.lines_removed > 0 && (
        <StatBar value={stats.lines_removed} max={maxVal} color="var(--cl-stat-bar-negative)" label="Entfernt" prefix="-" />
      )}
      {stats.coverage_percent != null && (
        <StatBar value={stats.coverage_percent} max={100} color="var(--ui-primary)" label="Coverage" prefix="" />
      )}
      {stats.contributors && stats.contributors.length > 0 && (
        <div className="pt-2 border-t border-white/5">
          <span className="text-xs text-white/40">
            {stats.contributors.length} Contributor{stats.contributors.length > 1 ? 's' : ''}: {stats.contributors.join(', ')}
          </span>
        </div>
      )}
    </div>
  );
}
```

**Step 2: Commit**

```bash
cd ~/libs/shared-ui
git add components/Changelog/ChangelogStats.tsx
git commit -m "feat: changelog stats als balken-visualisierung"
```

---

## Task 5: KeywordCloud Redesign

**Files:**
- Rewrite: `~/libs/shared-ui/components/Changelog/KeywordTags.tsx`

**Step 1: Interaktive Keyword-Cloud**

```tsx
'use client';
import React from 'react';

interface KeywordCloudProps {
  keywords: string[];
  className?: string;
}

export function KeywordCloud({ keywords, className = '' }: KeywordCloudProps) {
  if (!keywords || keywords.length === 0) return null;

  return (
    <div className={`space-y-2 ${className}`}>
      <h4 className="text-xs font-medium text-white/40 uppercase tracking-wider">Keywords</h4>
      <div className="flex flex-wrap gap-1.5">
        {keywords.map((kw) => (
          <span
            key={kw}
            className="px-2.5 py-1 text-xs rounded-md transition-all duration-200
              hover:scale-105 cursor-default"
            style={{
              backgroundColor: 'var(--cl-keyword-bg)',
              border: '1px solid var(--cl-keyword-border)',
              color: 'var(--cl-keyword-text)',
            }}
          >
            {kw}
          </span>
        ))}
      </div>
    </div>
  );
}
```

**Step 2: Commit**

```bash
cd ~/libs/shared-ui
git add components/Changelog/KeywordTags.tsx
git commit -m "feat: keyword cloud redesign"
```

---

## Task 6: ChangelogHero (neu)

**Files:**
- Create: `~/libs/shared-ui/components/Changelog/internal/ChangelogHero.tsx`

**Step 1: Hero-Komponente mit Gradient, Bild-Slot, Counter**

```tsx
'use client';
import React, { useEffect, useRef, useState } from 'react';
import type { ChangelogHeroProps } from '../types';

function AnimatedCounter({ target, label }: { target: number; label: string }) {
  const [count, setCount] = useState(0);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (target <= 0) return;
    const duration = 1200;
    const startTime = performance.now();
    let rafId: number;

    function step(now: number) {
      const elapsed = now - startTime;
      const progress = Math.min(elapsed / duration, 1);
      const eased = 1 - Math.pow(1 - progress, 3); // ease-out cubic
      setCount(Math.round(eased * target));
      if (progress < 1) rafId = requestAnimationFrame(step);
    }
    rafId = requestAnimationFrame(step);
    return () => cancelAnimationFrame(rafId);
  }, [target]);

  return (
    <div className="text-center">
      <div className="text-2xl sm:text-3xl font-bold font-mono tabular-nums" style={{ color: 'var(--ui-primary)' }}>
        {count}
      </div>
      <div className="text-xs text-white/40 mt-1">{label}</div>
    </div>
  );
}

export function ChangelogHero({
  title = 'Changelog',
  subtitle = 'Alle Updates und Änderungen',
  heroImage,
  heroOverlay = true,
  projectLogo,
  totalReleases = 0,
  totalFeatures = 0,
  totalFixes = 0,
  rssUrl,
  className = '',
}: ChangelogHeroProps) {
  return (
    <div
      className={`relative overflow-hidden rounded-2xl mb-10 ${className}`}
      style={{ minHeight: 'var(--cl-hero-min-height, 320px)' }}
    >
      {/* Gradient Background */}
      <div
        className="absolute inset-0"
        style={{
          background: `linear-gradient(135deg, var(--cl-hero-gradient-from) 0%, var(--cl-hero-gradient-to) 50%, var(--ui-bg, #0a0a0a) 100%)`,
          opacity: 0.15,
        }}
      />

      {/* Mesh Pattern */}
      <div
        className="absolute inset-0 opacity-[0.03]"
        style={{
          backgroundImage: `radial-gradient(circle at 1px 1px, white 1px, transparent 0)`,
          backgroundSize: '32px 32px',
        }}
      />

      {/* Optional Hero Image */}
      {heroImage && (
        <>
          <img
            src={heroImage}
            alt=""
            className="absolute inset-0 w-full h-full object-cover"
            loading="eager"
          />
          {heroOverlay && (
            <div
              className="absolute inset-0"
              style={{ background: `linear-gradient(to bottom, var(--cl-hero-overlay) 0%, var(--ui-bg, #0a0a0a) 100%)` }}
            />
          )}
        </>
      )}

      {/* Content */}
      <div className="relative z-10 flex flex-col items-center justify-center px-6 py-16 sm:py-20 text-center">
        {projectLogo && (
          <div className="mb-6 opacity-90">{projectLogo}</div>
        )}

        <h1 className="text-3xl sm:text-4xl lg:text-5xl font-bold tracking-tight text-white mb-3">
          {title}
        </h1>
        <p className="text-base sm:text-lg text-white/50 max-w-md mb-10">
          {subtitle}
        </p>

        {/* Stats Counter */}
        {(totalReleases > 0 || totalFeatures > 0 || totalFixes > 0) && (
          <div className="flex gap-8 sm:gap-12 mb-8">
            {totalReleases > 0 && <AnimatedCounter target={totalReleases} label="Releases" />}
            {totalFeatures > 0 && <AnimatedCounter target={totalFeatures} label="Features" />}
            {totalFixes > 0 && <AnimatedCounter target={totalFixes} label="Bugfixes" />}
          </div>
        )}

        {/* Utility Links */}
        {rssUrl && (
          <div className="flex gap-4 text-xs text-white/30">
            <a href={rssUrl} className="hover:text-white/60 transition-colors flex items-center gap-1">
              <svg className="w-3.5 h-3.5" fill="currentColor" viewBox="0 0 24 24"><path d="M6.18 15.64a2.18 2.18 0 110 4.36 2.18 2.18 0 010-4.36zM4 4.44A15.56 15.56 0 0119.56 20h-2.83A12.73 12.73 0 004 7.27V4.44zm0 5.66a9.9 9.9 0 019.9 9.9h-2.83A7.07 7.07 0 004 12.93V10.1z"/></svg>
              RSS Feed
            </a>
          </div>
        )}
      </div>
    </div>
  );
}
```

**Step 2: Commit**

```bash
cd ~/libs/shared-ui
git add components/Changelog/internal/ChangelogHero.tsx
git commit -m "feat: changelog hero mit gradient, bild-slot und counter"
```

---

## Task 7: ChangelogCard (neu)

**Files:**
- Create: `~/libs/shared-ui/components/Changelog/internal/ChangelogCard.tsx`

**Step 1: Glasmorphism Release-Card**

Card-Komponente mit: Glasmorphism, Hover-Glow, Badges, Mini-Stats, Aktuell-Badge, staggered Animation.
Verwendet ChangelogBadge (compact) und ChangelogStats (compact).
~120 Zeilen.

**Step 2: Commit**

```bash
cd ~/libs/shared-ui
git add components/Changelog/internal/ChangelogCard.tsx
git commit -m "feat: changelog card mit glasmorphism und hover-effekten"
```

---

## Task 8: ChangelogTimeline (neu)

**Files:**
- Create: `~/libs/shared-ui/components/Changelog/internal/ChangelogTimeline.tsx`

**Step 1: Timeline-Container**

Vertikale Timeline-Linie mit Dots, IntersectionObserver für staggered Fade-In.
Rendert ChangelogCard pro Entry.
Desktop: Linie links, Cards rechts. Mobile: keine Linie, Cards gestapelt.
~80 Zeilen.

**Step 2: Commit**

```bash
cd ~/libs/shared-ui
git add components/Changelog/internal/ChangelogTimeline.tsx
git commit -m "feat: changelog timeline mit intersection observer"
```

---

## Task 9: ChangelogMarkdown (neu)

**Files:**
- Create: `~/libs/shared-ui/components/Changelog/internal/ChangelogMarkdown.tsx`

**Step 1: Verbesserter Markdown→JSX Parser**

Unterstützt: ## Headers, **bold**, *italic*, `inline code`, ```code blocks```, - Listen, Links, Trennlinien.
Kein externer Dependency — reines Regex-basiertes Parsing.
Ersetzt den rudimentären Parser in ChangelogDetail.tsx.
~100 Zeilen.

**Step 2: Commit**

```bash
cd ~/libs/shared-ui
git add components/Changelog/internal/ChangelogMarkdown.tsx
git commit -m "feat: verbesserter changelog markdown parser"
```

---

## Task 10: ChangelogPage (Hauptkomponente — ersetzt Changelog.tsx)

**Files:**
- Rewrite: `~/libs/shared-ui/components/Changelog/Changelog.tsx` → `ChangelogPage.tsx` (rename + rewrite)

**Step 1: ChangelogPage als Komplettseite**

Kombiniert: Hero, Filter-Tabs, Timeline, Pagination.
Fetcht Daten via API, berechnet Hero-Stats (Totals über alle Entries).
Props: `ChangelogPageProps` (aus types.ts).
~180 Zeilen.

**WICHTIG:** Die alte `Changelog.tsx` wird durch `ChangelogPage.tsx` ersetzt.
Beide exportieren — Changelog (deprecated alias) + ChangelogPage (neu).

**Step 2: Commit**

```bash
cd ~/libs/shared-ui
git add components/Changelog/
git commit -m "feat: changelog page komplett-redesign (hero, timeline, cards)"
```

---

## Task 11: ChangelogDetailPage (Hauptkomponente — ersetzt ChangelogDetail.tsx)

**Files:**
- Rewrite: `~/libs/shared-ui/components/Changelog/ChangelogDetail.tsx` → `ChangelogDetailPage.tsx`

**Step 1: Detail-Seite mit 2-Spalten Layout**

Content (65%): Grouped Changes + Markdown Content.
Sidebar (35%): Stats-Balken, Contributors, Keywords, Prev/Next Navigation.
TL;DR Box mit left-border accent.
Breadcrumbs.
~220 Zeilen.

**WICHTIG:** Die alte `ChangelogDetail.tsx` wird durch `ChangelogDetailPage.tsx` ersetzt.
Beide exportieren — ChangelogDetail (deprecated alias) + ChangelogDetailPage (neu).

**Step 2: Commit**

```bash
cd ~/libs/shared-ui
git add components/Changelog/
git commit -m "feat: changelog detail page mit sidebar und stats-balken"
```

---

## Task 12: Index-Exports aktualisieren

**Files:**
- Rewrite: `~/libs/shared-ui/components/Changelog/index.ts`

**Step 1: Alle neuen Komponenten exportieren**

```typescript
// Hauptkomponenten (für Projekte)
export { ChangelogPage } from './ChangelogPage';
export { ChangelogDetailPage } from './ChangelogDetailPage';

// Einzelne Komponenten (für Custom-Layouts)
export { ChangelogBadge, BADGE_CONFIG } from './ChangelogBadge';
export { ChangelogStats } from './ChangelogStats';
export { KeywordCloud } from './KeywordTags';
export { ChangelogHero } from './internal/ChangelogHero';
export { ChangelogCard } from './internal/ChangelogCard';
export { ChangelogTimeline } from './internal/ChangelogTimeline';
export { ChangelogMarkdown } from './internal/ChangelogMarkdown';

// Deprecated Aliases (Rückwärtskompatibilität bis nächstes Major)
export { ChangelogPage as Changelog } from './ChangelogPage';
export { ChangelogDetailPage as ChangelogDetail } from './ChangelogDetailPage';

// Types
export type {
  ChangelogEntry,
  ChangelogChange,
  ChangelogStats as ChangelogStatsType,
  ChangelogListResponse,
  ChangelogSEOConfig,
  ChangelogPageProps,
  ChangelogDetailPageProps,
  ChangelogHeroProps,
  BadgeConfig,
} from './types';
```

**Step 2: Commit**

```bash
cd ~/libs/shared-ui
git add components/Changelog/index.ts
git commit -m "feat: changelog exports aktualisiert mit deprecated aliases"
```

---

## Task 13: Hero-Bilder optimieren und kopieren

**Files:**
- Source: `~/shadowops-bot/docs/ChatGPT Image 16. März 2026, 15_26_41.png` (GuildScout)
- Source: `~/shadowops-bot/docs/ChatGPT Image 16. März 2026, 15_27_20.png` (ZERODOX)
- Target: `~/GuildScout/web/public/images/changelog-hero.webp`
- Target: `~/ZERODOX/web/public/images/changelog-hero.webp`

**Step 1: Prüfen ob ImageMagick/cwebp verfügbar**

```bash
which convert || which cwebp || echo "weder ImageMagick noch cwebp vorhanden"
```

Falls keines vorhanden: PNG direkt kopieren (als .png statt .webp).

**Step 2: Bilder konvertieren und kopieren**

```bash
# GuildScout
mkdir -p ~/GuildScout/web/public/images/
convert ~/shadowops-bot/docs/"ChatGPT Image 16. März 2026, 15_26_41.png" \
  -resize 1920x600^ -gravity center -extent 1920x600 \
  -quality 85 ~/GuildScout/web/public/images/changelog-hero.webp

# ZERODOX
mkdir -p ~/ZERODOX/web/public/images/
convert ~/shadowops-bot/docs/"ChatGPT Image 16. März 2026, 15_27_20.png" \
  -resize 1920x600^ -gravity center -extent 1920x600 \
  -quality 85 ~/ZERODOX/web/public/images/changelog-hero.webp
```

Falls convert nicht vorhanden → PNG direkt kopieren:
```bash
cp ~/shadowops-bot/docs/"ChatGPT Image 16. März 2026, 15_26_41.png" \
   ~/GuildScout/web/public/images/changelog-hero.png
cp ~/shadowops-bot/docs/"ChatGPT Image 16. März 2026, 15_27_20.png" \
   ~/ZERODOX/web/public/images/changelog-hero.png
```

**Step 3: Commit in beiden Repos**

```bash
cd ~/GuildScout && git add web/public/images/changelog-hero.* && \
  git commit -m "feat: changelog hero hintergrundbild"
cd ~/ZERODOX && git add web/public/images/changelog-hero.* && \
  git commit -m "feat: changelog hero hintergrundbild"
```

---

## Task 14: GuildScout Integration

**Files:**
- Rewrite: `~/GuildScout/web/src/app/(public)/changelog/page.tsx`
- Rewrite: `~/GuildScout/web/src/app/(public)/changelog/[version]/page.tsx`

**Step 1: List-Page auf ChangelogPage umstellen**

Die bestehenden 210 Zeilen ersetzen durch ~40 Zeilen Wrapper:
- Server-Component mit ISR (revalidate 300)
- generateMetadata() für SEO (Titel, Description, JSON-LD)
- ChangelogPage mit GuildScout-spezifischen Props
- heroImage: `/images/changelog-hero.webp` (oder .png)

**Step 2: Detail-Page auf ChangelogDetailPage umstellen**

Die bestehenden 245 Zeilen ersetzen durch ~60 Zeilen Wrapper:
- Server-Component mit ISR (revalidate 300)
- generateMetadata() mit React.cache() Dedup (bestehende Logik behalten)
- generateStaticParams() (bestehende Logik behalten)
- ChangelogDetailPage mit GuildScout Props

**Step 3: CSS-Overrides in globals.css hinzufügen**

Am Ende von `~/GuildScout/web/src/styles/globals.css`:

```css
/* Changelog Theme Overrides */
:root {
  --cl-hero-gradient-from: var(--color-gold, #d4af37);
  --cl-hero-gradient-to: var(--color-gold-dark, #b8941f);
  --cl-timeline-dot: var(--color-gold-shimmer, #f0c850);
  --cl-timeline-dot-glow: rgba(212, 175, 55, 0.4);
  --cl-card-hover-glow: rgba(212, 175, 55, 0.2);
  --cl-card-hover-border: rgba(212, 175, 55, 0.3);
  --cl-tldr-border: var(--color-gold, #d4af37);
}
```

**Step 4: Commit**

```bash
cd ~/GuildScout
git add web/src/app/\(public\)/changelog/ web/src/styles/globals.css
git commit -m "feat: changelog pages auf shared-ui redesign umgestellt"
```

---

## Task 15: ZERODOX Integration

**Files:**
- Modify: `~/ZERODOX/web/src/app/changelog/page.tsx`
- Modify: `~/ZERODOX/web/src/app/changelog/[version]/page.tsx`
- Simplify: `~/ZERODOX/web/src/app/changelog/ChangelogWrapper.tsx`
- Simplify: `~/ZERODOX/web/src/app/changelog/[version]/ChangelogDetailWrapper.tsx`

**Step 1: Wrappers auf neue Komponenten umstellen**

ChangelogWrapper.tsx: `Changelog` → `ChangelogPage`, heroImage hinzufügen.
ChangelogDetailWrapper.tsx: `ChangelogDetail` → `ChangelogDetailPage`, Props erweitern.

**Step 2: CSS-Overrides in globals.css hinzufügen**

Am Ende von `~/ZERODOX/web/src/app/globals.css`:

```css
/* Changelog Theme Overrides */
:root {
  --cl-hero-gradient-from: var(--primary, #00D1E8);
  --cl-hero-gradient-to: var(--secondary, #F4B24D);
  --cl-timeline-dot: var(--primary, #00D1E8);
  --cl-timeline-dot-glow: rgba(0, 209, 232, 0.4);
  --cl-card-hover-glow: rgba(0, 209, 232, 0.15);
  --cl-card-hover-border: rgba(0, 209, 232, 0.3);
  --cl-tldr-border: var(--primary, #00D1E8);
}
```

**Step 3: Commit**

```bash
cd ~/ZERODOX
git add web/src/app/changelog/ web/src/app/globals.css
git commit -m "feat: changelog pages auf shared-ui redesign umgestellt"
```

---

## Task 16: OG-Image Generator für Changelogs

**Files:**
- Create: `~/GuildScout/web/src/app/(public)/changelog/[version]/opengraph-image.tsx`
- Create: `~/ZERODOX/web/src/app/changelog/[version]/opengraph-image.tsx`

**Step 1: GuildScout OG-Image**

Next.js ImageResponse (edge runtime) mit Gold-Gradient, Version, Titel, Badge-Summary.
Angelehnt an bestehende `~/GuildScout/web/src/app/opengraph-image.tsx` (134 Zeilen).
~80 Zeilen.

**Step 2: ZERODOX OG-Image**

Gleiche Struktur, Cyan-Theme.
Angelehnt an bestehende `~/ZERODOX/web/src/app/opengraph-image.tsx` (142 Zeilen).
~80 Zeilen.

**Step 3: Commit in beiden Repos**

```bash
cd ~/GuildScout && git add web/src/app/\(public\)/changelog/\[version\]/opengraph-image.tsx && \
  git commit -m "feat: dynamische og-images für changelog releases"
cd ~/ZERODOX && git add web/src/app/changelog/\[version\]/opengraph-image.tsx && \
  git commit -m "feat: dynamische og-images für changelog releases"
```

---

## Task 17: Testen

**Step 1: shared-ui Build prüfen**

```bash
cd ~/libs/shared-ui
npx tsc --noEmit
```

Expected: Keine TypeScript-Fehler.

**Step 2: GuildScout Dev-Server starten und changelog testen**

```bash
cd ~/GuildScout/web
NODE_OPTIONS="--max-old-space-size=2048" npx next build 2>&1 | tail -20
```

Expected: Build erfolgreich, keine Fehler.
Dann manuell prüfen: `curl -s http://localhost:3000/changelog | head -50` (falls Dev-Server läuft).

**Step 3: ZERODOX Dev-Server prüfen**

```bash
cd ~/ZERODOX/web
NODE_OPTIONS="--max-old-space-size=2048" npx next build 2>&1 | tail -20
```

Expected: Build erfolgreich, keine Fehler.

**ACHTUNG:** Builds EINZELN ausführen (8 GB VPS, OOM-Gefahr). NIEMALS parallel!

**Step 4: Changelog API Verfügbarkeit prüfen**

```bash
curl -s "http://localhost:8766/api/changelogs?project=guildscout&limit=1" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'OK: {d[\"meta\"][\"total\"]} entries')" 2>/dev/null || echo "API nicht erreichbar"
```

---

## Task 18: Feinschliff und Cleanup

**Step 1: Alte Dateien aufräumen**

Falls `Changelog.tsx` und `ChangelogDetail.tsx` noch separat existieren (neben den neuen ChangelogPage/ChangelogDetailPage), löschen und nur die neuen behalten. Deprecated Aliases in index.ts beibehalten.

**Step 2: CLAUDE.md in shared-ui aktualisieren**

Changelog-Komponenten-Dokumentation in `~/libs/shared-ui/CLAUDE.md` aktualisieren — neue Komponenten, Props, CSS-Variablen.

**Step 3: Final Commit + Tag**

```bash
cd ~/libs/shared-ui
git add -A
git commit -m "chore: changelog redesign cleanup und doku"
git tag -a v0.2.0 -m "feat: changelog redesign"
```

---

## Zusammenfassung

| Task | Repo | Scope | ~Zeilen |
|------|------|-------|---------|
| 1 | shared-ui | CSS Design-Tokens | 70 |
| 2 | shared-ui | Types erweitern | 50 |
| 3 | shared-ui | Badge Redesign | 55 |
| 4 | shared-ui | Stats Redesign | 90 |
| 5 | shared-ui | KeywordCloud | 30 |
| 6 | shared-ui | Hero (neu) | 130 |
| 7 | shared-ui | Card (neu) | 120 |
| 8 | shared-ui | Timeline (neu) | 80 |
| 9 | shared-ui | Markdown (neu) | 100 |
| 10 | shared-ui | ChangelogPage | 180 |
| 11 | shared-ui | ChangelogDetailPage | 220 |
| 12 | shared-ui | Index-Exports | 30 |
| 13 | GS + ZD | Hero-Bilder | — |
| 14 | GuildScout | Integration | 100 |
| 15 | ZERODOX | Integration | 60 |
| 16 | GS + ZD | OG-Images | 160 |
| 17 | alle | Testen | — |
| 18 | shared-ui | Cleanup | — |

**Gesamt: ~1.475 neue Zeilen** (ersetzt ~944 bestehende Zeilen)
