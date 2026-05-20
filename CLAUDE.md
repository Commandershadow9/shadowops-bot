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
│   ├── cogs/                     # Slash-Commands (admin, inspector, monitoring)
│   ├── integrations/             # Externe Systeme (siehe unten)
│   └── utils/                    # config, logging, embeds, state
├── tests/
│   ├── unit/                     # 161+ Unit-Tests
│   ├── integration/              # End-to-End-Workflows
│   └── conftest.py
├── config/
│   ├── config.example.yaml       # Template (commited)
│   ├── config.yaml               # Real config (gitignored)
│   ├── DO-NOT-TOUCH.md           # Critical files protection
│   ├── INFRASTRUCTURE.md
│   └── PROJECT_*.md              # Per-projekt-Notizen
├── deploy/
│   ├── shadowops-bot.service          # systemd Bot-Service
│   ├── *-watchdog.{service,timer}     # Externe Uptime-Watchdogs (6x HTTP/systemd + Backup-Test)
│   ├── shadowops-watchdog.env.example # Webhook-Env Template
│   └── MONITORING_SETUP.md            # Setup-Anleitung Watchdogs
├── .github/
│   └── workflows/
│       ├── ci.yml                     # Test-Pipeline (pytest)
│       ├── worker-dedup-gate.yml      # Verhindert Duplikat-Worker-PRs
│       └── auto-label-pr.yml          # Auto-Labeling nach Conventional Commits
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

### Module unter `src/integrations/`

- `ai_engine.py` — Dual-Engine Router (Codex Primary, Claude Fallback)
- `smart_queue.py` — Analyse-Pool (Semaphore=3) + serieller Fix-Lock + Circuit Breaker
- `verification.py` — Pre-Push Pipeline (Confidence ≥85% → Tests → Claude-Verify → KB-Check)
- `orchestrator/` — Multi-Event-Batching (10s Fenster) + Approval-Flow (Package: core, batch_mixin, planner_mixin, executor_mixin, recovery_mixin, discord_mixin, models)
- `event_watcher.py` — Lauscht auf Fail2ban/CrowdSec/AIDE/Docker-Events
- `knowledge_base.py` — SQL Learning (fix_attempts, fix_verifications, finding_quality, scan_coverage)
- `code_analyzer.py` — Code Structure Analyzer (Git-History + AST)
- `context_manager.py` — RAG: Project-Context + DO-NOT-TOUCH + Infra
- `github_integration/` — Webhooks mit HMAC-SHA256 Verification + Jules Workflow (Package: core, webhook_mixin, event_handlers_mixin, jules_workflow_mixin, notifications_mixin, ci_mixin, agent_review/)
- `project_monitor.py` — Multi-Project Health-Checks
- `deployment_manager.py` — Auto-Deploy mit Backup/Rollback
- `incident_manager.py` — Incident Threads in Discord
- `customer_notifications.py` — Customer-Facing Alerts (Multi-Guild)
- `fail2ban.py` / `crowdsec.py` / `aide.py` / `docker.py` — Security-Integrationen

## Externes Monitoring (seit 2026-05-17 — Defense-in-Depth)

Zusätzlich zum internen `project_monitor.py` laufen 5 unabhängige user-systemd Watchdogs, die alle 5 Minuten ihre Services prüfen und Down/Recovery direkt via Discord-Webhook in `#🩺-uptime-alerts` posten (NICHT über den Bot — funktioniert auch wenn shadowops-bot tot ist):

| Watchdog | Mode | Target |
|---|---|---|
| `shadowops-watchdog` | http | http://127.0.0.1:8766/health |
| `zerodox-watchdog` | http | https://zerodox.de/api/health |
| `guildscout-watchdog` | http | http://localhost:8765/health |
| `mayday-sim-watchdog` | http | http://127.0.0.1:3200/api/health |
| `ai-agent-framework-watchdog` | systemd | guildscout-feedback-agent, zerodox-support-agent, seo-agent |
| `cmdshadow-design-watchdog` | systemd-result | cmdshadow-design-healthcheck.service (max_age=36h, 1h-Cycle) |
| `mayday-ci-runner-watchdog` | http + jq-filter | http://10.8.0.10:9100/health, filter=`.components.ci_runner.ok` (#mayday-sim#425) |
| `shadowops-backup-test` | — | monatlich 1. d. Monats, Wrapper um `~/ZERODOX/scripts/backup-test.sh` |

**Script:** `scripts/service-watchdog.sh` (generisch, parametrisiert) und `scripts/bot-watchdog.sh` (Backward-Compat). **Service-Files:** `deploy/<name>-watchdog.{service,timer}`. **Webhook-Config:** `~/.config/shadowops-watchdog.env` (chmod 600). **Setup-Anleitung:** [`deploy/MONITORING_SETUP.md`](./deploy/MONITORING_SETUP.md).

**Regel beim Hinzufügen eines neuen kritischen Services:** Watchdog-Service-File aus `deploy/` kopieren, Env-Vars anpassen, Symlink in `~/.config/systemd/user/`, `daemon-reload + enable + start`, Recovery-Alert testen. Tabelle hier UND in `MONITORING_SETUP.md` erweitern.

### JSON-Path-Filter für aggregierte Health-Endpoints (seit 2026-05-20, mayday-sim#437)

Wenn der Health-Endpoint mehrere Komponenten aggregiert (z.B. `runner-health.service` auf V-Server1 deckt `ci_runner` + `github_runners` + `load` ab) und HTTP 503 zurückgibt sobald **irgendeine** Komponente kaputt ist, würde der Standard-Watchdog False-Positives feuern. Lösung: `WATCHDOG_HEALTH_JQ_FILTER` in der ENV-Datei setzen.

```env
WATCHDOG_HEALTH_JQ_FILTER=.components.ci_runner.ok
# Alternative: alerts[]-Filter
WATCHDOG_HEALTH_JQ_FILTER='[.alerts[] | select(.component == "ci_runner" and .severity == "critical")] | length == 0'
```

Wenn gesetzt: HTTP-Status wird **ignoriert** (außer curl-Fehler), jq-Expression ist Truth-Source. Test-Coverage: `tests/unit/test_service_watchdog_jq_filter.py` (8 Tests, Stub-HTTP-Server).

### Cross-Repo-Contribution-Pfad (seit 2026-05-20)

**keydev (`@hamannmanfred90-lgtm`) hat write-Access** auf diesem Repo via GitHub Collaborator-Status. Pattern für Cross-Team-Beiträge an `service-watchdog.sh` und `deploy/*-watchdog.*`:

1. keydev (oder anderer Cross-Team-Contributor) erstellt Branch `feat/<topic>` direkt im Repo, kein Fork nötig
2. PR auf main, normales CI-Setup (`ci.yml` läuft pytest)
3. cmdshadow reviewed + merged
4. **Roll-out-Step bleibt bei cmdshadow** (ENV-File-Updates + `systemctl --user`-Restart sind cross-user-Operationen → nicht ohne Sudoers-Aufweichung delegierbar)

Read-Only ACL auf `scripts/` und `deploy/` für `keydev` ist gesetzt (`getfacl` zeigt `user:keydev:r-x`) — er kann Files lokal lesen und Tests laufen lassen. ENV-Files (`~/.config/*.env`) bleiben chmod 600, kein Read-Access, weil sie Webhook-Secrets enthalten.

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

2026-05-18 — deploy/-Verzeichnis aktualisiert (Watchdog-Services), .github/workflows/ hinzugefuegt.
