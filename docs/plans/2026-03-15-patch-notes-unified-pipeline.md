# Patch Notes — Unified Pipeline Umbau

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Die 3 verschiedenen Patch-Notes-Pfade (V3/Legacy/Advanced) zu EINEM einheitlichen System zusammenführen, das für alle Projekte identisch funktioniert.

**Architecture:** Ein einziger `_send_push_notification` Pfad: AI generiert immer Raw-Text → ein einziger `_build_customer_embed` baut das Discord-Teaser-Embed → Web-Export IMMER mit SEO → Feedback-Buttons IMMER angehängt. Das "Advanced System" (PatchNotesManager) und der duale Embed-Pfad werden entfernt.

**Tech Stack:** Python 3.12, discord.py 2.7, AI Engine (Codex/Claude CLI), SQLite Changelog-DB, aiohttp API POST

---

## Ist-Zustand (Probleme)

| Problem | Ursache |
|---------|---------|
| Mal Bewertungs-Buttons, mal nicht | `version=None` → Feedback wird übersprungen |
| Mal SEO-Tags, mal nicht | Strukturiert → SEO, Raw-Text → kein SEO |
| Zwei verschiedene Embed-Designs | `_build_v3_customer_embed` vs `_build_customer_embed` |
| Advanced System nie genutzt | `use_advanced_system: false` aber Code existiert |
| Web-Export übersprungen ohne Version | `if not version: return` |

## Soll-Zustand

```
Push/Release → AI (Raw-Text, immer) → Sanitize → EINEN Embed bauen
                                                → Web-Export (IMMER, mit Auto-Version)
                                                → Feedback-Buttons (IMMER)
                                                → Discord: Teaser + Link
                                                → Internal: Preview
```

---

### Task 1: Toten Code entfernen — Advanced System + Legacy Embed

**Files:**
- Delete: `src/integrations/patch_notes_manager.py`
- Modify: `src/integrations/github_integration/notifications_mixin.py`
- Modify: `src/bot.py` (PatchNotesManager init entfernen)

**Step 1:** `patch_notes_manager.py` löschen — wird nicht benutzt (`use_advanced_system: false`)

**Step 2:** In `notifications_mixin.py`: Entferne den Advanced System Check:
```python
# ENTFERNEN (Zeilen 93-105):
# if self.patch_notes_manager and patch_config.get('use_advanced_system', False):
#     ... handle_git_push ...
```

**Step 3:** In `notifications_mixin.py`: Entferne `_build_structured_customer_embed` (die alte V2 Methode, Zeilen 238-302) — wird nirgends aufgerufen.

**Step 4:** In `bot.py`: Entferne PatchNotesManager-Init (suche nach `patch_notes_manager`).

**Step 5:** Commit: `refactor: Toten Code entfernt — Advanced System + V2 Embed`

---

### Task 2: Einen einzigen Embed-Pfad bauen

**Files:**
- Modify: `src/integrations/github_integration/notifications_mixin.py`

**Step 1:** Die Embed-Selection (Zeilen 144-163) durch EINEN Pfad ersetzen:

```python
# VORHER (DUAL):
if isinstance(ai_result, dict) and ai_result.get('discord_highlights'):
    customer_embed = self._build_v3_customer_embed(...)
    await self._export_structured_web_changelog(...)
else:
    customer_embed = self._build_customer_embed(...)
    await self._export_web_changelog(...)

# NACHHER (EINHEITLICH):
customer_embed = self._build_unified_embed(
    repo_name, project_color, commits, language,
    ai_result, project_config, git_stats
)
version = self._resolve_version(ai_result, commits)
await self._unified_web_export(
    repo_name, commits, ai_result, project_config, language, git_stats, version
)
```

**Step 2:** `_build_unified_embed()` implementieren — EINE Methode die mit dict, str und None umgehen kann:

```python
def _build_unified_embed(self, repo_name, project_color, commits, language,
                          ai_result, project_config, git_stats):
    """EIN Embed-Builder für alle Fälle."""

    # Version + Titel bestimmen
    version = self._resolve_version(ai_result, commits)
    title = self._resolve_title(ai_result, version)
    changelog_url = project_config.get('patch_notes', {}).get('changelog_url', '')

    # Changelog-Link
    is_real_version = version and not version.startswith('patch.')
    if changelog_url:
        changelog_link = f"{changelog_url}/{version.replace('.', '-')}" if is_real_version else changelog_url
    else:
        changelog_link = ''

    # Embed erstellen
    version_str = f"v{version} — " if is_real_version else ''
    embed = discord.Embed(
        title=f"🚀 {version_str}{title}",
        url=changelog_link or None,
        color=project_color,
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_author(name=repo_name.upper())

    # Description bauen — EIN Weg für alle Inputs
    description = self._build_description(ai_result, commits, language)

    # Changelog-Link am Ende
    if changelog_link:
        link_text = "Alle Details & vollständige Patch Notes" if language == 'de' else "Full details & complete patch notes"
        description += f"\n\n📖 [{link_text}]({changelog_link})"

    embed.description = description[:4096]

    # Footer
    embed.set_footer(text=self._build_footer(version, commits, git_stats))

    return embed
```

**Step 3:** `_build_description()` — Wandelt JEDES AI-Ergebnis in fließenden Text:

```python
def _build_description(self, ai_result, commits, language):
    """Baut Description aus AI-Ergebnis (dict/str/None)."""
    if isinstance(ai_result, dict):
        return self._description_from_structured(ai_result, commits, language)
    elif isinstance(ai_result, str) and ai_result.strip():
        return ai_result.strip()
    else:
        return self._categorize_commits_text(commits, language)
```

**Step 4:** `_description_from_structured()` — Structured Data → fließender Text:

```python
def _description_from_structured(self, ai_data, commits, language):
    """Strukturierte AI-Daten → fließende Discord Description."""
    parts = []

    tldr = ai_data.get('tldr', '')
    if tldr:
        parts.append(f"> {tldr}")
        parts.append("")

    changes = ai_data.get('changes', [])
    features = [c for c in changes if c.get('type') == 'feature']
    fixes = [c for c in changes if c.get('type') == 'fix']
    improvements = [c for c in changes if c.get('type') == 'improvement']
    breaking = ai_data.get('breaking_changes', [])
    is_major = len(commits) >= 15

    if features:
        max_show = 6 if is_major else 4
        parts.append("**🆕 Neue Features**")
        for f in features[:max_show]:
            parts.append(f"→ {f.get('description', '')}")
        if len(features) > max_show:
            parts.append(f"  *+{len(features) - max_show} weitere*")
        parts.append("")

    if breaking:
        parts.append("**⚠️ Breaking Changes**")
        for b in breaking[:3]:
            parts.append(f"⚠️ {b}")
        parts.append("")

    if fixes:
        parts.append("**🐛 Bugfixes**")
        for f in fixes[:4]:
            parts.append(f"→ {f.get('description', '')}")
        if len(fixes) > 4:
            parts.append(f"  *+{len(fixes) - 4} weitere*")
        parts.append("")

    if improvements:
        parts.append("**⚡ Verbesserungen**")
        for i in improvements[:3]:
            parts.append(f"→ {i.get('description', '')}")
        if len(improvements) > 3:
            parts.append(f"  *+{len(improvements) - 3} weitere*")
        parts.append("")

    # Fallback wenn keine changes
    if not changes and not breaking:
        highlights = ai_data.get('discord_highlights', [])
        if highlights:
            parts.append("**🔥 Highlights**")
            for h in highlights[:5]:
                parts.append(f"→ {h}")
            parts.append("")

    return "\n".join(parts)
```

**Step 5:** Commit: `refactor: Unified Embed Builder — ein Pfad für alle AI-Ergebnisse`

---

### Task 3: Unified Web-Export (IMMER mit SEO)

**Files:**
- Modify: `src/integrations/github_integration/notifications_mixin.py`

**Step 1:** Entferne `_export_web_changelog` und `_export_structured_web_changelog` (zwei separate Methoden). Ersetze durch EINE:

```python
async def _unified_web_export(self, repo_name, commits, ai_result,
                               project_config, language, git_stats, version):
    """Web-Export — IMMER, mit SEO, egal welches AI-Ergebnis."""
    exporter = getattr(self, 'web_exporter', None)
    if not exporter:
        return

    # Titel + TL;DR extrahieren (aus dict oder str)
    title, tldr, content, changes, seo_keywords = self._extract_web_content(
        ai_result, repo_name, version
    )

    try:
        await exporter.export_and_store(
            project=repo_name,
            version=version,
            title=title,
            tldr=tldr,
            content=content,
            stats=git_stats or {},
            language=language,
            changes=changes,
            seo_keywords=seo_keywords,
        )
        self.logger.info(f"📝 Web-Export: {repo_name} v{version}")
    except Exception as e:
        self.logger.warning(f"⚠️ Web-Export fehlgeschlagen: {e}")
```

**Step 2:** `_extract_web_content()` — Konvertiert AI-Ergebnis zu Web-Feldern:

```python
def _extract_web_content(self, ai_result, repo_name, version):
    """Extrahiere Titel, TL;DR, Content, Changes, SEO aus jedem AI-Ergebnis."""
    if isinstance(ai_result, dict):
        title = ai_result.get('title', f'{repo_name} Update')
        tldr = ai_result.get('tldr', '')
        content = ai_result.get('web_content', ai_result.get('summary', ''))
        changes = ai_result.get('changes', [])
        seo_keywords = ai_result.get('seo_keywords', [])
        return title, tldr, content, changes, seo_keywords

    elif isinstance(ai_result, str) and ai_result.strip():
        # TL;DR aus erstem Satz extrahieren
        text = ai_result.strip()
        tldr_match = re.search(r'\*\*TL;DR:\*\*\s*(.+?)(?:\n|$)', text)
        if tldr_match:
            tldr = tldr_match.group(1).strip()
        else:
            first_line = text.split('\n')[0].strip()
            tldr = first_line[:200] if first_line and not first_line.startswith('**') else f"{repo_name} Update"

        title = f"{repo_name} Update"
        content = text  # Raw-Text als Web-Content
        changes = []  # Keine strukturierten Changes
        seo_keywords = []  # Werden vom Exporter auto-extrahiert
        return title, tldr, content, changes, seo_keywords

    else:
        return f"{repo_name} Update", '', '', [], []
```

**Step 3:** Commit: `refactor: Unified Web-Export — IMMER mit SEO, ein Pfad`

---

### Task 4: Version-Handling vereinheitlichen

**Files:**
- Modify: `src/integrations/github_integration/notifications_mixin.py`

**Step 1:** EINE `_resolve_version()` Methode statt verstreuter Version-Logik:

```python
def _resolve_version(self, ai_result, commits):
    """Bestimme Version: Commits > AI > Auto-Version. NIE None."""
    # 1. Aus Commits (expliziter Version-Tag)
    v = self._extract_version_from_commits(commits)
    if v:
        return v

    # 2. Aus AI-Ergebnis (nur echte Versionen)
    if isinstance(ai_result, dict):
        ai_v = ai_result.get('version')
        if ai_v and ai_v != 'patch' and not ai_v.startswith('0.0.'):
            return ai_v

    # 3. Auto-Version (Fallback, IMMER)
    return f"patch.{datetime.now(timezone.utc).strftime('%Y.%m.%d')}"
```

**Step 2:** Alle alten Version-Extraktionen entfernen (in `_send_push_notification`, `_export_web_changelog`, `_export_structured_web_changelog`). Stattdessen überall `_resolve_version()` nutzen.

**Step 3:** Commit: `refactor: Unified Version-Handling — _resolve_version() statt 5 verschiedene Stellen`

---

### Task 5: Feedback-Buttons IMMER anhängen

**Files:**
- Modify: `src/integrations/github_integration/notifications_mixin.py`

**Step 1:** In `_send_push_notification` nach dem Embed-Build — Version ist jetzt IMMER gesetzt (dank `_resolve_version`), also werden Feedback-Buttons IMMER angehängt:

```python
# Version ist IMMER gesetzt (nie None)
version = self._resolve_version(ai_result, commits)

# Customer Channel + Feedback (IMMER)
await self._send_to_customer_channels(customer_embed, repo_name, project_config, version)
await self._send_external_git_notifications(repo_name, customer_embed, project_config, version)
```

**Step 2:** Entferne den Auto-Version-Fallback in `_send_push_notification` Zeile 170-172 (jetzt in `_resolve_version`).

**Step 3:** Commit: `fix: Feedback-Buttons IMMER — Version ist nie None`

---

### Task 6: Alten Code aufräumen + Doku

**Files:**
- Modify: `src/integrations/github_integration/notifications_mixin.py` (alte Methoden entfernen)
- Modify: `CLAUDE.md` (Tabelle aktualisieren)
- Delete: `src/integrations/patch_notes_manager.py`

**Step 1:** Entferne aus `notifications_mixin.py`:
- `_build_customer_embed()` (ersetzt durch `_build_unified_embed`)
- `_build_v3_customer_embed()` (ersetzt durch `_build_unified_embed`)
- `_build_structured_customer_embed()` (V2, nie genutzt)
- `_export_web_changelog()` (ersetzt durch `_unified_web_export`)
- `_export_structured_web_changelog()` (ersetzt durch `_unified_web_export`)
- `_build_changelog_fallback_description()` (in `_build_description` integriert)

**Step 2:** CLAUDE.md aktualisieren:
- `patch_notes_manager.py` aus Tabelle entfernen
- Beschreibungen aktualisieren

**Step 3:** Commit: `chore: Patch Notes Cleanup — alte Methoden + PatchNotesManager entfernt`

---

### Task 7: Tests + Deploy

**Step 1:** `pytest tests/unit/test_github_integration.py -x -v`
**Step 2:** Syntax-Check aller geänderten Dateien
**Step 3:** `scripts/restart.sh --logs`
**Step 4:** Health-Check + Log-Prüfung
**Step 5:** `/release-notes` testen (falls Commits vorhanden)
**Step 6:** Commit: `feat: Patch Notes Unified Pipeline — ein System für alle Projekte`

---

## Release-Steuerung (bleibt unverändert)

| Trigger | Verhalten |
|---------|-----------|
| Version-Commit (vX.Y.Z) | Sofort Patch Notes |
| Wöchentlich Sonntag 20:00 | Cron-Release wenn ≥3 Commits |
| `/release-notes <projekt>` | Manueller Release |
| ≥20 Commits (Notbremse) | Auto-Release |
| Sonstige Commits | Sammeln, kein Discord-Spam |

## Was NICHT geändert wird

- `patch_notes_batcher.py` — Funktioniert wie gewünscht
- `patch_notes_web_exporter.py` — Bleibt, wird einheitlich genutzt
- `patch_notes_feedback.py` — Bleibt, wird jetzt IMMER genutzt
- `content_sanitizer.py` — Bleibt
- `ai_patch_notes_mixin.py` — AI-Generation bleibt (Codex → Claude Fallback)
- `bot.py` — Cron + Batcher-Init bleiben
- `cogs/admin.py` — `/release-notes` + `/pending-notes` bleiben
