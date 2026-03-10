# ADR-001: Dual-Engine AI-System (Codex CLI + Claude CLI)

**Status:** Accepted
**Datum:** 2026-03-10
**Kontext:** ShadowOps braucht AI fuer Security-Analyse und Fix-Generierung. Ein einzelnes Modell ist ein Single Point of Failure — faellt die API aus, steht der gesamte Security-Workflow still. Ausserdem haben verschiedene Tasks unterschiedliche Anforderungen: schnelle Triage vs. tiefe Analyse.

## Entscheidung

Dual-Engine-Architektur mit zwei CLI-basierten Providern:

- **CodexProvider (primaer):** Codex CLI mit `--output-schema` fuer strukturierten JSON-Output. Drei Modell-Tiers: `gpt-4o` (fast), `gpt-5.3-codex` (standard), `o3` (thinking/tiefe Analyse). Timeouts: 60s standard, 300s fuer Thinking-Modelle.
- **ClaudeProvider (fallback):** Claude CLI ohne nativen Schema-Support — Schema-Anweisungen werden stattdessen im Prompt eingebettet.
- **TaskRouter:** Routet Auftraege severity- und task-basiert zum passenden Engine/Modell.
- **AIEngine:** Hauptklasse, kompatibel mit dem alten `AIService`-Interface fuer nahtlose Migration.

Beide Provider nutzen `asyncio.create_subprocess_exec` (kein `shell=True`). Die `CLAUDECODE` Umgebungsvariable wird aus Subprocess-Umgebungen entfernt, um "nested session"-Fehler zu vermeiden.

## Alternativen

- **Nur OpenAI API direkt:** Kein Fallback bei Ausfaellen, API-Key-Management noetig.
- **Nur Claude API direkt:** Kein strukturierter Output via `--output-schema`.
- **Ollama lokal:** Wurde in v3.x genutzt, in v4.0 entfernt wegen unzureichender Analysequalitaet bei Security-Events. Server hat nur 8 GB RAM — lokale Modelle konkurrieren mit Bot und anderen Services.

## Konsequenzen

**Positiv:**
- Resilient gegen Ausfaelle einzelner AI-Provider.
- Severity-basiertes Routing: schnelle Modelle fuer LOW, Thinking-Modelle fuer CRITICAL.
- Strukturierter Output (JSON-Schema) reduziert Parsing-Fehler bei Codex.

**Negativ:**
- Zwei CLI-Tools (`codex`, `claude`) als externe Dependencies auf dem Host.
- Schema-Dateien muessen OpenAI-kompatibel sein (alle Properties in `required`).
- Claude-Fallback liefert weniger zuverlaessig strukturierten Output als Codex mit `--output-schema`.
