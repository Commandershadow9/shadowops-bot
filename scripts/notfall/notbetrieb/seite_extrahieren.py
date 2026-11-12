#!/usr/bin/env python3
"""Löst den Inhalt von <main id="main-content"> aus einer gerenderten ZERODOX-Seite
und baut daraus eine eigenständige Notbetriebs-Seite (ZERODOX #5629).

Positivliste statt Filter: Nur die Tags unten bleiben erhalten, alle Attribute
fallen weg (ausser einem geprüften href). Skripte, Stile, Formulare, Bilder und
SVG verschwinden samt Inhalt. Die Ausgabe lädt nichts nach und läuft unter
`default-src 'none'`.

Aufruf: seite_extrahieren.py <titel> <eingabe.html> <vorlage.html> > ausgabe.html
Exit 2, wenn kein main-content gefunden wurde oder der Text verdächtig kurz ist.
"""
import html
import re
import sys
from html.parser import HTMLParser

ERLAUBT = {
    "h1", "h2", "h3", "h4", "h5", "p", "ul", "ol", "li", "a", "strong", "em", "b", "i",
    "br", "hr", "table", "thead", "tbody", "tr", "td", "th", "section", "article",
    "div", "span", "dl", "dt", "dd", "address", "blockquote", "small", "sup", "sub",
}
LEER = {"br", "hr"}
WEG_MIT_INHALT = {"script", "style", "noscript", "svg", "form", "button", "iframe",
                  "template", "select", "textarea", "nav", "picture", "video", "audio"}
HREF_OK = re.compile(r"^(https://|mailto:|/(?!/))[^\s\"'<>]*$")
MINDESTTEXT = 400


class Extraktor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tiefe_main = 0   # >0 = innerhalb von main-content
        self.weg = 0          # >0 = innerhalb eines verworfenen Elements
        self.aus: list[str] = []
        self.text = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if self.tiefe_main == 0:
            if tag == "main" and a.get("id") == "main-content":
                self.tiefe_main = 1
            return
        if tag == "main":
            self.tiefe_main += 1
            return
        if self.weg or tag in WEG_MIT_INHALT:
            if tag not in LEER and tag not in {"img", "input", "source"}:
                self.weg += 1
            return
        if tag not in ERLAUBT:
            return
        if tag == "a":
            href = (a.get("href") or "").strip()
            self.aus.append(f'<a href="{html.escape(href)}">' if HREF_OK.match(href) else "<a>")
        else:
            self.aus.append(f"<{tag}>")

    def handle_startendtag(self, tag, attrs):
        if self.tiefe_main and not self.weg and tag in LEER:
            self.aus.append(f"<{tag}>")

    def handle_endtag(self, tag):
        if self.tiefe_main == 0:
            return
        if tag == "main":
            self.tiefe_main -= 1
            return
        if self.weg:
            if tag in WEG_MIT_INHALT or tag not in LEER:
                self.weg -= 1
            return
        if tag in ERLAUBT and tag not in LEER:
            self.aus.append(f"</{tag}>")

    def handle_data(self, data):
        if self.tiefe_main and not self.weg:
            self.aus.append(html.escape(data, quote=False))
            self.text += len(data.strip())


def main() -> int:
    titel, eingabe, vorlage = sys.argv[1:4]
    p = Extraktor()
    with open(eingabe, encoding="utf-8", errors="replace") as f:
        p.feed(f.read())
    if p.text < MINDESTTEXT:
        print(f"zu wenig Inhalt ({p.text} Zeichen) — Seite nicht ersetzt", file=sys.stderr)
        return 2
    inhalt = re.sub(r"(<div>\s*</div>|<span>\s*</span>)", "", "".join(p.aus))
    with open(vorlage, encoding="utf-8") as f:
        seite = f.read()
    sys.stdout.write(seite.replace("{{TITEL}}", html.escape(titel)).replace("{{INHALT}}", inhalt))
    return 0


if __name__ == "__main__":
    sys.exit(main())
