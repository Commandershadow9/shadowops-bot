"""
Tests fuer die Takt-Ableitung aus dem eigenen systemd-Timer (ZERODOX #2452).

Hintergrund: Bis zum 18.08.2026 meldete `service-watchdog.sh` den Melderhythmus
als `${WATCHDOG_TAKT_SEK:-300}`. Die Variable setzte **keine** Unit, also griff
ueberall der Default — bei sieben Watchdogs mit 30-Minuten- bis 6-Stunden-Takt
war er falsch, und die ZERODOX-Systemstatus-Seite zeigte sie die meiste Zeit
faelschlich als "stumm".

Der Default `300` stimmte fuer die Mehrheit (8 von 15). Genau das machte ihn
unauffindbar: Die Seite sah nicht kaputt aus, sondern so, als waeren ein paar
Dienste ausgefallen.

Diese Tests pruefen die Ableitung gegen ein vorgetaeuschtes `systemctl` —
damit faellt der Wert nicht mehr aus einer Env-Variablen, die jemand vergessen
kann, sondern aus dem Timer, der den Watchdog tatsaechlich startet.
"""
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[2] / "scripts" / "lib" / "watchdog-report.sh"


@pytest.fixture
def fake_bin():
    """Verzeichnis mit vorgetaeuschtem `systemctl`, das vor dem echten liegt."""
    d = tempfile.mkdtemp(prefix="watchdog-takt-")
    try:
        yield Path(d)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _schreibe_systemctl(bindir: Path, ausgabe: str, exit_code: int = 0):
    p = bindir / "systemctl"
    p.write_text(f"#!/usr/bin/env bash\ncat <<'A'\n{ausgabe}\nA\nexit {exit_code}\n")
    p.chmod(0o755)


def _takt(bindir: Path, timer: str = "beispiel-watchdog.timer", env_extra=None) -> str:
    """Ruft _takt_aus_timer in einer Subshell auf und gibt die Ausgabe zurueck."""
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", **(env_extra or {})}
    r = subprocess.run(
        ["bash", "-c", f'source "{LIB}"; _takt_aus_timer "{timer}"'],
        env=env, capture_output=True, text=True, timeout=20,
    )
    return r.stdout.strip()


# ---------- Intervall-Timer ----------

def test_minuten_intervall_wird_in_sekunden_umgerechnet(fake_bin):
    _schreibe_systemctl(fake_bin, "TimersMonotonic={ OnUnitActiveUSec=30min ; next_elapse=1h }")
    assert _takt(fake_bin) == "1800"


def test_stunden_intervall(fake_bin):
    _schreibe_systemctl(fake_bin, "TimersMonotonic={ OnUnitActiveUSec=6h ; next_elapse=6h }")
    assert _takt(fake_bin) == "21600"


def test_boot_eintrag_wird_nicht_mit_dem_takt_verwechselt(fake_bin):
    """
    Der Regressionsfall: Jeder Timer traegt zusaetzlich `OnBootUSec`. Wer nur den
    ersten TimersMonotonic-Eintrag nimmt, liest je nach Reihenfolge die
    Verzoegerung nach dem Systemstart statt des Wiederholungsrhythmus.
    """
    _schreibe_systemctl(fake_bin, (
        "TimersMonotonic={ OnBootUSec=9min ; next_elapse=9min }\n"
        "TimersMonotonic={ OnUnitActiveUSec=30min ; next_elapse=30min }"
    ))
    assert _takt(fake_bin) == "1800"


def test_nur_boot_ohne_wiederholung_liefert_nichts(fake_bin):
    """Ein Timer, der nur einmal nach dem Start feuert, hat keinen Takt."""
    _schreibe_systemctl(fake_bin, "TimersMonotonic={ OnBootUSec=9min ; next_elapse=9min }")
    assert _takt(fake_bin) == ""


# ---------- Kalender-Timer ----------

def test_taeglicher_kalender_timer_ergibt_86400(fake_bin):
    """
    `doku-drift` (06:30) und `ki-cost` (07:15) laufen ueber OnCalendar, nicht
    ueber ein Intervall. Der Abstand wird aus zwei aufeinanderfolgenden
    Ausloesezeitpunkten berechnet — das gilt fuer jede Kalenderangabe, nicht
    nur fuer taegliche.
    """
    _schreibe_systemctl(fake_bin, "TimersCalendar={ OnCalendar=*-*-* 06:30:00 ; next_elapse=... }")
    assert _takt(fake_bin) == "86400"


def test_woechentlicher_kalender_timer(fake_bin):
    _schreibe_systemctl(fake_bin, "TimersCalendar={ OnCalendar=Mon *-*-* 08:00:00 ; next_elapse=... }")
    assert _takt(fake_bin) == str(7 * 86400)


# ---------- Fehlerfaelle ----------

def test_unbekannter_timer_liefert_leer_statt_zu_raten(fake_bin):
    _schreibe_systemctl(fake_bin, "", exit_code=1)
    assert _takt(fake_bin) == ""


def test_leere_ausgabe_liefert_leer(fake_bin):
    _schreibe_systemctl(fake_bin, "TimersMonotonic=")
    assert _takt(fake_bin) == ""


# ---------- Zeitzonen-Unabhaengigkeit ----------
#
# Die erste Fassung der Ableitung las die `(in UTC):`-Zeile von
# `systemd-analyze calendar`. Die gibt es aber NUR, wenn die lokale Zeitzone
# von UTC abweicht — auf einem System mit TZ=UTC laesst systemd sie als
# redundant weg.
#
# Auf dem Entwicklungsrechner (CEST) war das gruen, im CI (UTC) fiel die
# Ableitung still auf den 300-Sekunden-Default zurueck. Also genau in den
# Fehler, den sie beseitigen soll, und ausgerechnet bei den beiden
# Kalender-Timern (`doku-drift`, `ki-cost`).
#
# Diese Tests laufen dieselbe Ableitung unter beiden Zeitzonen.

@pytest.mark.parametrize("tz", ["UTC", "Europe/Berlin", "America/New_York"])
def test_kalender_ableitung_ist_zeitzonen_unabhaengig(fake_bin, tz):
    _schreibe_systemctl(fake_bin, "TimersCalendar={ OnCalendar=*-*-* 06:30:00 ; next_elapse=... }")
    assert _takt(fake_bin, env_extra={"TZ": tz}) == "86400", (
        f"Kalender-Takt unter TZ={tz} nicht ableitbar — "
        "vermutlich wieder an einer zeitzonenabhaengigen Ausgabezeile festgemacht"
    )


@pytest.mark.parametrize("tz", ["UTC", "Europe/Berlin"])
def test_intervall_ableitung_ist_zeitzonen_unabhaengig(fake_bin, tz):
    # Gegenprobe: Der Intervall-Zweig war nie betroffen, soll es auch bleiben.
    _schreibe_systemctl(fake_bin, "TimersMonotonic={ OnUnitActiveUSec=30min ; next_elapse=... }")
    assert _takt(fake_bin, env_extra={"TZ": tz}) == "1800"
