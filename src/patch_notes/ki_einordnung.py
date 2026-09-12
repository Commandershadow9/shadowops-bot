"""Ordnet Commits ein, deren Titel kein erkennbares Muster hat.

Die Gruppierung in `grouping.py` liest den Typ aus dem Conventional-Präfix
(`feat(hero): …`), aus PR-Labels oder aus einer Liste englischer Verben. Bleibt
alles davon erfolglos, gilt der Commit als OTHER — und alle OTHER-Commits eines
Laufs landen gesammelt in einer Gruppe „Sonstiges".

Das ist folgenreich, weil aus den Gruppen die Gliederung des veröffentlichten
Textes entsteht: Bei avunex-neustart galten 28 von 60 Commits als OTHER, der
erste Changelog hatte dadurch eine einzige Gruppe.

Dieses Modul schließt die Lücke mit einem einzigen KI-Aufruf pro Lauf: Es legt
der KI nur die offenen Titel vor und übernimmt deren Einordnung.

Drei Festlegungen, die nicht zufällig sind:

* **Deterministisches zuerst.** Nur wo Präfix, Label und Verbliste versagen,
  wird gefragt. Ein Commit mit `fix:` kostet nie einen KI-Aufruf.
* **Ab einer Schwelle.** Unter `_MINDESTENS_OFFEN` offenen Titeln lohnt der
  Aufruf nicht — eine einzelne Zeile in „Sonstiges" schadet der Gliederung
  nicht.
* **Ausfall ist unschädlich.** Fällt die KI aus, antwortet sie unbrauchbar oder
  nennt einen unbekannten Typ, bleibt der Commit OTHER. Der Lauf geht weiter;
  das Ergebnis ist dann das bisherige, nicht ein schlechteres.
"""
from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from patch_notes.context import PipelineContext

logger = logging.getLogger('shadowops')

# Unter so vielen offenen Titeln wird nicht gefragt.
_MINDESTENS_OFFEN = 3

# Mehr Titel als das werden nicht vorgelegt — der Prompt bliebe sonst
# unbegrenzt. Die verbleibenden behalten OTHER.
_HOECHSTENS_OFFEN = 120

# Nur diese Werte werden übernommen. Alles andere gilt als unbrauchbare
# Antwort und lässt den Commit bei OTHER — eine erfundene Kategorie wäre
# schlimmer als eine neutrale.
_ERLAUBTE_TAGS = {
    'FEATURE', 'BUGFIX', 'IMPROVEMENT', 'DOCS', 'INFRASTRUCTURE',
    'TEST', 'DEPS', 'BREAKING', 'REVERT',
}

_ANWEISUNG = """Ordne jeden Commit-Titel genau einer Kategorie zu.

Kategorien:
FEATURE        neue Funktion, neuer Inhalt, etwas kommt hinzu
BUGFIX         behebt ein falsches Verhalten
IMPROVEMENT    verbessert, vereinfacht, entfernt oder benennt Bestehendes um
DOCS           Dokumentation, Kommentare, Notizen
INFRASTRUCTURE Betrieb, Deployment, Container, CI
TEST           Tests
DEPS           Abhängigkeiten, Versionssprünge
BREAKING       verändert bestehendes Verhalten grundlegend
REVERT         nimmt eine frühere Änderung zurück

Antworte NUR mit einem JSON-Objekt: die Nummer als Schlüssel, die Kategorie als
Wert. Kein Text davor oder danach, keine Erklärung.

Beispiel: {"1": "FEATURE", "2": "BUGFIX"}

Bist du bei einem Titel unsicher, lass die Nummer weg. Eine fehlende Zuordnung
ist besser als eine geratene.

Titel:
"""


def offene_commits(commits: list[dict]) -> list[dict]:
    """Commits, die ohne KI als OTHER gelten würden."""
    from patch_notes.grouping import classify_commit
    return [c for c in commits if classify_commit(c) == 'OTHER']


def _titel(commit: dict) -> str:
    return (commit.get('message') or '').split('\n')[0].strip()


def _antwort_lesen(rohtext: str) -> dict[int, str]:
    """Zieht die Zuordnung aus der KI-Antwort. Unbrauchbares wird verworfen."""
    if not rohtext:
        return {}

    # Die KI rahmt JSON gern in ```json … ``` ein.
    treffer = re.search(r'\{.*\}', rohtext, re.DOTALL)
    if not treffer:
        return {}
    try:
        rohdaten = json.loads(treffer.group(0))
    except (ValueError, TypeError):
        return {}
    if not isinstance(rohdaten, dict):
        return {}

    ergebnis: dict[int, str] = {}
    for schluessel, wert in rohdaten.items():
        try:
            nummer = int(str(schluessel).strip())
        except (ValueError, TypeError):
            continue
        tag = str(wert).strip().upper()
        if tag in _ERLAUBTE_TAGS:
            ergebnis[nummer] = tag
    return ergebnis


def _ai_service(bot):
    if bot is None:
        return None
    github = getattr(bot, 'github_integration', None)
    if github is None:
        return None
    return getattr(github, 'ai_service', None)


async def ordne_offene_commits_ein(ctx: 'PipelineContext', bot=None) -> int:
    """Setzt `_ki_tag` auf Commits ohne erkennbares Muster.

    Gibt die Zahl der zugeordneten Commits zurück. 0 bedeutet: nichts zu tun,
    kein KI-Dienst verfügbar, oder die Antwort war unbrauchbar — in allen
    Fällen bleibt das bisherige Verhalten unverändert.
    """
    commits = ctx.enriched_commits or ctx.raw_commits
    if not commits:
        return 0

    offen = offene_commits(commits)
    if len(offen) < _MINDESTENS_OFFEN:
        return 0

    dienst = _ai_service(bot)
    if dienst is None:
        logger.debug(
            f"[v6] {ctx.project}: {len(offen)} Commits ohne Muster, "
            f"aber kein KI-Dienst — bleiben Sonstiges"
        )
        return 0

    vorgelegt = offen[:_HOECHSTENS_OFFEN]
    zeilen = [f"{i + 1}. {_titel(c)}" for i, c in enumerate(vorgelegt)]
    prompt = _ANWEISUNG + '\n'.join(zeilen)

    try:
        roh = await dienst.get_raw_ai_response(prompt, use_critical_model=False)
    except Exception as e:
        logger.warning(f"[v6] {ctx.project}: KI-Einordnung fehlgeschlagen: {e}")
        return 0

    zuordnung = _antwort_lesen(roh if isinstance(roh, str) else '')
    if not zuordnung:
        logger.warning(
            f"[v6] {ctx.project}: KI-Einordnung lieferte keine "
            f"verwertbare Antwort — {len(offen)} Commits bleiben Sonstiges"
        )
        return 0

    gesetzt = 0
    for nummer, tag in zuordnung.items():
        if 1 <= nummer <= len(vorgelegt):
            vorgelegt[nummer - 1]['_ki_tag'] = tag
            gesetzt += 1

    logger.info(
        f"[v6] {ctx.project}: {gesetzt} von {len(offen)} Commits ohne Muster "
        f"per KI eingeordnet"
    )
    return gesetzt
