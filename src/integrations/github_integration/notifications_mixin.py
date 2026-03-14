"""
Discord notification methods for GitHubIntegration.

v3: Teaser-Embed mit Kategorien, integrierte Buttons, Content Sanitizer
"""

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

import discord

from integrations.content_sanitizer import ContentSanitizer

logger = logging.getLogger('shadowops')


class NotificationsMixin:

    async def _send_push_notification(
        self, repo_name: str, repo_url: str, branch: str, pusher: str, commits: list
    ):
        """Send detailed Discord notification for a push event."""
        # Find project config to get color and potential customer channel (case-insensitive)
        project_config = {}
        project_config_key = repo_name

        # Try case-insensitive lookup for project config
        for key in self.config.projects.keys():
            if key.lower() == repo_name.lower():
                project_config = self.config.projects[key]
                project_config_key = key
                break

        if not project_config:
            project_config = self.config.projects.get(repo_name, {})

        project_color = project_config.get('color', 0x3498DB)  # Default blue
        patch_config = project_config.get('patch_notes', {})
        language = patch_config.get('language', 'de')

        # === BATCHING CHECK ===
        if hasattr(self, 'patch_notes_batcher') and self.patch_notes_batcher:
            if self.patch_notes_batcher.should_batch(commits, repo_name):
                result = self.patch_notes_batcher.add_commits(repo_name, commits)

                if result['ready']:
                    # Batch-Threshold erreicht — alle gesammelten Commits freigeben
                    all_commits = self.patch_notes_batcher.release_batch(repo_name)
                    if all_commits:
                        self.logger.info(f"🚀 Batch-Release: {len(all_commits)} Commits")
                        commits = all_commits
                    # Weiter mit normaler Verarbeitung
                else:
                    # Noch nicht genug — nur loggen, KEIN Discord-Spam
                    self.logger.info(
                        f"📦 {result['total_pending']}/{self.patch_notes_batcher.batch_threshold} "
                        f"Commits für {repo_name} gesammelt (kein Release)"
                    )
                    return

        # === INTERNAL EMBED (Technical, for developers) - DEUTSCH ===
        commits_url = f"{repo_url}/commits/{branch}" if repo_url else None
        internal_embed = discord.Embed(
            title=f"🚀 Code-Update: {repo_name}",
            url=commits_url,
            color=project_color,
            timestamp=datetime.now(timezone.utc)
        )
        internal_embed.set_author(name=pusher)
        internal_embed.add_field(name="Branch", value=branch, inline=True)
        internal_embed.add_field(name="Commits", value=str(len(commits)), inline=True)

        commit_details = []
        for commit in commits:
            sha = commit['id'][:7]
            author = commit['author']['name']
            message = commit['message'].split('\n')[0]  # First line of commit message
            url = commit['url']
            if url:
                commit_details.append(f"[`{sha}`]({url}) {message} - *{author}*")
            else:
                commit_details.append(f"`{sha}` {message} - *{author}*")

        if commit_details:
            internal_embed.description = "\n".join(commit_details)
        else:
            internal_embed.description = "Keine neuen Commits in diesem Push."

        # === ADVANCED PATCH NOTES SYSTEM (if available) ===
        if self.patch_notes_manager and patch_config.get('use_advanced_system', False):
            try:
                self.logger.info(f"🎯 Using advanced patch notes system for {repo_name}")
                await self.patch_notes_manager.handle_git_push(
                    project_name=repo_name,
                    project_config=project_config,
                    commits=commits,
                    repo_name=repo_name
                )
                return
            except Exception as e:
                self.logger.warning(f"⚠️ Advanced system failed, falling back to legacy: {e}", exc_info=True)

        # === AI-GENERATED PATCH NOTES ===
        use_ai = patch_config.get('use_ai', False)

        ai_result = None
        git_stats = {}
        if use_ai and self.ai_service:
            try:
                self.logger.info(f"🤖 Generiere KI Patch Notes für {repo_name} (Sprache: {language})...")
                ai_result, git_stats = await self._generate_ai_patch_notes(commits, language, repo_name, project_config)
                if ai_result:
                    result_type = "strukturiert" if isinstance(ai_result, dict) else "Raw-Text"
                    self.logger.info(f"✅ KI Patch Notes erfolgreich generiert ({result_type})")
            except Exception as e:
                self.logger.warning(f"⚠️ KI Patch Notes Generierung fehlgeschlagen, verwende Fallback: {e}")

        # === CONTENT SANITIZER ===
        security_config = patch_config.get('security', {})
        if security_config.get('sanitize', True):  # Default: aktiv
            sanitizer = ContentSanitizer(
                custom_patterns=security_config.get('custom_redact_patterns', []),
                enabled=True,
            )
            if isinstance(ai_result, dict):
                ai_result = sanitizer.sanitize_dict(ai_result)
                # Verschachtelte Felder: changes[].description und breaking_changes[]
                if 'changes' in ai_result:
                    for change in ai_result['changes']:
                        if isinstance(change, dict) and 'description' in change:
                            change['description'] = sanitizer.sanitize(change['description'])
                if 'breaking_changes' in ai_result:
                    ai_result['breaking_changes'] = [
                        sanitizer.sanitize(b) if isinstance(b, str) else b
                        for b in ai_result['breaking_changes']
                    ]
            elif isinstance(ai_result, str):
                ai_result = sanitizer.sanitize(ai_result)

        # === BUILD CUSTOMER EMBED + WEB EXPORT ===
        if isinstance(ai_result, dict) and ai_result.get('discord_highlights'):
            # v3: Neues Embed-Format mit Teaser-Stil
            customer_embed = self._build_v3_customer_embed(
                repo_name, project_color, commits, language,
                ai_result, project_config
            )
            await self._export_structured_web_changelog(
                repo_name, commits, ai_result, project_config, language
            )
        else:
            # Fallback: Legacy-Format
            ai_description = ai_result if isinstance(ai_result, str) else None
            customer_embed = self._build_customer_embed(
                repo_name, commits_url, project_color, commits, language,
                ai_description, project_config, git_stats
            )
            await self._export_web_changelog(
                repo_name, commits, ai_description, project_config, language, git_stats
            )

        # 1. Send to internal channel (technical embed + AI preview)
        await self._send_to_internal_channel(internal_embed, repo_name)
        await self._send_ai_preview_to_internal(customer_embed, repo_name)

        # 2. Send to customer-facing channels with feedback collection
        version = self._extract_version_from_commits(commits)
        await self._send_to_customer_channels(customer_embed, repo_name, project_config, version)

        # 3. Send to external notification channels (customer servers) WITH feedback collection
        await self._send_external_git_notifications(repo_name, customer_embed, project_config, version)

    def _build_customer_embed(self, repo_name: str, commits_url: str,
                               project_color: int, commits: list, language: str,
                               ai_description: Optional[str],
                               project_config: Dict,
                               git_stats: Optional[Dict] = None) -> discord.Embed:
        """Baue das Customer-Embed (Kurzformat für Discord)."""
        patch_config = project_config.get('patch_notes', {})

        # Language-specific texts
        if language == 'en':
            title_text = f"✨ Updates for {repo_name}"
        else:
            title_text = f"✨ Updates für {repo_name}"

        customer_embed = discord.Embed(
            title=title_text,
            url=commits_url,
            color=project_color,
            timestamp=datetime.now(timezone.utc)
        )

        if ai_description:
            customer_embed.description = ai_description
        else:
            # Fallback: Changelog oder Kategorisierung
            changelog_fallback = self._build_changelog_fallback_description(project_config, language)
            if changelog_fallback:
                customer_embed.description = changelog_fallback
            else:
                customer_embed.description = self._categorize_commits_text(commits, language)

        # Web-Link hinzufügen (falls konfiguriert)
        changelog_url = patch_config.get('changelog_url', '')
        if changelog_url:
            if language == 'de':
                customer_embed.add_field(
                    name="📖 Alle Details",
                    value=f"[Vollständige Patch Notes auf der Webseite]({changelog_url})",
                    inline=False
                )
            else:
                customer_embed.add_field(
                    name="📖 Full Details",
                    value=f"[Complete patch notes on the website]({changelog_url})",
                    inline=False
                )

        # Footer mit Stats
        footer_parts = [f"{len(commits)} Commit(s)"]

        if git_stats:
            files = git_stats.get('files_changed', 0)
            if files > 0:
                footer_parts.append(f"{files} Dateien")
            added = git_stats.get('lines_added', 0)
            removed = git_stats.get('lines_removed', 0)
            if added > 0:
                footer_parts.append(f"+{added}/-{removed}")

        customer_embed.set_footer(text=" · ".join(footer_parts))

        return customer_embed

    def _build_structured_customer_embed(self, repo_name: str, commits_url: str,
                                          project_color: int, commits: list, language: str,
                                          ai_data: Dict, project_config: Dict) -> discord.Embed:
        """Professionelles Discord-Embed aus strukturiertem AI-Output."""
        patch_config = project_config.get('patch_notes', {})

        # Titel: Projektname + AI-Titel
        title = ai_data.get('title', f'Updates für {repo_name}')
        embed = discord.Embed(
            title=f"✨ {repo_name} — {title}",
            url=commits_url,
            color=project_color,
            timestamp=datetime.now(timezone.utc)
        )

        # TL;DR als Description
        tldr = ai_data.get('tldr', '')
        if tldr:
            embed.description = f"**TL;DR:** {tldr}"

        # Discord-Highlights als Hauptfeld
        highlights = ai_data.get('discord_highlights', [])
        if highlights:
            highlights_text = "\n".join(f"• {h}" for h in highlights[:5])
            embed.add_field(
                name="🔥 Highlights",
                value=highlights_text,
                inline=False
            )

        # Breaking Changes separat hervorheben
        breaking = ai_data.get('breaking_changes', [])
        if breaking:
            breaking_text = "\n".join(f"⚠️ {b}" for b in breaking[:3])
            embed.add_field(name="⚠️ Breaking Changes", value=breaking_text, inline=False)

        # Web-Link
        changelog_url = patch_config.get('changelog_url', '')
        if changelog_url:
            v = ai_data.get('version') or self._extract_version_from_commits(commits)
            if v:
                full_url = f"{changelog_url}/{v.replace('.', '-')}"
            else:
                full_url = changelog_url
            link_text = "Alle Details auf der Webseite" if language == 'de' else "Full details on the website"
            embed.add_field(name="📖", value=f"[{link_text}]({full_url})", inline=False)

        # Footer: Version + Stats
        git_stats = ai_data.get('stats', {})
        footer_parts = []
        v = ai_data.get('version')
        if v and v != 'patch':
            footer_parts.append(f"v{v}")
        footer_parts.append(f"{len(commits)} Commit(s)")
        files = git_stats.get('files_changed', 0)
        if files > 0:
            footer_parts.append(f"{files} Dateien")
        added = git_stats.get('lines_added', 0)
        removed = git_stats.get('lines_removed', 0)
        if added > 0:
            footer_parts.append(f"+{added}/-{removed}")

        embed.set_footer(text=" · ".join(footer_parts))

        return embed

    def _build_v3_customer_embed(self, repo_name: str, project_color: int,
                                  commits: list, language: str,
                                  ai_data: Dict, project_config: Dict) -> discord.Embed:
        """Patch Notes v3: Detailliertes Embed mit allen Kategorien und Beschreibungen."""
        patch_config = project_config.get('patch_notes', {})
        changelog_url = patch_config.get('changelog_url', '')
        version = ai_data.get('version') or self._extract_version_from_commits(commits)

        # Version "0.0.0" oder "patch" nicht anzeigen
        if version and version in ('0.0.0', 'patch', '0.0.1'):
            version = None

        # Titel: Version + AI-Titel
        title = ai_data.get('title', 'Update')
        # Doppelte Version im Titel vermeiden (z.B. "v1.0.0 — GuildScout 1.0.0: ...")
        if version:
            # Entferne Version aus dem AI-Titel falls doppelt
            import re as _re
            title = _re.sub(
                rf'(?:GuildScout|ZERODOX|ShadowOps)?\s*v?{_re.escape(version)}[:\s—-]*',
                '', title, flags=_re.IGNORECASE
            ).strip(' :—-')
            if not title:
                title = 'Update'

        version_str = f"v{version} — " if version else ''

        changelog_link = ''
        if changelog_url and version:
            changelog_link = f"{changelog_url}/{version.replace('.', '-')}"

        embed = discord.Embed(
            title=f"\U0001f680 {version_str}{title}",
            url=changelog_link or None,
            color=project_color,
            timestamp=datetime.now(timezone.utc),
        )

        # Author-Feld: Projekt-Name
        embed.set_author(name=repo_name.upper())

        # TL;DR als Beschreibung
        tldr = ai_data.get('tldr', '')
        if tldr:
            embed.description = f"> {tldr}"

        # === Kategorisierte Changes mit Beschreibungen ===
        changes = ai_data.get('changes', [])
        features = [c for c in changes if c.get('type') == 'feature']
        fixes = [c for c in changes if c.get('type') == 'fix']
        improvements = [c for c in changes if c.get('type') == 'improvement']
        breaking = ai_data.get('breaking_changes', [])

        is_major = len(commits) >= 15 or (version and version.endswith('.0.0'))

        # Features mit Details (mehr bei Major Releases)
        if features:
            max_features = 6 if is_major else 4
            feature_lines = []
            for f in features[:max_features]:
                desc = f.get('description', '')
                details = f.get('details', [])
                feature_lines.append(f"\u2022 **{desc}**")
                # Sub-Details bei Major Releases
                if is_major and details:
                    for detail in details[:2]:
                        feature_lines.append(f"  \u2514 {detail}")
            if len(features) > max_features:
                feature_lines.append(f"  *+{len(features) - max_features} weitere*")
            text = "\n".join(feature_lines)
            if len(text) > 1024:
                text = text[:1020] + "..."
            embed.add_field(
                name="\U0001f195 Neue Features",
                value=text,
                inline=False,
            )

        # Breaking Changes
        if breaking:
            breaking_lines = [f"\u26a0\ufe0f {b}" for b in breaking[:3]]
            embed.add_field(
                name="\u26a0\ufe0f Breaking Changes",
                value="\n".join(breaking_lines),
                inline=False,
            )

        # Bugfixes MIT Beschreibungen (nicht nur Zähler)
        if fixes:
            if len(fixes) <= 4:
                fix_lines = [f"\u2022 {f.get('description', '')}" for f in fixes]
                text = "\n".join(fix_lines)
                if len(text) > 1024:
                    text = text[:1020] + "..."
                embed.add_field(
                    name=f"\U0001f41b {len(fixes)} Bugfix{'es' if len(fixes) != 1 else ''}",
                    value=text,
                    inline=False,
                )
            else:
                # Viele Fixes: Top 3 zeigen + Zähler
                fix_lines = [f"\u2022 {f.get('description', '')}" for f in fixes[:3]]
                if len(fixes) > 3:
                    fix_lines.append(f"  *+{len(fixes) - 3} weitere Fixes*")
                text = "\n".join(fix_lines)
                if len(text) > 1024:
                    text = text[:1020] + "..."
                embed.add_field(
                    name=f"\U0001f41b {len(fixes)} Bugfixes",
                    value=text,
                    inline=False,
                )

        # Verbesserungen MIT Beschreibungen
        if improvements:
            if len(improvements) <= 3:
                imp_lines = [f"\u2022 {i.get('description', '')}" for i in improvements]
                text = "\n".join(imp_lines)
                if len(text) > 1024:
                    text = text[:1020] + "..."
                embed.add_field(
                    name=f"\u26a1 {len(improvements)} Verbesserung{'en' if len(improvements) != 1 else ''}",
                    value=text,
                    inline=False,
                )
            else:
                imp_lines = [f"\u2022 {i.get('description', '')}" for i in improvements[:3]]
                if len(improvements) > 3:
                    imp_lines.append(f"  *+{len(improvements) - 3} weitere*")
                text = "\n".join(imp_lines)
                if len(text) > 1024:
                    text = text[:1020] + "..."
                embed.add_field(
                    name=f"\u26a1 {len(improvements)} Verbesserungen",
                    value=text,
                    inline=False,
                )

        # Fallback: discord_highlights wenn keine strukturierten changes
        if not changes and not breaking:
            highlights = ai_data.get('discord_highlights', [])
            if highlights:
                highlights_text = "\n".join(f"\u2022 {h}" for h in highlights[:5])
                embed.add_field(name="\U0001f525 Highlights", value=highlights_text, inline=False)

        # === Changelog-Link (kompakt) ===
        if changelog_link:
            link_text = "\U0001f4d6 [Alle Details im Changelog]" if language == 'de' else "\U0001f4d6 [Full changelog]"
            embed.add_field(
                name="\u200b",
                value=f"{link_text}({changelog_link})",
                inline=False,
            )

        # === Footer mit Stats (inkl. Coverage wenn vorhanden) ===
        git_stats = ai_data.get('stats', {})
        footer_parts = []
        if version:
            footer_parts.append(f"v{version}")
        footer_parts.append(f"{len(commits)} Commits")
        files = git_stats.get('files_changed', 0)
        if files > 0:
            footer_parts.append(f"{files} Dateien")
        added = git_stats.get('lines_added', 0)
        removed = git_stats.get('lines_removed', 0)
        if added > 0:
            footer_parts.append(f"+{added}/-{removed}")

        # Coverage + Tests (nur wenn gute Zahlen)
        tests_total = git_stats.get('tests_total')
        tests_passed = git_stats.get('tests_passed')
        coverage = git_stats.get('coverage_percent')
        if tests_total and tests_total > 0 and tests_passed == tests_total:
            footer_parts.append(f"\u2705 {tests_total} Tests")
        if coverage is not None and coverage >= 50:
            footer_parts.append(f"{coverage:.0f}% Coverage")

        embed.set_footer(text=" \u00b7 ".join(footer_parts))

        return embed

    async def _export_structured_web_changelog(self, repo_name: str, commits: list,
                                                 ai_data: Dict, project_config: Dict,
                                                 language: str) -> None:
        """Exportiere strukturierte Patch Notes als Web-Changelog + API POST."""
        version = ai_data.get('version') or self._extract_version_from_commits(commits)
        if not version or version == 'patch':
            return

        exporter = getattr(self, 'web_exporter', None)
        if not exporter:
            return

        try:
            if hasattr(exporter, 'export_and_store'):
                # v3: Zentrale DB + File-Backup + API POST in einem Schritt
                await exporter.export_and_store(
                    project=repo_name,
                    version=version,
                    title=ai_data.get('title', f'{repo_name} {version}'),
                    tldr=ai_data.get('tldr', ''),
                    content=ai_data.get('web_content', ai_data.get('summary', '')),
                    stats=ai_data.get('stats', {}),
                    language=language,
                    changes=ai_data.get('changes', []),
                    seo_keywords=ai_data.get('seo_keywords', []),
                    seo_description=ai_data.get('seo_description', ''),
                )
                self.logger.info(f"📝 Strukturierter Web-Changelog exportiert (v3): {repo_name} v{version}")
            else:
                # Fallback: Legacy export() + separater API POST
                result = exporter.export(
                    project=repo_name,
                    version=version,
                    title=ai_data.get('title', f'{repo_name} {version}'),
                    tldr=ai_data.get('tldr', ''),
                    content=ai_data.get('web_content', ai_data.get('summary', '')),
                    stats=ai_data.get('stats', {}),
                    language=language,
                    changes=ai_data.get('changes', []),
                )
                self.logger.info(f"📝 Strukturierter Web-Changelog exportiert: {repo_name} v{version}")

                # API POST (async, vom Exporter entkoppelt)
                json_data = result.get('json_data') if result else None
                if json_data:
                    try:
                        await exporter.post_to_api(repo_name, json_data)
                    except Exception as e:
                        self.logger.debug(f"API-POST übersprungen: {e}")

        except Exception as e:
            self.logger.warning(f"⚠️ Strukturierter Web-Export fehlgeschlagen: {e}")

    def _categorize_commits_text(self, commits: list, language: str) -> str:
        """Kategorisiere Commits als Fallback-Text."""
        if language == 'en':
            feature_header = "**🆕 New Features:**"
            bugfix_header = "**🐛 Bug Fixes:**"
            improvement_header = "**⚡ Improvements:**"
            other_header = "**📝 Other Changes:**"
            default_desc = "Various updates and improvements"
        else:
            feature_header = "**🆕 Neue Features:**"
            bugfix_header = "**🐛 Bugfixes:**"
            improvement_header = "**⚡ Verbesserungen:**"
            other_header = "**📝 Weitere Änderungen:**"
            default_desc = "Diverse Updates und Verbesserungen"

        features = []
        fixes = []
        improvements = []
        other = []

        for commit in commits:
            message = commit['message'].split('\n')[0]
            message_lower = message.lower()

            if message_lower.startswith('feat') or 'feature' in message_lower or 'add' in message_lower:
                features.append(self._format_user_friendly_commit(message))
            elif message_lower.startswith('fix') or 'bug' in message_lower or 'issue' in message_lower:
                fixes.append(self._format_user_friendly_commit(message))
            elif message_lower.startswith('improve') or 'optimize' in message_lower or 'enhance' in message_lower or 'update' in message_lower:
                improvements.append(self._format_user_friendly_commit(message))
            else:
                other.append(self._format_user_friendly_commit(message))

        description_parts = []

        if features:
            description_parts.append(feature_header + "\n" + "\n".join(f"• {f}" for f in features))
        if fixes:
            description_parts.append(bugfix_header + "\n" + "\n".join(f"• {f}" for f in fixes))
        if improvements:
            description_parts.append(improvement_header + "\n" + "\n".join(f"• {i}" for i in improvements))
        if other:
            description_parts.append(other_header + "\n" + "\n".join(f"• {o}" for o in other))

        return "\n\n".join(description_parts) if description_parts else default_desc

    def _extract_version_from_commits(self, commits: list) -> Optional[str]:
        """Extrahiere Version aus Commit-Messages (schließt IP-Adressen aus)."""
        for commit in commits:
            msg = commit.get('message', '')
            # Negative Lookahead: Kein 4. Oktett (→ IP-Adresse ausschließen)
            match = re.search(
                r'v?(?:ersion|elease)?\s*(?<![0-9.])([0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,4})(?!\.[0-9])',
                msg, re.IGNORECASE
            )
            if match:
                return match.group(1)
        return None

    async def _export_web_changelog(self, repo_name: str, commits: list,
                                     ai_description: Optional[str],
                                     project_config: Dict, language: str,
                                     git_stats: Optional[Dict] = None) -> None:
        """Exportiere Patch Notes als Web-Changelog (SEO-optimiert) + API POST."""
        version = self._extract_version_from_commits(commits)
        if not version:
            return

        exporter = getattr(self, 'web_exporter', None)
        if not exporter:
            return

        git_stats = git_stats or {}

        # TL;DR aus AI-Description extrahieren
        tldr = ""
        content = ai_description or ""
        if content:
            tldr_match = re.search(r'\*\*TL;DR:\*\*\s*(.+?)(?:\n|$)', content)
            if tldr_match:
                tldr = tldr_match.group(1).strip()
            else:
                first_line = content.split('\n')[0].strip()
                if first_line and not first_line.startswith('**'):
                    tldr = first_line
                else:
                    tldr = f"{repo_name} {version} Update"

        title = f"{repo_name} {version}"

        try:
            if hasattr(exporter, 'export_and_store'):
                # v3: Zentrale DB + File-Backup + API POST in einem Schritt
                await exporter.export_and_store(
                    project=repo_name,
                    version=version,
                    title=title,
                    tldr=tldr,
                    content=content,
                    stats=git_stats,
                    language=language,
                )
                self.logger.info(f"📝 Web-Changelog exportiert (v3): {repo_name} v{version}")
            else:
                # Fallback: Legacy export() + separater API POST
                result = exporter.export(
                    project=repo_name,
                    version=version,
                    title=title,
                    tldr=tldr,
                    content=content,
                    stats=git_stats,
                    language=language,
                )
                self.logger.info(f"📝 Web-Changelog exportiert: {repo_name} v{version}")

                # API POST (async, vom Exporter entkoppelt)
                json_data = result.get('json_data') if result else None
                if json_data:
                    try:
                        await exporter.post_to_api(repo_name, json_data)
                    except Exception as e:
                        self.logger.debug(f"API-POST übersprungen: {e}")

        except Exception as e:
            self.logger.warning(f"⚠️ Web-Export fehlgeschlagen: {e}")

    async def _send_ai_preview_to_internal(self, customer_embed: discord.Embed,
                                               repo_name: str) -> None:
        """Sende AI-Patch-Notes-Preview an den internen Channel."""
        internal_channel = self.bot.get_channel(self.deployment_channel_id)
        if not internal_channel:
            return

        # Nur senden wenn es tatsächlich AI-Content gibt
        if not customer_embed or not customer_embed.description:
            return

        try:
            # Preview-Embed erstellen (Kopie des Customer-Embeds mit Hinweis)
            preview = discord.Embed(
                title=f"\U0001f4e2 Veröffentlicht: {customer_embed.title or repo_name}",
                url=customer_embed.url,
                description=customer_embed.description,
                color=customer_embed.color,
                timestamp=customer_embed.timestamp,
            )
            if customer_embed.author:
                preview.set_author(name=customer_embed.author.name)

            # Alle Fields übernehmen (max 5 für Kürze)
            for field in customer_embed.fields[:5]:
                preview.add_field(name=field.name, value=field.value, inline=field.inline)

            footer_text = "\u2191 Im Update-Channel gepostet"
            if customer_embed.footer and customer_embed.footer.text:
                footer_text = f"{footer_text} \u00b7 {customer_embed.footer.text}"
            preview.set_footer(text=footer_text)

            await internal_channel.send(embed=preview)
            self.logger.info(f"\U0001f4e2 AI-Preview für {repo_name} im internen Channel gesendet")
        except Exception as e:
            self.logger.warning(f"\u26a0\ufe0f AI-Preview Fehler: {e}")

    async def _send_to_internal_channel(self, embed: discord.Embed, repo_name: str) -> None:
        """Sende technische Notification an internen Channel."""
        internal_channel = self.bot.get_channel(self.deployment_channel_id)
        if not internal_channel:
            return

        try:
            description_chunks = self._split_embed_description(embed.description or "")

            if len(description_chunks) <= 1:
                await internal_channel.send(embed=embed)
            else:
                for i, chunk in enumerate(description_chunks):
                    embed_copy = discord.Embed(
                        title=f"{embed.title} (Teil {i+1}/{len(description_chunks)})" if i > 0 else embed.title,
                        url=embed.url,
                        color=embed.color,
                        description=chunk,
                        timestamp=embed.timestamp
                    )
                    if i == 0:
                        embed_copy.set_author(name=embed.author.name)
                        for field in embed.fields:
                            embed_copy.add_field(name=field.name, value=field.value, inline=field.inline)
                    await internal_channel.send(embed=embed_copy)

            self.logger.info(f"📢 Technische Patch Notes für {repo_name} im internen Channel gesendet.")
        except Exception as e:
            self.logger.error(f"❌ Fehler beim Senden der Push-Benachrichtigung: {e}")

    async def _send_to_customer_channels(self, embed: discord.Embed, repo_name: str,
                                          project_config: Dict, version: Optional[str]) -> None:
        """Sende Patch Notes an Customer-Channel mit Feedback-Buttons."""
        customer_channel_id = project_config.get('update_channel_id')
        if not customer_channel_id:
            return

        customer_channel = self.bot.get_channel(customer_channel_id)
        if not customer_channel:
            self.logger.warning(f"⚠️ Kunden-Update Channel {customer_channel_id} für {repo_name} nicht gefunden.")
            return

        # Feedback-View erstellen
        view = None
        if self.feedback_collector and version:
            changelog_url = project_config.get('patch_notes', {}).get('changelog_url', '')
            full_url = ''
            if changelog_url and version:
                full_url = f"{changelog_url}/{version.replace('.', '-')}"
            view = self.feedback_collector.create_view(full_url)

        try:
            description_chunks = self._split_embed_description(embed.description or "")
            sent_message = None

            if len(description_chunks) <= 1:
                sent_message = await customer_channel.send(embed=embed, view=view)
            else:
                for i, chunk in enumerate(description_chunks):
                    embed_copy = discord.Embed(
                        title=f"{embed.title} (Teil {i+1}/{len(description_chunks)})" if i > 0 else embed.title,
                        url=embed.url,
                        color=embed.color,
                        description=chunk,
                        timestamp=embed.timestamp
                    )
                    if i == len(description_chunks) - 1 and embed.footer:
                        embed_copy.set_footer(text=embed.footer.text)
                    # View nur an die erste Nachricht anhängen
                    msg_view = view if i == 0 else None
                    message = await customer_channel.send(embed=embed_copy, view=msg_view)
                    if i == 0:
                        sent_message = message

            self.logger.info(f"📢 Patch Notes für {repo_name} im Kunden-Channel gesendet.")

            # Tracking aktivieren (ohne Reactions/separate Nachricht)
            if sent_message and self.feedback_collector and version:
                try:
                    await self.feedback_collector.track_patch_notes_message(
                        message=sent_message,
                        project=repo_name,
                        version=version,
                    )
                    self.logger.info(f"👍 Feedback tracking aktiviert für {repo_name} v{version}")
                except Exception as e:
                    self.logger.warning(f"⚠️ Feedback tracking fehlgeschlagen: {e}")

        except Exception as e:
            self.logger.error(f"❌ Fehler beim Senden im Kunden-Channel: {e}")

    async def _send_internal_only(self, repo_name: str, repo_url: str, branch: str,
                                   pusher: str, commits: list, color: int) -> None:
        """Sende nur interne Notification (wenn Commits gebatcht werden)."""
        internal_channel = self.bot.get_channel(self.deployment_channel_id)
        if not internal_channel:
            return

        commits_url = f"{repo_url}/commits/{branch}" if repo_url else None

        embed = discord.Embed(
            title=f"📦 Gesammelt: {repo_name}",
            url=commits_url,
            color=color,
            timestamp=datetime.now(timezone.utc),
            description=(
                f"**{len(commits)}** Commit(s) von **{pusher}** gesammelt.\n"
                f"Wird mit dem nächsten Release veröffentlicht."
            )
        )
        embed.add_field(name="Branch", value=branch, inline=True)

        # Zeige Batch-Status
        if hasattr(self, 'patch_notes_batcher') and self.patch_notes_batcher:
            summary = self.patch_notes_batcher.get_pending_summary()
            if repo_name in summary:
                info = summary[repo_name]
                embed.add_field(
                    name="📊 Batch",
                    value=f"{info['count']} ausstehend (Release bei {self.patch_notes_batcher.batch_threshold})",
                    inline=True
                )

        try:
            await internal_channel.send(embed=embed)
            self.logger.info(f"📦 Batch-Notification für {repo_name} gesendet")
        except Exception as e:
            self.logger.error(f"❌ Fehler bei Batch-Notification: {e}")

    async def _send_pr_notification(
        self, action: str, repo: str, pr_number: int, title: str,
        author: str, source: str, target: str, url: str
    ):
        """Send Discord notification for PR event"""
        channel_id = self.code_fixes_channel_id or self.deployment_channel_id
        channel = self.bot.get_channel(channel_id)
        if not channel:
            return

        action_emojis = {
            'opened': '🔓',
            'closed': '🔒',
            'reopened': '🔄',
            'synchronize': '🔃',
            'merged': '🎉'
        }

        emoji = action_emojis.get(action, '🔀')
        color = discord.Color.green() if action in ['opened', 'merged'] else discord.Color.orange()

        embed = discord.Embed(
            title=f"{emoji} Pull Request #{pr_number} {action}",
            description=f"**{title}**",
            url=url,
            color=color,
            timestamp=datetime.now(timezone.utc)
        )

        embed.add_field(name="Repository", value=repo, inline=True)
        embed.add_field(name="Author", value=author, inline=True)
        embed.add_field(name="Branch", value=f"`{source}` → `{target}`", inline=False)

        await channel.send(embed=embed)

    async def _send_release_notification(
        self, action: str, repo: str, tag: str, name: str,
        author: str, is_prerelease: bool, url: str
    ):
        """Send Discord notification for release event"""
        channel = self.bot.get_channel(self.deployment_channel_id)
        if not channel:
            return

        emoji = '🏷️' if is_prerelease else '🎉'
        release_type = 'Pre-release' if is_prerelease else 'Release'

        embed = discord.Embed(
            title=f"{emoji} {release_type} {action}: {name}",
            description=f"**{repo}** `{tag}`",
            url=url,
            color=discord.Color.purple() if is_prerelease else discord.Color.gold(),
            timestamp=datetime.now(timezone.utc)
        )

        embed.add_field(name="Tag", value=f"`{tag}`", inline=True)
        embed.add_field(name="Author", value=author, inline=True)
        embed.add_field(name="Type", value=release_type, inline=True)

        await channel.send(embed=embed)

    async def _send_deployment_success(
        self, repo: str, branch: str, sha: str, result: Dict
    ):
        """Send Discord notification for successful deployment"""
        channel = self.bot.get_channel(self.deployment_channel_id)
        if not channel:
            return

        duration = result.get('duration_seconds', 0)

        embed = discord.Embed(
            title="✅ Deployment Successful",
            description=f"**{repo}** deployed successfully",
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc)
        )

        embed.add_field(name="Repository", value=repo, inline=True)
        embed.add_field(name="Branch", value=f"`{branch}`", inline=True)
        embed.add_field(name="Commit", value=f"`{sha}`", inline=True)
        embed.add_field(name="Duration", value=f"{duration:.1f}s", inline=True)

        if result.get('tests_passed'):
            embed.add_field(name="Tests", value="✅ Passed", inline=True)

        await channel.send(embed=embed)

    async def _send_deployment_failure(
        self, repo: str, branch: str, sha: str, result: Dict
    ):
        """Send Discord notification for failed deployment"""
        channel = self.bot.get_channel(self.deployment_channel_id)
        if not channel:
            return

        error = result.get('error', 'Unknown error')
        rollback = result.get('rolled_back', False)

        embed = discord.Embed(
            title="❌ Deployment Failed",
            description=f"**{repo}** deployment failed",
            color=discord.Color.red(),
            timestamp=datetime.now(timezone.utc)
        )

        embed.add_field(name="Repository", value=repo, inline=True)
        embed.add_field(name="Branch", value=f"`{branch}`", inline=True)
        embed.add_field(name="Commit", value=f"`{sha}`", inline=True)

        if len(error) > 500:
            error = error[:497] + "..."
        embed.add_field(name="Error", value=f"```{error}```", inline=False)

        if rollback:
            embed.add_field(name="Rollback", value="✅ Auto-rollback successful", inline=False)

        await channel.send(embed=embed)

    async def _send_deployment_error(
        self, repo: str, branch: str, sha: str, error: str
    ):
        """Send Discord notification for deployment exception"""
        channel = self.bot.get_channel(self.deployment_channel_id)
        if not channel:
            return

        embed = discord.Embed(
            title="💥 Deployment Exception",
            description=f"**{repo}** deployment crashed",
            color=discord.Color.dark_red(),
            timestamp=datetime.now(timezone.utc)
        )

        embed.add_field(name="Repository", value=repo, inline=True)
        embed.add_field(name="Branch", value=f"`{branch}`", inline=True)
        embed.add_field(name="Commit", value=f"`{sha}`", inline=True)

        if len(error) > 500:
            error = error[:497] + "..."
        embed.add_field(name="Exception", value=f"```{error}```", inline=False)

        await channel.send(embed=embed)

    async def _send_external_git_notifications(self, repo_name: str, embed: discord.Embed,
                                                project_config: Dict, version: str = None):
        """
        Send Git push notifications to external servers (customer guilds)
        AND activate feedback collection with integrated buttons.
        """
        external_notifs = project_config.get('external_notifications', [])
        if not external_notifs:
            return

        for notif_config in external_notifs:
            if not notif_config.get('enabled', False):
                continue

            notify_on = notif_config.get('notify_on', {})
            if not notify_on.get('git_push', True):
                continue

            channel_id = notif_config.get('channel_id')
            if not channel_id:
                continue

            try:
                channel = self.bot.get_channel(int(channel_id))
                if not channel:
                    self.logger.warning(f"⚠️ External channel {channel_id} not found for {repo_name}")
                    continue

                # Feedback-View erstellen
                view = None
                if self.feedback_collector and version:
                    changelog_url = project_config.get('patch_notes', {}).get('changelog_url', '')
                    full_url = ''
                    if changelog_url and version:
                        full_url = f"{changelog_url}/{version.replace('.', '-')}"
                    view = self.feedback_collector.create_view(full_url)

                description_chunks = self._split_embed_description(embed.description or "")
                sent_message = None

                if len(description_chunks) <= 1:
                    sent_message = await channel.send(embed=embed, view=view)
                else:
                    for i, chunk in enumerate(description_chunks):
                        embed_copy = discord.Embed(
                            title=f"{embed.title} (Teil {i+1}/{len(description_chunks)})" if i > 0 else embed.title,
                            url=embed.url,
                            color=embed.color,
                            description=chunk,
                            timestamp=embed.timestamp
                        )
                        if i == len(description_chunks) - 1 and embed.footer:
                            embed_copy.set_footer(text=embed.footer.text)
                        # View nur an die erste Nachricht anhängen
                        msg_view = view if i == 0 else None
                        message = await channel.send(embed=embed_copy, view=msg_view)
                        if i == 0:
                            sent_message = message

                self.logger.info(f"📤 Sent git update for {repo_name} to external server")

                # Tracking aktivieren (ohne Reactions/separate Nachricht)
                if sent_message and self.feedback_collector and version:
                    try:
                        await self.feedback_collector.track_patch_notes_message(
                            message=sent_message,
                            project=repo_name,
                            version=version,
                        )
                        self.logger.info(f"👍 Feedback tracking activated for {repo_name} v{version}")
                    except Exception as e:
                        self.logger.warning(f"⚠️ Could not activate feedback tracking: {e}")

            except Exception as e:
                self.logger.error(f"❌ Failed to send external git notification for {repo_name}: {e}")
