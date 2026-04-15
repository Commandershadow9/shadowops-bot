---
title: Jules Workflow — Overview
status: active
version: v1
last_reviewed: 2026-04-15
owner: CommanderShadow9
related:
  - ../../adr/007-jules-secops-workflow.md
  - ../../design/jules-workflow.md
---

# Jules Workflow — Overview

Siehe auch: [detection.md](detection.md), [review-pipeline.md](review-pipeline.md), [loop-protection.md](loop-protection.md), [state-and-learning.md](state-and-learning.md), [integration.md](integration.md).

## Phase 0: Vorbereitung (kein Code, nur Verständnis)

**Lies vor dem ersten Task:**
1. Design-Doc komplett: `docs/design/jules-workflow.md`
2. Bestehender Handler-Flow: `src/integrations/github_integration/core.py` + `event_handlers_mixin.py`
3. Bestehende DB-Abstraktion: `src/integrations/security_engine/db.py`
4. Config-Ladelogik: `src/utils/config.py` (wie `self.config.foo.bar` aufgelöst wird)
5. PR #123 Kommentare (in Design-Doc Anhang A zusammengefasst) — verstehe *warum* jeder Gate existiert

---

## Phase 15: Rollout

### Task 15.1: Config-Vorbereitung — `enabled: false`, `dry_run: true`

**Files:**
- Modify: `config/config.yaml` (LIVE, NICHT in Git)

**Step 1: Config manuell anpassen**

```yaml
jules_workflow:
  enabled: true
  dry_run: true       # Phase 4 Testing
  max_iterations: 5
  cooldown_seconds: 300
  max_hours_per_pr: 2
  circuit_breaker:
    max_reviews_per_hour: 20
    pause_duration_seconds: 3600
  excluded_projects:
    - sicherheitsdienst
  max_diff_chars: 8000
  few_shot_examples: 3
  project_knowledge_limit: 10
  token_cap_per_pr: 50000
  notification_channel: "security-ops"
  escalation_channel: "alerts"
  role_ping_on_escalation: "@Shadow"
```

**Step 2: Bot restart über `scripts/restart.sh`**

```bash
cd /home/cmdshadow/shadowops-bot
scripts/restart.sh --logs
```

Beobachte Startup-Log auf:
- `[jules] connected to security_analyst DB`
- `[jules] connected to agent_learning DB`
- Keine `ImportError` oder `AttributeError`
- Stale-Lock-Recovery-Message (wenn vorhanden)

**Step 3: Health-Check**

```bash
curl -s http://127.0.0.1:8766/health/jules | jq
```

Erwartet: `{"enabled": true, "status": "healthy", "active_reviews": 0, ...}`.

**Step 4: Kein Commit** — config.yaml ist nicht in Git.

---

### Task 15.2: Dry-Run Phase — 24h Live-Events beobachten

**Keine Code-Änderung, nur Beobachtung.**

**Step 1: Logs monitoren**

```bash
journalctl -u shadowops-bot -f | grep -i "jules"
```

Für 24 Stunden beobachten. Erwartete Log-Muster:
- `[jules] Detected Jules PR ... action=opened` bei neuen PRs
- `[jules] DRY-RUN ...` statt echten AI-Calls
- Keine echten PR-Comments
- Keine DB-Writes außer `ensure_pending`

**Step 2: DB-Check**

```bash
psql "$SECURITY_ANALYST_DB_URL" -c "SELECT repo, pr_number, status, iteration_count FROM jules_pr_reviews;"
```

Alle Rows sollten `status='revision_requested'` oder `'pending'` haben, `iteration_count=0`.

**Step 3: Wenn 24h OK → Commit einer Notiz**

```bash
echo "2026-04-12 Dry-Run 24h OK: N Rows in jules_pr_reviews, 0 Errors" >> docs/rollout-notes.md
git add docs/rollout-notes.md
git commit -m "docs: Jules Dry-Run Phase 24h abgeschlossen"
```

---

### Task 15.3: Live-Schaltung für ZERODOX

**Files:**
- Modify: `config/config.yaml`

**Step 1: `dry_run: false`**

```yaml
jules_workflow:
  enabled: true
  dry_run: false   # war true
  ...
```

**Step 2: Restart**

```bash
scripts/restart.sh --logs
```

**Step 3: Beobachtung**

Der nächste Jules-PR in ZERODOX wird live reviewt. Erwartete Indikatoren:
- 1 Comment im PR (nicht 10+)
- Label `claude-approved` bei Approval
- Discord-Ping im `security-ops` Channel
- `iteration_count` in DB steigt korrekt

**Step 4: Wenn Problem → Sofort-Rollback**

```bash
# In config/config.yaml:
# enabled: false
# scripts/restart.sh
```

~30 Sekunden.

**Step 5: Wenn 48h OK → Rollout auf weitere Repos**

Keine Änderung nötig — `excluded_projects` steuert das. Standardmäßig sind alle Projekte außer `sicherheitsdienst` aktiv.

---

### Task 15.4: Gemini-Stash final löschen (nach erfolgreichem Rollout)

**Voraussetzung:** Phase 15.3 läuft 1 Woche ohne Incident.

```bash
git stash list | grep "gemini jules"
# Erwartet: stash@{0}: On main: gemini jules integration (buggy, replaced by 2026-04-11 design)
git stash drop stash@{0}
```

Der Gemini-Code ist danach final weg.

---

## Phase 16: Dokumentation

### Task 16.1: CLAUDE.md erweitern

**Files:**
- Modify: `CLAUDE.md` (root des shadowops-bot repos)

**Step 1: Neuer Abschnitt in der Integrations-Tabelle**

Suche in `CLAUDE.md` den Block "Einzelne Module" unter `src/integrations/` und füge hinzu:

```markdown
| `github_integration/jules_workflow_mixin.py` | Jules SecOps Workflow — PR-Handler, Gate-Pipeline (7 Schichten), Review-Orchestrierung |
| `github_integration/jules_state.py` | asyncpg-Layer für security_analyst.jules_pr_reviews, atomic Lock-Claim |
| `github_integration/jules_learning.py` | Few-Shot + Projekt-Knowledge Loader aus agent_learning DB |
| `github_integration/jules_review_prompt.py` | Claude-Prompt-Builder für strukturierte PR-Reviews |
| `github_integration/jules_gates.py` | Pure Loop-Schutz-Gates (Trigger-Whitelist, Cooldown, Cap, Circuit-Breaker) |
| `github_integration/jules_comment.py` | PR-Comment-Body-Builder + Self-Filter-Marker |
| `github_integration/jules_batch.py` | Nightly Outcome-Klassifizierung + jules_review_examples Update |
```

**Step 2: Architektur-Abschnitt**

Füge im Abschnitt "Architektur-Entscheidungen" hinzu:

```markdown
### Jules SecOps Workflow (seit 2026-04-11)
- **Hybrid-Fix:** ScanAgent fixt Server-Hardening selbst, delegiert Code-Fixes an Jules via GitHub-Issue mit `jules` Label
- **Claude-Review:** Strukturiert (BLOCKER/SUGGESTION/NIT), Schema-validiert, deterministischer Verdict
- **Loop-Schutz:** 7 Schichten (Trigger-Whitelist, SHA-Dedupe, Cooldown, Iteration-Cap 5, Circuit-Breaker 20/h, Time-Cap 2h, Single-Comment-Edit)
- **State:** `security_analyst.jules_pr_reviews` mit atomic Lock-Claim, Stale-Lock-Recovery nach 10min
- **Learning:** `agent_learning.jules_review_examples` + `agent_knowledge` (Few-Shot + Projekt-Konventionen), Nightly-Batch klassifiziert Outcomes
- **Rollback:** Config-Flag `jules_workflow.enabled: false` → ~30s
- **Design-Doc:** `docs/design/jules-workflow.md`
- **Implementation-Plan:** `docs/plans/2026-04-11-jules-secops-workflow.md`
- **Vorfall-Referenz:** PR #123 (ZERODOX) — 31 Kommentare Loop durch `issue_comment` Re-Trigger; siehe Design-Doc Anhang A
```

**Step 3: Safety-Rules erweitern**

In `.claude/rules/safety.md` neuer Block:

```markdown
## Jules SecOps Workflow (seit 2026-04-11)
- **NIEMALS `issue_comment` Events für Auto-Reviews whitelisten** — das war die PR #123 Hauptursache
- **NIEMALS `_validate_ai_output` aus Jules-Pipeline entfernen** — schützt vor halluzinierten Blockern
- **Single-Comment-Edit Strategie ist Pflicht** — neuer Comment pro Iteration triggert Webhook-Loop
- **`compute_verdict` ist deterministisch, nicht AI-überschreibbar** — schützt vor Confidence-Oszillation
- **max_iterations: 5 und max_hours_per_pr: 2 sind harte Limits** — bei Änderung Design-Doc Anhang A re-reviewen
- **Circuit-Breaker 20/h pro Repo NIE erhöhen** ohne Incident-Analyse
- **Stale-Lock-Timeout 10min nicht verkürzen** — manche AI-Calls brauchen 5-8 Minuten
- **Bei Jules-Workflow-Änderungen IMMER `test_jules_pr123_regression.py` laufen lassen**
```

**Step 4: Commit**

```bash
git add CLAUDE.md .claude/rules/safety.md
git commit -m "docs: Jules SecOps Workflow — CLAUDE.md + safety.md Integration"
```

---

## Execution Summary

**Total Tasks:** 30 Tasks über 16 Phasen.

**Geschätzte Dauer:**
- Phase 1-3 (DB + State): 2h
- Phase 4-6 (Schema + Prompt + AI): 2h
- Phase 7-8 (Gates + Mixin): 2h
- Phase 9-11 (Comment + Escalation + Wiring): 3h
- Phase 12-14 (ScanAgent + Health + Learning): 2h
- Phase 15 (Rollout): 3 Tage (nicht aktiv-Zeit)
- Phase 16 (Docs): 30min

**Aktive Entwicklungszeit:** ~11h. Rollout dauert zusätzlich 3-5 Tage Beobachtung.

**Abbruch-Kriterien:**
- Wenn Tests in Phase 1-14 auf mehr als 2 Iterationen fehlschlagen: Design-Doc erneut lesen, Gate-Reihenfolge prüfen
- Wenn Dry-Run in Phase 15.2 auch nur 1 echten PR-Comment postet: SOFORT `enabled: false`
- Wenn Live-Phase 15.3 mehr als 3 Reviews pro PR erzeugt: Circuit-Breaker triggert — Post-Mortem schreiben, nicht einfach Limit erhöhen

**Rollback-Garantie:**
- Code-Änderungen: `git stash` (Gemini) ist noch da, plus alle neuen Änderungen sind revertierbar via `git revert <commit>`
- Config-Änderungen: `jules_workflow.enabled: false` → ~30s
- DB-Änderungen: Tabellen sind `CREATE TABLE IF NOT EXISTS`, optional `DROP TABLE jules_pr_reviews; DROP TABLE jules_review_examples;`

---

**Plan complete and saved to `docs/plans/2026-04-11-jules-secops-workflow.md`.**
