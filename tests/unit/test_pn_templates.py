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


# ── Narrative Prompt-Upgrade Tests (2026-04-15) ───────────────────


def test_audience_address_per_template():
    """Jeder Template-Typ hat seine eigene Anrede-Konvention."""
    assert GamingTemplate().audience_address() == "Dispatcher"
    assert SaaSTemplate().audience_address() == "Team"
    assert DevOpsTemplate().audience_address() == "Ops"


def test_mega_prompt_has_anti_patterns(gaming_ctx):
    """Mega-Prompt muss explizite Anti-Pattern-Liste enthalten (gegen Statistik-Listings)."""
    gaming_ctx.update_size = "mega"
    prompt = GamingTemplate().build_prompt(gaming_ctx)
    assert "ANTI-PATTERNS" in prompt
    assert "Statistik-Listing" in prompt
    assert "NIE ERFINDEN" in prompt


def test_mega_prompt_has_few_shot(gaming_ctx):
    """Mega-Prompt muss ein Few-Shot-Beispiel (wörtliches Muster) enthalten."""
    gaming_ctx.update_size = "mega"
    prompt = GamingTemplate().build_prompt(gaming_ctx)
    assert "REFERENZ-BEISPIEL" in prompt
    # Gaming-Few-Shot enthält "Dispatcher" als Anrede-Muster
    assert "Dispatcher," in prompt


def test_major_prompt_has_narrative_block(gaming_ctx):
    """Major bekommt auch den Narrative-Block (keine Duplikation mit major-Legacy)."""
    gaming_ctx.update_size = "major"
    prompt = GamingTemplate().build_prompt(gaming_ctx)
    assert "MAJOR-UPDATE MODUS" in prompt
    assert "STRUKTUR DES web_content" in prompt


def test_small_prompt_has_no_narrative_block(gaming_ctx):
    """Small/normal/big bleiben kompakt ohne Narrative-Regeln."""
    gaming_ctx.update_size = "small"
    prompt = GamingTemplate().build_prompt(gaming_ctx)
    assert "ANTI-PATTERNS" not in prompt
    assert "REFERENZ-BEISPIEL" not in prompt


def test_narrative_input_block_contains_time_window():
    """_narrative_input_block liefert Zeitfenster wenn enriched_commits Dates haben."""
    from patch_notes.context import PipelineContext
    ctx = PipelineContext(
        project="test", project_config={"patch_notes": {}},
        raw_commits=[], trigger="manual", update_size="mega",
        enriched_commits=[
            {"timestamp": "2026-04-01T10:00:00+00:00", "author": {"name": "Shadow"}},
            {"timestamp": "2026-04-04T18:00:00+00:00", "author": {"name": "Shadow"}},
        ],
        groups=[{"tag": "FEATURE", "theme": "X",
                 "commits": [{"author": {"name": "Shadow"}}] * 12}],
    )
    block = GamingTemplate()._narrative_input_block(ctx)
    assert "RELEASE-FAKTEN" in block
    assert "Zeitfenster" in block
    # 3 oder 4 Tage (abhängig vom Kalender-Cross)
    assert "Tage" in block or "Wochen" in block


def test_release_notes_reader_ignores_template_comments(tmp_path):
    """_read_release_notes darf nicht das Template-Kommentar als Content werten."""
    from patch_notes.templates.base import _read_release_notes
    (tmp_path / "release_notes.md").write_text(
        "<!-- Template-Kommentar mit Beispielen... -->\n", encoding="utf-8"
    )
    result = _read_release_notes(tmp_path)
    assert result == ""


def test_release_notes_reader_returns_content(tmp_path):
    """Echter Dev-Kontext wird zurückgegeben, Kommentare gestrippt."""
    from patch_notes.templates.base import _read_release_notes
    (tmp_path / "release_notes.md").write_text(
        "<!-- Template -->\n\nShadow hat drei Nächte am Refactor gesessen.\nDDD war die Lösung.\n",
        encoding="utf-8",
    )
    result = _read_release_notes(tmp_path)
    assert "Shadow hat drei Nächte" in result
    assert "Template" not in result


def test_group_author_facts_filters_bots():
    """AI-Autoren (Codex, Bot) werden rausgefiltert."""
    from patch_notes.templates.base import _group_author_facts
    groups = [{
        "tag": "FEATURE", "theme": "X",
        "commits": [
            {"author": {"name": "Shadow"}},
            {"author": {"name": "Shadow"}},
            {"author": {"name": "codex"}},  # filtered
            {"author": {"name": "ai-bot"}},  # filtered
        ],
    }]
    lines = _group_author_facts(groups)
    assert len(lines) == 1
    assert "Shadow (2)" in lines[0]
    assert "codex" not in lines[0].lower()
