"""
CHANGELOG Parser for extracting version information.

Parses CHANGELOG.md files to extract detailed release notes for specific versions.
"""

import logging
import re
from pathlib import Path
from typing import Optional, Dict, List

logger = logging.getLogger('shadowops')


class ChangelogParser:
    """Parser for CHANGELOG.md files."""

    def __init__(self, changelog_path: Path):
        self.changelog_path = changelog_path
        self._content: Optional[str] = None

    def _load_content(self) -> str:
        """Load CHANGELOG.md content."""
        if self._content is None:
            if not self.changelog_path.exists():
                logger.warning(f"CHANGELOG not found: {self.changelog_path}")
                return ""

            try:
                with open(self.changelog_path, 'r', encoding='utf-8') as f:
                    self._content = f.read()
            except Exception as e:
                logger.error(f"Failed to read CHANGELOG: {e}", exc_info=True)
                self._content = ""

        return self._content

    def get_version_section(self, version: str) -> Optional[Dict[str, any]]:
        """
        Extract the CHANGELOG section for a specific version.

        Returns:
            Dict with keys: version, date, title, content, subsections
        """
        content = self._load_content()
        if not content:
            return None

        # Match version headers like:
        # ## Version 2.3.0 - Advanced Monitoring & Security (2025-12-01)
        # ## [2.3.0] - 2025-12-01
        version_pattern = rf'^##\s+(?:\[)?(?:Version\s+)?{re.escape(version)}(?:\])?\s*[-–—]\s*(.+?)(?:\s+\(([0-9-]+)\))?$'

        lines = content.split('\n')
        start_idx = None
        version_header = None
        version_title = None
        version_date = None

        # Find version header
        for idx, line in enumerate(lines):
            match = re.match(version_pattern, line, re.IGNORECASE)
            if match:
                start_idx = idx
                version_header = line
                version_title = match.group(1).strip()
                version_date = match.group(2) if match.group(2) else None
                break

        if start_idx is None:
            logger.warning(f"Version {version} not found in CHANGELOG")
            return None

        # Find end of section (next version header or end of file)
        end_idx = len(lines)
        for idx in range(start_idx + 1, len(lines)):
            if re.match(r'^##\s+(?:\[)?(?:Version\s+)?[0-9]+\.[0-9]+\.[0-9]+', lines[idx]):
                end_idx = idx
                break

        # Extract section content
        section_lines = lines[start_idx + 1:end_idx]
        section_content = '\n'.join(section_lines).strip()

        # Parse subsections (### headers)
        subsections = self._parse_subsections(section_content)

        return {
            'version': version,
            'date': version_date,
            'title': version_title,
            'header': version_header,
            'content': section_content,
            'subsections': subsections,
            'line_count': len(section_lines)
        }

    def _parse_subsections(self, content: str) -> List[Dict[str, str]]:
        """Parse subsections (### headers) from content."""
        subsections = []
        lines = content.split('\n')
        current_section = None
        current_content = []

        for line in lines:
            if line.startswith('### '):
                # Save previous section
                if current_section:
                    subsections.append({
                        'title': current_section,
                        'content': '\n'.join(current_content).strip()
                    })

                # Start new section
                current_section = line[4:].strip()
                current_content = []
            elif current_section:
                current_content.append(line)

        # Save last section
        if current_section:
            subsections.append({
                'title': current_section,
                'content': '\n'.join(current_content).strip()
            })

        return subsections

    def get_latest_version(self) -> Optional[str]:
        """Get the latest version from CHANGELOG."""
        content = self._load_content()
        if not content:
            return None

        # Find first version header
        match = re.search(r'^##\s+(?:\[)?(?:Version\s+)?([0-9]+\.[0-9]+\.[0-9]+)', content, re.MULTILINE)
        if match:
            return match.group(1)

        return None

    def is_major_release(self, version: str) -> bool:
        """Check if version is a major release (x.0.0 or x.y.0 with significant changes)."""
        parts = version.split('.')
        if len(parts) != 3:
            return False

        major, minor, patch = parts

        # x.0.0 is always major
        if minor == '0' and patch == '0':
            return True

        # x.y.0 might be major if it has significant changes
        if patch == '0':
            section = self.get_version_section(version)
            if section:
                # Check for indicators of major release
                content_lower = section['content'].lower()
                major_indicators = [
                    'major', 'breaking', 'significant', 'comprehensive',
                    'overhaul', 'redesign', 'rewrite'
                ]

                if any(indicator in content_lower for indicator in major_indicators):
                    return True

                # Or if it's a very long CHANGELOG section (>300 lines = significant)
                if section['line_count'] > 300:
                    return True

        return False

    def format_for_discord(self, version_data: Dict[str, any], max_fields: int = 10) -> Dict[str, any]:
        """
        Format CHANGELOG data for Discord embed.

        Returns dict with: title, description, fields (list of {name, value})
        """
        if not version_data:
            return None

        title = f"✨ Updates for {version_data['version']}"
        description = f"**{version_data['title']}** 🚀"

        if version_data['date']:
            description += f"\n*Released: {version_data['date']}*"

        fields = []

        # Add subsections as fields (Discord limit: 25 fields, 1024 chars per field)
        for subsection in version_data['subsections'][:max_fields]:
            # Split if content > 1024 chars
            content = subsection['content']

            if len(content) <= 1024:
                fields.append({
                    'name': subsection['title'],
                    'value': content
                })
            else:
                # Split into multiple fields
                parts = self._split_content(content, 1024)
                for i, part in enumerate(parts):
                    name = subsection['title'] if i == 0 else f"{subsection['title']} (cont.)"
                    fields.append({
                        'name': name,
                        'value': part
                    })

        return {
            'title': title,
            'description': description,
            'fields': fields
        }

    def _split_content(self, content: str, max_length: int) -> List[str]:
        """Split content into chunks of max_length, trying to break at newlines."""
        if len(content) <= max_length:
            return [content]

        parts = []
        current = ""

        for line in content.split('\n'):
            if len(current) + len(line) + 1 <= max_length:
                current += line + '\n'
            else:
                if current:
                    parts.append(current.rstrip())
                current = line + '\n'

        if current:
            parts.append(current.rstrip())

        return parts


def get_changelog_parser(project_path: Path) -> ChangelogParser:
    """Get ChangelogParser instance for a project."""
    changelog_path = project_path / 'CHANGELOG.md'
    return ChangelogParser(changelog_path)
