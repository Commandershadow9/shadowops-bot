"""Tests für EmbedBuilder — Ton-Konsistenz mit dem Alert-Humanizer.

create_alert kann optional eine Dringlichkeits-Handlungszeile anhängen
(gemeinsamer Ton via alert_humanizer.urgency_line). Default-Verhalten bleibt
unverändert (add_urgency=False), damit bestehende Aufrufer nicht brechen.
"""
from __future__ import annotations

from src.utils.embeds import EmbedBuilder, Severity


def test_severity_to_urgency_mapping():
    # Vergleich über .name — EmbedBuilder kann je nach pythonpath ein anderes
    # Urgency-Objekt geladen haben (utils.* vs. src.utils.*), Wert ist identisch.
    assert EmbedBuilder.severity_to_urgency(Severity.CRITICAL).name == "CRITICAL"
    assert EmbedBuilder.severity_to_urgency(Severity.HIGH).name == "HIGH"
    assert EmbedBuilder.severity_to_urgency(Severity.MEDIUM).name == "MEDIUM"
    # Kein Handlungsbedarf
    assert EmbedBuilder.severity_to_urgency(Severity.INFO).name == "NONE"
    assert EmbedBuilder.severity_to_urgency(Severity.SUCCESS).name == "NONE"
    assert EmbedBuilder.severity_to_urgency(Severity.LOW).name == "NONE"


def test_create_alert_default_has_no_urgency_line():
    """Default (add_urgency=False) -> Beschreibung unverändert."""
    embed = EmbedBuilder.create_alert("Titel", "Beschreibung", severity=Severity.CRITICAL)
    assert embed.description == "Beschreibung"
    assert "Dringlichkeit" not in embed.description


def test_create_alert_with_urgency_appends_line():
    embed = EmbedBuilder.create_alert(
        "Titel", "Beschreibung", severity=Severity.HIGH, add_urgency=True
    )
    assert "Beschreibung" in embed.description
    assert "Dringlichkeit" in embed.description
    assert "hoch" in embed.description.lower()


def test_create_alert_with_urgency_info_no_line():
    """INFO -> Urgency.NONE -> keine Zeile, auch mit add_urgency=True."""
    embed = EmbedBuilder.create_alert(
        "Titel", "Beschreibung", severity=Severity.INFO, add_urgency=True
    )
    assert embed.description == "Beschreibung"


def test_create_alert_still_builds_emoji_title_and_fields():
    """Regression: Emoji-Titel, project_tag und Felder funktionieren weiter."""
    embed = EmbedBuilder.create_alert(
        "Titel",
        "Beschreibung",
        severity=Severity.CRITICAL,
        fields=[{"name": "A", "value": "B", "inline": True}],
        project_tag="🖥️ [SERVER]",
    )
    assert "[SERVER]" in embed.title
    assert "🔴" in embed.title
    assert embed.color.value == Severity.CRITICAL.color
    assert len(embed.fields) == 1
    assert embed.fields[0].name == "A"
