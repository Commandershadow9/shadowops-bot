"""Docs-only urteilt nach den Mustern des Workflows, nicht nach einer Kopie (ZERODOX#3331).

Die Frage "ist dieser Merge reine Doku?" wurde an zwei Stellen unabhaengig
beantwortet:

* `hard_gate_docs_only_bypass` in ZERODOX/scripts/deploy.sh liest `paths-ignore`
  zur Laufzeit aus `web-quality.yml` — diese Seite kann nicht driften.
* `_paths_are_docs_only` im Bot trug eine hartkodierte Kopie von DREI Mustern,
  waehrend der Workflow DREISSIG hat.

Nicht abgedeckt waren u.a. `maintenance/**`, `web/public/**/*.svg`, `**.md`
ausserhalb des Roots, `.gitignore`, `LICENSE`. Ein Merge, der nur ein SEO-Bild
austauscht, startet `web-quality.yml` per `paths-ignore` nicht — der Bot hielt
ihn fuer Laufzeitcode und lief in den Fehlschlag.

⚠️ Die Muster hier sind bewusst NICHT aus web-quality.yml kopiert, sondern
zitieren nur einzelne davon als Eingabe. Eine Kopie waere die dritte Wahrheit.
"""
import pytest

from src.integrations.github_integration.ci_mixin import (
    _glob_zu_regex,
    _paths_are_docs_only,
    _pfad_passt_auf_muster,
)

# Auszug aus dem `paths-ignore`-Block, wie ihn die API liefert.
_MUSTER = [
    "**.md",
    "docs/**",
    "CLAUDE.md",
    ".gitignore",
    ".editorconfig",
    "LICENSE",
    "maintenance/**",
    "web/public/**/*.svg",
    "web/public/**/*.woff2",
    "web/public/robots.txt",
    "web/public/sitemap*.xml",
    ".github/ISSUE_TEMPLATE/**",
]


class TestGlobUebersetzung:
    def test_doppelstern_ueberspringt_verzeichnisse(self):
        assert _pfad_passt_auf_muster("docs/a/b/c.txt", ["docs/**"])
        assert _pfad_passt_auf_muster("web/public/bilder/tief/x.svg", ["web/public/**/*.svg"])

    def test_einzelstern_bleibt_im_segment(self):
        """Der Unterschied, an dem eine naive Uebersetzung scheitert."""
        assert _pfad_passt_auf_muster("web/public/sitemap-1.xml", ["web/public/sitemap*.xml"])
        assert not _pfad_passt_auf_muster(
            "web/public/unter/sitemap.xml", ["web/public/sitemap*.xml"]
        ), "`*` darf keinen Schraegstrich ueberspringen — sonst gilt Fremdes als Doku."

    def test_doppelstern_am_anfang_trifft_jede_tiefe(self):
        assert _pfad_passt_auf_muster("README.md", ["**.md"])
        assert _pfad_passt_auf_muster("web/docs/tief/x.md", ["**.md"])

    def test_exakte_datei(self):
        assert _pfad_passt_auf_muster(".gitignore", [".gitignore"])
        assert not _pfad_passt_auf_muster("web/.gitignore", [".gitignore"])

    def test_punkt_ist_kein_platzhalter(self):
        """`.` muss escaped sein, sonst trifft `LICENSE` auch `LICENSEX`."""
        assert not _pfad_passt_auf_muster("axgitignore", [".gitignore"])

    def test_regex_ist_verankert(self):
        muster = _glob_zu_regex("docs/**")
        assert muster.startswith("^") and muster.endswith("$"), (
            "Ohne Anker traefe `docs/**` auch `web/docs/…` — und das ist "
            "Laufzeitcode in einem Verzeichnis mit aehnlichem Namen."
        )


class TestUrteil:
    def test_seo_bild_gilt_jetzt_als_doku(self):
        """Der Fall aus dem Issue: frueher Fehlschlag, jetzt korrekt erkannt."""
        assert _paths_are_docs_only(["web/public/logo.svg"], _MUSTER)
        assert not _paths_are_docs_only(["web/public/logo.svg"]), (
            "Ohne Muster greift die enge Heuristik — sie kennt das Bild nicht. "
            "Genau diese Luecke beschreibt #3331."
        )

    def test_maintenance_gilt_als_doku(self):
        assert _paths_are_docs_only(["maintenance/systemd/x.timer"], _MUSTER)

    def test_code_bleibt_code(self):
        assert not _paths_are_docs_only(["web/src/app/page.tsx"], _MUSTER)

    def test_gemischt_ist_kein_docs_only(self):
        assert not _paths_are_docs_only(["docs/a.md", "web/src/lib/x.ts"], _MUSTER), (
            "Eine einzige Laufzeitdatei macht den Merge deploy-pflichtig."
        )

    def test_leere_liste_ist_nie_docs_only(self):
        assert not _paths_are_docs_only([], _MUSTER)

    def test_ohne_muster_bleibt_die_alte_heuristik(self):
        """Fail-closed: 'keine Muster gelesen' darf nicht 'alles ist Doku' heissen."""
        assert _paths_are_docs_only(["docs/a.md"], None)
        assert not _paths_are_docs_only(["maintenance/x.sh"], None)
        assert not _paths_are_docs_only(["web/public/logo.svg"], [])

    def test_alte_heuristik_bleibt_enger_als_die_muster(self):
        """Die Richtung des Irrtums ist entscheidend.

        Ohne Muster darf die Funktion hoechstens zu WENIG als Doku erkennen —
        das kostet einen ueberfluessigen Deploy. Zu viel zu erkennen hiesse,
        Laufzeitcode ohne CI auszuliefern.
        """
        nur_mit_mustern = ["maintenance/x.sh", "web/public/a.svg", "web/tief/b.md"]
        for pfad in nur_mit_mustern:
            assert _paths_are_docs_only([pfad], _MUSTER), pfad
            assert not _paths_are_docs_only([pfad]), (
                f"{pfad} darf ohne Muster NICHT als Doku gelten."
            )
