# ADR-007: Jules SecOps Workflow

**Status:** Accepted
**Datum:** 2026-04-11
**Kontext:** Security-Engine v6, GitHub Integration, AI-Review-Pipeline

## Kontext

Der ShadowOps Bot betreibt einen SecurityScanAgent, der regelmaessig Security-Findings erkennt.
Code-Level-Findings (NPM/Python Dependencies, Dockerfile-Issues) erfordern Pull-Requests in
den betroffenen Repos. Ein initialer Versuch, diese an Google Jules zu delegieren (via Gemini
generiert), fuehrte zum PR #123 Vorfall — ein infinite Review-Loop mit 31 Kommentaren in 90 Minuten.

## Entscheidung

### Architektur: Modular Monolith (Option A)

Statt eines separaten Microservice (Option C) wird der Jules-Workflow als Mixin im bestehenden
`github_integration/` Package implementiert. Begruendung:

- **Ressourcen-Sharing:** Webhook-Endpoint, AI-Engine, Discord-Client, DB-Pools werden wiederverwendet
- **VPS-Constraints:** 8 GB RAM, ~5 PRs/Tag — kein Bedarf fuer Service-Split
- **Upgrade-Pfad:** Saubere Modul-Grenzen ermoeglichen spaeteres Extrahieren in eigenen Service

### Fix-Modus: Hybrid

- Code-Findings (npm_audit, pip_audit, dockerfile, code_vulnerability) → Jules via GitHub-Issue
- Infrastruktur-Findings (UFW, Fail2ban, CrowdSec, AIDE) → ScanAgent fixt selbst
- Kritische Infrastruktur (SSH, DB-Schema) → Nur manuell

### Review-Architektur: Deterministisch + Strukturiert

- Claude Opus liefert BLOCKER/SUGGESTION/NIT als JSON (Schema-validiert)
- Verdict wird deterministisch berechnet (nicht KI-entschieden) — verhindert Confidence-Oszillation
- Maximal 1 Comment pro PR (Edit statt neu) — verhindert Webhook-Loop

### Loop-Schutz: Defense-in-Depth (7 Schichten)

Jede Schicht faengt einen spezifischen Fehlervektor ab:

1. Trigger-Whitelist (nur pull_request:opened/synchronize/ready_for_review)
2. SHA-Dedupe (atomic PostgreSQL UPDATE-Claim)
3. Cooldown (5 Minuten zwischen Reviews)
4. Iteration-Cap (max 5 pro PR)
5. Circuit-Breaker (Redis, 20/h pro Repo)
6. Time-Cap (max 2h pro PR)
7. Single-Comment-Edit (PATCH erzeugt kein Webhook-Event)

### State: PostgreSQL mit Atomic Lock

- `jules_pr_reviews` Tabelle mit UNIQUE(repo, pr_number)
- Lock-Claim via einzelnem UPDATE WHERE ... RETURNING (race-free)
- Stale-Lock-Recovery nach 10min beim Bot-Start

### Learning: Agent-Learning-DB Integration

- Few-Shot-Beispiele aus `jules_review_examples` (gewichtet nach Feedback)
- Projekt-Konventionen aus `agent_knowledge` (gelernt aus Shadow-Feedback)
- Nightly-Batch klassifiziert Outcomes automatisch

## Alternativen

- **Option B: Eigenes GitHub-App (Bot):** Dediziertes Bot-Konto fuer Reviews. Vermeidet Webhook-Feedback-Loops, erfordert aber GitHub-App-Setup und zweiten Auth-Flow. Overhead fuer ~5 PRs/Tag nicht gerechtfertigt.
- **Option C: Separater Microservice:** Jules-Workflow als eigener Service mit eigenem Port/DB. Bessere Isolation, aber doppelter Ressourcenverbrauch und Deployment-Komplexitaet auf einem 8 GB VPS.
- **Option D: Rein manuelle Reviews:** Kein Automatisierung. Sicher, aber skaliert nicht — Security-Findings wuerden sich aufstauen.

## Konsequenzen

**Positiv:**
- Kein manueller Code-Review-Aufwand fuer Security-Dependency-Updates
- Strukturierte, nachvollziehbare Review-Protokolle als PR-Comments
- Selbstverbessernd durch Feedback-Loop
- Sofortiger Rollback via Config-Flag (~30s)

**Negativ:**
- Abhaengigkeit von Jules (Google) fuer Code-Fixes — Fallback ist manuell
- Claude-Token-Kosten pro Review (~4000-8000 Token)
- Lazy-Init bedeutet: erster Review nach Bot-Start ist langsamer (DB-Connect)

**Risiken:**
- Jules koennte out-of-scope Refactoring machen → scope_check im Prompt
- Claude koennte falsch-positive Blocker generieren → deterministic verdict + Feedback-Learning
- Loop-Schutz koennte zu restriktiv sein → alle Limits sind konfigurierbar

## Referenzen

- Design-Doc: `docs/design/jules-workflow.md`
- Implementation-Plan: `docs/archive/INDEX.md` (archiviert — letzter Stand per `git show`)
- PR #123 Post-Mortem: Design-Doc Anhang A
