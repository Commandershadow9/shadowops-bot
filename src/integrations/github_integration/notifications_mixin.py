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

        # === UMLAUT-NORMALISIERUNG (AI gibt manchmal ae/oe/ue statt ä/ö/ü) ===
        if language == 'de' and ai_result:
            ai_result = self._normalize_german_umlauts(ai_result)

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
        version, version_source = self._resolve_version_with_source(ai_result, commits, repo_name)
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
            'version_source': version_source,
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
        """Bestimme Version: Git-Tags > Commit-Text > SemVer > AI > Fallback. NIE None."""
        version, _ = self._resolve_version_with_source(ai_result, commits, repo_name)
        return version

    def _resolve_version_with_source(self, ai_result, commits: list, repo_name: str = "") -> tuple:
        """Bestimme Version + Quelle. Kein doppelter subprocess-Aufruf fuer Metriken.

        Returns:
            (version: str, source: str) — source ist git_tag/explicit/semver/ai/fallback
        """
        # 1. Git-Tags auf Commits im Batch (zuverlaessigste Quelle)
        tag_v = self._get_version_from_commit_tags(commits, repo_name)
        if tag_v:
            return (self._ensure_unique_version(tag_v, repo_name), 'git_tag')

        # 2. Aus Commits (expliziter Version-Tag, z.B. "feat: Release v2.1.0")
        v = self._extract_version_from_commits(commits)
        if v:
            return (self._ensure_unique_version(v, repo_name), 'explicit')

        # 3. Semantic Versioning: Letzte Version + Commit-Typen → naechste Version
        sem_v = self._calculate_semver(commits, repo_name)
        if sem_v:
            return (sem_v, 'semver')  # _calculate_semver ruft bereits _ensure_unique_version auf

        # 4. Aus AI-Ergebnis (nur echte Versionen, NICHT von AI erfundene Major-Bumps)
        if isinstance(ai_result, dict):
            ai_v = ai_result.get('version')
            if ai_v and ai_v != 'patch' and not ai_v.startswith('0.0.'):
                return (self._ensure_unique_version(ai_v, repo_name), 'ai')

        # 5. Auto-Version (Fallback, IMMER)
        return (f"patch.{datetime.now(timezone.utc).strftime('%Y.%m.%d')}", 'fallback')

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
        """Lade die letzte semantische Version — Git-Tags zuerst, DB als Fallback."""
        # 1. Git-Tags als primäre Quelle (zuverlässigste Versionierung)
        git_version = self._get_last_version_from_git(repo_name)
        if git_version:
            return git_version

        # 2. Fallback: Changelog-DB
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

    def _get_last_version_from_git(self, repo_name: str) -> Optional[str]:
        """Lade die letzte Version aus Git-Tags (zuverlässigste Quelle)."""
        try:
            import subprocess
            # Projektpfad aus Config holen
            project_config = self.bot.config.projects.get(repo_name, {})
            project_path = project_config.get('path', '')
            if not project_path:
                return None

            # Neuesten Semver-Tag holen (z.B. v0.12.0)
            result = subprocess.run(
                ['git', 'tag', '-l', 'v*', '--sort=-v:refname'],
                capture_output=True, text=True, cwd=project_path, timeout=5
            )
            if result.returncode != 0 or not result.stdout.strip():
                return None

            # Ersten Tag nehmen (neueste Version)
            latest_tag = result.stdout.strip().splitlines()[0]
            # v0.12.0 → 0.12.0
            version = latest_tag.lstrip('v')
            if re.match(r'^\d+\.\d+\.\d+$', version):
                return version
            return None
        except Exception:
            return None

    def _get_version_from_commit_tags(self, commits: list, repo_name: str) -> Optional[str]:
        """Suche Git-Tags auf den Commits im aktuellen Batch.

        Wenn ein Commit exakt auf einem Tag liegt (z.B. v0.15.0),
        nutze diesen Tag als Version statt Semver zu berechnen.
        """
        try:
            import subprocess
            project_config = self.bot.config.projects.get(repo_name, {})
            project_path = project_config.get('path', '')
            if not project_path:
                return None

            commit_shas = set()
            for commit in commits:
                sha = commit.get('id', commit.get('sha', ''))
                if sha:
                    commit_shas.add(sha)

            if not commit_shas:
                return None

            # Alle Semver-Tags mit ihren Commits holen
            # %(*objectname:short) = Commit-SHA bei annotated Tags (leer bei lightweight)
            # %(objectname:short) = Tag-Object-SHA (annotated) oder Commit-SHA (lightweight)
            result = subprocess.run(
                ['git', 'tag', '-l', 'v*',
                 '--format=%(refname:short) %(*objectname:short) %(objectname:short)'],
                capture_output=True, text=True, cwd=project_path, timeout=5
            )
            if result.returncode != 0 or not result.stdout.strip():
                return None

            matched_tags = []
            for line in result.stdout.strip().splitlines():
                parts = line.split()
                if len(parts) < 2:
                    continue
                tag_name = parts[0]
                # Annotated: parts = [name, deref_commit_sha, tag_object_sha]
                # Lightweight: parts = [name, commit_sha] (kein deref)
                tag_sha = parts[1] if len(parts) == 3 and parts[1] else parts[-1]
                for commit_sha in commit_shas:
                    if commit_sha.startswith(tag_sha) or tag_sha.startswith(commit_sha[:7]):
                        version = tag_name.lstrip('v')
                        if re.match(r'^\d+\.\d+\.\d+$', version):
                            matched_tags.append(version)

            if not matched_tags:
                return None

            matched_tags.sort(key=lambda v: [int(x) for x in v.split('.')], reverse=True)
            self.logger.info(f"🏷️ Git-Tag im Batch gefunden: v{matched_tags[0]} ({repo_name})")
            return matched_tags[0]
        except Exception as e:
            self.logger.debug(f"Git-Tag-Suche fehlgeschlagen: {e}")
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

    def _build_discord_summary(self, ai_result, commits: list, language: str,
                                git_stats: Optional[Dict] = None) -> str:
        """Kürzt Content für Discord wenn changelog_url gesetzt ist (Teaser-Ersatz).

        Extrahiert TL;DR + max 6 Highlights aus dem Content, egal ob dict oder Raw-Text.
        Der "Alle Details" Link wird danach vom Caller angehängt.
        """
        MAX_HIGHLIGHTS = 6

        # Strukturiert: TL;DR + Highlights aus changes
        if isinstance(ai_result, dict):
            parts = []
            tldr = ai_result.get('tldr', '')
            if tldr:
                parts.append(f"> {tldr}")
                parts.append("")

            changes = ai_result.get('changes', [])
            highlights = ai_result.get('discord_highlights', [])

            # Inline-Credits auch im Summary-Modus
            team_credits = git_stats.get('team_credits', {}) if git_stats else {}
            if team_credits and changes:
                self._enrich_changes_with_git_authors(changes, commits, team_credits)

            if changes:
                unique_authors = {c.get('author', '') for c in changes if c.get('author', '')}
                show_author = len(unique_authors) >= 1
                for c in changes[:MAX_HIGHLIGHTS]:
                    parts.append(self._format_change_line(c, show_author))
                if len(changes) > MAX_HIGHLIGHTS:
                    parts.append(f"*+{len(changes) - MAX_HIGHLIGHTS} weitere Änderungen*")
            elif highlights:
                for h in highlights[:MAX_HIGHLIGHTS]:
                    parts.append(f"\u2192 {h}")
            elif ai_result.get('summary', ''):
                parts.append(ai_result['summary'][:500])

            return '\n'.join(parts) if parts else self._categorize_commits_text(commits, language)

        # Raw-Text: Ersten Absatz als TL;DR, dann Zeilen mit • oder → als Highlights
        elif isinstance(ai_result, str) and ai_result.strip():
            text = ai_result.strip()
            lines = text.split('\n')

            parts = []
            highlights = []

            for line in lines:
                stripped = line.strip()
                # TL;DR: Erster nicht-leerer, nicht-Header Absatz
                if not parts and stripped and not stripped.startswith('#') and not stripped.startswith('**'):
                    if stripped.startswith('>'):
                        parts.append(stripped)
                    else:
                        parts.append(f"> {stripped[:200]}")
                    continue
                # Highlights: Zeilen mit •, →, - als Aufzählungen
                if stripped and (stripped.startswith(('\u2022 ', '\u2192 ', '- **', '• **'))):
                    # Feature-Name extrahieren (fett)
                    highlight = stripped.lstrip('\u2022\u2192- ').strip()
                    # Nach dem ersten Satz abschneiden
                    if ' — ' in highlight:
                        highlight = highlight.split(' — ')[0]
                    elif ' – ' in highlight:
                        highlight = highlight.split(' – ')[0]
                    highlights.append(f"\u2192 {highlight}")

            if parts:
                parts.append("")
            for h in highlights[:MAX_HIGHLIGHTS]:
                parts.append(h)
            if len(highlights) > MAX_HIGHLIGHTS:
                parts.append(f"*+{len(highlights) - MAX_HIGHLIGHTS} weitere Änderungen*")

            if not parts:
                # Fallback: Ersten 400 Zeichen
                parts.append(text[:400] + ('...' if len(text) > 400 else ''))

            return '\n'.join(parts)

        return self._categorize_commits_text(commits, language)

    def _build_description(self, ai_result, commits: list, language: str,
                            discord_only: bool = False,
                            git_stats: Optional[Dict] = None) -> str:
        """Baut Description aus AI-Ergebnis (dict/str/None)."""
        if isinstance(ai_result, dict):
            return self._description_from_structured(
                ai_result, commits, language, discord_only=discord_only, git_stats=git_stats
            )
        elif isinstance(ai_result, str) and ai_result.strip():
            # Raw-Text bereinigen (pseudo-strukturierte Artefakte entfernen)
            cleaned, _, _ = self._clean_raw_text_content(ai_result)
            return cleaned
        else:
            return self._categorize_commits_text(commits, language)

    @staticmethod
    def _format_change_line(change: dict, show_author: bool) -> str:
        """Formatiere eine Change-Zeile mit optionaler Inline-Attribution."""
        desc = change.get('description', '')
        author = change.get('author', '')
        if show_author and author:
            return f"\u2192 {desc} \u00b7 *{author}*"
        return f"\u2192 {desc}"

    def _enrich_changes_with_git_authors(self, changes: list, commits: list,
                                          team_credits: Dict) -> None:
        """Reichere AI-generierte Changes mit echten Git-Autoren an.

        Matcht jede Change-Description gegen Commit-Messages per Keyword-Overlap
        und setzt den Author aus den echten Git-Daten.
        """
        if not team_credits or not commits:
            return

        # Commit-Message → Author-Name Mapping aufbauen
        commit_authors = {}
        for commit in commits:
            author_name = commit.get('author', {}).get('name') or \
                          commit.get('author', {}).get('username', '')
            if not author_name:
                continue
            # _resolve_team_member lebt auf AIPatchNotesMixin, erreichbar über self (MRO)
            member = getattr(self, '_resolve_team_member', lambda x: None)(author_name)
            if member is None:
                continue
            display_name = member[0]
            # Commit-Titel als Key (ohne Conventional-Commit Prefix)
            title = commit.get('message', '').split('\n')[0]
            clean_title = re.sub(
                r'^(feat|fix|chore|docs|perf|refactor|style|test)(\([^)]*\))?[!:]?\s*', '', title
            ).strip().lower()
            if clean_title:
                commit_authors[clean_title] = display_name

        if not commit_authors:
            return

        # Für jeden Change: Besten Match über Keyword-Overlap finden
        for change in changes:
            if change.get('author'):
                continue  # AI hat schon einen Author gesetzt, nicht überschreiben
            desc_lower = change.get('description', '').lower()
            if not desc_lower:
                continue

            desc_words = set(re.findall(r'\w{3,}', desc_lower))
            best_match = None
            best_score = 0

            for commit_title, author in commit_authors.items():
                commit_words = set(re.findall(r'\w{3,}', commit_title))
                overlap = len(desc_words & commit_words)
                if overlap > best_score:
                    best_score = overlap
                    best_match = author

            # Mindestens 2 gemeinsame Wörter für Zuordnung
            if best_match and best_score >= 2:
                change['author'] = best_match
            elif best_match and best_score >= 1 and len(desc_words) <= 4:
                # Bei kurzen Descriptions reicht 1 Match
                change['author'] = best_match

        # Fallback: Wenn nur 1 Person im Team ist, alle ohne Author zuweisen
        human_credits = {k: v for k, v in team_credits.items() if k != '__autonomous__'}
        if len(human_credits) == 1:
            sole_author = next(iter(human_credits))
            for change in changes:
                if not change.get('author'):
                    change['author'] = sole_author

    def _description_from_structured(self, ai_data: dict, commits: list, language: str,
                                      discord_only: bool = False,
                                      git_stats: Optional[Dict] = None) -> str:
        """Strukturierte AI-Daten → fließende Discord Description.

        Args:
            discord_only: Wenn True, werden Details mit angezeigt (kein Web-Link verfuegbar).
                          Community-optimiertes Format: ausfuehrlicher aber nicht zu lang.
            git_stats: Git-Stats mit team_credits für Inline-Author-Attribution.
        """
        parts = []

        tldr = ai_data.get('tldr', '')
        if tldr:
            parts.append(f"> {tldr}")
            parts.append("")

        # Bei Discord-only: Summary als Einleitung hinzufuegen
        if discord_only:
            summary = ai_data.get('summary', '')
            if summary and summary != tldr:
                parts.append(summary)
                parts.append("")

        changes = ai_data.get('changes', [])

        # Changes mit echten Git-Autoren anreichern (post-processing)
        team_credits = git_stats.get('team_credits', {}) if git_stats else {}
        if team_credits:
            self._enrich_changes_with_git_authors(changes, commits, team_credits)

        features = [c for c in changes if c.get('type') == 'feature']
        fixes = [c for c in changes if c.get('type') == 'fix']
        improvements = [c for c in changes if c.get('type') == 'improvement']
        breaking = ai_data.get('breaking_changes', [])
        is_major = len(commits) >= 15

        # Inline-Credits anzeigen wenn Team-Credits vorhanden
        unique_authors = {c.get('author', '') for c in changes if c.get('author', '')}
        show_author = len(unique_authors) >= 1

        # Bei Discord-only: Mehr Features zeigen + Details
        max_features = (8 if is_major else 6) if discord_only else (6 if is_major else 4)
        max_fixes = 6 if discord_only else 4
        max_improvements = 5 if discord_only else 3

        if features:
            parts.append("**\U0001f195 Neue Features**")
            for f in features[:max_features]:
                parts.append(self._format_change_line(f, show_author))
                # Discord-only: Details mit anzeigen (kurz)
                if discord_only:
                    details = f.get('details', [])
                    for d in details[:2]:
                        parts.append(f"  \u2022 {d}")
            if len(features) > max_features:
                parts.append(f"  *+{len(features) - max_features} weitere*")
            parts.append("")

        if breaking:
            parts.append("**\u26a0\ufe0f Breaking Changes**")
            for b in breaking[:3]:
                parts.append(f"\u26a0\ufe0f {b}")
            parts.append("")

        if fixes:
            parts.append("**\U0001f41b Bugfixes**")
            for f in fixes[:max_fixes]:
                parts.append(self._format_change_line(f, show_author))
            if len(fixes) > max_fixes:
                parts.append(f"  *+{len(fixes) - max_fixes} weitere*")
            parts.append("")

        if improvements:
            parts.append("**\u26a1 Verbesserungen**")
            for i in improvements[:max_improvements]:
                parts.append(self._format_change_line(i, show_author))
            if len(improvements) > max_improvements:
                parts.append(f"  *+{len(improvements) - max_improvements} weitere*")
            parts.append("")

        # Fallback wenn keine changes
        if not changes and not breaking:
            highlights = ai_data.get('discord_highlights', [])
            if highlights:
                parts.append("**\U0001f525 Highlights**")
                for h in highlights[:7 if discord_only else 5]:
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
        # Projektname hübsch formatieren (mayday_sim → MAYDAY SIM, zerodox → ZERODOX)
        display_name = repo_name.replace('_', ' ').replace('-', ' ').upper()
        embed.set_author(name=display_name)

        # Description bauen — Discord-only wenn keine Changelog-Page existiert
        is_discord_only = not changelog_link

        # Teaser-Modus: Discord-Kurzversion wenn changelog_url gesetzt
        if not is_discord_only and isinstance(ai_result, dict) and ai_result.get('discord_teaser'):
            description = ai_result['discord_teaser']
        elif not is_discord_only:
            # changelog_url gesetzt aber kein Teaser → Content kürzen für Discord
            description = self._build_discord_summary(ai_result, commits, language, git_stats=git_stats)
        else:
            description = self._build_description(
                ai_result, commits, language, discord_only=is_discord_only, git_stats=git_stats
            )

        # Changelog-Link am Ende (nur wenn Page vorhanden)
        if changelog_link:
            link_text = "Alle Details & vollständige Patch Notes" if language == 'de' else "Full details & complete patch notes"
            description += f"\n\n\U0001f4d6 [{link_text}]({changelog_link})"

        embed.description = description[:4096]

        # Footer
        embed.set_footer(text=self._build_footer(version, commits, git_stats))

        return embed

    @staticmethod
    def _clean_raw_text_content(text: str) -> tuple[str, list, str]:
        """Bereinige Raw-Text von pseudo-strukturierten Artefakten.

        Wenn die AI statt JSON einen Hybrid aus Markdown + JSON-Blöcken liefert,
        enthält der Text Felder wie **changes** ```json [...] oder **discord_teaser**.
        Diese werden extrahiert und aus dem Content entfernt.

        Returns:
            (cleaned_content, parsed_changes, parsed_teaser)
        """
        import json as _json

        parsed_changes = []
        parsed_teaser = ''

        # 1. **patch_notes** Header entfernen
        text = re.sub(r'^\*\*patch_notes\*\*\s*\n?', '', text.strip())

        # 2. **changes** ```json [...] ``` Block extrahieren und entfernen
        changes_match = re.search(
            r'\*\*changes\*\*\s*\n```json\s*\n(.*?)\n```',
            text, re.DOTALL
        )
        if changes_match:
            try:
                parsed_changes = _json.loads(changes_match.group(1))
            except (_json.JSONDecodeError, ValueError):
                pass
            text = text[:changes_match.start()] + text[changes_match.end():]

        # 3. **discord_teaser** Sektion extrahieren und entfernen
        teaser_match = re.search(
            r'\*\*discord_teaser\*\*\s*\n(.*?)(?=\n\*\*\w|$)',
            text, re.DOTALL
        )
        if teaser_match:
            parsed_teaser = teaser_match.group(1).strip()
            text = text[:teaser_match.start()] + text[teaser_match.end():]

        # 4. Credits-Zeile entfernen (👥 **Dieses Update:** ... oder 👥 **This Update:** ...)
        text = re.sub(r'→?\s*👥\s*\*\*(?:Dieses Update|This Update):\*\*[^\n]*\n?', '', text)

        # 5. Trailing Whitespace + mehrfache Leerzeilen bereinigen
        text = re.sub(r'\n{3,}', '\n\n', text).strip()

        return text, parsed_changes, parsed_teaser

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
            # Raw-Text bereinigen (pseudo-strukturierte Artefakte entfernen)
            text, parsed_changes, parsed_teaser = self._clean_raw_text_content(ai_result)

            # TL;DR aus erstem Satz extrahieren
            tldr_match = re.search(r'\*\*TL;DR:\*\*\s*(.+?)(?:\n|$)', text)
            if tldr_match:
                tldr = tldr_match.group(1).strip()
            else:
                # Erste Blockquote-Zeile oder erste Nicht-Header-Zeile
                for line in text.split('\n'):
                    line = line.strip()
                    if line.startswith('> '):
                        tldr = re.sub(r'[>\*]+', '', line).strip()[:200]
                        break
                    elif line and not line.startswith('**') and not line.startswith('#'):
                        tldr = line[:200]
                        break
                else:
                    tldr = f"{repo_name} Update"

            # Titel aus erster fetter Überschrift extrahieren
            title_match = re.search(r'\*\*([^*]+)\*\*', text)
            if title_match:
                candidate = title_match.group(1).strip()
                # Nur verwenden wenn es nach einem echten Titel aussieht (keine Kategorie)
                if len(candidate) > 10 and not any(k in candidate.lower() for k in ['feature', 'bugfix', 'verbesserung', 'neue', 'fix']):
                    title = candidate[:100]
                else:
                    title = f"{repo_name} Update"
            else:
                title = f"{repo_name} Update"

            content = text
            changes = parsed_changes
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

        # Rollen-Ping für öffentlichen Update-Channel (optional)
        role_id = project_config.get('update_channel_role_mention')
        role_mention = f"<@&{role_id}>" if role_id else ""

        try:
            description_chunks = self._split_embed_description(embed.description or "")
            sent_message = None
            mention_content = f"{role_mention} Neues Update verfügbar!" if role_mention else None
            mention_kwargs = {"allowed_mentions": discord.AllowedMentions(roles=True)} if role_mention else {}

            if len(description_chunks) <= 1:
                sent_message = await customer_channel.send(
                    content=mention_content, embed=embed, view=view, **mention_kwargs
                )
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
                    # Rollen-Ping nur bei der ersten Nachricht
                    content = mention_content if i == 0 else None
                    kwargs = mention_kwargs if i == 0 else {}
                    message = await customer_channel.send(
                        content=content, embed=embed_copy, view=msg_view, **kwargs
                    )
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

    @staticmethod
    def _normalize_german_umlauts(data):
        """Normalisiere AI-Output: ae→ä, oe→ö, ue→ü, ss→ß (kontextsensitiv).

        AI-Engines geben manchmal ASCII-safe Text statt Umlaute zurück.
        Ersetzt nur in typisch deutschen Wörtern, nicht in Fremdwörtern.
        """
        import re

        # Häufige Wörter mit falschem Umlaut → korrekte Form
        # Nur sichere Ersetzungen (keine Fremdwörter wie "Blue", "Queue")
        SAFE_REPLACEMENTS = {
            # ue → ü
            'fuer': 'für', 'Fuer': 'Für', 'ueber': 'über', 'Ueber': 'Über',
            'ueberarbeitet': 'überarbeitet', 'Ueberarbeitet': 'Überarbeitet',
            'Ueberblick': 'Überblick', 'ueberblick': 'überblick',
            'Uebergaeng': 'Übergäng', 'uebergaeng': 'übergäng',
            'Uebergang': 'Übergang', 'uebergang': 'übergang',
            'gruess': 'grüß', 'Gruess': 'Grüß',
            'spuerbar': 'spürbar', 'fluessig': 'flüssig',
            'Einfuehrung': 'Einführung', 'einfuehrung': 'einführung',
            'verfuegbar': 'verfügbar', 'Verfuegbar': 'Verfügbar',
            'unterstuetz': 'unterstütz', 'Unterstuetz': 'Unterstütz',
            'zuverlaessig': 'zuverlässig', 'Zuverlaessig': 'Zuverlässig',
            'Ausfuehrlich': 'Ausführlich', 'ausfuehrlich': 'ausführlich',
            'natuerlich': 'natürlich', 'Natuerlich': 'Natürlich',
            'genuegt': 'genügt',
            # ae → ä
            'Aenderung': 'Änderung', 'aenderung': 'änderung',
            'Aenderungen': 'Änderungen', 'aenderungen': 'änderungen',
            'Uebersicht': 'Übersicht', 'uebersicht': 'übersicht',
            'naechst': 'nächst', 'Naechst': 'Nächst',
            'staerker': 'stärker', 'Staerker': 'Stärker',
            'waehrend': 'während', 'Waehrend': 'Während',
            'spaeter': 'später', 'Spaeter': 'Später',
            'haeufig': 'häufig', 'Haeufig': 'Häufig',
            'schaerfer': 'schärfer', 'faehig': 'fähig',
            'vollstaendig': 'vollständig', 'Vollstaendig': 'Vollständig',
            'Atmosphaere': 'Atmosphäre', 'atmosphaere': 'atmosphäre',
            'Stabilitaet': 'Stabilität', 'stabilitaet': 'stabilität',
            'Qualitaet': 'Qualität', 'qualitaet': 'qualität',
            # oe → ö
            'groesser': 'größer', 'Groesser': 'Größer',
            'groesste': 'größte', 'Groesste': 'Größte',
            'koennen': 'können', 'Koennen': 'Können',
            'koennt': 'könnt', 'moechte': 'möchte',
            'moeglich': 'möglich', 'Moeglich': 'Möglich',
            'geloest': 'gelöst', 'Geloest': 'Gelöst',
            'hoechst': 'höchst', 'Hoechst': 'Höchst',
            'Loeschung': 'Löschung', 'loeschung': 'löschung',
            # ss → ß (kontextsensitiv)
            'grosse': 'große', 'Grosse': 'Große',
            'grosser': 'großer', 'grosses': 'großes',
            'schliessen': 'schließen', 'Schliessen': 'Schließen',
            'Strasse': 'Straße', 'strasse': 'straße',
            'Strassen': 'Straßen', 'strassen': 'straßen',
            'heisst': 'heißt', 'Heisst': 'Heißt',
            'weiss': 'weiß', 'Weiss': 'Weiß',
            'draussen': 'draußen', 'Draussen': 'Draußen',
            'schliesslich': 'schließlich',
        }

        def _replace_in_text(text: str) -> str:
            if not isinstance(text, str):
                return text
            for wrong, correct in SAFE_REPLACEMENTS.items():
                # Wortgrenzen-Match um Teilwörter korrekt zu ersetzen
                text = re.sub(rf'\b{re.escape(wrong)}', correct, text)
            return text

        if isinstance(data, str):
            return _replace_in_text(data)
        elif isinstance(data, dict):
            result = {}
            for key, value in data.items():
                if isinstance(value, str):
                    result[key] = _replace_in_text(value)
                elif isinstance(value, list):
                    result[key] = [
                        _replace_in_text(item) if isinstance(item, str)
                        else (NotificationsMixin._normalize_german_umlauts(item) if isinstance(item, dict) else item)
                        for item in value
                    ]
                elif isinstance(value, dict):
                    result[key] = NotificationsMixin._normalize_german_umlauts(value)
                else:
                    result[key] = value
            return result
        return data
