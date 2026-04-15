# 🛠️ ShadowOps-Bot - Detaillierter Fix-Plan

**Erstellt:** 2025-11-20
**Status:** In Bearbeitung
**Ziel:** Vollständiges Learning System + Production-Ready Bot

---

## 📊 ÜBERSICHT

| Phase | Beschreibung | Aufwand | Status |
|-------|--------------|---------|--------|
| **PHASE 0** | Kritische Blocker (Config, Hardcoded IDs) | 2-3h | ⏳ PENDING |
| **PHASE 1** | Learning System Enhancement (Git, Logs) | 6-8h | ⏳ PENDING |
| **PHASE 2** | Code-Fixes (Bugs, TODOs, Race Conditions) | 4-6h | ⏳ PENDING |
| **PHASE 3** | Test-Suite (Unit + Integration) | 8-12h | ⏳ PENDING |
| **PHASE 4** | Neue Features (Commands, Improvements) | 3-4h | ⏳ PENDING |
| **PHASE 5** | Multi-Projekt-Setup (Github, Kunden) | 4-6h | ⏳ PENDING |
| **GESAMT** | | **27-39h** | **0% Fertig** |

---

## 🎯 PROJEKTZIELE

### **Hauptziel:**
Die KI soll durch maximales Learning optimal Sicherheitslücken fixen, Code verstehen und Projekte am Laufen halten.

### **Spezifische Ziele:**
1. ✅ KI lernt aus Git-Commits, Logs, Events
2. ✅ KI versteht Code-Struktur und Projekt-Kontext
3. ✅ Vollständige Tests für sichere Auto-Remediation
4. ✅ Multi-Projekt-Support für Kunden-Visibility
5. ✅ Production-Ready Setup

---

# PHASE 0: KRITISCHE BLOCKER 🚨

**Priorität:** SOFORT
**Aufwand:** 2-3 Stunden
**Ziel:** Bot ist startbar und konfigurierbar

## Tasks:

### ☐ 0.1 Config-Struktur erstellen
**Datei:** `config/config.example.yaml`
**Problem:** Config-Verzeichnis fehlt, Bot kann nicht starten

**Schritte:**
1. `mkdir config`
2. Erstelle `config/config.example.yaml` mit vollständiger Struktur:
   - Discord (Token, Guild ID)
   - Channels (alle 10+ Channels)
   - AI-Services (Ollama, Claude, OpenAI)
   - Auto-Remediation (Dry-Run, Approval Mode)
   - Log-Paths
   - Bot-Settings

**Acceptance Criteria:**
- [ ] `config/` Verzeichnis existiert
- [ ] `config.example.yaml` hat alle benötigten Felder
- [ ] README.md Setup-Anleitung ist korrekt

---

### ☐ 0.2 Hardcoded Channel IDs entfernen
**Dateien:**
- `src/integrations/self_healing.py:626,628`
- `src/integrations/orchestrator.py:150,716,872`

**Problem:** Channel IDs sind fest im Code, Bot funktioniert nur mit einem Server

**Schritte:**
1. Finde ALLE hardcoded IDs: `1438503736220586164`, `1438503699302957117`, `1438503737315299351`
2. Ersetze durch Config-Zugriffe:
   ```python
   # Vorher:
   channel_id = 1438503736220586164

   # Nachher:
   channel_id = self.config.auto_remediation.get('notifications', {}).get('alerts_channel')
   ```
3. Füge Fallback-Handling hinzu (falls Channel nicht konfiguriert)

**Acceptance Criteria:**
- [ ] Keine hardcoded Channel IDs mehr im Code
- [ ] Alle Channel-Zugriffe nutzen Config
- [ ] Graceful Fallback wenn Channel fehlt

---

### ☐ 0.3 Service File Pfade anpassen
**Datei:** `shadowops-bot.service`

**Problem:** Service zeigt auf `/home/cmdshadow/`, aber Projekt ist in `/home/user/`

**Schritte:**
1. Update `WorkingDirectory=/home/cmdshadow/shadowops-bot`
2. Update `ExecStart=/home/cmdshadow/shadowops-bot/venv/bin/python3 ...`
3. Dokumentiere in README.md den Production-Pfad

**Acceptance Criteria:**
- [ ] Service File zeigt auf Production-Pfad
- [ ] README.md dokumentiert Setup-Pfad

---

### ☐ 0.4 Config Loader - Error Handling
**Datei:** `src/utils/config.py`

**Problem:** FileNotFoundError bei fehlender Config ist zu hart

**Schritte:**
1. Bessere Fehlermeldung mit Hinweis auf `config.example.yaml`
2. Prüfe ob alle benötigten Felder vorhanden sind
3. Gib Warnings für fehlende optionale Felder

**Acceptance Criteria:**
- [ ] Klare Fehlermeldung wenn Config fehlt
- [ ] Validierung aller Pflichtfelder
- [ ] Warnings für optionale Felder

---

### ☐ 0.5 Basis-Smoke-Test
**Ziel:** Bot startet ohne Fehler

**Schritte:**
1. Erstelle minimal-valide `config/config.yaml` von Example
2. Starte Bot: `python3 src/bot.py`
3. Prüfe Startup-Logs
4. Prüfe Discord-Connection

**Acceptance Criteria:**
- [ ] Bot startet ohne Exceptions
- [ ] Discord-Connection erfolgreich
- [ ] Alle Integrationen laden

---

# PHASE 1: LEARNING SYSTEM ENHANCEMENT 🧠

**Priorität:** HOCH
**Aufwand:** 6-8 Stunden
**Ziel:** KI versteht Code, Git-History, Logs maximal

## Tasks:

### ☐ 1.1 Git History Analyzer implementieren
**Neue Datei:** `src/integrations/git_history_analyzer.py`

**Features:**
1. **Commit-History laden** (letzter Monat)
   ```python
   def load_commit_history(self, project_path: str, days: int = 30) -> List[Dict]:
       # git log --since="30 days ago" --pretty=format:'{...}'
   ```

2. **Pattern Recognition**
   - Welche Files werden oft geändert?
   - Welche Commit-Messages betreffen Fixes? ("fix:", "security:", "bugfix:")
   - Welche Authors haben Security-Expertise?

3. **Code-Change-Analyse**
   - `git diff` für Fix-Commits
   - Welche Code-Pattern wurden verwendet?
   - Dependencies-Updates erkennen

4. **Integration in Context Manager**
   - Git-History als Kontext-Quelle
   - Übergabe an AI in Prompts

**Acceptance Criteria:**
- [ ] Commit-History laden funktioniert
- [ ] Pattern Recognition extrahiert relevante Infos
- [ ] Git-Context wird in AI-Prompts integriert
- [ ] Unit-Tests für Git-Analyzer

---

### ☐ 1.2 Log-File Analyzer implementieren
**Neue Datei:** `src/integrations/log_analyzer.py`

**Features:**
1. **Tool-Logs lesen**
   - Fail2ban: `/var/log/fail2ban/fail2ban.log`
   - CrowdSec: `/var/log/crowdsec/crowdsec.log`
   - Docker: `/var/log/docker.log`
   - ShadowOps: `logs/shadowops.log`

2. **Pattern-Extraktion**
   - Häufige Fehler-Pattern
   - Wiederkehrende IPs/Threats
   - Performance-Bottlenecks
   - Erfolgreiche Fix-Pattern

3. **Anomalie-Erkennung**
   - Ungewöhnliche Log-Einträge
   - Neue Error-Types
   - Spike-Detection

4. **Learning-Integration**
   - Log-Insights als Context
   - Trend-Analyse für AI

**Acceptance Criteria:**
- [ ] Log-Parsing für alle Tools
- [ ] Pattern-Extraktion funktioniert
- [ ] Anomalie-Detection implementiert
- [ ] Integration in AI-Context

---

### ☐ 1.3 Code-Structure Analyzer
**Neue Datei:** `src/integrations/code_analyzer.py`

**Features:**
1. **Projekt-Struktur verstehen**
   - Welche Module/Dateien gibt es?
   - Wie hängen sie zusammen?
   - Entry-Points identifizieren

2. **Dependency-Graph**
   - Import-Beziehungen
   - Kritische Module
   - Bottlenecks

3. **Code-Quality-Metrics**
   - Zeilen-Count
   - Komplexität
   - Dokumentations-Coverage

4. **Integration**
   - Code-Structure als Context
   - Für bessere Fix-Strategien

**Acceptance Criteria:**
- [ ] Projekt-Struktur-Analyse läuft
- [ ] Dependency-Graph erstellt
- [ ] Metrics berechnet
- [ ] Context-Integration

---

### ☐ 1.4 Enhanced AI Prompts
**Dateien:** `src/integrations/ai_service.py`

**Updates:**
1. **Git-History-Context hinzufügen**
   ```python
   # In generate_fix_strategy()
   if self.git_analyzer:
       git_context = self.git_analyzer.get_relevant_commits(event)
       prompt_parts.append("# GIT HISTORY INSIGHTS")
       prompt_parts.append(git_context)
   ```

2. **Log-Analysis-Context**
   ```python
   if self.log_analyzer:
       log_insights = self.log_analyzer.get_recent_patterns()
       prompt_parts.append("# LOG PATTERN INSIGHTS")
       prompt_parts.append(log_insights)
   ```

3. **Code-Structure-Context**
   ```python
   if self.code_analyzer:
       structure = self.code_analyzer.get_project_structure(project)
       prompt_parts.append("# CODE STRUCTURE")
       prompt_parts.append(structure)
   ```

**Acceptance Criteria:**
- [ ] Git-Context in Prompts
- [ ] Log-Insights in Prompts
- [ ] Code-Structure in Prompts
- [ ] AI-Responses nutzen neue Infos

---

### ☐ 1.5 Knowledge Base Enhancement
**Optionen:**

**Option A: SQL Knowledge Base** (empfohlen für komplexes Learning)
- Neue Datei: `src/integrations/knowledge_base.py`
- SQLite DB: `data/ai_knowledge.db`
- Tabellen:
  - `fixes` - Alle durchgeführten Fixes
  - `vulnerabilities` - Alle erkannten Vulnerabilities
  - `strategies` - Fix-Strategien mit Success-Rate
  - `code_changes` - Git-Commits
  - `log_patterns` - Erkannte Log-Pattern

**Option B: Enhanced JSON** (einfacher, aktuelles System erweitern)
- Erweitere `logs/event_history.json`
- Neue Files:
  - `data/git_insights.json`
  - `data/log_patterns.json`
  - `data/code_metrics.json`

**Empfehlung:** Option A für bessere Query-Performance und Struktur

**Acceptance Criteria:**
- [ ] Knowledge Base implementiert
- [ ] Daten werden persistent gespeichert
- [ ] Query-Interface für AI
- [ ] Migration von alten Event-History-Daten

---

### ☐ 1.6 Success Rate Tracking
**Datei:** `src/integrations/orchestrator.py`

**Features:**
1. **Success-Rate berechnen**
   ```python
   def get_success_rate(self, event_signature: str) -> float:
       attempts = self.event_history.get(event_signature, [])
       successful = sum(1 for a in attempts if a['result'] == 'success')
       return successful / len(attempts) if attempts else 0.0
   ```

2. **Strategy-Rating**
   - Welche Strategien funktionieren am besten?
   - Für welche Event-Types?
   - Unter welchen Bedingungen?

3. **Adaptive Strategy Selection**
   - AI wählt Strategien mit höchster Success-Rate
   - Fallback zu neuen Strategien wenn alte fehlschlagen

**Acceptance Criteria:**
- [ ] Success-Rate-Berechnung
- [ ] Strategy-Rating implementiert
- [ ] Adaptive Selection funktioniert
- [ ] Metrics in Discord sichtbar

---

### ☐ 1.7 Adaptive Retry Delays
**Datei:** `src/integrations/self_healing.py`

**Problem:** Feste Delays, keine Anpassung an Fehlertyp

**Solution:**
```python
def calculate_retry_delay(self, attempt: int, error_type: str) -> int:
    """Exponential backoff basiert auf Error-Type"""
    base_delays = {
        'network': 5,      # Schnell wieder versuchen
        'permission': 60,  # Länger warten
        'resource': 30,    # Mittel
        'unknown': 10
    }
    base = base_delays.get(error_type, 10)
    return min(300, base * (2 ** attempt))  # Max 5 Minuten
```

**Acceptance Criteria:**
- [ ] Error-Type-Detection
- [ ] Exponential Backoff implementiert
- [ ] Max-Delay-Cap (5 Min)
- [ ] Tests für Retry-Logic

---

# PHASE 2: CODE-FIXES 🔧

**Priorität:** HOCH
**Aufwand:** 4-6 Stunden
**Ziel:** Alle kritischen Bugs fixen

## Tasks:

### ☐ 2.1 TODO in Orchestrator - Verify-Logic
**Datei:** `src/integrations/orchestrator.py:1631`

**Problem:** Kein Vergleich von Before/After Vulnerability-Counts

**Solution:**
```python
async def _verify_trivy_scan(self, project_name: str, before_counts: Dict) -> bool:
    # Run new scan
    after_counts = await self._get_current_vulnerability_counts(project_name)

    # Compare
    improvements = {
        'critical': before_counts.get('critical', 0) - after_counts.get('critical', 0),
        'high': before_counts.get('high', 0) - after_counts.get('high', 0)
    }

    if improvements['critical'] > 0 or improvements['high'] > 0:
        logger.info(f"✅ Improvements: CRITICAL -{improvements['critical']}, HIGH -{improvements['high']}")
        return True
    else:
        logger.warning(f"⚠️ No improvement detected")
        return False
```

**Acceptance Criteria:**
- [ ] Before-Counts werden gespeichert
- [ ] After-Counts werden berechnet
- [ ] Vergleich implementiert
- [ ] Tests für Verify-Logic

---

### ☐ 2.2 Race Condition - Event Watcher
**Datei:** `src/integrations/event_watcher.py:425-475`

**Problem:** Gleichzeitiger Zugriff auf `seen_events` Dict ohne Lock

**Solution:**
```python
def __init__(self, ...):
    self.seen_events_lock = asyncio.Lock()

async def _is_new_event(self, event):
    async with self.seen_events_lock:
        # ... existing logic
        self.seen_events[event_signature] = current_time

async def _save_seen_events(self):
    async with self.seen_events_lock:
        # ... existing logic
```

**Acceptance Criteria:**
- [ ] asyncio.Lock implementiert
- [ ] Alle Zugriffe geschützt
- [ ] Tests für Concurrency

---

### ☐ 2.3 Memory Leak - Event History
**Datei:** `src/integrations/orchestrator.py:106-109`

**Problem:** `event_history` Dict wächst unbegrenzt

**Solution:**
```python
# After adding new attempt
self.event_history[event_signature] = self.event_history[event_signature][-10:]  # Keep last 10
```

**Acceptance Criteria:**
- [ ] Max-Size für Event-History (10 per Type)
- [ ] Old entries werden gelöscht
- [ ] Tests für Memory-Usage

---

### ☐ 2.4 Rollback Implementation
**Datei:** `src/integrations/orchestrator.py:1000-1025`

**Problem:** Rollback-Logic fehlt komplett

**Solution:**
```python
async def _rollback_phase(self, phase: Dict, batch: RemediationBatch):
    """Rollback using Backup Manager"""
    try:
        # Get backup IDs from phase
        backup_ids = phase.get('backup_ids', [])

        for backup_id in backup_ids:
            logger.info(f"🔄 Rolling back: {backup_id}")
            success = await self.backup_manager.restore_backup(backup_id)

            if success:
                logger.info(f"✅ Rollback successful: {backup_id}")
            else:
                logger.error(f"❌ Rollback failed: {backup_id}")

        # Restart services
        await self.service_manager.restart_services(affected_services)

    except Exception as e:
        logger.error(f"❌ Rollback error: {e}", exc_info=True)
```

**Acceptance Criteria:**
- [ ] Backup-Manager-Integration
- [ ] Service-Restart nach Rollback
- [ ] Discord-Notification bei Rollback
- [ ] Tests für Rollback-Flow

---

### ☐ 2.5 AI Service - Retry Logic
**Datei:** `src/integrations/ai_service.py:121-127`

**Problem:** Keine Retries bei temporären Fehlern

**Solution:**
```python
async def _call_with_retry(self, func, max_retries: int = 3):
    """Exponential backoff retry"""
    for attempt in range(max_retries):
        try:
            return await func()
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            if attempt < max_retries - 1:
                delay = 2 ** attempt
                logger.warning(f"⚠️ Retry {attempt+1}/{max_retries} after {delay}s: {e}")
                await asyncio.sleep(delay)
            else:
                raise
```

**Acceptance Criteria:**
- [ ] Retry-Logic implementiert
- [ ] Exponential Backoff
- [ ] Max-Retries konfigurierbar
- [ ] Tests für Retry-Szenarien

---

### ☐ 2.6 Service File Validation
**Datei:** `src/integrations/service_manager.py`

**Problem:** Keine Prüfung ob Service existiert

**Solution:**
```python
async def _validate_service(self, service_name: str) -> bool:
    """Check if systemd service exists"""
    result = await self.executor.execute_async(f"systemctl list-unit-files | grep {service_name}")
    return result.returncode == 0

async def start_service(self, service_name: str):
    if not await self._validate_service(service_name):
        raise ServiceNotFoundError(f"Service {service_name} not found")
    # ... existing logic
```

**Acceptance Criteria:**
- [ ] Service-Existenz-Prüfung
- [ ] Custom Exception für fehlende Services
- [ ] Frühe Validierung beim Start
- [ ] Tests für Validation

---

### ☐ 2.7 Fail2ban Permission Check
**Datei:** `src/integrations/fail2ban.py`

**Problem:** Bot braucht sudo, aber keine Prüfung

**Solution:**
```python
async def validate_permissions(self):
    """Check if bot has necessary permissions"""
    result = await self.executor.execute_async("fail2ban-client ping")
    if result.returncode != 0:
        logger.error("❌ No permissions for fail2ban-client. Run as root or add to sudoers.")
        return False
    return True
```

**Acceptance Criteria:**
- [ ] Permission-Check beim Start
- [ ] Klare Fehlermeldung
- [ ] Documentation für sudoers-Setup

---

### ☐ 2.8 CrowdSec Reconnect Logic
**Datei:** `src/integrations/crowdsec.py`

**Problem:** Bei CrowdSec-Ausfall keine Reconnect-Logic

**Solution:**
```python
async def _monitor_with_reconnect(self):
    """Monitor with automatic reconnect"""
    retry_count = 0
    max_retries = 5

    while retry_count < max_retries:
        try:
            await self._monitor_crowdsec()
        except ConnectionError as e:
            retry_count += 1
            delay = min(300, 2 ** retry_count * 5)
            logger.warning(f"⚠️ CrowdSec connection lost. Retry {retry_count}/{max_retries} in {delay}s")
            await asyncio.sleep(delay)
```

**Acceptance Criteria:**
- [ ] Reconnect-Logic implementiert
- [ ] Exponential Backoff
- [ ] Max-Retries dann Circuit-Breaker
- [ ] Tests für Reconnect

---

### ☐ 2.9 Inefficient File Polling
**Datei:** `src/integrations/docker.py:25-80`

**Problem:** Disk I/O bei jedem Poll

**Solution (Optional):**
```python
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

class TrivyScanHandler(FileSystemEventHandler):
    def on_created(self, event):
        if event.src_path.endswith('.json'):
            self._process_new_scan(event.src_path)
```

**Acceptance Criteria:**
- [ ] Watchdog-Integration (optional)
- [ ] Event-basiertes Monitoring
- [ ] Fallback zu Polling wenn watchdog fehlt

---

# PHASE 3: TEST-SUITE 🧪

**Priorität:** HOCH
**Aufwand:** 8-12 Stunden
**Ziel:** 80%+ Test-Coverage für kritische Module

## Tasks:

### ☐ 3.1 Test-Infrastruktur aufbauen
**Neue Dateien/Verzeichnisse:**
```
tests/
├── __init__.py
├── conftest.py              # Pytest fixtures
├── unit/
│   ├── __init__.py
│   ├── test_config.py
│   ├── test_ai_service.py
│   ├── test_orchestrator.py
│   ├── test_self_healing.py
│   ├── test_git_analyzer.py
│   ├── test_log_analyzer.py
│   └── fixers/
│       ├── test_trivy_fixer.py
│       ├── test_crowdsec_fixer.py
│       ├── test_fail2ban_fixer.py
│       └── test_aide_fixer.py
├── integration/
│   ├── test_event_workflow.py
│   ├── test_approval_flow.py
│   ├── test_fix_execution.py
│   └── test_learning_loop.py
└── fixtures/
    ├── sample_trivy_scan.json
    ├── sample_crowdsec_decision.json
    ├── sample_config.yaml
    └── sample_git_log.txt
```

**Dependencies:**
```txt
# requirements-dev.txt
pytest==7.4.3
pytest-asyncio==0.21.1
pytest-cov==4.1.0
pytest-mock==3.12.0
black==23.12.0
flake8==6.1.0
mypy==1.7.1
```

**Acceptance Criteria:**
- [ ] Test-Struktur erstellt
- [ ] pytest.ini konfiguriert
- [ ] Dev-Dependencies installiert
- [ ] Erste Tests laufen

---

### ☐ 3.2 Unit Tests - Config
**Datei:** `tests/unit/test_config.py`

**Tests:**
- `test_load_valid_config()` - Config laden funktioniert
- `test_missing_config_file()` - Fehler bei fehlender Config
- `test_invalid_yaml()` - Fehler bei ungültigem YAML
- `test_missing_required_fields()` - Validation von Pflichtfeldern
- `test_default_values()` - Default-Werte werden gesetzt

**Acceptance Criteria:**
- [ ] 5+ Tests für Config-Loader
- [ ] 100% Coverage für config.py

---

### ☐ 3.3 Unit Tests - AI Service
**Datei:** `tests/unit/test_ai_service.py`

**Tests:**
- `test_ollama_call()` - Ollama-Request funktioniert
- `test_claude_fallback()` - Fallback zu Claude bei Ollama-Fehler
- `test_openai_fallback()` - Fallback zu OpenAI bei Claude-Fehler
- `test_retry_logic()` - Retries bei temporären Fehlern
- `test_timeout_handling()` - Timeout wird korrekt behandelt
- `test_context_integration()` - Context wird in Prompts integriert
- `test_previous_attempts()` - Previous Attempts in Prompts

**Acceptance Criteria:**
- [ ] 7+ Tests für AI Service
- [ ] Mocking von API-Calls
- [ ] 80%+ Coverage

---

### ☐ 3.4 Unit Tests - Orchestrator
**Datei:** `tests/unit/test_orchestrator.py`

**Tests:**
- `test_batch_creation()` - Batch wird korrekt erstellt
- `test_phase_execution()` - Phasen werden sequential ausgeführt
- `test_verify_logic()` - Verify nach Fix funktioniert
- `test_rollback_on_failure()` - Rollback bei Fehler
- `test_event_history_save()` - History wird gespeichert
- `test_success_rate_calc()` - Success-Rate-Berechnung
- `test_circuit_breaker()` - Circuit Breaker stoppt bei zu vielen Failures

**Acceptance Criteria:**
- [ ] 7+ Tests für Orchestrator
- [ ] 80%+ Coverage

---

### ☐ 3.5 Unit Tests - Git Analyzer
**Datei:** `tests/unit/test_git_analyzer.py`

**Tests:**
- `test_load_commit_history()` - Git-Log wird geladen
- `test_pattern_recognition()` - Fix-Commits werden erkannt
- `test_code_change_analysis()` - Diff-Analyse funktioniert
- `test_context_generation()` - Context für AI wird generiert

**Acceptance Criteria:**
- [ ] 4+ Tests für Git-Analyzer
- [ ] Mocking von Git-Commands
- [ ] 80%+ Coverage

---

### ☐ 3.6 Unit Tests - Log Analyzer
**Datei:** `tests/unit/test_log_analyzer.py`

**Tests:**
- `test_parse_fail2ban_logs()` - Fail2ban-Logs parsen
- `test_parse_crowdsec_logs()` - CrowdSec-Logs parsen
- `test_pattern_extraction()` - Pattern-Extraktion
- `test_anomaly_detection()` - Anomalien werden erkannt

**Acceptance Criteria:**
- [ ] 4+ Tests für Log-Analyzer
- [ ] Sample-Logs in Fixtures
- [ ] 80%+ Coverage

---

### ☐ 3.7 Unit Tests - Fixers
**Dateien:** `tests/unit/fixers/test_*.py`

**Tests pro Fixer:**
- `test_analyze_vulnerability()` - Vulnerability-Analyse
- `test_generate_fix()` - Fix-Generation
- `test_execute_fix()` - Fix-Execution (gemockt)
- `test_verify_fix()` - Verify nach Fix

**Fixer:**
- Trivy Fixer
- CrowdSec Fixer
- Fail2ban Fixer
- AIDE Fixer

**Acceptance Criteria:**
- [ ] 4 Tests pro Fixer (16 gesamt)
- [ ] Mocking von Command-Execution
- [ ] 80%+ Coverage

---

### ☐ 3.8 Integration Tests - Event Workflow
**Datei:** `tests/integration/test_event_workflow.py`

**Tests:**
- `test_full_trivy_workflow()` - Trivy-Event → Analyse → Fix → Verify
- `test_full_crowdsec_workflow()` - CrowdSec-Event → Ban → Verify
- `test_multi_event_batch()` - Mehrere Events → 1 Batch → Koordiniert

**Acceptance Criteria:**
- [ ] 3+ Integration-Tests
- [ ] End-to-End-Flow getestet
- [ ] Real Discord-Mock

---

### ☐ 3.9 Integration Tests - Learning Loop
**Datei:** `tests/integration/test_learning_loop.py`

**Tests:**
- `test_learning_from_success()` - Success wird gespeichert, nächster Fix nutzt es
- `test_learning_from_failure()` - Failure wird gespeichert, nächster Fix vermeidet es
- `test_adaptive_strategy()` - AI wählt beste Strategie basiert auf History

**Acceptance Criteria:**
- [ ] 3+ Learning-Tests
- [ ] Event-History-Integration
- [ ] Success-Rate-Tracking

---

### ☐ 3.10 Test-Coverage & CI/CD
**Ziel:** >80% Coverage

**Setup:**
```bash
# Run tests
pytest tests/ -v --cov=src --cov-report=html

# Coverage-Report
open htmlcov/index.html
```

**Optional: GitHub Actions**
```yaml
# .github/workflows/tests.yml
name: Tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - name: Run tests
        run: |
          pip install -r requirements.txt -r requirements-dev.txt
          pytest tests/ --cov=src --cov-fail-under=80
```

**Acceptance Criteria:**
- [ ] Coverage-Report funktioniert
- [ ] >80% Coverage erreicht
- [ ] CI/CD optional eingerichtet

---

# PHASE 4: NEUE FEATURES ⭐

**Priorität:** MITTEL
**Aufwand:** 3-4 Stunden
**Ziel:** Fehlende Commands implementieren

## Tasks:

### ☐ 4.1 Command: /set-approval-mode
**Datei:** `src/bot.py` (neuer Command)

**Features:**
```python
@app_commands.command(name="set-approval-mode", description="Ändere Auto-Remediation Approval Mode")
@app_commands.describe(mode="paranoid/auto/dry-run")
async def set_approval_mode(interaction: discord.Interaction, mode: str):
    """
    Modes:
    - paranoid: Frage bei JEDEM Event (default)
    - auto: Nur bei CRITICAL fragen
    - dry-run: Keine Execution, nur Logs
    """
    if mode not in ['paranoid', 'auto', 'dry-run']:
        await interaction.response.send_message("❌ Invalid mode", ephemeral=True)
        return

    # Update config in memory
    self.config.auto_remediation['approval_mode'] = mode

    # Save to file (optional)
    # self.config.save()

    await interaction.response.send_message(f"✅ Approval Mode: **{mode}**")
```

**Acceptance Criteria:**
- [ ] Command implementiert
- [ ] 3 Modi funktionieren
- [ ] Config-Update im Memory
- [ ] Discord-Feedback
- [ ] Tests für Command

---

### ☐ 4.2 Command: /get-ai-stats
**Datei:** `src/bot.py` (neuer Command)

**Features:**
```python
@app_commands.command(name="get-ai-stats", description="Zeige AI-Provider Status")
async def get_ai_stats(interaction: discord.Interaction):
    """
    Zeigt:
    - Ollama Status (Online/Offline)
    - Claude Status (API-Key valid?)
    - OpenAI Status
    - Last 10 AI-Calls (Success/Failure)
    - Average Response Time
    """
    stats = await self.ai_service.get_stats()

    embed = discord.Embed(title="🤖 AI Provider Stats", color=0x00FF00)
    embed.add_field(name="Ollama", value=stats['ollama']['status'])
    embed.add_field(name="Claude", value=stats['claude']['status'])
    embed.add_field(name="OpenAI", value=stats['openai']['status'])
    embed.add_field(name="Avg Response Time", value=f"{stats['avg_time']:.2f}s")

    await interaction.response.send_message(embed=embed)
```

**Acceptance Criteria:**
- [ ] Command implementiert
- [ ] Stats-Tracking in AIService
- [ ] Embed mit allen Infos
- [ ] Tests für Command

---

### ☐ 4.3 Command: /reload-context
**Datei:** `src/bot.py` (neuer Command)

**Features:**
```python
@app_commands.command(name="reload-context", description="Lade Project-Context neu")
async def reload_context(interaction: discord.Interaction):
    """
    Lädt alle Context-Files neu:
    - context/system/infrastructure.md
    - context/projects/*.md
    - DO-NOT-TOUCH Rules
    """
    await interaction.response.defer()

    try:
        self.context_manager.load_all_contexts()

        embed = discord.Embed(title="🔄 Context Reloaded", color=0x00FF00)
        embed.add_field(name="Projects", value=len(self.context_manager.projects))
        embed.add_field(name="Infrastructure", value="✅ Loaded")
        embed.add_field(name="DO-NOT-TOUCH Rules", value=len(self.context_manager.get_do_not_touch_list()))

        await interaction.followup.send(embed=embed)

    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")
```

**Acceptance Criteria:**
- [ ] Command implementiert
- [ ] Context-Reload funktioniert
- [ ] Embed mit Statistiken
- [ ] Error-Handling
- [ ] Tests für Command

---

### ☐ 4.4 Dependency Version-Pinning
**Datei:** `requirements.txt`

**Updates:**
```txt
# Vorher:
openai==1.54.0
anthropic==0.39.0

# Nachher:
openai>=1.54.0,<2.0.0
anthropic>=0.39.0,<1.0.0
aiohttp>=3.9.1,<4.0.0
discord.py>=2.3.2,<3.0.0
```

**Acceptance Criteria:**
- [ ] Alle Dependencies haben Upper-Bounds
- [ ] Bot startet weiterhin
- [ ] Tests laufen

---

### ☐ 4.5 Safe Upgrade Paths - Config
**Neue Datei:** `config/safe_upgrades.yaml`

**Auslagern von hardcoded Upgrades:**
```yaml
postgres:
  15:
    next: 16
    notes: "Requires pg_upgrade or dump/restore"
    risk: medium
    migration_url: "https://www.postgresql.org/docs/16/release-16.html"
  14:
    next: 15
    notes: "Minor breaking changes in replication"
    risk: low

redis:
  7:
    next: 8
    notes: "NOT YET RELEASED - Stay on 7.x"
    risk: unknown
  6:
    next: 7
    notes: "Check for deprecated commands, review ACL changes"
    risk: medium

mysql:
  8.0:
    next: 8.1
    notes: "Check for removed features"
    risk: low
  5.7:
    next: 8.0
    notes: "MAJOR upgrade - test extensively"
    risk: high

mongodb:
  6:
    next: 7
    notes: "Check compatibility mode"
    risk: medium

nginx:
  1.24:
    next: 1.25
    notes: "Stable release"
    risk: low
```

**Acceptance Criteria:**
- [ ] Config-File erstellt
- [ ] Loader in DockerImageAnalyzer
- [ ] Fallback zu hardcoded wenn File fehlt
- [ ] Tests für Config-Loading

---

### ☐ 4.6 Logging-Level reduzieren
**Dateien:** Verschiedene

**Änderungen:**
```python
# Vorher:
logger.info(f"   📝 Prompt-Länge: {prompt_length} Zeichen")

# Nachher:
logger.debug(f"   📝 Prompt-Länge: {prompt_length} Zeichen")
```

**Regeln:**
- `INFO`: Nur wichtige Events (Fix started, Success, Failure)
- `DEBUG`: Details (Prompt-Länge, Timeouts, etc.)
- `WARNING`: Probleme die keine Fehler sind
- `ERROR`: Echte Fehler

**Acceptance Criteria:**
- [ ] Alle verbose Logs sind DEBUG
- [ ] Production-Logs sind übersichtlich
- [ ] Debug-Mode in Config

---

# PHASE 5: MULTI-PROJEKT-SETUP 🌐

**Priorität:** MITTEL
**Aufwand:** 4-6 Stunden
**Ziel:** Bot überwacht alle Projekte, Kunden-Visibility

## Tasks:

### ☐ 5.1 Github Integration
**Neue Datei:** `src/integrations/github_integration.py`

**Features:**
1. **Webhook-Listener**
   - Push-Events empfangen
   - Pull-Request-Events
   - Release-Events

2. **Auto-Deploy auf Server**
   ```python
   async def handle_push_event(self, payload: Dict):
       repo = payload['repository']['name']
       branch = payload['ref'].split('/')[-1]

       if branch == 'main':
           logger.info(f"🚀 New push to {repo}/main - Triggering deploy")
           await self._deploy_to_server(repo)
   ```

3. **Discord-Notifications**
   - Push-Events in Discord posten
   - PR-Merge-Notifications
   - Deploy-Status

**Acceptance Criteria:**
- [ ] Webhook-Server läuft
- [ ] Push-Events werden empfangen
- [ ] Auto-Deploy funktioniert
- [ ] Discord-Notifications
- [ ] Tests für Github-Integration

---

### ☐ 5.2 Multi-Projekt-Monitoring
**Datei:** `src/integrations/project_monitor.py`

**Features:**
1. **Health-Checks pro Projekt**
   ```python
   projects = {
       'sicherheitstool': {
           'url': 'https://sicherheitstool.example.com/health',
           'expected_status': 200,
           'check_interval': 60
       },
       'guildscout': {...},
       'nexus': {...}
   }
   ```

2. **Uptime-Tracking**
   - Uptime pro Projekt
   - Downtime-Events
   - SLA-Berechnung

3. **Discord-Dashboard**
   - Embed mit allen Projekten
   - Status (🟢 Online, 🔴 Offline)
   - Letzte Änderung
   - Uptime %

**Acceptance Criteria:**
- [ ] Health-Checks für alle Projekte
- [ ] Uptime-Tracking
- [ ] Discord-Dashboard
- [ ] Tests für Monitoring

---

### ☐ 5.3 Kunden-Visibility Features
**Neue Commands:**

**`/projekt-status [name]`**
```python
@app_commands.command(name="projekt-status")
async def projekt_status(interaction: discord.Interaction, name: str):
    """
    Zeigt:
    - Status (Online/Offline)
    - Letzte Änderung (Git-Commit)
    - Uptime (%)
    - Aktuelle Version
    - Known Issues
    """
```

**`/alle-projekte`**
```python
@app_commands.command(name="alle-projekte")
async def alle_projekte(interaction: discord.Interaction):
    """Übersicht aller Projekte"""
    # Table mit allen Projekten
```

**Acceptance Criteria:**
- [ ] Commands implementiert
- [ ] Embeds mit allen Infos
- [ ] Tests für Commands

---

### ☐ 5.4 Auto-Deployment System
**Datei:** `src/integrations/deployment_manager.py`

**Features:**
1. **Deployment-Workflow**
   ```python
   async def deploy_project(self, project_name: str, branch: str = 'main'):
       # 1. Pull latest code
       # 2. Run tests (if configured)
       # 3. Backup current version
       # 4. Deploy new version
       # 5. Health-Check
       # 6. Rollback if failed
   ```

2. **Safety-Checks**
   - Tests müssen passen
   - Backup vor Deploy
   - Health-Check nach Deploy
   - Auto-Rollback bei Fehler

3. **Discord-Notifications**
   - Deploy started
   - Deploy progress
   - Deploy success/failure

**Acceptance Criteria:**
- [ ] Deployment-Workflow funktioniert
- [ ] Safety-Checks implementiert
- [ ] Auto-Rollback bei Fehler
- [ ] Discord-Feedback
- [ ] Tests für Deployment

---

### ☐ 5.5 Customer-Facing Channel
**Datei:** `config/config.yaml`

**Neue Channels:**
```yaml
channels:
  # Existing...
  customer_alerts: 0        # Kunden-sichtbare Alerts
  customer_status: 0        # Status-Updates für Kunden
  deployment_log: 0         # Deployment-Notifications
```

**Features:**
- Kunden-freundliche Nachrichten (weniger Tech-Details)
- Nur wichtige Events
- Schöne Embeds

**Acceptance Criteria:**
- [ ] Neue Channels in Config
- [ ] Kunden-Filter für Nachrichten
- [ ] Schöne Embeds

---

### ☐ 5.6 Incident Management
**Neue Datei:** `src/integrations/incident_manager.py`

**Features:**
1. **Incident-Detection**
   - Projekt ist down → Incident
   - Kritische Vulnerability → Incident
   - Deploy-Failure → Incident

2. **Incident-Tracking**
   - Status (Open/In Progress/Resolved)
   - Affected Projects
   - Timeline
   - Resolution Notes

3. **Discord-Thread pro Incident**
   - Automatischer Thread
   - Updates im Thread
   - Resolution-Summary

**Acceptance Criteria:**
- [ ] Incident-Detection
- [ ] Tracking-System
- [ ] Discord-Threads
- [ ] Tests für Incidents

---

# PHASE 6: DOKUMENTATION & CLEANUP 📚

**Priorität:** NIEDRIG
**Aufwand:** 2-3 Stunden
**Ziel:** Alles dokumentieren

## Tasks:

### ☐ 6.1 README Update
**Datei:** `README.md`

**Updates:**
- Learning System Features
- Git-Integration
- Multi-Projekt-Setup
- Neue Commands
- Test-Suite

**Acceptance Criteria:**
- [ ] README aktualisiert
- [ ] Alle Features dokumentiert

---

### ☐ 6.2 API-Dokumentation
**Neue Datei:** `docs/reference/api.md`

**Content:**
- Alle Commands
- Alle Integrationen
- Config-Optionen
- Webhook-API

**Acceptance Criteria:**
- [ ] API-Doku vollständig

---

### ☐ 6.3 Setup-Guide
**Neue Datei:** `docs/SETUP_GUIDE.md`

**Content:**
- Schritt-für-Schritt Installation
- Discord-Bot-Setup
- Config-Erstellung
- Service-Installation
- Troubleshooting

**Acceptance Criteria:**
- [ ] Setup-Guide vollständig

---

### ☐ 6.4 Code-Comments
**Alle Dateien**

**Rules:**
- Docstrings für alle Functions
- Type-Hints für alle Parameters
- Comments für komplexe Logik

**Acceptance Criteria:**
- [ ] Alle Functions haben Docstrings
- [ ] Type-Hints überall

---

### ☐ 6.5 CHANGELOG Update
**Datei:** `CHANGELOG.md`

**Version 3.1.0:**
- Git History Learning
- Log-File Analysis
- Code-Structure Analysis
- Vollständige Test-Suite
- Neue Commands
- Multi-Projekt-Setup
- Bug-Fixes

**Acceptance Criteria:**
- [ ] CHANGELOG aktualisiert

---

# TRACKING & PROGRESS 📊

## Current Progress: 0%

| Phase | Tasks | Completed | Progress |
|-------|-------|-----------|----------|
| Phase 0 | 5 | 0 | ⬜⬜⬜⬜⬜⬜⬜⬜⬜⬜ 0% |
| Phase 1 | 7 | 0 | ⬜⬜⬜⬜⬜⬜⬜⬜⬜⬜ 0% |
| Phase 2 | 9 | 0 | ⬜⬜⬜⬜⬜⬜⬜⬜⬜⬜ 0% |
| Phase 3 | 10 | 0 | ⬜⬜⬜⬜⬜⬜⬜⬜⬜⬜ 0% |
| Phase 4 | 6 | 0 | ⬜⬜⬜⬜⬜⬜⬜⬜⬜⬜ 0% |
| Phase 5 | 6 | 0 | ⬜⬜⬜⬜⬜⬜⬜⬜⬜⬜ 0% |
| Phase 6 | 5 | 0 | ⬜⬜⬜⬜⬜⬜⬜⬜⬜⬜ 0% |
| **TOTAL** | **48** | **0** | **0%** |

---

# NÄCHSTE SCHRITTE 🚀

## JETZT STARTEN:
1. **Phase 0.1** - Config-Struktur erstellen
2. **Phase 0.2** - Hardcoded IDs entfernen
3. **Phase 0.3** - Service File anpassen
4. **Phase 0.4** - Config Loader verbessern
5. **Phase 0.5** - Smoke-Test

**Nach deiner Bestätigung starte ich mit Phase 0! 🎯**

---

**Fragen? Anmerkungen? Änderungen am Plan?**
