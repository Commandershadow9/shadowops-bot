---
title: ADR-005: SIGUSR1-basierte Log-Rotation
status: accepted
last_reviewed: 2026-04-15
owner: CommanderShadow9
---

# ADR-005: SIGUSR1-basierte Log-Rotation

**Status:** Accepted
**Datum:** 2026-03-10
**Kontext:** Python `logging.FileHandler` haelt einen offenen File-Descriptor auf die Log-Datei (z.B. `logs/shadowops_20260310.log`). Wenn logrotate die Datei verschiebt/umbenennt, schreibt der Handler weiter in den alten File-Descriptor — neue Log-Eintraege landen im rotierten File statt in der neuen Datei.

## Entscheidung

Eigener SIGUSR1-Signal-Handler in `bot.py` (registriert via `loop.add_signal_handler`):

1. Logrotate rotiert die Datei und sendet SIGUSR1 via `postrotate`-Skript.
2. Der Handler iteriert ueber alle `FileHandler` des `shadowops`-Loggers.
3. Jeder FileHandler wird geschlossen und entfernt.
4. Ein neuer FileHandler wird mit aktuellem Datums-Dateinamen erstellt (`shadowops_YYYYMMDD.log`).
5. Der neue Handler bekommt Level DEBUG und dasselbe Format wie der alte.

Der Handler laeuft im asyncio Event-Loop (via `add_signal_handler`) — kein Thread-Safety-Problem.

## Alternativen

- **`WatchedFileHandler`:** Erkennt inode-Aenderungen und oeffnet die Datei automatisch neu. Funktioniert nur auf Linux (inotify), ist platform-abhaengig und hat Timing-Probleme bei schneller Rotation.
- **`TimedRotatingFileHandler`:** Fuehrt eigene Rotation durch — kollidiert mit logrotate und erzeugt doppelte Rotation. Entweder logrotate ODER TimedRotating, nicht beides.
- **Bot-Restart bei Rotation:** systemd `ExecReload` oder `kill -TERM` + Restart. Verursacht 10-30s Downtime, waehrend der keine Security-Events verarbeitet werden.
- **copytruncate in logrotate:** Kopiert die Datei und truncated das Original. Verursacht Log-Verlust zwischen Kopie und Truncate bei hohem Schreib-Durchsatz.

## Konsequenzen

**Positiv:**
- Zero-Downtime Log-Rotation — Bot laeuft ununterbrochen weiter.
- Saubere Trennung: logrotate kuemmert sich um Dateien, Bot um seine Handler.
- Neuer Handler bekommt automatisch das aktuelle Datum im Dateinamen.

**Negativ:**
- Eigener Signal-Handler muss gewartet werden — Standard-Python-Logging bietet das nicht.
- Frueherer Bug: SIGUSR1 war nicht behandelt, Default-Aktion war Prozess-Terminierung → Bot-Crash bei jeder Rotation → systemd-Restart (behoben seit v4.0).
- Bei Aenderungen am Log-Format muss der Handler-Code in `bot.py` angepasst werden (kein zentrales Config).
