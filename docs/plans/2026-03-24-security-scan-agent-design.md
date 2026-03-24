# SecurityScanAgent — Design-Doc für Nächste Session

> **WICHTIG FÜR DEN IMPLEMENTIERER:** Lies zuerst den ALTEN Security Analyst KOMPLETT durch,
> bevor du eine Zeile Code schreibst. Verstehe WAS er macht, WARUM er es macht, und WIE.
> Dann baue es Stück für Stück im Agent Framework Pattern nach — besser, sauberer, aber
> mit der gleichen Tiefe und Autonomie.

**Goal:** Den DeepScanMode der Security Engine v6 zu einem vollwertigen autonomen Security Agent umbauen — nach dem gleichen Pattern wie der SEO Agent im Agent Framework. Die KI arbeitet frei mit Shell-Zugriff, findet selbst Lücken, fixt direkt oder erstellt PRs/Issues.

---

## PFLICHT: Den alten Analyst zuerst studieren

### Diese Dateien KOMPLETT lesen und verstehen:

| Datei | Was drin ist | Warum wichtig |
|-------|-------------|---------------|
| `src/integrations/analyst/security_analyst.py` | Gesamte Scan+Fix Logik, 1500+ Zeilen | DAS ist der Agent den wir nachbauen |
| `src/integrations/analyst/prompts.py` | System-Prompt, Context-Template, Fix-Prompt | Die "Seele" des Agents — was die KI kann/darf/soll |
| `src/integrations/analyst/analyst_db.py` | 9 DB-Tabellen, build_ai_context(), build_scan_plan() | Wie Wissen persistent gespeichert und abgerufen wird |
| `src/integrations/analyst/activity_monitor.py` | Idle-Detection (SSH, Git, Claude, Discord) | Wann der Agent arbeiten darf ohne zu stören |
| `src/integrations/ai_engine.py` | run_analyst_session(), Codex+Claude Calls | Wie die KI aufgerufen wird (stdin, timeouts, fallback) |
| `src/schemas/analyst_session.json` | Structured Output Schema | Was die KI zurückgeben MUSS |

### Was der alte Analyst RICHTIG macht (übernehmen!):

1. **KI arbeitet frei** — bekommt Shell-Zugriff (Bash, Read, Grep, Glob) und entscheidet selbst was geprüft wird
2. **Datengetriebene Scan-Planung** — Coverage-Lücken, Hotspots, Git-Activity bestimmen was gescannt wird
3. **Adaptive Session-Steuerung** — 0-3 Sessions/Tag je nach Backlog (20+ Findings = fix_only, 0 = maintenance)
4. **Full Learning Pipeline** — jeder Fix-Versuch gespeichert, Verifikation nach 14 Tagen, Knowledge-Decay
5. **Fix-Phase mit Claude** — Claude bekommt Findings + vorherige Versuche, fixt Code direkt oder erstellt PR
6. **Health-Snapshots** — Service-Status vor/nach Session, Regressionen werden erkannt
7. **Activity Monitor** — Läuft nur wenn User idle (SSH, Git, Discord, Claude-Sessions geprüft)
8. **Geschützte Infrastruktur** — Bind-Adressen, Ports, Docker-Netzwerk nur per Issue/PR änderbar
9. **Issue Quality-Gates** — Mindest-Content, Duplikat-Check (DB + GitHub), Projekt-Skip-Liste
10. **Findings-Selbstbewertung** — confidence, discovery_method, is_false_positive, is_actionable

### Was der alte Analyst FALSCH macht (verbessern!):

1. **Isolierte DB** — AnalystDB (asyncpg) kennt KnowledgeBase (psycopg2) nicht → SecurityDB v6 nutzen
2. **Kein Phase-Type-System** — Fix-Phase hat keine semantische Struktur → PhaseTypeExecutor nutzen
3. **Kein Cross-Mode-Lock** — Analyst und Orchestrator können gleichzeitig fixen → remediation_status nutzen
4. **Monolithische Klasse** — 1500 Zeilen in einer Datei → Agent Framework Pattern (Base + Hooks)
5. **Keine Provider-Chain** — Hardcoded Codex→Claude → FixProvider Registry nutzen
6. **Kein Circuit Breaker** — Fehler→Backoff ist simpel → richtiger CircuitBreaker
7. **Keine NoOp-Detection** — Fixt Dinge die schon gefixt sind → NoOpProvider

---

## Architektur: SecurityScanAgent

### Nach Agent Framework Pattern (`~/agents/core/`)

```
~/agents/projects/security/          ← NEUES Projekt im Agent Framework
├── agent.py                         ← SecurityScanAgent(AgentBase)
├── handler.py                       ← SecurityHandler (Scan + Fix Logik)
├── prompts.py                       ← System-Prompt + Context (vom alten Analyst übernehmen!)
├── activity_monitor.py              ← Idle-Detection (vom alten Analyst übernehmen!)
└── config.yaml                      ← Konfiguration

ODER (Alternative): Im Bot bleiben aber Framework-Patterns nutzen

~/shadowops-bot/src/integrations/security_engine/
├── scan_agent.py                    ← SecurityScanAgent (ersetzt deep_scan.py)
├── prompts.py                       ← Vom alten analyst/prompts.py übernehmen
├── activity_monitor.py              ← Vom alten analyst/activity_monitor.py übernehmen
└── ... (restliche Engine-Module bleiben)
```

### Entscheidung: Im Agent Framework ODER im Bot?

**Empfehlung: Im Bot bleiben** — der Security Agent braucht direkte Integration mit:
- Discord (Slash-Commands, Embed-Updates, Approval-Buttons)
- EventWatcher (Reactive Mode Events)
- ProactiveMode (Coverage-Reports)
- Alle 4 Fixer (über Registry)

Das Agent Framework ist für standalone Agents (SEO, Feedback) die per Redis/PG-Events getriggert werden. Der Security Agent ist tiefer integriert.

**ABER:** Die Patterns des Frameworks übernehmen:
- AgentBase ABC mit Hooks (on_analysis_failed, get_autonomy_rules, enrich_context)
- AIProviderChain für Codex→Claude Fallback
- CircuitBreaker pro Provider
- Token-Budget Management

---

## Kern-Komponenten (Was zu bauen ist)

### 1. SecurityScanAgent (ersetzt DeepScanMode)

```python
class SecurityScanAgent:
    """Autonomer Security Agent — arbeitet wie SEO Agent aber für Security"""

    # Vom alten Analyst übernehmen:
    - Adaptive Session-Planung (_plan_session)
    - Activity Monitor Integration (nur wenn User idle)
    - Pre-Session Maintenance (Git-Sync, Fix-Verifikation, Knowledge-Decay)
    - Post-Session Briefing (Discord-Notification, Pending wenn User offline)

    # NEU (Security Engine v6 nutzen):
    - SecurityDB statt AnalystDB (unified, gleiche Tabellen)
    - PhaseTypeExecutor für Fix-Phase (statt roher Claude-Call)
    - remediation_status für Cross-Mode-Lock
    - LearningBridge für Cross-Agent Learning
    - CircuitBreaker pro AI-Provider
    - NoOpProvider in Fix-Chain

    # Hooks (Agent Framework Pattern):
    async def on_scan_complete(session_result): ...
    async def on_fix_failed(finding, error): ...
    async def on_regression_detected(finding, verification): ...
    async def get_autonomy_rules() -> str: ...
    async def enrich_scan_context(mode) -> str: ...
```

### 2. AI-Session (Scan-Phase)

**Exakt wie der alte Analyst — KI arbeitet frei:**

```python
async def _run_scan_session(self, mode, plan):
    # 1. Kontext bauen (build_ai_context — VOM ALTEN ANALYST ÜBERNEHMEN)
    #    - Wissen nach Kategorie + Confidence
    #    - Letzte Session-Info
    #    - Patterns, Orchestrator-Fixes, IP-Reputation
    #    - Fix-Effektivität, Finding-Trends
    #    - Git-Delta, Scan-Lücken, Finding-Qualität

    # 2. Scan-Plan bauen (build_scan_plan — VOM ALTEN ANALYST ÜBERNEHMEN)
    #    - Coverage-Lücken (höchste Prio)
    #    - Regressionen
    #    - Hotspots
    #    - Geänderte Projekte

    # 3. Prompt zusammenstellen (prompts.py — VOM ALTEN ANALYST ÜBERNEHMEN)
    #    - System-Prompt mit Server-Kontext
    #    - Erlaubte Tools (Bash, Read, Grep, Glob)
    #    - Geschützte Infrastruktur
    #    - Ausgabe-Schema

    # 4. AI-Call (Codex Primary → Claude Fallback)
    #    - Codex: `codex exec --output-schema analyst_session.json`
    #    - Claude: `claude -p --allowed-tools ... --max-turns N`
    #    - Prompt via stdin (kein Leak in ps/proc)
    #    - Timeout: 20-120 Min je nach Modus

    # 5. Ergebnisse verarbeiten (_process_results — VOM ALTEN ANALYST ÜBERNEHMEN)
    #    - Findings in DB (mit Duplikat-Check)
    #    - Knowledge-Updates
    #    - Coverage aufzeichnen
    #    - Finding-Quality Assessments
```

### 3. Fix-Phase (Claude mit Shell-Zugriff)

**Exakt wie der alte Analyst — Claude fixt selbständig:**

```python
async def _run_fix_session(self, findings):
    # 1. Findings formatieren (mit vorherigen Fix-Versuchen!)
    #    → Learning: "Letztes Mal hast du X probiert, hat nicht funktioniert"

    # 2. Fix-Prompt (FIX_SESSION_PROMPT — VOM ALTEN ANALYST ÜBERNEHMEN)
    #    - Geschützte Infrastruktur (nur per Issue/PR)
    #    - System-Fixes direkt (Permissions, Firewall, Configs)
    #    - Code-Fixes per PR (Branch fix/security-findings)

    # 3. Claude-Call mit vollen Tools
    #    - `claude -p --allowed-tools Bash(*),Read,Write,Grep,Glob`
    #    - max_turns=200, timeout=2h
    #    - Claude arbeitet FREI — liest Code, ändert Dateien, erstellt PRs

    # 4. Ergebnisse: Pro Finding
    #    - "fixed" → Finding closed, Fix-Attempt recorded
    #    - "pr_created" → PR-URL gespeichert
    #    - "failed" → Fix-Attempt recorded (Learning für nächstes Mal)

    # NEU: Durch PhaseTypeExecutor + Registry
    #    - NoOp-Check VOR dem Fix (ist schon gefixt?)
    #    - Phase-Type: 'fix' für Config, 'contain' für Sofort-Block
    #    - Cross-Mode-Lock: claim_event() vor Fix
```

### 4. Activity Monitor (1:1 übernehmen)

```
Vom alten Analyst übernehmen — funktioniert perfekt:
- SSH-Sessions prüfen (who | wc -l)
- Git-Activity (Commits in letzten 30 Min)
- AI-Prozesse (claude --session-id)
- Discord-Presence (Owner online/dnd?)
- 30-Min Cooldown nach letzter Aktivität
```

### 5. Prompts (1:1 übernehmen + erweitern)

```
Vom alten Analyst übernehmen:
- ANALYST_SYSTEM_PROMPT (Server-Kontext, erlaubte Tools, Regeln)
- ANALYST_CONTEXT_TEMPLATE (Knowledge, Findings, Scan-Plan)
- FIX_SESSION_PROMPT (Geschützte Infra, Vorgehen, Ausgabe-Format)

Erweitern um:
- Phase-Type Awareness ("Du bist in einer FIX-Phase, nicht RECON")
- Cross-Agent Wissen ("Der SEO Agent hat folgendes gefunden: ...")
- Provider-Chain Status ("Codex Quota aufgebraucht, nutze Claude")
```

---

## Migrations-Plan (Schrittweise)

### Schritt 1: Prompts + Activity Monitor kopieren
- `analyst/prompts.py` → `security_engine/prompts.py` (1:1 Kopie)
- `analyst/activity_monitor.py` → `security_engine/activity_monitor.py` (1:1 Kopie)
- Keine Logik-Änderung, nur neuer Pfad

### Schritt 2: SecurityScanAgent Grundstruktur
- `security_engine/scan_agent.py` erstellen
- Main-Loop vom alten Analyst übernehmen
- SecurityDB statt AnalystDB nutzen (gleiche Tabellen, anderer Access-Layer)
- Hooks definieren (on_scan_complete, on_fix_failed, etc.)

### Schritt 3: Scan-Phase migrieren
- `build_ai_context()` in SecurityDB implementieren (gleiche Queries)
- `build_scan_plan()` in SecurityDB implementieren
- AI-Call über bestehenden ai_engine.run_analyst_session()
- _process_results() übernehmen

### Schritt 4: Fix-Phase migrieren
- Findings laden + formatieren (mit vorherigen Versuchen)
- Claude-Call über ai_engine.run_fix_session()
- Ergebnisse über PhaseTypeExecutor verarbeiten
- Cross-Mode-Lock (remediation_status)

### Schritt 5: Integration + Test
- SecurityEngine.deep_scan → SecurityScanAgent
- In bot.py: security_engine.start() startet den Scan-Agent
- Alter Analyst deaktivieren (config: security_analyst.enabled = false)
- Tests: Session-Lifecycle, Scan-Phase, Fix-Phase, Activity Monitor

### Schritt 6: Alter Analyst entfernen
- `src/integrations/analyst/security_analyst.py` → deprecated
- Imports in bot.py bereinigen
- Tests aktualisieren

---

## Checkliste: Was vom alten Analyst übernommen werden MUSS

- [ ] Adaptive Session-Planung (fix_only/full_scan/quick_scan/maintenance)
- [ ] Activity Monitor (SSH + Git + Claude + Discord Idle-Detection)
- [ ] Pre-Session Maintenance (Git-Sync, Fix-Verifikation, Knowledge-Decay)
- [ ] build_ai_context() — 11 Kontext-Bereiche (Wissen, Patterns, IP-Reputation, etc.)
- [ ] build_scan_plan() — Coverage-Lücken, Hotspots, Regressionen, Git-Activity
- [ ] System-Prompt mit geschützter Infrastruktur + erlaubten Tools
- [ ] Structured Output Schema (analyst_session.json)
- [ ] Codex-Call: `codex exec --output-schema` via stdin
- [ ] Claude-Call: `claude -p --allowed-tools --max-turns` via stdin
- [ ] Codex-Quota-Cache (6h Skip nach Quota-Fehler)
- [ ] Findings-Verarbeitung mit Duplikat-Check (pg_trgm similarity)
- [ ] Fix-Phase: Claude mit Shell-Zugriff, vorherige Versuche im Kontext
- [ ] Health-Snapshots vor/nach Session
- [ ] Finding-Quality Assessments (confidence, false_positive, discovery_method)
- [ ] Scan-Coverage Tracking (welche Bereiche geprüft/übersprungen)
- [ ] Knowledge-Updates aus AI-Output
- [ ] Discord-Briefing (sofort wenn online, pending wenn offline)
- [ ] Failure-Backoff (30min → 2h → 6h → Tag-Ende)
- [ ] Token-Tracking (Delta-Messung)
- [ ] Issue Quality-Gates (MIN_TITLE=10, MIN_BODY=30, Dedup)
- [ ] Midnight Reset (Session-Counter)

---

## Was NICHT übernommen wird (Security Engine v6 macht das besser)

- ~~AnalystDB~~ → SecurityDB (unified asyncpg, gleiche Tabellen)
- ~~Hardcoded if/elif Fixer~~ → FixProvider Registry + Adapter
- ~~Keine Cross-Mode-Koordination~~ → remediation_status (Event-Claiming)
- ~~Separates Learning~~ → LearningBridge (Cross-Agent)
- ~~Simpler Fehler-Backoff~~ → CircuitBreaker (per-Key)
- ~~Keine NoOp-Detection~~ → NoOpProvider in Provider-Chain
