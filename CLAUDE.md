# CLAUDE.md — shadowops-bot

> Diese Datei ist die Wissensbasis fuer alle KI-Tools (Claude Code, Codex, Routine-Worker).
> Sie wird automatisch geladen. Halte sie aktuell — wenn die Doku luegt, lernt die KI Falsches.

## Projekt-Ueberblick

**ShadowOps** ist ein autonomer Security-Discord-Bot fuer Server-Monitoring (Fail2ban, CrowdSec, Docker, AIDE) mit Dual-Engine AI (Codex + Claude CLI), persistentem Lernsystem (SQL Knowledge Base) und Multi-Project-Management.

- **Repo:** https://github.com/Commandershadow9/shadowops-bot
- **Default-Branch:** `main`
- **Lizenz:** MIT
- **Maintainer:** Solo-Dev (CommanderShadow), entwickelt mit hohem KI-Anteil

## Tech-Stack

| Bereich | Technologie | Version |
|---|---|---|
| Sprache | Python | 3.9+ (CI nutzt 3.11) |
| Discord | discord.py | siehe requirements.txt |
| Datenbank | PostgreSQL | 3 DBs: security_analyst, agent_learning, seo_agent |
| Cache | Redis | — |
| AI Primary | Codex CLI | gpt-4o / gpt-5.3-codex / o3 |
| AI Fallback | Claude CLI | claude-sonnet-4-6 / claude-opus-4-6 |
| Container | Docker | mit Trivy fuer Scans |
| Service | systemd | `/etc/systemd/system/shadowops-bot.service` |
| Tests | pytest | 150+ Tests, unit + integration |
| Webhook | GitHub Webhooks | HMAC-SHA256 verifiziert |

## Architektur-Prinzipien

1. **Defense-in-Depth.** Jede destruktive Aktion hat Backup → Fix → Verify → Restart, mit Rollback-Pfad.
2. **Confidence-Based AI.** <85% confidence → Fix wird blockiert.
3. **Lernen statt Wiederholen.** Knowledge Base speichert jeden Fix-Versuch + Outcome. Beim Retry waehlt der Agent einen anderen Ansatz.
4. **Single Approval pro Plan.** Multi-Event-Batching, ein Plan, eine Genehmigung.
5. **Dry-Run-First.** Neue Fix-Strategien laufen erst im Dry-Run-Mode.
6. **Loop-Schutz.** 7 Schichten gegen Review-Loops (Trigger-Whitelist, SHA-Dedupe, Cooldown, Iteration-Cap, Circuit-Breaker, Time-Cap, Single-Comment-Edit).

## Verzeichnis-Struktur

```
shadowops-bot/
├── src/
│   ├── bot.py                    # Haupt-Bot
│   ├── cogs/                     # Slash-Commands
│   │   ├── admin.py              #   /scan, /stop-all-fixes, /release-notes, ...
│   │   ├── inspector.py          #   /get-ai-stats, /agent-stats, /security-engine, ...
│   │   ├── monitoring.py         #   /status, /bans, /threats, /docker, /aide
│   │   └── customer_setup_commands.py  # /setup-customer-server
│   ├── patch_notes/              # Patch-Notes Pipeline v6 (State Machine, ~2100 LOC)
│   │   ├── pipeline.py           #   Hauptpipeline + asyncio Lock + Circuit Breaker
│   │   ├── stages/               #   5 Stufen: collect, classify, generate, validate, distribute
│   │   ├── templates/            #   gaming / saas / devops Templates
│   │   └── versioning.py / grouping.py / state.py / ...
│   ├── integrations/             # Externe Systeme
│   │   ├── ai_engine.py          #   Dual-Engine Router (Codex Primary, Claude Fallback)
│   │   ├── smart_queue.py        #   Analyse-Pool (Semaphore=3) + Fix-Lock + Circuit Breaker
│   │   ├── verification.py       #   Pre-Push Pipeline (Confidence ≥85%)
│   │   ├── orchestrator/         #   Multi-Event-Batching (10s Fenster) + Approval-Flow
│   │   ├── event_watcher.py      #   Lauscht auf Fail2ban/CrowdSec/AIDE/Docker-Events
│   │   ├── knowledge_base.py     #   SQL Learning (fix_attempts, finding_quality, ...)
│   │   ├── code_analyzer.py      #   Code Structure Analyzer (Git-History + AST)
│   │   ├── context_manager.py    #   RAG: Project-Context + DO-NOT-TOUCH + Infra
│   │   ├── github_integration/   #   Webhooks (HMAC-SHA256) + Jules SecOps + Agent-Review
│   │   ├── security_engine/      #   Security Engine v6 (Scan, Fix, Learning, Activity)
│   │   ├── fixers/               #   Fix-Adapter: fail2ban, crowdsec, trivy, aide, walg
│   │   ├── ai_learning/          #   Agent-Learning DB + Knowledge Synthesizer
│   │   ├── analyst/              #   Legacy Security Analyst (Referenz, nicht aktiv gestartet)
│   │   ├── project_monitor.py    #   Multi-Project Health-Checks
│   │   ├── deployment_manager.py #   Auto-Deploy mit Backup/Rollback
│   │   ├── incident_manager.py   #   Incident Threads in Discord
│   │   ├── customer_notifications.py  # Customer-Facing Alerts (Multi-Guild)
│   │   └── fail2ban.py / crowdsec.py / aide.py / docker.py
│   └── utils/                    # config, logging, embeds, state
├── tests/
│   ├── unit/                     # 161+ Unit-Tests
│   ├── integration/              # End-to-End-Workflows
│   └── conftest.py
├── config/
│   ├── config.example.yaml       # Template (committed)
│   ├── config.yaml               # Real config (gitignored)
│   ├── DO-NOT-TOUCH.md           # Critical files protection
│   ├── INFRASTRUCTURE.md
│   └── PROJECT_*.md              # Per-Projekt-Notizen
├── deploy/
│   └── shadowops-bot.service     # systemd Unit
├── scripts/                      # Wartungs-Skripte
├── docs/
│   ├── SECURITY_ANALYST.md
│   ├── SETUP_GUIDE.md
│   ├── reference/api.md
│   ├── adr/                      # Architecture Decision Records
│   └── plans/                    # Design-Dokumente
├── data/                         # Runtime-Daten (gitignored)
├── logs/                         # Logs (gitignored)
├── .claude/                      # KI-spezifische Configs
└── .routines/                    # Worker State + Prompts (siehe unten)
```

## Coding-Conventions

- **Naming:** `snake_case` fuer Funktionen/Variablen, `PascalCase` fuer Klassen, `UPPER_CASE` fuer Konstanten.
- **Type-Hints:** Pflicht fuer neue Funktionen (auch wenn mypy noch nicht strict laeuft).
- **Docstrings:** Google-Style, mindestens fuer Public-Funktionen.
- **Async-First:** discord.py + aiohttp — neue I/O ist `async`.
- **Fehler-Handling:** Niemals leere `except:`. Mindestens loggen + re-raise oder klar entscheiden.
- **Logging:** `from src.utils.logger import get_logger` — niemals `print()`.
- **Secrets:** AUSSCHLIESSLICH via Env-Vars (DISCORD_BOT_TOKEN, OPENAI_API_KEY, ANTHROPIC_API_KEY). Niemals in Code, niemals in `config.yaml`.
- **Tests:** Neue Module brauchen Tests in `tests/unit/test_<module>.py`. Fixtures in `conftest.py` wiederverwenden.
- **Conventional Commits:** `fix:`, `feat:`, `refactor:`, `perf:`, `docs:`, `chore:`.

## DO und DON'T

### DO
- Backups vor destruktiven Aktionen (`deployment_manager.py` macht das schon — beibehalten).
- Confidence-Score in jeden AI-Output reflektieren.
- Knowledge-Base updaten nach Fix (success/failure egal).
- DO-NOT-TOUCH-Liste pruefen bevor Files angefasst werden.
- Discord-Updates in Echtzeit (Backup → Fix → Verify → Restart sichtbar).

### DON'T
- **Niemals** `config.yaml` oder `.env` committen.
- **Niemals** Secrets in Logs schreiben (Logger maskiert standardmaessig nicht — selbst mitdenken).
- **Niemals** `enforce_admins: true` in der Branch-Protection — sonst sperrt sich Solo-Dev aus.
- **Niemals** Public API in `src/integrations/*` aendern ohne BREAKING-Markierung im Commit.
- **Niemals** `pytest` mit Live-DB laufen lassen — Fixtures nutzen.
- **Niemals** Discord-Token, Webhook-Secrets, oder DB-Passwords im Code referenzieren — nur `os.environ`.
- **Niemals** systemd-Service per Worker-PR aendern — das ist Server-State, nicht Repo-State.

## Setup-Commands

```bash
# Dependencies
pip3 install -r requirements.txt
pip3 install -r requirements-dev.txt

# Config (einmalig)
cp config/config.example.yaml config/config.yaml
chmod 600 config/config.yaml
# Secrets als Env-Vars in ~/.bashrc oder Service-EnvFile:
#   export DISCORD_BOT_TOKEN="..."
#   export OPENAI_API_KEY="..."
#   export ANTHROPIC_API_KEY="..."

# Lokal testen
python3 src/bot.py

# Tests
pytest tests/ -v
pytest tests/ --cov=src --cov-report=html

# Service (Production)
sudo systemctl restart shadowops-bot
sudo journalctl -u shadowops-bot -f
```

## Beispiele fuer typische Tasks

### Neuen Slash-Command hinzufuegen
1. Cog-Datei: `src/cogs/<bereich>.py` — Command als Methode mit `@app_commands.command(...)`.
2. Cog in `bot.py` laden (`await self.add_cog(...)` falls noch nicht generisch gelistet).
3. Test: `tests/unit/test_<bereich>.py` mit Mock-Interaction.
4. Doku: README → "Slash Commands" Sektion ergaenzen.

### Neue Security-Integration (analog zu fail2ban.py)
1. Modul in `src/integrations/<name>.py` mit Klasse, async `check()`-Methode, gibt strukturierte Events zurueck.
2. Im `event_watcher.py` registrieren.
3. Config-Key in `config.example.yaml` ergaenzen.
4. Test mit Mock-Subprocess-Output.
5. README + `docs/SECURITY_ANALYST.md` updaten.

### Neuen AI-Provider als Engine
1. Klasse in `ai_engine.py` analog zu `CodexEngine` / `ClaudeEngine`.
2. In `TaskRouter` registrieren mit Routing-Regeln.
3. Quota-Failover testen (Mock-Output mit Limit-Marker).
4. Tests in `test_ai_engine.py` ergaenzen.
5. CLAUDE.md (diese Datei) im Tech-Stack-Block updaten.

## Routine-Worker

Drei Worker laufen automatisch (siehe `.routines/prompts/`):

- **Cleanup-Crew** — `.routines/prompts/cleanup.md` — taeglich 03:00 + 14:00. Refactor, Dead-Code, Konsistenz, Quick-Wins. State: `.routines/state/cleanup.json`
- **Guardian** — `.routines/prompts/guardian.md` — taeglich 05:00 + bei Push-Webhook. SAST, Dependency-Scan, Secret-Scan, passive Live-Checks gegen `zerodox.de` (NICHT gegen shadowops-bot — das ist ein Server-side Bot, kein Public-Endpoint). State: `.routines/state/guardian.json`
- **Doku-Kurator** — `.routines/prompts/doku.md` — taeglich 02:00. Drift, Luecken, KI-Konformitaet, Anti-Bloat. State: `.routines/state/doku.json`

Worker-Konventionen:
- Branches: `routine/<worker>/<topic>` (z.B. `routine/cleanup/dedupe-event-watcher`)
- PR-Labels: `status:routine-generated`, `worker:<name>`, `type:<refactor|fix|...>`, `area:<modul>`
- Bei Unsicherheit: Issue statt PR (`status:needs-info`).

## Statistik (Stand v5.1)

20.000+ LoC, 150+ Tests, 3 PostgreSQL DBs (21+7+11 Tabellen), 4 Security-Integrationen, 15 Discord-Commands, 3 Monitored Projects (GuildScout, ZERODOX, AI Agents).

## Aktuelle Doku

- [README.md](./README.md)
- [docs/SECURITY_ANALYST.md](./docs/SECURITY_ANALYST.md)
- [docs/SETUP_GUIDE.md](./docs/SETUP_GUIDE.md)
- [docs/reference/api.md](./docs/reference/api.md)
- [DOCS_OVERVIEW.md](./DOCS_OVERVIEW.md)
- [config/DO-NOT-TOUCH.md](./config/DO-NOT-TOUCH.md)

## Letztes Update dieser Datei

2026-04-26 — initiales Setup, generiert aus Worker-Bundle.
