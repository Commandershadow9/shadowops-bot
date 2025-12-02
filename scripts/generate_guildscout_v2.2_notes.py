#!/usr/bin/env python3
"""
Generate detailed patch notes for GuildScout v2.2.0 using AI
"""

import asyncio
import sys
import os
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

import discord
from utils.config import Config
from integrations.ai_service import AIService


COMMITS = """
- feat: Combined dashboard + welcome message with auto-cleanup (by Shadow)
- fix: Add missing _live_log_initialized dict initialization (by Shadow)
- feat: Dashboard in #guild-rankings mit Commands + Live Activity (by Shadow)
- fix: Clean up logging and remove redundant messages (by Shadow)
- feat: Smart verification integration & dashboard improvements (by Shadow)
- feat: Import-Progress in Guild-Rankings mit Auto-Cleanup (by Shadow)
- fix: Protect import-status message from cleanup (by Shadow)
- feat: Persistent lifetime statistics across bot restarts (by Shadow)
- fix: Remove redundant import success notification (by Shadow)
- refactor: Rename ranking channel to dashboard channel (by Shadow)
- feat: Implement status channel with acknowledgment system (by Shadow)
- feat: Route verification errors to status channel (by Shadow)
- fix: Remove remaining log_channel_id references (by Shadow)
- fix: Complete removal of log channel code (by Shadow)
- feat: Implement delta import to catch missed messages during downtime (by Shadow)
- fix: Remove final log_channel_id references from bot.py (by Shadow)
- fix: Enable delta import on bot restart instead of force reimport (by Shadow)
- feat: Track last message timestamp for reliable delta imports (by Shadow)
- fix: Handle timezone-aware datetimes in delta import (by Shadow)
- fix: Import asyncio in delta import function (by Shadow)
- docs: Update documentation for v2.2.0 and finish log channel removal (by Shadow)
- fix: Resolve AttributeError in DiscordLogger by using status_channel_id (by Shadow)
- refactor: Remove verbose status channel logging from /analyze (by Shadow)
"""

PROMPT_DE = f"""Du bist ein professioneller Technical Writer. Erstelle DETAILLIERTE, benutzerfreundliche Patch Notes für das Projekt "GuildScout".

COMMITS (VOLLSTÄNDIGE LISTE):
{COMMITS}

KRITISCHE REGELN:
⚠️ BESCHREIBE NUR ÄNDERUNGEN DIE WIRKLICH IN DEN COMMITS OBEN STEHEN!
⚠️ ERFINDE KEINE FEATURES ODER FIXES DIE NICHT IN DER COMMIT-LISTE SIND!
⚠️ Wenn ein Commit unklar ist, überspringe ihn lieber als zu raten!

WICHTIG - ZUSAMMENHÄNGENDE FEATURES ERKENNEN:
🔍 Suche nach VERWANDTEN Commits die zusammengehören (z.B. mehrere "fix:" oder "feat:" Commits für das gleiche Feature)
🔍 Commit-Serien wie "Delta Import", "Dashboard", "Status Channel" sind EINZELNE Features, nicht getrennte Punkte!
🔍 Bei großen Refactorings: Erkenne die GESAMTBEDEUTUNG, nicht nur Einzelschritte!

⚠️ Es gibt 23 Commits. Gruppiere verwandte Commits zu EINEM detaillierten Feature-Punkt!

BEISPIEL FÜR GRUPPIERUNG:
Wenn du diese Commits siehst:
- feat: Implement delta import to catch missed messages during downtime
- fix: Handle timezone-aware datetimes in delta import
- fix: Import asyncio in delta import function
- fix: Enable delta import on bot restart instead of force reimport
- feat: Track last message timestamp for reliable delta imports

Dann NICHT schreiben:
• Delta Import implementiert
• Timezone-Fehler behoben
• Asyncio importiert

Sondern STATTDESSEN schreiben:
• **Intelligenter Delta-Import**: Der Bot erkennt jetzt automatisch wenn er offline war und importiert nur die Nachrichten die während der Downtime verpasst wurden. Das bedeutet:
  - Keine verlorenen Nachrichten mehr bei Bot-Neustarts
  - Deutlich schnellerer Start (nur neue Nachrichten statt komplett neu importieren)
  - Automatische Erkennung von Downtime über 1 Minute
  - Fortschrittsanzeige im Dashboard während des Imports

AUFGABE:
Fasse diese Commits zu professionellen, DETAILLIERTEN Patch Notes zusammen:

1. GRUPPIERE verwandte Commits zu EINEM ausführlichen Bulletpoint
2. Kategorisiere in: 🆕 Neue Features, 🐛 Bugfixes, ⚡ Verbesserungen
3. Verwende einfache, klare Sprache aber sei AUSFÜHRLICH
4. Beschreibe WAS das Feature macht UND WARUM es wichtig ist
5. Bei großen Features: 3-5 Sätze oder Bulletpoints mit Details
6. Entferne Jargon und technische Präfixe
7. Zielgruppe: Endkunden die verstehen wollen was sich verbessert hat
8. Maximal 8000 Zeichen - nutze den Platz aus!

FORMAT:
Verwende Markdown mit ** für Kategorien und • für Hauptpunkte.
Bei komplexen Features: Nutze Sub-Bulletpoints (Einrückung mit 2 Leerzeichen).

FORMAT-BEISPIEL:
**🆕 Neue Features:**
• **Feature-Name**: Detaillierte Beschreibung was das Feature macht und warum es wichtig ist.
  - Erster Nutzen oder technisches Detail
  - Zweiter Nutzen oder technisches Detail
  - Dritter Nutzen oder technisches Detail

**🐛 Bugfixes:**
• **Bug-Kategorie**: Was wurde gefixt und welches Problem hatte es verursacht

**⚡ Verbesserungen:**
• **Verbesserung**: Detaillierte Beschreibung der Verbesserung

Erstelle JETZT die DETAILLIERTEN Patch Notes basierend auf den ECHTEN Commits oben:"""


async def main():
    print("🤖 Generating AI patch notes for GuildScout v2.2.0...")
    print()

    # Load config
    config = Config()

    # Create AI service
    ai_service = AIService(config)

    # Generate patch notes
    print("📝 Calling AI (this may take 30-60 seconds)...")
    patch_notes = await ai_service.get_raw_ai_response(
        prompt=PROMPT_DE,
        use_critical_model=False  # Use smaller model to avoid RAM issues
    )

    if not patch_notes:
        print("❌ AI generation failed!")
        sys.exit(1)

    # Clean up response
    response = patch_notes.strip()

    # Ensure it starts with a category
    if not response.startswith('**'):
        lines = response.split('\n')
        start_idx = 0
        for i, line in enumerate(lines):
            if line.startswith('**'):
                start_idx = i
                break
        response = '\n'.join(lines[start_idx:])

    print()
    print("="*80)
    print("GENERATED PATCH NOTES:")
    print("="*80)
    print(response)
    print("="*80)
    print()
    print(f"Length: {len(response)} characters")
    print()
    print("✅ Patch notes generated successfully!")
    print("   Copy the text above and post it in your Discord update channel.")


if __name__ == "__main__":
    asyncio.run(main())
