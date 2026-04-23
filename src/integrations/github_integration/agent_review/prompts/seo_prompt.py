"""SEO-Review Prompt Builder.

Deckt ab: SEO + GSC + GEO + AEO.

Output-Format: gleiches JSON-Schema wie Jules-Review (verdict, blockers,
suggestions, nits, scope_check). Severity: critical|high|medium|low.
"""
from __future__ import annotations

from typing import Any, Dict, List


MAX_DIFF_CHARS_DEFAULT = 8000
MAX_FILES_BEFORE_BLOCKER = 50


def truncate_diff(diff: str, max_chars: int = MAX_DIFF_CHARS_DEFAULT) -> str:
    """Schneidet Diff auf max_chars, mit Marker."""
    if len(diff) <= max_chars:
        return diff
    cut = diff[:max_chars]
    remaining = len(diff) - max_chars
    return cut + f"\n\n[... {remaining} Zeichen abgeschnitten ...]"


def build_seo_review_prompt(
    *,
    diff: str,
    project: str,
    iteration: int,
    files_changed: List[str],
    knowledge: List[str],
    few_shot: List[Dict[str, Any]],
    max_diff_chars: int = MAX_DIFF_CHARS_DEFAULT,
) -> str:
    """Baut den Claude-Prompt fuer einen SEO-Agent PR."""
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

    return f"""Du bist ein Senior SEO-Reviewer. Pruefe diesen Pull-Request vom SEO-Agent.

Der SEO-Agent ist ein **Multi-Domain-Agent** der abdeckt:
- **SEO**: On-Page (Meta, Canonical, H-Tags, Internal Links), Sitemap, Robots
- **GSC** (Google Search Console): Indexing-Signale, Strukturierte Daten (Schema.org), URL-Submissions
- **GEO** (Local Search): Business-Schema, lokale Keywords, NAP-Konsistenz
- **AEO** (Answer Engine Optimization): AI-Search-Lesbarkeit (Perplexity, Claude, ChatGPT, Bing Copilot)

Sei pragmatisch — der Agent hat seinen Job, du verifizierst nur Qualitaet und Scope.

---

## Kontext

- **Projekt:** {project}
- **Iteration: {iteration}** of 5
- **Anzahl Dateien:** {len(files_changed)}

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

### SEO-Qualitaet
- Meta-Descriptions unique, 120-160 Zeichen, beschreibend?
- Canonical-Tags korrekt (kein Self-Loop, keine widerspruechlichen)?
- Sitemap-Aenderungen konsistent (LAST_UPDATED nur bei echter Content-Aenderung)?
- Interne Links sinnvoll platziert (nicht Footer-Spam, kontextuell)?
- H-Tag-Hierarchie sauber (kein H1-Skip)?

### GSC (Google Search Console)
- Strukturierte Daten (Schema.org JSON-LD) syntaktisch korrekt?
- Robots.txt nicht versehentlich kaputt gemacht (z.B. Disallow auf money pages)?
- Index-Signale nicht widerspruechlich (canonical vs. noindex vs. robots)?
- Keine doppelten URL-Submissions an die Search Console?

### GEO (Local Search)
- Falls Local-SEO betroffen: LocalBusiness-Schema komplett (Name, Address, Phone)?
- NAP-Konsistenz (Name/Adresse/Phone) ueber Pages hinweg?

### AEO (AI Engine Optimization)
- Content-Struktur AI-lesbar (klare Headings, FAQ-Schema, How-To-Schema)?
- Anwortbarkeit hoch (kurze, direkte Antworten am Anfang von Sektionen)?

### Scope-Sicherheit (KRITISCH)
- NUR Content/Metadata-Aenderungen (`.md`, `.mdx`, `.ts` mit Content, `sitemap.ts`, `robots.txt`)?
- KEINE Aenderungen an: `package.json`, `next.config.*`, `tsconfig.*`, `eslint.config.*`, `prisma/schema.prisma` (Build-Config)?
- KEINE Layout-/Component-Aenderungen (`layout.tsx`, `components/`)?
- KEINE Datenbank-/Backend-Schema-Aenderungen?
- Existierende Tests bleiben stabil?

### Batch-Groesse
- Bei **mehr als {MAX_FILES_BEFORE_BLOCKER} File-Aenderungen**: als BLOCKER markieren.
  Begruendung: zu grosser Batch fuer einen Review-Cycle, sollte aufgeteilt werden.

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
      "file": "web/src/content/blog.md",
      "line": 23,
      "severity": "critical|high|medium|low",
      "suggested_fix": "Wie der Agent es fixen sollte"
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
- **BLOCKER**: Out-of-Scope (Build-Config, Layout, Schema), Schema.org Syntax-Fehler, kaputte Robots/Sitemap, >50 Files Batch.
- **SUGGESTION**: Optimierungs-Vorschlag (besseres Wording, weitere Schema-Felder, AEO-Verbesserung).
- **NIT**: Stil (Whitespace, Reihenfolge), nicht-blockierend.

severity-Werte: critical | high | medium | low.

Halte den Scope-Check STRENG: SEO-Agent darf KEINE Build-/Layout-/Backend-Aenderungen einreichen.
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
