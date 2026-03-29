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

Provider-Limits werden nicht als generische "Fehler" behandelt. Stattdessen liest die `AIEngine` Reset-Hinweise aus dem CLI-Output, cached den betroffenen Provider bis zum Reset und ueberspringt ihn in dieser Zeit. Fuer den `weekly_deep` Pfad gilt zusaetzlich: Claude bleibt die bevorzugte Engine fuer tiefe Code-Reviews, aber bei erkanntem Claude-Limit wird derselbe Lauf sofort ueber Codex fortgesetzt.

## Alternativen

- **Nur OpenAI API direkt:** Kein Fallback bei Ausfaellen, API-Key-Management noetig.
- **Nur Claude API direkt:** Kein strukturierter Output via `--output-schema`.
- **Ollama lokal:** Wurde in v3.x genutzt, in v4.0 entfernt wegen unzureichender Analysequalitaet bei Security-Events. Server hat nur 8 GB RAM — lokale Modelle konkurrieren mit Bot und anderen Services.

## Konsequenzen

**Positiv:**
- Resilient gegen Ausfaelle einzelner AI-Provider.
- Severity-basiertes Routing: schnelle Modelle fuer LOW, Thinking-Modelle fuer CRITICAL.
- Strukturierter Output (JSON-Schema) reduziert Parsing-Fehler bei Codex.
- Provider-Quoten fuehren nicht mehr zu Retry-Loops oder Discord-Spam, sondern zu kontrolliertem Failover.
- Weekly-Deep-Scans bleiben auch bei temporarem Claude-Limit funktionsfaehig.

**Negativ:**
- Zwei CLI-Tools (`codex`, `claude`) als externe Dependencies auf dem Host.
- Schema-Dateien muessen OpenAI-kompatibel sein (alle Properties in `required`).
- Claude-Fallback liefert weniger zuverlaessig strukturierten Output als Codex mit `--output-schema`.
- Betriebslogik wird komplexer: Provider-Quota, Session-Backoff und Tages-Disable muessen getrennt beobachtet werden.
