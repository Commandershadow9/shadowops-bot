"""
Regressionstests fuer den Reflection-Prompt der Security-Engine.

Bug (Journal 13.06. + 28.06.): Die Post-Scan-Reflection crashte reproduzierbar mit
``KeyError: '"quality_score"'``. Root-Cause: ``REFLECTION_PROMPT.format(...)`` lief ueber
ein Template, das ein JSON-Beispiel (``{"quality_score": ...}``) enthaelt — ``str.format()``
interpretiert die geschweiften Klammern als Replacement-Field.

Fix: ``render_reflection_prompt()`` nutzt ``.replace()`` statt ``.format()``.
"""

import pytest

from src.integrations.security_engine.prompts import (
    REFLECTION_PROMPT,
    render_reflection_prompt,
)


def test_old_format_call_would_crash():
    """Beweist den alten Crash: .format() auf dem ECHTEN Template wirft KeyError.

    Rot vor Fix (das war der Live-Aufruf), dokumentiert die Falle dauerhaft.
    """
    with pytest.raises(KeyError, match='quality_score'):
        REFLECTION_PROMPT.format(session_summary="X", weekly_context="Y")


def test_render_substitutes_both_placeholders():
    out = render_reflection_prompt("SESSION_MARKER", "WEEK_MARKER")
    assert "SESSION_MARKER" in out
    assert "WEEK_MARKER" in out
    # Keine unaufgeloesten Platzhalter mehr
    assert "{session_summary}" not in out
    assert "{weekly_context}" not in out


def test_render_preserves_json_example():
    """Das JSON-Beispiel (mit quality_score) muss unveraendert im Prompt bleiben."""
    out = render_reflection_prompt("s", "w")
    assert '{"quality_score": 75' in out
    assert '"blind_spots"' in out


def test_render_does_not_raise_with_braces_in_values():
    """Werte mit geschweiften Klammern (z.B. JSON im Session-Summary) brechen nichts."""
    summary = 'Session-Ergebnis: {"findings": 3, "fixes": 1}'
    out = render_reflection_prompt(summary, "Woche ok")
    assert summary in out
