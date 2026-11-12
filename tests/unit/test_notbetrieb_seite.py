"""
Tests für seite_extrahieren.py (ZERODOX #5629): Die Notbetriebs-Kopie von Impressum und
Datenschutz darf nichts ausführen und nichts nachladen, egal was die Quellseite enthält.
"""
import subprocess
import sys
from pathlib import Path

BASIS = Path(__file__).resolve().parents[2] / "scripts" / "notfall" / "notbetrieb"
LANG = "<p>" + "Angaben gemäß § 5 DDG. " * 30 + "</p>"


def _extrahiere(tmp_path, html):
    quelle = tmp_path / "q.html"
    quelle.write_text(html, encoding="utf-8")
    r = subprocess.run([sys.executable, str(BASIS / "seite_extrahieren.py"), "Impressum", str(quelle),
                        str(BASIS / "seite-vorlage.html")], capture_output=True, text=True, timeout=30)
    return r


def test_nur_main_content_und_kein_skript(tmp_path):
    r = _extrahiere(tmp_path, f"""<html><head><script>alert(1)</script></head><body>
      <nav>Menü</nav><main id="main-content" class="x"><h1 onclick="x()">Impressum</h1>{LANG}
      <script>steal()</script><img src=x onerror=alert(1)><svg><script>a()</script></svg>
      <a href="javascript:alert(1)">böse</a><a href="//evil.example/x">fremd</a>
      <a href="/agb">AGB</a><a href="mailto:info@zerodox.de">Mail</a>
      <div style="background:url(x)">Text &lt;b&gt;</div></main><footer>Fuß</footer></body></html>""")
    assert r.returncode == 0, r.stderr
    aus = r.stdout
    inhalt = aus.split("{{INHALT}}")[0] if "{{INHALT}}" in aus else aus
    for verboten in ("<script", "onclick", "onerror", "<img", "<svg", "javascript:", "//evil", "style=\"", "steal("):
        assert verboten not in inhalt.split("</style>", 1)[1], verboten
    assert '<a href="/agb">AGB</a>' in aus and 'href="mailto:info@zerodox.de"' in aus
    assert "Menü" not in aus and "Fuß" not in aus.replace("Datenschutz", "")
    assert "&lt;b&gt;" in aus and "<h1>Impressum</h1>" in aus


def test_zu_wenig_inhalt_ersetzt_nichts(tmp_path):
    r = _extrahiere(tmp_path, '<main id="main-content"><p>Fehler</p></main>')
    assert r.returncode == 2 and r.stdout == ""


def test_ohne_main_content_ersetzt_nichts(tmp_path):
    r = _extrahiere(tmp_path, f"<main>{LANG}</main>")
    assert r.returncode == 2
