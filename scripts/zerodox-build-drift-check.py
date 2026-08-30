#!/usr/bin/env python3
"""
zerodox-build-drift-check.py — buildSha-Drift-Backstop fuer ZERODOX (stdlib-only).

Hintergrund (ZERODOX-Issue #1720): bei schnell aufeinanderfolgenden main-Pushes
kann der Bot-Webhook-Trigger einen Deploy verpassen (Race im Deploy-Lock) oder
der Working-Tree-Guard bricht einen Deploy ab, ohne dass spaeter automatisch
nachgeholt wird. Ergebnis: `origin/main` laeuft von der laufenden Produktion
weg, ohne dass irgendjemand das merkt. Dieses Skript ist der Backstop dafuer —
es vergleicht periodisch den live `buildSha` (aus `/api/health`) gegen den
`origin/main`-HEAD des lokalen ZERODOX-Repos.

Wird als `type: script`-Check im deklarativen Check-Engine (project_monitor.py)
eingehaengt (siehe config/config.example.yaml, Sektion `projects.zerodox.monitor.checks`).
Exit 0 = OK, Exit != 0 = FAIL (Message = stderr, von check_runner._run_script
auf 200 Zeichen gekappt). Die Flake-Filter-Toleranz (`flake_polls`) UND die
Alert-Infrastruktur (Discord, Anti-Spam-Cooldown) kommen komplett aus dem
bestehenden Check-Engine — dieses Skript enthaelt bewusst KEIN eigenes
State-File, keinen eigenen Discord-Call, keinen eigenen Timer.

WICHTIGER DESIGN-PUNKT (PFLICHT-Hinweis aus Issue-Kommentar 2026-07-08):
Nicht jeder buildSha-Unterschied ist ein Fehler. `ZERODOX/scripts/deploy.sh`
hat seit Issue #1262 einen dokumentierten Docs-only-Graceful-Skip: ein Commit,
der AUSSCHLIESSLICH nicht-deploy-relevante Pfade aendert (Top-Level *.md,
docs/**, .claude/**), wird von deploy.sh bewusst NICHT deployt (Runtime bleibt
unveraendert) — daher bleibt buildSha dort absichtlich hinter origin/main
zurueck. Dieses Skript uebernimmt exakt dieselbe Allowlist-Regex wie
deploy.sh (dort: `grep -vE '^(docs/|\\.claude/|[^/]+\\.md$)'`), damit beide
Stellen konsistent bewerten, was "deploy-relevant" heisst. Bei Aenderung der
Allowlist in deploy.sh MUSS diese Kopie synchron gehalten werden (siehe
NON_DEPLOY_RELEVANT_RE unten).

Fail-Open vs. Fail-Safe (bewusst unterschiedlich, je nach Fehlerquelle):
  - `/api/health` nicht erreichbar / buildSha fehlt / "unknown"
    -> Fail-OPEN (exit 0). Erreichbarkeit von zerodox.de wird bereits von
       `zerodox-watchdog` ueberwacht (siehe infrastructure.md Watchdog-Tabelle)
       — ein zweiter Alarm fuer denselben Ausfall waere reines Rauschen.
  - `git fetch`/`git rev-parse`/`git diff` im lokalen Repo schlaegt fehl
    -> Fail-SAFE (exit 1, FAIL). Das ist NICHT redundant abgedeckt und
       entspricht genau der Fail-Safe-Philosophie, die deploy.sh selbst fuer
       den analogen Fall dokumentiert ("im Zweifel NICHT als docs-only werten").

ENV-Overrides:
  ZERODOX_HEALTH_URL          Health-Endpoint (default https://zerodox.de/api/health)
  ZERODOX_REPO_PATH           Lokaler Repo-Pfad (default /home/cmdshadow/ZERODOX)
  ZERODOX_HEALTH_TIMEOUT_SEC  HTTP-Timeout Sekunden (default 10)
  ZERODOX_GIT_TIMEOUT_SEC     Timeout pro Git-Kommando Sekunden (default 15)
"""

import json
import os
import re
import subprocess
import sys
from urllib import error, request

# ─── Konfig ──────────────────────────────────────────────────────────────────
HEALTH_URL = os.environ.get("ZERODOX_HEALTH_URL", "https://zerodox.de/api/health")
REPO_PATH = os.environ.get("ZERODOX_REPO_PATH", "/home/cmdshadow/ZERODOX")
HEALTH_TIMEOUT_SEC = float(os.environ.get("ZERODOX_HEALTH_TIMEOUT_SEC", "10"))
GIT_TIMEOUT_SEC = float(os.environ.get("ZERODOX_GIT_TIMEOUT_SEC", "15"))

# Identisch zu ZERODOX/scripts/deploy.sh (Issue #1262, "DEPLOY_RELEVANT="-Zeile):
# nicht-deploy-relevant = Top-Level *.md, docs/**, .claude/**. Alles andere
# (insbesondere web/**, auch web/**/*.md wie Blog-Posts) gilt als deploy-relevant.
NON_DEPLOY_RELEVANT_RE = re.compile(r"^(docs/|\.claude/|[^/]+\.md$)")


def fetch_live_build_sha(url: str, timeout: float) -> "str | None":
    """Holt buildSha aus /api/health. None bei jedem Fehler (fail-open-Fall)."""
    try:
        with request.urlopen(url, timeout=timeout) as resp:
            if resp.status != 200:
                print(f"[drift-check] /api/health HTTP {resp.status}", file=sys.stderr)
                return None
            body = json.loads(resp.read().decode("utf-8", errors="replace"))
    except (error.URLError, error.HTTPError, TimeoutError, json.JSONDecodeError, OSError) as e:
        print(f"[drift-check] /api/health nicht erreichbar/ungueltig: {e}", file=sys.stderr)
        return None

    sha = body.get("buildSha")
    if not sha or sha == "unknown":
        print(f"[drift-check] buildSha fehlt oder 'unknown' (Alt-Image?): {sha!r}", file=sys.stderr)
        return None
    return sha


def run_git(args: list, timeout: float) -> "tuple[bool, str]":
    """Fuehrt `git -C REPO_PATH <args>` aus. Rueckgabe (ok, stdout-oder-stderr)."""
    try:
        proc = subprocess.run(
            ["git", "-C", REPO_PATH, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return False, str(e)
    if proc.returncode != 0:
        return False, proc.stderr.strip()
    return True, proc.stdout.strip()


def deploy_relevant_paths(changed_paths: list) -> list:
    """Filtert die Docs-only-Allowlist raus (Analog zu deploy.sh grep -vE)."""
    return [p for p in changed_paths if p and not NON_DEPLOY_RELEVANT_RE.match(p)]


def main() -> int:
    live_sha = fetch_live_build_sha(HEALTH_URL, HEALTH_TIMEOUT_SEC)
    if not live_sha:
        # Fail-open: Erreichbarkeit wird schon von zerodox-watchdog abgedeckt.
        print("[drift-check] OK (fail-open): buildSha nicht bestimmbar, kein Alarm hier.")
        return 0

    fetch_ok, fetch_msg = run_git(["fetch", "origin", "main"], GIT_TIMEOUT_SEC)
    if not fetch_ok:
        print(f"[drift-check] Warnung: git fetch origin main fehlgeschlagen: {fetch_msg}", file=sys.stderr)
        # Weiter versuchen mit dem, was lokal an origin/main bekannt ist —
        # falls das auch fehlschlaegt, greift der Fail-Safe-Zweig unten.

    rev_ok, origin_sha = run_git(["rev-parse", "origin/main"], GIT_TIMEOUT_SEC)
    if not rev_ok:
        # Fail-safe: lokales Repo nicht auswertbar -> nicht stillschweigend OK.
        print(
            f"[drift-check] FAIL: origin/main HEAD nicht bestimmbar "
            f"(fetch_ok={fetch_ok}): {origin_sha}",
            file=sys.stderr,
        )
        return 1

    if live_sha == origin_sha:
        print(f"[drift-check] OK: buildSha aktuell ({live_sha[:12]})")
        return 0

    diff_ok, diff_out = run_git(["diff", "--name-only", live_sha, origin_sha], GIT_TIMEOUT_SEC)
    if not diff_ok:
        # Fail-safe, analog deploy.sh-Kommentar: nach force-push/rebase evtl.
        # live_sha nicht mehr im lokalen Repo -> "im Zweifel NICHT als
        # docs-only werten", sondern alarmieren.
        print(
            f"[drift-check] FAIL: git diff {live_sha[:12]}..{origin_sha[:12]} "
            f"fehlgeschlagen (evtl. force-push/rebase): {diff_out}",
            file=sys.stderr,
        )
        return 1

    changed_paths = [p for p in diff_out.splitlines() if p]
    relevant = deploy_relevant_paths(changed_paths)
    if not relevant:
        print(
            f"[drift-check] OK: buildSha {live_sha[:12]} != origin/main {origin_sha[:12]}, "
            f"aber nur nicht-deploy-relevante Pfade geaendert ({len(changed_paths)} Datei(en), "
            f"docs-only wie deploy.sh #1262)."
        )
        return 0

    preview = ", ".join(relevant[:5])
    more = f" (+{len(relevant) - 5} weitere)" if len(relevant) > 5 else ""
    print(
        f"[drift-check] FAIL: buildSha-Drift live={live_sha[:12]} "
        f"origin/main={origin_sha[:12]}, {len(relevant)} deploy-relevante Datei(en): "
        f"{preview}{more}",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
