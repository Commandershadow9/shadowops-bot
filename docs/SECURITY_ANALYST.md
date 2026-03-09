# Security Analyst — Autonomer AI Security Engineer

## Übersicht

Der Security Analyst ist ein autonomer AI-Agent, der wie ein echter Security Engineer denkt — kein Checklisten-Abarbeiter. Er bekommt Rohdaten vom Server und entscheidet selbst, was er untersucht.

**Kernprinzip:** Freies Denken, nicht Job-Execution.

### Wie funktioniert es?

1. **ActivityMonitor** erkennt, wann der User idle ist (SSH, Git, Claude-Prozesse, Discord-Präsenz)
2. Sobald 30 Minuten Inaktivität vergangen sind, startet eine autonome Session
3. **Claude Code CLI** (`claude -p`) wird mit Multi-Turn-Zugriff gestartet (max 25 Turns)
4. Der Agent untersucht frei: Logs, Docker, Firewall, Zertifikate, Berechtigungen, ...
5. Nach der Session: Health-Vergleich (vor/nach), Briefing an Discord, Issues auf GitHub
6. Wenn der User wieder online kommt, wird das Briefing-Embed zugestellt

### Was darf er?

| Erlaubt | Verboten |
|---------|----------|
| UFW-Regeln anpassen | Code pushen |
| Dateiberechtigungen fixen | `rm -rf` auf Projektverzeichnisse |
| Systemd-Services prüfen | Docker Volumes löschen |
| Docker-Container inspizieren | `.env`-Dateien ändern |
| Log-Analyse | `git push` |
| Zertifikate prüfen | Ports ohne UFW-Regel öffnen |
| `gh issue create` | Deployments auslösen |

### Was passiert bei Code-Problemen?

Der Analyst erstellt **GitHub Issues** mit Beschreibung, betroffenen Dateien und Severity. Er fixt niemals Code direkt — das macht der Mensch.

### Issue-Routing

Issues landen automatisch im richtigen GitHub-Repo:

| Projekt-Keyword | Repo |
|----------------|------|
| `guildscout` | Commandershadow9/GuildScout |
| `zerodox` | Commandershadow9/ZERODOX |
| `shadowops` | Commandershadow9/shadowops-bot |
| Server / Infra / unbekannt | Commandershadow9/shadowops-bot (Default) |

Matching ist Substring-basiert: `"GuildScout / Security Analyst"` matcht `guildscout` → GuildScout-Repo.

Issues werden erstellt bei:
- `fix_type: issue_needed` — immer
- `fix_type: needs_decision` — nur bei Severity critical/high/medium

---

## Architektur

```
┌────────────────────────────────────────────────────┐
│                  ShadowOps Bot                      │
│              (Phase 6: SecurityAnalyst)             │
└──────────────────────┬─────────────────────────────┘
                       │
    ┌──────────────────▼──────────────────┐
    │         SecurityAnalyst             │
    │   (security_analyst.py, 839 LOC)   │
    │                                     │
    │  - Main Loop (60s Intervall)        │
    │  - Session-Management               │
    │  - Health Snapshots (vor/nach)       │
    │  - Discord Briefings                │
    │  - GitHub Issue Creation            │
    └──┬──────────┬──────────┬───────────┘
       │          │          │
  ┌────▼────┐ ┌──▼────┐ ┌──▼──────────┐
  │ Activity │ │Analyst│ │  AI Engine  │
  │ Monitor  │ │  DB   │ │ (Claude CLI)│
  │ (260 LOC)│ │(546)  │ │ Multi-Turn  │
  └──────────┘ └───────┘ └─────────────┘
       │          │
  4 Checks:   asyncpg Pool
  - SSH        5 Tabellen:
  - Git        - sessions
  - AI Procs   - knowledge
  - Discord    - findings
               - learned_patterns
               - health_snapshots
```

## Dateien

| Datei | LOC | Zweck |
|-------|-----|-------|
| `src/integrations/analyst/security_analyst.py` | 839 | Hauptklasse, Session-Orchestrierung |
| `src/integrations/analyst/analyst_db.py` | 546 | asyncpg DB-Layer (Pool, CRUD, AI-Kontext) |
| `src/integrations/analyst/activity_monitor.py` | 260 | User-Aktivitätserkennung |
| `src/integrations/analyst/prompts.py` | 178 | System-Prompt + Kontext-Template |
| `src/integrations/analyst/__init__.py` | 5 | Exports |
| `src/integrations/analyst/db_setup.sql` | 79 | Postgres-Schema |
| `src/schemas/analyst_session.json` | 74 | JSON-Schema für Session-Output |

## Konfiguration

In `config/config.yaml`:

```yaml
security_analyst:
  enabled: true
  database_dsn: "postgresql://security_analyst:PASSWORD@127.0.0.1:5433/security_analyst"
  max_sessions_per_day: 1
  session_timeout: 1800      # 30 Minuten max pro Session
  session_max_turns: 25      # Max CLI-Turns pro Session
  model: "claude-opus-4-6"   # Welches Modell für die Sessions
```

Channel in `channels:`:
```yaml
channels:
  security_briefing: CHANNEL_ID  # Oder 0 für DM an Admin
```

## Datenbank

Läuft auf dem GuildScout Postgres (Port 5433):

```bash
# Schema anlegen
docker exec -i guildscout-postgres psql -U security_analyst -d security_analyst \
  < src/integrations/analyst/db_setup.sql

# DB-User erstellen (einmalig)
docker exec -i guildscout-postgres psql -U guildscout -d guildscout -c \
  "CREATE USER security_analyst WITH PASSWORD 'sec_analyst_2026';"
docker exec -i guildscout-postgres psql -U guildscout -d guildscout -c \
  "CREATE DATABASE security_analyst OWNER security_analyst;"
```

### Tabellen

| Tabelle | Zweck |
|---------|-------|
| `sessions` | Laufende/abgeschlossene Analyse-Sessions |
| `knowledge` | Akkumuliertes Wissen (UPSERT per category+subject) |
| `findings` | Security-Findings mit Severity, Status, Fix-Details |
| `learned_patterns` | Wiedererkannte Muster (JSONB examples) |
| `health_snapshots` | Service-Zustand vor/nach Sessions |

## ActivityMonitor

Prüft 4 Quellen parallel via `asyncio.gather()`:

| Check | Methode | Bedeutung |
|-------|---------|-----------|
| SSH | `who` Befehl | User hat Terminal offen |
| Git | `git log --since 30min` in 5 Projektverzeichnissen | Kürzliche Commits |
| AI-Prozesse | `pgrep -a claude \| grep --session-id` | Nur interaktive Claude-Sessions (ignoriert Agents, MCP-Server) |
| Discord | `member.status` via discord.py | User ist online/idle/dnd |

**Cooldown:** 30 Minuten nach letzter erkannter Aktivität.

## Claude CLI Session

Die autonome Session wird über `AIEngine.run_analyst_session()` gestartet:

```bash
claude -p "PROMPT" \
  --max-turns 25 \
  --output-format text \
  --model claude-opus-4-6 \
  --allowedTools "Bash(git:*),Bash(docker:*),Bash(ufw:*),...,Read,Glob,Grep,Write,Edit,ToolSearch,mcp__docker__list-containers,..."
```

**Wichtig:**
- `--allowedTools` Syntax: `Bash(command:*)` mit **Doppelpunkt** (nicht Leerzeichen!)
- MCP-Tools aus `~/.claude.json` sind in `-p` Sessions sichtbar, müssen aber in `--allowedTools` stehen
- `ToolSearch` muss erlaubt sein, damit deferred MCP-Tools geladen werden können
- Nur whitelisted Bash-Prefixe — kein `rm`, kein `dd`

### MCP-Tools (verfügbar)

| MCP Server | Tools | Zugriff |
|------------|-------|---------|
| Docker | `list-containers`, `get-logs` | Read-only |
| Postgres (GuildScout) | `execute_sql`, `list_schemas`, `list_objects`, `analyze_db_health` | Read-only! |
| Postgres (ZERODOX) | `execute_sql`, `list_schemas`, `list_objects`, `analyze_db_health` | Read-only! |
| Redis | `info`, `scan_keys`, `get`, `hgetall`, `type`, `dbsize` | Read-only |
| GitHub | `list_issues`, `search_issues`, `search_code`, `issue_write` | Issues erstellen |

## Discord-Briefing

Nach jeder Session wird ein Embed gepostet:

- **Grün:** Keine Findings, alles sauber
- **Gelb:** Niedrige/mittlere Findings
- **Rot:** Kritische/hohe Findings

Inhalt: Topics, Auto-Fixes, Entscheidungsbedarf, nächste Priorität, Token-Stats.

## Troubleshooting

```bash
# Logs prüfen
sudo journalctl -u shadowops-bot --since "1 hour ago" | grep -i analyst

# DB-Verbindung testen
docker exec -i guildscout-postgres psql -U security_analyst -d security_analyst \
  -c "SELECT COUNT(*) FROM sessions;"

# Manuelle Session via Discord
# /security-scan [fokus]

# ActivityMonitor debuggen
sudo journalctl -u shadowops-bot | grep -i "activity\|idle\|aktiv"
```
