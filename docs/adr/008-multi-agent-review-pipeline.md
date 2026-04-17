---
title: ADR 008 — Multi-Agent Review Pipeline
status: accepted
last_reviewed: 2026-04-15
owner: CommanderShadow9
---

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
- **adapter.build_prompt() wird jetzt im Review-Pfad genutzt** (Phase 6 Final): `ai_engine.review_pr()` akzeptiert `prompt_override` + `model_preference` Parameter, das Mixin reicht den Adapter durch
- **Vollständiges Multi-Agent-Routing:** Jules-Legacy-Pfad bleibt primär; SEO/Codex-PRs nehmen den Adapter-Pfad wenn `agent_review.enabled=true`
- **agent_type-Spalte** in `jules_pr_reviews` wird bei non-Jules-PRs automatisch gesetzt (Multi-Agent-Statistik)

**Schlecht:**
- Mehr Files (~13 neue Module) statt einem dicken Mixin
- Legacy-Jules-Detection-Check (`_jules_is_jules_pr`) bleibt parallel zum Detector erhalten — Doppel-Pfad als Safety-Net, sollte in V2 auf Detector-Only reduziert werden

**Blast Radius Rollout:**
- Phase-1-Detection: Nur Logging, kein Verhaltensänderung → risikofrei
- Auto-Merge: Config-gated (`auto_merge.enabled: false` default, per-project `allowed` Flag). Rollback via Config-Flag < 30s
- Outcome-Tracking: Additive Tabelle, ändert nichts am Merge-Pfad

## Erweiterung 2026-04-14 — SecurityScanAgent-Delegation

Nach erfolgreichem E2E-Test (PR #141, 20s Review-Zeit) wurde die
Autonomie-Schleife geschlossen: **SecurityScanAgent delegiert
Code-Security-Findings direkt in die agent_task_queue** statt GitHub-Issues
zu öffnen.

**Flow:** ScanAgent findet XSS in ZERODOX → `queue.enqueue(source='scan_agent')`
→ Queue-Scheduler (60s) startet Jules-Session → Jules öffnet PR → Webhook
→ Multi-Agent-Review-Pipeline → Claude-Review → Label.

**Delegation-Kriterien (4-stufige Safety):**
1. `agent_review.enabled=true` (Feature-Flag)
2. `finding.category` ∈ `{code_security, xss, sql_injection, auth, ...}`
3. `finding.affected_project` ∈ Jules-bekannten Projekten
4. `finding.affected_files` nicht leer

**Infrastruktur-Findings** (docker, config, permissions, network_exposure,
backup) bleiben bewusst im GitHub-Issue-Pfad — diese brauchen OS-Zugriff,
keine Code-Änderung.

**Safety-Default:** Wenn `agent_review.enabled=false` (aktueller Default),
läuft der GitHub-Issue-Pfad wie bisher. Keine Behavior-Change ohne
explizite Config-Aktivierung.

## Quantitativ

- **296 Unit-Tests** grün (Phase 1-6 + Jules-Regression PR #123 + vertiefte Adapter-Integration + ScanAgent-Delegation)
- **~3000 neue Zeilen** Code + ~1700 Zeilen Tests
- **Zwei neue DB-Tabellen:** `agent_task_queue`, `auto_merge_outcomes`
- **Eine additive Spalte:** `jules_pr_reviews.agent_type` (default 'jules', wird für non-Jules PRs gesetzt)
- **4 neue Scheduled-Tasks in `bot.py`:** Queue-Scheduler (60s), Suggestions-Poller (8h), Outcome-Check (60min), Daily-Digest (08:15)
- **2 erweiterte API-Signaturen:** `ai_engine.review_pr(prompt_override=..., model_preference=...)`, `_jules_run_review(adapter=...)`
- **2 neue ScanAgent-Helpers:** `_should_delegate_to_jules()`, `_enqueue_jules_fix()` mit Lazy-Property-Accessoren

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

- Design-Doc: `docs/design/multi-agent-review.md`
- Implementierungsplan: `docs/archive/INDEX.md` (archiviert — letzter Stand per `git show`)
- Jules-Vorgänger: ADR 007
- Vorfall-Referenz: PR #123 (ZERODOX) — Loop-Schutz-Regression getestet
