"""Tests für config-driven Templates."""
import pytest
from patch_notes.templates import get_template
from patch_notes.templates.base import BaseTemplate
from patch_notes.templates.gaming import GamingTemplate
from patch_notes.templates.saas import SaaSTemplate
from patch_notes.templates.devops import DevOpsTemplate
from patch_notes.context import PipelineContext

@pytest.fixture
def gaming_ctx():
    return PipelineContext(
        project="mayday_sim",
        project_config={"patch_notes": {
            "type": "gaming", "language": "de",
            "target_audience": "Gamer", "project_description": "Leitstellensim",
        }},
        raw_commits=[], trigger="cron",
        groups=[
            {"tag": "FEATURE", "theme": "BOS-Funk", "scope": "gameplay",
             "commits": [{"message": "feat: BOS radio"}], "summary": "Funkverkehr",
             "is_player_facing": True, "pr_labels": []},
            {"tag": "INFRASTRUCTURE", "theme": "Event-System", "scope": "events",
             "commits": [{"message": "feat(events): store"}] * 30, "summary": "CQRS",
             "is_player_facing": False, "pr_labels": []},
        ],
        version="0.21.0", update_size="major",
    )

def test_get_template_gaming():
    assert isinstance(get_template("gaming"), GamingTemplate)

def test_get_template_saas():
    assert isinstance(get_template("saas"), SaaSTemplate)

def test_get_template_devops():
    assert isinstance(get_template("devops"), DevOpsTemplate)

def test_get_template_unknown_falls_back():
    assert isinstance(get_template("unknown_type"), BaseTemplate)

def test_gaming_categories():
    cats = GamingTemplate().categories()
    assert "Neuer Content" in cats
    assert "Gameplay-Verbesserungen" in cats

def test_saas_tone():
    tone = SaaSTemplate().tone_instruction()
    assert "sachlich" in tone.lower() or "professionell" in tone.lower()

def test_build_prompt_contains_groups(gaming_ctx):
    prompt = GamingTemplate().build_prompt(gaming_ctx)
    assert "BOS-Funk" in prompt
    assert "Event-System" in prompt
    assert "mayday_sim" in prompt
    assert "0.21.0" in prompt

def test_build_prompt_player_facing_first(gaming_ctx):
    prompt = GamingTemplate().build_prompt(gaming_ctx)
    pf_pos = prompt.index("Spieler-/Nutzer-relevante")
    infra_pos = prompt.index("Infrastruktur / Backend")
    assert pf_pos < infra_pos

def test_length_limits_scale():
    t = GamingTemplate()
    small = t.length_limits("small")
    major = t.length_limits("major")
    assert major["max"] > small["max"]
