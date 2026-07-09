# Security-Agent-Team v2 — Spec (2026-07-09)

**Status:** APPROVED (Owner-Freigabe 2026-07-09, Chat-Session)
**Ersetzt/ergänzt:** `security-agent-team-vision.md` (DRAFT 2026-05-29), `2026-06-02-security-agent-team-p1-spec.md` (P1, umgesetzt via PR #307)
**Epic:** [#290](https://github.com/Commandershadow9/shadowops-bot/issues/290)

---

## 1. Kontext & Ausgangslage (verifiziert 2026-07-09)

Die Bestandsaufnahme (5 parallele Explorer + Lücken-Check) ergab:

- **Der AI-Kern des Monolithen war seit 2026-07-07 komplett tot** (Doppel-Ursache: stales Codex-Modell `gpt-5.3-codex` → HTTP 400; hartcodierter Claude-CLI-Pfad `~/.local/bin/claude` existiert nicht mehr → FileNotFoundError). Reparatur läuft als PR (`fix/security-engine-revive-290`).
- **Jules ist operativ tot** (kein `api_key`, Scheduler startet nie; `agent_task_queue`-Einträge laufen ins Leere). Der Fix-Kanal des Fix-Routers existiert nur auf dem Papier.
- **P1 (Job-Contract, BaseWorker, npm-audit-Worker, systemd-Templates) ist gebaut, aber nie aktiviert** — und wäre in der vorliegenden Form nicht lauffähig (Redis-URL ohne Auth trotz `requirepass`, `~/.config/shadowops-security-team.env` fehlt, zerodox `npm_audit_path` zeigt auf `ZERODOX` statt `ZERODOX/web`).
- **Ressourcen-Annahmen obsolet:** Server hat 64 GB RAM (Doku: 8/21 GB). Always-on-Worker sind unkritisch.
- **Phase 0 (2026-07-09, erledigt):** Trivy 0.72.0 + täglicher Scan 04:15 (Bot-kompatibles Format, erster Lauf: 15 Images, 15 CRITICAL / 242 HIGH), kptr_restrict=1, Backup-Dirs 700/750, Dashboard-Binding 127.0.0.1, gh/nodejs-Updates. 9 obsolete/erledigte Issues geschlossen (#313 #314 #295 #310 #311 #327 #328 #330 #331).
- **Lehre aus zwei stillen Ausfällen** (SEO 27.06., Security 07.07.): Prozess-Liveness ≠ Arbeits-Liveness. Selbstüberwachung muss den **Arbeitserfolg in der DB** prüfen, nicht den Service-State.

## 2. Owner-Entscheidungen (2026-07-09)

| Frage | Entscheidung |
|---|---|
| Fix-Kanal für Code-Findings | **Claude-CLI + PR-Gate** (Jules-Ersatz). Kein Fremd-Modell an Security-Fixes. |
| Autonomie-Grenze | **Whitelist-Host-Mini-Fixes autonom** (chmod, sysctl-Drop-In, 127.0.0.1-Rebinding) mit Discord-Report + Rollback-Info + Audit. **Alles Repo-Ändernde nur als PR**, Owner merged. Gefährliches (SSH, DB-Schema, Traefik, UFW-Struktur, Docker-Engine) nur als Vorschlag/Issue. |
| Worker-Standort | **Prod-Host, systemd-User-Units, Scanner strikt read-only** (Findings-Schreiben in DB ausgenommen). Beantwortet #296 für die Scanner; schreibende Aktionen laufen über den PR-Weg bzw. die Whitelist mit Audit-Trail. |
| Architektur | **Ansatz 1: Team-Ausbau nach #290** — P1-Fundament reparieren + aktivieren, Orchestrator vervollständigen, Worker inkrementell, Monolith parallel bis Soak, dann entkernen. |
| Kein Auto-Merge | Bestätigt (aus #290 übernommen). |

## 3. Ziel-Architektur — sechs Säulen

```
                      ┌──────────────────────────────────────────────┐
   Trigger:           │              security-orchestrator           │
   - Cron (Daily)     │  sec:trigger → Fan-out → Aggregation →       │
   - Redis publish    │  Fix-Routing → Discord-Report                │
   - Event-Eskalation │  (systemd --user, eigenständiger Prozess)    │
     (event_watcher)  └──────────┬───────────────────────────────────┘
                                 │ sec:job:<typ>:request / :result
                                 ▼
                     Redis guildscout-redis:6379 (MIT Auth)
        ┌──────────┬─────────────┼─────────────┬──────────────┐
        ▼          ▼             ▼             ▼              ▼
   npm-audit   config-audit  container-scan  code-scan   secret-scan
   (P1, da)    (W2, neu)     (W2, neu)       (W4, neu)   (W4, neu)
        └──────────┴─────────────┴─────────────┴──────────────┘
                                 ▼
              security_analyst-DB (Port 5433): findings + sec_jobs
              (Fingerprint-Dedup: fingerprint.py, geteilter store_finding())
                                 ▼
                        ┌────────────────┐
                        │   Fix-Router   │
                        └───┬────┬───┬───┘
              auto_whitelist│  pr│   │human_only
                            ▼    ▼   ▼
                     Whitelist- Claude-CLI  GitHub-Issue
                     Fixer +    Branch+PR → (3-Phasen-Plan)
                     Discord +  Label →     + Discord-Alarm
                     Audit+     Independent-
                     Rollback   Claude-Review-Action
                                (VERDICT-Gate) → Owner merged
```

1. **Event-getrieben** (existiert): CrowdSec/AIDE/Trivy-Events → `event_watcher` → ReactiveMode. Bleibt im Bot.
2. **Deterministische Daily-Scans** (neu, `config-audit-worker`): Port-Bindings außerhalb 127.0.0.1/VPN, world-readable Backup-/Secrets-Verzeichnisse, sysctl-Baseline-Drift, Container ohne `cap_drop ALL`/`no-new-privileges`, apt-Security-Backlog-Schwelle, Docker-Resolver-Fehlerrate (#285), UFW-Regel-Drift gegen dokumentierte Soll-Liste. Quelle der Regeln: die realen Findings #315 #327 #329 #330 #331 #267 #285.
3. **CVE-Pipeline** (Basis gelegt): Trivy-Cron 04:15 (`~/scripts/trivy-daily-scan.sh`) → `/var/log/trivy-scans` → `container-scan-worker` liest, dedupliziert, priorisiert (CRITICAL zuerst, externe vs. eigene Images via `docker_image_analyzer`), routet in die Fix-Pipeline.
4. **LLM-Tiefen-Scans** (`code-scan-worker`): Claude-CLI headless, read-only Analyse pro Projekt-Profil; ersetzt die fragilen In-Bot-Sessions des Monolithen schrittweise. Provider-Chain: Codex→Claude wie `~/agents/core/ai/chain.py`, aber für **Analyse** — Fixes sind Claude-only (Owner-Entscheid).
5. **Fix-Pipeline mit drei Kanälen** (Abschnitt 4).
6. **Selbstüberwachung** (Abschnitt 5).

## 4. Fix-Pipeline

**Routing-Grundlage:** bestehende `classify_fix_mode`-Logik (scan_agent.py:143) wird in ein eigenständiges `team/routing.py` extrahiert und bereinigt (Jules-Zweig raus, fail2ban-Kategorien raus).

| Kanal | Kategorien (initial) | Verhalten |
|---|---|---|
| `auto_whitelist` | `file_permissions`, `sysctl_drift`, `port_binding_localhost` | Fix-Worker führt aus. Pflicht je Aktion: (a) Vorher-Zustand erfassen, (b) Discord-Report mit exaktem Rollback-Befehl, (c) Audit-Zeile in DB (`fix_attempts_v2`), (d) Tages-Cap (default 10/Tag) + CircuitBreaker (3 Fehler → 24h Pause), (e) Idempotenz (bereits korrekt → No-Op, kein Report-Spam). |
| `pr` | `npm_audit`/`pip_audit`-Upgrades, `dockerfile`, `code_vulnerability`, Config-Dateien in Repos | Claude-CLI headless in **isoliertem Worktree** des Ziel-Repos: Branch `security-agent/<finding-slug>`, Commit, Push, PR mit Label `security-agent-fix`. Danach GitHub-Action **Independent Claude Review** (eigener API-Key-Secret `CLAUDE_SECURITY_REVIEW_KEY`, frischer Kontext, Diff-Cap 200 KB, erzwungene erste Zeile `VERDICT: APPROVE|REQUEST_CHANGES|UNCLEAR`, Exit≠0 bei allem außer APPROVE → Branch-Protection-Gate). **Owner merged manuell.** Kein Auto-Merge. |
| `human_only` | `ssh_config`, `database_schema`, `traefik`, `ufw_structure`, `docker_engine` | GitHub-Issue im 3-Phasen-Format (Lagebild → Eindämmung → Verifikation, mit Confidence + Rollback-Plan; Vorlage aus #310/#311) + Discord-Alarm bei HIGH/CRITICAL. |

**Guards in jedem Kanal:** Fingerprint-Dedup (`fingerprint.py`) + 24h-Throttle pro Finding; `PYTEST_CURRENT_TEST`-Guard (Lehre #1069); Deletion-Guard + Post-Fix-Integrity-Check (aus scan_agent übernommen) für alles Schreibende.

## 5. Selbstüberwachung (Pflicht ab W1)

- **`security-freshness-watchdog`** (Klon des `seo-audit-freshness-watchdog`-Musters): prüft stündlich in der `security_analyst`-DB, ob der letzte erfolgreiche Orchestrator-Lauf < 26h alt ist (`sec_jobs.completed_at` bzw. Lauf-Tabelle). Alarm via Discord-Webhook (`#🩺-uptime-alerts`), unabhängig vom Bot-Prozess.
- **`job-reaper`** (Klon `seo/scripts/job-reaper.sh`): `sec_jobs` mit `in_progress > 6h` → `cancelled` (OOM/SIGKILL-Zombies).
- **NO_SUBSCRIBER-Detection** im Orchestrator: `publish()`-Rückgabewert 0 → Worker tot → sofortiger Discord-Alarm (Muster SEO `_dispatch_phase34_workers`).
- **Subscribe-vor-Publish** im Orchestrator (Message-Verlust-Falle aus SEO agent.py:2577).

## 6. Betriebsfestlegungen

- **systemd:** User-Units (`--user`), Muster SEO-Worker inkl. `UnsetEnvironment=CLAUDECODE CLAUDE_CODE_SESSION` (Pflicht für Claude-CLI-Calls), `Restart=always`, `MemoryMax` (Orchestrator 512M, deterministische Worker 768M, code-scan 2G).
- **Redis:** guildscout-redis `127.0.0.1:6379` **mit Passwort** aus `~/.config/shadowops-security-team.env` (chmod 600; Secret NIE in Unit-Files/Repo).
- **DB:** bestehende `security_analyst`-DB (guildscout-postgres 5433). Schema weiterhin via `SecurityDB._ensure_schema()`; `migrations/*.sql` bleibt Doku-Parität.
- **Feature-Flag:** `SECURITY_TEAM_ENABLED` bleibt Master-Switch; zusätzlich `security_team.active_workers`-Liste. Monolith bleibt bis W4-Soak Source-of-Truth für LLM-Scans.
- **Notifications:** Worker/Orchestrator → Discord-**Webhook** (Watchdog-Muster, bot-unabhängig). Das tägliche Security-Briefing im Bot (Channel `security_briefing`) bleibt und liest aus der geteilten DB.
- **Token-Budget:** Tages-Cap über Redis (Seam aus P1 aktivieren; deterministische Worker `token_cost=0`, code-scan/Fix-PRs zählen). Default 100k/Tag, Alarm bei 80 %.

## 7. Aufräumen (fest eingeplant, nicht „später")

- **W3:** Jules-Pfade deaktivieren + `agent_task_queue`-Altbestand sichten (Report an Owner), fail2ban-Adapter + -Watcher entfernen (fail2ban ist nicht installiert, #295).
- **W5:** `deep_scan.py` (Legacy) entfernen, `PROJECT_SECURITY_PROFILES` aus dem Code in `config.yaml` auslagern (mit Drift-Warnung im config-audit-Worker), Vision-Doc auf FINAL setzen, `docs/README.md` + RAM-/Server-Zahlen repo-weit nachführen, `PROTECTED_PORT_BINDINGS` aktualisieren (leitstelle-web hat keinen Host-Port mehr).

## 8. Rollout — 5 Wellen mit Gates

| Welle | Inhalt | Gate (vor „fertig") |
|---|---|---|
| **W1** | P1 lauffähig: **minimaler `sec:trigger`-Subscribe-Loop im Orchestrator** (ohne ihn kein täglicher Soak), Redis-Auth-URL, `~/.config/shadowops-security-team.env`, `npm_audit_path`-Fix (`ZERODOX/web`), `security_team`-Sektion in Live-Config, Units installieren; `security-freshness-watchdog` + `job-reaper` + Cron-Trigger + Soak-Vergleichs-Script | Units laufen, 1 manueller `sec:trigger`-Lauf schreibt Findings + `sec_jobs` korrekt; Watchdog alarmiert bei künstlicher Staleness. **7d-Soak:** npm-audit-Worker-Output vs. Monolith deckungsgleich |
| **W2** | Orchestrator komplett (Result-Aggregation, NO_SUBSCRIBER-Detection, Token-Cap-Enforcement) + `config-audit-worker` (7 Regeln) + `container-scan-worker` (Trivy-Konsum) | Alle Regeln feuern auf künstlich präparierte Verstöße (Testumgebung/Mocks); Trivy-Findings landen dedupliziert in der DB; Unit-Tests mock-only |
| **W3** | Fix-Pipeline: `routing.py`-Extraktion, Whitelist-Fixer, Claude-CLI-PR-Kanal, Independent-Review-Action (Secret `CLAUDE_SECURITY_REVIEW_KEY` = Operator-Schritt), `human_only`-Issue-Format | 1 echter Whitelist-Fix end-to-end mit Report+Rollback-Test; 1 PR end-to-end durch Review-Gate (Test-Finding); Cap/CircuitBreaker-Tests |
| **W4** | `code-scan-worker` (Claude-CLI read-only) + `secret-scan-worker` (gitleaks installieren) → danach Monolith-LLM-Sessions per Flag aus | code-scan liefert auf einem Referenz-Repo plausible Findings; 7d-Parallel-Soak gegen Monolith-Sessions |
| **W5** | Multi-Projekt-Profile in Config, Monolith zu dünnem Orchestrator-Adapter entkernen, Legacy-Löschung, Doku-Cutover | Alle Tests grün, 7d stabiler Betrieb, PROJECT_TIMELINE/CLAUDE.md/MEMORY nachgeführt |

Jede Welle: eigener Branch/PR, Tests (mock-only), **Claude-Review vor Merge**, kein `Closes #290` bis W5.

## 9. Nicht-Ziele (unverändert aus #290)

- Keine kostenpflichtigen Security-APIs (CLI-only)
- Kein Ersatz für trivy/gitleaks/CrowdSec — Worker orchestrieren sie
- **Kein Auto-Merge** von Security-Fixes
- Kein Scan fremder Systeme — ausschließlich eigene Server/Repos des Betreibers

## 10. Offene Operator-Punkte (nicht blockierend für W1)

1. **Wartungsfenster** für Docker-Engine-Batch (#267-Rest, 5 Pakete) + Traefik-Härtung (#315) — zusammenlegen, ~5 Min Container-Downtime.
2. **Repo-Secret** `CLAUDE_SECURITY_REVIEW_KEY` für die Review-Action anlegen (W3).
3. **#296** (VM-Isolation-Konzept): für Scanner durch diese Spec beantwortet (read-only Host); für etwaige spätere schreibende Ausführ-Worker bleibt die VM-Option offen — Issue bleibt bis W3 offen, dann Entscheid dokumentieren.
