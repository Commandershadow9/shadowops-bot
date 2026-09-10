"""Wächter für _iter_source_files (ZERODOX #3260-Nachlauf, 10.09.2026).

WAS HIER SCHIEFGING
-------------------
Die Analyse läuft synchron im asyncio-Event-Loop. Am 10.09.2026 blockierte sie
ihn 7 Minuten 55 Sekunden (Journal 13:21:21 → 13:29:16) und meldete für
ZERODOX 327.161 Dateien mit 81,6 Mio. Zeilen — bei 2.453 Dateien in web/src.

Zwei Fehler übereinander:

1. `.claude` fehlte in `_skip_dirs`. Darunter liegen 40 isolierte Arbeitskopien
   des Repos, jede mit vollständigem web/src — dieselben Quellen vierzigmal.
2. Der Filter griff NACH `rglob`, also nach dem Abstieg. Er verwarf Treffer,
   sparte aber keine Zeit: `node_modules` wurde vollständig durchlaufen und
   danach weggeworfen.

Discord gibt einer Slash-Command-Interaktion 3 Sekunden. In diesen 8 Minuten
beantwortete der Bot KEINEN Befehl und meldete „Die Anwendung reagiert nicht".

Gemessen nach dem Fix: 14.360 Dateien in 0,13 s statt 327.169 in 91 s.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from integrations.code_analyzer import CodeAnalyzer  # noqa: E402


@pytest.fixture
def projekt(tmp_path: Path) -> Path:
    """Ein Projekt mit genau den Fallen, die den Vorfall ausgelöst haben."""
    # Echte Quelle — muss gefunden werden
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "echt.ts").write_text("export const a = 1;\n")
    (tmp_path / "src" / "tief").mkdir()
    (tmp_path / "src" / "tief" / "auch_echt.tsx").write_text("export const b = 2;\n")

    # Abhängigkeiten — dürfen weder gezählt noch betreten werden
    tief = tmp_path / "node_modules" / "irgendwas" / "dist" / "sub"
    tief.mkdir(parents=True)
    (tief / "fremd.js").write_text("module.exports = {};\n")

    # Arbeitskopien — der eigentliche Auslöser des Vorfalls. Dieselbe Datei
    # noch einmal, unter .claude/worktrees.
    kopie = tmp_path / ".claude" / "worktrees" / "feature-x" / "src"
    kopie.mkdir(parents=True)
    (kopie / "echt.ts").write_text("export const a = 1;\n")
    (kopie / "auch_echt.tsx").write_text("export const b = 2;\n")

    return tmp_path


def _gefunden(projekt: Path) -> set[str]:
    analyzer = CodeAnalyzer(str(projekt))
    return {p.name for p in analyzer._iter_source_files([projekt])}


def test_findet_echte_quellen(projekt: Path):
    assert _gefunden(projekt) == {"echt.ts", "auch_echt.tsx"}


def test_node_modules_wird_nicht_gezaehlt(projekt: Path):
    assert "fremd.js" not in _gefunden(projekt)


def test_arbeitskopien_werden_nicht_doppelt_gezaehlt(projekt: Path):
    """Der Kern des Vorfalls: .claude/worktrees darf nicht mitzählen.

    Ohne den Ausschluss lieferte der Durchlauf jede Datei zweimal — bei 40
    Arbeitskopien vierzigmal, und genau daraus entstanden die gemeldeten
    327.161 Dateien.
    """
    treffer = [p for p in CodeAnalyzer(str(projekt))._iter_source_files([projekt])]
    assert len(treffer) == 2, f"erwartet 2 Dateien, gefunden {len(treffer)}: {treffer}"
    assert not any(".claude" in p.parts for p in treffer)


def test_steigt_nicht_in_uebersprungene_verzeichnisse_ab(projekt: Path, monkeypatch):
    """Prüft das PRUNING, nicht nur das Ergebnis.

    Ein Filter nach dem Fund liefert dieselbe Dateiliste wie ein Pruning vor
    dem Abstieg — der Unterschied liegt allein in der Laufzeit, und genau der
    war der Vorfall. Deshalb wird hier gezählt, welche Verzeichnisse `os.walk`
    überhaupt anfasst: Taucht `node_modules` darin auf, ist die alte Fassung
    zurück, obwohl alle anderen Tests grün blieben.
    """
    import integrations.code_analyzer as modul

    besucht: list[str] = []
    echtes_walk = modul.os.walk

    def messendes_walk(top, *args, **kwargs):
        for wurzel, verzeichnisse, dateien in echtes_walk(top, *args, **kwargs):
            besucht.append(str(wurzel))
            yield wurzel, verzeichnisse, dateien

    monkeypatch.setattr(modul.os, "walk", messendes_walk)
    list(CodeAnalyzer(str(projekt))._iter_source_files([projekt]))

    assert besucht, "os.walk wurde gar nicht aufgerufen — Implementierung geändert?"
    assert not any("node_modules" in p for p in besucht), (
        "node_modules wurde betreten — das Pruning greift nicht mehr"
    )
    assert not any(".claude" in p for p in besucht), (
        ".claude wurde betreten — der Worktree-Ausschluss greift nicht mehr"
    )
