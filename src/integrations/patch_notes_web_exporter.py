"""
Patch Notes Web Exporter — Generiert SEO-optimierte Changelog-Dateien.

Exportiert Patch Notes als JSON und Markdown für die Webseite.
- JSON: Maschinenlesbar für Frontend-Rendering
- Markdown: Menschenlesbar, SEO-optimiert mit Keywords
"""

import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime, timezone

logger = logging.getLogger('shadowops')


class PatchNotesWebExporter:
    """
    Exportiert Patch Notes als SEO-optimiertes JSON und Markdown.
    """

    def __init__(self, base_output_dir: Path):
        self.base_output_dir = base_output_dir
        self.base_output_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"✅ PatchNotesWebExporter initialisiert (Output: {self.base_output_dir})")

    def _get_project_dir(self, project: str) -> Path:
        """Projekt-spezifisches Output-Verzeichnis."""
        project_dir = self.base_output_dir / project.lower()
        project_dir.mkdir(parents=True, exist_ok=True)
        return project_dir

    def export(self, project: str, version: str, title: str, tldr: str,
               content: str, stats: Dict, language: str = 'de',
               changes: Optional[List[Dict]] = None,
               seo_keywords: Optional[List[str]] = None) -> Dict[str, Path]:
        """
        Exportiere Patch Notes als JSON + Markdown.

        Args:
            project: Projektname
            version: Versionsnummer
            title: Patch Notes Titel
            tldr: Kurzzusammenfassung
            content: Vollständiger Inhalt (Markdown)
            stats: Git/CI Stats Dict
            language: Sprache (de/en)
            changes: Strukturierte Änderungen (optional)
            seo_keywords: SEO-Keywords (optional)

        Returns:
            Dict mit Pfaden: {'json': Path, 'markdown': Path}
        """
        project_dir = self._get_project_dir(project)
        timestamp = datetime.now(timezone.utc).isoformat()

        # SEO-Keywords automatisch aus Content extrahieren falls nicht angegeben
        if not seo_keywords:
            seo_keywords = self._extract_keywords(project, content, language)

        # JSON Export
        json_data = {
            'project': project,
            'version': version,
            'title': title,
            'tldr': tldr,
            'content': content,
            'stats': stats,
            'language': language,
            'changes': changes or [],
            'seo': {
                'keywords': seo_keywords,
                'meta_description': self._build_meta_description(project, version, tldr),
                'og_title': f"{project} {version} — {tldr[:80]}",
                'og_description': tldr[:200],
            },
            'published_at': timestamp,
            'slug': f"{version.replace('.', '-')}",
        }

        json_path = project_dir / f"v{version}.json"
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(json_data, f, indent=2, ensure_ascii=False)

        # Markdown Export (SEO-optimiert)
        md_content = self._build_seo_markdown(
            project, version, title, tldr, content, stats, language, seo_keywords
        )

        md_path = project_dir / f"v{version}.md"
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write(md_content)

        # Index aktualisieren
        self._update_index(project, version, title, tldr, timestamp)

        logger.info(f"📝 Web-Export für {project} v{version}: {json_path}, {md_path}")

        return {'json': json_path, 'markdown': md_path}

    def _build_seo_markdown(self, project: str, version: str, title: str,
                            tldr: str, content: str, stats: Dict,
                            language: str, keywords: List[str]) -> str:
        """Baue SEO-optimiertes Markdown."""
        # Frontmatter für Static Site Generators
        frontmatter = [
            '---',
            f'title: "{project} {version} — {title}"',
            f'description: "{tldr[:160]}"',
            f'version: "{version}"',
            f'project: "{project}"',
            f'date: "{datetime.now(timezone.utc).strftime("%Y-%m-%d")}"',
            f'keywords: [{", ".join(f"\"{k}\"" for k in keywords[:10])}]',
            f'language: "{language}"',
            '---',
            '',
        ]

        # Hauptinhalt
        lines = frontmatter.copy()
        lines.append(f'# {project} {version} — {title}')
        lines.append('')
        lines.append(f'> **TL;DR:** {tldr}')
        lines.append('')

        # Vollständiger Inhalt
        lines.append(content)
        lines.append('')

        # Stats-Sektion
        if stats and stats.get('commits', 0) >= 5:
            if language == 'de':
                lines.append('## 📊 Release-Statistiken')
            else:
                lines.append('## 📊 Release Stats')
            lines.append('')

            commits = stats.get('commits', 0)
            files = stats.get('files_changed', 0)
            added = stats.get('lines_added', 0)
            removed = stats.get('lines_removed', 0)
            contributors = stats.get('contributors', [])

            lines.append(f'- **{commits}** Commits')
            lines.append(f'- **{files}** Dateien geändert')
            lines.append(f'- **+{added}** / **-{removed}** Zeilen')

            if contributors:
                lines.append(f'- **{len(contributors)}** Contributor(s): {", ".join(contributors)}')

            tests_passed = stats.get('tests_passed')
            tests_total = stats.get('tests_total')
            coverage = stats.get('coverage_percent')

            if tests_total is not None:
                lines.append(f'- **{tests_passed}/{tests_total}** Tests bestanden')

            if coverage is not None:
                lines.append(f'- **{coverage:.1f}%** Code-Coverage')

            lines.append('')

        return '\n'.join(lines)

    def _build_meta_description(self, project: str, version: str, tldr: str) -> str:
        """Baue SEO Meta-Description (max 160 Zeichen)."""
        base = f"{project} {version}: {tldr}"
        if len(base) <= 160:
            return base
        return base[:157] + "..."

    def _extract_keywords(self, project: str, content: str, language: str) -> List[str]:
        """Extrahiere SEO-Keywords aus dem Content."""
        keywords = [project.lower(), 'patch notes', 'changelog', 'update', 'release']

        if language == 'de':
            keywords.extend(['aktualisierung', 'neue features', 'bugfix', 'verbesserungen'])
        else:
            keywords.extend(['new features', 'bug fixes', 'improvements'])

        # Extrahiere Feature-Namen (Bold-Text)
        bold_matches = re.findall(r'\*\*([^*]+)\*\*', content)
        for match in bold_matches[:5]:
            cleaned = match.strip().lower()
            # Kategorie-Header filtern
            if cleaned not in ['neue features:', 'bugfixes:', 'verbesserungen:',
                               'new features:', 'bug fixes:', 'improvements:',
                               'dokumentation:', 'documentation:']:
                if len(cleaned) > 3:
                    keywords.append(cleaned)

        return list(dict.fromkeys(keywords))  # Deduplizieren, Reihenfolge beibehalten

    def _update_index(self, project: str, version: str, title: str,
                      tldr: str, timestamp: str) -> None:
        """Aktualisiere den Changelog-Index für das Projekt."""
        project_dir = self._get_project_dir(project)
        index_path = project_dir / 'index.json'

        index = []
        if index_path.exists():
            try:
                with open(index_path, 'r', encoding='utf-8') as f:
                    index = json.load(f)
            except Exception:
                index = []

        # Neuen Eintrag hinzufügen (vorne, neueste zuerst)
        entry = {
            'version': version,
            'title': title,
            'tldr': tldr,
            'published_at': timestamp,
            'json_file': f"v{version}.json",
            'markdown_file': f"v{version}.md",
        }

        # Duplikat-Prüfung
        index = [e for e in index if e.get('version') != version]
        index.insert(0, entry)

        with open(index_path, 'w', encoding='utf-8') as f:
            json.dump(index, f, indent=2, ensure_ascii=False)

        logger.info(f"📋 Changelog-Index für {project} aktualisiert ({len(index)} Einträge)")

    def get_changelog_url(self, project: str, version: str, base_url: str = '') -> str:
        """Baue die Changelog-URL für Discord-Links."""
        if base_url:
            slug = version.replace('.', '-')
            return f"{base_url}/changelog/{slug}"
        return ''


def get_web_exporter(base_output_dir: Path = None) -> PatchNotesWebExporter:
    """Factory für PatchNotesWebExporter."""
    if base_output_dir is None:
        base_output_dir = Path.home() / '.shadowops' / 'changelogs'
    return PatchNotesWebExporter(base_output_dir)
