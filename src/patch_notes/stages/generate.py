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

    # 0. A/B-Variante auswählen (wenn verfügbar)
    ctx.variant_id = await _select_variant(ctx, bot)

    # 1. Prompt bauen
    prompt = template.build_prompt(ctx)

    # Feature-Branch-Teasers anhängen (wenn Projekt-Pfad verfügbar)
    teasers = _collect_feature_teasers(ctx)
    if teasers:
        prompt += f"\n\n{teasers}"

    ctx.prompt = prompt
    logger.info(
        f"[v6] {ctx.project}: Prompt gebaut ({len(prompt)} Zeichen, "
        f"Template: {template_type}, Variante: {ctx.variant_id or 'default'})"
    )

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


async def _select_variant(ctx: PipelineContext, bot) -> str:
    """Wähle A/B-Variante: Config-Pin → Learning-DB → Random."""
    if bot is None:
        return ""
    github = getattr(bot, 'github_integration', None)
    if not github:
        return ""

    ab = getattr(github, 'prompt_ab_testing', None)
    if not ab:
        return ""

    pc = ctx.project_config.get('patch_notes', {})

    # 1. Config-Pin hat Vorrang
    pinned = pc.get('preferred_variant', '')
    if pinned and pinned in ab.variants:
        logger.info(f"📌 Config: Gepinnte Variante '{pinned}'")
        return pinned

    # 2. Learning-DB (beste Variante nach Feedback)
    learning = getattr(github, 'patch_notes_learning', None)
    if learning:
        try:
            best = await learning.get_best_variant(ctx.project)
            if best and best in ab.variants:
                logger.info(f"🧪 Learning-DB: Beste Variante '{best}' für {ctx.project}")
                return best
        except Exception as e:
            logger.debug(f"Learning-DB Varianten-Abfrage fehlgeschlagen: {e}")

    # 3. Weighted Random
    try:
        variant = ab.select_variant(ctx.project, strategy='weighted_random')
        logger.info(f"🧪 A/B Test: Variante '{variant.name}' (ID: {variant.id})")
        return variant.id
    except Exception as e:
        logger.debug(f"A/B-Varianten-Auswahl fehlgeschlagen: {e}")
        return ""


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

    # Versuch 2: Raw Text Fallback via get_raw_ai_response (AIEngine API)
    try:
        raw = await ai_service.get_raw_ai_response(prompt, use_critical_model=use_critical)
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
    """Wrapper der JSON-Output erzwingt.

    WICHTIG: discord_highlights ist PFLICHT — AIEngine.generate_structured_patch_notes()
    returned None ohne dieses Feld (Schema-Validierung in patch_notes.json).
    """
    lang = ctx.project_config.get('patch_notes', {}).get('language', 'de')
    if lang == 'de':
        instruction = (
            "Antworte AUSSCHLIESSLICH mit einem JSON-Objekt. Kein Markdown, kein Text davor/danach.\n"
            "Schema (ALLE Felder PFLICHT):\n"
            "{\n"
            '  "title": str,                     # Update-Name OHNE Version\n'
            '  "tldr": str,                      # 1-2 Sätze Zusammenfassung\n'
            '  "discord_highlights": [str],      # 3-6 kurze Stichpunkte für Discord\n'
            '  "web_content": str,               # Vollständiger Markdown-Text für Website\n'
            '  "summary": str,                   # Kurzer Intro-Absatz (1-3 Sätze)\n'
            '  "changes": [{"type": str, "description": str, "details": [str]}],\n'
            '  "seo_keywords": [str]             # 3-8 SEO-Keywords\n'
            "}\n"
            "WICHTIG: 'title' enthält NUR den Update-Namen, KEINE Version.\n"
            "WICHTIG: 'discord_highlights' sind 3-6 knackige Bullet-Points (max 100 Zeichen).\n\n"
        )
    else:
        instruction = (
            "Respond ONLY with a JSON object. No markdown, no text before/after.\n"
            "Schema (ALL fields REQUIRED):\n"
            "{\n"
            '  "title": str,                     # Update name WITHOUT version\n'
            '  "tldr": str,                      # 1-2 sentence summary\n'
            '  "discord_highlights": [str],      # 3-6 short bullet points for Discord\n'
            '  "web_content": str,               # Full markdown content for website\n'
            '  "summary": str,                   # Short intro paragraph\n'
            '  "changes": [{"type": str, "description": str, "details": [str]}],\n'
            '  "seo_keywords": [str]\n'
            "}\n"
            "IMPORTANT: 'title' must contain ONLY the update name, NO version.\n"
            "IMPORTANT: 'discord_highlights' are 3-6 punchy bullet points (max 100 chars).\n\n"
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
    """Sammle aktive Feature-Branches mit Fortschritt für 'Demnächst'-Sektion."""
    project_path = ctx.project_config.get('path', '')
    if not project_path:
        return ""

    deploy_branch = ctx.project_config.get('deploy', {}).get('branch', 'main')
    min_commits = 5  # Nur Branches mit genug Substanz

    try:
        result = subprocess.run(
            ['git', 'branch', '-r', '--list', 'origin/feat/*', 'origin/fix/*'],
            capture_output=True, text=True, timeout=5, cwd=project_path,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return ""

        branches = [b.strip() for b in result.stdout.strip().split('\n')
                     if b.strip() and '->' not in b]
        if not branches:
            return ""

        teasers = []
        for branch in branches[:8]:
            # Commits ahead of deploy branch zählen
            count_r = subprocess.run(
                ['git', 'rev-list', '--count', f'origin/{deploy_branch}..{branch}'],
                capture_output=True, text=True, timeout=5, cwd=project_path,
            )
            if count_r.returncode != 0:
                continue
            try:
                ahead = int(count_r.stdout.strip())
            except (ValueError, AttributeError):
                continue
            if ahead < min_commits:
                continue

            # Feature-Highlights: feat:-Commits
            feat_r = subprocess.run(
                ['git', 'log', '--oneline', '--grep=^feat',
                 f'origin/{deploy_branch}..{branch}', '-3', '--format=%s'],
                capture_output=True, text=True, timeout=5, cwd=project_path,
            )
            highlights = []
            if feat_r.returncode == 0 and feat_r.stdout.strip():
                for line in feat_r.stdout.strip().splitlines()[:3]:
                    cleaned = re.sub(r'^feat(?:\([^)]*\))?:\s*', '', line.strip())
                    if cleaned:
                        highlights.append(cleaned)

            # Branch-Name + Fortschritt
            short = branch.replace('origin/', '')
            display = short.split('/')[-1].replace('-', ' ').replace('_', ' ').title()
            branch_type = 'Feature' if '/feat/' in branch else 'Fix'

            if ahead >= 20:
                progress = "weit fortgeschritten"
            elif ahead >= 10:
                progress = "in aktiver Entwicklung"
            else:
                progress = "in frühen Phasen"

            teaser = f"- [{branch_type}] **{display}** ({ahead} Commits, {progress})"
            if highlights:
                teaser += "\n  Highlights: " + ", ".join(highlights)
            teasers.append(teaser)

        if not teasers:
            return ""

        return (
            "FEATURES IN ENTWICKLUNG (aktive Branches, NICHT auf main):\n"
            + "\n".join(teasers) + "\n\n"
            "TEASER-ANWEISUNGEN:\n"
            "→ Füge am Ende eine '🔮 Demnächst'-Sektion ein.\n"
            "→ Formuliere spannend aber EHRLICH — Features sind NOCH NICHT LIVE.\n"
            "→ 'Wir arbeiten an...', 'Demnächst erwartet euch...', 'Stay tuned für...'\n"
            "→ Max 2-3 Sätze pro Feature. Mache Lust auf das nächste Update!"
        )
    except Exception:
        return ""
