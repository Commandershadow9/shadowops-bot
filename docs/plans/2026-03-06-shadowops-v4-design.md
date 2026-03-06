# ShadowOps v4 — Design-Dokument

**Datum:** 2026-03-06
**Status:** DRAFT
**Autor:** Claude Code (Opus 4.6) + CommanderShadow
**Version:** v4.0.0

---

## 1. Zusammenfassung

ShadowOps v4 ist ein **Motor-Tausch**: Die fehlerhafte Ollama-AI-Engine (lokales LLM, OOM-Probleme) wird durch eine **Dual-Engine-Architektur** ersetzt:

- **Codex CLI** (gpt-5.3-codex / o3) als Primary Engine (97% der Tasks)
- **Claude CLI** (Sonnet/Opus 4.6) als Fallback + Critical Verification (3%)

Beide Engines haben **vollen MCP-Zugriff** auf Postgres, Redis, Docker, GitHub, Filesystem, Prisma.

Die gesamte Business-Logik (~11.500 Zeilen) bleibt erhalten:
- Approval-Modes, Knowledge-Base, Event-Watcher, Orchestrator
- Self-Healing, Deployment-Manager, Incident-Manager
- Discord-Integration, Embeds, Customer-Notifications
- Alle Cogs, Fixer, Git-History-Analyzer

---

## 2. Ist-Zustand

### 2.1 ShadowOps v3.6 (AUF EIS seit 2026-02-13)

- **15.000+ Zeilen** Python, discord.py
- **Kern-Problem:** Ollama llama3.1 (8B) auf 8GB-RAM VPS
  - 5-6 GB RAM-Verbrauch pro AI-Call
  - OOM-Kills zerstoeren laufende Services
  - ~800 Zeilen RAM-Management-Workarounds
  - Analyse-Qualitaet mittelmassig (8B-Modell)
- **Alles andere funktioniert:** Discord-Bot, Event-Watcher, Approval-System, Knowledge-Base, Deployment, Monitoring

### 2.2 Verfuegbare Infrastruktur

| Tool | Version | Auth | MCP-Support |
|------|---------|------|-------------|
| Codex CLI | 0.104.0 | ChatGPT Plus (OAuth) | Ja (codex mcp add) |
| Claude CLI | 2.1.70 | Claude Max (Token) | Ja (7 Server in ~/.claude.json) |
| Discord Bot Token | Vorhanden | Bot auf Guild aktiv | n/a |

### 2.3 Ueberwachte Services

| Service | Typ | Health-Check |
|---------|-----|-------------|
| guildscout-api-v3 | Docker | HTTP :8091 |
| guildscout-bot | systemd (user) | Port 8765 |
| guildscout-feedback-agent | systemd (user) | Prozess-Status |
| guildscout-postgres | Docker | pg_isready |
| guildscout-redis | Docker | redis-cli ping |
| zerodox-web | Docker | HTTP :3000 (intern) |
| zerodox-db | Docker | pg_isready |
| zerodox-support-agent | systemd (user) | Prozess-Status |
| seo-agent | systemd (user) | Prozess-Status |
| sicherheitsdienst-traefik | Docker | HTTP :8080 |

---

## 3. Architektur: Dual-Engine mit Smart Queue (B++)

### 3.1 Uebersicht

```
Discord Events / Security Events / GitHub Webhooks / Health Checks
                            |
                            v
                    +-----------------+
                    |  Event Watcher  |  (unveraendert, 845 Zeilen)
                    |  - Fail2ban     |
                    |  - CrowdSec     |
                    |  - Trivy/Docker |
                    |  - AIDE         |
                    |  - Health Checks|
                    +---------+-------+
                              |
                              v
                    +-----------------+
                    |   SmartQueue    |  (NEU, ersetzt ollama_queue_manager)
                    |                 |
                    |  Analyse-Pool:  |
                    |  max 3 parallel |
                    |                 |
                    |  Fix-Lock:      |
                    |  strikt 1       |
                    +---------+-------+
                              |
                    +---------v---------+
                    |    TaskRouter     |  (NEU, Teil von ai_engine.py)
                    |                   |
                    |  Entscheidet:     |
                    |  - Welche Engine  |
                    |  - Welches Modell |
                    |  - Thinking?      |
                    |  - MCP noetig?    |
                    +---+----------+----+
                        |          |
              +---------v--+  +----v-----------+
              |   Codex    |  |    Claude       |
              |  Engine    |  |    Engine       |
              |  (97%)     |  |    (3%)         |
              |            |  |                 |
              | codex exec |  | claude -p       |
              | MCP: alle  |  | MCP: alle       |
              | Token:egal |  | Token: sparsam  |
              +-----+------+  +------+----------+
                    |                 |
                    v                 v
              +-----------------------------+
              |       Orchestrator          |  (angepasst, 2379 Zeilen)
              |  - Batch-Processing         |
              |  - Coordinated Plans        |
              |  - Discord Approval Buttons |
              +-------------+---------------+
                            |
              +-------------v---------------+
              |       Self-Healing          |  (angepasst, 1382 Zeilen)
              |  - Job Queue + Retry        |
              |  - Circuit Breaker          |
              |  - Fixer Delegation         |
              |  - Backup + Rollback        |
              +-------------+---------------+
                            |
              +-------------v---------------+
              |    Approval Modes           |  (unveraendert, 270 Zeilen)
              |  PARANOID / BALANCED / AGG. |
              +-------------+---------------+
                            |
              +-------------v---------------+
              |    Knowledge Base           |  (unveraendert, 431 Zeilen)
              |  SQLite: fixes, strategies  |
              |  Success-Rate Tracking      |
              +-----------------------------+
```

### 3.2 AI-Engine Layer (NEU)

Ersetzt: ai_service.py (1364 Z.) + ollama_queue_manager.py (357 Z.)

#### 3.2.1 Provider-Abstraktion

Zwei Provider-Klassen mit identischer Schnittstelle:

**CodexProvider** (Primary):
- Nutzt `codex exec` mit `--ephemeral` und `-s workspace-write`
- Modelle: gpt-4o (schnell), gpt-5.3-codex (standard), o3 (thinking)
- `--output-schema` fuer strukturierte Ausgaben
- `-o` fuer Output in Datei (JSON-Parsing)
- Subprocess-basiert mit asyncio und Timeout

**ClaudeProvider** (Fallback):
- Nutzt `claude -p` mit `--output-format json`
- Modelle: Sonnet 4.6 (standard), Opus 4.6 (thinking/verify)
- MCP automatisch via ~/.claude.json
- Subprocess-basiert mit asyncio und Timeout

Beide Provider implementieren die gleiche abstrakte Schnittstelle:
- `query(prompt, schema_path, model, timeout) -> dict`
- `is_available() -> bool`

#### 3.2.2 TaskRouter — Intelligente Modell-Auswahl

Routing-Matrix bestimmt pro Task:
- Welche Engine (codex/claude)
- Welche Modell-Klasse (fast/standard/thinking)
- Ob Thinking aktiviert wird

Regeln:
- CRITICAL analysis/fix -> Codex o3 (thinking xhigh)
- CRITICAL verify -> Claude Opus (thinking)
- HIGH/MEDIUM -> Codex gpt-5.3-codex (standard)
- LOW -> Codex gpt-4o (schnell)
- Overflow (alle Codex-Slots belegt) -> Claude Sonnet
- Codex-Fehler -> automatisch Claude Fallback

#### 3.2.3 Pre-Push Verification Pipeline

Schritte vor jedem Fix-Push:
1. Tests laufen (projektspezifisch: go test, pytest, npm test)
2. Bei HIGH/CRITICAL: Claude Opus verifiziert Fix
3. Knowledge Base: Success-Rate aehnlicher Fixes pruefen
4. Bei Bedenken: Fix verwerfen + Discord Alert
5. Nach Push: Health-Check, bei Fehler Rollback

### 3.3 SmartQueue (NEU)

Ersetzt: ollama_queue_manager.py + queue_dashboard.py + queue_admin.py

#### 3.3.1 Design-Prinzipien

1. Analyse = parallel (max 3 Slots, Codex + Claude)
2. Fix = strikt sequentiell (1 Fix gleichzeitig, nie unterbrechen)
3. CRITICAL verdraengt LOW (aus Analyse-Pool, nie aus Fix-Lock)
4. Batch bei Event-Sturm (>5 Events/60s = gruppieren)
5. Circuit Breaker (5 Fails = 1h Pause)

#### 3.3.2 Concurrency-Modell

```
Analyse-Pool [Slot 1] [Slot 2] [Slot 3]
    |              |         |
    v              v         v
  Codex          Codex    Codex/Claude(overflow)
    |              |         |
    +------+-------+---------+
           |
           v
    Fix-Queue (Priority-sorted)
           |
           v
    Fix-Lock [1 Slot]
           |
           v
    Verify + Push
```

#### 3.3.3 Szenario-Matrix

| Situation | Verhalten |
|-----------|-----------|
| 1 Event | Analyse in Slot 1, Fix danach |
| 3 Events gleichzeitig | 3x parallel analysieren, Fixes sequentiell nach Prioritaet |
| Fix laeuft + CRITICAL Event | CRITICAL Analyse parallel, Fix Queue vorne einreihen, laufender Fix wird NICHT unterbrochen |
| >5 Events in 60s | Batch-Modus: Orchestrator gruppiert, 1 koordinierter Plan |
| Codex Timeout | Automatisch Claude Fallback |
| 5 fehlgeschlagene Fixes | Circuit Breaker: 1h Pause, Discord Alert |
| Fix laeuft + Projekt crasht | Crash-Event in Analyse-Pool, laufender Fix geht weiter, Crash-Fix naechster in Queue |

### 3.4 Modell-Routing Matrix

| Situation | Engine | Modell | Thinking | MCP |
|-----------|--------|--------|----------|-----|
| Fail2ban Ban | Codex | gpt-4o | nein | nein |
| CrowdSec Alert | Codex | gpt-5.3-codex | nein | nein |
| Docker CVE (Trivy) | Codex | gpt-5.3-codex | nein | ja (docker) |
| AIDE File Change | Codex | gpt-5.3-codex | nein | ja (filesystem) |
| Projekt Down/Crash | Codex | o3 | xhigh | ja (docker, postgres, redis) |
| Aktiver Angriff | Codex | o3 | xhigh | ja (alle) |
| Patch Notes | Codex | gpt-5.3-codex | nein | nein |
| Code-Verbesserung | Codex | gpt-5.3-codex | high | ja (filesystem, postgres) |
| CRITICAL Fix Verify | Claude | Opus 4.6 | ja | ja (alle) |
| Codex Timeout/Fehler | Claude | Sonnet 4.6 | nein | ja |
| Overflow (>3 parallel) | Claude | Sonnet 4.6 | nein | ja |
| Pre-Push Security | Claude | Opus 4.6 | ja | ja (filesystem) |

### 3.5 Modell-Eskalation

```
Level 1: gpt-4o — Schnell, guenstig. LOW-Priority.
Level 2: gpt-5.3-codex — Beste Balance. 90% der Tasks.
Level 3: o3 (thinking xhigh) — Tiefe Analyse. CRITICAL Events.
Level 4: Claude Opus 4.6 — Finale Verifikation. Pre-Push Review.
```

---

## 4. Dateien-Aenderungen

### 4.1 NEU erstellen

| Datei | Zeilen (ca.) | Beschreibung |
|-------|-------------|-------------|
| src/integrations/ai_engine.py | ~500 | Provider-Abstraktion (Codex + Claude), TaskRouter |
| src/integrations/smart_queue.py | ~400 | SmartQueue mit Analyse-Pool + Fix-Lock |
| src/integrations/verification.py | ~200 | Pre-Push Verification Pipeline |
| src/schemas/fix_strategy.json | ~40 | JSON-Schema fuer Codex --output-schema |
| src/schemas/patch_notes.json | ~30 | JSON-Schema fuer Patch Notes |
| src/schemas/incident_analysis.json | ~35 | JSON-Schema fuer Incident-Analyse |

### 4.2 ANPASSEN (minimal)

| Datei | Aenderungen |
|-------|-------------|
| src/bot.py | Ollama-Init raus, neue AI-Engine Init rein (~30 Zeilen) |
| src/integrations/orchestrator.py | ai_service -> ai_engine Aufrufe (~20 Zeilen) |
| src/integrations/self_healing.py | Provider-Referenzen anpassen (~15 Zeilen) |
| src/integrations/patch_notes_manager.py | llama3.1 -> ai_engine Aufrufe (~10 Zeilen) |
| src/integrations/auto_fix_manager.py | Provider-Referenzen anpassen (~10 Zeilen) |
| src/integrations/continuous_learning_agent.py | Ollama-Calls -> ai_engine (~20 Zeilen) |
| config/config.yaml | Neue ai Section (Codex + Claude statt Ollama) |

### 4.3 ENTFERNEN

| Datei | Grund |
|-------|-------|
| src/integrations/ollama_queue_manager.py | Ersetzt durch smart_queue.py |
| src/integrations/queue_dashboard.py | Nicht mehr noetig |
| src/commands/queue_admin.py | Nicht mehr noetig |
| RAM-Management in ai_service.py (~300 Z.) | Kein lokales LLM mehr |

### 4.4 UNVERAENDERT (11.500+ Zeilen)

- approval_modes.py (270 Z.)
- knowledge_base.py (431 Z.)
- event_watcher.py (845 Z.)
- git_history_analyzer.py (440 Z.)
- prompt_ab_testing.py (496 Z.)
- knowledge_synthesizer.py (461 Z.)
- deployment_manager.py (773 Z.)
- project_monitor.py (854 Z.)
- incident_manager.py (300+ Z.)
- customer_notifications.py (523 Z.)
- github_integration.py (300+ Z.)
- backup_manager.py (~200 Z.)
- impact_analyzer.py, command_executor.py, service_manager.py
- context_manager.py (weiter genutzt fuer RAG)
- Alle Fixer: trivy_fixer.py, crowdsec_fixer.py, fail2ban_fixer.py, aide_fixer.py
- Alle Cogs: admin.py, monitoring.py, inspector.py
- Alle Utils: config.py, embeds.py, state_manager.py, logger.py, discord_logger.py

---

## 5. Config-Aenderungen

### 5.1 Neue AI-Section (ersetzt Ollama-Config)

```yaml
ai:
  primary:
    engine: codex
    models:
      fast: gpt-4o
      standard: gpt-5.3-codex
      thinking: o3
    thinking_effort: xhigh
    timeout: 300
    timeout_thinking: 600

  fallback:
    engine: claude
    cli_path: /home/cmdshadow/.local/bin/claude
    models:
      fast: claude-sonnet-4-6
      standard: claude-sonnet-4-6
      thinking: claude-opus-4-6
    timeout: 300

  queue:
    max_analysis_parallel: 3
    fix_lock: true
    batch_threshold: 5
    batch_window: 10
    circuit_breaker_threshold: 5
    circuit_breaker_timeout: 3600

  verification:
    test_before_push: true
    verify_critical_with_claude: true
    min_confidence: 0.85

  routing:
    critical_analysis: { engine: codex, model: thinking }
    critical_fix: { engine: codex, model: thinking }
    critical_verify: { engine: claude, model: thinking }
    high_analysis: { engine: codex, model: standard }
    low_analysis: { engine: codex, model: fast }
    patch_notes: { engine: codex, model: standard }
    pre_push_review: { engine: claude, model: thinking }
```

---

## 6. MCP-Server Setup

### 6.1 Codex MCP (einmalig registrieren)

```bash
codex mcp add postgres-guildscout -- uvx postgres-mcp \
  "postgresql://guildscout:devpassword123@127.0.0.1:5433/guildscout"

codex mcp add postgres-zerodox -- uvx postgres-mcp \
  "postgresql://zerodox:zerodox-db-secret-2025@127.0.0.1:5434/zerodox"

codex mcp add redis -- uvx --from redis-mcp-server@latest \
  redis-mcp-server --url "redis://127.0.0.1:6379/0"

codex mcp add docker -- uvx docker-mcp

codex mcp add github --url "https://api.githubcopilot.com/mcp/"

codex mcp add filesystem -- npx -y @modelcontextprotocol/server-filesystem \
  /home/cmdshadow/GuildScout /home/cmdshadow/ZERODOX \
  /home/cmdshadow/agents /home/cmdshadow/shadowops-bot

codex mcp add prisma -- npx -y prisma mcp
```

### 6.2 Claude MCP

Bereits konfiguriert in ~/.claude.json (7 Server).

---

## 7. Codex Skills

Skills in ~/.codex/skills/shadowops/:

| Skill | Aufgabe |
|-------|---------|
| security-analyzer | Security Events bewerten, Severity + Confidence + Fix-Strategie |
| patch-notes-writer | Patchnotes aus Commits, DE/EN, Discord-kompatibel |
| code-reviewer | Code vor Push pruefen, OWASP Top 10, Security-Fokus |
| incident-diagnoser | Crashes analysieren via MCP (docker, postgres, redis) |
| fix-verifier | Fix-Ergebnis pruefen, Vorher/Nachher Vergleich |

---

## 8. Token-Budget-Strategie

### 8.1 Verteilung

ChatGPT Plus Abo (Codex CLI):
- 97% aller Bot-Tasks
- Security-Analyse, Patch Notes, Code-Review, Fixes
- Thinking-Modelle (o3) bei Critical
- Token-Budget: grosszuegig nutzbar

Claude Max Abo (Claude CLI):
- 3% der Tasks
- CRITICAL Fix Verification (Opus)
- Pre-Push Security Review (Opus)
- Fallback bei Codex-Fehlern (Sonnet)
- Overflow bei >3 parallelen Tasks (Sonnet)
- Hauptbudget bleibt fuer manuelle Entwicklung (Claude Code)

### 8.2 Geschaetzte Token pro Tag (Normalbetrieb)

| Task-Typ | Haeufigkeit | Engine | Token/Tag |
|-----------|-------------|--------|-----------|
| Health Checks + Bewertung | ~100/Tag | Codex | ~50k |
| Security Events | ~5-10/Tag | Codex | ~20k |
| Patch Notes | ~1-2/Tag | Codex | ~10k |
| Critical Verification | ~0-1/Tag | Claude | ~5k |
| Fallback | ~0-1/Tag | Claude | ~3k |

---

## 9. Sicherheits-Design

### 9.1 Pre-Push Pipeline

1. Fix generiert
2. Tests laufen (pytest, go test, npm test)
3. Confidence >= 85%
4. Bei HIGH/CRITICAL Risk: Claude Opus verifiziert
5. Knowledge Base: Success-Rate pruefen (>50%)
6. Push / Apply
7. Health-Check nach Fix
8. Bei Fehler: Automatischer Rollback

### 9.2 Unveraenderte Safety-Mechanismen

- DO-NOT-TOUCH Listen
- Approval-Modes (PARANOID/BALANCED/AGGRESSIVE)
- Circuit Breaker (5 Fails = 1h Pause)
- Backup vor jedem Fix
- Automatischer Rollback
- Confidence-Threshold (min 85%)
- Risk-Assessment (CRITICAL/HIGH/MEDIUM/LOW)
- Command Validation

### 9.3 Neue Safety-Features

- Dual-Engine Verification: CRITICAL Fixes von BEIDEN Engines geprueft
- Test-Pflicht: Kein Push ohne bestandene Tests
- Modell-Eskalation: Automatisch groesseres Modell bei Unsicherheit
- Token-Isolation: Claude-Budget fuer manuelle Entwicklung geschuetzt

---

## 10. Migrations-Schritte

### Phase 1: Motor-Tausch (Kern)
1. Codex MCP-Server registrieren
2. ai_engine.py erstellen (Provider + TaskRouter)
3. smart_queue.py erstellen
4. verification.py erstellen
5. JSON-Schemas erstellen
6. bot.py anpassen (Ollama raus, neue Engine rein)
7. orchestrator.py anpassen
8. self_healing.py anpassen
9. config.yaml neue AI-Section
10. Ollama-spezifische Dateien entfernen
11. Testen: Bot starten, Events simulieren

### Phase 2: Integration + Skills
12. patch_notes_manager.py anpassen
13. auto_fix_manager.py anpassen
14. continuous_learning_agent.py anpassen
15. Codex Skills erstellen (5 Skills)
16. Projekt-Config aktualisieren (neue Services)
17. Multi-Guild testen

### Phase 3: Polish + Launch
18. Discord-Commands anpassen
19. Memory Leaks fixen
20. Tests aktualisieren
21. Dokumentation aktualisieren
22. systemd-Service aktivieren
23. LIVE-Test im Paranoid-Mode

---

## 11. Risiken und Mitigationen

| Risiko | Wahrscheinlichkeit | Mitigation |
|--------|-------------------|------------|
| Codex CLI API-Limits | Mittel | Claude Fallback, Queue-Throttling |
| Claude Token zu hoch | Gering | Nur 3% Tasks, strikte Routing-Matrix |
| Codex MCP instabil | Gering | Testen, Claude als Backup |
| Tests brechen | Hoch | Schrittweise anpassen, AI-Mocks |
| Structured Output Fehler | Mittel | JSON-Schema Validation, Retry |

---

## 12. Erfolgs-Kriterien

- [ ] Bot startet und verbindet zu Discord
- [ ] Event-Watcher erkennt Security Events
- [ ] Codex analysiert Events korrekt (Confidence > 80%)
- [ ] Approval-Buttons funktionieren
- [ ] Fix-Execution mit Backup + Rollback
- [ ] Knowledge-Base trackt Ergebnisse
- [ ] Patch Notes generiert
- [ ] Health-Monitoring fuer alle 10 Services
- [ ] Claude Fallback bei Codex-Fehler
- [ ] CRITICAL Verification durch Claude Opus
- [ ] Tests vor Push
- [ ] Kein OOM (0 GB lokales AI-RAM)
- [ ] Multi-Guild Notifications
