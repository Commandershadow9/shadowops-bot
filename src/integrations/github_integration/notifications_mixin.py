"""
Discord notification methods for GitHubIntegration.

v5: Unified Pipeline — ein Embed-Builder, ein Web-Export, ein Version-Resolver
"""

import logging
import re
from datetime import datetime, timezone
from typing import Dict, Optional

import discord

from integrations.content_sanitizer import ContentSanitizer

logger = logging.getLogger('shadowops')


class NotificationsMixin:

    async def _send_push_notification(
        self, repo_name: str, repo_url: str, branch: str, pusher: str, commits: list,
        skip_batcher: bool = False
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

        # === GLOBALER SAFETY-CHECK: Mindestens N Commits für Patch Notes ===
        # WICHTIG: Dieser Check gilt nur fuer direkte Releases (skip_batcher=True).
        # Bei normalem Flow muessen auch Einzel-Commits zum Batcher durchkommen,
        # damit sie gesammelt werden koennen (Threshold wird im Batcher geprueft).
        min_commits_global = patch_config.get('min_commits', 2)
        if skip_batcher and len(commits) < min_commits_global:
            self.logger.warning(
                f"⛔ Patch Notes für {repo_name} blockiert: "
                f"nur {len(commits)} Commit(s), Minimum ist {min_commits_global}. "
                f"(skip_batcher={skip_batcher})"
            )
            return

        # === BATCHING CHECK (skip bei manuellen/Cron-Releases) ===
        if not skip_batcher:
            # Batcher holen: zuerst eigenes Attribut, dann Fallback vom Bot
            batcher = getattr(self, 'patch_notes_batcher', None)
            if not batcher:
                batcher = getattr(self.bot, 'patch_notes_batcher', None)
                if batcher:
                    self.patch_notes_batcher = batcher
                    self.logger.info("🔧 PatchNotesBatcher vom Bot wiederhergestellt")

            if batcher:
                if batcher.should_batch(commits, repo_name):
                    result = batcher.add_commits(repo_name, commits)

                    if result['ready']:
                        # Batch-Threshold erreicht — alle gesammelten Commits freigeben
                        all_commits = batcher.release_batch(repo_name)
                        if all_commits:
                            self.logger.info(f"🚀 Batch-Release: {len(all_commits)} Commits")
                            commits = all_commits
                        # Weiter mit normaler Verarbeitung
                    else:
                        # Noch nicht genug — nur loggen, KEIN Discord-Spam
                        self.logger.info(
                            f"📦 {result['total_pending']}/{batcher.batch_threshold} "
                            f"Commits für {repo_name} gesammelt (kein Release)"
                        )
                        return
            else:
                # Safety-Net: Batcher nicht verfügbar → fail-closed (KEIN ungepufferter Release)
                self.logger.warning(
                    f"⚠️ PatchNotesBatcher nicht verfügbar! "
                    f"{len(commits)} Commit(s) für {repo_name} übersprungen — "
                    f"kein ungepufferter Release erlaubt."
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
                # Verschachtelte Felder: changes[].description, changes[].details[] und breaking_changes[]
                if 'changes' in ai_result:
                    for change in ai_result['changes']:
                        if isinstance(change, dict):
                            if 'description' in change:
                                change['description'] = sanitizer.sanitize(change['description'])
                            if 'details' in change and isinstance(change['details'], list):
                                change['details'] = [
                                    sanitizer.sanitize(d) if isinstance(d, str) else d
                                    for d in change['details']
                                ]
                if 'breaking_changes' in ai_result:
                    ai_result['breaking_changes'] = [
                        sanitizer.sanitize(b) if isinstance(b, str) else b
                        for b in ai_result['breaking_changes']
                    ]
            elif isinstance(ai_result, str):
                ai_result = sanitizer.sanitize(ai_result)

        # === HALLUZINATIONS-VALIDIERUNG ===
        validation = {'valid': True, 'warnings': [], 'fixes_applied': []}
        if isinstance(ai_result, dict):
            validation = self._validate_ai_output(ai_result, commits)
            if validation['warnings']:
                for w in validation['warnings']:
                    self.logger.warning(f"⚠️ Patch Notes Validierung ({repo_name}): {w}")
            if validation['fixes_applied']:
                for f in validation['fixes_applied']:
                    self.logger.info(f"🔧 Patch Notes Auto-Fix ({repo_name}): {f}")

        # === PIPELINE-METRIKEN ===
        version = self._resolve_version(ai_result, commits, repo_name)
        metrics = {
            'project': repo_name,
            'version': version,
            'total_commits': len(commits),
            'classified': {
                tag: 0 for tag in ['FEATURE', 'BUGFIX', 'DOCS', 'DESIGN-DOC',
                                   'SEO-AUTO', 'DEPS-AUTO', 'MERGE', 'REVERT', 'OTHER']
            },
            'pr_labels_found': sum(1 for c in commits if c.get('pr_label_tag')),
            'pr_bodies_found': sum(1 for c in commits if c.get('pr_body')),
            'hallucinations_caught': len(validation.get('fixes_applied', [])),
            'warnings': len(validation.get('warnings', [])),
            'version_source': 'explicit' if self._extract_version_from_commits(commits) else
                              ('semver' if self._calculate_semver(commits, repo_name) else 'fallback'),
        }
        for commit in commits:
            tag, _ = self._classify_commit(commit)
            if tag in metrics['classified']:
                metrics['classified'][tag] += 1
            else:
                metrics['classified']['OTHER'] += 1

        # Kompakte Metriken-Zeile
        clf = metrics['classified']
        self.logger.info(
            f"📊 Patch Notes Pipeline ({repo_name} v{version}): "
            f"{metrics['total_commits']} Commits "
            f"({clf['FEATURE']}F {clf['BUGFIX']}B {clf['DOCS']}D "
            f"{clf['SEO-AUTO']+clf['DEPS-AUTO']}Auto {clf['MERGE']}M) | "
            f"PR-Labels: {metrics['pr_labels_found']}, PR-Bodies: {metrics['pr_bodies_found']} | "
            f"Halluzinationen: {metrics['hallucinations_caught']} gefangen | "
            f"Version: {metrics['version_source']}"
        )

        # === UNIFIED EMBED + WEB EXPORT ===
        customer_embed = self._build_unified_embed(
            repo_name, project_color, commits, language,
            ai_result, project_config, git_stats
        )
        await self._unified_web_export(
            repo_name, commits, ai_result, project_config, language, git_stats, version
        )

        # 1. Internal
        await self._send_to_internal_channel(internal_embed, repo_name)
        await self._send_ai_preview_to_internal(customer_embed, repo_name)

        # 2. Customer + Feedback (version ist IMMER gesetzt)
        await self._send_to_customer_channels(customer_embed, repo_name, project_config, version)

        # 3. External
        await self._send_external_git_notifications(repo_name, customer_embed, project_config, version)

    # ── Unified Embed + Version + Web-Export (v5) ──────────────────────

    def _resolve_version(self, ai_result, commits: list, repo_name: str = "") -> str:
        """Bestimme Version: Commits > SemVer-Berechnung > AI > Auto-Version. NIE None."""
        # 1. Aus Commits (expliziter Version-Tag, z.B. "feat: Release v2.1.0")
        v = self._extract_version_from_commits(commits)
        if v:
            return v

        # 2. Semantic Versioning: Letzte Version + Commit-Typen → naechste Version
        sem_v = self._calculate_semver(commits, repo_name)
        if sem_v:
            return sem_v

        # 3. Aus AI-Ergebnis (nur echte Versionen, NICHT von AI erfundene Major-Bumps)
        if isinstance(ai_result, dict):
            ai_v = ai_result.get('version')
            if ai_v and ai_v != 'patch' and not ai_v.startswith('0.0.'):
                return ai_v

        # 4. Auto-Version (Fallback, IMMER)
        return f"patch.{datetime.now(timezone.utc).strftime('%Y.%m.%d')}"

    def _calculate_semver(self, commits: list, repo_name: str) -> Optional[str]:
        """
        Berechne naechste Semantic Version basierend auf Commit-Typen.

        Logik:
        - Mindestens 1 Breaking Change (feat!:, fix!:) → MAJOR Bump
        - Mindestens 1 [FEATURE] Commit → MINOR Bump
        - Nur [BUGFIX], [IMPROVEMENT], etc. → PATCH Bump
        """
        if not repo_name:
            return None

        # Letzte Version aus Changelog-DB laden
        last_version = self._get_last_version_from_db(repo_name)
        if not last_version:
            return None

        # Commit-Typen zaehlen (nutzt _classify_commit aus AIPatchNotesMixin)
        has_breaking = False
        has_feature = False

        for commit in commits:
            msg = commit.get('message', '')
            title = msg.split('\n')[0]

            # Breaking Change: "feat!:" oder "fix!:" oder "BREAKING CHANGE:" im Body
            if re.match(r'^\w+(?:\([^)]*\))?!:', title):
                has_breaking = True
            elif 'BREAKING CHANGE:' in msg or 'BREAKING-CHANGE:' in msg:
                has_breaking = True
            # Feature: "feat:" oder "feat(scope):"
            elif re.match(r'^feat(?:\([^)]*\))?:', title):
                has_feature = True

        # Version parsen und bumpen
        try:
            parts = last_version.split('.')
            if len(parts) != 3:
                return None
            major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])

            if has_breaking:
                new_version = f"{major + 1}.0.0"
            elif has_feature:
                new_version = f"{major}.{minor + 1}.0"
            else:
                new_version = f"{major}.{minor}.{patch + 1}"

            # Kollisionsschutz: Wenn Version schon existiert, Patch hochzaehlen
            return self._ensure_unique_version(new_version, repo_name)
        except (ValueError, IndexError):
            return None

    def _ensure_unique_version(self, version: str, repo_name: str) -> str:
        """Stelle sicher, dass die Version noch nicht in der DB existiert."""
        try:
            import sqlite3
            from pathlib import Path
            db_path = Path(__file__).resolve().parent.parent.parent.parent / 'data' / 'changelogs.db'
            if not db_path.exists():
                return version

            with sqlite3.connect(str(db_path)) as conn:
                cursor = conn.cursor()
                # Alle existierenden Versionen fuer dieses Projekt laden
                cursor.execute(
                    "SELECT version FROM changelogs WHERE project = ?",
                    (repo_name,)
                )
                existing = {row[0] for row in cursor.fetchall()}

            if version not in existing:
                return version

            # Version existiert → Patch hochzaehlen bis frei
            parts = version.split('.')
            major, minor = int(parts[0]), int(parts[1])
            patch_num = int(parts[2])
            for _ in range(100):  # Max 100 Versuche
                patch_num += 1
                candidate = f"{major}.{minor}.{patch_num}"
                if candidate not in existing:
                    return candidate

            return version  # Sollte nie passieren
        except Exception:
            return version

    def _get_last_version_from_db(self, repo_name: str) -> Optional[str]:
        """Lade die letzte semantische Version aus der Changelog-DB."""
        try:
            import sqlite3
            from pathlib import Path
            db_path = Path(__file__).resolve().parent.parent.parent.parent / 'data' / 'changelogs.db'
            if not db_path.exists():
                return None

            with sqlite3.connect(str(db_path)) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT version FROM changelogs "
                    "WHERE project = ? AND version NOT LIKE 'patch.%' "
                    "ORDER BY created_at DESC LIMIT 1",
                    (repo_name,)
                )
                row = cursor.fetchone()

            if not row:
                return None

            # Nur echte SemVer-Versionen (X.Y.Z)
            version = row[0]
            if re.match(r'^\d+\.\d+\.\d+$', version):
                return version
            return None
        except Exception:
            return None

    def _resolve_title(self, ai_result, version: str) -> str:
        """Titel aus AI oder Fallback. Doppelte Version entfernen."""
        title = 'Update'
        if isinstance(ai_result, dict):
            title = ai_result.get('title', 'Update')
        elif isinstance(ai_result, str) and ai_result.strip():
            # Erste Zeile als Titel-Kandidat (nur wenn kurz genug)
            first = ai_result.strip().split('\n')[0].strip()
            if first and len(first) < 100 and not first.startswith('**'):
                title = first

        # Version aus Titel entfernen (Dopplung vermeiden)
        if version:
            title = re.sub(
                rf'(?:GuildScout|ZERODOX|ShadowOps)?\s*v?{re.escape(version)}[:\s\u2014-]*',
                '', title, flags=re.IGNORECASE
            ).strip(' :\u2014-')
        if not title:
            title = 'Update'
        return title

    def _build_description(self, ai_result, commits: list, language: str) -> str:
        """Baut Description aus AI-Ergebnis (dict/str/None)."""
        if isinstance(ai_result, dict):
            return self._description_from_structured(ai_result, commits, language)
        elif isinstance(ai_result, str) and ai_result.strip():
            return ai_result.strip()
        else:
            return self._categorize_commits_text(commits, language)

    def _description_from_structured(self, ai_data: dict, commits: list, language: str) -> str:
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
            parts.append("**\U0001f195 Neue Features**")
            for f in features[:max_show]:
                parts.append(f"\u2192 {f.get('description', '')}")
            if len(features) > max_show:
                parts.append(f"  *+{len(features) - max_show} weitere*")
            parts.append("")

        if breaking:
            parts.append("**\u26a0\ufe0f Breaking Changes**")
            for b in breaking[:3]:
                parts.append(f"\u26a0\ufe0f {b}")
            parts.append("")

        if fixes:
            parts.append("**\U0001f41b Bugfixes**")
            for f in fixes[:4]:
                parts.append(f"\u2192 {f.get('description', '')}")
            if len(fixes) > 4:
                parts.append(f"  *+{len(fixes) - 4} weitere*")
            parts.append("")

        if improvements:
            parts.append("**\u26a1 Verbesserungen**")
            for i in improvements[:3]:
                parts.append(f"\u2192 {i.get('description', '')}")
            if len(improvements) > 3:
                parts.append(f"  *+{len(improvements) - 3} weitere*")
            parts.append("")

        # Fallback wenn keine changes
        if not changes and not breaking:
            highlights = ai_data.get('discord_highlights', [])
            if highlights:
                parts.append("**\U0001f525 Highlights**")
                for h in highlights[:5]:
                    parts.append(f"\u2192 {h}")
                parts.append("")

        return "\n".join(parts)

    def _build_footer(self, version: str, commits: list, git_stats: Optional[dict] = None) -> str:
        """Footer mit Version, Stats, Coverage, Tests."""
        footer_parts = []
        if version:
            footer_parts.append(f"v{version}")
        footer_parts.append(f"{len(commits)} Commits")

        if git_stats:
            files = git_stats.get('files_changed', 0)
            if files > 0:
                footer_parts.append(f"{files} Dateien")
            added = git_stats.get('lines_added', 0)
            removed = git_stats.get('lines_removed', 0)
            if added > 0:
                footer_parts.append(f"+{added}/-{removed}")

            tests_total = git_stats.get('tests_total')
            tests_passed = git_stats.get('tests_passed')
            coverage = git_stats.get('coverage_percent')
            if tests_total and tests_total > 0 and tests_passed == tests_total:
                footer_parts.append(f"\u2705 {tests_total} Tests")
            if coverage is not None and coverage >= 50:
                footer_parts.append(f"{coverage:.0f}% Coverage")

        return " \u00b7 ".join(footer_parts)

    def _build_unified_embed(self, repo_name: str, project_color: int,
                              commits: list, language: str, ai_result,
                              project_config: Dict,
                              git_stats: Optional[Dict] = None) -> discord.Embed:
        """EIN Embed-Builder für alle Fälle (dict/str/None)."""
        version = self._resolve_version(ai_result, commits, repo_name)
        title = self._resolve_title(ai_result, version)
        changelog_url = project_config.get('patch_notes', {}).get('changelog_url', '')

        # Changelog-Link
        is_real_version = version and not version.startswith('patch.')
        if changelog_url:
            changelog_link = f"{changelog_url}/{version.replace('.', '-')}" if is_real_version else changelog_url
        else:
            changelog_link = ''

        # Embed erstellen
        version_str = f"v{version} \u2014 " if is_real_version else ''
        embed = discord.Embed(
            title=f"\U0001f680 {version_str}{title}",
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
            description += f"\n\n\U0001f4d6 [{link_text}]({changelog_link})"

        embed.description = description[:4096]

        # Footer
        embed.set_footer(text=self._build_footer(version, commits, git_stats))

        return embed

    def _extract_web_content(self, ai_result, repo_name: str, version: str):
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
            content = text
            changes = []
            seo_keywords = []
            return title, tldr, content, changes, seo_keywords

        else:
            return f"{repo_name} Update", '', '', [], []

    async def _unified_web_export(self, repo_name: str, commits: list, ai_result,
                                   project_config: Dict, language: str,
                                   git_stats: Optional[Dict], version: str) -> None:
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
            self.logger.info(f"\U0001f4dd Web-Export: {repo_name} v{version}")
        except Exception as e:
            self.logger.warning(f"\u26a0\ufe0f Web-Export fehlgeschlagen: {e}")

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

        # Interner Kunden-Channel (z.B. für verifizierte Kunden mit Rollen-Ping)
        await self._send_to_internal_customer_channel(embed, repo_name, project_config, version)

    async def _send_to_internal_customer_channel(self, embed: discord.Embed, repo_name: str,
                                                  project_config: Dict, version: Optional[str]) -> None:
        """Sende Patch Notes an internen Kunden-Channel mit Rollen-Ping."""
        internal_channel_id = project_config.get('internal_channel_id')
        if not internal_channel_id:
            return

        internal_channel = self.bot.get_channel(internal_channel_id)
        if not internal_channel:
            self.logger.warning(f"⚠️ Interner Kunden-Channel {internal_channel_id} für {repo_name} nicht gefunden.")
            return

        # Rollen-Mention vorbereiten
        role_id = project_config.get('internal_channel_role_mention')
        role_mention = f"<@&{role_id}>" if role_id else ""

        try:
            description_chunks = self._split_embed_description(embed.description or "")

            if len(description_chunks) <= 1:
                await internal_channel.send(
                    content=f"{role_mention} Neues Update verfügbar!" if role_mention else None,
                    embed=embed,
                    allowed_mentions=discord.AllowedMentions(roles=True),
                )
            else:
                for i, chunk in enumerate(description_chunks):
                    embed_copy = discord.Embed(
                        title=f"{embed.title} (Teil {i+1}/{len(description_chunks)})" if i > 0 else embed.title,
                        url=embed.url,
                        color=embed.color,
                        description=chunk,
                        timestamp=embed.timestamp,
                    )
                    if i == len(description_chunks) - 1 and embed.footer:
                        embed_copy.set_footer(text=embed.footer.text)
                    # Rollen-Ping nur bei der ersten Nachricht
                    content = f"{role_mention} Neues Update verfügbar!" if (i == 0 and role_mention) else None
                    await internal_channel.send(
                        content=content,
                        embed=embed_copy,
                        allowed_mentions=discord.AllowedMentions(roles=True),
                    )

            self.logger.info(f"📢 Patch Notes für {repo_name} im internen Kunden-Channel gesendet.")

        except Exception as e:
            self.logger.error(f"❌ Fehler beim Senden im internen Kunden-Channel: {e}")

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
