# ShadowOps v4 — Implementierungsplan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Ollama AI-Engine durch Dual-Engine (Codex CLI + Claude CLI) ersetzen, SmartQueue + Verification Pipeline einbauen, alle 11.500 Zeilen Business-Logik erhalten.

**Architecture:** Zwei CLI-Provider (CodexProvider, ClaudeProvider) mit identischer Schnittstelle, TaskRouter fuer intelligente Modell-Auswahl, SmartQueue mit 3 Analyse-Slots + 1 Fix-Lock, Pre-Push Verification Pipeline. Bestehende Systeme (Approval, Knowledge Base, Event Watcher, Orchestrator, Self-Healing) bleiben unveraendert.

**Tech Stack:** Python 3.11, discord.py, asyncio, subprocess (Codex/Claude CLI), JSON Schema, pytest

---

## Phase 1: Motor-Tausch (Kern)

### Task 1: JSON-Schemas erstellen

**Files:**
- Create: `src/schemas/fix_strategy.json`
- Create: `src/schemas/patch_notes.json`
- Create: `src/schemas/incident_analysis.json`

**Step 1:** `mkdir -p src/schemas`

**Step 2:** Erstelle `fix_strategy.json` mit Feldern: description (string), confidence (number 0-1), severity_assessment (enum LOW/MEDIUM/HIGH/CRITICAL), analysis (string), root_cause (string), steps (array mit action/command/risk_level/rollback_command), affected_services (array), estimated_downtime_seconds (integer). Alle mit `additionalProperties: false`.

**Step 3:** Erstelle `patch_notes.json` mit Feldern: title, summary, changes (array mit type enum feature/fix/improvement/breaking + description), version, language (enum de/en). `additionalProperties: false`.

**Step 4:** Erstelle `incident_analysis.json` mit Feldern: incident_type, severity, affected_service, root_cause, immediate_action, fix_steps (array), confidence (number), requires_restart (boolean). `additionalProperties: false`.

**Step 5: Commit**
```
git add src/schemas/ && git commit -m "feat: JSON-Schemas fuer Codex Structured Output"
```

---

### Task 2: AI-Engine erstellen (ai_engine.py)

**Files:**
- Create: `src/integrations/ai_engine.py` (~500 Zeilen)
- Create: `tests/unit/test_ai_engine.py`

**Step 1: Test schreiben**

Test-Klassen:
- `TestTaskRouter`: Testet Routing-Logik (CRITICAL->codex/o3, LOW->codex/gpt-4o, CRITICAL verify->claude/opus)
- `TestCodexProvider`: Testet JSON-Parsing bei Erfolg, None bei Fehler (mock subprocess)
- `TestClaudeProvider`: Testet JSON-Parsing aus Claude `{"result": "..."}` Wrapper
- `TestAIEngine`: Testet Init erstellt beide Provider + Router

**Step 2:** `pytest tests/unit/test_ai_engine.py -v` → Expected: ModuleNotFoundError

**Step 3: Implementiere ai_engine.py**

Klassen:
1. **CodexProvider** — `codex` CLI via `asyncio.create_subprocess_exec`, Methoden: `query(prompt, model, schema_path, timeout) -> dict`, `query_raw(prompt, model, timeout) -> str`, `is_available() -> bool`. Env: `CLAUDECODE` entfernen. Modelle: fast/standard/thinking.

2. **ClaudeProvider** — `claude -p` CLI, `--output-format json`, `--model`, `--max-turns 1`. Gleiche Methoden. Parse `{"result": "..."}` Wrapper. MCP automatisch aus ~/.claude.json.

3. **TaskRouter** — Config-basiertes Routing: `get_route(severity, task_type) -> dict(engine, model, model_class, schema_path)`. Routing-Matrix aus config.yaml.

4. **AIEngine** — Hauptklasse mit:
   - `generate_fix_strategy(context) -> dict` (kompatibel mit AIService)
   - `generate_coordinated_plan(prompt, context) -> dict`
   - `get_ai_analysis(prompt, context, use_critical_model) -> str`
   - `generate_raw_text` als Alias fuer `get_ai_analysis`
   - `verify_fix(fix_description, fix_commands, event) -> dict`
   - `_execute_with_fallback(prompt, route) -> dict` (Primary -> Fallback)
   - `_build_analysis_prompt(event, previous_attempts) -> str`
   - Stats-Tracking (codex/claude calls/success/failures)

**Step 4:** `pytest tests/unit/test_ai_engine.py -v` → Expected: PASS

**Step 5: Commit**
```
git add src/integrations/ai_engine.py tests/unit/test_ai_engine.py
git commit -m "feat: AI Engine mit Codex + Claude Provider und TaskRouter"
```

---

### Task 3: SmartQueue erstellen

**Files:**
- Create: `src/integrations/smart_queue.py` (~300 Zeilen)
- Create: `tests/unit/test_smart_queue.py`

**Step 1: Test schreiben**

Test-Klassen:
- `TestSmartQueueInit`: Defaults korrekt
- `TestSmartQueueAnalysis`: submit ANALYSIS -> accepted, laeuft parallel
- `TestSmartQueueFixLock`: fix_locked=True -> Item in fix_queue eingereiht
- `TestCircuitBreaker`: N Fehler -> circuit_breaker_open=True

**Step 2:** `pytest tests/unit/test_smart_queue.py -v` → Expected: ModuleNotFoundError

**Step 3: Implementiere smart_queue.py**

Klassen:
1. **QueueItemType** (Enum): ANALYSIS, FIX
2. **QueueItem** (dataclass): item_type, event, callback, priority, created_at, result
3. **SmartQueue**:
   - `analysis_semaphore` (asyncio.Semaphore, max 3)
   - `fix_lock` (asyncio.Lock, strikt 1)
   - `fix_queue` (Priority-sorted List)
   - `submit(item) -> bool`
   - `_run_analysis(item)` — parallel via semaphore
   - `_fix_worker()` — Hintergrund-Task, sequentiell
   - Circuit Breaker: `_record_failure()`, `_record_success()`, `_check_circuit_breaker_reset()`
   - Batch-Erkennung: `is_batch_mode()`, `record_event()`
   - `start()`, `stop()`, `get_stats()`

**Step 4:** `pytest tests/unit/test_smart_queue.py -v` → Expected: PASS

**Step 5: Commit**
```
git add src/integrations/smart_queue.py tests/unit/test_smart_queue.py
git commit -m "feat: SmartQueue mit Analyse-Pool und Fix-Lock"
```

---

### Task 4: Verification Pipeline erstellen

**Files:**
- Create: `src/integrations/verification.py` (~200 Zeilen)

**Step 1: Implementiere verification.py**

Klasse **VerificationPipeline**:
- `__init__(ai_engine, knowledge_base, config)`
- `verify(fix_strategy, event, project_config) -> dict(approved, reason, checks)`
- Checks:
  1. Confidence >= min_confidence (0.85)
  2. Tests laufen (project test_command in project_path)
  3. Claude Opus Verification (bei CRITICAL/HIGH)
  4. Knowledge Base Success-Rate Check (>50%)
- `_run_tests(test_command, project_path) -> dict(passed, detail)`
- `_check_knowledge_base(fix_strategy, event) -> dict(passed, detail)`

**Step 2: Commit**
```
git add src/integrations/verification.py
git commit -m "feat: Pre-Push Verification Pipeline"
```

---

### Task 5: Config aktualisieren

**Files:**
- Modify: `config/config.yaml:208-224`

**Step 1:** Ersetze alte `ai:` Section (Zeile 208-224) mit neuer Dual-Engine Config:

```yaml
ai:
  enabled: true
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
    medium_analysis: { engine: codex, model: standard }
    low_analysis: { engine: codex, model: fast }
    patch_notes: { engine: codex, model: standard }
    pre_push_review: { engine: claude, model: thinking }
```

WICHTIG: Alte OpenAI/Anthropic API-Keys und Ollama-Config entfernen (Auth laeuft ueber CLI).

**Step 2: Commit**
```
git add config/config.yaml
git commit -m "feat: Config von Ollama auf Dual-Engine umgestellt"
```

---

### Task 6: bot.py anpassen

**Files:**
- Modify: `src/bot.py` (Zeilen 36, 54-55, 87, 103-104, 246, 480-494, 718-744)

**Step 1: Imports aendern**
- Zeile 36: `from integrations.ai_service import AIService` → `from integrations.ai_engine import AIEngine`
- Zeile 54-55: `OllamaQueueManager` und `QueueDashboard` Imports entfernen

**Step 2: Instance-Variablen**
- Zeile 103-104: `self.queue_manager = None` und `self.queue_dashboard = None` → `self.smart_queue = None`

**Step 3: Channel-Name**
- Zeile 246: `('ollama_queue', ...)` → `('ai_queue', '🤖-ai-queue', 'AI Engine Queue Status', system_category)`

**Step 4: AI-Init (Zeile 480-494)**

Ersetze:
```python
self.ai_service = AIService(self.config, context_manager=..., discord_logger=...)
...
self.queue_manager = OllamaQueueManager(ai_service=self.ai_service)
await self.queue_manager.start_worker()
```

Mit:
```python
self.ai_service = AIEngine(self.config, context_manager=self.context_manager, discord_logger=self.discord_logger)
self.auto_fix_manager.ai_service = self.ai_service

from integrations.smart_queue import SmartQueue
queue_config = self.config.ai.get('queue', {})
self.smart_queue = SmartQueue(queue_config, discord_logger=self.discord_logger)
await self.smart_queue.start()
```

**Step 5:** Queue Dashboard Block (Zeile 718-744) entfernen

**Step 6: Commit**
```
git add src/bot.py && git commit -m "feat: bot.py auf AIEngine + SmartQueue umgestellt"
```

---

### Task 7: orchestrator.py anpassen

**Files:**
- Modify: `src/integrations/orchestrator.py`

**Step 1:** Suche nach `self.ai_service.ollama_*` Referenzen und ersetze mit Router-Aufruf.
Die Methoden `generate_fix_strategy()` und `generate_coordinated_plan()` haben identische Signatur — keine Aenderung noetig.

**Step 2: Commit** (falls Aenderungen)
```
git add src/integrations/orchestrator.py
git commit -m "fix: Orchestrator Ollama-Referenzen entfernt"
```

---

### Task 8: self_healing.py anpassen

**Files:**
- Modify: `src/integrations/self_healing.py:827`

**Step 1:** Ersetze Zeile 827:
```python
model_name = self.ai_service.ollama_model_critical if job.event.severity == 'CRITICAL' else self.ai_service.ollama_model
```
mit:
```python
route = self.ai_service.router.get_route(job.event.severity, 'analysis')
model_name = route['model']
```

**Step 2:** Suche nach weiteren `ollama` Referenzen in self_healing.py

**Step 3: Commit**
```
git add src/integrations/self_healing.py
git commit -m "fix: Self-Healing Ollama-Referenzen durch Router ersetzt"
```

---

### Task 9: Tests aktualisieren

**Files:**
- Modify: `tests/conftest.py:49-69` (mock_config)
- Modify/Delete: `tests/unit/test_ai_service.py`

**Step 1:** Ersetze `config.ai` in conftest.py mit neuer Dual-Engine Config (primary/fallback/routing/verification/queue Struktur)

**Step 2:** Entferne oder passe `test_ai_service.py` an (Ollama-Tests nicht mehr relevant)

**Step 3:** `pytest tests/ -v --tb=short -x`

**Step 4: Commit**
```
git add tests/ && git commit -m "fix: Tests auf AIEngine Config umgestellt"
```

---

### Task 10: Ollama-Dateien entfernen

**Files:**
- Delete: `src/integrations/ollama_queue_manager.py`
- Delete: `src/integrations/queue_dashboard.py`
- Delete: `src/commands/queue_admin.py`

**Step 1:**
```
git rm src/integrations/ollama_queue_manager.py src/integrations/queue_dashboard.py src/commands/queue_admin.py
```

**Step 2:** Verifiziere keine Imports mehr:
```
grep -r "ollama_queue_manager\|queue_dashboard\|queue_admin" src/ --include="*.py"
```

**Step 3: Commit**
```
git add -A && git commit -m "chore: Ollama-Dateien entfernt"
```

---

### Task 11: Codex MCP-Server registrieren

**Step 1:** Registriere alle 7 MCP-Server:
```
codex mcp add postgres-guildscout -- uvx postgres-mcp "postgresql://guildscout:SICHERES_PASSWORT@127.0.0.1:5433/guildscout"
codex mcp add postgres-zerodox -- uvx postgres-mcp "postgresql://zerodox:SICHERES_PASSWORT@127.0.0.1:5434/zerodox"
codex mcp add redis -- uvx --from redis-mcp-server@latest redis-mcp-server --url "redis://127.0.0.1:6379/0"
codex mcp add docker -- uvx docker-mcp
codex mcp add github --url "https://api.githubcopilot.com/mcp/"
codex mcp add filesystem -- npx -y @modelcontextprotocol/server-filesystem /home/cmdshadow/GuildScout /home/cmdshadow/ZERODOX /home/cmdshadow/agents /home/cmdshadow/shadowops-bot
codex mcp add prisma -- npx -y prisma mcp
```

**Step 2:** `codex mcp list` → 7 Server

---

### Task 12: Rauchtest Phase 1

**Step 1:** `pytest tests/ -v --tb=short -x`
**Step 2:** Import-Check: `python3 -c "from src.integrations.ai_engine import AIEngine; print('OK')"`
**Step 3:** Fehler beheben falls noetig

---

## Phase 2: Integration + Skills

### Task 13: patch_notes_manager.py anpassen

**Files:** Modify: `src/integrations/patch_notes_manager.py` (Zeilen 20, 81, 187, 227, 373)

**Step 1:** Import `AIService` → `AIEngine`
**Step 2:** Entferne `model_pref='llama3.1'` aus `generate_raw_text` Aufrufen (Zeile 187, 227)
**Step 3:** Typ-Annotationen `AIService` → `AIEngine` (Zeile 81, 373)
**Step 4: Commit**
```
git add src/integrations/patch_notes_manager.py
git commit -m "fix: Patch Notes Manager auf AIEngine umgestellt"
```

---

### Task 14: auto_fix_manager.py pruefen

**Files:** Modify: `src/integrations/auto_fix_manager.py`

**Step 1:** Interface-Kompatibilitaet pruefen — `get_ai_analysis` existiert in AIEngine
**Step 2:** Falls noetig, Referenzen anpassen
**Step 3: Commit** (falls Aenderungen)

---

### Task 15: continuous_learning_agent.py anpassen

**Files:** Modify: `src/integrations/ai_learning/continuous_learning_agent.py` (Zeilen 83, 626, 871)

**Step 1:** Docstring anpassen (Zeile 83)
**Step 2:** `get_ai_analysis` Aufrufe sind kompatibel — verifizieren
**Step 3: Commit**

---

### Task 16: Codex Skills erstellen

**Files:** Create: `~/.codex/skills/shadowops/` (5 Markdown-Dateien)

Skills: security-analyzer, patch-notes-writer, code-reviewer, incident-diagnoser, fix-verifier

Jeder Skill enthaelt: Kontext (ShadowOps Bot, Server-Setup), Aufgabe, Output-Format (JSON Schema), MCP-Hinweise.

---

### Task 17: Projekt-Config aktualisieren

**Step 1:** `patch_notes.use_ai: true` fuer alle aktiven Projekte
**Step 2:** Commit

---

### Task 18: Multi-Guild testen

Manuelle Verifizierung dass external_notifications funktionieren (GuildScout -> JustMemplex Guild)

---

## Phase 3: Polish + Launch

### Task 19: Rauchtest komplett
`pytest tests/ -v --tb=short`

### Task 20: Alte Tests aufraeumen
test_ai_service.py entfernen, Commit

### Task 21: systemd Service aktivieren
`systemctl --user restart shadowops-bot && systemctl --user status shadowops-bot`

### Task 22: LIVE-Test im Paranoid-Mode
Bot starten, Logs beobachten, Events abwarten, Discord-Output verifizieren

### Task 23: Abschluss-Commit
```
git commit -m "feat: ShadowOps v4 — Dual-Engine mit SmartQueue und Verification Pipeline"
git push origin main
```

---

## Aenderungs-Uebersicht

| Phase | Tasks | Neue Dateien | Geaenderte Dateien | Geloeschte Dateien |
|-------|-------|-------------|-------------------|-------------------|
| 1 | 1-12 | ai_engine.py, smart_queue.py, verification.py, 3 Schemas, 2 Tests | bot.py, orchestrator.py, self_healing.py, config.yaml, conftest.py | ollama_queue_manager.py, queue_dashboard.py, queue_admin.py |
| 2 | 13-18 | 5 Codex Skills | patch_notes_manager.py, auto_fix_manager.py, continuous_learning_agent.py | — |
| 3 | 19-23 | — | test_ai_service.py (entfernen) | test_ai_service.py |
