---
title: Security Engine v6 — Proactive Mode + Polish
status: active
version: v6
last_reviewed: 2026-04-15
owner: CommanderShadow9
related:
---

# Security Engine v6 — Proactive Mode + Polish (Phase 4)

Diese Datei dokumentiert Phase 4 — den Proactive Mode (regelmaessige Coverage-Checks, Trend-Analyse, automatische Haertungsvorschlaege), die Integration mit der `agent_learning` DB, die Cog-Umstellung sowie das Aufraeumen der alten Module.

---

## Task 4.1: ProactiveMode

**Files:**
- Create: `src/integrations/security_engine/proactive.py`
- Test: `tests/unit/test_proactive_mode.py`

**Beschreibung:** Regelmaessige Coverage-Checks und proaktive Haertung:
- Welche Bereiche wurden >7 Tage nicht gescannt?
- Gibt es Trends (steigende Ban-Rate, neue IPs)?
- Automatische Haertungsvorschlaege basierend auf DB-Wissen

Nutzt `scan_coverage` und `knowledge` Tabellen aus der bestehenden DB.

---

## Task 4.2: agent_learning Integration

**Files:**
- Modify: `src/integrations/security_engine/db.py` (add agent_learning queries)

**Beschreibung:** Security-Fixes als `agent_feedback` in die agent_learning DB schreiben, damit alle Agents davon lernen. Gleiche Pattern wie Patch Notes:
- Erfolgreiche Fixes -> positive Feedback
- Fehlgeschlagene Fixes -> negative Feedback
- Automatische Quality-Scores

---

## Task 4.3: Cogs aktualisieren

**Files:**
- Modify: `src/cogs/monitoring.py` (SecurityEngine statt self_healing/orchestrator)
- Modify: `src/cogs/admin.py` (SecurityEngine statt self_healing/orchestrator)
- Modify: `src/cogs/inspector.py` (neue Stats-Methoden)

**Beschreibung:** Slash-Commands auf SecurityEngine umstellen:
- `/remediation-stats` -> `security_engine.get_stats()`
- `/set-approval-mode` -> `security_engine.set_approval_mode()`
- `/scan` -> `security_engine.trigger_scan()`

---

## Task 4.4: Alte Module entfernen

**Files:**
- Delete: `src/integrations/knowledge_base.py` (-> security_engine/db.py)
- Delete: `src/integrations/self_healing.py` (-> security_engine/engine.py)
- Delete: `src/integrations/orchestrator/` (-> security_engine/reactive.py + executor.py)
- Delete: `src/integrations/analyst/security_analyst.py` (-> security_engine/deep_scan.py)
- Delete: `src/integrations/analyst/analyst_db.py` (-> security_engine/db.py)
- Keep: `src/integrations/analyst/prompts.py` (wird von deep_scan.py importiert)
- Keep: `src/integrations/analyst/activity_monitor.py` (wird von engine.py importiert)

**WICHTIG:** Erst loeschen wenn alle Tests gruen sind und der Bot erfolgreich startet.

---

## Task 4.5: CLAUDE.md + Doku aktualisieren

**Files:**
- Modify: `CLAUDE.md` (Neue Architektur dokumentieren)
- Modify: `.claude/rules/safety.md` (SecurityEngine-Referenzen)
- Create: `docs/security-engine-v6-overview.md`

---

## Task 4.6: Integration-Tests

**Files:**
- Create: `tests/integration/test_security_engine_integration.py`

**Beschreibung:** End-to-End Test mit gemockter DB:
1. Event erstellen
2. SecurityEngine.handle_security_event() aufrufen
3. Pruefen: Fix wurde aufgerufen, DB wurde geschrieben, Discord wurde benachrichtigt
4. Pruefen: No-Op bei doppeltem Event
5. Pruefen: Fast-Path bei 1 Event vs. KI-Plan bei 3+ Events
