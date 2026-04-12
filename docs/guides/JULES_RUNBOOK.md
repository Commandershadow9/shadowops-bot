# Jules SecOps Workflow — Operational Runbook

## Uebersicht

Der Jules SecOps Workflow automatisiert Security-Fix-PRs via Google Jules mit Claude Opus Review.
Dieses Dokument beschreibt den taeglichen Betrieb, Monitoring und Troubleshooting.

## Quick Reference

| Aktion | Befehl / Ort |
|--------|-------------|
| Status pruefen | `curl http://127.0.0.1:8766/health/jules` |
| Logs beobachten | `journalctl -u shadowops-bot -f \| grep jules` |
| Deaktivieren | `config.yaml` → `jules_workflow.enabled: false` → Restart |
| Dry-Run-Modus | `config.yaml` → `jules_workflow.dry_run: true` → Restart |
| DB-State pruefen | `psql $DSN -c "SELECT repo, pr_number, status, iteration_count FROM jules_pr_reviews"` |
| Metriken (24h) | `psql $DSN -c "SELECT * FROM jules_daily_stats"` |
| Circuit-Breaker Reset | `redis-cli DEL jules:circuit:<repo>` |

## Normaler Betrieb

### Was passiert automatisch?

1. **ScanAgent** findet Code-Level-Finding (npm_audit, pip_audit, etc.)
2. **Issue** wird mit Label `jules` + `security` erstellt
3. **Jules** arbeitet am Issue, oeffnet PR
4. **Claude Opus** reviewt den PR strukturiert (BLOCKER/SUGGESTION/NIT)
5. **Approved** → Label `claude-approved` + Discord-Ping → Shadow merged manuell
6. **Revision** → Comment mit Blocker-Liste → Jules iteriert → max 5 Runden
7. **Escalation** → Discord-Alarm → Shadow muss manuell pruefen

### Discord-Kanaele

| Kanal | Was |
|-------|-----|
| security-ops | Neue Jules-PRs, Approvals, Iteration-Updates |
| alerts | Escalations (max_iterations, timeout, circuit_breaker) |
| ai-learning | Nightly-Batch Summary (Reviewed/Approved/Quality-Score) |

### Nightly-Batch (23:07)

- Klassifiziert abgeschlossene Reviews (approved_clean, false_positive, good_catch, missed_issue)
- Schreibt Beispiele in `jules_review_examples` fuer Few-Shot-Learning
- Discord-Post in ai-learning

## Monitoring

### Health-Endpoint

```bash
curl -s http://127.0.0.1:8766/health/jules | jq
```

**Gesunde Werte:**
- `active_reviews: 0-2` (selten mehr als 1 parallel)
- `escalated_24h: 0` (Eskalationen deuten auf Probleme)
- `stats_24h.total_reviews: 0-10` (abhaengig von Scan-Frequenz)

**Alarm-Werte:**
- `active_reviews > 3` → moeglicher Lock-Stau
- `escalated_24h > 3` → viele Findings die Jules nicht loesen kann
- `status: "error"` → DB-Verbindungsproblem

### Log-Patterns

```bash
# Erfolgreicher Review
[jules] Jules PR erkannt: ZERODOX#42 sha=abc1234 action=opened
[jules] review ok: verdict=approved blockers=0

# Loop-Schutz greift
[jules] ZERODOX#42 skip=blocked_trigger     # issue_comment blockiert
[jules] ZERODOX#42 skip=cooldown            # <5min seit letztem Review
[jules] ZERODOX#42 skip=already_reviewed    # gleicher SHA

# Eskalation
[jules] ZERODOX#42 ESCALATE max_iterations  # 5 Runden erreicht
[jules] Circuit Breaker OPEN ZERODOX        # 20 Reviews/h ueberschritten
```

## Troubleshooting

### Problem: Jules-PR wird nicht erkannt

**Symptom:** PR von Jules geoeffnet, kein Review gestartet, kein Log-Eintrag

**Pruefen:**
1. Hat der PR das Label `jules`? Oder ist der Author `google-labs-jules[bot]`?
2. Ist `jules_workflow.enabled: true` in config.yaml?
3. Ist der Webhook aktiv? `curl http://127.0.0.1:9090/` (sollte 405 oder aehnlich geben)
4. Kommt das Webhook-Event an? `journalctl -u shadowops-bot | grep "Received GitHub event"`

### Problem: Review startet aber kein Comment erscheint

**Symptom:** Log zeigt "review ok" aber kein PR-Comment

**Pruefen:**
1. `gh auth status` — ist gh CLI eingeloggt?
2. Hat der User `Commandershadow9` Schreibrechte im Repo?
3. Ist das Repo in `excluded_projects`?

### Problem: Endlos-Reviews (sollte nicht passieren!)

**Sofort-Massnahme:**
```bash
# Config: enabled: false → Restart
# Oder Redis Circuit-Breaker manuell setzen:
redis-cli SET jules:circuit:<repo> 999 EX 3600
```

**Post-Mortem:**
1. Welches Gate hat versagt? (Logs pruefen)
2. `test_jules_pr123_regression.py` laufen lassen
3. Commit-History des Mixin pruefen (wurde ein Gate entfernt?)

### Problem: Stale Lock (PR haengt in "reviewing")

```sql
-- Pruefen:
SELECT id, repo, pr_number, status, lock_acquired_at, lock_owner
FROM jules_pr_reviews WHERE status = 'reviewing';

-- Manuell freigeben (wenn aelter als 10min):
UPDATE jules_pr_reviews
SET status = 'revision_requested', lock_owner = NULL, lock_acquired_at = NULL
WHERE id = <ID>;
```

### Problem: Circuit-Breaker ist offen

```bash
# Pruefen:
redis-cli GET jules:circuit:<repo>

# Reset (nur nach Ursachen-Analyse!):
redis-cli DEL jules:circuit:<repo>
```

## Rollback

### Sofort (30 Sekunden)

```yaml
# config/config.yaml
jules_workflow:
  enabled: false
```

```bash
scripts/restart.sh
```

### Vollstaendig (Code-Revert)

```bash
# Letzten Jules-Commit finden und reverten
git log --oneline --grep="jules" | head -5
git revert <SHA>
scripts/restart.sh
```

## Konfigurationsaenderungen

Alle Aenderungen in `config/config.yaml` → `jules_workflow:` Block.
Bot-Restart noetig fuer Config-Reload.

| Parameter | Wann aendern? |
|-----------|-------------|
| `max_iterations` | Wenn 5 Runden nicht reichen fuer komplexe Fixes |
| `cooldown_seconds` | Wenn Jules sehr schnell committed (< 5min) |
| `max_hours_per_pr` | Wenn Jules komplexe PRs braucht die laenger dauern |
| `circuit_breaker.max_reviews_per_hour` | NIE erhoehen ohne Incident-Analyse! |
| `excluded_projects` | Wenn ein Projekt keine Jules-Fixes bekommen soll |
