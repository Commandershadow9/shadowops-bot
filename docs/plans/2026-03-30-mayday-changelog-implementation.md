# MayDay Sim Changelog — Implementierungsplan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Dynamische Changelog-Seite für MayDay Sim im "Einsatzprotokoll"-Stil mit einheitlichem Daten-Flow über die zentrale ShadowOps Bot API, erweiterten Gaming-Badges und Discord-Teaser-Modus.

**Architecture:** ShadowOps Bot (Port 8766, SQLite ChangelogDB) → MayDay Web Next.js API-Proxy → SSR Frontend-Pages. Discord bekommt Teaser-Version mit Cliffhanger + Link. 9 Gaming-spezifische Badge-Types ersetzen die generischen 5 für Gaming-Projekte.

**Tech Stack:** Python 3.12 (ShadowOps Bot), Next.js 16 + React 19 + Tailwind 4 + Framer Motion (MayDay Web), next/og (OG-Images)

**Design-Doc:** `docs/plans/2026-03-30-mayday-changelog-design.md`

---

## Phase 1: ShadowOps Bot — Backend-Änderungen

### Task 1: CORS + Config für MayDay Sim

**Files:**
- Modify: `src/utils/health_server.py:22-30` (CORS)
- Modify: `config/config.yaml:289-307` (MayDay patch_notes)

**Step 1: CORS erweitern**

In `src/utils/health_server.py`, `CORS_ALLOWED_ORIGINS` Set erweitern:

```python
CORS_ALLOWED_ORIGINS: set[str] = {
    'https://guildscout.eu',
    'https://guildscout.de',
    'https://guildscout.zerodox.de',
    'https://zerodox.de',
    'https://www.zerodox.de',
    'https://maydaysim.de',
    'https://www.maydaysim.de',
    'http://localhost:3000',
    'http://localhost:3001',
    'http://localhost:3200',
}
```

**Step 2: Config changelog_url setzen**

In `config/config.yaml` unter `mayday_sim.patch_notes`:

```yaml
patch_notes:
  enabled: true
  language: de
  use_ai: true
  use_critical_model: false
  preferred_variant: gaming_community_v2
  release_mode: daily
  daily_release_hour: 22
  daily_min_commits: 3
  changelog_url: https://maydaysim.de/changelog
  project_description: "MayDay Sim — Realistische Leitstellen-Simulation..."
  target_audience: "Gamer, BOS-Enthusiasten und Blaulicht-Fans..."
  batch_threshold: 8
  emergency_threshold: 20
  cron_day: sunday
  cron_hour: 20
  cron_min_commits: 3
```

**Step 3: Bot neustarten und prüfen**

Run: `sudo systemctl restart shadowops-bot && sleep 10 && journalctl -u shadowops-bot --since "1 min ago" --no-pager | grep -i "mayday\|cors\|changelog"`
Expected: Bot startet, mayday_sim Config geladen

**Step 4: Commit**

```bash
git add src/utils/health_server.py
git commit -m "feat: CORS + changelog_url für MayDay Sim Changelog"
```

---

### Task 2: Patch Notes Schema — Gaming Badge-Types erweitern

**Files:**
- Modify: `src/schemas/patch_notes.json` (change.type enum erweitern)

**Step 1: Schema erweitern**

In `src/schemas/patch_notes.json`, das `type` enum im `changes` Array erweitern:

```json
"type": {
  "type": "string",
  "enum": ["feature", "content", "gameplay", "design", "performance", "multiplayer", "fix", "breaking", "infrastructure", "improvement", "docs", "security"]
}
```

Die neuen Types (`content`, `gameplay`, `design`, `performance`, `multiplayer`, `infrastructure`) kommen zusätzlich zu den bestehenden. Bestehende Projekte nutzen weiterhin die alten Types — die KI wählt basierend auf dem Prompt.

**Step 2: Commit**

```bash
git add src/schemas/patch_notes.json
git commit -m "feat: erweiterte Gaming Badge-Types im Patch Notes Schema"
```

---

### Task 3: Gaming Community v2 Template — Teaser + Badge-Types

**Files:**
- Modify: `src/integrations/prompt_ab_testing.py:538-644` (Template erweitern)

**Step 1: Template um Discord-Teaser und Gaming-Types erweitern**

In `_get_gaming_community_v2_template_de()` den Kategorie-Block ersetzen. Die bestehenden Kategorien (🆕 Neuer Content, 🎨 Design & Look, etc.) werden beibehalten, aber das Schema wird um die neuen `type`-Werte erweitert.

Am ENDE des Templates (vor `{stats_line}`) einfügen:

```
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
```

Zusätzlich den Teaser-Block hinzufügen:

```
═══════════════════════════════════════
DISCORD-TEASER (PFLICHT wenn changelog_url vorhanden):
═══════════════════════════════════════

Generiere ein zusätzliches Feld "discord_teaser" mit max 1000 Zeichen:

FORMAT:
🚨 [Projekt] — v[Version]

[2-3 Hype-Sätze die erzählen was sich verändert hat — als würdest du einem Kumpel davon erzählen]

[Emoji] [Highlight 1 — 1 packender Satz]
[Emoji] [Highlight 2 — 1 packender Satz]
[Emoji] [Highlight 3 — 1 packender Satz]
[Emoji] [Highlight 4 — 1 packender Satz]
[Emoji] [Highlight 5 — 1 packender Satz]
[Emoji] [Highlight 6 — 1 packender Satz]

[Cliffhanger: "Aber das war noch nicht alles..." oder ähnlich]

EMOJIS pro Type:
🔵 feature, 🗺️ content, 🎮 gameplay, 🎨 design, ⚡ performance, 👥 multiplayer, 🔴 fix, ⚠️ breaking, 🛡️ infrastructure
```

**Step 2: Schema um discord_teaser erweitern**

In `src/schemas/patch_notes.json` das neue Feld hinzufügen:

```json
"discord_teaser": {
  "type": "string",
  "description": "Kurzer Discord-Teaser mit Cliffhanger (max 1000 Zeichen)"
}
```

Und in `required` Array aufnehmen.

**Step 3: Commit**

```bash
git add src/integrations/prompt_ab_testing.py src/schemas/patch_notes.json
git commit -m "feat: Gaming Badge-Types + Discord-Teaser im gaming_community_v2 Template"
```

---

### Task 4: Discord Teaser-Modus in Notifications

**Files:**
- Modify: `src/integrations/github_integration/notifications_mixin.py:620-660` (Teaser nutzen)

**Step 1: Teaser aus AI-Result extrahieren**

In `_build_embed()` (ca. Zeile 647), wenn `changelog_link` gesetzt ist UND `ai_result` ein `discord_teaser` Feld hat, dieses statt der vollen Description verwenden:

```python
# Nach Zeile 647 (is_discord_only = not changelog_link)
if not is_discord_only and isinstance(ai_result, dict) and ai_result.get('discord_teaser'):
    # Teaser-Modus: Kurze Discord-Version + Link
    description = ai_result['discord_teaser']
else:
    description = self._build_description(ai_result, commits, language, discord_only=is_discord_only)
```

Der Rest der Methode (changelog_link Anhang ab Zeile 651) bleibt — er hängt den "Alle Details" Link an.

**Step 2: Commit**

```bash
git add src/integrations/github_integration/notifications_mixin.py
git commit -m "feat: Discord Teaser-Modus für Projekte mit changelog_url"
```

---

### Task 5: Bot neustarten und testen

**Step 1: Bot neustarten**

Run: `sudo systemctl restart shadowops-bot`

**Step 2: Health-Check**

Run: `curl -s http://127.0.0.1:8766/health | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','FAIL'))"`
Expected: `ok`

**Step 3: Changelog-API testen**

Run: `curl -s "http://127.0.0.1:8766/api/changelogs?project=mayday_sim&limit=2" | head -c 500`
Expected: JSON mit MayDay Sim Changelogs

**Step 4: Commit alle ShadowOps Änderungen**

```bash
git add -A && git commit -m "feat: MayDay Sim Changelog Backend — CORS, Gaming-Badges, Discord-Teaser"
```

---

## Phase 2: MayDay Sim Web — Frontend

### Task 6: API-Proxy Route

**Files:**
- Create: `/srv/leitstelle/app/web/src/app/api/changelogs/route.ts`

**Step 1: API-Proxy erstellen (nach ZERODOX-Vorbild)**

```typescript
import { NextRequest, NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

const CHANGELOG_API_URL = process.env.CHANGELOG_API_URL || 'http://127.0.0.1:8766';

export async function GET(request: NextRequest) {
  const { searchParams } = new URL(request.url);
  const page = searchParams.get('page') || '1';
  const limit = searchParams.get('limit') || '20';

  try {
    const response = await fetch(
      `${CHANGELOG_API_URL}/api/changelogs?project=mayday_sim&page=${page}&limit=${limit}`,
    );

    if (!response.ok) {
      return NextResponse.json(
        { error: 'Changelog-API nicht erreichbar' },
        { status: response.status },
      );
    }

    const data = await response.json();
    return NextResponse.json(data);
  } catch (error) {
    console.error('[CHANGELOGS] Proxy error:', error);
    return NextResponse.json(
      { error: 'Changelog-API nicht erreichbar' },
      { status: 502 },
    );
  }
}
```

**Step 2: Testen**

Run: `curl -s http://127.0.0.1:3200/api/changelogs | head -c 300`
Expected: JSON mit MayDay Sim Changelogs (durchgereicht vom Bot)

**Step 3: Commit**

```bash
cd /srv/leitstelle/app && git add web/src/app/api/changelogs/route.ts
git commit -m "feat: Changelog API-Proxy Route"
```

---

### Task 7: TypeScript Types + Badge-Mapping

**Files:**
- Create: `/srv/leitstelle/app/web/src/types/changelog.ts`

**Step 1: Types definieren**

```typescript
export interface ChangelogEntry {
  project: string;
  version: string;
  title: string;
  tldr: string;
  content: string;
  language: string;
  published_at: string;
  stats: {
    commits: number;
    files_changed: number;
    lines_added: number;
    lines_removed: number;
  };
  changes: Change[];
  seo: {
    keywords: string[];
    meta_description: string;
    og_title: string;
    og_description: string;
  };
  slug: string;
}

export interface Change {
  type: ChangeType;
  description: string;
  details?: string[];
}

export type ChangeType =
  | 'feature'
  | 'content'
  | 'gameplay'
  | 'design'
  | 'performance'
  | 'multiplayer'
  | 'fix'
  | 'breaking'
  | 'infrastructure'
  | 'improvement'
  | 'docs'
  | 'security';

export interface BadgeConfig {
  label: string;
  emoji: string;
  colorClass: string;
}

export const BADGE_MAP: Record<ChangeType, BadgeConfig> = {
  feature:        { label: 'NEUES FEATURE',   emoji: '🔵', colorClass: 'bg-md-bos-thw/15 text-md-bos-thw' },
  content:        { label: 'NEUER CONTENT',   emoji: '🗺️', colorClass: 'bg-md-success/15 text-md-success' },
  gameplay:       { label: 'GAMEPLAY',         emoji: '🎮', colorClass: 'bg-md-accent/15 text-md-accent' },
  design:         { label: 'DESIGN',           emoji: '🎨', colorClass: 'bg-md-bos-rescue/15 text-md-bos-rescue' },
  performance:    { label: 'PERFORMANCE',      emoji: '⚡', colorClass: 'bg-sky-500/15 text-sky-400' },
  multiplayer:    { label: 'MULTIPLAYER',      emoji: '👥', colorClass: 'bg-md-bos-police/15 text-md-bos-police' },
  fix:            { label: 'BUGFIX',           emoji: '🔴', colorClass: 'bg-md-bos-fire/15 text-md-bos-fire' },
  breaking:       { label: 'ACHTUNG',          emoji: '⚠️', colorClass: 'bg-md-bos-warn/15 text-md-bos-warn' },
  infrastructure: { label: 'INFRASTRUKTUR',    emoji: '🛡️', colorClass: 'bg-md-text-muted/15 text-md-text-muted' },
  improvement:    { label: 'VERBESSERT',       emoji: '🟠', colorClass: 'bg-md-bos-rescue/15 text-md-bos-rescue' },
  docs:           { label: 'DOKUMENTATION',    emoji: '📖', colorClass: 'bg-md-text-muted/15 text-md-text-muted' },
  security:       { label: 'SICHERHEIT',       emoji: '🔒', colorClass: 'bg-md-bos-police/15 text-md-bos-police' },
};

export const CHANGELOG_API_URL = '/api/changelogs';
```

**Step 2: Commit**

```bash
cd /srv/leitstelle/app && git add web/src/types/changelog.ts
git commit -m "feat: Changelog TypeScript Types + Gaming Badge-Mapping"
```

---

### Task 8: Changelog Übersichtsseite — Einsatzprotokoll

**Files:**
- Rewrite: `/srv/leitstelle/app/web/src/app/changelog/page.tsx`

**Step 1: Übersichtsseite komplett neu schreiben**

Die bestehende statische Seite (hardcoded Array) wird ersetzt durch eine SSR-Page die Daten von der API lädt. Layout im "Einsatzprotokoll"-Stil mit:

- Hero-Bereich oben (Bild + Overlay-Text + BOS-Grid)
- Timeline mit vertikaler Linie (md-accent)
- Release-Cards mit BOS-Badges, TL;DR, Stats
- Neueste Version groß, ältere kompakter
- Framer Motion Stagger-Animationen
- Metadata + JSON-LD

Schlüssel-Elemente:
- `fetch('/api/changelogs?limit=20')` serverseitig (SSR, `revalidate: 60`)
- Timeline-Dots mit Pulse-Animation für neueste Version
- Change-Type Badges mit BOS-Farben aus `BADGE_MAP`
- Stats-Leiste in JetBrains Mono
- "Vollständiger Bericht →" Links zu Detail-Seiten
- SEO: Metadata, OpenGraph, canonical `/changelog`

**Step 2: Testen**

Browser: `https://maydaysim.de/changelog` (oder `http://127.0.0.1:3200/changelog`)
Expected: Timeline mit Releases aus der API, BOS-Badges, animierte Cards

**Step 3: Commit**

```bash
cd /srv/leitstelle/app && git add web/src/app/changelog/page.tsx
git commit -m "feat: Changelog Übersichtsseite — Einsatzprotokoll-Design"
```

---

### Task 9: Changelog Detail-Seite

**Files:**
- Create: `/srv/leitstelle/app/web/src/app/changelog/[version]/page.tsx`

**Step 1: Detail-Seite erstellen**

SSR-Page mit dynamischem `[version]` Parameter (Dashes → Dots Konvertierung wie GuildScout/ZERODOX):

- Version aus URL extrahieren: `0-16-0` → `0.16.0`
- Fetch: `/api/changelogs/mayday_sim/{version}` (via Bot API direkt, SSR)
- Header: Version (Monospace groß) + Datum + Titel
- Stats-Leiste: Commits, Dateien, Lines +/-
- TL;DR in eigener Card (md-surface, Akzent-Border)
- Changes gruppiert nach Type mit BOS-Farb-Headern
- Pfeil-Format (→) pro Änderung mit Detail-Unterpunkten
- SEO-Keywords als Tag-Cloud am Ende
- Prev/Next Navigation (aus Index der API)
- Metadata dynamisch: Titel, Description, Keywords aus SEO-Feld
- JSON-LD: TechArticle Schema + Breadcrumb
- Canonical: `/changelog/{version-with-dashes}`

**Step 2: Testen**

Browser: `https://maydaysim.de/changelog/0-16-0`
Expected: Detail-Seite mit vollem Content, BOS-Badges, Stats, Keywords

**Step 3: Commit**

```bash
cd /srv/leitstelle/app && git add web/src/app/changelog/\[version\]/page.tsx
git commit -m "feat: Changelog Detail-Seite mit SEO + JSON-LD"
```

---

### Task 10: OG-Image Generator

**Files:**
- Create: `/srv/leitstelle/app/web/src/app/changelog/[version]/opengraph-image.tsx`

**Step 1: Dynamisches OG-Image erstellen**

Next.js `ImageResponse` (1200x630px):
- Background: `#0c1117` (md-bg) mit BOS-Grid-Pattern
- "MAYDAY SIM" Logo-Text oben links
- Version groß in JetBrains Mono (`#3b82f6`)
- Titel darunter
- Stats-Zeile: "X Features · Y Fixes · Z Commits"
- BOS-Akzent-Linie als Trenner

```typescript
import { ImageResponse } from 'next/og';

export const runtime = 'edge';
export const alt = 'MAYDAY SIM Changelog';
export const size = { width: 1200, height: 630 };
export const contentType = 'image/png';

export default async function Image({ params }: { params: { version: string } }) {
  const version = params.version.replace(/-/g, '.');
  // Fetch changelog data
  const res = await fetch(`http://127.0.0.1:8766/api/changelogs/mayday_sim/${version}`);
  const data = await res.json();

  return new ImageResponse(
    // JSX mit BOS-Design...
  );
}
```

**Step 2: Testen**

Browser: `https://maydaysim.de/changelog/0-16-0/opengraph-image`
Expected: PNG-Bild (1200x630) mit MayDay-Design

**Step 3: Commit**

```bash
cd /srv/leitstelle/app && git add web/src/app/changelog/\[version\]/opengraph-image.tsx
git commit -m "feat: Dynamisches OG-Image für Changelog-Versionen"
```

---

### Task 11: Sitemap erweitern

**Files:**
- Modify: `/srv/leitstelle/app/web/src/app/sitemap.ts`

**Step 1: Dynamische Changelog-URLs in Sitemap**

Die Sitemap soll für jede Changelog-Version eine eigene URL enthalten:

```typescript
import type { MetadataRoute } from "next";

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const baseUrl = "https://maydaysim.de";

  // Statische Seiten
  const staticPages: MetadataRoute.Sitemap = [
    { url: baseUrl, lastModified: new Date(), changeFrequency: "weekly", priority: 1 },
    { url: `${baseUrl}/faq`, lastModified: new Date(), changeFrequency: "monthly", priority: 0.8 },
    { url: `${baseUrl}/changelog`, lastModified: new Date(), changeFrequency: "weekly", priority: 0.7 },
    { url: `${baseUrl}/impressum`, lastModified: new Date(), changeFrequency: "yearly", priority: 0.3 },
    { url: `${baseUrl}/datenschutz`, lastModified: new Date(), changeFrequency: "yearly", priority: 0.3 },
  ];

  // Dynamische Changelog-Versionen
  let changelogPages: MetadataRoute.Sitemap = [];
  try {
    const res = await fetch('http://127.0.0.1:8766/api/changelogs?project=mayday_sim&limit=50', {
      next: { revalidate: 3600 },
    });
    if (res.ok) {
      const data = await res.json();
      const entries = data.entries || data || [];
      changelogPages = entries.map((entry: { version: string; published_at: string }) => ({
        url: `${baseUrl}/changelog/${entry.version.replace(/\./g, '-')}`,
        lastModified: new Date(entry.published_at),
        changeFrequency: 'monthly' as const,
        priority: 0.6,
      }));
    }
  } catch {
    // Sitemap ohne dynamische Changelog-URLs generieren
  }

  return [...staticPages, ...changelogPages];
}
```

**Step 2: Testen**

Run: `curl -s http://127.0.0.1:3200/sitemap.xml | head -40`
Expected: XML mit statischen Seiten + Changelog-Versionen

**Step 3: Commit**

```bash
cd /srv/leitstelle/app && git add web/src/app/sitemap.ts
git commit -m "feat: Dynamische Changelog-URLs in Sitemap"
```

---

## Phase 3: Hero-Bild + Deployment

### Task 12: ChatGPT Hero-Bild generieren (manuell)

**Prompt für ChatGPT (DALL-E):**

```
Dark emergency dispatch center interior at night. Multiple large
screens showing a German city map with red and blue emergency markers
and tactical grid overlay. One dispatcher silhouette seen from behind,
sitting at the command desk. Blue and red ambient lighting reflecting
off polished surfaces. Photorealistic, cinematic 16:9 ultrawide
composition. Dark moody atmosphere with subtle lens flare from the
screens. BOS/emergency services aesthetic. No text or logos.
```

**Bild speichern als:** `/srv/leitstelle/app/web/public/images/changelog-hero.webp`

Konvertierung (falls nötig): `cwebp -q 85 changelog-hero.png -o changelog-hero.webp`

---

### Task 13: Deployment + Smoke Test

**Step 1: MayDay Web neu bauen**

Run: `sudo docker compose -f /srv/leitstelle/app/docker-compose.yml --env-file /srv/leitstelle/.env build leitstelle-web`

**Step 2: MayDay Web deployen**

Run: `sudo docker compose -f /srv/leitstelle/app/docker-compose.yml --env-file /srv/leitstelle/.env up -d leitstelle-web`

**Step 3: Smoke Test**

- `curl -s http://127.0.0.1:3200/changelog | head -20` → HTML mit Timeline
- `curl -s http://127.0.0.1:3200/api/changelogs | head -100` → JSON Data
- `curl -s http://127.0.0.1:3200/sitemap.xml | grep changelog` → Changelog-URLs
- Browser: `https://maydaysim.de/changelog` → Einsatzprotokoll-Design

**Step 4: ShadowOps Bot neustarten (finale Config)**

Run: `sudo systemctl restart shadowops-bot`

**Step 5: Commit + Push**

```bash
cd /srv/leitstelle/app && git add -A && git commit -m "feat: MayDay Sim Changelog — Einsatzprotokoll-Design mit Gaming-Badges"
cd /home/cmdshadow/shadowops-bot && git add -A && git commit -m "feat: MayDay Sim Changelog Backend — CORS, Gaming-Badges, Discord-Teaser"
```
