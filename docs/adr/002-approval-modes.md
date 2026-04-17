---
title: ADR-002: Dreistufiges Approval-Mode-System
status: accepted
last_reviewed: 2026-04-15
owner: CommanderShadow9
---

# ADR-002: Dreistufiges Approval-Mode-System

**Status:** Accepted
**Datum:** 2026-03-10
**Kontext:** Auto-Remediation fuehrt Shell-Befehle auf dem Produktiv-Server aus (z.B. `cscli`, `fail2ban-client`, Firewall-Regeln). Unkontrollierte automatische Ausfuehrung ist ein Sicherheitsrisiko — ein fehlerhafter AI-Fix koennte den Server lahmlegen.

## Entscheidung

Drei Approval-Modi, implementiert im `ApprovalModeManager`:

- **PARANOID:** Jeder Fix braucht explizite User-Genehmigung via Discord. Aktuell aktiver Modus (Lernphase).
- **BALANCED:** LOW/MEDIUM-Severity-Fixes werden automatisch ausgefuehrt, HIGH/CRITICAL erfordern Approval. Confidence-Schwellwert aus der AI-Analyse wird beruecksichtigt.
- **AGGRESSIVE:** Maximale Automation — die meisten Fixes werden auto-ausgefuehrt, nur bei sehr niedriger Confidence oder unbekannten Fix-Typen wird Approval angefragt.

Die `ApprovalDecision`-Klasse kapselt die Entscheidung mit `should_auto_execute`, `reason`, `risk_level` und `confidence_threshold`. Der Manager erhaelt Event-Details und Fix-Strategie und entscheidet basierend auf Severity, Source und AI-Confidence.

## Alternativen

- **Nur manuell:** Zu langsam bei naechtlichen Angriffen, User muss immer online sein.
- **Nur automatisch:** Zu riskant — ein falscher Fix kann Dienste stoppen oder Firewall-Regeln zerstoeren.
- **Severity-basierte Eskalation ohne Modi:** Nicht flexibel genug — Operator kann Vertrauenslevel nicht global anpassen.

## Konsequenzen

**Positiv:**
- Sicher im PARANOID-Modus: kein Fix ohne menschliche Kontrolle.
- Schrittweise Vertrauens-Eskalation moeglich (PARANOID → BALANCED → AGGRESSIVE).
- Jede Entscheidung ist nachvollziehbar mit `reason`-String.

**Negativ:**
- PARANOID-Modus bedeutet manuelle Arbeit bei jedem Event, auch bei trivialen Fixes.
- AGGRESSIVE-Modus erfordert gruendliches Testen, bevor er aktiviert werden kann.
- Confidence-Schwellwerte muessen empirisch kalibriert werden.
