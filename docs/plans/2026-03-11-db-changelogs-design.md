# DB-basiertes Changelog-System

**Datum:** 2026-03-11
**Status:** Approved
**Scope:** GuildScout (Go/Fiber), ZERODOX (Next.js/Prisma), ShadowOps Bot (Python)

## Motivation

Aktuelle JSON-Datei-basierte Changelogs skalieren nicht: `index.json` waechst unbegrenzt, keine Pagination, keine Suche, Race Conditions bei concurrent writes.

## Architektur

```
ShadowOps Bot --POST--> GuildScout API --> PostgreSQL (changelogs)
                   \--> ZERODOX API    --> PostgreSQL (changelogs)

Frontend (GET) <-- Paginierte API <-- PostgreSQL
```

## Datenbank-Schema

Identisch in beiden Projekten:

```sql
CREATE TABLE changelogs (
    id           SERIAL PRIMARY KEY,
    version      VARCHAR(20) UNIQUE NOT NULL,
    title        TEXT NOT NULL,
    tldr         TEXT NOT NULL,
    content      TEXT NOT NULL,
    stats        JSONB DEFAULT '{}',
    seo          JSONB DEFAULT '{}',
    language     VARCHAR(5) DEFAULT 'en',
    published_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_changelogs_published ON changelogs(published_at DESC);
```

## API-Endpoints

| Method | Path | Auth | Beschreibung |
|--------|------|------|-------------|
| GET | /api/v2/changelogs?page=1&limit=10 | Keine | Paginierte Liste |
| GET | /api/v2/changelogs/:version | Keine | Einzelne Version |
| POST | /api/v2/changelogs | X-API-Key | Neuen Changelog erstellen |

### Response-Format (GET List)

```json
{
  "entries": [
    {"version": "3.4.0", "title": "...", "tldr": "...", "published_at": "..."}
  ],
  "pagination": {"page": 1, "limit": 10, "total": 42, "pages": 5}
}
```

### POST Body

```json
{
  "version": "3.4.0",
  "title": "...",
  "tldr": "...",
  "content": "Markdown...",
  "stats": {"commits": 8, "files_changed": 15, ...},
  "seo": {"meta_description": "...", "og_title": "..."},
  "language": "en"
}
```

## ShadowOps Bot Aenderungen

- `PatchNotesWebExporter.export()`: Primaer HTTP POST, Fallback JSON-Dateien
- Neue Config-Keys: `patch_notes.api_endpoints.{project}.{url,api_key}`

## Frontend Aenderungen

- GuildScout React: Fetch von `/api/v2/changelogs`, Pagination-Buttons
- ZERODOX Next.js: Fetch von `/api/changelogs`, Pagination-Buttons

## Migration

Einmalig bestehende JSON-Testdaten in DB importieren.

## Unveraendert

- Discord Patch-Notes-Posts
- AI-Generierung
- Batcher-Logic
