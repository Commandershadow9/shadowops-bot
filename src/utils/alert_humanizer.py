"""Alert-Humanizer — Status-Telemetrie zu mensch-lesbarem Deutsch.

Zentrales, dependency-freies, rein funktionales Modul (analog
``integrations.health_schema_v1`` — nur stdlib + dataclasses + Enum,
KEIN pydantic). Alle Embed-Builder rufen dieselben Uebersetzungs-Funktionen,
damit Discord-Meldungen konsistent *was ist los / wie schlimm / was tun*
beantworten statt Roh-Enums (``LOAD_CRITICAL``), Rohzahlen
(``Load 1min=32.23 on 8 CPUs``) und Status-Tupel (``unreachable -> critical``)
zu zeigen.

Design-Doc: ``docs/2026-05-28-alert-humanizer-design.md``.

Single-Source-Konvention (Konsistenz-Anker):
    ``STATUS_EMOJI`` und ``STATUS_COLOR`` leben ab jetzt HIER. Der Aggregator
    ``cogs/phase_5e_health_aggregator.py`` definiert sie aktuell noch selbst
    (Zeilen ~67/74) — in einem Folge-Schritt (Builder-Migration) importiert er
    sie von hier und entfernt seine lokalen Kopien (Deduplizierung). Die Werte
    hier sind identisch zu den dortigen, inkl. des Aggregator-eigenen
    ``"unreachable"``-Zustands (nicht Teil des Schema-v1-HealthStatus, aber im
    Drift-Tracking als vierter Zustand verwendet).

Robustheit:
    Jede Funktion hat einen Fallback, der NIE Information verschluckt — ein
    unbekannter Code oder eine unparsebare Metrik gibt den Rohwert durch,
    niemals eine leere Zeile, niemals ein Crash.

Lokalisierung:
    Deutsch, Komma als Dezimaltrenner (32,2 statt 32.2), Umlaute Pflicht.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional, Protocol


# ---------- Status-Konstanten (Single-Source, siehe Modul-Docstring) ----------

# Status -> Discord-Color
STATUS_COLOR: dict[str, int] = {
    "ok": 0x2ECC71,        # gruen
    "degraded": 0xF39C12,  # gelb-orange
    "critical": 0xE74C3C,  # rot
    "unreachable": 0x7F8C8D,  # grau
}

STATUS_EMOJI: dict[str, str] = {
    "ok": "🟢",
    "degraded": "🟡",
    "critical": "🔴",
    "unreachable": "⚫",
}

# Emoji fuer unbekannte/unerwartete Status-Werte
_UNKNOWN_EMOJI = "⚪"


# ---------- Dringlichkeit ----------

class Urgency(Enum):
    """Dringlichkeitsstufen fuer Alerts/Uebergaenge."""

    NONE = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


_URGENCY_LINES: dict[Urgency, str] = {
    Urgency.NONE: "",
    Urgency.LOW: "→ Dringlichkeit: niedrig · im Auge behalten",
    Urgency.MEDIUM: "→ Dringlichkeit: mittel · demnächst prüfen",
    Urgency.HIGH: "→ Dringlichkeit: hoch · bitte zeitnah prüfen",
    Urgency.CRITICAL: "→ Dringlichkeit: kritisch · sofort eingreifen",
}


def urgency_line(urgency: Urgency) -> str:
    """Dringlichkeit -> deutscher Handlungs-Hinweis.

    ``NONE`` ergibt einen Leerstring (kein Handlungsbedarf). Unbekannte Werte
    fallen sicher auf Leerstring zurueck.
    """
    return _URGENCY_LINES.get(urgency, "")


def format_downtime(seconds: float) -> str:
    """Sekunden -> kurzer deutscher Dauer-Klartext, z.B. '2 Min', '1 Std 5 Min'.

    Zentral hier, damit alle Builder (phase_5e, project_monitor, incident_manager)
    dieselbe Formatierung nutzen statt zu duplizieren. Negative Werte -> '0 Sek'.
    """
    seconds = int(max(0, seconds))
    if seconds < 60:
        return f"{seconds} Sek"
    if seconds < 3600:
        return f"{seconds // 60} Min"
    if seconds < 86400:
        h, rem = divmod(seconds, 3600)
        m = rem // 60
        return f"{h} Std {m} Min" if m else f"{h} Std"
    days, rem = divmod(seconds, 86400)
    h = rem // 3600
    return f"{days} Tg {h} Std" if h else f"{days} Tg"


# ---------- Status-Uebergaenge ----------

@dataclass
class TransitionInfo:
    """Ergebnis von :func:`humanize_transition`."""

    headline: str       # z.B. "ueberlastet (war kurz nicht erreichbar)"
    urgency: Urgency
    emoji: str
    is_recovery: bool


# Status-Rang fuer "wird besser / wird schlimmer"-Heuristik des Defaults.
_STATUS_RANK: dict[str, int] = {
    "ok": 0,
    "degraded": 1,
    "critical": 2,
    "unreachable": 3,
}

# (prev, new) -> (headline, urgency, is_recovery)
_TRANSITIONS: dict[tuple[str, str], tuple[str, Urgency, bool]] = {
    # --- aus ok heraus (Verschlechterung) ---
    ("ok", "degraded"): ("läuft eingeschränkt", Urgency.LOW, False),
    ("ok", "critical"): ("kritisch überlastet", Urgency.HIGH, False),
    ("ok", "unreachable"): ("nicht mehr erreichbar", Urgency.CRITICAL, False),

    # --- aus degraded heraus ---
    ("degraded", "ok"): ("wieder stabil", Urgency.NONE, True),
    ("degraded", "critical"): ("verschlechtert sich (jetzt kritisch)", Urgency.HIGH, False),
    ("degraded", "unreachable"): ("nicht mehr erreichbar", Urgency.CRITICAL, False),

    # --- aus critical heraus ---
    ("critical", "ok"): ("wieder stabil", Urgency.NONE, True),
    ("critical", "degraded"): ("erholt sich (noch nicht stabil)", Urgency.MEDIUM, False),
    ("critical", "unreachable"): ("nicht mehr erreichbar (war kritisch)", Urgency.CRITICAL, False),

    # --- aus unreachable heraus (kommt zurueck) ---
    ("unreachable", "ok"): ("wieder erreichbar und stabil", Urgency.NONE, True),
    ("unreachable", "degraded"): ("wieder erreichbar (noch eingeschränkt)", Urgency.MEDIUM, False),
    ("unreachable", "critical"): ("überlastet (war kurz nicht erreichbar)", Urgency.HIGH, False),
}


def humanize_transition(prev: str, new: str) -> TransitionInfo:
    """Status-Uebergang -> Klartext-Headline + Dringlichkeit.

    ``prev``/``new`` aus {ok, degraded, critical, unreachable}. Bekannte
    Kombinationen werden aus :data:`_TRANSITIONS` aufgeloest; unerwartete
    Kombinationen bekommen einen vernuenftigen Default anhand des Status-Rangs
    (besser/schlechter/gleich), damit nie eine leere Headline entsteht.
    """
    emoji = STATUS_EMOJI.get(new, _UNKNOWN_EMOJI)

    mapped = _TRANSITIONS.get((prev, new))
    if mapped is not None:
        headline, urgency, is_recovery = mapped
        return TransitionInfo(headline=headline, urgency=urgency, emoji=emoji, is_recovery=is_recovery)

    # Kein Uebergang (gleicher Status)
    if prev == new:
        return TransitionInfo(
            headline=f"unverändert ({new})",
            urgency=Urgency.NONE,
            emoji=emoji,
            is_recovery=False,
        )

    # Default-Heuristik anhand des Status-Rangs
    prev_rank = _STATUS_RANK.get(prev)
    new_rank = _STATUS_RANK.get(new)
    if prev_rank is not None and new_rank is not None:
        if new_rank < prev_rank:
            # Verbesserung
            is_recovery = new == "ok"
            return TransitionInfo(
                headline=f"erholt sich ({prev} → {new})",
                urgency=Urgency.NONE if is_recovery else Urgency.MEDIUM,
                emoji=emoji,
                is_recovery=is_recovery,
            )
        # Verschlechterung
        return TransitionInfo(
            headline=f"verschlechtert sich ({prev} → {new})",
            urgency=Urgency.HIGH,
            emoji=emoji,
            is_recovery=False,
        )

    # Voellig unbekannte Status-Werte -> nie leer, nie Crash
    return TransitionInfo(
        headline=f"Statuswechsel: {prev} → {new}",
        urgency=Urgency.MEDIUM,
        emoji=emoji,
        is_recovery=False,
    )


# ---------- Metrik-Parser ----------

def _de_decimal(value: float, digits: int = 1) -> str:
    """Float -> deutscher Dezimalstring (Komma), z.B. 32.23 -> '32,2'."""
    return f"{value:.{digits}f}".replace(".", ",")


_LOAD_RE = re.compile(
    r"Load\s+1min\s*=\s*([0-9]+(?:\.[0-9]+)?)\s+on\s+([0-9]+)\s+CPUs",
    re.IGNORECASE,
)

# Schwellwert (Load/CPU), ab dem von "ueberlastet" gesprochen wird.
_OVERLOAD_FACTOR = 1.5


def parse_load(message: str) -> Optional[str]:
    """``"Load 1min=32.23 on 8 CPUs"`` -> ``"CPU-Last 32,2 auf 8 Kernen — 4× überlastet"``.

    Faktor = load/cpus (gerundet). Ab ~1,5x wird "X× überlastet" angehaengt,
    darunter nur das Verhaeltnis. Regex-Match scheitert -> ``None``
    (Aufrufer faellt dann auf die Rohmessage zurueck).
    """
    if not message:
        return None
    match = _LOAD_RE.search(message)
    if match is None:
        return None
    try:
        load = float(match.group(1))
        cpus = int(match.group(2))
    except (ValueError, TypeError):
        return None
    if cpus <= 0:
        return None

    base = f"CPU-Last {_de_decimal(load)} auf {cpus} Kernen"
    factor = load / cpus
    if factor >= _OVERLOAD_FACTOR:
        return f"{base} — {round(factor)}× überlastet"
    return base


_DISK_RE = re.compile(
    r"Disk\s+usage\s+([0-9]+(?:\.[0-9]+)?)\s*%\s+on\s+(\S+)",
    re.IGNORECASE,
)


def parse_disk(message: str) -> Optional[str]:
    """``"Disk usage 84.8% on /"`` -> ``"Platte zu 84,8 % voll (/)"``.

    Regex-Match scheitert -> ``None`` (Aufrufer faellt auf Rohmessage zurueck).
    """
    if not message:
        return None
    match = _DISK_RE.search(message)
    if match is None:
        return None
    try:
        percent = float(match.group(1))
    except (ValueError, TypeError):
        return None
    mount = match.group(2)
    return f"Platte zu {_de_decimal(percent)} % voll ({mount})"


_MEM_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*%")


def parse_memory(message: str) -> Optional[str]:
    """``"Memory usage 87%"`` -> ``"Arbeitsspeicher zu 87 % belegt"``.

    Nimmt den ersten Prozentwert in der Message. Kein Prozentwert -> ``None``.
    """
    if not message:
        return None
    match = _MEM_RE.search(message)
    if match is None:
        return None
    raw = match.group(1)
    # Ganzzahlige Prozente ohne ",0" ausgeben, sonst Dezimalkomma.
    if "." in raw:
        try:
            pretty = _de_decimal(float(raw))
        except (ValueError, TypeError):
            pretty = raw
    else:
        pretty = raw
    return f"Arbeitsspeicher zu {pretty} % belegt"


def parse_service(message: str) -> Optional[str]:
    """``"github-runner-1 inactive"`` -> ``"Dienst github-runner-1 läuft nicht"``.

    Erstes Token (Service-Name) wird extrahiert; der Rest bleibt erhalten,
    falls vorhanden. Leere Message -> ``None``.
    """
    if not message or not message.strip():
        return None
    parts = message.split()
    service = parts[0]
    return f"Dienst {service} läuft nicht"


# ---------- Alert-Codes ----------

@dataclass(frozen=True)
class AlertSpec:
    """Spezifikation pro bekanntem Alert-Code."""

    label: str
    parser: Optional[Callable[[str], Optional[str]]]


# code -> AlertSpec. Der parser darf None liefern (-> Fallback auf Rohmessage).
ALERT_LABELS: dict[str, AlertSpec] = {
    "LOAD_CRITICAL": AlertSpec("CPU-Last kritisch", parse_load),
    "LOAD_HIGH": AlertSpec("CPU-Last hoch", parse_load),
    "DISK_HIGH": AlertSpec("Plattenplatz knapp", parse_disk),
    "DISK_CRITICAL": AlertSpec("Plattenplatz kritisch", parse_disk),
    "MEM_HIGH": AlertSpec("Arbeitsspeicher knapp", parse_memory),
    "MEM_CRITICAL": AlertSpec("Arbeitsspeicher kritisch", parse_memory),
    "MEMORY_HIGH": AlertSpec("Arbeitsspeicher knapp", parse_memory),
    "MEMORY_CRITICAL": AlertSpec("Arbeitsspeicher kritisch", parse_memory),
    "SERVICE_DOWN": AlertSpec("Dienst ausgefallen", parse_service),
    "SERVICE_FAILED": AlertSpec("Dienst fehlgeschlagen", parse_service),
}


class _AlertLike(Protocol):
    code: str
    component: str
    message: str


def _title_case_code(code: str) -> str:
    """``"WEIRD_NEW_CODE"`` -> ``"Weird New Code"``."""
    return " ".join(part.capitalize() for part in code.replace("-", "_").split("_") if part)


def humanize_alert(alert: _AlertLike) -> str:
    """``HealthAlert`` (oder duck-typed code/component/message) -> eine lesbare Zeile.

    Bekannte Codes (siehe :data:`ALERT_LABELS`) bekommen Klartext + Metrik-Kontext;
    der zugehoerige Parser liefert den Kontext oder ``None`` -> Fallback auf die
    Rohmessage. Unbekannte Codes fallen auf ``"<Title-Case-Code>: <message>"``
    zurueck. Es geht NIE Information verloren und es wird NIE eine leere Zeile
    zurueckgegeben.
    """
    code = str(getattr(alert, "code", "") or "UNKNOWN")
    message = str(getattr(alert, "message", "") or "")

    spec = ALERT_LABELS.get(code)
    if spec is not None:
        context: Optional[str] = None
        if spec.parser is not None:
            try:
                context = spec.parser(message)
            except Exception:  # pragma: no cover - Parser duerfen nie crashen lassen
                context = None
        if context:
            return context
        # Parser scheiterte -> bekanntes Label + Rohmessage (Info bleibt erhalten)
        if message.strip():
            return f"{spec.label}: {message}"
        return spec.label

    # Unbekannter Code -> Title-Case + Rohmessage
    title = _title_case_code(code)
    if message.strip():
        return f"{title}: {message}"
    return title


# ---------- Runbooks ----------

# role|component -> Runbook-Datei (relativ, im Repo unter docs/ops/ erwartet).
RUNBOOKS: dict[str, str] = {
    # Rollen
    "ci-runner": "mayday-ci-runner.md",
    "web-prod": "zerodox-web.md",
    "web-dev": "zerodox-web.md",
    # Komponenten
    "disk": "disk-pressure.md",
    "memory": "memory-pressure.md",
    "load": "high-load.md",
    "database": "database.md",
    "redis": "redis.md",
    "wireguard": "wireguard.md",
    "github_runners": "mayday-ci-runner.md",
}


def runbook_for(role: str, components: list[str]) -> Optional[str]:
    """Liefert den passendsten Runbook-Pfad — Rolle hat Vorrang vor Komponente.

    Unbekannte Rolle UND keine bekannte Komponente -> ``None``.
    """
    rb = RUNBOOKS.get(role)
    if rb is not None:
        return rb
    for component in components or []:
        rb = RUNBOOKS.get(component)
        if rb is not None:
            return rb
    return None
