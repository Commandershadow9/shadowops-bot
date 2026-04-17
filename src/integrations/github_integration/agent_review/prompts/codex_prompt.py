"""Codex-Review Prompt Builder.

Codex-PRs kommen typisch vom SecurityScanAgent (Code-Security-Fixes via
Codex CLI) oder von autonomen Refactoring-Tasks. Andere Schwerpunkte als
SEO/Jules: hier geht es um **Code-Korrektheit, Security, Backwards-Compat**
— nicht um Content oder externe Reviews.

Output-Format: gleiches JSON-Schema wie Jules/SEO (verdict, blockers,
suggestions, nits, scope_check). Severity: critical|high|medium|low.
"""
from __future__ import annotations

from typing import Any, Dict, List


MAX_DIFF_CHARS_DEFAULT = 10000  # mehr als SEO weil Code-Diffs verbose sind
MAX_FILES_BEFORE_WARNING = 30


def truncate_diff(diff: str, max_chars: int = MAX_DIFF_CHARS_DEFAULT) -> str:
    """Schneidet Diff auf max_chars, mit Marker."""
    if len(diff) <= max_chars:
        return diff
    cut = diff[:max_chars]
    remaining = len(diff) - max_chars
    return cut + f"\n\n[... {remaining} Zeichen abgeschnitten ...]"


def build_codex_review_prompt(
    *,
    diff: str,
    project: str,
    iteration: int,
    files_changed: List[str],
    knowledge: List[str],
    few_shot: List[Dict[str, Any]],
    finding_context: Dict[str, Any] | None = None,
    max_diff_chars: int = MAX_DIFF_CHARS_DEFAULT,
) -> str:
    """Baut den Claude-Prompt fuer einen Codex-Agent PR (Security-Fix oder Refactor)."""
    diff_short = truncate_diff(diff, max_diff_chars)

    files_block = (
        "\n".join(f"- `{f}`" for f in files_changed[:60])
        if files_changed
        else "(Liste nicht verfuegbar)"
    )

    knowledge_block = (
        "\n".join(f"- {k}" for k in knowledge)
        if knowledge
        else "(noch keine gelernten Konventionen)"
    )

    examples_block = _format_examples(few_shot)
    finding_block = _format_finding_context(finding_context)

    return f"""Du bist ein Senior Code-Reviewer. Pruefe diesen Pull-Request vom Codex-Agent
(autonomer AI-Coder, oft fuer Security-Fixes oder Refactoring).

Codex-PRs sind in der Regel:
- **Security-Fixes** vom SecurityScanAgent (CVE-Patches, Input-Validation, Auth-Hardening)
- **Refactorings** mit klarem Scope (Logger-Swap, Import-Cleanup, Type-Annotations)
- **Bug-Fixes** mit Test-Begleitung

Sei strikt bei Code-Qualitaet — Security-Fixes muessen wasserdicht sein.

---

## Kontext

- **Projekt:** {project}
- **Iteration: {iteration}** of 5
- **Anzahl Dateien:** {len(files_changed)}

{finding_block}

---

## Geaenderte Dateien

{files_block}

---

## Projekt-Konventionen (gelernt aus vorigen Reviews)

{knowledge_block}

---

## Beispiele (vorige Reviews dieses Projekts)

{examples_block}

---

## Diff

```diff
{diff_short}
```

---

## Pruefe folgende Bereiche

### Korrektheit
- Loest der Fix wirklich das beschriebene Problem (siehe Finding-Kontext)?
- Werden Edge-Cases abgedeckt (None, leere Strings, negative Zahlen, Unicode)?
- Wird Logik nicht versehentlich invertiert (z.B. `if x` -> `if not x`)?
- Werden bestehende Tests noch gruen sein (laufen nicht hier, aber Logik pruefen)?

### Security (KRITISCH bei Security-Fixes)
- Input-Validation an den richtigen Stellen (System-Boundaries)?
- Keine SQL-Injection (Parametrisierte Queries, kein String-Concat)?
- Keine Command-Injection (`shell=True` vermeiden, Argumente als Liste)?
- Keine Path-Traversal (`../`-Schutz, `os.path.realpath` Vergleich)?
- Secrets nicht in Logs/Errors geleakt?
- Crypto: keine eigenen Implementierungen, immer `cryptography`/`secrets`?

### Code-Qualitaet
- Keine Magic-Numbers (Konstanten extrahiert)?
- Keine ueberfluessigen `try/except` mit `pass` (Silent-Failure)?
- Keine ungenutzen Imports/Variablen?
- Type-Hints sinnvoll (kein `Any` wo konkreter Typ moeglich)?
- Funktionen kurz und fokussiert (SRP)?

### Backwards-Compatibility
- Werden oeffentliche APIs (Funktionen, Klassen, Models) ohne Migration veraendert?
- Bei DB-Schema-Aenderungen: Migration vorhanden?
- Bei Config-Aenderungen: Default-Werte abwaertskompatibel?

### Test-Coverage
- Bei Bug-Fix: Regression-Test vorhanden?
- Bei neuem Code-Pfad: Happy-Path + 1 Edge-Case getestet?
- Bei Security-Fix: Test der den ALTEN Bug reproduziert (jetzt rot wuerde)?

### Scope-Check (KRITISCH)
- Beschraenkt sich der PR auf das beschriebene Finding/Task?
- Keine "drive-by" Refactorings die nicht zur Aufgabe gehoeren?
- Bei mehr als {MAX_FILES_BEFORE_WARNING} Files: Warnung als SUGGESTION
  (PR sollte aufgeteilt werden, ausser zusammenhaengender Refactor).

---

## Ausgabe (NUR JSON, kein Markdown-Fence, kein Extra-Text)

```json
{{
  "verdict": "approved" | "revision_requested",
  "summary": "1-3 Saetze: Was macht der PR und wie ist die Qualitaet?",
  "blockers": [
    {{
      "title": "Kurz",
      "reason": "Warum Blocker",
      "file": "src/auth.py",
      "line": 42,
      "severity": "critical|high|medium|low",
      "suggested_fix": "Konkreter Code-Vorschlag"
    }}
  ],
  "suggestions": [...],
  "nits": [...],
  "scope_check": {{
    "in_scope": true|false,
    "explanation": "Warum"
  }}
}}
```

**Definitionen:**
- **BLOCKER**: Security-Hole, falsche Logik, fehlende Validation, nicht-abwaertskompatible Aenderung
  ohne Migration, kein Test bei Security-Fix, eindeutig Out-of-Scope.
- **SUGGESTION**: Verbesserung (Konstante extrahieren, besserer Name, mehr Tests, Type-Hint).
- **NIT**: Stil (Whitespace, Import-Reihenfolge, Docstring-Format), nicht-blockierend.

severity-Werte: critical | high | medium | low.

Sei bei Security-Fixes besonders kritisch — lieber revision_requested mit konkretem
Vorschlag als ein halbgarer Patch.
"""


def _format_examples(examples: List[Dict[str, Any]]) -> str:
    """Formatiert Few-Shot-Examples kompakt."""
    if not examples:
        return "(noch keine Beispiele fuer dieses Projekt)"
    lines = []
    for ex in examples[:4]:
        outcome = ex.get("outcome", "unknown")
        summary = ex.get("diff_summary", "")[:100]
        lines.append(f"- **[{outcome}]** {summary}")
    return "\n".join(lines)


def _format_finding_context(finding: Dict[str, Any] | None) -> str:
    """Formatiert das urspruengliche Security-Finding (falls vorhanden)."""
    if not finding:
        return ""
    title = finding.get("title", "")
    severity = finding.get("severity", "unknown")
    description = finding.get("description", "")[:300]
    return f"""---

## Urspruengliches Security-Finding

- **Titel:** {title}
- **Severity:** {severity}
- **Beschreibung:** {description}
"""
