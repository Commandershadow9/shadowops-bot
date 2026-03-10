# Advanced Patch Notes System

## 🎯 Overview

Das Advanced Patch Notes System kombiniert drei Strategien für **maximale Qualität** bei Patch Notes:

1. **CHANGELOG-basiert**: AINITURNE zusammenfassen, nur formatieren
2. **Two-Pass Review**: Discord Approval vor Posting
3. **Major/Minor Detection**: Automatische Erkennung wichtiger Releases

## 🏗️ Architektur

```
Git Push Event
      ↓
GitHub Integration
      ↓
Advanced System aktiviert? ─[Nein]→ Legacy AI Generation
      ↓ [Ja]
PatchNotesManager
      ↓
Version Detection (Commits oder CHANGELOG)
      ↓
Major Release? ──[Ja]──→ CHANGELOG-basiert + Review
      ↓ [Nein]
Minor/Patch Release
      ↓
Hybrid AI (CHANGELOG + Commits)
      ↓
Direct Posting
```

## ⚙️ Konfiguration

### Project Config in `config.yaml`:

```yaml
projects:
  guildscout:
    enabled: true
    path: /home/cmdshadow/GuildScout
    patch_notes:
      language: en
      use_ai: true
      use_advanced_system: true  # 🆕 NEU: Aktiviert Advanced System
    external_notifications:
      - guild_id: 1390695394777890897
        channel_id: 1442887630034440426
        enabled: true
        notify_on:
          git_push: true
```

### Konfigurationsoptionen:

| Option | Typ | Default | Beschreibung |
|--------|-----|---------|--------------|
| `use_advanced_system` | bool | false | Aktiviert CHANGELOG-basiertes System mit Review |
| `use_ai` | bool | false | Legacy AI-generierung (falls Advanced deaktiviert) |
| `language` | string | 'de' | Sprache für Patch Notes ('de' oder 'en') |

## 📋 Funktionsweise

### 1. Major Release Detection

Ein Release gilt als "Major" wenn:
- Version ist `x.0.0` (z.B. 3.0.0)
- Version ist `x.y.0` UND:
  - CHANGELOG enthält Keywords: "major", "breaking", "significant", "comprehensive", "overhaul"
  - ODER CHANGELOG-Abschnitt hat >300 Zeilen

**Bei Major Releases:**
- ✅ Nutzt CHANGELOG.md direkt (keine Zusammenfassung!)
- ✅ AI verbessert nur Formatierung
- ✅ Sendet Draft zur Approval (Discord Buttons)
- ✅ Admin kann approve oder manuelles Script wählen

### 2. Minor/Patch Releases

**Bei Minor/Patch Releases:**
- ✅ Hybrid AI: Kombiniert CHANGELOG + Commit Messages
- ✅ AI formatiert für Discord (behält alle Details)
- ✅ Direct Posting (kein Approval nötig)

### 3. Version Detection

Version wird automatisch erkannt:
1. **Aus Commits**: Sucht nach "v2.3.0", "Version 2.3.0", etc.
2. **Aus CHANGELOG**: Falls nicht in Commits, nutzt latest version

## 🎮 Approval-Prozess (Major Releases)

Wenn Major Release detected wird:

1. **Draft-Embed wird generiert** (CHANGELOG-basiert)
2. **Discord-Nachricht** im internen Channel mit Buttons:
   - ✅ **Approve & Post**: Postet Notes sofort
   - ✏️ **Use Manual Script**: Nutzt vordefiniertes Script
   - ❌ **Cancel**: Bricht Posting ab
3. **30 Minuten Timeout**: Buttons verfallen nach 30min

## 📝 CHANGELOG Format

Damit das System optimal funktioniert, folge diesem Format:

```markdown
# Changelog

## Version 2.3.0 - Advanced Monitoring & Security (2025-12-01)

> **Major Update:** Umfassende Monitoring-, Performance- und Sicherheits-Features.

### 🏥 Health Monitoring System
- **Automated Health Alerts**: Kontinuierliche Systemüberwachung alle 5 Minuten
  - Verifikations-Gesundheit: Erkennt ausgefallene Verifikation
  - Rate Limit Monitoring: Warnt bei kritischer API Auslastung
  - Datenbank-Gesundheit: Überwacht Wachstum und Korruption

### 📊 Performance Profiling
- **`/profile` Command**: Umfassendes Performance-Profiling
  - Langsamste Operationen
  - Meistgenutzte Operationen
  - Bottleneck-Analyse
```

**Wichtig:**
- Verwende `## Version X.Y.Z - Title (Date)` Format
- Subsections mit `###` Headern
- Bullet Points mit Details
- Keywords für Major-Detection ("major", "significant", etc.)

## 🔧 Entwickler-Guide

### CHANGELOG Parser nutzen:

```python
from src.utils.changelog_parser import get_changelog_parser
from pathlib import Path

parser = get_changelog_parser(Path('/path/to/project'))

# Get specific version
version_data = parser.get_version_section('2.3.0')

# Check if major release
is_major = parser.is_major_release('2.3.0')

# Format for Discord
discord_data = parser.format_for_discord(version_data)
```

### PatchNotesManager nutzen:

```python
from src.integrations.patch_notes_manager import get_patch_notes_manager

manager = get_patch_notes_manager(bot, ai_service)

# Handle git push
await manager.handle_git_push(
    project_name='guildscout',
    project_config=project_config,
    commits=commits,
    repo_name='GuildScout'
)
```

## 🎨 Discord Embed Limits

Beachte Discord Limits:
- **Embed Description**: Max 4096 Zeichen
- **Field Value**: Max 1024 Zeichen
- **Total Embed**: Max 6000 Zeichen
- **Fields**: Max 25 Fields

Das System splittet automatisch bei Überschreitung.

## 🚨 Fallback-Strategie

Bei Fehlern im Advanced System:
1. **Fallback zu Legacy AI**: Nutzt commit-basierte AI-Generierung
2. **Logging**: Alle Fehler werden geloggt
3. **Keine Unterbrechung**: User sieht immer Patch Notes

## 🔍 Debugging

### Logs anzeigen:

```bash
journalctl --user -u shadowops-bot.service -f | grep -i "patch\|changelog"
```

### Test-Run (ohne Posting):

Erstelle Test-Script:
```python
import asyncio
from pathlib import Path
from src.utils.changelog_parser import get_changelog_parser

async def test():
    parser = get_changelog_parser(Path('/home/cmdshadow/GuildScout'))
    version_data = parser.get_version_section('2.3.0')

    print(f"Version: {version_data['version']}")
    print(f"Is Major: {parser.is_major_release('2.3.0')}")
    print(f"Subsections: {len(version_data['subsections'])}")

    for section in version_data['subsections']:
        print(f"\n### {section['title']}")
        print(f"Length: {len(section['content'])} chars")

asyncio.run(test())
```

## 📊 Monitoring & Metrics

Das System loggt:
- ✅ Version Detection Events
- ✅ Major vs Minor Entscheidungen
- ✅ CHANGELOG Parsing Erfolge/Fehler
- ✅ AI Enhancement Versuche
- ✅ Approval-Status (Approved/Cancelled/Timeout)

## 🔐 Permissions

Benötigte Permissions für Approval-Buttons:
- **Bot**: `SEND_MESSAGES`, `EMBED_LINKS`, `ADD_REACTIONS`
- **User**: Jeder User kann Buttons klicken (konfigurierbar)

Später kann eingeschränkt werden auf:
```python
# In PatchNotesApprovalView:
@ui.button(...)
async def approve_button(self, interaction: discord.Interaction, button: ui.Button):
    # Check permissions
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admin-only", ephemeral=True)
        return
    # ... rest
```

## 🎯 Best Practices

1. **CHANGELOG pflegen**: Je detaillierter, desto besser die Patch Notes
2. **Semantische Versionierung**: Korrekte Major/Minor/Patch Nummerierung
3. **Keywords nutzen**: "breaking", "major" bei wichtigen Releases
4. **Subsections organisieren**: Klare ### Struktur in CHANGELOG
5. **Review nutzen**: Bei Major Releases immer approven, nicht blind posten

## 🔄 Migration von Legacy

Migrationsschritte:

1. **CHANGELOG aktualisieren**: Stelle sicher Format stimmt
2. **Config anpassen**: `use_advanced_system: true` setzen
3. **Test-Push**: Mache kleinen Commit und beobachte Logs
4. **Approval testen**: Bei Major Release Buttons testen
5. **Legacy als Fallback**: Bleibt automatisch aktiv

Rollback:
```yaml
# In config.yaml:
use_advanced_system: false  # Zurück zu Legacy
```

## 📚 Troubleshooting

### Problem: "Version not found in CHANGELOG"

**Lösung:**
- Prüfe CHANGELOG Format
- Version muss Format haben: `## Version X.Y.Z` oder `## [X.Y.Z]`
- Commit Message muss Version enthalten: "v2.3.0" oder "Version 2.3.0"

### Problem: "Advanced system failed, falling back to legacy"

**Lösung:**
- Schaue Logs: `journalctl --user -u shadowops-bot.service -n 100`
- Prüfe ob `CHANGELOG.md` existiert: `ls /home/cmdshadow/GuildScout/CHANGELOG.md`
- Teste Parser manuell (siehe Debugging Section)

### Problem: Approval Buttons reagieren nicht

**Lösung:**
- Prüfe Bot Permissions: `SEND_MESSAGES`, `EMBED_LINKS`
- Timeout? Buttons verfallen nach 30min
- Checke Logs auf Exceptions

## 🆕 Future Enhancements

Mögliche Erweiterungen:
- [ ] Multi-Language CHANGELOG Support
- [ ] Template-System für verschiedene Release-Types
- [ ] Automatische Screenshots/GIFs einfügen
- [ ] Integration mit Jira/Linear Issues
- [ ] Auto-Tweet für Major Releases
- [ ] Changelog-zu-Blog-Post Generator

## 📝 Examples

### Beispiel 1: Major Release Flow

```
1. Push to main with "Version 3.0.0" in commit
2. System detects v3.0.0 from commit
3. Checks CHANGELOG → findet "## Version 3.0.0 - Complete Rewrite"
4. Is Major? → Ja (x.0.0 + "Complete Rewrite" keyword)
5. Parses CHANGELOG (7 Subsections, 450 lines)
6. AI enhances formatting (optional)
7. Creates Discord Embed (splits into 5 fields)
8. Sends to internal channel with Approval buttons
9. Admin clicks "✅ Approve & Post"
10. Posts to customer Discord channels
```

### Beispiel 2: Minor Release Flow

```
1. Push to main with "fix: Bug in dashboard" commit
2. System detects v2.3.1 from CHANGELOG (latest)
3. Checks CHANGELOG → findet "## Version 2.3.1 - Bugfixes"
4. Is Major? → Nein (x.y.1 + <300 lines)
5. Hybrid AI: Combines CHANGELOG + 3 commits
6. AI formats for Discord (keeps all details)
7. Direct posting to customer channels (no approval)
```

---

**Version:** 1.0.0
**Author:** CommanderShadow & Claude (Anthropic)
**Last Updated:** 2025-12-01
