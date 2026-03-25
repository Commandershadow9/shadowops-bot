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

        # ── Security ScanAgent ──
        scan_agent = getattr(self.bot, 'security_analyst', None)
        pool = None
        if scan_agent and hasattr(scan_agent, 'db') and scan_agent.db and scan_agent.db.pool:
            pool = scan_agent.db.pool
        if pool:
            try:
                # Direkte Queries statt AnalystDB-Methoden
                findings_open = await pool.fetchval("SELECT COUNT(*) FROM findings WHERE status='open'")
                findings_fixed = await pool.fetchval(
                    "SELECT COUNT(*) FROM findings WHERE status='fixed' AND found_at >= NOW()-INTERVAL '30 days'")
                week_sessions = await pool.fetchval(
                    "SELECT COUNT(*) FROM sessions WHERE started_at >= NOW()-INTERVAL '7 days' AND status='completed'")
                week_findings = await pool.fetchval(
                    "SELECT COUNT(*) FROM findings WHERE found_at >= NOW()-INTERVAL '7 days'")
                week_fixes = await pool.fetchval(
                    "SELECT COUNT(*) FROM fix_attempts WHERE created_at >= NOW()-INTERVAL '7 days' AND result='success'")
                week_regressions = await pool.fetchval(
                    "SELECT COUNT(*) FROM fix_verifications WHERE checked_at >= NOW()-INTERVAL '7 days' AND still_valid=FALSE")

                analyst_text = (
                    f"Sessions: **{week_sessions}** · "
                    f"Findings: **{week_findings}** · "
                    f"Fixes: **{week_fixes}** ✅"
                )
                if week_regressions:
                    analyst_text += f" · Regressionen: **{week_regressions}** ⚠️"
                analyst_text += f"\nGesamt: {findings_open} offen / {findings_fixed} behoben"

                embed.add_field(name="🔒 Security ScanAgent", value=analyst_text, inline=False)
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

        # ── Security DB ──
        if pool:
            try:
                sa_rows = await pool.fetchval(
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
    # Weekly-Recap: Umfassender Wochenbericht
    # ─────────────────────────────────────────────

    async def send_weekly_recap(self, security_db=None):
        """Umfassender Weekly-Recap — Trends, Insights, Coverage, Fix-Impact.

        Wird nach dem Weekly-Deep-Scan aufgerufen (Sonntag Nacht).
        Detaillierter als send_weekly_summary() (das nur Stats zeigt).
        """
        channel = self._get_channel()
        if not channel:
            return

        pool = None
        if security_db and hasattr(security_db, 'pool') and security_db.pool:
            pool = security_db.pool
        if not pool:
            # Fallback: Pool vom Bot holen
            scan_agent = getattr(self.bot, 'security_analyst', None)
            if scan_agent and hasattr(scan_agent, 'db') and scan_agent.db:
                pool = scan_agent.db.pool
        if not pool:
            return

        try:
            # ── 1. Finding-Trend (4 Wochen) ──
            trend_rows = await pool.fetch("""
                SELECT date_trunc('week', found_at)::date as woche,
                       COUNT(*) as total,
                       COUNT(*) FILTER (WHERE severity IN ('critical','high')) as crit_high,
                       COUNT(*) FILTER (WHERE status='fixed') as fixed
                FROM findings WHERE found_at >= NOW()-INTERVAL '28 days'
                GROUP BY 1 ORDER BY 1
            """)

            trend_text = ""
            for r in trend_rows:
                emoji = "🟢" if r['crit_high'] == 0 else "🟡" if r['crit_high'] <= 3 else "🔴"
                trend_text += (f"{emoji} **KW {r['woche'].strftime('%d.%m.')}:** "
                               f"{r['total']} Findings ({r['crit_high']} crit/high), "
                               f"{r['fixed']} gefixt\n")
            if not trend_text:
                trend_text = "(keine Findings in den letzten 4 Wochen)"

            # ── 2. Coverage-Status ──
            coverage_rows = await pool.fetch("""
                SELECT area, MAX(checked_at)::date as letzte,
                       EXTRACT(DAY FROM NOW()-MAX(checked_at))::int as tage_her
                FROM scan_coverage WHERE checked=TRUE GROUP BY area
                ORDER BY tage_her DESC
            """)

            coverage_text = ""
            for r in coverage_rows:
                emoji = "✅" if r['tage_her'] <= 1 else "🟡" if r['tage_her'] <= 3 else "🔴"
                coverage_text += f"{emoji} {r['area']} ({r['tage_her']}d)\n"
            if not coverage_text:
                coverage_text = "(keine Coverage-Daten)"

            # ── 3. Fix-Effektivitaet ──
            fix_rows = await pool.fetch("""
                SELECT f.category, COUNT(fa.*) as versuche,
                       COUNT(*) FILTER (WHERE fa.result='success') as erfolge
                FROM fix_attempts fa JOIN findings f ON f.id=fa.finding_id
                GROUP BY f.category ORDER BY versuche DESC LIMIT 5
            """)

            fix_text = ""
            for r in fix_rows:
                rate = (r['erfolge'] / r['versuche'] * 100) if r['versuche'] > 0 else 0
                emoji = "🟢" if rate >= 80 else "🟡" if rate >= 50 else "🔴"
                fix_text += f"{emoji} {r['category']}: {r['erfolge']}/{r['versuche']} ({rate:.0f}%)\n"
            if not fix_text:
                fix_text = "(keine Fix-Versuche)"

            # ── 4. Neue Insights (diese Woche) ──
            insight_rows = await pool.fetch("""
                SELECT category, subject, content, confidence
                FROM knowledge WHERE last_verified >= NOW()-INTERVAL '7 days'
                  AND confidence >= 0.8
                ORDER BY confidence DESC LIMIT 8
            """)

            insight_text = ""
            for r in insight_rows:
                insight_text += f"- **{r['subject']}** ({int(r['confidence']*100)}%): {r['content'][:80]}\n"
            if not insight_text:
                insight_text = "(keine neuen Insights)"

            # ── 5. Offene Findings Zusammenfassung ──
            open_stats = await pool.fetchrow("""
                SELECT COUNT(*) as total,
                       COUNT(*) FILTER (WHERE severity='critical') as critical,
                       COUNT(*) FILTER (WHERE severity='high') as high,
                       COUNT(*) FILTER (WHERE severity='medium') as medium,
                       COUNT(*) FILTER (WHERE severity='low') as low,
                       COUNT(*) FILTER (WHERE severity='info') as info
                FROM findings WHERE status='open'
            """)

            # ── 6. Wochen-Stats ──
            week_stats = await pool.fetchrow("""
                SELECT COUNT(*) as sessions,
                       COALESCE(SUM(findings_count),0) as findings,
                       COALESCE(SUM(auto_fixes_count),0) as fixes,
                       COALESCE(SUM(issues_created),0) as issues
                FROM sessions WHERE started_at >= NOW()-INTERVAL '7 days' AND status='completed'
            """)

            # ── Embed bauen ──
            crit_count = (open_stats['critical'] or 0) + (open_stats['high'] or 0)
            if crit_count > 0:
                color = 0xE74C3C
                status_emoji = "🔴"
            elif (open_stats['medium'] or 0) > 5:
                color = 0xF39C12
                status_emoji = "🟡"
            else:
                color = 0x2ECC71
                status_emoji = "🟢"

            embed = discord.Embed(
                title=f"{status_emoji} Weekly Security Recap",
                description=(
                    f"**{week_stats['sessions']}** Sessions · "
                    f"**{week_stats['findings']}** Findings · "
                    f"**{week_stats['fixes']}** Fixes · "
                    f"**{week_stats['issues']}** Issues"
                ),
                color=color,
                timestamp=datetime.now(timezone.utc),
            )

            # Offene Findings
            open_text = (
                f"🔴 {open_stats['critical'] or 0} Critical · "
                f"🟠 {open_stats['high'] or 0} High · "
                f"🟡 {open_stats['medium'] or 0} Medium · "
                f"🔵 {open_stats['low'] or 0} Low · "
                f"⚪ {open_stats['info'] or 0} Info"
            )
            embed.add_field(name=f"📊 Offene Findings ({open_stats['total']})", value=open_text, inline=False)
            embed.add_field(name="📈 4-Wochen-Trend", value=trend_text[:1024], inline=False)
            embed.add_field(name="🗺️ Coverage", value=coverage_text[:1024], inline=True)
            embed.add_field(name="🔧 Fix-Effektivitaet", value=fix_text[:1024], inline=True)
            embed.add_field(name="💡 Top-Insights", value=insight_text[:1024], inline=False)

            # ── 7. Proactive Report (aus Security Engine) ──
            try:
                engine = getattr(self.bot, 'security_engine', None)
                proactive = getattr(engine, '_last_proactive_report', None) if engine else None
                if proactive:
                    gaps = proactive.get('coverage_gaps', [])
                    eff = proactive.get('fix_effectiveness', {})
                    recs = proactive.get('recommendations', [])

                    proactive_parts = []
                    if gaps:
                        gap_names = [g.get('area', '?') for g in gaps[:5]]
                        proactive_parts.append(f"Coverage-Luecken: {', '.join(gap_names)}")
                    if eff:
                        eff_items = []
                        for src, stats in eff.items():
                            rate = stats.get('success_rate', 0)
                            emoji = '🟢' if rate >= 0.8 else '🟡' if rate >= 0.5 else '🔴'
                            eff_items.append(f"{emoji} {src}: {rate:.0%}")
                        proactive_parts.append("Fix-Rate: " + " · ".join(eff_items))
                    if recs:
                        proactive_parts.append(f"{len(recs)} Empfehlungen")

                    if proactive_parts:
                        embed.add_field(
                            name="📊 Proactive Report",
                            value="\n".join(proactive_parts)[:1024],
                            inline=False,
                        )
            except Exception:
                pass

            embed.set_footer(text="Weekly Security Recap · SecurityScanAgent")

            await channel.send(embed=embed)
            logger.info("Weekly Security Recap gesendet")

        except Exception as e:
            logger.warning("Weekly-Recap fehlgeschlagen: %s", e)

    # ─────────────────────────────────────────────
    # Meilensteine
    # ─────────────────────────────────────────────

    async def check_milestones(self):
        """Prüft ob ein Learning-Meilenstein erreicht wurde."""
        channel = self._get_channel()
        if not channel:
            return

        scan_agent = getattr(self.bot, 'security_analyst', None)
        pool = None
        if scan_agent and hasattr(scan_agent, 'db') and scan_agent.db and scan_agent.db.pool:
            pool = scan_agent.db.pool
        if not pool:
            return

        try:
            total_fixes = await pool.fetchval(
                "SELECT COUNT(*) FROM fix_attempts WHERE result='success'"
            )
            total_findings = await pool.fetchval(
                "SELECT COUNT(*) FROM findings"
            )
            total_sessions = await pool.fetchval(
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
