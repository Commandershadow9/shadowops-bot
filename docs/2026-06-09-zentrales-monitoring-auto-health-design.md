# Zentrales Monitoring & Auto-Health über ShadowOps — Design

**Status:** Entwurf (Design approved 2026-06-09, Spec in Review)
**Autor:** Claude (Opus 4.8) mit cmdshadow
**Betroffene Systeme:** shadowops-bot (Engine), ZERODOX, GuildScout, MayDay, Server (cmdshadow-Crontab + user-systemd Watchdogs)
**Auslöser:** Server-Umzug Blue→Green (mayday-sim#491) — verstreute Health-Checks gingen beim Cut-over verloren, weil nirgends vollständig stand, *was* überwacht wird.

---

## 1 · Kontext & Problem

Monitoring/Health läuft heute auf **drei parallelen, teils überlappenden Schichten**:

| Schicht | Umfang | Charakter | Problem |
|---|---|---|---|
| **~12 Health-Crons** (cmdshadow-Crontab, `ZERODOX/scripts/`) | synthetic-monitor, cron-health-check, akquise-ai-watchdog, agent-listener-health, ensure-analytics-network … | je eigenes Script, ZERODOX-lokal | verstreut, beim Umzug verloren-gegangen-Risiko |
| **28 user-systemd Watchdog-Units** (~14 Timer) | zerodox/mayday/guildscout/akquise/disk/memory/ki-cost/drift … | externe Defense-in-Depth, Discord-Alert | teils redundant zu ShadowOps |
| **ShadowOps `project_monitor` + `auto_remediation`** | health_url + Container je Projekt, Circuit-Breaker, AI-Fix-Engine | **die zentrale Engine — schon da, aber nicht alleinverantwortlich** | übernimmt nur einen Teil |

**Kern-Schmerz:** Die Verstreutheit. Beim Cut-over gingen Checks verloren (MayDay-DB-Backup-Cron, 5 Timer, daily-cron), *weil* es kein vollständiges, versioniertes Inventar gab. Zusätzlich starteten beim Cut-over zwei Systeme unkoordiniert Container neu (Auto-Heal-Vorfall 2026-06-07).

**Nicht das Problem:** ShadowOps' Fähigkeiten — die Engine kann bereits Container-Monitoring, Auto-Remediation (balanced approval, Circuit-Breaker), AI-gestützte Fixes (codex→claude), Security-Scans (trivy/crowdsec/aide) und Discord-Approval-Workflows.

## 2 · Ziele & Nicht-Ziele

**Ziele:**
- **Zentral:** ShadowOps ist die *eine* Engine für Health/Auto-Heal/Scans/Alerts aller Projekte.
- **Skalierbar:** Neuer Check = ein deklarativer YAML-Eintrag. Neues Projekt = ein Block.
- **Umzugs-sicher:** Das deklarative Inventar ist Single-Source-of-Truth und übersteht Server-Wechsel.
- **Sicher (gestuft):** Reversible Heilung autonom, riskante Aktionen nur per Approval.
- **Ausfallsicher:** Ein unabhängiger Dead-Man-Watchdog überwacht ShadowOps selbst (Defense-in-Depth bleibt, aber schlank).

**Nicht-Ziele (YAGNI):**
- Keine Voll-Zentralisierung, die ShadowOps zum Single-Point-of-Failure macht (Dead-Man bleibt extern).
- Kein Neubau der Engine — die bestehende `project_monitor`/`auto_remediation`-Basis wird *erweitert*, nicht ersetzt.
- Keine Migration von Report-only-Crons, die nichts mit Health/Heal zu tun haben, solange sie nicht ohnehin in die Engine passen (Entscheidung pro Check in Phase 0).

## 3 · Architektur (Ziel-Topologie)

ShadowOps wird die **eine Engine**. Vier interne Bausteine, ein externer Rest:

```
                ┌─────────────────────────── ShadowOps (zentral) ───────────────────────────┐
                │                                                                            │
   config.yaml  │   ┌──────────────┐   Befund   ┌──────────────┐  Policy  ┌───────────────┐ │
  (Inventar) ───┼──▶│ Check-Runner │──────────▶ │ Heal-Executor│────────▶ │ Alert-/Approval│ │──▶ Discord
                │   │ http/cont/   │            │ (gestuft)    │          │  Dispatcher    │ │
                │   │ synthetic/   │            └──────┬───────┘          └───────────────┘ │
                │   │ resource/sec │                   │                                     │
                │   └──────────────┘            ┌──────▼───────┐                             │
                │                               │ Maintenance- │  ← Pause-Schalter           │
                │                               │ Gate         │    (global/projektweise)    │
                │                               └──────────────┘                             │
                └────────────────────────────────────┬───────────────────────────────────────┘
                                                      │ überwacht "lebt ShadowOps?"
                                          ┌───────────▼────────────┐
                                          │  Dead-Man-Watchdog      │ ──▶ eigener Discord-Webhook
                                          │  (user-systemd, extern, │     (bot-unabhängig)
                                          │   einziger externer Rest)│
                                          └─────────────────────────┘
```

- **Check-Runner** — führt alle Checks config-getrieben aus. Check-Typen: `http`, `container`, `synthetic` (Skript/Business), `resource` (disk/mem/netz), `security` (Scans, existiert).
- **Heal-Executor** — wendet pro Check die gestufte Heal-Policy an (§5).
- **Alert-/Approval-Dispatcher** — Discord (existiert: Channels approvals/ai-learning/code-fixes/orchestrator).
- **Maintenance-Gate** — globaler/projektweiser Pause-Schalter, der Auto-Heal aussetzt (löst den Cut-over-Vorfall).
- **Dead-Man-Watchdog** *(extern, einziger Rest)* — minimaler user-systemd-Timer, prüft NUR ShadowOps-Liveness (`:8766/health` + `bot_ready` + NRestarts-Loop) und alarmiert bei dessen Tod über einen **eigenen** Webhook (darf nicht von ShadowOps abhängen).

## 4 · Deklaratives Check-Inventar (= „skalierbar")

Jeder Check ist ein YAML-Eintrag unter seinem Projekt. Erweiterung der bestehenden `projects:`-Sektion in `config.yaml`:

```yaml
projects:
  zerodox:
    checks:
      - id: web-liveness
        type: http
        target: https://zerodox.de/api/health
        interval: 300
        expect: { status: 200, json_path: "$.status", json_eq: "ok" }
        heal: { action: restart-container, target: zerodox-web }   # reversibel → autonom
      - id: agent-listener
        type: http
        target: https://zerodox.de/api/internal/agent-listener-health
        interval: 300
        heal: { action: restart-service, target: zerodox-support-agent }
      - id: onboarding-smoke
        type: synthetic
        script: ZERODOX/scripts/synthetic-monitor.sh
        interval: 900
        heal: { action: alert-only }                                # kein sicherer Auto-Fix
      - id: analytics-bridge
        type: container
        check: network-attached
        target: { container: zerodox-web, network: guildscout-postgres }
        interval: 600
        heal: { action: network-reconnect }                         # reversibel → autonom
```

**Check-Typ-Vertrag** (jeder Typ hat eine klare Schnittstelle):
| Typ | Prüft | Beispiel-Heal |
|---|---|---|
| `http` | HTTP-Status + optional JSON-Assertion | restart-container/service |
| `container` | Container up / RestartCount / Netz-Anbindung | restart / network-reconnect |
| `synthetic` | Business-/E2E-Skript (Exit-Code + Marker) | alert-only / restart |
| `resource` | Disk-%, RAM/Swap-%, Inode | auto-prune (reversibel) / alert |
| `security` | trivy/crowdsec/aide (existiert) | alert / approval-Fix |

Das ersetzt die verstreuten Crons und macht das Inventar zur Single-Source-of-Truth.

## 5 · Gestufte Heal-Policy (sicher)

Spiegelt die `server-safety`/`autonomy`-Regeln (reversibel = einfach machen, riskant = stop & fragen):

| Stufe | Aktionen | Verhalten | Schutz |
|---|---|---|---|
| **`reversible-auto`** | Container-Restart, Netz-Reconnect, Service-Neustart, Disk-Prune | Bot macht's **sofort selbst** | **Circuit-Breaker** (max 5/h → Eskalation statt Loop; existiert) |
| **`approval-required`** | Code-Fix, Deploy, Config/Secret-Änderung, DB-Eingriff | Discord-**Approval** vor Ausführung | `approval_mode: balanced`, `min_confidence: 0.85` (existiert) |
| **`alert-only`** | (kein sicherer Auto-Fix) | nur melden | — |

**Maintenance-Gate** — der zentrale Wartungs-Schalter:
- `maintenance on zerodox` (Discord-Command **oder** Flag-Datei) → Auto-Heal für ZERODOX (oder `global`) ist pausiert, Checks laufen weiter (nur kein Heal), optional gedrosselte Alerts.
- `maintenance off zerodox` → Heal wieder aktiv.
- **Löst den Ausgangs-Punkt:** Statt cron-health-check *und* project_monitor manuell zu pausieren, ein Schalter im einen System. Vor jedem Deploy/Wartung: Gate an.

## 6 · Ausgangs-Inventar (Basis für Phase 0)

Bekannter Stand (Phase 0 verfeinert + ergänzt um GuildScout-/MayDay-eigene Crons):

**Health-Crons (cmdshadow-Crontab):**
| Cron | Intervall | Kategorie | Ziel-Check-Typ | Heal heute |
|---|---|---|---|---|
| `cron-health-check.sh` | */10 | liveness | http | nur Alert |
| `synthetic-monitor.sh` | */15 | funktional | synthetic | nur Alert |
| `akquise-ai-watchdog.sh` | */5 | liveness | http | nur Alert |
| `akquise-ai-synthetic-check.sh` | */15 | funktional | synthetic | nur Alert |
| `cron-agent-listener-health.sh` | */5 | funktional | http/synthetic | nur Alert |
| `ensure-analytics-network.sh` | @reboot+*/10 | ressource/netz | container | **network-reconnect** |
| `ci-main-health-check.sh` | hourly | meta/CI | http(GitHub) | nur Alert |
| `billing-pdf-drift-check` | daily | business | http | nur Alert |
| `cron-soak-monitor.sh` | daily | meta/report | (Report — Phase-0-Entscheid) | — |
| `cron-stale-pr-monitor.sh` | weekly | meta/report | (Report — Phase-0-Entscheid) | — |
| `cron-backup-monitor.sh` | weekly | meta/report | (Report — Phase-0-Entscheid) | — |

**user-systemd Watchdogs (~14 aktiv / 28 Units):**
| Watchdog | Kategorie | Ziel |
|---|---|---|
| `zerodox-watchdog`, `guildscout-watchdog`, `mayday-sim-watchdog`, `zerodox-akquise-ai-watchdog`, `mayday-ci-runner-watchdog` | liveness/http | → Engine `http`-Check |
| `disk-hygiene-watchdog`, `memory-watchdog` | resource | → Engine `resource`-Check |
| `ai-agent-framework-watchdog`, `cmdshadow-design-watchdog`(+healthcheck) | service-liveness | → Engine `container`/`http` |
| `mayday-sim-build-drift-watchdog`, `doku-drift-watchdog`, `check-worker-drift`, `ki-cost-watchdog` | drift/meta/report | Phase-0-Entscheid (Engine vs. bleibt) |
| **`shadowops-watchdog`, `shadowops-drift-watchdog`** | **Dead-Man** | **bleiben extern** (überwachen ShadowOps selbst) |

## 7 · Migrationsplan (projektweise, Parallelbetrieb)

- **Phase 0 — Inventar vervollständigen:** Alle Crons + 28 Watchdog-Units + GuildScout-/MayDay-eigene Crons katalogisieren → pro Check: Typ, Aktion, ShadowOps-Äquivalent **oder Lücke**. Report-only-Checks bewusst als „bleibt Cron" oder „Engine" markieren. Ergebnis = vollständige Inventar-Tabelle (Teil dieser Spec / eigenes Doc).
- **Phase 1 — Engine-Lücken schließen:** Fehlende Check-Typen in ShadowOps nachrüsten — v.a. `synthetic` (Skript-Runner mit Exit-Code/Marker) und `resource` (disk/mem/netz). Deklaratives `checks:`-Schema (§4) + Maintenance-Gate (§5) + Heal-Stufen (§5) implementieren. TDD.
- **Phase 2 — ZERODOX zuerst:** ZERODOX-Checks deklarativ in ShadowOps aktivieren, **parallel** zu den Alt-Crons/-Watchdogs. Übernahme **aktiv real triggern** (Fehler injizieren → ShadowOps erkennt + heilt sichtbar im Discord-Channel) + **max 24 h** Parallel-Beobachtung. Erst nach beweisbarer Übernahme (§8) Alt-Cron/-Watchdog abschalten.
- **Phase 3 — GuildScout, dann MayDay** analog.
- **Phase 4 — Final:** Dead-Man-Watchdog härten (ggf. shadowops-watchdog + shadowops-drift-watchdog zu einem konsolidieren), alle redundanten Watchdogs abschalten, Inventar-Doku als SSoT finalisieren.
- **Eiserne Regel:** Ein alter Check stirbt **erst nach beweisbarer Übernahme** — nie vorher.

## 8 · Cut-over-Kriterien — „wann darf das Doppelsystem sterben?"

Pro migriertem Check muss **alles** erfüllt sein, bevor das Alt-System (Cron/Watchdog) abgeschaltet wird:

1. **Funktionale Parität:** ShadowOps-Check liefert für denselben Zustand dasselbe Urteil wie der Alt-Check (verifiziert über ≥1 echten oder injizierten Fehlerfall).
2. **Alert-Parität:** ShadowOps-Alert landet im richtigen Discord-Channel mit verwertbarem Inhalt (deckungsgleich zum Alt-Alert).
3. **Heal-Verifikation** (falls Check ein Heal hat): Chaos-Test bestanden — Fehler injiziert → ShadowOps heilt reversibel → Recovery bestätigt; Circuit-Breaker greift bei Loop.
4. **Maintenance-Gate greift:** Gate an → kein Heal; Gate aus → Heal wieder aktiv (getestet).
5. **Aktiv-real verifiziert (statt passiv gewartet):** Der Befund wird durch **bewusstes, reales Triggern** erzwungen — Container real gestoppt, Endpoint real abgeklemmt, Disk real gefüllt — so dass Erkennung + Heal **sichtbar live** durchlaufen (real, nicht simuliert, nicht abgewartet). Ergänzende passive Parallel-Beobachtung: **max 24 h** (Projekt-Tempo), nicht 7 Tage.
6. **Dokumentiert:** Check steht im Inventar (SSoT), Alt-Mechanismus als „abgelöst durch ShadowOps:<check-id>" markiert.

**Abschalt-Reihenfolge pro Check:** Alt-Cron auskommentieren / Watchdog-Timer `disable` (nicht löschen) → 48 h beobachten → erst dann entfernen. Rollback = Alt-Mechanismus reaktivieren (1 Zeile).

**Dead-Man-Watchdog wird NIE abgeschaltet** — er ist der bewusste externe Rest.

## 9 · Verifikation / Testing

- **Unit/TDD:** Check-Runner pro Typ, Heal-Executor pro Stufe, Maintenance-Gate, Circuit-Breaker — isoliert testbar (pure Logik wo möglich).
- **Parallel-Vergleich:** Während Phase 2/3 Alert-Logs beider Systeme diffen.
- **Chaos-Tests:** Container stoppen → Heal; Netz trennen → Reconnect; Disk füllen → Prune.
- **Dead-Man-Test:** shadowops-bot stoppen → externer Watchdog alarmiert (über eigenen Webhook).
- **Maintenance-Test:** Gate an → Container stoppen → kein Heal → Gate aus → Heal.

## 10 · Risiken & Mitigation

| Risiko | Mitigation |
|---|---|
| ShadowOps fällt aus → kein Monitoring | Dead-Man-Watchdog (extern, eigener Webhook) |
| Heal-Loop (Container restart-crash-restart) | Circuit-Breaker (max 5/h → Eskalation) |
| Auto-Heal während Deploy/Wartung | Maintenance-Gate vor jedem Eingriff |
| Check beim nächsten Umzug verloren | Inventar als versionierte SSoT in Git |
| Migrations-Loch (Alt zu früh aus) | Eiserne Regel: beweisbare Übernahme (§8) + 48h-disable-vor-delete |
| Riskante Auto-Aktion (Code/Deploy/Secret) | Stufe `approval-required` (Discord-Approval) |

## 11 · Offene Punkte (Phase-0-Entscheidungen)

- Report-only-Crons (soak-monitor, stale-pr, backup-monitor, ki-cost, doku-drift): in die Engine als `report`-Typ, oder bewusst als eigenständige Cron-/Watchdog-Schicht belassen? (Kein Health/Heal — niedrige Priorität.)
- Maintenance-Gate-Trigger: Discord-Command **und/oder** Flag-Datei **und/oder** automatisch während `deploy.sh`-Lauf?
- Dead-Man-Konsolidierung: shadowops-watchdog + shadowops-drift-watchdog zu einem, oder beide behalten (zwei unabhängige Augen auf ShadowOps)?
- GuildScout-/MayDay-eigene Crons: vollständiges Inventar steht noch aus (Phase 0).

---

*Nächster Schritt nach Spec-Approval: Implementierungsplan via writing-plans (Phase 0 zuerst — Inventar).*
