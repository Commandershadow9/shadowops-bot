# MayDay Sim Changelog — Einsatzprotokoll Design

**Datum:** 2026-03-30
**Status:** Approved
**Projekt:** MayDay Sim (maydaysim.de) + ShadowOps Bot

## Ziel

Dynamische Changelog-Seite für MayDay Sim im "Einsatzprotokoll"-Stil. Daten kommen aus der zentralen ShadowOps Bot API (einheitliches Pattern für alle Projekte). Discord bekommt eine Teaser-Version mit Cliffhanger + Link zur Website.

## Entscheidungen

| Thema | Entscheidung |
|-------|-------------|
| Architektur | Einheitliches Pattern: Bot API → Projekt-Proxy → Frontend |
| Design-Stil | "Einsatzprotokoll" — BOS-Look mit Timeline, Grid-Pattern |
| Bilder | Hero (1x ChatGPT), OG-Images (automatisch via next/og) |
| Discord | Teaser-Modus: Hype-Text + Highlights + Cliffhanger + Link |
| Web | Ausführlich: Voller Content, alle Details, Stats, SEO-Keywords |
| Badges | 9 Gaming-spezifische Badge-Types mit BOS-Farben |
| SEO | JSON-LD, OG-Images, Sitemap, Breadcrumbs, Keywords |
| Shared-UI | NICHT genutzt — eigenes Gaming-Design |

## Architektur

### Einheitliches Changelog-Pattern (alle Projekte)

```
ShadowOps Bot (Port 8766, SQLite ChangelogDB)
       │  GET /api/changelogs?project=mayday_sim
       │  GET /api/changelogs/mayday_sim/{version}
       ▼
MayDay Web (/src/app/api/changelogs/route.ts)
       │  Dünner Proxy (~15 Zeilen)
       ▼
Frontend Pages (SSR, revalidate 60s)
  ├── /changelog              → Übersicht
  ├── /changelog/[version]    → Detail
  └── /changelog/[version]/opengraph-image.tsx → OG-Image
```

### ShadowOps Bot Änderungen

1. **Config (config.yaml):** `changelog_url: https://maydaysim.de/changelog` für mayday_sim
2. **CORS (health_server.py):** `https://maydaysim.de` zu Allowlist
3. **Discord Teaser:** Neues Feld `discord_teaser` in Patch Notes JSON-Output
4. **Gaming Badges:** Erweiterte Change-Types im gaming_community_v2 Template

## Badge-System (9 Gaming-Types)

| Badge | Type-Key | Farbe | CSS-Variable |
|-------|----------|-------|-------------|
| NEUES FEATURE | `feature` | Blau | `md-bos-thw` |
| NEUER CONTENT | `content` | Grün | `md-success` |
| GAMEPLAY | `gameplay` | Akzent-Blau | `md-accent` |
| DESIGN | `design` | Orange | `md-bos-rescue` |
| PERFORMANCE | `performance` | Hell-Blau | `md-bos-thw` (light) |
| MULTIPLAYER | `multiplayer` | Dunkelblau | `md-bos-police` |
| BUGFIX | `fix` | Rot | `md-bos-fire` |
| ACHTUNG | `breaking` | Gelb | `md-bos-warn` |
| INFRASTRUKTUR | `infrastructure` | Grau | `md-text-muted` |

Mapping im gaming_community_v2 Prompt: KI generiert `type` pro Change, Frontend mappt auf Badge.
Non-Gaming-Projekte behalten die klassischen 5 Types (feature, fix, improvement, security, breaking).

## Frontend-Design

### Übersichtsseite `/changelog`

- **Hero:** Statisches ChatGPT-Bild mit Overlay "EINSATZPROTOKOLL", BOS-Grid-Pattern
- **Timeline:** Vertikale Linie (md-accent), animierte Dots (Pulse für neueste Version)
- **Release-Cards:** md-surface Background, Hover-Glow, BOS-Badge-Farben
  - Neueste Version: Groß, mit TL;DR und allen Highlights
  - Ältere Versionen: Kompakter, nur Top-3 Changes + Stats
- **Stats-Leiste:** JetBrains Mono, Commits/Dateien/LOC
- **Animationen:** Framer Motion fade-in-up pro Card (staggered)

### Detail-Seite `/changelog/[version]`

- **Back-Link:** "← Zurück zum Einsatzprotokoll"
- **Header:** Version (Monospace, groß) + Datum + Titel
- **Stats-Leiste:** Commits, Dateien, Lines Added/Removed
- **TL;DR:** Storytelling-Zusammenfassung in eigener Card
- **Changes:** Gruppiert nach Type, jede Gruppe mit BOS-Farb-Header
  - Pfeil-Format (→) für jede Änderung
  - Detail-Punkte unter jeder Änderung
- **SEO-Keywords:** Als Tag-Cloud am Ende
- **Prev/Next Navigation:** Links zu benachbarten Versionen

### OG-Image (automatisch)

Next.js `ImageResponse` in `/changelog/[version]/opengraph-image.tsx`:
- BOS-Grid Background (#0c1117)
- MAYDAY SIM Logo
- Version groß (JetBrains Mono)
- Titel
- Stats-Zeile (X Features, Y Fixes)
- 1200x630px

## Discord-Integration

### Teaser-Modus (neu)

Wenn `changelog_url` gesetzt ist, generiert gaming_community_v2 zusätzlich ein `discord_teaser` Feld:

```
🚨 MAYDAY SIM — v0.16.0

[2-3 Hype-Sätze die erzählen was sich verändert hat]

🔵 Feature-Highlight 1
🗺️ Content-Highlight 2
🎮 Gameplay-Highlight 3
🎨 Design-Highlight 4
🔴 Fix-Highlight 5
⚡ Performance-Highlight 6

[Cliffhanger-Satz: "Aber das war noch nicht alles..."]

→ Komplette Patch Notes: maydaysim.de/changelog/{version}
```

Max ~1000 Zeichen. Genug zum Scannen, Cliffhanger macht neugierig.

### Unterschied Discord vs. Web

| Aspekt | Discord (Teaser) | Web (Vollständig) |
|--------|-----------------|-------------------|
| Länge | ~1000 Zeichen | Unbegrenzt |
| Details | 1 Zeile pro Change | Mehrzeilig mit Details |
| Storytelling | Teaser + Cliffhanger | Voller Content |
| Stats | Keine | Commits, Dateien, LOC |
| SEO-Keywords | Keine | Tag-Cloud |
| Bilder | Embed-Thumbnail | OG-Image + Hero |

## SEO

- **Meta-Tags:** Aus `seo` Feld der JSON (title, description, keywords)
- **JSON-LD:** TechArticle Schema pro Version
- **Breadcrumbs:** Home → Changelog → v0.16.0 (JSON-LD)
- **Sitemap:** Dynamisch erweitern — jede Version als eigene URL
- **Canonical:** `/changelog/{version-with-dashes}`
- **OG-Image:** Automatisch generiert (s.o.)

## Hero-Bild (ChatGPT Prompt)

Einmalig generieren, für Changelog-Übersichtsseite:

```
Prompt: "Dark emergency dispatch center interior at night,
multiple large screens showing a German city map with red
and blue emergency markers, tactical overlay with grid lines,
one dispatcher silhouette from behind, blue and red ambient
lighting reflecting off surfaces, photorealistic, cinematic
composition, 16:9 ultrawide aspect ratio, dark moody atmosphere,
BOS/emergency services aesthetic"
```

Bild wird als WebP in `/public/images/changelog-hero.webp` gespeichert.

## Nicht im Scope

- Shared-UI Komponenten (eigenes Design)
- GuildScout/ZERODOX Changelog-Umbau (spätere Phase)
- Feature-Illustrationen pro Release (zu viel manueller Aufwand)
- Technischer Details-Bereich (Zielgruppe = Gamer, nicht Devs)
