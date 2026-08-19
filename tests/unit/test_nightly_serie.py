"""
Tests fuer `scripts/nightly-serie.sh` — Serienlaenge roter Nightly-Laeufe (ZERODOX #2467).

Am 19.08.2026 gemessen: Der Workflow "Web Quality Nightly" war **14 von 15
Laeufen rot**, ununterbrochen seit dem 06.08. Kein einziger gruener Lauf in zwei
Wochen. Die Fehler sind echt — vier von sechs Shards mit konkret scheiternden
Tests (Portal-ShieldCard, Posteingang, Operator-Review, Mailing-DOI-Flow) —, und
keiner davon war als Issue erfasst.

## Warum das trotz Discord-Alarm passieren konnte

Der Workflow postet bei jedem Fehlschlag eine Discord-Nachricht. Das ist ein
**Ereignis**-Signal: "heute Nacht war es rot". Genau so sah es auch in der
vierzehnten Nacht aus — nicht anders als in der ersten.

"14 Naechte in Folge rot" ist aber eine voellig andere Aussage als "eine Nacht
rot". Sie wird heute von niemandem gesendet. Ein Dauerzustand, der wie ein
Einzelvorfall gemeldet wird, wird zu Hintergrundrauschen — derselbe Fehlermodus
wie beim Einmal-Alarm des runner-vm-disk-Watchdogs (#2425).

Dieses Skript berechnet die Serienlaenge; der Watchdog darum alarmiert erst ab
einer Schwelle und meldet die Dauer mit.
"""
import json
import os
import subprocess
from pathlib import Path

import pytest

SKRIPT = Path(__file__).resolve().parents[2] / "scripts" / "nightly-serie.sh"


def _lauf(laeufe: list[dict], env_extra: dict | None = None) -> dict:
    """
    Ruft das Skript mit einer vorgegebenen Lauf-Liste auf.

    Die Liste kommt im Betrieb aus `gh run list --json conclusion`. Im Test wird
    sie ueber NIGHTLY_LAEUFE_JSON hereingereicht, damit die Logik ohne Netz und
    ohne GitHub-Konto pruefbar ist — die Serienrechnung ist der Teil, der
    schiefgehen kann, nicht der API-Aufruf.
    """
    env = {
        "PATH": os.environ.get("PATH", ""),
        "NIGHTLY_LAEUFE_JSON": json.dumps(laeufe),
        **(env_extra or {}),
    }
    roh = subprocess.run(
        ["bash", str(SKRIPT)], env=env, capture_output=True, text=True, timeout=20
    )
    assert roh.returncode == 0, roh.stderr
    werte: dict = {}
    for zeile in roh.stdout.strip().splitlines():
        if "=" in zeile:
            k, v = zeile.split("=", 1)
            werte[k] = v
    return werte


# ---------- Serienrechnung ----------

def test_alles_gruen_ergibt_serie_null():
    w = _lauf([{"conclusion": "success"}] * 5)
    assert w["SERIE"] == "0"
    assert w["ZUSTAND"] == "ok"


def test_durchgehend_rot_zaehlt_alle():
    w = _lauf([{"conclusion": "failure"}] * 14)
    assert w["SERIE"] == "14"


def test_serie_endet_am_ersten_gruenen_lauf():
    # Neueste zuerst — wie `gh run list` liefert.
    w = _lauf([
        {"conclusion": "failure"},
        {"conclusion": "failure"},
        {"conclusion": "success"},
        {"conclusion": "failure"},
    ])
    assert w["SERIE"] == "2", "nur die ununterbrochene Serie ab dem neuesten Lauf zaehlt"


def test_ein_einzelner_roter_lauf_ist_noch_kein_befund():
    w = _lauf([{"conclusion": "failure"}, {"conclusion": "success"}])
    assert w["SERIE"] == "1"
    assert w["ZUSTAND"] == "ok", "eine einzelne rote Nacht ist normaler Betrieb"


# ---------- Schwelle ----------

def test_ab_der_schwelle_gilt_es_als_auffaellig():
    w = _lauf([{"conclusion": "failure"}] * 3, {"NIGHTLY_SERIE_SCHWELLE": "3"})
    assert w["ZUSTAND"] == "auffaellig"


def test_knapp_unter_der_schwelle_bleibt_ok():
    w = _lauf([{"conclusion": "failure"}] * 2, {"NIGHTLY_SERIE_SCHWELLE": "3"})
    assert w["ZUSTAND"] == "ok"


# ---------- Zustaende, die nicht "rot" sind ----------

def test_abgebrochener_lauf_zaehlt_zur_serie():
    """
    SERIE misst den Abstand zum letzten GRUENEN Lauf, nicht die Zahl der
    Fehlschlaege — die Frage lautet "wie lange lief nichts mehr durch?".

    ⚠️ `cancelled` beweist keinen Erfolg und zaehlt deshalb mit. Wer ihn
    ueberspringt, meldet eine kuerzere Serie als tatsaechlich vergangen ist.
    Genau so ein Eintrag stand am 15.08. mitten in der 14-Tage-Serie; haette er
    sie zurueckgesetzt, waere der Dauerausfall zweimal als "gerade erst
    angefangen" erschienen.
    """
    w = _lauf([
        {"conclusion": "failure"},
        {"conclusion": "cancelled"},
        {"conclusion": "failure"},
    ])
    assert w["SERIE"] == "3"


def test_laufender_lauf_wird_uebersprungen():
    w = _lauf([
        {"conclusion": None},
        {"conclusion": "failure"},
        {"conclusion": "failure"},
    ])
    assert w["SERIE"] == "2"


def test_leere_liste_meldet_unbekannt_statt_ok():
    # ⚠️ Keine Laeufe heisst NICHT "alles gut" — es heisst, der Workflow ist
    # womoeglich abgeschaltet. Ein stilles "ok" waere hier die schlimmere Antwort.
    w = _lauf([])
    assert w["ZUSTAND"] == "unbekannt"


def test_nur_abgebrochene_laeufe_melden_unbekannt():
    w = _lauf([{"conclusion": "cancelled"}] * 3)
    assert w["ZUSTAND"] == "unbekannt", "ohne ein einziges Ergebnis ist die Lage unbekannt"


# ---------- Ausgabeformat ----------

def test_gibt_serie_zustand_und_letzten_gruenen_lauf_aus():
    w = _lauf([
        {"conclusion": "failure", "createdAt": "2026-08-19T02:00:00Z"},
        {"conclusion": "success", "createdAt": "2026-08-05T02:00:00Z"},
    ])
    assert set(w) >= {"SERIE", "ZUSTAND", "LETZTER_GRUENER"}
    assert w["LETZTER_GRUENER"] == "2026-08-05"
