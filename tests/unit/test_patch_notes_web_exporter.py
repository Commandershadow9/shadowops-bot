"""Tests fuer PatchNotesWebExporter."""

import json

from integrations.patch_notes_web_exporter import PatchNotesWebExporter


def test_exporter_writes_editorial_fields_to_json_and_markdown(tmp_path):
    exporter = PatchNotesWebExporter(tmp_path)
    changes = [{
        "type": "feature",
        "title": "BOS-Funk",
        "description": "Funk verbessert",
        "impact": "Dispatcher sehen Statuswechsel direkt im Einsatz.",
        "before": "Status war nur indirekt sichtbar.",
        "after": "Statuswechsel erscheinen direkt im Einsatzkontext.",
        "why": "Das reduziert Rueckfragen in parallelen Einsaetzen.",
        "user_action": "Keine",
        "is_hero": True,
        "source_commits": ["add radio status"],
        "details": ["Konkrete Statusanzeige im Einsatz"],
        "author": "Shadow",
    }]

    result = exporter.export(
        project="mayday_sim",
        version="1.2.3",
        title="Funk-Update",
        tldr="Funkstatus ist sichtbarer.",
        content="## Intro\nMehr Kontrolle im Einsatz.",
        stats={"commits": 6, "files_changed": 4, "lines_added": 120, "lines_removed": 12},
        language="de",
        changes=changes,
        seo_keywords=["mayday", "funk"],
    )

    json_data = json.loads(result["json"].read_text(encoding="utf-8"))
    md = result["markdown"].read_text(encoding="utf-8")

    assert json_data["changes"][0]["impact"] == "Dispatcher sehen Statuswechsel direkt im Einsatz."
    assert json_data["changes"][0]["is_hero"] is True
    assert "## Highlights" in md
    assert "### BOS-Funk" in md
    assert "Vorher" in md
    assert "Änderungen im Detail" in md
