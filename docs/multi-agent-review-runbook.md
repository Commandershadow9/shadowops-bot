# Multi-Agent Review — Operator Runbook

**Zielgruppe:** On-Call Engineer, SRE, Platform-Team
**Voraussetzung:** VPN-Zugang, `gh` CLI authentifiziert, Zugriff auf `security_analyst` DB

Dieses Runbook ergänzt den Rollout-Guide (`docs/multi-agent-review-rollout.md`). Der Rollout-Guide beschreibt **wie** aktiviert wird — dieses Runbook beschreibt **was zu tun ist wenn etwas schiefgeht**.

---

## 1. Health-Checks (tägliche Kontrolle)

### 1.1 Pipeline lebt?

```bash
# Sollte alle 4 Tasks zeigen
journalctl -u shadowops-bot --since "2h ago" | \
  grep -E "Agent-Review.*gestartet|agent-(queue|suggestions|outcome|digest)"
```

Erwartete Zeilen:
- `Agent-Review Queue-Scheduler gestartet (60s Loop)`
- `Agent-Review Suggestions-Poller gestartet (alle 8h)`
- `Agent-Review Outcome-Check gestartet (stuendlich)`
- `Agent-Review Daily-Digest gestartet (taeglich 08:15)`

### 1.2 Queue nicht verstopft?

```sql
-- Im postgres-guildscout MCP oder psql
SELECT status, COUNT(*) AS cnt, MAX(age(now(), created_at)) AS oldest
FROM agent_task_queue
GROUP BY status;
```

**Alarmzone:**
- `queued > 50` → Scheduler läuft nicht oder Jules-API down
- `oldest (queued) > 1h` → Scheduler blockiert
- `failed > 10` in 24h → Systematisches Problem

### 1.3 Auto-Merge-Revert-Rate prüft

```sql
SELECT agent_type, rule_matched,
       COUNT(*) AS total,
       SUM(CASE WHEN reverted THEN 1 ELSE 0 END) AS reverted,
       ROUND(100.0 * SUM(CASE WHEN reverted THEN 1 ELSE 0 END) / COUNT(*), 1) AS rate_pct
FROM auto_merge_outcomes
WHERE merged_at > now() - interval '7 days' AND checked_at IS NOT NULL
GROUP BY agent_type, rule_matched
HAVING COUNT(*) >= 5
ORDER BY rate_pct DESC;
```

**Alarmzone:** Rate ≥ 20% für eine Rule → `auto_merge.projects.{name}.allowed: false` setzen für das betroffene Projekt, Incident-Report erstellen.

---

## 2. Incident-Playbooks

### 2.1 Runaway Auto-Merge (Revert-Flut)

**Symptom:** Mehr als 3 Auto-Merges in 24h wurden revertet.

**Sofort-Aktion:**
```yaml
# config/config.yaml
agent_review:
  auto_merge:
    enabled: false
```
```bash
scripts/restart.sh --logs
```

**Forensik:**
```sql
SELECT id, agent_type, project, repo, pr_number, rule_matched, merged_at, reverted_at
FROM auto_merge_outcomes
WHERE reverted = true AND reverted_at > now() - interval '48 hours'
ORDER BY reverted_at DESC;
```

**Root-Cause-Check:**
1. Gibt es ein gemeinsames `rule_matched`? → Adapter-`merge_policy` zu lax
2. Ein einzelnes Projekt betroffen? → `auto_merge.projects.{name}.allowed: false`
3. Ein einzelner Agent? → `adapters.{agent}: false` bis Fix deployed

### 2.2 Queue-Scheduler Crashloop

**Symptom:** `[agent-queue] scheduler crashed` wiederholt in Logs.

**Prüfen:**
```bash
journalctl -u shadowops-bot -n 200 | grep -A 20 "agent-queue.*crashed"
```

**Häufige Ursachen:**
- `JulesAPIError: http_401` → API-Key ungültig, prüfe `jules_workflow.api_key`
- `asyncpg.exceptions.ConnectionDoesNotExistError` → DB-Pool tot, Bot-Restart
- `JulesAPIError: rate_limited` → Normal bei 100/24h-Limit, Scheduler retryt selbstständig

**Sofort-Aktion wenn anhaltend:**
```yaml
agent_review:
  enabled: false   # deaktiviert komplette Pipeline
```

### 2.3 Phantom-PRs (Detector matcht zu viel)

**Symptom:** Normale User-PRs werden als SEO/Codex erkannt.

**Prüfen:**
```bash
journalctl -u shadowops-bot --since "24h ago" | grep "agent-detector" | sort | uniq -c | sort -rn | head
```

Confidence-Scores in Log:
```bash
journalctl -u shadowops-bot | grep -E "agent-detector.*→ (seo|codex)" | tail -20
```

**Wenn False-Positive bestätigt:**
1. `adapters.{betroffener}: false` setzen — sofort stoppt der falsche Adapter
2. Code-Fix: Detection-Muster im Adapter verschärfen (z.B. branch-Prefix strikter)
3. Re-deploy, Re-enable

### 2.4 Daily-Digest fehlt

**Symptom:** 08:15 keine Message in `🧠-ai-learning`.

**Checks:**
```bash
journalctl -u shadowops-bot --since "today 08:00" | grep agent-digest
```

Mögliche Fehler:
- `discord_logger is None` → Bot war 08:15 offline (Deploy-Zeitpunkt?)
- DB-Query-Fehler → teilweise gerendert oder gar nichts

**Manueller Trigger** (bevor nächster 08:15):
```python
# In Python-Shell mit Bot-Context:
await bot.agent_daily_digest_task.coro(bot)
```

---

## 3. Rollback-Sequenz

Gestaffelt nach Impact. Gehe Stufe für Stufe, nicht überspringen:

### Level 1 — Auto-Merge stoppen (niedrigster Impact)

```yaml
agent_review:
  auto_merge:
    enabled: false
```
- Review-Flow läuft weiter
- Claude reviewed weiter, aber setzt nur Label statt zu mergen
- Rollout-Zeit: ~30s (nächster Bot-Restart)

### Level 2 — Einzelnen Adapter deaktivieren

```yaml
agent_review:
  adapters:
    seo: false    # oder codex: false
```
- Jules und die anderen Adapter laufen weiter
- Rollout-Zeit: ~30s

### Level 3 — Agent-Review komplett aus

```yaml
agent_review:
  enabled: false
```
- Legacy-Jules-Pfad ist einziger aktiver Pfad
- Queue-Scheduler + Poller + Outcome-Check + Digest stoppen alle
- Bestehende Queued-Tasks bleiben erhalten (status='queued'), nicht verloren
- Rollout-Zeit: ~30s

### Level 4 — Queue manuell drainieren (nur wenn nötig)

```sql
-- Alle queued Tasks cancellen
UPDATE agent_task_queue SET status='cancelled', failure_reason='manual_drain', updated_at=now()
WHERE status='queued';
```

### Level 5 — Pipeline-Daten löschen (nur nach Major-Incident, mit Backup!)

```sql
-- BACKUP FIRST!
\copy agent_task_queue TO '/tmp/queue_backup.csv' CSV HEADER;
\copy auto_merge_outcomes TO '/tmp/outcomes_backup.csv' CSV HEADER;

DELETE FROM agent_task_queue WHERE status IN ('failed','cancelled');
-- auto_merge_outcomes NIEMALS löschen — Audit-Trail muss erhalten bleiben
```

---

## 4. Monitoring-Queries (schnelle Diagnose)

### Aktive Adapter

```bash
journalctl -u shadowops-bot --since "today" | grep -oE "Detector aktiv mit [0-9]+ Adapter.n.: \[.*\]" | tail -1
```

### Reviews letzte 24h nach Agent

```sql
-- verdict kommt aus last_review_json (kein eigener Column)
SELECT agent_type,
       last_review_json->>'verdict' AS verdict,
       COUNT(*)
FROM jules_pr_reviews
WHERE updated_at > now() - interval '24 hours'
  AND last_review_json IS NOT NULL
GROUP BY agent_type, last_review_json->>'verdict'
ORDER BY agent_type, verdict;
```

### Pipeline-Metriken (letzte Woche)

```sql
WITH merges AS (
  SELECT agent_type,
         COUNT(*) AS total_merges,
         SUM(CASE WHEN reverted THEN 1 ELSE 0 END) AS reverts,
         SUM(CASE WHEN ci_passed_after_merge THEN 1 ELSE 0 END) AS ci_ok
  FROM auto_merge_outcomes
  WHERE merged_at > now() - interval '7 days' AND checked_at IS NOT NULL
  GROUP BY agent_type
)
SELECT agent_type, total_merges, reverts, ci_ok,
       ROUND(100.0 * reverts / NULLIF(total_merges, 0), 1) AS revert_pct
FROM merges;
```

### Queue-Throughput (letzte 24h)

```sql
SELECT
  source,
  status,
  COUNT(*) AS cnt,
  AVG(EXTRACT(EPOCH FROM (released_at - created_at))) AS avg_queue_sec
FROM agent_task_queue
WHERE created_at > now() - interval '24 hours'
GROUP BY source, status;
```

### ScanAgent-Delegation-Rate (letzte 7 Tage)

```sql
-- Wie viele Findings wurden an Jules delegiert vs. als GitHub-Issue gepostet?
SELECT
  DATE(created_at) AS day,
  COUNT(*) FILTER (WHERE source='scan_agent') AS jules_delegated,
  COUNT(*) FILTER (WHERE source='manual') AS manual_tasks,
  COUNT(*) FILTER (WHERE source='jules_suggestion') AS jules_suggestions
FROM agent_task_queue
WHERE created_at > now() - interval '7 days'
GROUP BY DATE(created_at)
ORDER BY day DESC;
```

### ScanAgent-delegierte Tasks: Status-Verteilung

```sql
SELECT
  status,
  COUNT(*) AS cnt,
  MIN(created_at) AS oldest,
  MAX(updated_at) AS newest
FROM agent_task_queue
WHERE source = 'scan_agent'
GROUP BY status
ORDER BY cnt DESC;
```

**Alarmzone:** `queued > 10` und `oldest > 2h` → Scheduler haengt oder Jules-API down.

---

## 5. Konfigurations-Referenz

### Pflicht-Keys in `config/config.yaml`

```yaml
agent_review:
  enabled: true/false         # Master-Switch
  adapters:
    jules: true/false
    seo: true/false
    codex: true/false
  auto_merge:
    enabled: true/false       # SEPARATER Switch
    projects:
      <name>: { allowed: true/false, trivial_threshold: N }

jules_workflow:
  api_key: "jules-..."        # NIE in Git. Nur config.yaml oder ENV var.
  enabled: true
```

### Opt-in Features

```yaml
agent_review:
  suggestions_poller:
    enabled: false            # 3x täglich Jules Top-Suggestions pollen
    repos: [...]
  claude_review:
    max_concurrent_calls: 8   # Bot-Resource-Protection (Ressourcen-Cap)
  jules_queue:
    max_new_sessions_per_24h: 100   # Jules Plan-Limit
    max_concurrent_sessions: 15
```

---

## 6. Eskalations-Pfad

1. **Monitoring-Alert** → On-Call Engineer (Discord `#✋-approvals` Ping)
2. Runbook durchgehen → 95% der Fälle lösbar
3. **Complex Case** → Platform-Lead (@CommanderShadow)
4. **Security-Incident** (Auto-Merge hat Secret veröffentlicht, Backdoor gemerged, etc.) → Sofort-Rollback Level 3 + Incident-Report

---

## 7. Referenzen

- **ADR:** [`docs/adr/008-multi-agent-review-pipeline.md`](adr/008-multi-agent-review-pipeline.md)
- **Design-Doc:** [`docs/plans/2026-04-14-multi-agent-review-design.md`](plans/2026-04-14-multi-agent-review-design.md)
- **Implementierungsplan:** [`docs/plans/2026-04-14-multi-agent-review.md`](plans/2026-04-14-multi-agent-review.md)
- **Rollout-Guide:** [`docs/multi-agent-review-rollout.md`](multi-agent-review-rollout.md)
- **Safety-Rules:** [`.claude/rules/safety.md`](../.claude/rules/safety.md#multi-agent-review-pipeline-seit-2026-04-14)
- **Jules-Vorgänger:** [`docs/adr/007-jules-secops-workflow.md`](adr/007-jules-secops-workflow.md)
