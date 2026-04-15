---
title: Multi-Agent Review Pipeline — Uebersicht
status: active
version: v1
last_reviewed: 2026-04-15
owner: CommanderShadow9
related:
  - ../../adr/008-multi-agent-review-pipeline.md
  - ../../plans/2026-04-14-multi-agent-review-design.md
  - ../jules-workflow/README.md
---

# Multi-Agent Review Pipeline — Uebersicht

Erweitert den bestehenden Jules-Workflow um ein Adapter-Pattern fuer SEO- und Codex-PR-Reviews,
Jules-Suggestions-Auto-Start, eine Queue fuer Jules-Limits, projekt-spezifisches Auto-Merge und
einen Daily-Digest — ohne bestehende Server-Agents oder die sieben Jules-Module anzufassen.

**Architektur:** Adapter-Pattern als additive Erweiterung. `JulesAdapter` wrapt den existierenden
Jules-Code 1:1 (keine Regression). Neu hinzu kommen `SeoAdapter` und `CodexAdapter`. Ein
Detector-Dispatcher ersetzt die hardcoded Jules-Detection im Mixin. Die Queue wird nur fuer den
Jules-Session-Start genutzt (100 pro 24h, 15 concurrent). Auto-Merge laeuft ueber eine
Rule-Engine, der Outcome-Tracker lernt aus den Ergebnissen.

**Tech Stack:** Python 3.12, asyncpg, aiohttp, Claude CLI (Opus + Sonnet), Jules API v1alpha,
PostgreSQL, Redis, pytest.

**Feature-Flag & Rollback:** `agent_review.enabled: false` ist Default. Rollback unter 30 Sekunden
via Config-Flag. Auto-Merge hat einen separaten Toggle (`agent_review.auto_merge.enabled`).

Design Reference: `docs/plans/2026-04-14-multi-agent-review-design.md`

---

## Navigation

| Datei | Inhalt |
|-------|--------|
| [`adapters.md`](./adapters.md) | Adapter-ABC, JulesAdapter, SeoAdapter, CodexAdapter, gemeinsames Interface, Prompt-Templates |
| [`queue-and-poller.md`](./queue-and-poller.md) | TaskQueue (asyncpg), Jules-API-Client, Suggestions-Poller (Stub-Status) |
| [`merge-and-outcomes.md`](./merge-and-outcomes.md) | Merge-Policy pro Adapter, Outcome-Tracker, 24h-Revert-Detection |
| [`digest-and-ui.md`](./digest-and-ui.md) | Daily-Digest (Markdown-Report), Weekly-Recap (Discord-Embed), farbkodierte Review-Embeds |

---

## Phase 0: Groundwork (DB + Config)

### DB-Migration — `agent_task_queue` + `auto_merge_outcomes`

**File:** `src/integrations/github_integration/agent_review_schema.sql`

```sql
-- Multi-Agent Review Pipeline — Queue + Outcome Tracking
-- Siehe docs/plans/2026-04-14-multi-agent-review-design.md §7, §9

-- Queue fuer Jules-Session-Starts (nur POST /sessions)
CREATE TABLE IF NOT EXISTS agent_task_queue (
    id              BIGSERIAL PRIMARY KEY,
    source          TEXT NOT NULL,                     -- 'jules_suggestion'|'scan_agent'|'manual'
    priority        INTEGER NOT NULL CHECK (priority BETWEEN 0 AND 4),
    payload         JSONB NOT NULL,                    -- {repo, prompt, title, ...}
    project         TEXT,
    scheduled_for   TIMESTAMPTZ NOT NULL DEFAULT now(),
    released_at     TIMESTAMPTZ,
    released_as     TEXT,                              -- jules session_id nach Release
    failure_reason  TEXT,
    retry_count     INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'queued'
                    CHECK (status IN ('queued','released','failed','cancelled')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_queue_next
    ON agent_task_queue(scheduled_for ASC, priority ASC)
    WHERE status = 'queued';

-- Outcome-Tracking fuer Auto-Merges
CREATE TABLE IF NOT EXISTS auto_merge_outcomes (
    id              BIGSERIAL PRIMARY KEY,
    agent_type      TEXT NOT NULL,
    project         TEXT NOT NULL,
    repo            TEXT NOT NULL,
    pr_number       INTEGER NOT NULL,
    rule_matched    TEXT NOT NULL,
    merged_at       TIMESTAMPTZ NOT NULL,
    -- 24h-Check (nachtrag befuellt)
    reverted                   BOOLEAN NOT NULL DEFAULT false,
    reverted_at                TIMESTAMPTZ,
    ci_passed_after_merge      BOOLEAN,
    deployed_without_incident  BOOLEAN,
    follow_up_fix_needed       BOOLEAN NOT NULL DEFAULT false,
    checked_at                 TIMESTAMPTZ,
    created_at                 TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ame_agent_rule ON auto_merge_outcomes(agent_type, rule_matched);
CREATE INDEX IF NOT EXISTS idx_ame_pending_check
    ON auto_merge_outcomes(merged_at)
    WHERE checked_at IS NULL;

-- Bestehende jules_pr_reviews Tabelle um agent_type erweitern
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'jules_pr_reviews' AND column_name = 'agent_type'
    ) THEN
        ALTER TABLE jules_pr_reviews ADD COLUMN agent_type TEXT NOT NULL DEFAULT 'jules';
        CREATE INDEX idx_jpr_agent_type ON jules_pr_reviews(agent_type);
    END IF;
END $$;
```

### Config-Block `agent_review`

**File:** `config/config.example.yaml`

```yaml
agent_review:
  enabled: false                       # Master-Switch, default off bis Phase 6
  dry_run: false

  # Queue fuer Jules-Session-Starts (einzige Queue im System)
  jules_queue:
    max_new_sessions_per_24h: 100
    max_concurrent_sessions: 15
    retry_interval_seconds: 60
    scheduler_interval_seconds: 60

  # Claude-Review Capacity (kein Queue, nur Cap)
  claude_review:
    max_concurrent_calls: 8

  # Jules Suggestions Poller
  suggestions_poller:
    enabled: false                     # separat aktivierbar
    interval_hours: 8                  # 3x taeglich (08:00, 16:00, 00:00)
    repos:
      - "Commandershadow9/ZERODOX"
      - "Commandershadow9/GuildScout"
      - "Commandershadow9/shadowops-bot"
      - "Commandershadow9/ai-agent-framework"
      - "Commandershadow9/mayday-sim"

  # Auto-Merge Policies per Projekt
  auto_merge:
    enabled: false                     # separat aktivierbar
    default_method: "squash"
    projects:
      ZERODOX:            { allowed: true,  trivial_threshold: 100 }
      GuildScout:         { allowed: true,  trivial_threshold: 150 }
      mayday-sim:         { allowed: true,  trivial_threshold: 500 }
      shadowops-bot:      { allowed: true,  trivial_threshold: 50 }
      sicherheitsdienst:  { allowed: false }
      ai-agent-framework: { allowed: true,  trivial_threshold: 100 }

  # Discord Channels (alle existierend ausser agent_reviews)
  discord:
    jules_reviews:     "code-fixes"
    seo_reviews:       "seo-fixes"
    codex_reviews:     "agent-reviews"
    escalations:       "approvals"
    daily_digest:      "ai-learning"
    daily_digest_hour: 8
    daily_digest_minute: 15

  # Adapter-Toggle
  adapters:
    jules: true                        # Phase 1
    seo: false                         # Phase 2
    codex: false                       # Phase 2
```

---

## Phase 6: Rollout

### Task 6.1: Phase 1 live stellen

**File:** `config/config.yaml` (LIVE)

```yaml
agent_review:
  enabled: true
  dry_run: false
  adapters:
    jules: true
    seo: false       # noch aus
    codex: false
```

**Restart + Monitoring.** Alle 106 bestehenden Jules-Tests muessen weiter funktionieren.
Regression-Check auf Live-Traffic 24h. **Commit:** keiner (config.yaml in .gitignore).

### Task 6.2: SEO-Adapter aktivieren

```yaml
  adapters:
    seo: true
```

Beobachten, ob SEO-PRs reviewt werden. Manuelle Verifikation der ersten drei SEO-Reviews.

### Task 6.3: Codex + Auto-Merge aktivieren

```yaml
  adapters:
    codex: true
  auto_merge:
    enabled: true
```

Stuendliches Monitoring der ersten 48 Stunden.

### Task 6.4: Dokumentation

- Modify: `CLAUDE.md` (neuer Module-Block)
- Modify: `.claude/rules/safety.md` (Multi-Agent-Regeln)
- Modify: `docs/API.md` (neue Endpoints)
- Create: `docs/adr/008-multi-agent-review-pipeline.md`

---

## Execution Summary

- **Tasks total:** ~25 ueber sechs Phasen
- **Aktive Entwicklungszeit:** ~20h geschaetzt
- **Neue Tests:** ~40
- **Bestehende Tests:** 106 Jules-Tests muessen durchgehend gruen bleiben
- **Rollback-Zeit:** 30 Sekunden (Config-Flag)

**Abbruch-Bedingungen:**

- Ein bestehender Jules-Test schlaegt fehl -> sofort stoppen, reverten, analysieren
- Regression-Test PR #123 rot -> hart abbrechen
- SEO-Agent-PR wird faelschlich als Jules erkannt -> Detector-Priority pruefen
