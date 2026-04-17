"""
Deterministische Fingerprints fuer Security-Findings.

Ersetzt die alte Titel-basierte Dedup (_find_similar_open_finding), die bei
semantisch gleichen aber anders formulierten Findings versagt hat.
"""
from __future__ import annotations
import hashlib
import re
from typing import Optional, Sequence

# Stopwords fuer Signature-Extraktion (DE + EN, alles lowercase)
_STOPWORDS = frozenset({
    "die", "der", "das", "und", "oder", "aber", "nicht", "ist", "sind",
    "den", "dem", "des", "auf", "mit", "fuer", "von", "bei", "zu", "aus", "über",
    "als", "auch", "eine", "einer", "eines", "einem", "einen", "ein",
    "the", "and", "but", "not", "for", "with", "from", "that", "this",
    "aktuelle", "neue", "alte", "aktuell", "neu",
    "optimal", "situation", "problem",
    "security", "update", "updates", "fix", "fixes",
    "auszusetzen", "aussetzen", "einspielen", "nachziehen",
    "host",
})

# Technische Signature-Keywords: Laenge >= 4, kein Stopword, alphanumerisch.
# Bindestriche trennen Tokens (debian-host -> debian, host) damit "debian" matcht.
# Deutsche Umlaute + ß werden als Wortzeichen behandelt — sonst wuerde "Prüfsumme"
# mitten im Wort gerissen ("fsumme") und identische Findings bekaemen unterschiedliche
# Fingerprints (Dedup-Miss + False-Matches auf Artefakt-Tokens).
_KEYWORD_RE = re.compile(r"[a-zA-ZäöüÄÖÜß][a-zA-Z0-9äöüÄÖÜß_]{3,}")


def normalize_files(files: Optional[Sequence[str]]) -> tuple[str, ...]:
    """Dateipfade: strip, lowercase, dedupe, sortiert -> deterministisch."""
    if not files:
        return ()
    normalized = sorted({f.strip().lower() for f in files if f and f.strip()})
    return tuple(normalized)


def extract_signature_keywords(text: str, max_keywords: int = 3) -> tuple[str, ...]:
    """Extrahiert bis zu N technische Keywords (Reihenfolge: Vorkommen)."""
    if not text:
        return ()
    seen: list[str] = []
    for match in _KEYWORD_RE.finditer(text.lower()):
        word = match.group(0).lower()
        if word in _STOPWORDS:
            continue
        if word in seen:
            continue
        seen.append(word)
        if len(seen) >= max_keywords:
            break
    return tuple(seen)


def compute_finding_fingerprint(
    category: str,
    affected_project: str,
    affected_files: Optional[Sequence[str]],
    title: str,
) -> str:
    """
    SHA1-Fingerprint aus (category, project, files, signature_keywords).

    Zwei Findings mit gleichem Fingerprint sind semantisch dasselbe Problem.
    """
    # Keywords hier sortiert (nicht in extract_*): Order-Independence im Fingerprint,
    # aber extract_signature_keywords behaelt Vorkommen-Reihenfolge fuer externe Nutzer
    keywords = sorted(extract_signature_keywords(title or ""))
    parts = [
        (category or "unknown").strip().lower(),
        (affected_project or "unknown").strip().lower(),
        "|".join(normalize_files(affected_files)),
        "|".join(keywords),
    ]
    payload = "\x1f".join(parts)  # ASCII Unit Separator
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()
