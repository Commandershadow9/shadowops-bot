#!/usr/bin/env python3
"""
zerodox-host-tree-drift-check.py — Host-Baum-Drift für ZERODOX (stdlib-only).

Hintergrund (ZERODOX-Issue #2801): Die per Cron laufenden Host-Skripte werden
aus `/home/cmdshadow/ZERODOX` ausgeführt — einem Arbeitsbaum, den **nichts
automatisch aktualisiert**. Für die Container gibt es einen gepflegten Weg
(der Deploy-Bot hält `~/ZERODOX-deploy` per fetch + reset --hard aktuell,
#2344); für die Host-Skripte gibt es keinen.

Am 29.08.2026 wurde `scripts/backup-files.sh` erweitert, damit 331 Vollmacht-PDFs
gesichert werden. PR #2800 war grün, gemergt, das Issue geschlossen — und die
stündliche Sicherung lief weiter mit der alten Fassung.

Dieses Skript ist der Wächter dafür. Es ist die zweite Hälfte von
`zerodox-build-drift-check.py`:

    build-drift      laufender Container  vs. origin/main   (buildSha)
    host-tree-drift  Dateien auf Platte   vs. origin/main   (dieses Skript)

Kein Container-Check kann den Host-Fall sehen, und umgekehrt.

Eingehängt als `type: script`-Check in der Check-Engine (project_monitor.py,
config.yaml). Exit 0 = OK, Exit != 0 = FAIL (Message = stderr). Persistenz-
Filter, Discord-Alarm und Anti-Spam-Cooldown kommen aus der Engine — dieses
Skript hat bewusst KEIN eigenes State-File, keinen eigenen Discord-Call und
keinen eigenen Timer.

WARUM NUR EIN TEIL DER ABWEICHUNGEN ZÄHLT:
Ein Arbeitsbaum liegt fast immer irgendwo zurück, das ist sein Zweck. Ein
Befund ist er erst dort, wo der **Host** die Datei tatsächlich liest und
ausführt. Alles andere wäre Rauschen, und ein Wächter, der dauernd meldet,
wird weggeklickt wie ein kaputter. Scharf sind daher genau drei Klassen —
siehe `scharfe_pfade`.
"""

import os
import subprocess
import sys

# ─── Konfig ──────────────────────────────────────────────────────────────────
REPO_PATH = os.environ.get("ZERODOX_REPO_PATH", "/home/cmdshadow/ZERODOX")
REMOTE_REF = os.environ.get("ZERODOX_REMOTE_REF", "origin/main")
GIT_TIMEOUT_SEC = float(os.environ.get("ZERODOX_GIT_TIMEOUT_SEC", "15"))

# Die Check-Engine kappt stderr auf 200 Zeichen. Eine Meldung, die alle Treffer
# aufzählt, verliert damit ausgerechnet den Hinweis am Ende — also von vornherein
# nur die ersten paar benennen und den Rest zählen.
MAX_GENANNTE_DATEIEN = 2

# Harte Obergrenze, gleich der Kappung in check_runner._run_script. Sie hier zu
# kennen heisst: WIR entscheiden, was verloren geht, nicht der Zufall der Länge.
MELDUNG_MAX_ZEICHEN = 200


# ─── Was der Host tatsächlich ausführt ───────────────────────────────────────
#
# ⚠️ Bewusst startswith/endswith statt fnmatch: fnmatch kennt kein `**`, dort
# steht ein gewöhnliches `*`, das auch `/` überquert. Eine Musterliste sähe
# hier kürzer aus und träfe still die falschen Dateien.

# Skripte unter scripts/ laufen aus diesem Baum, sobald ein Cron sie ruft.
# Eine .md-Datei daneben tut das nicht — die Endung entscheidet, nicht der Ordner.
AUSFUEHRBARE_ENDUNGEN = (".sh", ".py", ".mjs", ".js", ".ts")

# Hooks werden von jeder interaktiven Session aus DIESEM Baum gestartet.
HOOK_PRAEFIX = ".claude/hooks/"

# Registriert, welche Hooks überhaupt laufen. Weicht sie ab, ist ein Hook
# entweder gar nicht angemeldet oder zeigt auf eine Datei, die es hier nicht
# gibt — beides lautlos (die Hook-Zeilen enden auf `|| true`).
HOOK_REGISTRIERUNG = ".claude/settings.json"


def scharfe_pfade(geaendert):
    """Filtert aus geänderten Pfaden die heraus, die der Host ausführt.

    Alles andere ist für diesen Wächter kein Befund: `web/src/**` läuft im
    Container (dafür ist build-drift zuständig), `docs/**` und `*.md` laufen
    gar nicht, und `.claude/rules/**` ist Prompt-Text ohne Betriebsfolge.
    """
    return [p for p in geaendert if _ist_scharf(p)]


def _ist_scharf(pfad):
    if pfad == HOOK_REGISTRIERUNG:
        return True
    if pfad.startswith(HOOK_PRAEFIX):
        return True
    return pfad.startswith("scripts/") and pfad.endswith(AUSFUEHRBARE_ENDUNGEN)


# ─── Git-Schicht ─────────────────────────────────────────────────────────────

def run_git(args, timeout):
    """(ok, ausgabe) — als Modul-Global gehalten, damit Tests es ersetzen können."""
    try:
        fertig = subprocess.run(
            ["git", "-C", REPO_PATH] + args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,  # der Rueckgabewert wird ausgewertet, nicht geworfen
        )
    # Bewusst nur diese zwei: TimeoutExpired (haengendes fetch) und OSError
    # (git fehlt, REPO_PATH weg). Alles andere darf durchschlagen — ein
    # unerwarteter Absturz endet mit Exit != 0 und ist damit fail-safe.
    except (subprocess.SubprocessError, OSError) as fehler:
        return False, str(fehler)
    ausgabe = (fertig.stdout or "") + (fertig.stderr or "")
    return fertig.returncode == 0, ausgabe.strip()


def pruefe():
    """(ok, meldung) — ok=False heisst: eine ausgeführte Datei weicht ab.

    Fail-SAFE bei Git-Fehlern, exakt wie der Zwilling: Ein kaputtes Repo ist
    NICHT anderweitig überwacht, und ein Wächter, der bei eigener Blindheit
    "alles gut" meldet, ist schlimmer als keiner.
    """
    ok, aus = run_git(["fetch", "origin", "--quiet"], GIT_TIMEOUT_SEC)
    if not ok:
        return False, f"git fetch fehlgeschlagen ({REPO_PATH}): {aus[:120]}"

    # ⚠️ Gegen den ARBEITSBAUM vergleichen, nicht gegen HEAD: Der Cron liest die
    # Datei auf der Platte, nicht den Commit. Das erfasst in einem Aufruf sowohl
    # "Baum liegt zurück" als auch "lokal verändert" als auch "Datei fehlt ganz"
    # — der Wachhund-Fall vom 30.08.2026 war der dritte.
    ok, aus = run_git(
        ["diff", "--name-only", REMOTE_REF, "--", "scripts/", ".claude/"],
        GIT_TIMEOUT_SEC,
    )
    if not ok:
        return False, f"git diff gegen {REMOTE_REF} fehlgeschlagen: {aus[:120]}"

    geaendert = [zeile.strip() for zeile in aus.splitlines() if zeile.strip()]
    scharf = scharfe_pfade(geaendert)
    if not scharf:
        return True, ""

    # Nur als Zusatz zu einem echten Befund — allein gemeldet wäre es Dauerlärm.
    ok_branch, branch = run_git(["rev-parse", "--abbrev-ref", "HEAD"], GIT_TIMEOUT_SEC)
    detached = " [detached HEAD]" if ok_branch and branch.strip() == "HEAD" else ""

    genannt = ", ".join(scharf[:MAX_GENANNTE_DATEIEN])
    weitere = len(scharf) - MAX_GENANNTE_DATEIEN
    if weitere > 0:
        genannt += f" (+{weitere} weitere)"

    # ⚠️ Reihenfolge ist Absicht: Anzahl und Diagnose stehen VOR der Dateiliste,
    # damit die Kappung Beispiele frisst und nicht den Befund. Der Echtlauf am
    # 30.08.2026 ergab 278 Zeichen — die alte Fassung hätte den detached-Hinweis
    # ausgerechnet dann verloren, wenn viele Dateien betroffen sind.
    meldung = (
        f"{len(scharf)} vom Host ausgeführte Datei(en) weichen von {REMOTE_REF} ab"
        f"{detached}: {genannt}"
    )
    return False, meldung[:MELDUNG_MAX_ZEICHEN]


def main():
    ok, meldung = pruefe()
    if ok:
        return 0
    print(meldung, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
