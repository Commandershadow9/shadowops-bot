---
paths:
  - "src/integrations/ai_engine.py"
  - "src/schemas/**"
  - "src/integrations/analyst/**"
---
# AI Engine

## Dual-Engine Architektur
- **Primary:** CodexProvider (Codex CLI, `--output-schema` fuer strukturierten Output)
- **Fallback:** ClaudeProvider (Claude CLI, Schema im Prompt eingebaut)
- **Router:** TaskRouter bestimmt Engine + Modell basierend auf Severity + Task-Typ

## Modelle
| Engine | Fast | Standard | Thinking |
|--------|------|----------|----------|
| Codex | gpt-4o | gpt-5.3-codex | o3 |
| Claude | claude-sonnet-4-6 | claude-sonnet-4-6 | claude-opus-4-6 |

## Schema-Regeln (KRITISCH)
- OpenAI Structured Output: ALLE Properties muessen in `required` stehen
- Jedes verschachtelte Objekt mit `properties` braucht vollstaendiges `required`
- `additionalProperties: false` ist Pflicht
- Schema-Dateien: `src/schemas/*.json`

## Schema-Mapping
| Schema | Zweck | Genutzt von |
|--------|-------|-------------|
| `fix_strategy.json` | Remediation-Plaene | Orchestrator |
| `incident_analysis.json` | Incident-Analyse | Self-Healing |
| `patch_notes.json` | AI-generierte Patchnotes | PatchNotesManager |
| `analyst_session.json` | Security Analyst Output | SecurityAnalyst |

## Security Analyst
- Autonome Sessions (1/Tag, max 25 Turns)
- Activity Monitor prueft ob User aktiv ist (Claude Code Sessions)
- Ergebnisse als Discord Embed + GitHub Issues
- DB: `data/analyst.db` (asyncpg zu lokaler PostgreSQL)

## CLAUDECODE Bug
- `CLAUDECODE` env var MUSS aus subprocess entfernt werden
- Sonst "nested session" Fehler bei Claude CLI
- Wird in `_get_clean_env()` behandelt
