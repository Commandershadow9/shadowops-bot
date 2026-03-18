# Security Analyst — Autonomer, lernender AI Security Engineer

## Uebersicht

Der Security Analyst ist ein autonomer AI-Agent mit **Full Learning Pipeline**:
Er scannt, fixt, verifiziert seine Fixes, bewertet seine eigenen Findings,
und passt seine Intensitaet automatisch an den Workload an.

**Kernprinzip:** Schlanker Prompt + wachsende Knowledge-DB. Die DB ist die Wissensquelle, der Prompt nur der Auftrag.

### Wie funktioniert es?

1. **ActivityMonitor** erkennt, wann der User idle ist (SSH, Git, Claude-Prozesse, Discord-Praesenz)
2. **Pre-Session Maintenance:** Security-Profile, Git-Activity, Fix-Verifikation, Knowledge-Decay
3. **Session-Planner** waehlt den optimalen Modus basierend auf Workload
4. **Scan-Plan** wird datengetrieben erstellt (Coverage-Luecken, Hotspots, Git-Delta)
5. **Dual-Engine AI** (Codex primaer, Claude Fallback) fuehrt den Scan durch
6. **Fix-Phase** arbeitet Findings ab — mit vollem Knowledge-Kontext + vorherigen Fix-Versuchen
7. **Post-Session:** Coverage, Quality-Assessment, Fix-Attempts in DB

### Adaptive Session-Steuerung

| Offene Findings | Modus | Sessions/Tag | Timeout | Turns |
|-----------------|-------|--------------|---------|-------|
| >=20 | `fix_only` | bis 3 | 2h | 200 |
| 5-19 | `full_scan` | bis 2 | 45min | 60 |
| 1-4 | `quick_scan` | 1 | 20min | 30 |
| 0 | `maintenance` | 1 (oder 0) | 10min | 15 |

Bei 0 Findings und letztem Scan <3 Tage: Gar keine Session (spart Tokens).

### Full Learning Pipeline

```
VOR dem Scan:
  _pre_session_maintenance()
  ├── _sync_project_security_profiles()  → Angriffsoberflaechen in DB
  ├── _sync_git_activity_to_db()         → Commits, Hotspots, Security-Fixes
  ├── _verify_recent_fixes()             → 10 Fixes der letzten 14 Tage pruefen
  └── decay_knowledge_confidence()       → -5% fuer Wissen >14 Tage alt

SCAN mit priorisiertem Plan:
  build_scan_plan()
  ├── 🔴 Coverage-Luecken (>5 Tage nicht gecheckt)
  ├── 🔴 Fix-Regressionen
  ├── 🔴 Noch nie gepruefte Bereiche
  ├── 🟡 Finding-Hotspots (haeufigste Kategorien)
  └── 🟡 Projekte mit Security-Commits

FIX mit Knowledge-Kontext:
  ├── Vorherige Fix-Versuche pro Finding sichtbar
  ├── Gesamtes gelerntes Wissen (Knowledge-DB)
  ├── Geschuetzte Infrastruktur (nur Issue/PR)
  └── Fix-Ergebnis aufgezeichnet (Ansatz + Commands + Erfolg/Misserfolg)

NACH der Session:
  ├── scan_coverage → Bereiche gecheckt/uebersprungen
  ├── finding_quality → Confidence, Discovery-Method, False Positives
  ├── fix_attempts → Ansatz, Ergebnis, Seiteneffekte
  ├── Cross-Agent-Knowledge → Offene Criticals in agent_learning DB
  ├── LearningNotifier → Automatisches Embed in 🧠-ai-learning
  └── Alles fliesst in die naechste Session zurueck

EXTERNE TRIGGER:
  CrowdSec/Fail2ban Critical Event → trigger_event_scan()
  → Sofortiger Quick-Scan (2h Cooldown, ignoriert Session-Limit)
```

### Discord-Notifications (🧠-ai-learning)

Der Analyst postet automatisch nach jeder Session:
- Kompaktes Embed: Modus, Findings, Fixes, Tokens, Coverage
- Farbcodiert: Gruen=Fixes, Orange=Findings, Rot=Regressionen, Blau=Routine
- Woechentliches Summary (Montag): Alle Agents, DB-Groessen, Trends
- Meilensteine: 🏆 bei 10/25/50/100/250 Fixes/Findings/Sessions

Slash-Command `/agent-stats` zeigt das Learning-Dashboard on-demand.

### 3-Ebenen-Schutz gegen Infrastruktur-Breaks

| Ebene | Mechanismus |
|-------|-------------|
| Prompt | GESCHUETZTE INFRASTRUKTUR Tabelle (Bind-Adressen, Ports, Docker, UFW) |
| Knowledge-DB | `infrastructure_constraints/bind_addresses_docker_bridge` (99% Confidence) |
| Validierung | PROTECTED_PORT_BINDINGS in Health-Snapshot (8766, 9090, 9091) |

Ausloeser: Incident 2026-03-17 — Analyst aenderte 0.0.0.0→127.0.0.1, 11h Ausfall.

### Dual-Engine AI

| Eigenschaft | Codex (Primaer) | Claude (Fallback) |
|------------|-----------------|-------------------|
| CLI | `codex exec --output-schema` | `claude -p --allowedTools` |
| Modell | `gpt-5.3-codex` (konfigurierbar) | `claude-opus-4-6` (konfigurierbar) |
| Output | Strukturiertes JSON via Schema | JSON in Temp-Datei + stdout-Extraktion |

### Fehlerbehandlung

| Fehler # | Backoff | Aktion |
|----------|---------|--------|
| 1 | 30 Minuten | Discord-Alert, naechster Versuch nach Cooldown |
| 2 | 2 Stunden | Discord-Alert, naechster Versuch nach Cooldown |
| 3+ | Tages-Sperre | Discord-Alert (rot), Analyst fuer heute deaktiviert |

### Discord-Notifications

| Event | Farbe | Wann |
|-------|-------|------|
| Session gestartet | Blau | Bei jedem Start |
| Session erfolgreich | Gruen/Gelb/Rot | Nach Ergebnis |
| Session fehlgeschlagen | Orange | Bei Fehler |
| Fix-Regression erkannt | Orange | Bei Verifikation |
| Health-Regression | Rot | Services nach Session down |

---

## Datenbank

Laeuft auf dem GuildScout Postgres (Port 5433), DB: `security_analyst`.

### Tabellen (9 Stueck)

| Tabelle | Zweck |
|---------|-------|
| `sessions` | Laufende/abgeschlossene Analyse-Sessions |
| `knowledge` | Akkumuliertes Wissen mit Confidence-Decay (UPSERT per category+subject) |
| `findings` | Security-Findings mit Severity, Status, Fix-Details |
| `learned_patterns` | Wiedererkannte Muster (JSONB examples, times_seen) |
| `health_snapshots` | Service-Zustand vor/nach Sessions (+ Port-Bindings) |
| `fix_attempts` | **NEU:** Jeden Fix-Versuch mit Ansatz, Commands, Ergebnis, Seiteneffekte |
| `fix_verifications` | **NEU:** Periodische Pruefung ob Fixes noch aktiv sind |
| `finding_quality` | **NEU:** Selbstbewertung (confidence, false_positive, discovery_method) |
| `scan_coverage` | **NEU:** Welche Bereiche in welcher Session gecheckt wurden |

### Knowledge-Kategorien

| Kategorie | Inhalt |
|-----------|--------|
| `infrastructure_constraints` | Geschuetzte Bereiche (Bind-Adressen, Ports) |
| `project_activity` | Git-Commits, Hotspots, Security-Fixes pro Projekt |
| `project_security` | Angriffsoberflaeche, Auth, Secrets pro Projekt |
| `security` | Akkumuliertes Security-Wissen aus allen Sessions |
| `operational` | Betriebliche Erkenntnisse |

### Scan-Bereiche (10 Standard-Areas)

`firewall`, `ssh`, `docker`, `permissions`, `packages`, `services`, `logs`, `network`, `credentials`, `dependencies`

---

## Dateien

| Datei | Zweck |
|-------|-------|
| `src/integrations/analyst/security_analyst.py` | Hauptklasse, Session-Planner, Learning Pipeline, Pre-Session Maintenance |
| `src/integrations/analyst/analyst_db.py` | asyncpg DB-Layer (9 Tabellen, build_ai_context, build_scan_plan) |
| `src/integrations/analyst/activity_monitor.py` | User-Aktivitaetserkennung |
| `src/integrations/analyst/prompts.py` | System-Prompt, Kontext-Template, Fix-Prompt (mit Schutzregeln) |
| `src/schemas/analyst_session.json` | JSON-Schema (+ areas_checked, finding_assessments) |

## Konfiguration

In `config/config.yaml`:

```yaml
security_analyst:
  enabled: true
  database_dsn: "postgresql://security_analyst:PASSWORD@127.0.0.1:5433/security_analyst"
  max_sessions_per_day: 1         # Basis-Limit (dynamisch erhoehbar bei hohem Backlog)
  model: "gpt-5.3-codex"          # Primaeres Modell (Codex)
  fallback_model: "claude-opus-4-6"  # Fallback-Modell (Claude)
```

Das dynamische Session-Limit wird automatisch basierend auf offenen Findings berechnet
(bis zu 3x bei schwerem Backlog).

## Troubleshooting

```bash
# Analyst-Logs pruefen
sudo journalctl -u shadowops-bot --since "1 hour ago" | grep -i analyst

# DB-Verbindung testen
docker exec -i guildscout-postgres psql -U security_analyst -d security_analyst \
  -c "SELECT COUNT(*) FROM sessions;"

# Offene Findings anzeigen
docker exec -i guildscout-postgres psql -U security_analyst -d security_analyst \
  -c "SELECT id, severity, title FROM findings WHERE status='open' ORDER BY id;"

# Fix-Verifikationen pruefen
docker exec -i guildscout-postgres psql -U security_analyst -d security_analyst \
  -c "SELECT fa.id, f.title, fa.result, fa.still_valid FROM fix_attempts fa JOIN findings f ON f.id=fa.finding_id ORDER BY fa.created_at DESC LIMIT 10;"

# Coverage-Luecken anzeigen
docker exec -i guildscout-postgres psql -U security_analyst -d security_analyst \
  -c "SELECT area, MAX(checked_at) as last_check FROM scan_coverage WHERE checked=TRUE GROUP BY area ORDER BY last_check;"

# Knowledge-DB inspizieren
docker exec -i guildscout-postgres psql -U security_analyst -d security_analyst \
  -c "SELECT category, subject, confidence FROM knowledge ORDER BY category, subject;"

# Manueller Scan via Discord
# /security-scan [fokus]

# ActivityMonitor debuggen
sudo journalctl -u shadowops-bot | grep -i "activity\|idle\|session-plan"
```

## Design-Dokument

Vollstaendiges Design: `docs/plans/2026-03-18-analyst-learning-pipeline-design.md`
