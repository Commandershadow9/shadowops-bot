import pytest
from integrations.security_engine.fingerprint import (
    compute_finding_fingerprint, normalize_files, extract_signature_keywords
)


class TestNormalizeFiles:
    def test_sorts_and_lowercases(self):
        assert normalize_files(["src/B.py", "src/a.py"]) == ("src/a.py", "src/b.py")

    def test_empty(self):
        assert normalize_files([]) == ()

    def test_none(self):
        assert normalize_files(None) == ()

    def test_strips_whitespace(self):
        assert normalize_files(["  src/a.py  "]) == ("src/a.py",)


class TestExtractSignatureKeywords:
    def test_extracts_tech_terms(self):
        text = "ImageMagick Security-Updates auf Debian-Host aussetzen"
        kws = extract_signature_keywords(text)
        assert "imagemagick" in kws
        assert "debian" in kws

    def test_ignores_stopwords(self):
        text = "Die aktuelle Situation ist nicht optimal"
        assert extract_signature_keywords(text) == ()

    def test_max_three_keywords(self):
        text = "imagemagick debian ubuntu redhat alpine container docker"
        assert len(extract_signature_keywords(text)) == 3

    def test_extracts_german_umlauts(self):
        kws = extract_signature_keywords("Prüfsumme für Übergabe fehlt")
        assert "prüfsumme" in kws
        assert "übergabe" in kws


class TestComputeFingerprint:
    def test_same_category_project_files_same_fingerprint(self):
        fp1 = compute_finding_fingerprint(
            category="dependencies",
            affected_project="infrastructure",
            affected_files=["Dockerfile"],
            title="ImageMagick Security-Updates auf Debian-Host",
        )
        fp2 = compute_finding_fingerprint(
            category="dependencies",
            affected_project="infrastructure",
            affected_files=["Dockerfile"],
            title="Debian Security-Update: ImageMagick",  # andere Formulierung, gleiches Problem
        )
        assert fp1 == fp2

    def test_different_project_different_fingerprint(self):
        fp1 = compute_finding_fingerprint("dependencies", "infrastructure", ["Dockerfile"], "X imagemagick")
        fp2 = compute_finding_fingerprint("dependencies", "guildscout", ["Dockerfile"], "X imagemagick")
        assert fp1 != fp2

    def test_different_category_different_fingerprint(self):
        fp1 = compute_finding_fingerprint("dependencies", "p", [], "x imagemagick")
        fp2 = compute_finding_fingerprint("secrets", "p", [], "x imagemagick")
        assert fp1 != fp2

    def test_fingerprint_is_40char_hex(self):
        fp = compute_finding_fingerprint("cat", "proj", [], "title")
        assert len(fp) == 40
        int(fp, 16)  # darf nicht werfen

    def test_order_independent_files(self):
        fp1 = compute_finding_fingerprint("c", "p", ["a.py", "b.py"], "t imagemagick")
        fp2 = compute_finding_fingerprint("c", "p", ["b.py", "a.py"], "t imagemagick")
        assert fp1 == fp2

    def test_order_independent_title_keywords(self):
        fp1 = compute_finding_fingerprint("c", "p", [], "imagemagick debian")
        fp2 = compute_finding_fingerprint("c", "p", [], "debian imagemagick")
        assert fp1 == fp2

    def test_umlaut_title_same_fingerprint(self):
        # Kern-Check: mit Umlaut-Support im Regex produzieren semantisch gleiche
        # Titel identische Keywords ("prüfsumme", "größe") und damit identische
        # Fingerprints. Ohne Umlaut-Support wuerden beide auf Artefakt-Tokens
        # ("fsumme", "e") reduziert, was zu zufaelligen False-Matches fuehrt.
        fp1 = compute_finding_fingerprint(
            "config", "zerodox", ["prisma/schema.prisma"],
            "Prüfsumme über Größe der Datei"
        )
        fp2 = compute_finding_fingerprint(
            "config", "zerodox", ["prisma/schema.prisma"],
            "Größe und Prüfsumme der Datei"
        )
        assert fp1 == fp2
