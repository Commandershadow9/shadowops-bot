# Design: Alert-Humanizer — Mensch-lesbare Discord-Meldungen

**Datum:** 2026-05-28
**Branch:** `feat/alert-humanizer`
**Status:** Design — Implementierung folgt
**Motivation:** Die Discord-Alerts (Runner-VM-Drift, Deploy, Incidents, Health) sind Status-Telemetrie statt Mensch-lesbarer Meldungen: Status-Enums (`DRIFT (unreachable → critical)`), Roh-Codes (`LOAD_CRITICAL`), Rohzahlen (`Load 1min=32.23 on 8 CPUs`) — ohne *was ist los / wie schlimm / was tun*.

## Problem (konkretes Beispiel)

**Ist-Zustand** (`#🏗️-runner-vm`):
```
🔴 Runner-VM: DRIFT (unreachable → critical)
Vorher: unreachable · Jetzt: critical
🔴 Critical Alerts
• LOAD_CRITICAL (load) — Load 1min=32.23 on 8 CPUs
🟡 Warning Alerts
• DISK_HIGH (disk) — Disk usage 84.8% on /
```
Drei solche Pings über 3h (DRIFT → DRIFT → RECOVERED) ohne Verknüpfung — in Wahrheit *ein* selbst-erholter Vorfall.

**Ziel-Zustand:**
```
🔴 CI-Runner-VM überlastet (war kurz nicht erreichbar)
Die Runner-VM (10.8.0.10) antwortet wieder, meldet aber akute Überlast.

🔴 CPU-Last 32,2 auf 8 Kernen — 4× überlastet
🟡 Platte zu 84,8 % voll (/)

→ Dringlichkeit: hoch · CI-Jobs könnten hängen bleiben
→ Check: hängende Build-Jobs auf der VM · Runbook: mayday-ci-runner.md
Verlauf: nicht erreichbar → kritisch · vor 2 Min
```

## Anti-Patterns (von allen Buildern geteilt)

1. **Status-Enum ohne Erklärung** — `LOAD_CRITICAL`, `DISK_HIGH`, `DRIFT (x→y)`
2. **Rohzahl ohne Kontext** — `Load 1min=32.23 on 8 CPUs` (ist das viel?)
3. **Keine Dringlichkeit/Handlung** — kein "muss ich ran?" / "was tun?"
4. **Keine Runbook-Verweise**
5. **Kein Vorfalls-Zusammenhang** — jeder State-Change = eigener Ping

## Architektur

Ein zentrales, **dependency-freies, rein funktionales** Modul (wie `health_schema_v1.py` — nur dataclasses + stdlib, kein pydantic). Alle Embed-Builder rufen dieselben Übersetzungs-Funktionen.

### Modul 1: `src/utils/alert_humanizer.py` (neu, pure functions, voll testbar)

```python
# --- Status-Übergänge ---
def humanize_transition(prev: str, new: str) -> TransitionInfo:
    """ok|degraded|critical|unreachable × Übergang → Klartext + Dringlichkeit."""
    # Returns: TransitionInfo(headline, urgency, emoji, is_recovery)
    # Beispiele:
    #   (unreachable, critical) → "überlastet (war kurz nicht erreichbar)", urgency=HIGH
    #   (critical, degraded)    → "erholt sich (noch nicht stabil)", urgency=MEDIUM
    #   (degraded, ok)          → "wieder stabil", urgency=NONE, is_recovery=True
    #   (ok, unreachable)       → "nicht mehr erreichbar", urgency=CRITICAL

@dataclass
class TransitionInfo:
    headline: str        # "überlastet (war kurz nicht erreichbar)"
    urgency: Urgency     # Enum NONE|LOW|MEDIUM|HIGH|CRITICAL
    emoji: str
    is_recovery: bool

# --- Alert-Codes (Rohcode → Label + Kontext-Formatter) ---
def humanize_alert(alert: HealthAlert) -> str:
    """HealthAlert(code, component, message) → eine lesbare Zeile.
    Bekannte Codes bekommen Klartext + Metrik-Kontext; unbekannte
    fallen auf Title-Case(code) + message zurück (nie Information verlieren)."""
    # LOAD_CRITICAL/LOAD_HIGH  → "CPU-Last 32,2 auf 8 Kernen — 4× überlastet"
    # DISK_HIGH/DISK_CRITICAL  → "Platte zu 84,8 % voll (/)"
    # MEM_*/MEMORY_*           → "Arbeitsspeicher zu X % belegt"
    # SERVICE_DOWN/_FAILED     → "Dienst <name> läuft nicht"
    # <unbekannt>              → "<Title-Case-Code>: <message>"

ALERT_LABELS: dict[str, AlertSpec]   # code → (label, parser, runbook_hint)

# --- Metrik-Parser (Rohstring → Verhältnis/Kontext) ---
def parse_load(message: str) -> str | None:   # "Load 1min=32.23 on 8 CPUs" → "32,2 auf 8 Kernen (4× überlastet)"
def parse_disk(message: str) -> str | None:   # "Disk usage 84.8% on /" → "84,8 % voll (/)"
# Parser sind defensiv: Regex-Match scheitert → None → Fallback auf Rohmessage.

# --- Dringlichkeit → Handlungs-Hinweis ---
def urgency_line(urgency: Urgency) -> str:    # "→ Dringlichkeit: hoch · CI-Jobs könnten hängen bleiben"

# --- Runbook-Verweis pro Rolle/Komponente ---
RUNBOOKS: dict[str, str]   # role|component → Runbook-Pfad
def runbook_for(role: str, components: list[str]) -> str | None
```

**Lokalisierung:** Deutsch, Komma als Dezimaltrenner (32,2), Umlaute Pflicht.
**Robustheit:** Jede Funktion hat einen Fallback der NIE Information verschluckt — unbekannter Code/unparsebare Metrik → Rohwert durchreichen, nie leer.

### Modul 2: Incident-Grouping in `phase_5e_health_aggregator.py`

State-Changes desselben Hosts innerhalb eines **Incident-Fensters** zu *einem* Vorfall bündeln:

- **Incident-Start:** erster Drift weg von `ok` (→ degraded/critical/unreachable).
- **Während offen:** weitere Drifts (critical→degraded etc.) **editieren die bestehende Incident-Message** (Discord `message.edit`) statt neue Posts — Verlauf als Timeline im selben Embed.
- **Incident-Ende:** Drift zurück auf `ok` → Recovery, Embed final editiert mit **Gesamt-Downtime** + "selbst-erholt (keine manuelle Aktion erkennbar)" wenn zutreffend.
- **State:** `dict[host, OpenIncident]` im Cog (in-memory reicht; SQLite-`health_history` ist schon da für Persistenz/Recovery-after-restart als optionaler Ausbau).
- **OpenIncident:** `{host, started_at, message_id, transitions: list[(ts, prev, new)], worst_status}`.

Ergebnis: aus 3 Pings (DRIFT→DRIFT→RECOVERED über 3h) wird **eine** sich aktualisierende Nachricht mit Timeline + finaler Downtime.

## Migration der 5 Builder (alle nutzen Humanizer)

| Datei | Builder | Änderung |
|---|---|---|
| `cogs/phase_5e_health_aggregator.py` | `_build_drift_embed`, `_build_status_embed`, `_build_trend_embed` | Humanizer + Incident-Grouping |
| `cogs/deployment_manager.py` | `_send_deploy_success/_failure` | Schritt-Liste → Klartext-Zusammenfassung, Dauer mit Kontext, Fehler-Step hervorgehoben |
| `cogs/project_monitor.py` | `_create_incident_embed/_recovery_embed` | `humanize_transition` + Downtime-Klartext + Runbook |
| `cogs/incident_manager.py` | `_create_incident_embed` | Severity-Enum → Dringlichkeit-Klartext |
| `utils/embeds.py` | `EmbedBuilder.create_alert` + neu `EmbedBuilder.health_drift()` | Humanizer als gemeinsame Quelle, damit `create_alert` denselben Ton trifft |

**Konsistenz-Anker:** `STATUS_EMOJI`/`STATUS_COLOR` (phase_5e:67/74) bleiben Single-Source — der Humanizer importiert sie bzw. sie wandern nach `alert_humanizer.py` und phase_5e importiert von dort (dedupliziert).

## Teststrategie (pytest, dependency-frei)

- `tests/unit/test_alert_humanizer.py` (neu): jede pure function gegen Tabelle von (input → erwarteter Output). Schwerpunkte:
  - Alle bekannten Status-Übergänge (4×4 Matrix, sinnvolle Teilmenge)
  - Bekannte Alert-Codes (LOAD/DISK/MEM/SERVICE) mit echten message-Strings aus Prod
  - **Fallback-Pfade:** unbekannter Code, unparsebare Metrik → Rohwert bleibt erhalten (kein Crash, keine leere Zeile)
  - Dezimal-Lokalisierung (32.23 → "32,2")
- `tests/unit/test_phase_5e_health_aggregator.py` (erweitern): Incident-Grouping — 3 Drifts erzeugen 1 Message (edit), Recovery zeigt Downtime.
- Bestehende Tests müssen grün bleiben (Embed-Struktur-Asserts ggf. anpassen).
- **Einzeln ausführen** (`.venv/bin/python -m pytest tests/unit/test_alert_humanizer.py -q`).

## Umsetzungs-Reihenfolge (Abhängigkeit)

1. **`alert_humanizer.py` + Tests** — Basis, blockiert alles. Muss grün sein bevor Builder migriert werden.
2. **Incident-Grouping** in phase_5e (nutzt Humanizer) + Tests.
3. **Builder-Migration** (4 weitere) — können nach (1) parallel, je eigener Commit.
4. **PR** gegen `main`, Vorher/Nachher im Body, alle Tests grün.

## Deploy-Hinweis

ShadowOps Auto-Deploy nur via **PR-Merge** (Direct-Push geblockt, Memory `project_shadowops-webhook-pattern`). Bot ist `Restart=always`. Nach Merge: Webhook → Pull → Restart. Kein manueller Eingriff nötig, aber Health-Watchdog (`shadowops-watchdog`) im Auge behalten.

## Nicht im Scope (YAGNI)

- Health-Endpoint auf der Runner-VM (10.8.0.10, `/opt/runner-health/`) ändern — die Rohdaten reichen, Übersetzung passiert im Bot.
- Persistente Incident-History über Bot-Restart hinaus (in-memory reicht; SQLite-Ausbau optional später).
- Neue Alert-Codes erfinden — nur bestehende übersetzen + sauberer Fallback.
