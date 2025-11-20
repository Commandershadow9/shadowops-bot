"""
Git History Analyzer für ShadowOps Bot
Analysiert Git-Commits um aus vergangenen Fixes und Code-Changes zu lernen
"""

import subprocess
import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional, Any
import logging

logger = logging.getLogger('shadowops')


class GitHistoryAnalyzer:
    """
    Analysiert Git-History um Pattern zu erkennen und Context für AI zu generieren

    Features:
    - Commit-History laden (konfigurierbare Zeitspanne)
    - Pattern Recognition (häufig geänderte Files, Fix-Commits, Security-Commits)
    - Code-Change-Analyse (git diff für relevante Commits)
    - Author-Expertise-Tracking (wer hat Security-Wissen?)
    - Integration in Context Manager für AI-Prompts
    """

    def __init__(self, project_path: str, days_to_analyze: int = 30):
        """
        Args:
            project_path: Pfad zum Git-Repository
            days_to_analyze: Wie viele Tage zurück soll analysiert werden
        """
        self.project_path = Path(project_path)
        self.days_to_analyze = days_to_analyze
        self.commits_cache: List[Dict] = []
        self.pattern_cache: Optional[Dict] = None

        # Pattern für wichtige Commit-Messages
        self.fix_patterns = [
            r'\bfix(?:ed|es|ing)?\b',
            r'\bbug\b',
            r'\brepair\b',
            r'\bresolve[ds]?\b',
            r'\bpatch\b'
        ]

        self.security_patterns = [
            r'\bsecurity\b',
            r'\bvulnerability\b',
            r'\bCVE-\d{4}-\d+',
            r'\bXSS\b',
            r'\bSQL injection\b',
            r'\bauth(?:entication|orization)\b',
            r'\bpermission\b'
        ]

        self.dependency_patterns = [
            r'\bupgrade\b',
            r'\bupdate\b',
            r'\bdependenc(?:y|ies)\b',
            r'\bpackage\.json\b',
            r'\brequirements\.txt\b',
            r'\bDockerfile\b'
        ]

    def is_git_repository(self) -> bool:
        """Prüft ob Pfad ein Git-Repository ist"""
        git_dir = self.project_path / '.git'
        return git_dir.exists() and git_dir.is_dir()

    def load_commit_history(self, force_reload: bool = False) -> List[Dict]:
        """
        Lädt Commit-History aus Git

        Args:
            force_reload: Cache ignorieren und neu laden

        Returns:
            Liste von Commit-Dicts mit allen Infos
        """
        # Use cache if available
        if self.commits_cache and not force_reload:
            logger.debug(f"📚 Using cached commits: {len(self.commits_cache)} entries")
            return self.commits_cache

        if not self.is_git_repository():
            logger.warning(f"⚠️ Not a git repository: {self.project_path}")
            return []

        try:
            # Calculate date range
            since_date = (datetime.now() - timedelta(days=self.days_to_analyze)).strftime('%Y-%m-%d')

            # Git log with custom format (JSON-friendly)
            # Format: hash|author|email|date|subject|body
            git_command = [
                'git', 'log',
                f'--since={since_date}',
                '--pretty=format:%H|%an|%ae|%ai|%s|%b',
                '--no-merges'  # Skip merge commits for cleaner analysis
            ]

            result = subprocess.run(
                git_command,
                cwd=self.project_path,
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode != 0:
                logger.error(f"❌ Git log failed: {result.stderr}")
                return []

            # Parse commits
            commits = []
            for line in result.stdout.strip().split('\n'):
                if not line:
                    continue

                parts = line.split('|', 5)
                if len(parts) < 6:
                    continue

                commit = {
                    'hash': parts[0],
                    'author': parts[1],
                    'email': parts[2],
                    'date': parts[3],
                    'subject': parts[4],
                    'body': parts[5] if len(parts) > 5 else '',
                    'full_message': f"{parts[4]}\n{parts[5]}" if len(parts) > 5 else parts[4]
                }

                # Analyze commit type
                commit['is_fix'] = self._matches_patterns(commit['full_message'], self.fix_patterns)
                commit['is_security'] = self._matches_patterns(commit['full_message'], self.security_patterns)
                commit['is_dependency'] = self._matches_patterns(commit['full_message'], self.dependency_patterns)

                # Get changed files
                commit['changed_files'] = self._get_changed_files(commit['hash'])

                commits.append(commit)

            self.commits_cache = commits
            logger.info(f"✅ Loaded {len(commits)} commits from last {self.days_to_analyze} days")

            # Log statistics
            fix_count = sum(1 for c in commits if c['is_fix'])
            security_count = sum(1 for c in commits if c['is_security'])
            logger.info(f"   📊 Fix commits: {fix_count}, Security commits: {security_count}")

            return commits

        except subprocess.TimeoutExpired:
            logger.error("❌ Git log timeout")
            return []
        except Exception as e:
            logger.error(f"❌ Error loading git history: {e}", exc_info=True)
            return []

    def _matches_patterns(self, text: str, patterns: List[str]) -> bool:
        """Prüft ob Text einem der Pattern entspricht (case-insensitive)"""
        text_lower = text.lower()
        return any(re.search(pattern, text_lower, re.IGNORECASE) for pattern in patterns)

    def _get_changed_files(self, commit_hash: str) -> List[str]:
        """Holt Liste der geänderten Dateien für einen Commit"""
        try:
            result = subprocess.run(
                ['git', 'diff-tree', '--no-commit-id', '--name-only', '-r', commit_hash],
                cwd=self.project_path,
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode == 0:
                return [f.strip() for f in result.stdout.strip().split('\n') if f.strip()]
            return []

        except Exception as e:
            logger.debug(f"Could not get changed files for {commit_hash}: {e}")
            return []

    def analyze_patterns(self) -> Dict[str, Any]:
        """
        Analysiert Commit-History und extrahiert Pattern

        Returns:
            Dict mit Pattern-Analyse:
            - frequently_changed_files: Files die oft geändert werden
            - fix_authors: Authors mit vielen Fix-Commits
            - security_experts: Authors mit Security-Commits
            - common_fix_types: Häufige Fix-Kategorien
            - recent_security_fixes: Letzte Security-Fixes
        """
        # Use cache if available
        if self.pattern_cache:
            return self.pattern_cache

        commits = self.load_commit_history()

        if not commits:
            return {
                'frequently_changed_files': [],
                'fix_authors': [],
                'security_experts': [],
                'common_fix_types': [],
                'recent_security_fixes': []
            }

        # === FILE CHANGE FREQUENCY ===
        file_changes: Dict[str, int] = {}
        for commit in commits:
            for file in commit['changed_files']:
                file_changes[file] = file_changes.get(file, 0) + 1

        # Top 10 most frequently changed files
        frequently_changed = sorted(
            file_changes.items(),
            key=lambda x: x[1],
            reverse=True
        )[:10]

        # === AUTHOR EXPERTISE ===
        author_fixes: Dict[str, int] = {}
        author_security: Dict[str, int] = {}

        for commit in commits:
            author = commit['author']

            if commit['is_fix']:
                author_fixes[author] = author_fixes.get(author, 0) + 1

            if commit['is_security']:
                author_security[author] = author_security.get(author, 0) + 1

        # Top authors
        fix_authors = sorted(author_fixes.items(), key=lambda x: x[1], reverse=True)[:5]
        security_experts = sorted(author_security.items(), key=lambda x: x[1], reverse=True)[:5]

        # === COMMON FIX TYPES ===
        # Extract fix categories from commit messages
        fix_commits = [c for c in commits if c['is_fix']]
        fix_keywords = {}

        for commit in fix_commits:
            # Extract keywords after "fix" or "bug"
            text = commit['full_message'].lower()
            matches = re.findall(r'(?:fix|bug)(?:ed|es|ing)?\s+(\w+)', text)
            for keyword in matches:
                if len(keyword) > 3:  # Skip short words
                    fix_keywords[keyword] = fix_keywords.get(keyword, 0) + 1

        common_fix_types = sorted(fix_keywords.items(), key=lambda x: x[1], reverse=True)[:10]

        # === RECENT SECURITY FIXES ===
        security_commits = [c for c in commits if c['is_security']][:5]
        recent_security_fixes = [
            {
                'date': c['date'],
                'author': c['author'],
                'subject': c['subject'],
                'files': c['changed_files'][:3]  # Top 3 files
            }
            for c in security_commits
        ]

        self.pattern_cache = {
            'frequently_changed_files': frequently_changed,
            'fix_authors': fix_authors,
            'security_experts': security_experts,
            'common_fix_types': common_fix_types,
            'recent_security_fixes': recent_security_fixes,
            'total_commits': len(commits),
            'total_fixes': len(fix_commits),
            'total_security': len(security_commits)
        }

        logger.info(f"📊 Pattern analysis complete:")
        logger.info(f"   Total commits: {len(commits)}")
        logger.info(f"   Fix commits: {len(fix_commits)}")
        logger.info(f"   Security commits: {len(security_commits)}")
        logger.info(f"   Top changed file: {frequently_changed[0][0] if frequently_changed else 'N/A'}")

        return self.pattern_cache

    def get_relevant_commits(self, event_type: str, keywords: List[str] = None) -> List[Dict]:
        """
        Findet relevante Commits für einen Event-Type

        Args:
            event_type: z.B. 'trivy', 'fail2ban', 'docker'
            keywords: Zusätzliche Keywords zum Filtern

        Returns:
            Liste relevanter Commits
        """
        commits = self.load_commit_history()

        # Build search patterns
        search_patterns = [event_type.lower()]
        if keywords:
            search_patterns.extend([k.lower() for k in keywords])

        # Filter commits
        relevant = []
        for commit in commits:
            text = commit['full_message'].lower()

            # Check if any search pattern matches
            if any(pattern in text for pattern in search_patterns):
                relevant.append(commit)

        logger.debug(f"Found {len(relevant)} relevant commits for {event_type}")
        return relevant

    def get_code_changes_for_commit(self, commit_hash: str, max_lines: int = 200) -> str:
        """
        Holt Code-Changes (git diff) für einen Commit

        Args:
            commit_hash: Git commit hash
            max_lines: Maximale Zeilen des Diffs

        Returns:
            Diff als String (gekürzt wenn zu lang)
        """
        try:
            result = subprocess.run(
                ['git', 'show', '--pretty=format:', '--no-color', commit_hash],
                cwd=self.project_path,
                capture_output=True,
                text=True,
                timeout=15
            )

            if result.returncode != 0:
                return ""

            diff = result.stdout.strip()

            # Limit lines
            lines = diff.split('\n')
            if len(lines) > max_lines:
                diff = '\n'.join(lines[:max_lines]) + f"\n\n... (truncated, {len(lines) - max_lines} more lines)"

            return diff

        except Exception as e:
            logger.debug(f"Could not get diff for {commit_hash}: {e}")
            return ""

    def generate_context_for_ai(self, event_type: str = None, keywords: List[str] = None) -> str:
        """
        Generiert Context-String für AI-Prompts basiert auf Git-History

        Args:
            event_type: Optional - filtern nach Event-Type
            keywords: Optional - zusätzliche Keywords

        Returns:
            Formatierter Context-String für AI
        """
        try:
            # Load pattern analysis
            patterns = self.analyze_patterns()

            context_parts = []

            # === OVERVIEW ===
            context_parts.append("# GIT HISTORY INSIGHTS")
            context_parts.append(f"Analyzed: Last {self.days_to_analyze} days | {patterns['total_commits']} commits")
            context_parts.append("")

            # === FREQUENTLY CHANGED FILES ===
            if patterns['frequently_changed_files']:
                context_parts.append("## Frequently Changed Files (Potential Hot-Spots)")
                for file, count in patterns['frequently_changed_files'][:5]:
                    context_parts.append(f"- `{file}` ({count} changes)")
                context_parts.append("")

            # === RECENT SECURITY FIXES ===
            if patterns['recent_security_fixes']:
                context_parts.append("## Recent Security Fixes (Learn from these!)")
                for fix in patterns['recent_security_fixes']:
                    date_short = fix['date'][:10]  # YYYY-MM-DD
                    context_parts.append(f"- **{date_short}**: {fix['subject']}")
                    if fix['files']:
                        context_parts.append(f"  Files: {', '.join(fix['files'])}")
                context_parts.append("")

            # === COMMON FIX TYPES ===
            if patterns['common_fix_types']:
                context_parts.append("## Common Fix Categories")
                for keyword, count in patterns['common_fix_types'][:5]:
                    context_parts.append(f"- {keyword.title()} ({count}x)")
                context_parts.append("")

            # === RELEVANT COMMITS (if filtered) ===
            if event_type or keywords:
                relevant_commits = self.get_relevant_commits(event_type or '', keywords or [])

                if relevant_commits:
                    context_parts.append(f"## Relevant Commits for '{event_type}'")
                    for commit in relevant_commits[:3]:  # Top 3
                        date_short = commit['date'][:10]
                        context_parts.append(f"- **{date_short}** by {commit['author']}: {commit['subject']}")
                    context_parts.append("")

            # === EXPERT AUTHORS ===
            if patterns['security_experts']:
                context_parts.append("## Security Experts")
                for author, count in patterns['security_experts']:
                    context_parts.append(f"- {author} ({count} security commits)")
                context_parts.append("")

            return '\n'.join(context_parts)

        except Exception as e:
            logger.error(f"❌ Error generating git context: {e}", exc_info=True)
            return "# GIT HISTORY INSIGHTS\n(Error loading git history)\n"

    def get_statistics(self) -> Dict[str, Any]:
        """Gibt Statistiken über Git-History zurück"""
        patterns = self.analyze_patterns()

        return {
            'total_commits': patterns['total_commits'],
            'total_fixes': patterns['total_fixes'],
            'total_security': patterns['total_security'],
            'days_analyzed': self.days_to_analyze,
            'top_changed_file': patterns['frequently_changed_files'][0] if patterns['frequently_changed_files'] else None,
            'top_fix_author': patterns['fix_authors'][0] if patterns['fix_authors'] else None,
            'cache_size': len(self.commits_cache)
        }
