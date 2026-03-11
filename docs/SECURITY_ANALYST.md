# Security Analyst — Autonomer AI Security Engineer

## Uebersicht

Der Security Analyst ist ein autonomer AI-Agent, der wie ein echter Security Engineer denkt — kein Checklisten-Abarbeiter. Er bekommt Rohdaten vom Server und entscheidet selbst, was er untersucht.

**Kernprinzip:** Freies Denken, nicht Job-Execution.

### Wie funktioniert es?

1. **ActivityMonitor** erkennt, wann der User idle ist (SSH, Git, Claude-Prozesse, Discord-Praesenz)
2. Sobald 30 Minuten Inaktivitaet vergangen sind, startet eine autonome Session
3. **Codex CLI** (`codex exec --output-schema`) wird als primaerer Provider gestartet
4. Bei Codex-Fehler: **Claude CLI** (`claude -p --allowedTools`) als Fallback
5. Der Agent untersucht frei: Logs, Docker, Firewall, Zertifikate, Berechtigungen, ...
6. Nach der Session: Health-Vergleich (vor/nach), Briefing an Discord, Issues auf GitHub
7. Wenn der User wieder online kommt, wird das Briefing-Embed zugestellt

### Dual-Engine AI

| Eigenschaft | Codex (Primaer) | Claude (Fallback) |
|------------|-----------------|-------------------|
| CLI | `codex exec --output-schema` | `claude -p --allowedTools` |
| Modell | `gpt-5.3-codex` (konfigurierbar) | `claude-opus-4-6` (konfigurierbar) |
| Output | Strukturiertes JSON via Schema | JSON in Temp-Datei + stdout-Extraktion |
| Timeout | 900s (15 Min) | 1800s (30 Min) |

### Fehlerbehandlung

| Fehler # | Backoff | Aktion |
|----------|---------|--------|
| 1 | 30 Minuten | Discord-Alert (orange), naechster Versuch nach Cooldown |
| 2 | 2 Stunden | Discord-Alert (orange), naechster Versuch nach Cooldown |
| 3+ | Tages-Sperre | Discord-Alert (rot), Analyst fuer heute deaktiviert |

- **Tages-Reset:** Um Mitternacht werden Failure-Counter + Cooldown zurueckgesetzt
- **Erfolg:** Setzt Failure-Counter sofort auf 0
- **Session-Lock:** `asyncio.Lock()` verhindert parallele Sessions

### Discord-Notifications (volle Transparenz)

| Event | Farbe | Wann |
|-------|-------|------|
| Session gestartet | Blau | Bei jedem Start (auto + manuell) |
| Session erfolgreich | Gruen/Gelb/Rot | Nach Ergebnis-Verarbeitung |
| Session fehlgeschlagen | Orange | Bei jedem einzelnen Fehler |
| Analyst deaktiviert | Rot | Nach 3 konsekutiven Fehlern |
| Health-Regression | Rot | Wenn Services nach Session down sind |

### Was darf er?

| Erlaubt | Verboten |
|---------|----------|
| UFW-Regeln anpassen | Code pushen |
| Dateiberechtigungen fixen | `rm -rf` auf Projektverzeichnisse |
| Systemd-Services pruefen | Docker Volumes loeschen |
| Docker-Container inspizieren | `.env`-Dateien aendern |
| Log-Analyse | `git push` |
| Zertifikate pruefen | Ports ohne UFW-Regel oeffnen |
| `gh issue create` | Deployments ausloesen |

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
    │   (security_analyst.py)            │
    │                                     │
    │  - Main Loop (60s Intervall)        │
    │  - Session-Lock (asyncio.Lock)      │
    │  - Failure-Backoff (30m/2h/Sperre)  │
    │  - Health Snapshots (vor/nach)       │
    │  - Discord Notifications            │
    │  - GitHub Issue Creation            │
    └──┬──────────┬──────────┬───────────┘
       │          │          │
  ┌────▼────┐ ┌──▼────┐ ┌──▼──────────────────┐
  │ Activity │ │Analyst│ │     AI Engine        │
  │ Monitor  │ │  DB   │ │ Codex (primary)      │
  │          │ │       │ │ Claude (fallback)     │
  └──────────┘ └───────┘ └─────────────────────┘
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

| Datei | Zweck |
|-------|-------|
| `src/integrations/analyst/security_analyst.py` | Hauptklasse, Session-Orchestrierung, Lock, Backoff |
| `src/integrations/analyst/analyst_db.py` | asyncpg DB-Layer (Pool, CRUD, AI-Kontext) |
| `src/integrations/analyst/activity_monitor.py` | User-Aktivitaetserkennung |
| `src/integrations/analyst/prompts.py` | System-Prompt + Kontext-Template (provider-agnostisch) |
| `src/integrations/analyst/__init__.py` | Exports |
| `src/integrations/analyst/db_setup.sql` | Postgres-Schema |
| `src/schemas/analyst_session.json` | JSON-Schema fuer Session-Output |
| `tests/unit/test_security_analyst.py` | 21 Unit-Tests (Counter, Backoff, Lock, Notifications) |

## Konfiguration

In `config/config.yaml`:

```yaml
security_analyst:
  enabled: true
  database_dsn: "postgresql://security_analyst:PASSWORD@127.0.0.1:5433/security_analyst"
  max_sessions_per_day: 3
  session_timeout: 1800      # 30 Minuten max pro Session
  session_max_turns: 25      # Max CLI-Turns pro Session
  model: "gpt-5.3-codex"     # Primaeres Modell (Codex)
  fallback_model: "claude-opus-4-6"  # Fallback-Modell (Claude)
```

Channel in `channels:`:
```yaml
channels:
  security_briefing: CHANNEL_ID
```

## Datenbank

Laeuft auf dem GuildScout Postgres (Port 5433):

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

## AI-Session Details

### Codex (Primaer)

```bash
codex exec --ephemeral --skip-git-repo-check \
  -c 'mcp_servers={}' -s workspace-write \
  -m gpt-5.3-codex \
  --output-schema src/schemas/analyst_session.json \
  "PROMPT"
```

- Timeout: 900s
- `--skip-git-repo-check`: Retry ohne Flag falls CLI-Version es nicht kennt
- `--output-schema`: Erzwingt strukturiertes JSON (kein Prompt-Hack noetig)

### Claude (Fallback)

```bash
claude -p "PROMPT + Ausgabe-Anweisung" \
  --model claude-opus-4-6 \
  --max-turns 25 \
  --output-format text \
  --allowedTools "Bash(git:*),Bash(docker:*),...,Read,Glob,Grep,Write,Edit"
```

- Timeout: 1800s
- Ergebnis wird in Temp-Datei geschrieben (`tempfile.mkstemp`)
- Fallback: JSON-Extraktion aus stdout (begrenzt auf 500 KB)
- Nur whitelisted Bash-Prefixe — kein `rm`, kein `dd`

## Discord-Briefing

Nach jeder Session wird ein Embed gepostet:

- **Gruen:** Keine Findings, alles sauber
- **Gelb:** Findings vorhanden
- **Rot:** Kritische/hohe Findings oder Health-Regression

Inhalt: Topics, Auto-Fixes, Entscheidungsbedarf, naechste Prioritaet.

## Troubleshooting

```bash
# Logs pruefen
sudo journalctl -u shadowops-bot --since "1 hour ago" | grep -i analyst

# DB-Verbindung testen
docker exec -i guildscout-postgres psql -U security_analyst -d security_analyst \
  -c "SELECT COUNT(*) FROM sessions;"

# Manuelle Session via Discord
# /security-scan [fokus]

# ActivityMonitor debuggen
sudo journalctl -u shadowops-bot | grep -i "activity\|idle\|aktiv"

# Failure-Status pruefen
sudo journalctl -u shadowops-bot | grep -i "backoff\|cooldown\|consecutive"
```
