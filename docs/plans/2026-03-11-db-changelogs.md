# DB-basiertes Changelog-System — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Changelogs von statischen JSON-Dateien auf PostgreSQL-basierte API mit Pagination umstellen — in GuildScout (Go/Fiber), ZERODOX (Next.js/Prisma) und ShadowOps Bot (Python).

**Architecture:** ShadowOps Bot POSTet Changelogs per HTTPS an die jeweilige Projekt-API. Jedes Projekt speichert in seiner eigenen PostgreSQL-DB. Frontends fetchen paginiert via GET-Endpoints. File-Export bleibt als Fallback.

**Tech Stack:** Go/Fiber/pgx (GuildScout), Next.js/Prisma (ZERODOX), Python/aiohttp (ShadowOps Bot)

---

## Task 1: GuildScout — DB-Migration + Repository

**Files:**
- Create: `/home/cmdshadow/GuildScout/api/internal/database/changelog_repository.go`
- Modify: `/home/cmdshadow/GuildScout/api/cmd/server/main.go`

**Step 1: Create changelogs table**

Verbinde dich auf die GuildScout-DB und fuehre aus:

```sql
CREATE TABLE IF NOT EXISTS changelogs (
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
CREATE INDEX IF NOT EXISTS idx_changelogs_published ON changelogs(published_at DESC);
```

Run via MCP: `postgres-guildscout` execute_sql

**Step 2: Create changelog_repository.go**

Folgt dem Projekt-Pattern: Struct + Constructor + List/GetByVersion/Create Methoden.
- List: Paginiert mit COUNT + LIMIT/OFFSET
- Create: INSERT ON CONFLICT UPDATE (Upsert)
- GetByVersion: Single row by version string

**Step 3: Commit**

```bash
cd /home/cmdshadow/GuildScout
git add api/internal/database/changelog_repository.go
git commit -m "feat: add changelog repository with pagination support"
```

---

## Task 2: GuildScout — Handler + Routes

**Files:**
- Create: `/home/cmdshadow/GuildScout/api/internal/handlers/changelog.go`
- Modify: `/home/cmdshadow/GuildScout/api/cmd/server/main.go`

**Step 1: Create changelog_handler.go**

Folgt dem Projekt-Pattern (narrow interface, DI, response helpers):
- List: GET /api/v2/changelogs?page=1&limit=10 → OKWithMeta
- Get: GET /api/v2/changelogs/:version → OK
- Create: POST /api/v2/changelogs + X-API-Key Header → Created

API-Key wird aus CHANGELOG_API_KEY Env-Var gelesen.

**Step 2: Register routes in main.go**

- Initialisiere ChangelogRepository + ChangelogHandler nach den anderen Repos
- Registriere Routes unter `api.Group("/changelogs")` (public, keine Auth)
- Entferne alte statische Changelog-Routes (/changelogs static, /dashboard/changelogs/*)

**Step 3: Commit**

---

## Task 3: GuildScout — Frontend Update

**Files:**
- Modify: `/home/cmdshadow/GuildScout/dashboard/src/pages/Changelog.tsx`

**Step 1: Update Changelog.tsx**

- ChangelogIndex: Fetch von `/api/v2/changelogs?page=X&limit=10`
- Response-Format: `{ data: [...], meta: { page, per_page, total, total_pages } }`
- Pagination-Komponente mit Prev/Next Buttons
- ChangelogDetailView: Fetch von `/api/v2/changelogs/:version`
- Response-Format: `{ data: { version, title, tldr, content, stats, ... } }`

**Step 2: Rebuild Dashboard**

```bash
cd /home/cmdshadow/GuildScout/dashboard
NODE_OPTIONS="--max-old-space-size=2048" npm run build
cp -r dist/* /home/cmdshadow/GuildScout/api/static/dashboard/
```

**Step 3: Commit**

---

## Task 4: ZERODOX — Prisma Model + Migration

**Files:**
- Modify: `/home/cmdshadow/ZERODOX/web/prisma/schema.prisma` (nach Zeile 960)

**Step 1: Add Changelog model**

```prisma
model Changelog {
  id          Int      @id @default(autoincrement())
  version     String   @unique @db.VarChar(20)
  title       String
  tldr        String
  content     String   @db.Text
  stats       Json     @default("{}")
  seo         Json     @default("{}")
  language    String   @default("de") @db.VarChar(5)
  publishedAt DateTime @default(now()) @map("published_at")
  createdAt   DateTime @default(now()) @map("created_at")

  @@index([publishedAt(sort: Desc)])
  @@map("changelogs")
}
```

**Step 2: Run migration**

```bash
cd /home/cmdshadow/ZERODOX/web
npx prisma migrate dev --name add-changelogs
```

**Step 3: Commit**

---

## Task 5: ZERODOX — API Routes

**Files:**
- Create: `/home/cmdshadow/ZERODOX/web/src/app/api/changelogs/route.ts` (GET list + POST)
- Create: `/home/cmdshadow/ZERODOX/web/src/app/api/changelogs/[version]/route.ts` (GET detail)
- Delete: `/home/cmdshadow/ZERODOX/web/src/app/api/changelogs/[...path]/route.ts` (alter File-Handler)

**Step 1: GET+POST /api/changelogs**

- GET: Paginiert mit page/limit Query-Params, Prisma findMany + count
- POST: API-Key Auth via X-API-Key Header, Prisma upsert
- Response-Format konsistent: `{ success, data, meta }`

**Step 2: GET /api/changelogs/[version]**

- Prisma findUnique by version
- 404 wenn nicht gefunden

**Step 3: Delete old file route, Commit**

---

## Task 6: ZERODOX — Frontend Update

**Files:**
- Modify: `/home/cmdshadow/ZERODOX/web/src/app/changelog/ChangelogClient.tsx`
- Modify: `/home/cmdshadow/ZERODOX/web/src/app/changelog/[version]/ChangelogDetailClient.tsx`

**Step 1: ChangelogClient.tsx — Paginierte API**

- Fetch von `/api/changelogs?page=X&limit=10`
- Pagination-Buttons (Zurueck/Weiter)
- Response: `{ data: [...], meta: { page, per_page, total, total_pages } }`

**Step 2: ChangelogDetailClient.tsx**

- Fetch von `/api/changelogs/:version` statt JSON-Datei
- Response: `{ data: { version, title, content, stats, ... } }`
- Content wird weiterhin mit sanitize-html XSS-geschuetzt

**Step 3: Commit**

---

## Task 7: ShadowOps Bot — Web Exporter Update

**Files:**
- Modify: `/home/cmdshadow/shadowops-bot/src/integrations/patch_notes_web_exporter.py`
- Modify: `/home/cmdshadow/shadowops-bot/src/bot.py`
- Modify: `/home/cmdshadow/shadowops-bot/config/config.example.yaml`

**Step 1: Add _post_to_api method**

- Async HTTP POST via aiohttp
- Sendet version, title, tldr, content, stats, seo, language, published_at
- X-API-Key Header
- 10s Timeout, graceful error handling

**Step 2: Extend __init__ to accept api_endpoints**

```python
def __init__(self, base_output_dir: Path, api_endpoints: Optional[Dict] = None):
```

**Step 3: Call _post_to_api at end of export()**

File-Export bleibt als Fallback bestehen.

**Step 4: Update bot.py initialization**

API-Endpoints aus project config laden und an WebExporter uebergeben.

**Step 5: Update config.example.yaml**

```yaml
      api_endpoint:
        url: ""
        api_key: ""
```

**Step 6: Commit**

---

## Task 8: Deploy + Migration

**Step 1:** GuildScout DB-Migration ausfuehren (CREATE TABLE)
**Step 2:** API-Keys generieren (openssl rand -hex 32)
**Step 3:** GuildScout Docker Image + Container neu bauen mit CHANGELOG_API_KEY env var
**Step 4:** ZERODOX Prisma Migration ausfuehren
**Step 5:** ZERODOX Container mit CHANGELOG_API_KEY env var deployen
**Step 6:** Bestehende JSON-Testdaten in DB importieren (per API POST)
**Step 7:** ShadowOps Bot config.yaml aktualisieren + neustarten
**Step 8:** Verifizieren: curl API-Endpoints + Frontend im Browser
