# ADR-003: SmartQueue mit Analyse-Pool, Fix-Lock und Circuit Breaker

**Status:** Accepted
**Datum:** 2026-03-10
**Kontext:** Mehrere Security-Events koennen gleichzeitig eintreffen (z.B. Brute-Force-Welle). Der Server hat nur 8 GB RAM — unkontrollierte Parallelisierung fuehrt zu OOM-Kills durch earlyoom. Gleichzeitig muessen Fixes seriell laufen, da sie den Server-Zustand veraendern.

## Entscheidung

`SmartQueue` mit drei Mechanismen:

- **Analyse-Pool:** `asyncio.Semaphore` mit 3 parallelen Slots (konfigurierbar via `max_analysis_parallel`). Analysen sind read-only und koennen sicher parallel laufen.
- **Fix-Lock:** `asyncio.Lock` — nur ein Fix gleichzeitig. Fix-Queue ist prioritaetsbasiert (`SEVERITY_PRIORITY`: CRITICAL=0, HIGH=1, MEDIUM=2, LOW=3). CRITICAL-Fixes werden vor LOW-Fixes abgearbeitet.
- **Circuit Breaker:** Nach 5 aufeinanderfolgenden Fehlern (`circuit_breaker_threshold`) oeffnet der Breaker und pausiert fuer 1 Stunde (`circuit_breaker_timeout=3600s`). Verhindert Endlos-Loops bei systematischen Problemen.

Zusaetzlich: **Batch-Modus-Erkennung** bei Event-Bursts (ab 5 Events in 10 Sekunden) fuer zusammengefasste Verarbeitung.

Queue-Eintraege sind typisiert als `QueueItem` (dataclass) mit `item_type` (ANALYSIS/FIX), `event`-Dict, `callback` und `priority`.

## Alternativen

- **Alles seriell:** Sicher, aber zu langsam — 10 Events a 60s Analyse = 10 Minuten Wartezeit.
- **Alles parallel:** Schnell, aber OOM-Gefahr. 5 parallele AI-CLI-Aufrufe koennten 4+ GB RAM verbrauchen.
- **Worker-Pool mit Threads:** Unnoetig komplex fuer asyncio-basierte Architektur, GIL-Probleme.

## Konsequenzen

**Positiv:**
- Analysen laufen schnell parallel (3x Durchsatz vs. seriell).
- Fixes sind sicher seriell — kein Race Condition bei Server-Zustandsaenderungen.
- Circuit Breaker schuetzt vor Endlos-Schleifen bei defektem AI-Provider.
- Prioritaets-Queue stellt sicher, dass CRITICAL-Events zuerst bearbeitet werden.

**Negativ:**
- 3 parallele Slots sind konservativ — auf groesseren Servern waere mehr moeglich.
- Circuit Breaker blockiert eine Stunde — in dieser Zeit werden auch valide Events nicht bearbeitet.
- Batch-Modus-Logik erhoht die Komplexitaet der Queue.
