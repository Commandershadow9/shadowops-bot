"""Stufe 3: Generate — Template + AI-Call + Structured Output Parsing."""

from __future__ import annotations

import json
import logging
import re
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from patch_notes.context import PipelineContext

logger = logging.getLogger('shadowops')


async def generate(ctx: PipelineContext, bot=None) -> None:
    """Stufe 3: Prompt bauen, AI aufrufen, Output parsen."""
    from patch_notes.templates import get_template

    template_type = ctx.project_config.get('patch_notes', {}).get('type', 'devops')
    template = get_template(template_type)

    # 1. Prompt bauen
    prompt = template.build_prompt(ctx)

    # Feature-Branch-Teasers anhängen (wenn Projekt-Pfad verfügbar)
    teasers = _collect_feature_teasers(ctx)
    if teasers:
        prompt += f"\n\n{teasers}"

    ctx.prompt = prompt
    logger.info(f"[v6] {ctx.project}: Prompt gebaut ({len(prompt)} Zeichen, Template: {template_type})")

    # 2. AI-Call
    start = time.monotonic()
    ai_service = _get_ai_service(bot)

    if ai_service:
        ctx.ai_result = await _call_ai(ai_service, prompt, ctx)
    else:
        logger.warning(f"[v6] {ctx.project}: Kein AI-Service verfügbar — Fallback")
        ctx.ai_result = None

    ctx.generation_time_s = round(time.monotonic() - start, 2)
    ctx.metrics['ai_generation_time_s'] = ctx.generation_time_s
    ctx.metrics['ai_engine'] = ctx.ai_engine_used
    ctx.metrics['prompt_length'] = len(prompt)

    logger.info(
        f"[v6] {ctx.project}: AI-Generierung in {ctx.generation_time_s}s "
        f"(Engine: {ctx.ai_engine_used or 'none'})"
    )


def _get_ai_service(bot):
    """Hole AI-Service vom Bot (falls vorhanden)."""
    if bot is None:
        return None
    github = getattr(bot, 'github_integration', None)
    if github is None:
        return None
    return getattr(github, 'ai_service', None)


async def _call_ai(ai_service, prompt: str, ctx: PipelineContext) -> dict | str | None:
    """AI-Call mit Retry und Structured Output."""
    patch_config = ctx.project_config.get('patch_notes', {})
    use_critical = patch_config.get('use_critical_model', True)

    # Structured Prompt: JSON-Schema Anweisung + eigentlicher Prompt
    structured_prompt = _build_structured_wrapper(prompt, ctx)

    # Versuch 1: Structured Output
    try:
        result = await ai_service.generate_structured_patch_notes(
            prompt=structured_prompt,
            use_critical_model=use_critical,
        )
        if result and isinstance(result, dict):
            ctx.ai_engine_used = getattr(ai_service, '_last_engine', 'unknown')
            # Echte Git-Stats einsetzen (AI-Stats sind unzuverlässig)
            result['stats'] = ctx.git_stats
            result['language'] = patch_config.get('language', 'de')
            return result
    except Exception as e:
        logger.warning(f"[v6] Structured Output fehlgeschlagen: {e}")

    # Versuch 2: Raw Text Fallback
    try:
        raw = await ai_service.query(prompt)
        if raw and isinstance(raw, str):
            ctx.ai_engine_used = getattr(ai_service, '_last_engine', 'unknown')
            # Versuche JSON aus Raw-Text zu extrahieren
            parsed = _try_parse_json(raw)
            if parsed:
                parsed['stats'] = ctx.git_stats
                return parsed
            return raw
    except Exception as e:
        logger.warning(f"[v6] Raw Fallback fehlgeschlagen: {e}")

    return None


def _build_structured_wrapper(prompt: str, ctx: PipelineContext) -> str:
    """Wrapper der JSON-Output erzwingt."""
    lang = ctx.project_config.get('patch_notes', {}).get('language', 'de')
    if lang == 'de':
        instruction = (
            "Antworte AUSSCHLIESSLICH mit einem JSON-Objekt. Kein Markdown, kein Text davor/danach.\n"
            "Schema: {\"title\": str, \"tldr\": str, \"web_content\": str, "
            "\"changes\": [{\"type\": str, \"description\": str, \"details\": [str]}], "
            "\"seo_keywords\": [str]}\n"
            "WICHTIG: 'title' enthält NUR den Update-Namen, KEINE Version.\n\n"
        )
    else:
        instruction = (
            "Respond ONLY with a JSON object. No markdown, no text before/after.\n"
            "Schema: {\"title\": str, \"tldr\": str, \"web_content\": str, "
            "\"changes\": [{\"type\": str, \"description\": str, \"details\": [str]}], "
            "\"seo_keywords\": [str]}\n"
            "IMPORTANT: 'title' must contain ONLY the update name, NO version.\n\n"
        )
    return instruction + prompt


def _try_parse_json(text: str) -> dict | None:
    """Versuche JSON aus AI-Antwort zu extrahieren (Markdown-Fences, etc.)."""
    # Direkt parsen
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass

    # JSON aus Markdown-Fence extrahieren
    fence_match = re.search(r'```(?:json)?\s*\n(.*?)\n```', text, re.DOTALL)
    if fence_match:
        try:
            return json.loads(fence_match.group(1))
        except (json.JSONDecodeError, TypeError):
            pass

    # Erstes { bis letztes } extrahieren
    brace_start = text.find('{')
    brace_end = text.rfind('}')
    if brace_start >= 0 and brace_end > brace_start:
        try:
            return json.loads(text[brace_start:brace_end + 1])
        except (json.JSONDecodeError, TypeError):
            pass

    return None


def _collect_feature_teasers(ctx: PipelineContext) -> str:
    """Sammle aktive Feature-Branches für 'Demnächst'-Sektion."""
    project_path = ctx.project_config.get('path', '')
    if not project_path:
        return ""

    deploy_branch = ctx.project_config.get('deploy', {}).get('branch', 'main')

    try:
        result = subprocess.run(
            ['git', 'branch', '-r', '--list', 'origin/feat/*'],
            capture_output=True, text=True, timeout=5, cwd=project_path,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return ""

        branches = [b.strip() for b in result.stdout.strip().split('\n') if b.strip()]
        if not branches:
            return ""

        lines = ["# Aktive Feature-Branches (für 'Demnächst'-Sektion)"]
        for branch in branches[:5]:
            name = branch.replace('origin/feat/', '').replace('-', ' ').title()
            lines.append(f"- {name} (in Entwicklung)")

        return "\n".join(lines)
    except Exception:
        return ""
