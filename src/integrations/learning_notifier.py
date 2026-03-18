"""
Learning Notifier — Automatische Discord-Posts über Agent-Erkenntnisse.

Postet in den 🧠-ai-learning Channel:
- Nach jeder Analyst-Session: Was gelernt, was gefunden, was gefixt
- Nach Feedback-Auswertung: Welche Patch Notes gut ankamen
- Wöchentliches Learning-Summary: DB-Wachstum, Trends, Top-Insights
- Meilensteine: Erste 50 Fixes, neue Patterns, Regressionen
"""

import logging
from datetime import datetime, timezone
from typing import Optional, Dict, List

import discord

logger = logging.getLogger('shadowops.learning')


class LearningNotifier:
    """Automatische Discord-Benachrichtigungen über Agent-Learning."""

    def __init__(self, bot):
        self.bot = bot

    def _get_channel(self) -> Optional[discord.TextChannel]:
        """Holt den 🧠-ai-learning Channel."""
        channel_id = self.bot.config.channels.get('ai_learning')
        if not channel_id:
            return None
        return self.bot.get_channel(int(channel_id))

    # ─────────────────────────────────────────────
    # Analyst: Session-Zusammenfassung
    # ─────────────────────────────────────────────

    async def notify_analyst_session(
        self,
        session_id: int,
        mode: str,
        findings_count: int,
        fixed_count: int,
        pr_count: int,
        tokens_used: int,
        regressions: int = 0,
        coverage_areas: int = 0,
        new_patterns: int = 0,
    ):
        """Kompakte Session-Zusammenfassung nach jedem Analyst-Run."""
        channel = self._get_channel()
        if not channel:
            return

        # Farbe basierend auf Ergebnis
        if regressions > 0:
            color = 0xE74C3C  # Rot — Regressionen
        elif findings_count > 0:
            color = 0xF39C12  # Orange — neue Findings
        elif fixed_count > 0:
            color = 0x2ECC71  # Grün — Fixes
        else:
            color = 0x3498DB  # Blau — Routine

        mode_labels = {
            'full_scan': '🔍 Voller Scan',
            'quick_scan': '⚡ Quick-Scan',
            'fix_only': '🔧 Fix-Session',
            'maintenance': '🔄 Maintenance',
        }

        embed = discord.Embed(
            title=f"{mode_labels.get(mode, mode)} — Session #{session_id}",
            color=color,
            timestamp=datetime.now(timezone.utc),
        )

        # Kompakte Stats in einer Zeile
        stats_parts = []
        if findings_count:
            stats_parts.append(f"📋 {findings_count} Findings")
        if fixed_count:
            stats_parts.append(f"✅ {fixed_count} gefixt")
        if pr_count:
            stats_parts.append(f"🔀 {pr_count} PRs")
        if regressions:
            stats_parts.append(f"⚠️ {regressions} Regressionen")
        if not stats_parts:
            stats_parts.append("✅ Alles clean")

        embed.description = " · ".join(stats_parts)

        # Zusatz-Infos nur wenn relevant
        details = []
        if coverage_areas:
            details.append(f"Scan-Abdeckung: {coverage_areas}/10 Bereiche")
        if new_patterns:
            details.append(f"Neue Patterns erkannt: {new_patterns}")
        if tokens_used:
            details.append(f"Token: ~{tokens_used:,}")

        if details:
            embed.add_field(name="Details", value="\n".join(details), inline=False)

        embed.set_footer(text="Security Analyst")

        try:
            await channel.send(embed=embed)
        except Exception as e:
            logger.debug("Learning-Notification fehlgeschlagen: %s", e)

    # ─────────────────────────────────────────────
    # Patch Notes: Feedback-Ergebnis
    # ─────────────────────────────────────────────

    async def notify_feedback_evaluated(
        self,
        project: str,
        version: str,
        variant_id: Optional[str],
        auto_score: float,
        feedback_score: float,
        combined_score: float,
        feedback_count: int,
    ):
        """Ergebnis einer Feedback-Auswertung posten."""
        channel = self._get_channel()
        if not channel:
            return

        if feedback_count == 0:
            return  # Kein Feedback → nicht posten

        # Score-Bewertung
        if combined_score >= 80:
            color = 0x2ECC71
            verdict = "⭐ Ausgezeichnet"
        elif combined_score >= 60:
            color = 0x3498DB
            verdict = "👍 Gut"
        elif combined_score >= 40:
            color = 0xF39C12
            verdict = "➡️ Ausbaufähig"
        else:
            color = 0xE74C3C
            verdict = "👎 Schwach"

        embed = discord.Embed(
            title=f"📝 Patch Notes Feedback — {project} v{version}",
            description=f"**{verdict}** (Score: {combined_score:.0f}/100)",
            color=color,
            timestamp=datetime.now(timezone.utc),
        )

        embed.add_field(
            name="Scores",
            value=f"Auto: {auto_score:.0f} · Feedback: {feedback_score:.0f} · Combined: {combined_score:.0f}",
            inline=False,
        )

        if variant_id:
            embed.add_field(name="Variante", value=f"`{variant_id}`", inline=True)
        embed.add_field(name="Feedbacks", value=str(feedback_count), inline=True)

        embed.set_footer(text="Patch Notes Learning")

        try:
            await channel.send(embed=embed)
        except Exception:
            pass

    # ─────────────────────────────────────────────
    # Wöchentliches Learning-Summary
    # ─────────────────────────────────────────────

    async def send_weekly_summary(self):
        """Wöchentliches Learning-Summary — alle Agents zusammen."""
        channel = self._get_channel()
        if not channel:
            return

        embed = discord.Embed(
            title="🧠 Wöchentliches Learning-Summary",
            description="Was die AI-Agents diese Woche gelernt haben",
            color=0x9B59B6,
            timestamp=datetime.now(timezone.utc),
        )

        # ── Security Analyst ──
        analyst = getattr(self.bot, 'security_analyst', None)
        if analyst and analyst.db and analyst.db.pool:
            try:
                stats = await analyst.db._get_30day_stats()
                # Letzte 7 Tage
                week_sessions = await analyst.db.pool.fetchval(
                    "SELECT COUNT(*) FROM sessions WHERE started_at >= NOW() - INTERVAL '7 days' AND status='completed'"
                )
                week_findings = await analyst.db.pool.fetchval(
                    "SELECT COUNT(*) FROM findings WHERE found_at >= NOW() - INTERVAL '7 days'"
                )
                week_fixes = await analyst.db.pool.fetchval(
                    "SELECT COUNT(*) FROM fix_attempts WHERE created_at >= NOW() - INTERVAL '7 days' AND result='success'"
                )
                week_regressions = await analyst.db.pool.fetchval(
                    "SELECT COUNT(*) FROM fix_verifications WHERE checked_at >= NOW() - INTERVAL '7 days' AND still_valid=FALSE"
                )

                analyst_text = (
                    f"Sessions: **{week_sessions}** · "
                    f"Findings: **{week_findings}** · "
                    f"Fixes: **{week_fixes}** ✅"
                )
                if week_regressions:
                    analyst_text += f" · Regressionen: **{week_regressions}** ⚠️"
                analyst_text += f"\nGesamt: {stats['findings_open']} offen / {stats['findings_fixed']} behoben"

                embed.add_field(name="🔒 Security Analyst", value=analyst_text, inline=False)
            except Exception:
                pass

        # ── Patch Notes ──
        try:
            from integrations.patch_notes_learning import PatchNotesLearning
            pn = PatchNotesLearning()
            await pn.connect()

            week_gens = await pn.pool.fetchval(
                "SELECT COUNT(*) FROM pn_generations WHERE created_at >= NOW() - INTERVAL '7 days'"
            )
            week_fb = await pn.pool.fetchval(
                "SELECT COUNT(*) FROM agent_feedback WHERE agent='patch_notes' AND created_at >= NOW() - INTERVAL '7 days'"
            )
            avg_score = await pn.pool.fetchval(
                "SELECT AVG(combined_score) FROM agent_quality_scores WHERE agent='patch_notes' AND assessed_at >= NOW() - INTERVAL '7 days'"
            )

            pn_text = f"Generierungen: **{week_gens}** · Feedbacks: **{week_fb}**"
            if avg_score:
                pn_text += f" · Ø Score: **{avg_score:.0f}**/100"

            embed.add_field(name="📝 Patch Notes", value=pn_text, inline=False)

            # DB-Größe
            total_rows = await pn.pool.fetchval(
                """SELECT
                    (SELECT COUNT(*) FROM pn_generations) +
                    (SELECT COUNT(*) FROM agent_feedback) +
                    (SELECT COUNT(*) FROM pn_examples) +
                    (SELECT COUNT(*) FROM agent_knowledge) +
                    (SELECT COUNT(*) FROM seo_fix_impact)"""
            )
            await pn.close()

            embed.add_field(
                name="💾 agent_learning DB",
                value=f"**{total_rows}** Datensätze gesamt",
                inline=True,
            )
        except Exception:
            pass

        # ── Security Analyst DB ──
        if analyst and analyst.db and analyst.db.pool:
            try:
                sa_rows = await analyst.db.pool.fetchval(
                    """SELECT
                        (SELECT COUNT(*) FROM sessions) +
                        (SELECT COUNT(*) FROM findings) +
                        (SELECT COUNT(*) FROM knowledge) +
                        (SELECT COUNT(*) FROM fix_attempts) +
                        (SELECT COUNT(*) FROM scan_coverage) +
                        (SELECT COUNT(*) FROM finding_quality) +
                        (SELECT COUNT(*) FROM learned_patterns)"""
                )
                embed.add_field(
                    name="💾 security_analyst DB",
                    value=f"**{sa_rows}** Datensätze gesamt",
                    inline=True,
                )
            except Exception:
                pass

        embed.set_footer(text="Wöchentliches Summary · Montag 08:00")

        try:
            await channel.send(embed=embed)
            logger.info("Wöchentliches Learning-Summary gesendet")
        except Exception as e:
            logger.debug("Weekly-Summary fehlgeschlagen: %s", e)

    # ─────────────────────────────────────────────
    # Meilensteine
    # ─────────────────────────────────────────────

    async def check_milestones(self):
        """Prüft ob ein Learning-Meilenstein erreicht wurde."""
        channel = self._get_channel()
        if not channel:
            return

        analyst = getattr(self.bot, 'security_analyst', None)
        if not analyst or not analyst.db or not analyst.db.pool:
            return

        try:
            total_fixes = await analyst.db.pool.fetchval(
                "SELECT COUNT(*) FROM fix_attempts WHERE result='success'"
            )
            total_findings = await analyst.db.pool.fetchval(
                "SELECT COUNT(*) FROM findings"
            )
            total_sessions = await analyst.db.pool.fetchval(
                "SELECT COUNT(*) FROM sessions WHERE status='completed'"
            )

            milestones = [
                (total_fixes, [10, 25, 50, 100, 250], "Fixes", "✅"),
                (total_findings, [25, 50, 100, 250, 500], "Findings", "📋"),
                (total_sessions, [10, 25, 50, 100], "Sessions", "🔍"),
            ]

            for value, thresholds, label, icon in milestones:
                for threshold in thresholds:
                    if value == threshold:
                        embed = discord.Embed(
                            title=f"🏆 Meilenstein: {threshold} {label}!",
                            description=f"{icon} Der Security Analyst hat **{threshold} {label}** erreicht.",
                            color=0xF1C40F,
                            timestamp=datetime.now(timezone.utc),
                        )
                        embed.set_footer(text="Learning Milestone")
                        await channel.send(embed=embed)
                        logger.info("Meilenstein: %d %s", threshold, label)
                        break

        except Exception:
            pass
