#!/usr/bin/env python3
"""
ki-cost-watchdog.py — Taeglicher KI-Kosten-Watchdog (stdlib-only).

Aggregiert Token-Verbrauch + geschaetzte USD-Kosten ueber:
  - Claude Code   (~/.claude/projects/**/*.jsonl, plus best-effort keydev)
  - Codex CLI     (~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl)

postet einen taeglichen Rollup als Discord-Embed und alarmiert (roter Embed),
wenn die Tageskosten ein Vielfaches (KICOST_ANOMALY_FACTOR) des 7-Tage-Schnitts
ueberschreiten.

Verhaltens-Eckpunkte (analog memory-watchdog.sh):
  - Webhook aus ~/.config/shadowops-watchdog.env (KICOST_WEBHOOK, sonst
    SHADOWOPS_WATCHDOG_WEBHOOK). Beide leer -> nur stdout, kein Fehler.
  - State-File mit 30-Tage-History (cost+tokens pro Tag).
  - Discord-Send via urllib (kein requests). Webhook-Fehler -> stderr, kein Crash.

ENV-Overrides:
  KICOST_WEBHOOK / SHADOWOPS_WATCHDOG_WEBHOOK  Discord-Webhook-URL
  KICOST_DAY                Tag YYYY-MM-DD (default: heute UTC)
  KICOST_ANOMALY_FACTOR     Faktor fuer relativen Anomalie-Alarm (default 2.5)
  KICOST_ABSOLUTE_ALERT_USD Absolute Kostendecke USD/Tag (default 0 = deaktiviert).
                            Fängt dauerhaft teure Hintergrund-Pfade, die Teil der
                            eigenen Baseline sind und den relativen Alarm nie auslösen.
  KICOST_TOP_PROJECTS       Top-N Claude-Projekte nach Kosten im Embed (default 4)
  STATE_FILE                Pfad zum State-File
  WEBHOOK_CONFIG            Pfad zur Env-Config
  PRICE_CLAUDE_OPUS_IN/OUT      USD pro 1M Token (default 15 / 75)
  PRICE_CLAUDE_SONNET_IN/OUT    USD pro 1M Token (default 3 / 15)
  PRICE_CODEX_IN/OUT            USD pro 1M Token (default 2.5 / 10)
  PRICE_<BUCKET>_CACHE_READ     USD pro 1M Cache-Read-Token (default 0.1x Input)
  PRICE_<BUCKET>_CACHE_WRITE    USD pro 1M Cache-Write-Token (default 1.25x Input)

CACHE-PRICING (#292):
  Anthropic berechnet Cache-Reads real ~0.1x und Cache-Writes ~1.25x des
  Input-Preises. Frueher wurden alle drei Input-Kategorien voll gewertet, was die
  notionalen Kosten ueberschaetzt hat. Die Token-ZAHL bleibt unveraendert (das
  Anomalie-Signal ist davon unberuehrt) — nur die USD-Kosten sind jetzt korrekt.

KRITISCH (Codex-Kumulativ-Falle):
  payload.info.total_token_usage ist KUMULATIV pro Session-Datei (waechst ueber
  die Zeilen). Naives Summieren aller Zeilen wuerde massiv doppelt zaehlen.
  Korrekt: pro Session-Datei nur den HOECHSTEN total_tokens-Eintrag nehmen und
  dessen input/output als Session-Total werten. Tag-Zuordnung ueber den
  timestamp dieses Eintrags (Fallback: Datum aus dem Dateipfad YYYY/MM/DD).
"""

import json
import os
import random
import sys
import time
from datetime import datetime, timezone
from glob import glob
from urllib import request, error

# ─── Konfig ──────────────────────────────────────────────────────────────────
HOME = os.path.expanduser("~")
WEBHOOK_CONFIG = os.environ.get(
    "WEBHOOK_CONFIG", os.path.join(HOME, ".config", "shadowops-watchdog.env")
)
STATE_FILE = os.environ.get(
    "STATE_FILE",
    os.path.join(HOME, "shadowops-bot", "data", "watchdog_state_ki-cost.json"),
)
ANOMALY_FACTOR = float(os.environ.get("KICOST_ANOMALY_FACTOR", "2.5"))
HISTORY_DAYS = 30
AVG_WINDOW = 7
# Absolute Kostendecke (opt-in): fängt einen dauerhaft teuren Pfad, den der rein
# relative Anomalie-Alarm (ANOMALY_FACTOR x Schnitt) NIE sieht — ein seit Tag 1
# laufender Block wird Teil der eigenen Baseline. Default 0 = deaktiviert, weil
# notionale Abo-Kosten stark mit interaktiver Nutzung schwanken (ein fester
# Gesamt-Schwellwert würde intensive Dev-Tage fälschlich alarmen). Der robuste
# Dauer-Fix ist die Pro-Projekt-Sicht unten. Setze KICOST_ABSOLUTE_ALERT_USD=<x>
# knapp über deiner aus dem Rollup abgelesenen Baseline, wenn du einen harten
# Backstop willst.
ABSOLUTE_ALERT_USD = float(os.environ.get("KICOST_ABSOLUTE_ALERT_USD", "0"))
# Top-N teuerste Claude-Projekte im täglichen Rollup: Pro-Projekt-Sicht statt
# Flat-Total macht einen dominanten Hintergrund-Verbraucher sofort sichtbar.
TOP_PROJECTS_N = int(os.environ.get("KICOST_TOP_PROJECTS", "4"))

# Claude-Quellen (keydev best-effort)
CLAUDE_GLOBS = [
    os.path.join(HOME, ".claude", "projects", "**", "*.jsonl"),
    "/home/keydev/.claude/projects/**/*.jsonl",
]
CODEX_BASE = os.path.join(HOME, ".codex", "sessions")


def _price(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


# Cache-Multiplikatoren (Anthropic): Cache-Read ~0.1x, Cache-Write ~1.25x Input.
CACHE_READ_FACTOR = 0.1
CACHE_WRITE_FACTOR = 1.25


def _bucket_prices(prefix, def_in, def_out):
    """Preis-Bucket: Input/Output + abgeleitete Cache-Read/Write-Preise (USD/1M).

    Cache-Preise leiten sich per Default aus dem (ggf. via ENV ueberschriebenen)
    Input-Preis ab, lassen sich aber separat ueber PRICE_<prefix>_CACHE_READ /
    PRICE_<prefix>_CACHE_WRITE setzen.
    """
    p_in = _price(f"PRICE_{prefix}_IN", def_in)
    p_out = _price(f"PRICE_{prefix}_OUT", def_out)
    return {
        "in": p_in,
        "out": p_out,
        "cache_write": _price(f"PRICE_{prefix}_CACHE_WRITE", round(p_in * CACHE_WRITE_FACTOR, 6)),
        "cache_read": _price(f"PRICE_{prefix}_CACHE_READ", round(p_in * CACHE_READ_FACTOR, 6)),
    }


PRICES = {
    "claude_opus": _bucket_prices("CLAUDE_OPUS", 15.0, 75.0),
    "claude_sonnet": _bucket_prices("CLAUDE_SONNET", 3.0, 15.0),
    "codex": _bucket_prices("CODEX", 2.5, 10.0),
}


def compute_cost(bucket, input_tokens, cache_write_tokens, cache_read_tokens, output_tokens):
    """USD-Kosten einer Token-Aufteilung in einem Preis-Bucket.

    Input/Cache-Write/Cache-Read/Output werden je mit ihrem eigenen Preis
    gewertet (Cache-Read ~0.1x, Cache-Write ~1.25x Input). Die Token-ZAHLEN beim
    Aufrufer bleiben unveraendert — nur die Kosten spiegeln den Cache-Rabatt.
    """
    p = PRICES[bucket]
    return (
        int(input_tokens) * p["in"]
        + int(cache_write_tokens) * p["cache_write"]
        + int(cache_read_tokens) * p["cache_read"]
        + int(output_tokens) * p["out"]
    ) / 1_000_000


def model_bucket(model: str) -> str:
    """Modell-Routing -> Preis-Bucket."""
    m = (model or "").lower()
    if "opus" in m:
        return "claude_opus"
    if "sonnet" in m or "haiku" in m or "claude" in m:
        return "claude_sonnet"
    return "codex"


# ─── Webhook laden ───────────────────────────────────────────────────────────
def load_webhook() -> str:
    """Liest KICOST_WEBHOOK (fallback SHADOWOPS_WATCHDOG_WEBHOOK).

    Reihenfolge: echte Prozess-ENV gewinnt, sonst aus der Env-Config-Datei.
    Leere Werte -> "" (stdout-only, kein Fehler).
    """
    file_vals = {}
    if os.path.isfile(WEBHOOK_CONFIG):
        try:
            with open(WEBHOOK_CONFIG, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, val = line.split("=", 1)
                    key = key.replace("export ", "").strip()
                    val = val.strip().strip('"').strip("'")
                    file_vals[key] = val
        except OSError as exc:
            print(f"[ki-cost-watchdog] WARN: Config nicht lesbar: {exc}", file=sys.stderr)

    for key in ("KICOST_WEBHOOK", "SHADOWOPS_WATCHDOG_WEBHOOK"):
        val = os.environ.get(key) or file_vals.get(key)
        if val:
            return val
    return ""


# ─── Tag-Helfer ──────────────────────────────────────────────────────────────
def target_day() -> str:
    day = os.environ.get("KICOST_DAY")
    if day:
        return day.strip()
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def ts_to_day(ts) -> str:
    """ISO-Timestamp -> YYYY-MM-DD (nur Praefix, robust gegen Z/Offset)."""
    if not isinstance(ts, str) or len(ts) < 10:
        return ""
    return ts[:10]


# ─── Claude-Aggregation ──────────────────────────────────────────────────────
def _project_from_path(path):
    """~/.claude/projects/<projekt>/<session>.jsonl -> <projekt> (Pro-Projekt-Sicht)."""
    parts = path.replace("\\", "/").split("/")
    try:
        idx = parts.index("projects")
    except ValueError:
        return "?"
    return parts[idx + 1] if idx + 1 < len(parts) else "?"


def collect_claude(day: str):
    """Summiert Claude-Token+Kosten fuer den Tag.

    KRITISCH (Claude-Dedup-Falle): Claude Code schreibt dieselbe Assistant-
    Message oft in MEHRERE JSONL-Dateien (Session-Resumes, Sidechains). An einem
    typischen Tag sind >50% der usage-Zeilen Duplikate mit identischer
    message.id. Naives Summieren aller Zeilen verdoppelt die Kosten (~2x
    Ueberzaehlung verifiziert). Daher Dedup ueber message.id (Fallback
    requestId). Eine eindeutige usage-Zeile = ein API-Call.

    Returns: dict(tokens_in, tokens_out, cost, calls, keydev_readable)
    """
    agg = {"tokens_in": 0, "tokens_out": 0, "cost": 0.0, "calls": 0, "by_project": {}}
    keydev_readable = False
    seen_ids = set()

    for pattern in CLAUDE_GLOBS:
        is_keydev = "/keydev/" in pattern
        try:
            files = glob(pattern, recursive=True)
        except OSError:
            files = []
        for path in files:
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    if is_keydev:
                        keydev_readable = True
                    for line in fh:
                        if '"usage"' not in line:
                            continue
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                        except (ValueError, TypeError):
                            continue
                        msg = obj.get("message") if isinstance(obj.get("message"), dict) else None
                        usage = (msg or {}).get("usage") or obj.get("usage")
                        if not isinstance(usage, dict):
                            continue
                        ts = obj.get("timestamp") or (msg or {}).get("timestamp")
                        if ts_to_day(ts) != day:
                            continue

                        # Dedup ueber message.id (Fallback requestId). Keine ID ->
                        # nicht deduplizierbar, also zaehlen (selten).
                        mid = (msg or {}).get("id") or obj.get("requestId")
                        if mid is not None:
                            # keydev-IDs koennten mit cmdshadow kollidieren ->
                            # mit Quelle praefixen
                            key = (pattern, mid) if is_keydev else mid
                            if key in seen_ids:
                                continue
                            seen_ids.add(key)

                        model = (msg or {}).get("model") or obj.get("model") or ""

                        reg_in = int(usage.get("input_tokens") or 0)
                        cache_write = int(usage.get("cache_creation_input_tokens") or 0)
                        cache_read = int(usage.get("cache_read_input_tokens") or 0)
                        # Token-ZAHL = volle Input-Summe (Anomalie-Signal bleibt korrekt)
                        in_tok = reg_in + cache_write + cache_read
                        out_tok = int(usage.get("output_tokens") or 0)
                        bucket = model_bucket(model)
                        # Kosten: Cache-Read 0.1x, Cache-Write 1.25x Input (statt alles voll)
                        cost = compute_cost(bucket, reg_in, cache_write, cache_read, out_tok)

                        agg["tokens_in"] += in_tok
                        agg["tokens_out"] += out_tok
                        agg["cost"] += cost
                        agg["calls"] += 1

                        proj = _project_from_path(path)
                        bp = agg["by_project"].setdefault(
                            proj, {"cost": 0.0, "tokens": 0, "calls": 0}
                        )
                        bp["cost"] += cost
                        bp["tokens"] += in_tok + out_tok
                        bp["calls"] += 1
            except (OSError, PermissionError):
                # keydev oft kein Lesezugriff -> best-effort ueberspringen
                continue

    agg["keydev_readable"] = keydev_readable
    return agg


# ─── Codex-Aggregation (Kumulativ-Falle!) ────────────────────────────────────
def collect_codex(day: str):
    """Summiert Codex-Token+Kosten fuer den Tag.

    KRITISCH: total_token_usage ist kumulativ pro Session-Datei. Daher pro Datei
    nur den Eintrag mit dem hoechsten total_tokens nehmen (= Session-Endstand)
    und dessen input/output als Session-Total werten. reasoning_output_tokens
    zaehlt fuer Kostenzwecke als Teil von output (Codex billt reasoning als output).

    Tag-Zuordnung: timestamp des Max-Eintrags, Fallback Dateipfad YYYY/MM/DD.
    """
    agg = {"tokens_in": 0, "tokens_out": 0, "cost": 0.0, "sessions": 0}
    # Codex-Cache (cached_input_tokens) bleibt vorerst zum Input-Preis gewertet
    # (Issue #292 betrifft die Anthropic-Cache-Rabatte; Codex-Rabatt = Folge-Thema).
    _cp = PRICES["codex"]
    p_in, p_out = _cp["in"], _cp["out"]

    try:
        files = glob(os.path.join(CODEX_BASE, "*", "*", "*", "rollout-*.jsonl"))
    except OSError:
        files = []

    # Fallback-Tag aus dem Dateipfad: .../sessions/YYYY/MM/DD/rollout-*.jsonl
    def path_day(path):
        parts = path.split(os.sep)
        try:
            i = parts.index("sessions")
            y, m, d = parts[i + 1], parts[i + 2], parts[i + 3]
            return f"{y}-{m}-{d}"
        except (ValueError, IndexError):
            return ""

    for path in files:
        best = None  # (total_tokens, in_tok, out_tok, day_of_entry)
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if "total_token_usage" not in line:
                        continue
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except (ValueError, TypeError):
                        continue
                    payload = obj.get("payload")
                    if not isinstance(payload, dict):
                        continue
                    info = payload.get("info")
                    ttu = None
                    if isinstance(info, dict):
                        ttu = info.get("total_token_usage")
                    if not isinstance(ttu, dict):
                        # selten direkt unter payload
                        ttu = payload.get("total_token_usage")
                    if not isinstance(ttu, dict):
                        continue

                    total = int(ttu.get("total_tokens") or 0)
                    in_tok = (
                        int(ttu.get("input_tokens") or 0)
                        + int(ttu.get("cached_input_tokens") or 0)
                    )
                    out_tok = (
                        int(ttu.get("output_tokens") or 0)
                        + int(ttu.get("reasoning_output_tokens") or 0)
                    )
                    entry_day = ts_to_day(obj.get("timestamp")) or path_day(path)

                    if best is None or total > best[0]:
                        best = (total, in_tok, out_tok, entry_day)
        except (OSError, PermissionError):
            continue

        if best is None:
            continue
        _total, in_tok, out_tok, entry_day = best
        if not entry_day:
            entry_day = path_day(path)
        if entry_day != day:
            continue

        cost = in_tok / 1_000_000 * p_in + out_tok / 1_000_000 * p_out
        agg["tokens_in"] += in_tok
        agg["tokens_out"] += out_tok
        agg["cost"] += cost
        agg["sessions"] += 1

    return agg


# ─── State / History ─────────────────────────────────────────────────────────
def load_state():
    if os.path.isfile(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                return data
        except (OSError, ValueError):
            pass
    return {"history": []}


def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)
    os.replace(tmp, STATE_FILE)


def update_history(state, day, cost, tokens):
    """Trage Tag in History ein (idempotent: gleicher Tag wird ersetzt).

    Liefert 7-Tage-Schnitt der VORHERIGEN Tage (ohne heute), nur befuellte Tage.
    """
    history = [h for h in state.get("history", []) if isinstance(h, dict)]
    # 7-Tage-Schnitt aus den letzten bis zu 7 Tagen VOR dem heutigen Eintrag
    prior = [h for h in history if h.get("day") != day]
    prior_sorted = sorted(prior, key=lambda h: h.get("day", ""))
    window = prior_sorted[-AVG_WINDOW:]
    avg = sum(float(h.get("cost") or 0.0) for h in window) / len(window) if window else 0.0

    # Heutigen Tag updaten/anhaengen
    history = [h for h in history if h.get("day") != day]
    history.append({"day": day, "cost": round(cost, 4), "tokens": int(tokens)})
    history = sorted(history, key=lambda h: h.get("day", ""))[-HISTORY_DAYS:]
    state["history"] = history
    return avg


# ─── Discord-Send (urllib) ───────────────────────────────────────────────────
def fmt_tokens(n) -> str:
    n = int(n)
    if n >= 1_000_000:
        return f"{n/1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}k"
    return str(n)


def _send_once(webhook, data):
    """Ein POST-Versuch. Returns (code, retry_after_seconds_or_None).

    code=None signalisiert einen Netzwerk-/URL-Fehler (kein HTTP-Status).
    retry_after ist nur bei HTTP 429 gesetzt.
    """
    req = request.Request(
        webhook,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "ki-cost-watchdog/1.0"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=10) as resp:
            return resp.getcode(), None
    except error.HTTPError as exc:
        retry_after = None
        if exc.code == 429:
            try:
                retry_after = float((exc.headers.get("Retry-After") or "1").strip())
            except (TypeError, ValueError, AttributeError):
                retry_after = 1.0
        return exc.code, retry_after
    except (error.URLError, OSError, ValueError) as exc:
        print(f"[ki-cost-watchdog] WARN: Discord-Send fehlgeschlagen: {exc}", file=sys.stderr)
        return None, None


def send_discord(webhook, payload) -> bool:
    """Sendet den Embed mit 429-Resilienz (#293) — Python-Pendant zur geteilten
    Bash-Lib scripts/lib/discord-send.sh.

    - Kleiner Jitter (0..KICOST_MAX_JITTER_MS, default 400ms) VOR dem Send entzerrt
      gleichzeitige Multi-Service-Alarme ueber denselben Webhook.
    - Bei HTTP 429: respektiert Retry-After (gedeckelt auf DISCORD_RETRY_CAP=10s)
      und versucht GENAU 1 Retry. Andere Fehler: kein Retry.
    """
    data = json.dumps(payload).encode("utf-8")

    max_jitter_ms = int(os.environ.get("KICOST_MAX_JITTER_MS", "400") or 0)
    if max_jitter_ms > 0:
        time.sleep(random.uniform(0, max_jitter_ms / 1000.0))

    retry_cap = float(os.environ.get("DISCORD_RETRY_CAP", "10") or 10)
    for attempt in (1, 2):
        code, retry_after = _send_once(webhook, data)
        if code in (200, 204):
            print(f"[ki-cost-watchdog] Discord-Send OK (HTTP {code})")
            return True
        if attempt == 1 and code == 429:
            wait = min(retry_after if retry_after is not None else 1.0, retry_cap)
            print(f"[ki-cost-watchdog] HTTP 429 — warte {wait:g}s, 1 Retry", file=sys.stderr)
            time.sleep(wait)
            continue
        if code is not None:
            print(f"[ki-cost-watchdog] WARN: Discord HTTP {code}", file=sys.stderr)
        return False
    return False


def build_payload(day, claude, codex, total_cost, total_tokens, avg, anomaly,
                  top_projects=None, anomaly_absolute=False):
    color = 15158332 if anomaly else 3447003  # rot vs. blau
    title = "🚨 KI-Kosten-Anomalie!" if anomaly else "🤖 KI-Kosten Tages-Rollup"
    if anomaly_absolute:
        desc = (
            f"Tageskosten **${total_cost:.2f}** überschreiten die absolute Kostendecke "
            f"(${ABSOLUTE_ALERT_USD:.0f}). Prüfe die Top-Projekte unten — ein dauerhaft "
            f"teurer Hintergrund-Pfad, den der relative Schnitt nicht sieht?"
        )
    elif anomaly:
        desc = (
            f"Tageskosten **${total_cost:.2f}** liegen ueber dem "
            f"{ANOMALY_FACTOR:g}x 7-Tage-Schnitt (${avg:.2f})."
        )
    else:
        desc = f"Token- und Kostenuebersicht fuer **{day}** (UTC)."

    fields = [
        {
            "name": "Claude Code",
            "value": (
                f"{fmt_tokens(claude['tokens_in'] + claude['tokens_out'])} tok "
                f"· ${claude['cost']:.2f}\n"
                f"({claude['calls']} Calls)"
            ),
            "inline": True,
        },
        {
            "name": "Codex",
            "value": (
                f"{fmt_tokens(codex['tokens_in'] + codex['tokens_out'])} tok "
                f"· ${codex['cost']:.2f}\n"
                f"({codex['sessions']} Sessions)"
            ),
            "inline": True,
        },
        {
            "name": "Gesamt",
            "value": f"{fmt_tokens(total_tokens)} tok · **${total_cost:.2f}**",
            "inline": True,
        },
        {
            "name": "7-Tage-Schnitt",
            "value": (f"${avg:.2f}/Tag" if avg > 0 else "n/a (noch keine History)"),
            "inline": False,
        },
    ]

    if top_projects:
        proj_lines = "\n".join(
            f"`{name}` — ${d['cost']:.2f} ({d['calls']} Calls)"
            for name, d in top_projects
        )
        fields.append({
            "name": f"Top Claude-Projekte (Kosten)",
            "value": proj_lines,
            "inline": False,
        })

    return {
        "username": "ShadowOps KI-Kosten Watchdog",
        "embeds": [
            {
                "title": title,
                "description": desc,
                "color": color,
                "fields": fields,
                "footer": {"text": "ki-cost-watchdog auf VPS (10.8.0.1)"},
                "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        ],
    }


# ─── Main ────────────────────────────────────────────────────────────────────
def main() -> int:
    day = target_day()
    webhook = load_webhook()

    claude = collect_claude(day)
    codex = collect_codex(day)

    total_cost = claude["cost"] + codex["cost"]
    total_tokens = (
        claude["tokens_in"] + claude["tokens_out"]
        + codex["tokens_in"] + codex["tokens_out"]
    )

    state = load_state()
    avg = update_history(state, day, total_cost, total_tokens)

    anomaly_relative = avg > 0 and total_cost > ANOMALY_FACTOR * avg
    anomaly_absolute = ABSOLUTE_ALERT_USD > 0 and total_cost > ABSOLUTE_ALERT_USD
    anomaly = anomaly_relative or anomaly_absolute
    # Top-Projekte nach Kosten — macht einen dominanten Hintergrund-Verbraucher
    # (z.B. einen server-seitigen KI-Dienst) im täglichen Rollup sofort sichtbar.
    top_projects = sorted(
        claude.get("by_project", {}).items(),
        key=lambda kv: kv[1]["cost"],
        reverse=True,
    )[:TOP_PROJECTS_N]

    # stdout-Rollup (immer, auch ohne Webhook) — Verifikations-relevant
    print(
        f"[ki-cost-watchdog] day={day} "
        f"claude_tok={claude['tokens_in'] + claude['tokens_out']} "
        f"claude_usd={claude['cost']:.2f} "
        f"codex_tok={codex['tokens_in'] + codex['tokens_out']} "
        f"codex_usd={codex['cost']:.2f} "
        f"total_usd={total_cost:.2f} "
        f"avg7={avg:.2f} "
        f"anomaly={anomaly} "
        f"keydev_readable={claude['keydev_readable']}"
    )
    if top_projects:
        proj_str = ", ".join(f"{name}=${d['cost']:.2f}" for name, d in top_projects)
        print(f"[ki-cost-watchdog] top_projects: {proj_str}")

    try:
        save_state(state)
    except OSError as exc:
        print(f"[ki-cost-watchdog] WARN: State nicht speicherbar: {exc}", file=sys.stderr)

    if not webhook:
        print("[ki-cost-watchdog] Kein Webhook konfiguriert — nur stdout.")
        return 0

    payload = build_payload(
        day, claude, codex, total_cost, total_tokens, avg, anomaly, top_projects, anomaly_absolute
    )
    send_discord(webhook, payload)  # Fehler werden geloggt, kein Crash
    return 0


if __name__ == "__main__":
    sys.exit(main())
