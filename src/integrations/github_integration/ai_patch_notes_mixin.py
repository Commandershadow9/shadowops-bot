"""
AI patch notes generation methods for GitHubIntegration.
"""

import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Dict, Optional

from integrations.git_history_analyzer import GitHistoryAnalyzer

logger = logging.getLogger('shadowops')


class AIPatchNotesMixin:

    def _collect_git_stats(self, commits: list, project_path: Optional[Path]) -> Dict:
        """
        Sammle Git-Statistiken aus Commits und Repository.

        Returns:
            Dict mit commits, files_changed, lines_added, lines_removed, contributors
        """
        stats = {
            'commits': len(commits),
            'files_changed': 0,
            'lines_added': 0,
            'lines_removed': 0,
            'contributors': [],
        }

        # Contributors aus Commits
        authors = set()
        for commit in commits:
            author = commit.get('author', {})
            name = author.get('name') or author.get('username', '')
            if name:
                authors.add(name)
        stats['contributors'] = sorted(authors)

        # Git diff stats berechnen
        if project_path and project_path.exists():
            try:
                # Ältesten und neuesten Commit-SHA finden
                shas = []
                for commit in commits:
                    sha = commit.get('id') or commit.get('sha') or commit.get('hash')
                    if sha:
                        shas.append(sha)

                if len(shas) >= 2:
                    diff_range = f"{shas[0]}^..{shas[-1]}"
                elif len(shas) == 1:
                    diff_range = f"{shas[0]}^..{shas[0]}"
                else:
                    diff_range = None

                if diff_range:
                    result = subprocess.run(
                        ['git', 'diff', '--shortstat', diff_range],
                        cwd=str(project_path),
                        capture_output=True,
                        text=True,
                        timeout=10
                    )
                    if result.returncode == 0 and result.stdout.strip():
                        output = result.stdout.strip()
                        # Parse: "23 files changed, 847 insertions(+), 203 deletions(-)"
                        files_match = re.search(r'(\d+) files? changed', output)
                        ins_match = re.search(r'(\d+) insertions?\(\+\)', output)
                        del_match = re.search(r'(\d+) deletions?\(-\)', output)

                        if files_match:
                            stats['files_changed'] = int(files_match.group(1))
                        if ins_match:
                            stats['lines_added'] = int(ins_match.group(1))
                        if del_match:
                            stats['lines_removed'] = int(del_match.group(1))
            except Exception as e:
                logger.debug(f"Git stats collection failed: {e}")

        # Test-Ergebnisse laden (falls vorhanden)
        test_results_path = Path(__file__).parent.parent.parent / 'data' / 'test_results.json'
        if test_results_path.exists():
            try:
                with open(test_results_path, 'r', encoding='utf-8') as f:
                    test_data = json.load(f)
                if test_data.get('status') in ('passed', 'failed'):
                    stats['tests_passed'] = test_data.get('tests_passed', 0)
                    stats['tests_total'] = test_data.get('tests_total', 0)
                    coverage = test_data.get('coverage_percent')
                    if coverage is not None and coverage != 'null':
                        stats['coverage_percent'] = float(coverage)
            except Exception as e:
                logger.debug(f"Test results loading failed: {e}")

        return stats

    def _format_stats_line(self, stats: Dict, language: str = 'de') -> str:
        """Formatiere Stats als einzeilige Zusammenfassung für Discord."""
        parts = []
        commits = stats.get('commits', 0)
        files = stats.get('files_changed', 0)
        added = stats.get('lines_added', 0)

        if commits > 0:
            parts.append(f"{commits} Commits")
        if files > 0:
            parts.append(f"{files} Dateien" if language == 'de' else f"{files} files")
        if added > 0:
            removed = stats.get('lines_removed', 0)
            parts.append(f"+{added}/-{removed} Zeilen" if language == 'de' else f"+{added}/-{removed} lines")

        tests_total = stats.get('tests_total')
        tests_passed = stats.get('tests_passed')
        if tests_total is not None and tests_total > 0:
            parts.append(f"{tests_passed}/{tests_total} Tests ✅")

        coverage = stats.get('coverage_percent')
        if coverage is not None:
            parts.append(f"{coverage:.0f}% Coverage")

        if not parts:
            return ""

        return "📊 " + " · ".join(parts)

    def _format_stats_section(self, stats: Dict, language: str = 'de') -> str:
        """Formatiere Stats als Kontext-Sektion für den AI-Prompt."""
        if stats.get('commits', 0) < 5:
            return ""

        if language == 'de':
            section = "# RELEASE-STATISTIKEN\n"
        else:
            section = "# RELEASE STATS\n"

        section += f"- {stats.get('commits', 0)} Commits\n"
        section += f"- {stats.get('files_changed', 0)} Dateien geändert\n"
        section += f"- +{stats.get('lines_added', 0)} / -{stats.get('lines_removed', 0)} Zeilen\n"

        contributors = stats.get('contributors', [])
        if contributors:
            section += f"- Contributors: {', '.join(contributors)}\n"

        return section

    def _split_embed_description(self, description: str, max_length: int = 4096) -> list[str]:
        """
        Split a long description into multiple chunks that fit Discord's limits.

        Args:
            description: The full description text
            max_length: Maximum length per chunk (Discord limit: 4096)

        Returns:
            List of description chunks
        """
        if len(description) <= max_length:
            return [description]

        chunks = []
        current_chunk = ""

        # Split by paragraphs first (double newline)
        paragraphs = description.split('\n\n')

        for paragraph in paragraphs:
            # If adding this paragraph would exceed limit, save current chunk
            if len(current_chunk) + len(paragraph) + 2 > max_length:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                    current_chunk = ""

                # If single paragraph is too long, split by lines
                if len(paragraph) > max_length:
                    lines = paragraph.split('\n')
                    for line in lines:
                        if len(current_chunk) + len(line) + 1 > max_length:
                            if current_chunk:
                                chunks.append(current_chunk.strip())
                            current_chunk = line + "\n"
                        else:
                            current_chunk += line + "\n"
                else:
                    current_chunk = paragraph + "\n\n"
            else:
                current_chunk += paragraph + "\n\n"

        # Add remaining chunk
        if current_chunk.strip():
            chunks.append(current_chunk.strip())

        return chunks

    def _format_user_friendly_commit(self, message: str) -> str:
        """Convert technical commit message to user-friendly text."""
        # Remove conventional commit prefixes
        message = message.replace('feat:', '').replace('fix:', '').replace('chore:', '')
        message = message.replace('docs:', '').replace('style:', '').replace('refactor:', '')
        message = message.replace('perf:', '').replace('test:', '').replace('build:', '')
        message = message.replace('ci:', '').replace('improve:', '').replace('update:', '')

        # Remove issue references for cleaner look (keep in internal)
        message = re.sub(r'\(#\d+\)', '', message)
        message = re.sub(r'#\d+', '', message)
        message = re.sub(r'Fixes? #\d+', '', message, flags=re.IGNORECASE)
        message = re.sub(r'Closes? #\d+', '', message, flags=re.IGNORECASE)

        # Clean up whitespace
        message = ' '.join(message.split())
        message = message.strip().strip(':').strip()

        # Capitalize first letter
        if message:
            message = message[0].upper() + message[1:]

        return message

    def _build_code_changes_context(self, commits: list, project_path: Optional[Path]) -> str:
        """Build a truncated diff summary for a handful of commits."""
        if not self.patch_notes_include_diffs or not commits or not project_path:
            return ""

        try:
            repo_path = Path(project_path)
        except Exception:
            return ""

        analyzer = GitHistoryAnalyzer(str(repo_path))
        if not analyzer.is_git_repository():
            return ""

        max_commits = min(self.patch_notes_diff_max_commits, len(commits))
        if max_commits <= 0:
            return ""

        sections = []
        for commit in commits[-max_commits:]:
            commit_id = commit.get('id') or commit.get('sha') or commit.get('hash')
            if not commit_id:
                continue
            diff = analyzer.get_code_changes_for_commit(commit_id, self.patch_notes_diff_max_lines)
            if not diff:
                continue
            title = commit.get('message', '').split('\n')[0].strip()
            short_id = commit_id[:7]
            label = f"{short_id} {title}".strip()
            sections.append(f"## {label}\n{diff}")

        if not sections:
            return ""

        return "CODE CHANGES (DIFF SUMMARY, MAY BE TRUNCATED):\n\n" + "\n\n".join(sections)

    def _load_patch_notes_context(self, project_config: Optional[Dict],
                                  project_path: Optional[Path]) -> str:
        """Load optional context files for richer patch notes prompts."""
        if not project_config:
            return ""

        patch_config = project_config.get('patch_notes', {})
        context_files = patch_config.get('context_files') or patch_config.get('context_file')
        if not context_files:
            return ""

        if isinstance(context_files, str):
            context_files = [context_files]
        if not isinstance(context_files, list):
            return ""

        base_path = project_path
        if not base_path:
            base = project_config.get('path', '')
            base_path = Path(base) if base else None

        per_file_limit = int(patch_config.get('context_max_chars', 1500))
        total_limit = int(patch_config.get('context_total_max_chars', 4000))

        sections = []
        total_chars = 0

        for entry in context_files:
            if not entry:
                continue
            entry_path = Path(entry)
            if not entry_path.is_absolute() and base_path:
                entry_path = base_path / entry_path
            if not entry_path.exists():
                continue
            try:
                content = entry_path.read_text(encoding='utf-8', errors='ignore').strip()
            except Exception:
                continue

            if not content:
                continue

            if per_file_limit > 0 and len(content) > per_file_limit:
                head_len = max(1, per_file_limit // 2)
                tail_len = per_file_limit - head_len
                content = (
                    content[:head_len].rstrip()
                    + "\n... (snip) ...\n"
                    + content[-tail_len:].lstrip()
                )

            section = f"PROJECT CONTEXT FILE: {entry_path.name}\n{content}"
            total_chars += len(section)
            if total_limit > 0 and total_chars > total_limit:
                break
            sections.append(section)

        if not sections:
            return ""

        return "PROJECT CONTEXT (REFERENCE):\n\n" + "\n\n".join(sections)

    def _build_changelog_fallback_description(self, project_config: Optional[Dict], language: str) -> str:
        """Build a user-facing description from CHANGELOG.md if present."""
        if not project_config:
            return ""

        project_path = project_config.get('path')
        if not project_path:
            return ""

        changelog_path = Path(project_path) / 'CHANGELOG.md'
        if not changelog_path.exists():
            return ""

        try:
            from utils.changelog_parser import get_changelog_parser
            parser = get_changelog_parser(Path(project_path))
            version = parser.get_latest_version()
            if not version:
                return ""
            version_data = parser.get_version_section(version)
            if not version_data:
                return ""

            header = f"**Version {version}**"
            if version_data.get('title'):
                header = f"{header} — {version_data['title']}"

            content = version_data.get('content', '').strip()
            if not content:
                return ""

            # Keep changelog content as the primary source (already structured).
            if language == 'de':
                return f"{header}\n\n{content}"
            return f"{header}\n\n{content}"
        except Exception as e:
            self.logger.warning(f"⚠️ CHANGELOG Fallback failed: {e}")
            return ""

    def _is_patch_notes_too_short(self, response: str, commits: list) -> bool:
        """Heuristic to detect underspecified AI output."""
        if not response:
            return True

        bullet_count = sum(
            1 for line in response.splitlines()
            if line.strip().startswith('•')
        )
        commit_detail = any(
            len([l for l in (c.get('message') or '').splitlines() if l.strip()]) >= 5
            for c in commits
        )

        if len(commits) > 1 or commit_detail:
            min_bullets = 3
        else:
            min_bullets = 1

        if bullet_count < min_bullets:
            return True

        if commit_detail and len(response) < 300:
            return True

        return False

    async def _generate_ai_patch_notes(self, commits: list, language: str, repo_name: str,
                                       project_config: Optional[Dict] = None) -> Optional[str]:
        """
        Generate professional, user-friendly patch notes using AI with training system.

        NEW: Uses CHANGELOG.md + AI Training System for better quality!

        Process:
        1. Load CHANGELOG.md if available (fullest information source)
        2. Use PatchNotesTrainer to build enhanced prompt with examples
        3. Generate patch notes with AI
        4. Calculate quality score
        5. Save high-quality examples for future training

        Args:
            commits: List of commit dictionaries
            language: 'de' or 'en'
            repo_name: Repository name for context
            project_config: Optional project configuration

        Returns:
            AI-generated patch notes string or None if failed
        """
        if not self.ai_service or not commits:
            return None

        # Try to get CHANGELOG content
        changelog_content = ""
        version = None
        version_data = None

        project_path = None
        if project_config:
            project_path = Path(project_config.get('path', ''))
            changelog_path = project_path / 'CHANGELOG.md'

            if changelog_path.exists():
                try:
                    # Detect version from commits
                    for commit in commits:
                        msg = commit.get('message', '')
                        match = re.search(r'v?(?:ersion|elease)?\s*([0-9]+\.[0-9]+\.[0-9]+)', msg, re.IGNORECASE)
                        if match:
                            version = match.group(1)
                            break

                    from utils.changelog_parser import get_changelog_parser
                    parser = get_changelog_parser(project_path)

                    # Get CHANGELOG section if version found
                    if version:
                        version_data = parser.get_version_section(version)

                        if version_data:
                            changelog_content = version_data['content']
                            self.logger.info(f"📖 Using CHANGELOG.md section for v{version} ({len(changelog_content)} chars)")
                        else:
                            self.logger.info(f"⚠️ Version {version} not found in CHANGELOG, using commits only")
                    else:
                        # Fallback: use latest version from CHANGELOG
                        latest = parser.get_latest_version()
                        if latest:
                            version = latest
                            version_data = parser.get_version_section(version)
                            if version_data:
                                changelog_content = version_data['content']
                                self.logger.info(
                                    f"📖 Using latest CHANGELOG.md section for v{version} "
                                    f"({len(changelog_content)} chars)"
                                )
                        if not changelog_content:
                            self.logger.info("⚠️ No version detected in commits, using commits only")

                except Exception as e:
                    self.logger.warning(f"⚠️ Could not parse CHANGELOG: {e}")

        project_context = self._load_patch_notes_context(project_config, project_path)

        # Collect git stats
        git_stats = self._collect_git_stats(commits, project_path)
        stats_line = self._format_stats_line(git_stats, language)
        stats_section = self._format_stats_section(git_stats, language)

        # Build enhanced prompt with A/B Testing
        selected_variant = None
        variant_id = None

        code_changes_context = self._build_code_changes_context(commits, project_path)

        if self.patch_notes_trainer and self.prompt_ab_testing and (changelog_content or project_config):
            try:
                # Select prompt variant using A/B testing (weighted by performance)
                selected_variant = self.prompt_ab_testing.select_variant(
                    project=repo_name,
                    strategy='weighted_random'
                )
                variant_id = selected_variant.id

                self.logger.info(f"🧪 A/B Test: Using variant '{selected_variant.name}' (ID: {variant_id}) with language '{language}'")

                # Build prompt from variant template (language-specific)
                variant_template = self.prompt_ab_testing.get_variant_template(
                    variant_id=variant_id,
                    language=language
                )
                # format_map mit DefaultDict um KeyError bei alten Templates zu vermeiden
                from collections import defaultdict

                format_values = defaultdict(str, {
                    'project': repo_name,
                    'changelog': changelog_content or "No CHANGELOG available",
                    'commits': '\n'.join([f"- {c.get('message', '')}" for c in commits[:10]]),
                    'stats_section': stats_section,
                    'stats_line': stats_line if git_stats.get('commits', 0) >= 5 else '',
                })
                prompt = variant_template.format_map(format_values)

                # Add examples from trainer
                if self.patch_notes_trainer.good_examples:
                    prompt += "\n\n# EXAMPLES OF HIGH-QUALITY PATCH NOTES\n\n"
                    for i, example in enumerate(self.patch_notes_trainer.good_examples[:2], 1):
                        prompt += f"## Example {i} ({example['project']} v{example['version']}):\n"
                        prompt += f"```\n{example['generated_notes'][:400]}...\n```\n\n"

                if code_changes_context:
                    prompt += f"\n\n{code_changes_context}"
                if project_context:
                    prompt += f"\n\n{project_context}"

            except Exception as e:
                self.logger.warning(f"⚠️ A/B Testing failed, using enhanced prompt: {e}")
                try:
                    prompt = self.patch_notes_trainer.build_enhanced_prompt(
                        changelog_content=changelog_content,
                        commits=commits,
                        language=language,
                        project=repo_name
                    )
                    if code_changes_context:
                        prompt += f"\n\n{code_changes_context}"
                    if project_context:
                        prompt += f"\n\n{project_context}"
                    self.logger.info(f"🎯 Using enhanced AI prompt with training examples")
                except Exception as e2:
                    self.logger.warning(f"⚠️ Enhanced prompt failed, using fallback: {e2}")
                    prompt = self._build_fallback_prompt(
                        commits,
                        language,
                        repo_name,
                        changelog_content,
                        code_changes_context,
                        project_context
                    )
        else:
            # Fallback to original prompt if no trainer available
            prompt = self._build_fallback_prompt(
                commits,
                language,
                repo_name,
                changelog_content,
                code_changes_context,
                project_context
            )

        # Log commits being processed
        num_commits = len(commits)
        self.logger.info(f"🔍 AI Processing {num_commits} commit(s) for {repo_name}:")
        for i, commit in enumerate(commits[:5], 1):
            msg = commit.get('message', '').split('\n')[0]
            self.logger.info(f"   {i}. {msg}")
        if num_commits > 5:
            self.logger.info(f"   ... and {num_commits - 5} more commits")

        # Call AI Service
        patch_config = project_config.get('patch_notes', {}) if project_config else {}
        use_critical_model = patch_config.get('use_critical_model', True)
        try:
            ai_response = await self.ai_service.get_raw_ai_response(
                prompt=prompt,
                use_critical_model=use_critical_model
            )

            if not ai_response:
                return None

            # Clean up response
            response = ai_response.strip()

            # Ensure it starts with a category
            if not response.startswith('**'):
                lines = response.split('\n')
                start_idx = 0
                for i, line in enumerate(lines):
                    if line.startswith('**'):
                        start_idx = i
                        break
                response = '\n'.join(lines[start_idx:])

            if self._is_patch_notes_too_short(response, commits):
                self.logger.info("⚠️ AI Patch Notes zu kurz, starte zweiten Durchlauf mit striktem Prompt")
                strict_prompt = self._build_fallback_prompt(
                    commits,
                    language,
                    repo_name,
                    changelog_content,
                    code_changes_context,
                    project_context,
                    strict=True
                )
                retry = await self.ai_service.get_raw_ai_response(
                    prompt=strict_prompt,
                    use_critical_model=True
                )
                if retry:
                    retry = retry.strip()
                    if not retry.startswith('**'):
                        retry_lines = retry.split('\n')
                        retry_start = 0
                        for i, line in enumerate(retry_lines):
                            if line.startswith('**'):
                                retry_start = i
                                break
                        retry = '\n'.join(retry_lines[retry_start:])
                    if retry and len(retry) >= len(response):
                        response = retry

            # Calculate quality score and record A/B test result
            quality_context = changelog_content or project_context
            if self.patch_notes_trainer and quality_context:
                try:
                    if not version and commits:
                        version = (commits[-1].get('id') or commits[-1].get('sha') or '')[:7] or None
                    quality_score = self.patch_notes_trainer.calculate_quality_score(
                        generated_notes=response,
                        changelog_content=quality_context
                    )
                    self.logger.info(f"📊 Patch Notes Quality Score: {quality_score:.1f}/100")

                    # Record A/B test result if variant was used
                    if self.prompt_ab_testing and variant_id:
                        self.prompt_ab_testing.record_result(
                            variant_id=variant_id,
                            project=repo_name,
                            version=version,
                            quality_score=quality_score,
                            user_feedback_score=0.0  # Will be updated from reactions
                        )
                        self.logger.info(f"🧪 A/B Test result recorded for variant {variant_id}")

                        # Schedule auto-tuning check (runs if conditions met)
                        if self.prompt_auto_tuner:
                            try:
                                self.prompt_auto_tuner.schedule_auto_tuning(
                                    project=repo_name,
                                    min_samples=10,
                                    improvement_threshold=5.0
                                )
                            except Exception as e:
                                self.logger.debug(f"Auto-tuning check skipped: {e}")

                    # Save as training example when a version is available
                    if version:
                        self.patch_notes_trainer.save_example(
                            version=version,
                            changelog_content=quality_context,
                            generated_notes=response,
                            quality_score=quality_score,
                            project=repo_name
                        )

                    if quality_score >= 80:
                        self.logger.info(f"🌟 High-quality patch notes! Saved as training example.")
                except Exception as e:
                    self.logger.warning(f"⚠️ Quality scoring failed: {e}")

            # Append stats line to response if substantial release
            if stats_line and git_stats.get('commits', 0) >= 5:
                # Nur anhängen wenn nicht schon im AI-Output enthalten
                if '📊' not in response:
                    response = response.rstrip() + f"\n\n{stats_line}"

            self.logger.info(f"✅ AI generated patch notes for {repo_name} ({len(response)} chars)")

            # Store stats for web export (accessible via attribute)
            self._last_git_stats = git_stats
            self._last_version = version

            return response if response else None

        except Exception as e:
            self.logger.error(f"AI patch notes generation failed: {e}")
            return None

    def _build_fallback_prompt(self, commits: list, language: str, repo_name: str,
                               changelog_content: str = "", code_changes_context: str = "",
                               project_context: str = "", strict: bool = False) -> str:
        """Build fallback prompt when trainer is not available."""
        # Build commit summary for AI
        commit_summaries = []
        for commit in commits:
            full_msg = commit.get('message', '')
            lines = full_msg.split('\n')
            title = lines[0]

            # Get body (skip empty lines after title)
            body_lines = []
            for line in lines[1:]:
                if line.strip():
                    body_lines.append(line)

            author = commit.get('author', {}).get('name', 'Unknown')

            # Include full message if it has substantial body
            if len(body_lines) > 2:
                body = '\n'.join(body_lines[:30])
                commit_summaries.append(f"- {title}\n  {body}\n  (by {author})")
            else:
                commit_summaries.append(f"- {title} (by {author})")

        commits_text = "\n".join(commit_summaries)
        num_commits = len(commits)
        detail_instruction = ""

        extra_sections = []
        if changelog_content:
            extra_sections.append(f"CHANGELOG INFORMATION:\n{changelog_content}")
        if code_changes_context:
            extra_sections.append(code_changes_context)
        if project_context:
            extra_sections.append(project_context)
        extra_context = "\n\n".join(extra_sections).strip()
        if extra_context:
            extra_context = f"\n\n{extra_context}\n"

        if num_commits > 30:
            # Many commits - ask for high-level overview
            if language == 'de':
                detail_instruction = f"\n\n⚠️ WICHTIG: Es gibt {num_commits} Commits! Erstelle eine HIGH-LEVEL Übersicht. Gruppiere ähnliche Commits und beschreibe große Features detailliert, aber fasse Kleinigkeiten zusammen."
            else:
                detail_instruction = f"\n\n⚠️ IMPORTANT: There are {num_commits} commits! Create a HIGH-LEVEL overview. Group similar commits and describe major features in detail, but summarize minor changes."
        elif num_commits > 15:
            # Medium amount - balanced approach, but RECOGNIZE major features
            if language == 'de':
                detail_instruction = f"\n\n⚠️ Es gibt {num_commits} Commits. Gruppiere verwandte Commits (z.B. alle zum gleichen Feature-Namen) zu EINEM detaillierten Feature-Punkt. Release-Features sind GROSS und benötigen detaillierte Erklärung!"
            else:
                detail_instruction = f"\n\n⚠️ There are {num_commits} commits. Group related commits (e.g., all commits for the same feature) into ONE detailed feature point. Release features are MAJOR and need detailed explanation!"

        # Build prompt based on language
        strict_rules = ""
        if strict:
            min_bullets = max(3, len(commits))
            min_chars = 400 if len(commits) > 1 else 250
            if language == 'de':
                strict_rules = (
                    "\n\nSTRICTE QUALITAETSREGELN:\n"
                    f"- Nutze mindestens {min_bullets} Bulletpoints (bei vorhandenen Detail-Infos).\n"
                    f"- Ziel: mindestens {min_chars} Zeichen, wenn Commit-Body oder Diff Details enthalten.\n"
                    "- Erzeuge KEINE Einzeiler-Ausgabe.\n"
                )
            else:
                strict_rules = (
                    "\n\nSTRICT QUALITY RULES:\n"
                    f"- Use at least {min_bullets} bullet points when detailed info exists.\n"
                    f"- Target at least {min_chars} characters if commit body or diff includes details.\n"
                    "- Do NOT return a single-line answer.\n"
                )

        if language == 'de':
            prompt = f"""Du bist ein professioneller Technical Writer. Erstelle benutzerfreundliche Patch Notes für das Projekt "{repo_name}".

COMMITS (VOLLSTÄNDIGE LISTE):
{commits_text}
{extra_context}

KRITISCHE REGELN:
⚠️ BESCHREIBE NUR ÄNDERUNGEN DIE WIRKLICH IN DEN COMMITS OBEN STEHEN!
⚠️ ERFINDE KEINE FEATURES ODER FIXES DIE NICHT IN DER COMMIT-LISTE SIND!
⚠️ Wenn ein Commit unklar ist, überspringe ihn lieber als zu raten!
⚠️ Nutze CHANGELOG INFORMATION und CODE CHANGES (falls vorhanden) für Details.
⚠️ Wenn Texte "offen", "todo", "still open" oder "risiken" nennen, markiere sie NICHT als abgeschlossen.

WICHTIG - ZUSAMMENHÄNGENDE FEATURES ERKENNEN:
🔍 Suche nach VERWANDTEN Commits die zusammengehören (z.B. mehrere "fix:" oder "feat:" Commits für das gleiche Feature)
🔍 Release-Commits (feat: Release v...) enthalten oft GROSSE Änderungen - beschreibe diese DETAILLIERT!
🔍 Commit-Serien mit gleichem Feature-Namen sind EINZELNE Features, nicht getrennte Punkte!
🔍 Bei großen Refactorings: Erkenne die GESAMTBEDEUTUNG, nicht nur Einzelschritte!
🔍 Wenn Commit-Bodies Abschnitte enthalten (z.B. "Rate Limiting:", "Monitoring:"), nutze pro Abschnitt einen Bulletpoint.
🔍 Bei reinen Doku-/Status-Updates: als Status-Update zusammenfassen, keine Features erfinden.

AUFGABE:
Fasse diese Commits zu professionellen, DETAILLIERTEN Patch Notes zusammen:{detail_instruction}{strict_rules}

1. GRUPPIERE verwandte Commits zu EINEM ausführlichen Bulletpoint
2. Kategorisiere in: 🆕 Neue Features, 🐛 Bugfixes, ⚡ Verbesserungen
3. Verwende einfache, klare Sprache aber sei AUSFÜHRLICH
4. Beginne mit Nutzer-Nutzen, danach technische Details
5. Bei großen Features: 3-5 Sätze oder Sub-Bulletpoints mit Details
6. Entferne Jargon und technische Präfixe
7. Zielgruppe: Endkunden die verstehen wollen was sich verbessert hat
8. Maximal 8000 Zeichen - nutze den Platz aus!
9. Erfinde keine Details und wiederhole keine Beispiel-Formulierungen.
9. Erfinde keine Details und verwende keine Beispiel-Formulierungen aus dem Prompt.

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

Erstelle JETZT die DETAILLIERTEN Patch Notes basierend auf den ECHTEN Commits oben (nur die Kategorien + Bulletpoints, keine Einleitung):"""
        else:  # English
            prompt = f"""You are a professional Technical Writer. Create user-friendly patch notes for the project "{repo_name}".

COMMITS (COMPLETE LIST):
{commits_text}
{extra_context}

CRITICAL RULES:
⚠️ ONLY DESCRIBE CHANGES THAT ARE ACTUALLY IN THE COMMITS ABOVE!
⚠️ NEVER INVENT FEATURES OR FIXES THAT ARE NOT IN THE COMMIT LIST!
⚠️ If a commit is unclear, skip it rather than guessing!
⚠️ Use CHANGELOG INFORMATION and CODE CHANGES (if present) for details.
⚠️ If text says "open", "todo", "still open", or "risks", do NOT mark it as completed.

IMPORTANT - RECOGNIZE RELATED FEATURES:
🔍 Look for RELATED commits that belong together (e.g., multiple "fix:" or "feat:" commits for the same feature)
🔍 Release commits (feat: Release v...) often contain MAJOR changes - describe these in DETAIL!
🔍 Commit series with the same feature name are SINGLE features, not separate items!
🔍 For large refactorings: Recognize the OVERALL SIGNIFICANCE, not just individual steps!
🔍 If commit bodies include sections (e.g., "Rate Limiting:"), use one bullet per section.
🔍 For doc/status-only updates: summarize as status updates; do not invent features.

TASK:
Summarize these commits into professional, DETAILED patch notes:{detail_instruction}{strict_rules}

1. GROUP related commits into ONE comprehensive bulletpoint
2. Categorize into: 🆕 New Features, 🐛 Bug Fixes, ⚡ Improvements
3. Use simple, clear language but be COMPREHENSIVE
4. Lead with user impact, then technical details
5. For major features: 3-5 sentences or sub-bulletpoints with details
6. Remove jargon and technical prefixes
7. Target audience: End customers who want to understand what improved
8. Maximum 8000 characters - use the space!
9. Do not invent details or reuse example wording.
9. Do not invent details and do not reuse example wording from the prompt.

FORMAT:
Use Markdown with ** for categories and • for main points.
For complex features: Use sub-bulletpoints (indented with 2 spaces).

FORMAT EXAMPLE:
**🆕 New Features:**
• **Feature Name**: Detailed description of what the feature does and why it's important.
  - First benefit or technical detail
  - Second benefit or technical detail
  - Third benefit or technical detail

**🐛 Bug Fixes:**
• **Bug Category**: What was fixed and what problem it caused

**⚡ Improvements:**
• **Improvement**: Detailed description of the improvement

Create the DETAILED patch notes NOW based on the REAL commits above (only categories + bulletpoints, no introduction):"""

        return prompt
