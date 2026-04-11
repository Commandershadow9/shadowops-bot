# Security Analyst Agent — Implementierungsplan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Den ShadowOps Bot von einem passiven Scan-Bot zu einem autonom denkenden Security Analyst transformieren, der den Server aktiv schuetzt, Infrastruktur-Probleme selbst fixt, und Code-Issues als GitHub Issues dokumentiert.

**Architecture:** Aktivitaetsbasierter Agent der in den Offline-Phasen des Users autonome Claude-Code-Sessions startet. Die AI bekommt den kompletten Server-Zustand + akkumuliertes Wissen aus einer Postgres-DB und denkt frei ueber Security nach. Ergebnisse werden in der DB gespeichert (lernendes System), Briefings per Discord gepostet, und riskante Aktionen ueber Discord-Approval oder GitHub Issues kommuniziert.

**Tech Stack:** Python 3.11, asyncpg (Postgres), discord.py, Claude CLI, asyncio

**Bestehendes das wiederverwendet wird:**
- EventWatcher (CrowdSec/Fail2ban/AIDE/Trivy) bleibt unveraendert
- AI Engine (Codex+Claude Provider) wird erweitert
- SelfHealing + ApprovalView wird vom Analyst genutzt
- Discord-Channel-System +1 neuer Channel
- CommandExecutor fuer sichere Befehlsausfuehrung
- Fixer (Trivy/CrowdSec/Fail2ban/AIDE) bleiben als Spezialisten

---

## Task 1: Datenbank-Setup

**Zweck:** Postgres-Datenbank fuer das wachsende Wissen des Security Analyst erstellen.

**Files:**
- Create: `src/integrations/analyst/db_setup.sql`
- Create: `src/integrations/analyst/__init__.py`

### Step 1: SQL-Schema schreiben

Datei: `src/integrations/analyst/db_setup.sql`

```sql
-- Security Analyst Datenbank-Schema
-- Ausfuehren: docker exec -i guildscout-postgres psql -U security_analyst -d security_analyst < src/integrations/analyst/db_setup.sql

-- Sessions muessen zuerst erstellt werden (wird von findings referenziert)
CREATE TABLE IF NOT EXISTS sessions (
    id SERIAL PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ,
    trigger_type TEXT NOT NULL,
    topics_investigated TEXT[],
    findings_count INT DEFAULT 0,
    auto_fixes_count INT DEFAULT 0,
    issues_created INT DEFAULT 0,
    tokens_used INT DEFAULT 0,
    model_used TEXT,
    ai_summary TEXT,
    status TEXT DEFAULT 'running'
);

-- Akkumuliertes Wissen ueber den Server
CREATE TABLE IF NOT EXISTS knowledge (
    id SERIAL PRIMARY KEY,
    category TEXT NOT NULL,
    subject TEXT NOT NULL,
    content TEXT NOT NULL,
    confidence FLOAT DEFAULT 0.5,
    last_verified TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(category, subject)
);

-- Gefundene Security-Findings
CREATE TABLE IF NOT EXISTS findings (
    id SERIAL PRIMARY KEY,
    severity TEXT NOT NULL,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    affected_project TEXT,
    affected_files TEXT[],
    status TEXT DEFAULT 'open',
    fix_type TEXT,
    github_issue_url TEXT,
    auto_fix_details TEXT,
    rollback_command TEXT,
    found_at TIMESTAMPTZ DEFAULT NOW(),
    fixed_at TIMESTAMPTZ,
    session_id INT REFERENCES sessions(id)
);

-- Gelernte Patterns (waechst ueber Zeit)
CREATE TABLE IF NOT EXISTS learned_patterns (
    id SERIAL PRIMARY KEY,
    pattern_type TEXT NOT NULL,
    description TEXT NOT NULL,
    examples JSONB DEFAULT '[]',
    times_seen INT DEFAULT 1,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Service Health Snapshots (fuer Rollback-Entscheidungen)
CREATE TABLE IF NOT EXISTS health_snapshots (
    id SERIAL PRIMARY KEY,
    taken_at TIMESTAMPTZ DEFAULT NOW(),
    session_id INT REFERENCES sessions(id),
    services JSONB NOT NULL,
    docker_containers JSONB NOT NULL,
    system_resources JSONB NOT NULL
);

-- Indizes
CREATE INDEX IF NOT EXISTS idx_findings_status ON findings(status);
CREATE INDEX IF NOT EXISTS idx_findings_severity ON findings(severity);
CREATE INDEX IF NOT EXISTS idx_findings_project ON findings(affected_project);
CREATE INDEX IF NOT EXISTS idx_knowledge_category ON knowledge(category);
CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at DESC);
```

### Step 2: Datenbank und User auf Postgres erstellen

Run:
```bash
docker exec guildscout-postgres psql -U guildscout -c "CREATE USER security_analyst WITH PASSWORD 'SICHERES_PASSWORT';"
docker exec guildscout-postgres psql -U guildscout -c "CREATE DATABASE security_analyst OWNER security_analyst;"
```

### Step 3: Schema anwenden

Run:
```bash
docker exec -i guildscout-postgres psql -U security_analyst -d security_analyst < /home/cmdshadow/shadowops-bot/src/integrations/analyst/db_setup.sql
```
Expected: Alle Tabellen erstellt, keine Fehler.

### Step 4: Verbindung testen

Run:
```bash
docker exec guildscout-postgres psql -U security_analyst -d security_analyst -c "\dt"
```
Expected: 5 Tabellen (knowledge, findings, sessions, learned_patterns, health_snapshots)

### Step 5: `__init__.py` erstellen

Datei: `src/integrations/analyst/__init__.py`
```python
from .security_analyst import SecurityAnalyst
from .analyst_db import AnalystDB
from .activity_monitor import ActivityMonitor

__all__ = ['SecurityAnalyst', 'AnalystDB', 'ActivityMonitor']
```

### Step 6: Commit

```bash
git add src/integrations/analyst/db_setup.sql src/integrations/analyst/__init__.py
git commit -m "feat: Security Analyst DB-Schema und Setup"
```

---

## Task 2: AnalystDB — Datenbank-Zugriff

**Zweck:** Asyncpg-basierter DB-Layer fuer den Security Analyst.

**Files:**
- Create: `src/integrations/analyst/analyst_db.py`

**Dependencies:**
- `asyncpg` muss installiert werden

### Step 1: asyncpg zu den Dependencies hinzufuegen

Run: `cd ~/shadowops-bot && pip install asyncpg`

Falls requirements.txt existiert, Zeile hinzufuegen: `asyncpg>=0.29.0`

### Step 2: AnalystDB schreiben

Datei: `src/integrations/analyst/analyst_db.py`

Methoden:
- `connect()` / `close()` - Pool-Management
- `start_session(trigger_type) -> int` - Neue Session starten
- `end_session(session_id, summary, topics, ...)` - Session abschliessen
- `pause_session(session_id)` - Session pausieren (User aktiv)
- `get_last_session() -> Dict` - Letzte Session holen
- `get_all_knowledge() -> List[Dict]` - Gesamtes Wissen laden
- `upsert_knowledge(category, subject, content, confidence)` - Wissen aktualisieren
- `add_finding(...) -> int` - Finding speichern
- `get_open_findings() -> List[Dict]` - Offene Findings (sortiert nach Severity)
- `get_recent_findings(days) -> List[Dict]` - Findings der letzten N Tage
- `mark_finding_fixed(finding_id)` - Finding als gefixt markieren
- `save_health_snapshot(session_id, services, containers, resources)` - Snapshot speichern
- `add_pattern(pattern_type, description, example)` - Pattern speichern/hochzaehlen
- `get_patterns() -> List[Dict]` - Alle Patterns laden
- `build_ai_context() -> str` - Kompletten Kontext fuer AI-Prompt bauen

Der `build_ai_context()` baut einen Markdown-String mit:
- Wissen nach Kategorien gruppiert (mit Konfidenz-Werten)
- Offene Findings (Top 20, sortiert nach Severity)
- Letzte Session (Datum, Themen, Zusammenfassung)
- Gelernte Patterns (Top 10)
- Statistiken (30-Tage: Gesamt, Offen, Behoben, Kritisch)

Verwendet `asyncpg.create_pool(dsn, min_size=1, max_size=3)`.

### Step 3: Commit

```bash
git add src/integrations/analyst/analyst_db.py
git commit -m "feat: AnalystDB asyncpg Datenbank-Layer"
```

---

## Task 3: ActivityMonitor — Erkennung der User-Aktivitaet

**Zweck:** Erkennen ob der User aktiv entwickelt, um Konflikte zu vermeiden.

**Files:**
- Create: `src/integrations/analyst/activity_monitor.py`

### Step 1: ActivityMonitor schreiben

Klasse: `ActivityMonitor`

Konstanten:
- `PROJECTS` - Liste der Git-Verzeichnisse die geprueft werden
- `COOLDOWN_SECONDS = 1800` - 30 Min nach letzter Aktivitaet
- `CHECK_INTERVAL = 60` - Jede Minute pruefen
- `GIT_RECENT_MINUTES = 30` - Git-Commits der letzten 30 Min

Hauptmethode: `is_user_active() -> bool`
- Fuehrt 4 Checks parallel aus (asyncio.gather):
  1. `_check_ssh()` - `who | wc -l` > 0
  2. `_check_git_activity()` - `git log --since='30 minutes ago'` in allen Projekten
  3. `_check_ai_processes()` - `pgrep -cf '(claude|codex)'` > 0
  4. `_check_discord_presence()` - member.status in ('online', 'dnd')
- Wenn einer True: `last_activity = time.time()`, return True
- Sonst: Cooldown pruefen, erst idle wenn 30 Min vergangen

Weitere Methoden:
- `is_user_on_discord() -> str` - Returns 'online', 'idle', 'dnd', 'offline'
- `wait_for_idle()` - Blockiert bis User idle

Alle Shell-Befehle mit `asyncio.create_subprocess_shell()` und 5s Timeout.
Bei Exceptions: False zurueckgeben (im Zweifel nicht blockieren).

### Step 2: Testen

```bash
cd ~/shadowops-bot
python3 -c "
import asyncio
from src.integrations.analyst.activity_monitor import ActivityMonitor
# Quick-Test mit FakeBot
"
```
Expected: SSH sollte True sein (du bist verbunden).

### Step 3: Commit

```bash
git add src/integrations/analyst/activity_monitor.py
git commit -m "feat: ActivityMonitor erkennt User-Aktivitaet"
```

---

## Task 4: Autonome AI-Session — AI Engine erweitern

**Zweck:** Die AI Engine um Multi-Turn autonome Sessions erweitern.

**Files:**
- Modify: `src/integrations/ai_engine.py` (neue Methode)
- Create: `src/schemas/analyst_session.json`

### Step 1: Session-Output-Schema erstellen

Datei: `src/schemas/analyst_session.json`

Schema-Felder:
- `summary` (string) - Zusammenfassung
- `topics_investigated` (string[]) - Untersuchte Themen
- `findings` (array of objects) - Jedes Finding hat: severity, category, title, description, fix_type (auto_fixed|issue_needed|needs_decision|info_only), affected_project, affected_files, auto_fix_details, rollback_command, issue_title, issue_body
- `knowledge_updates` (array) - category, subject, content, confidence
- `health_check_passed` (boolean) - Services noch ok nach Fixes
- `next_priority` (string) - Was als naechstes untersuchen

Alle mit `additionalProperties: false`.

### Step 2: `run_analyst_session()` zur AIEngine hinzufuegen

Neue Methode in `AIEngine` Klasse:

```python
async def run_analyst_session(self, prompt, timeout=1800, max_turns=25):
```

Ablauf:
1. Temporaere Datei fuer strukturierten Output erstellen (`tempfile.mktemp`)
2. Prompt erweitern: AI soll Ergebnisse als JSON in die Datei schreiben
3. Claude CLI starten mit:
   - `--model claude-opus-4-6`
   - `--max-turns 25` (statt `--max-turns 1`)
   - KEIN `--output-format json` (Multi-Turn braucht Freiheit)
   - `cwd=/home/cmdshadow`
   - `CLAUDECODE` env-var entfernen (nested session verhindern)
4. Auf Completion warten (mit Timeout)
5. Output-Datei lesen und als Dict zurueckgeben
6. Fallback: stdout parsen wenn keine Datei
7. Cleanup: Temp-Datei loeschen

Wichtig: `asyncio.create_subprocess_exec` verwenden (NICHT `shell=True`).

### Step 3: Commit

```bash
git add src/schemas/analyst_session.json src/integrations/ai_engine.py
git commit -m "feat: Autonome Analyst-Session in AI Engine"
```

---

## Task 5: SecurityAnalyst — Das Herzstueck

**Zweck:** Die Hauptklasse die alles orchestriert.

**Files:**
- Create: `src/integrations/analyst/security_analyst.py`
- Create: `src/integrations/analyst/prompts.py`

### Step 1: Prompt-Vorlage schreiben

Datei: `src/integrations/analyst/prompts.py`

Zwei Konstanten:

**ANALYST_SYSTEM_PROMPT** - Enthaelt:
- Server-Beschreibung (OS, RAM, Projekte, Infrastruktur)
- Hinweis auf sensible Daten (Creator-Adressen, API-Keys)
- Auftrag: "Untersuche die Security. Denke frei. Keine Checkliste."
- Moegliche Untersuchungsbereiche (nicht limitierend)
- SAFETY-REGELN:
  - DARF: Dateien lesen, lesende Shell-Befehle, UFW-Regeln aendern (mit Backup), Permissions anpassen, Docker Cleanup, Pakete updaten, in security_analyst DB schreiben
  - DARF NICHT: rm -rf, .env loeschen, git push, docker compose down, Services stoppen, Prod-DB-Daten aendern, Ports auf 0.0.0.0, Security-Tools deaktivieren
- Nach-Fix-Regeln:
  - docker ps pruefen (alle Container UP)
  - systemctl Checks
  - Health-Endpoints pruefen
  - Bei Fehler: SOFORT rollbacken
- Code-Problem-Regeln:
  - NICHT selbst fixen
  - Dokumentieren mit verifiziertem Dateipfad + Zeilennummer
  - issue_title und issue_body liefern

**ANALYST_CONTEXT_TEMPLATE** - Platzhalter fuer:
- Bisheriges Wissen aus der DB
- Fokus-Empfehlung (nicht wiederholen was gestern geprueft wurde)

### Step 2: SecurityAnalyst Klasse schreiben

Datei: `src/integrations/analyst/security_analyst.py`

Klasse: `SecurityAnalyst`

Konstanten:
- `MAX_SESSIONS_PER_DAY = 1`
- `SESSION_TIMEOUT = 1800` (30 Min)
- `SESSION_MAX_TURNS = 25`
- `APPROVAL_TIMEOUT = 300` (5 Min)
- `MAIN_LOOP_INTERVAL = 60` (1 Min)

Init:
- `self.db = AnalystDB(dsn)`
- `self.activity_monitor = ActivityMonitor(bot)`
- Session-State Tracking

Methoden:
- `start()` - DB connect + Main-Loop Task starten
- `stop()` - Task canceln + DB close
- `_main_loop()` - Endlosschleife:
  - Tages-Reset (sessions_today = 0 bei neuem Tag)
  - Briefing posten wenn User online kommt und Briefing pending
  - Session starten wenn: User idle + sessions_today < max + keine laufende Session
- `_run_session()` - Eine komplette Session:
  1. Health Snapshot VORHER
  2. AI-Kontext aus DB bauen
  3. Pruefen ob User noch idle
  4. Autonome AI-Session starten
  5. Health Snapshot NACHHER + Vergleich
  6. Knowledge-Updates in DB speichern
  7. Findings in DB speichern + GitHub Issues erstellen
  8. Session abschliessen
  9. Briefing posten (oder pending setzen wenn User offline)
- `_take_health_snapshot(session_id)` - Docker + systemd + Ressourcen
- `_compare_health(before, after)` - True wenn alles noch laeuft
- `_send_health_alert(before, after)` - Kritischer Discord-Alert
- `_post_briefing(result)` - Discord-Embed mit:
  - Status-Emoji (gruen/gelb/orange/rot)
  - Untersuchte Themen
  - Auto-Fixes (was wurde erledigt)
  - Braucht Entscheidung (mit Severity-Icons)
  - Naechste Prioritaet
  - Stats-Footer
- `_create_github_issue(finding)` - Via `gh issue create`
  - Repo-Mapping: guildscout/zerodox/shadowops
  - Labels: security + priority:severity
  - Strukturierter Body mit Severity, Kategorie, Dateien
- `manual_scan(focus=None)` - Manueller Trigger (Discord-Command)

### Step 3: Commit

```bash
git add src/integrations/analyst/security_analyst.py src/integrations/analyst/prompts.py
git commit -m "feat: SecurityAnalyst Kernklasse mit Briefing und GitHub-Issues"
```

---

## Task 6: Bot-Integration

**Zweck:** SecurityAnalyst in den Bot einbauen.

**Files:**
- Modify: `src/bot.py`
- Modify: `config/config.yaml`

### Step 1: Config erweitern

In `config/config.yaml`:

```yaml
security_analyst:
  enabled: true
  database_dsn: "postgresql://security_analyst:SICHERES_PASSWORT@127.0.0.1:5433/security_analyst"
  max_sessions_per_day: 1
  session_timeout: 1800
  session_max_turns: 25
  model: "claude-opus-4-6"
```

Unter `channels:`:
```yaml
  security_briefing: 0
```

### Step 2: Bot.py anpassen

1. Import: `from integrations.analyst import SecurityAnalyst`
2. In `__init__`: `self.security_analyst = None`
3. Phase 6: SecurityAnalyst initialisieren und starten (wenn enabled)
4. Shutdown: `security_analyst.stop()` aufrufen

### Step 3: Discord Slash-Command `/security-scan`

Neuer Command der `manual_scan(focus)` aufruft und das Briefing postet.

### Step 4: Commit

```bash
git add src/bot.py config/config.yaml
git commit -m "feat: SecurityAnalyst in Bot integriert"
```

---

## Task 7: Testen und Validieren

### Step 1: DB-Verbindung testen

```bash
cd ~/shadowops-bot
python3 -c "import asyncio; from src.integrations.analyst.analyst_db import AnalystDB; ..."
```
Expected: Session erstellen und Knowledge schreiben funktioniert.

### Step 2: ActivityMonitor testen

Expected: SSH = True (du bist verbunden).

### Step 3: Bot-Start testen

```bash
sudo systemctl restart shadowops-bot
sleep 5
sudo journalctl -u shadowops-bot --since "30 seconds ago" --no-pager | tail -20
```
Expected: "SecurityAnalyst gestartet" in Logs.

### Step 4: Manuellen Scan testen (Discord)

`/security-scan` im Discord ausfuehren.
Expected: Briefing-Embed erscheint nach einigen Minuten.

### Step 5: Idle-Detection testen

Ueber VPN verbinden, alle SSH-Sessions schliessen, warten ob der Analyst nach 30 Min startet.

---

## Dateien-Uebersicht

| Datei | Aktion | Zweck |
|-------|--------|-------|
| `src/integrations/analyst/__init__.py` | CREATE | Package-Init |
| `src/integrations/analyst/db_setup.sql` | CREATE | Postgres-Schema |
| `src/integrations/analyst/analyst_db.py` | CREATE | DB-Zugriff (asyncpg) |
| `src/integrations/analyst/activity_monitor.py` | CREATE | User-Aktivitaet erkennen |
| `src/integrations/analyst/security_analyst.py` | CREATE | Hauptklasse |
| `src/integrations/analyst/prompts.py` | CREATE | AI-Prompt-Vorlagen |
| `src/schemas/analyst_session.json` | CREATE | Output-Schema fuer AI |
| `src/integrations/ai_engine.py` | MODIFY | +run_analyst_session() |
| `src/bot.py` | MODIFY | Phase 6 + Import + Shutdown |
| `config/config.yaml` | MODIFY | +security_analyst Sektion |

## Abhaengigkeiten

```
Task 1 (DB-Setup)
    |
    v
Task 2 (AnalystDB) ----+
                        |
Task 3 (ActivityMonitor) +---> Task 5 (SecurityAnalyst) --> Task 6 (Bot) --> Task 7 (Test)
                        |
Task 4 (AI Engine) -----+
```

Tasks 2, 3, 4 sind unabhaengig voneinander und koennen parallel implementiert werden.

## Risiken und Mitigationen

| Risiko | Mitigation |
|--------|-----------|
| AI macht etwas kaputt | Safety-Regeln im Prompt + Health-Check vor/nach Session |
| Token-Verschwendung | Max 1 Session/Tag, 25 Turns, 30 Min Timeout |
| Bot und User gleichzeitig | ActivityMonitor (SSH, Git, Discord, Claude-Prozesse) |
| AI halluziniert Findings | Prompt verlangt verifizierte Dateipfade |
| Postgres Connection-Pool | max_size=3, min_size=1, Command-Timeout 30s |
| Service-Ausfall nach Fix | Health-Snapshot Vergleich + sofortiger Rollback + Alert |
