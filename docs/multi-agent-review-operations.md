# Multi-Agent Review — Operations Guide

**Zielgruppe:** Dich (Shadow) im täglichen Betrieb. Kein Engineering-Ticket, sondern die Routine.

Die Pipeline reviewt PRs **projekt-übergreifend**: ZERODOX, GuildScout, shadowops-bot, ai-agent-framework, mayday-sim. Für jedes Projekt mit GitHub-Webhook-Verbindung zum Bot.

---

## Projekte im System

| Projekt | Webhook | Jules-Whitelist | Status |
|---------|---------|-----------------|--------|
| **ZERODOX** | ✅ | ✅ | Voll aktiv mit Config-Flag |
| **GuildScout** | ✅ | ✅ | Voll aktiv mit Config-Flag |
| **shadowops-bot** | ✅ | ✅ | Voll aktiv mit Config-Flag |
| **ai-agent-framework** | ❌ | ✅ | Webhook einmalig anlegen |
| **mayday-sim** | ❌ | ✅ | Webhook einmalig anlegen |

**Webhook für mayday-sim später aktivieren** (wenn du freigibst):

```bash
gh api repos/Commandershadow9/mayday-sim/hooks \
  --method POST \
  -f 'name=web' \
  -f 'active=true' \
  -F 'events[]=pull_request' \
  -F 'events[]=push' \
  -F 'events[]=release' \
  -f 'config[url]=http://37.114.53.56:9090/webhook' \
  -f 'config[content_type]=json' \
  -f 'config[secret]=<github_webhook_secret aus config.yaml>'
```

Nach dem Setup reviewt der Bot mayday-sim-PRs automatisch — kein Neustart nötig.

---

## Ein Tag im autonomen Betrieb

```
06:00  Daily Health-Check (bestehend)
08:15  ★ Agent-Review Daily-Digest → 🧠-ai-learning Channel
10:33  ScanAgent findet XSS → Queue → Scheduler → Jules
10:42  Jules-PR erscheint → Bot reviewt in ~20s → Label
11:00  Stündlicher Outcome-Check (24h-Revert-Detection)
18:00  Nächste ScanAgent-Session
22:00  Daily-Patch-Notes-Release (wenn >=15 Commits)
23:07  Jules Nightly-Batch → 🧠-ai-learning (Learning-Update)
```

**Deine aktive Zeit: ~5min pro Tag** für den Morning-Digest + Freitags der Weekly-Check.

---

## Review-Routinen

### Täglich: Daily-Digest lesen (1 min, 08:15)

Post erscheint automatisch in **🧠-ai-learning**. Du siehst auf einen Blick:

- **Reviews** letzte 24h: `jules 8✅ / 2🟡`, `seo 3✅`
- **Auto-Merges** + Reverts (sollte 0 sein)
- **Pending Manual-Merges** (wartende approved PRs)
- **Queue-Status**
- **7-Tage Revert-Trend**

**Entscheidungsmatrix:**
- Alles grün → nichts tun
- Reverts > 0 → betroffener Projekt/Rule ins Auge nehmen
- Pending Manual-Merges > 10 → Team-Kapazität prüfen

### Wöchentlich: Weekly-Check (5 min, Fr oder Mo)

```bash
cd /home/cmdshadow/shadowops-bot
scripts/weekly_review_check.sh
```

**Ampel-System:** Script zeigt warnings mit gelb/rot wenn was schief läuft. Exit-Code 0 = alles gut, 1 = prüfen.

**6 Sektionen** werden gezeigt:
1. **Pipeline-Throughput** — wie viel lief durch
2. **Reviews pro Agent** — Verteilung approved/revision
3. **Revert-Rate pro Rule** — Auto-Merge-Qualität
4. **Queue-Health** — hängen Tasks?
5. **Jules-API-Limits** — wie nahe am 100/24h
6. **Pending Manual-Merges** — wartende PRs

---

## Monitoring-Channels (Discord)

| Channel | Was landet | Check-Frequenz |
|---------|-----------|---------------|
| **🛡️-security-ops** | Jules-Reviews (Embeds, farbkodiert) | Nach Bedarf |
| **seo-fixes** | SEO-Agent-PR-Reviews | Wöchentlich |
| **🤖-agent-reviews** | Codex/ScanAgent-Code-Fix-Reviews | Bei Security-Findings |
| **🚨-alerts** | Escalations (nach 5 Iterationen kein Fix) | Sofort wenn Ping |
| **🧠-ai-learning** | Daily-Digest (08:15), Nightly-Batch (23:07) | Täglich morgens |
| **✋-approvals** | Pending human approvals | Wenn Ping |

---

## Interventionen

### Alles auf einmal stoppen

```yaml
# config/config.yaml
agent_review:
  enabled: false
```
```bash
scripts/restart.sh  # <10s Downtime
```
Legacy-Jules-Pfad läuft wie vor dem Feature.

### Nur Auto-Merge stoppen (Reviews laufen weiter)

```yaml
agent_review:
  auto_merge:
    enabled: false
```
Reviews werden weiter gepostet, aber PRs bekommen nur Labels — keine automatischen Merges.

### Nur einen Adapter deaktivieren

```yaml
agent_review:
  adapters:
    seo: false   # oder codex: false
```
Jules läuft weiter, andere nicht.

### Einzelnen Queue-Task cancellen

```sql
-- via postgres-guildscout MCP
UPDATE agent_task_queue
SET status='cancelled', failure_reason='manual_cancel'
WHERE id = <task_id> AND status='queued';
```

### Bot gerade nichts tun lassen (laufen aber still)

Kein Config-Change nötig — wenn keine Webhooks kommen, arbeitet die Pipeline nicht. Die Scheduled-Tasks (60s Queue, 60min Outcome, 08:15 Digest) laufen weiter, sehen aber nichts zu tun.

---

## Wann du eingreifen solltest

### Rot (sofort)

- **Revert-Rate > 20%** für eine Rule → `auto_merge.enabled: false`, Incident
- **Queue-Tasks hängen >2h** → Scheduler-Issue, Runbook öffnen
- **Circuit-Breaker-Ping** in 🚨-alerts → Jules-API down oder Loop-Schutz getriggert

### Gelb (nächster Werktag)

- **Revert-Rate 10-20%** für eine Rule → Rule-Konfig überdenken
- **Pending Manual-Merges > 10** → Team-Kapazität-Gespräch
- **70%+ der Jules-24h-Limits** → Tasks-Priorität prüfen

### Grün (nichts tun)

- Reverts = 0
- Queue-Alter < 2h
- Sessions < 70/24h
- Alle Agents approven mehrheitlich

---

## Manueller Trigger (wenn du willst)

### Eigene Jules-Session starten

Dashboard ist die einfachste Option: https://jules.google.com/

Oder via CLI:
```bash
jules new --repo Commandershadow9/ZERODOX "beschreibe die Aufgabe"
```

Jules öffnet PR, Bot reviewt automatisch.

### Smoke-Test reproducieren

```bash
cd /home/cmdshadow/shadowops-bot
source .venv/bin/activate
PYTHONPATH=src python scripts/smoke_test_multi_agent_review.py
```

7/7 Stages grün = System gesund. Rot = Runbook öffnen.

---

## Rollout-Pfad für mayday-sim

Wenn du freigibst:

1. **Webhook anlegen** (Kommando oben unter "Projekte im System")
2. **Check**: beim nächsten PR auf mayday-sim sollte der Bot ihn sehen (Log-Line `📥 Received GitHub event: pull_request`)
3. **Config-Flag aktivieren** in `config/config.yaml`:
   ```yaml
   agent_review:
     enabled: true
     adapters:
       jules: true
     auto_merge:
       projects:
         mayday-sim: { allowed: true, trivial_threshold: 500 }
   ```
4. **Monitoring**: mayday-sim taucht im Weekly-Check + Daily-Digest auf, sobald erste Reviews laufen

**Kein Code-Change nötig.** mayday-sim ist bereits in `_JULES_KNOWN_PROJECTS` + `_JULES_DELEGATABLE_CATEGORIES` Whitelist.

---

## Referenz-Dokumentation

- **Rollout-Guide (Stufen):** `docs/multi-agent-review-rollout.md`
- **Incident-Runbook (Playbooks):** `docs/multi-agent-review-runbook.md`
- **ADR-008 (Architektur):** `docs/adr/008-multi-agent-review-pipeline.md`
- **Safety-Rules:** `.claude/rules/safety.md` (Section "Multi-Agent Review Pipeline")
- **Smoke-Test:** `scripts/smoke_test_multi_agent_review.py`
- **Weekly-Check:** `scripts/weekly_review_check.sh`

---

**TL;DR:** Täglich 1 min Daily-Digest lesen, freitags 5 min Weekly-Check laufen lassen. Der Rest ist autonom.
