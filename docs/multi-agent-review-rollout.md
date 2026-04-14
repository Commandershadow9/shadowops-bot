# Multi-Agent Review Pipeline — Rollout-Guide

**Voraussetzungen:**
- Feature-Branch `feature/multi-agent-review` in main gemerged
- Bot-Restart via `scripts/restart.sh` mind. 1x erfolgreich nach Merge
- Schema live: `agent_task_queue`, `auto_merge_outcomes`, `jules_pr_reviews.agent_type` vorhanden

## Stufe 1 — Jules-Only (24h Monitoring)

**Aktion:** `config/config.yaml` editieren

```yaml
agent_review:
  enabled: true
  dry_run: false
  adapters:
    jules: true
    seo: false
    codex: false
  auto_merge:
    enabled: false      # explizit aus — erst Phase 3
```

**Restart:**
```bash
scripts/restart.sh --logs
```

**Check nach Start (Logs):**
```
[agent-review] Detector aktiv mit 1 Adapter(n): ['jules']
[agent-review] startup ok (queue, api_client=True, poller=False)
🛡️ Agent-Review Queue-Scheduler gestartet (60s Loop)
🛡️ Agent-Review Outcome-Check gestartet (stuendlich)
🛡️ Agent-Review Daily-Digest gestartet (taeglich 08:15)
```

**Monitoring 24h:**
- Jules-PRs funktionieren weiter wie vor dem Rollout
- `agent-detector` Logs fuer jeden PR — alle sollten `→ jules` anzeigen
- Kein `agent-queue` Error im Log
- Erste Daily-Digest postet um 08:15 in `🧠-ai-learning`

**Abbruch-Kriterium:**
- Jules-Test-Regression → `agent_review.enabled: false`, Restart
- Detector erkennt einen Jules-PR NICHT (sollte `→ jules` loggen, tut es nicht) → Issue checken

## Stufe 2 — SEO-Adapter dazu

**Nach 24h erfolgreichem Jules-Only Betrieb:**

```yaml
agent_review:
  adapters:
    jules: true
    seo: true         # <- neu
    codex: false
```

Restart.

**Check:**
- Log: `Detector aktiv mit 2 Adapter(n): ['jules', 'seo']`
- Erster SEO-PR (vom Cron-Agent): Detector loggt `→ seo`, Claude-Review laeuft mit SEO-Prompt
- Review-Embed in `seo-fixes` Channel

**Manuelle Verifikation erste 3 SEO-Reviews:**
1. SEO-Prompt korrekt ausgewaehlt? (Log: prompt enthält "Multi-Domain-Agent")
2. Scope-Check strikt? (blockert bei package.json-Aenderung)
3. Merge-Policy korrekt? (alle sollten MANUAL returnen solange auto_merge.enabled=false)

## Stufe 3 — Codex + Auto-Merge aktivieren

**Nach 48h erfolgreichem 2-Adapter-Betrieb:**

```yaml
agent_review:
  adapters:
    jules: true
    seo: true
    codex: true       # <- neu
  auto_merge:
    enabled: true     # <- neu, scharf
    projects:
      ZERODOX:            { allowed: true, trivial_threshold: 100 }
      GuildScout:         { allowed: true, trivial_threshold: 150 }
      mayday-sim:         { allowed: true, trivial_threshold: 500 }
      shadowops-bot:      { allowed: true, trivial_threshold: 50 }
      sicherheitsdienst:  { allowed: false }
      ai-agent-framework: { allowed: true, trivial_threshold: 100 }
```

Restart.

**Stündliches Monitoring erste 48h:**
- `auto_merge_outcomes` Tabelle fuellen sich mit Eintraegen
- Daily-Digest zeigt die ersten Auto-Merges (erwartet: Tests-only Jules-PRs + Content-Only SEO-PRs)
- KEINE Reverts in den ersten 24h (wenn doch: `auto_merge.enabled: false` und Pipeline analysieren)

**Check-Query fuer DB:**
```sql
SELECT agent_type, rule_matched, COUNT(*), SUM(CASE WHEN reverted THEN 1 ELSE 0 END) AS reverted
FROM auto_merge_outcomes
WHERE merged_at > now() - interval '7 days'
GROUP BY agent_type, rule_matched
ORDER BY reverted DESC, count DESC;
```

## Rollback-Sequenz

**Level 1 — Auto-Merge stoppen:**
```yaml
agent_review:
  auto_merge:
    enabled: false
```
Restart. Review-Flow bleibt aktiv, nur Label-Pfad statt Auto-Merge.

**Level 2 — Pipeline deaktivieren:**
```yaml
agent_review:
  enabled: false
```
Restart. Legacy-Jules-Pfad ist wieder einziger aktiver Pfad.

**Level 3 — Sofort-Fallback ohne Restart:**
- Bot weiterlaufen lassen
- In DB: `UPDATE agent_task_queue SET status='cancelled' WHERE status='queued';`
- In Redis: `DEL circuit_breaker:*` (falls Scheduler aus Circuit-Breaker-Pause rauskommen soll)

## Smoke-Test nach Deploy

```bash
# 1. Bot ist gestartet
systemctl status shadowops-bot

# 2. Tasks laufen
journalctl -u shadowops-bot -n 200 | grep -E "agent-(queue|suggestions|outcome|digest)"

# 3. Schema ist live
psql -h 127.0.0.1 -p 5433 -U postgres -d security_analyst -c \
  "\dt agent_task_queue auto_merge_outcomes"

# 4. Queue ist accessible
psql -h 127.0.0.1 -p 5433 -U postgres -d security_analyst -c \
  "SELECT status, COUNT(*) FROM agent_task_queue GROUP BY status;"

# 5. Detector funktioniert (aktueller Log)
journalctl -u shadowops-bot --since "10 minutes ago" | grep agent-detector
```

## Testing vor Rollout

```bash
cd /home/cmdshadow/shadowops-bot
source .venv/bin/activate

# Komplette Regression
pytest tests/unit/agent_review/ tests/unit/test_jules_pr123_regression.py -x

# Erwartet: 244 passed
```

## Ansprechpartner bei Incidents

- Config-Aenderungen: `config/config.yaml` (NICHT in Git, Backup-Key im Passwort-Manager)
- DB-Zugriff: `postgres-guildscout` MCP auf Port 5433, DB `security_analyst`
- Logs: `journalctl -u shadowops-bot -f`
- Design-Doc: `docs/plans/2026-04-14-multi-agent-review-design.md`
- ADR: `docs/adr/008-multi-agent-review-pipeline.md`
