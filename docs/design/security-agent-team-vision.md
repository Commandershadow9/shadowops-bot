# Security-Agent-Team — Design-Skizze (Brainstorm-Grundlage)

**Status:** DRAFT / Brainstorm-Grundlage · 2026-05-29
**Vorbild:** SEO-Multi-Agent-Team (`~/agents/projects/seo/`, LIVE seit 2026-05-14)
**Auslöser:** User-Wunsch — den Security-Workflow „umfangreicher und besser" machen,
„gerne als Team erweitern wie der SEO-Agent, nur für Security".

> Dies ist eine **Skizze zum Brainstormen**, kein finales Spec. Die offenen
> Design-Fragen (Abschnitt 6) sind bewusst noch nicht alle entschieden.

## ✅ Entscheidung 2026-05-29 (Frage 1 — Heimat)

**Das Team wird in shadowops-bot ausgebaut.** Begründung des Users: ShadowOps
*ist* der Security-/Monitoring-Bot — das Team gehört in seine Kern-Identität,
nicht ins generische Agent-Framework. Findings-DB, Discord-Integration,
Jules-Workflow und die Watchdogs leben bereits hier.

**Zusatz-Mandat:** „Das Ganze gerne sauber neu und richtig angehen, um ihn [zu
*dem* Security/Monitoring-Bot] zu machen." → Ein gründliches Team-Refactoring des
gewachsenen `scan_agent.py`-Monolithen ist ausdrücklich erwünscht (nicht nur
Anbau). Eigene fokussierte Session/Welle — siehe Tracking-Issue.

---

## 1. Ausgangslage

**Heute:** `shadowops-bot/src/integrations/security_engine/scan_agent.py` — ein
**2626-Zeilen-Monolith** (`SecurityScanAgent`), der alles macht:
Scans triggern, Findings erzeugen, Fix-Routing (self-fix vs. Jules), GitHub-Issues
anlegen, Health-Checks, Learning-Pipeline, Project-Security-Profiles.

**Teilmodular ist die Engine schon:** `engine.py`, `executor.py`, `registry.py`,
`reactive.py`, `proactive.py`, `fixer_adapters.py`, `providers.py`, `fingerprint.py`,
`learning_bridge.py`, `models.py`, `migrations/`. Gute Bausteine — aber kein
Job-Bus, keine isolierten Worker-Prozesse, kein einheitlicher Job-Contract.

**SEO-Team zum Vergleich:** Orchestrator → Redis Pub/Sub (`seo:job:<type>:request/result`)
→ spezialisierte Worker (systemd `--user`, je ein Prozess) → Postgres Job-Lifecycle.
`BaseWorker`-Abstraktion + `contracts/job.py` + Per-Site-Onboarding + CLI-only AI.

---

## 2. Ziel-Architektur (analog SEO)

```
External Triggers (Cron / Redis publish sec:trigger / Webhook push)
        │
        ▼
  security-orchestrator.service   ← zerlegt Scan-Anfrage in Worker-Jobs
        │ publish sec:job:<type>:request
        ▼
  Redis (guildscout-redis:6379)   ← Job-Bus, gleiche Infra wie SEO
        │
   ┌────┼─────────────┬──────────────┬───────────────┬─────────────┐
   ▼    ▼             ▼              ▼               ▼             ▼
 npm-   code-scan-   secret-      container-      config-       dependency-
 audit  worker       scan-worker  scan-worker     audit-worker  graph-worker
 worker (XSS/SQLi)   (gitleaks)   (trivy)         (UFW/perms)   (supply-chain)
   │    │             │              │               │             │
   └────┴─────────────┴──────────────┴───────────────┴─────────────┘
        ▼
  Postgres security_engine DB (findings-Lifecycle, existiert schon)
        ▼
  Fix-Router → self-fix | Jules-Delegation | manuelle Triage
```

## 3. Worker-Kandidaten (aus heutigen Scan-Kategorien abgeleitet)

| Worker | Quelle heute | Rolle |
|---|---|---|
| `npm-audit-worker` | npm_audit-Kategorie | Dependency-CVEs JS/TS (genau der #1069-Fall) |
| `code-scan-worker` | code_security | XSS/SQLi/SSRF/Auth-Lücken via CLI-LLM + Pattern |
| `secret-scan-worker` | — (NEU) | gitleaks/trufflehog auf Commits + Working-Tree |
| `container-scan-worker` | image-Findings | Trivy auf Docker-Images |
| `config-audit-worker` | infra/config | UFW, Datei-Permissions, exposed Ports, .env-Leaks |
| `dependency-graph-worker` | — (NEU) | Supply-Chain: Lockfile-Drift, typosquatting, license |
| `fix-worker` | scan_agent Fix-Routing | self-fix-Pipeline + Jules-Enqueue (existiert teils) |

Jeder Worker: eigener systemd `--user`-Service, `BaseWorker` aus dem Framework,
CLI-only AI (codex→claude-Chain, **keine API-Kosten** — gleiches Constraint wie SEO).

## 4. Was wiederverwendet wird

- **Redis-Bus + Postgres** — gleiche Infra wie SEO (guildscout-redis:6379, seo_agent-DB-Muster)
- **Framework-Core** (`~/agents/core/`): `agent_base.py`, `runner.py`, `events`, `notify`, `ai`
- **Bestehende Security-Engine-Module** (registry/executor/fingerprint/learning_bridge) — werden Worker-Bausteine statt Monolith-Methoden
- **Fix-Delegation + Jules-Workflow** (`fixer_adapters.py`, `build_jules_issue_body`) — inkl. der heute gehärteten Scope-Guards (PR #288)
- **Independent-Claude-Review** (analog `jules_pr_reviewer.py` im SEO-Projekt)

## 5. Migrations-Pfad (inkrementell, wie SEO P2→P5)

1. **P1 — Job-Contract + Orchestrator-Stub** neben dem Monolith (Monolith läuft weiter)
2. **P2 — `BaseSecurityWorker`** + erster echter Worker (`npm-audit-worker`, kleinster Scope)
3. **P3 — Worker-Migration** je Kategorie (eine Subagent-Session pro Worker, wie SEO P3)
4. **P4 — Neue Worker** (secret-scan, dependency-graph)
5. **P5 — Multi-Projekt-Onboarding** (`onboard-project.sh` analog `onboard-site.sh`) — die PROJECT_SECURITY_PROFILES gibt's schon

Kein Big-Bang — der Monolith bleibt, bis jeder Worker einzeln soak-getestet ist.

## 6. Offene Design-Fragen (fürs Brainstorming)

1. **Heimat:** Migriert das Team ins **AI Agent Framework** (`~/agents/projects/security/`,
   konsistent mit SEO) — oder bleibt es in **shadowops-bot** (wo Findings-DB + Discord +
   Jules-Workflow schon leben)? Trade-off: Framework-Konsistenz vs. Migrations-Aufwand
   der gewachsenen Security-Engine.
2. **Orchestrator-Rolle:** Bleibt `scan_agent.py` der Orchestrator (entkernt) — oder neuer
   schlanker `security-orchestrator` wie beim SEO-Team?
3. **Worker-Granularität:** 7 Worker (oben) — oder gröber (scan/fix/report) zum Start?
4. **Trigger-Modell:** Nur Cron + Redis wie SEO — oder zusätzlich Webhook-getrieben
   (Push → sofort-Scan)? Die `reactive.py` deutet auf Event-Trigger hin.
5. **Cross-Projekt:** Ein Team für alle Projekte (ZERODOX/GuildScout/…) via
   Per-Projekt-Profile — oder pro Projekt eigene Worker-Instanzen?
6. **Review-Loop:** Soll jeder Security-Fix (nicht nur Jules-PRs) durch einen
   Independent-Claude-Reviewer wie im SEO-Team (`jules_pr_reviewer.py`)?
7. **Ressourcen:** 7 neue systemd-Worker auf dem 8-GB-VPS — Memory-Budget? (SEO hat
   schon 9 Worker; earlyoom-Schutz beachten, siehe OOM-Cascade-Vorfall 2026-05-25).

## 7. Nicht-Ziele (vorerst)

- Keine kostenpflichtigen Security-APIs (Snyk/SonarCloud) — CLI-only Constraint
- Kein Ersatz für Trivy/gitleaks — die bleiben die Scan-Engines, Worker orchestrieren sie
- Kein Auto-Merge von Security-Fixes — Human-in-the-Loop bleibt (Shadow merged manuell)
