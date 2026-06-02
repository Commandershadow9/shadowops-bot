"""Tests fuer scripts/ki-cost-watchdog.py — korrektes Cache-Token-Pricing (#292).

Anthropic bepreist Cache-Reads real ~0.1x des Input-Preises und Cache-Writes
~1.25x. Bisher wurden alle drei Input-Kategorien (input + cache_creation +
cache_read) zum vollen Input-Preis summiert, was die notionalen Kosten massiv
ueberschaetzt. Diese Tests fixieren das korrigierte Verhalten.

Geladen wird das Script (Dateiname mit Bindestrich) per importlib — pro Aufruf
frisch, damit ENV-Preis-Overrides beim Modul-Import greifen.
"""

import importlib.util
import json
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "ki-cost-watchdog.py"


def _load():
    """Laedt ki-cost-watchdog.py frisch als Modul (PRICES wird beim Import gebaut)."""
    spec = importlib.util.spec_from_file_location("ki_cost_watchdog", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_reiner_input_voller_preis():
    mod = _load()
    # 1M reine Input-Token Opus = $15.00
    assert round(mod.compute_cost("claude_opus", 1_000_000, 0, 0, 0), 2) == 15.00


def test_cache_read_ist_billiger_als_input():
    mod = _load()
    full = mod.compute_cost("claude_opus", 1_000_000, 0, 0, 0)
    cread = mod.compute_cost("claude_opus", 0, 0, 1_000_000, 0)
    # Cache-Read = 0.1x Input → $1.50
    assert round(cread, 2) == 1.50
    assert cread < full


def test_cache_write_ist_teurer_als_input():
    mod = _load()
    # Cache-Write = 1.25x Input → $18.75
    assert round(mod.compute_cost("claude_opus", 0, 1_000_000, 0, 0), 2) == 18.75


def test_output_normal_bepreist():
    mod = _load()
    assert round(mod.compute_cost("claude_opus", 0, 0, 0, 1_000_000), 2) == 75.00


def test_sonnet_cache_read():
    mod = _load()
    # Sonnet Input $3 → Cache-Read 0.1x = $0.30
    assert round(mod.compute_cost("claude_sonnet", 0, 0, 1_000_000, 0), 2) == 0.30


def test_env_override_cache_read(monkeypatch):
    monkeypatch.setenv("PRICE_CLAUDE_OPUS_CACHE_READ", "0.5")
    mod = _load()
    assert round(mod.compute_cost("claude_opus", 0, 0, 1_000_000, 0), 2) == 0.50


def test_collect_claude_wendet_cache_discount_an(tmp_path, monkeypatch):
    """collect_claude: Token-ZAHL bleibt voll, aber Kosten spiegeln den Cache-Rabatt."""
    day = "2026-06-02"
    proj = tmp_path / "projects" / "p"
    proj.mkdir(parents=True)
    line = {
        "timestamp": f"{day}T10:00:00Z",
        "message": {
            "id": "msg1",
            "model": "claude-opus-4",
            "usage": {
                "input_tokens": 0,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 1_000_000,
                "output_tokens": 0,
            },
        },
    }
    (proj / "s.jsonl").write_text(json.dumps(line) + "\n", encoding="utf-8")

    monkeypatch.setenv("KICOST_DAY", day)
    mod = _load()
    monkeypatch.setattr(mod, "CLAUDE_GLOBS", [str(tmp_path / "projects" / "**" / "*.jsonl")])

    agg = mod.collect_claude(day)
    # Token-Zahl unveraendert (Anomalie-Signal bleibt korrekt)
    assert agg["tokens_in"] == 1_000_000
    # Kosten = 0.1x Opus-Input statt voller $15
    assert round(agg["cost"], 2) == 1.50
