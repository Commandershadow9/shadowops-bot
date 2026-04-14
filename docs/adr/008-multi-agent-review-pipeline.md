# ADR 008 — Multi-Agent Review Pipeline

**Status:** Accepted
**Datum:** 2026-04-14
**Kontext:** Jules SecOps Workflow (ADR 007) hat sich bewährt (17 PRs erfolgreich reviewed auf ZERODOX). Jetzt sollen **alle AI-Agenten** (Jules, SEO-Agent, Codex-Fixes vom SecurityScanAgent) durch denselben Review-Flow laufen.

## Entscheidung

**Adapter-Pattern** statt Vererbung oder If-Else-Kaskaden. Jeder Agent-Typ bekommt einen `AgentAdapter` mit:
- `detect(pr)` → Confidence 0.0-1.0
- `build_prompt(...)` → Review-Prompt mit agent-spezifischem Fokus
- `model_preference(pr, diff_len)` → `(primary, fallback)`
- `merge_policy(review, pr, project)` → `AUTO` / `MANUAL` / `BLOCKED`
- `discord_channel(verdict)` → Channel-Name
- `iteration_mention()` → Optional: `@mention` für Revision-Comments

`AgentDetector` wählt per Confidence-Ranking (Threshold 0.8) den richtigen Adapter.

## Architektur

```
PR-Webhook
    ↓
handle_jules_pr_event  (Jules-Detection)
    ↓
_jules_run_review  (Claude-Review mit Schema)
    ↓
verdict=approved?
    ↓ ja
_handle_approval_with_adapter
    ├─ AgentDetector.detect(pr) → Adapter
    ├─ adapter.merge_policy(review, pr, project)
    ├─ AUTO + _auto_merge_enabled?
    │   ├─ ja: gh pr merge --squash --auto
    │   │       └─ OutcomeTracker.record_auto_merge
    │   └─ nein: _jules_apply_approval (Label-Pfad, wie ADR 007)
```

**Parallele Queue** für ausgehende Jules-API-Sessions:
- Queue-Scheduler (60s Loop) — respektiert `100/24h` + `15 concurrent`
- Suggestions-Poller (8h Loop) — pollt Jules Top-Suggestions → Queue

**Outcome-Tracking:**
- Jeder Auto-Merge → `auto_merge_outcomes` Tabelle (checked_at=NULL)
- Stündlicher Check: Revert-Commit-Detection via `gh api`
- Daily-Digest (08:15) aggregiert letzte 24h + 7-Tage-Trend in `🧠-ai-learning`

## Alternativen erwogen

**A) Vererbung (`JulesReviewer` → `SeoReviewer`):** Abgelehnt. Diamond-Inheritance-Risiko, Mixin schon überladen.

**B) Großer If-Else-Baum:** Abgelehnt. Jeder neue Agent würde das Mixin weiter aufblähen (aktuell 600+ Zeilen).

**C) Strategy-Pattern via Config-Dicts:** Erwogen. Adapter-Pattern ist explizit typisierter — `merge_policy()` returnt Enum statt String, `build_prompt()` ist nicht serialisierbar.

## Konsequenzen

**Gut:**
- Adapter-Tests sind isoliert (keine DB, kein Netzwerk nötig für Detection/Policy)
- Neue Agent-Typen brauchen nur eine neue Klasse + Test-Suite — kein Mixin-Touch
- Confidence-Ranking erlaubt Ambiguitätsauflösung (SEO-Body + Jules-Label → SEO gewinnt bei 0.95 > 0.9)
- Safe-Default-Rollout: Config-Toggles pro Adapter (`jules: true`, `seo: false`, `codex: false`)

**Schlecht:**
- Mehr Files (~10 neue Module) statt einem dicken Mixin
- Detector läuft in Phase 1-4 parallel zur Legacy-Jules-Detection als Diagnostik — noch nicht der primäre Pfad
- `adapter.build_prompt()` wird aktuell nicht genutzt (nur `merge_policy()`) — der Review-Pfad ist weiter hard-coded Jules. Phase 7 (späterer Refactor) löst das

**Blast Radius Rollout:**
- Phase-1-Detection: Nur Logging, kein Verhaltensänderung → risikofrei
- Auto-Merge: Config-gated (`auto_merge.enabled: false` default, per-project `allowed` Flag). Rollback via Config-Flag < 30s
- Outcome-Tracking: Additive Tabelle, ändert nichts am Merge-Pfad

## Quantitativ

- **244 Unit-Tests** grün (Phase 1-5 + Jules-Regression PR #123)
- **~2400 neue Zeilen** Code + ~1200 Zeilen Tests
- **Zwei neue DB-Tabellen:** `agent_task_queue`, `auto_merge_outcomes`
- **Eine additive Spalte:** `jules_pr_reviews.agent_type` (default 'jules')
- **4 neue Scheduled-Tasks in `bot.py`:** Queue-Scheduler (60s), Suggestions-Poller (8h), Outcome-Check (60min), Daily-Digest (08:15)

## Rollout-Schritte

| Phase | Config | Beobachtung |
|-------|--------|-------------|
| 6.1 | `enabled: true`, nur Jules-Adapter | 24h Live-Traffic, Regression-Check |
| 6.2 | `adapters.seo: true` | Erste 3 SEO-Reviews manuell verifizieren |
| 6.3 | `adapters.codex: true`, `auto_merge.enabled: true` | 48h stündliches Monitoring |

**Abbruch-Bedingungen:**
- Jules-Test-Regression → sofort Config-Flag zurück auf `false`
- SEO-PR wird als Jules detected → Adapter-Confidence prüfen, ggf. Priorität anpassen
- Auto-Merge revertet in 24h → `auto_merge.projects.{name}.allowed: false` für betroffenes Projekt

## Referenzen

- Design-Doc: `docs/plans/2026-04-14-multi-agent-review-design.md`
- Implementierungsplan: `docs/plans/2026-04-14-multi-agent-review.md`
- Jules-Vorgänger: ADR 007
- Vorfall-Referenz: PR #123 (ZERODOX) — Loop-Schutz-Regression getestet
