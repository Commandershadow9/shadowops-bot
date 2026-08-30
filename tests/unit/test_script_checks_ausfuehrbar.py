"""Jeder `type: script`-Check muss startbar sein (shadowops-bot#445-Nachlauf).

`CheckRunner._run_script` ruft `asyncio.create_subprocess_exec(argv[0], ...)` —
es startet die Datei DIREKT über ihren Shebang, nicht über `python3 <datei>`.
Fehlt das Ausführbar-Bit, wirft das einen `PermissionError`, der Check meldet
dauerhaft ERROR, und der Alarm sieht aus wie ein echter Befund.

Genau das war am 30.08.2026 bei `zerodox-build-drift-check.py` der Fall: Modus
`100644` im Index, stündliche Health-Alerts seit Einführung — für einen Check,
der nie eine einzige Messung durchgeführt hat.

Der Test prüft die Beispiel-Config, weil nur sie versioniert ist
(`config/config.yaml` enthält Secrets und ist gitignored).
"""

import os
from pathlib import Path

import yaml

_WURZEL = Path(__file__).resolve().parents[2]
_CONFIG = _WURZEL / "config" / "config.example.yaml"


def _script_checks():
    """Alle (projekt, check-id, target) mit type: script."""
    daten = yaml.safe_load(_CONFIG.read_text())
    for projekt, pdaten in (daten.get("projects") or {}).items():
        checks = ((pdaten.get("monitor") or {}).get("checks")) or []
        for check in checks:
            if check.get("type") == "script":
                yield projekt, check.get("id"), check.get("target")


def test_es_gibt_ueberhaupt_script_checks():
    """Sonst prüfte der Test unten stillschweigend eine leere Menge."""
    assert list(_script_checks()), "keine type:script-Checks gefunden — Parser kaputt?"


# Die `target`-Pfade sind absolut und serverspezifisch
# (`/home/cmdshadow/shadowops-bot/scripts/...`). Geprüft wird deshalb die
# ENTSPRECHENDE DATEI IM REPO, nicht der Pfad auf dem Server — der existiert im
# CI nicht, und der erste Entwurf dieses Tests scheiterte genau daran.
#
# Das ist ohnehin die richtige Frage: Das Ausführbar-Bit reist im Git-Modus mit
# (100644 vs. 100755). Wer es im Repo setzt, hat es überall.
_MARKER = "/shadowops-bot/"


def _repo_pfad(target):
    """Target → Pfad im Repo, oder None für repo-fremde Skripte."""
    roh = target.split()[0]
    if _MARKER not in roh:
        return None
    return _WURZEL / roh.split(_MARKER, 1)[1]


def test_jedes_script_target_existiert_und_ist_ausfuehrbar():
    fehler = []
    geprueft = 0
    for projekt, check_id, target in _script_checks():
        if not target:
            fehler.append(f"{projekt}/{check_id}: kein target")
            continue
        pfad = _repo_pfad(target)
        if pfad is None:
            continue  # Skript ausserhalb des Repos — nicht unsere Zusage
        geprueft += 1
        if not pfad.exists():
            fehler.append(f"{projekt}/{check_id}: {pfad} existiert nicht")
        elif not os.access(pfad, os.X_OK):
            fehler.append(
                f"{projekt}/{check_id}: {pfad} ist nicht ausführbar — "
                f"create_subprocess_exec wirft PermissionError"
            )
    assert geprueft, "kein einziges repo-eigenes Skript geprüft — Pfad-Mapping kaputt?"
    assert not fehler, "Nicht startbare Skript-Checks:\n  " + "\n  ".join(fehler)
