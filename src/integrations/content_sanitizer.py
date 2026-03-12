"""
Content Sanitizer — Filtert sensible Informationen aus AI-generierten Patch Notes.

Entfernt automatisch:
  - Absolute/relative Dateipfade (Home, Var, Etc, Src, Config)
  - IP-Adressen und localhost-Referenzen
  - Port-Nummern und Config-Datei-Namen
  - API-Endpunkt-Pfade

Einsatz: Vor dem Versand von Patch Notes an Discord oder die Webseite.
"""

import logging
import re
from copy import deepcopy
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger('shadowops')

# Standard-Keys die in sanitize_dict bereinigt werden
DEFAULT_SANITIZE_KEYS = [
    'content', 'tldr', 'title', 'summary',
    'discord_highlights', 'web_content',
]


def _build_default_patterns() -> List[Tuple[re.Pattern, str]]:
    """Erstellt die Standard-Regex-Patterns fuer sensible Informationen."""
    return [
        # 1. Absolute Unix-Pfade: /home/..., /var/..., /etc/..., /tmp/..., /opt/..., /usr/...
        (re.compile(r'/(?:home|var|etc|tmp|opt|usr)(?:/[\w.\-]+)+'), ''),

        # 2. Tilde-Pfade: ~/shadowops-bot/..., ~/GuildScout/...
        (re.compile(r'~/[\w.\-]+(?:/[\w.\-]+)*'), ''),

        # 3. Relative Source-Pfade: src/..., tests/..., config/...
        (re.compile(r'(?:src|tests|config)/[\w.\-]+(?:/[\w.\-]+)*'), ''),

        # 4. IPv4-Adressen mit optionalem Port: 10.8.0.1, 172.23.0.5:5433
        (re.compile(r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?::\d+)?'), ''),

        # 5. localhost mit optionalem Port: localhost, localhost:5433
        (re.compile(r'localhost(?::\d+)?'), ''),

        # 6. Port-Referenzen: Port 5433, port 8766
        (re.compile(r'[Pp]ort\s+\d+'), ''),

        # 7. Config-Datei-Referenzen: config.yaml, .env, .env.production, credentials.json, secrets.json
        (re.compile(r'(?:config\.ya?ml|\.env(?:\.\w+)?|credentials\.json|secrets\.json)'), ''),

        # 8. API-Endpunkt-Pfade: /api/users/login, /api/changelogs
        (re.compile(r'/api(?:/[\w.\-]+)+'), ''),
    ]


class ContentSanitizer:
    """Filtert sensible Informationen aus Texten bevor sie veroeffentlicht werden."""

    def __init__(
        self,
        custom_patterns: Optional[List[str]] = None,
        enabled: bool = True,
    ):
        self.enabled = enabled
        self.patterns: List[Tuple[re.Pattern, str]] = _build_default_patterns()

        # Custom-Patterns am Ende anhaengen
        if custom_patterns:
            for pattern_str in custom_patterns:
                self.patterns.append((re.compile(pattern_str), ''))

        logger.debug(
            "ContentSanitizer initialisiert: %d Patterns, enabled=%s",
            len(self.patterns), self.enabled,
        )

    def sanitize(self, text: str) -> str:
        """Wendet alle Regex-Patterns auf den Text an und bereinigt das Ergebnis."""
        if not self.enabled or not text:
            return text

        result = text

        # Alle Patterns anwenden
        for pattern, replacement in self.patterns:
            result = pattern.sub(replacement, result)

        # Nachbereitung
        result = self._cleanup(result)

        return result

    def sanitize_dict(
        self,
        data: Dict,
        keys: Optional[List[str]] = None,
    ) -> Dict:
        """Wendet sanitize() rekursiv auf Dict-Felder an.

        Args:
            data: Das Dictionary mit zu bereinigenden Werten.
            keys: Liste von Keys die bereinigt werden sollen.
                  Default: DEFAULT_SANITIZE_KEYS
        """
        if not self.enabled:
            return deepcopy(data)

        target_keys = keys or DEFAULT_SANITIZE_KEYS
        result = deepcopy(data)

        for key in target_keys:
            if key not in result:
                continue

            value = result[key]

            if isinstance(value, str):
                result[key] = self.sanitize(value)
            elif isinstance(value, list):
                result[key] = [
                    self.sanitize(item) if isinstance(item, str) else item
                    for item in value
                ]

        return result

    def _cleanup(self, text: str) -> str:
        """Nachbereitung: Leerzeichen, leere Bullets, Leerzeilen bereinigen."""
        # Mehrfache Leerzeichen zu einem (aber nicht Newlines)
        text = re.sub(r'[^\S\n]+', ' ', text)

        # Leere Bullet-Points entfernen (Zeilen die nur Bullet-Zeichen haben)
        text = re.sub(r'^[ \t]*[•\-╰][ \t]*$', '', text, flags=re.MULTILINE)

        # Mehrfache Leerzeilen auf max 2 reduzieren
        text = re.sub(r'\n{3,}', '\n\n', text)

        # strip() am Ende
        text = text.strip()

        return text
