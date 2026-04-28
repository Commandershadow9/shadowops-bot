"""
Cog for commands that inspect the bot's internal state, like AI and project status.
"""

import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime

class InspectorCog(commands.Cog):
    """
    Contains slash commands for inspecting internal systems.
    """

    def __init__(self, bot):
        self.bot = bot
        self.logger = bot.logger

    @app_commands.command(name="get-ai-stats", description="🤖 Zeige AI-Provider Status und Statistiken")
    async def get_ai_stats_command(self, interaction: discord.Interaction):
        """Zeigt AI-Provider Status und Performance-Statistiken"""
        await interaction.response.defer(ephemeral=False)
        try:
            if not getattr(self.bot, 'ai_service', None):
                await interaction.followup.send("⏸️ AI ist deaktiviert", ephemeral=True)
                return

            embed = discord.Embed(
                title="🤖 AI Provider Status",
                description="Übersicht über alle konfigurierten AI-Provider",
                color=0x5865F2,
                timestamp=datetime.now()
            )
            # Codex (Primary)
            if hasattr(self.bot.ai_service, 'codex_provider'):
                codex = self.bot.ai_service.codex_provider
                codex_info = f"Status: 🟢 Primary\nModelle: `{codex.models}`"
                embed.add_field(name="⚡ Codex CLI (Primary)", value=codex_info, inline=False)
            # Claude (Fallback)
            if hasattr(self.bot.ai_service, 'claude_provider'):
                claude = self.bot.ai_service.claude_provider
                claude_info = f"Status: 🟡 Fallback\nModelle: `{claude.models}`\nCLI: `{claude.cli_path}`"
                embed.add_field(name="🧠 Claude CLI (Fallback)", value=claude_info, inline=False)
            # Stats
            if hasattr(self.bot.ai_service, 'stats'):
                stats = self.bot.ai_service.get_stats()
                stats_info = (
                    f"Codex: {stats.get('codex_success', 0)}/{stats.get('codex_calls', 0)} erfolgreich\n"
                    f"Claude: {stats.get('claude_success', 0)}/{stats.get('claude_calls', 0)} erfolgreich"
                )
                embed.add_field(name="📊 Engine Stats", value=stats_info, inline=False)

            await interaction.followup.send(embed=embed)
        except Exception as e:
            self.logger.error(f"❌ Fehler in /get-ai-stats: {e}", exc_info=True)
            await interaction.followup.send("❌ Fehler beim Abrufen der AI-Statistiken", ephemeral=True)

    @app_commands.command(name="projekt-status", description="📊 Zeige Status für ein bestimmtes Projekt")
    @app_commands.describe(name="Name des Projekts (z.B. shadowops-bot, guildscout)")
    async def projekt_status_command(self, interaction: discord.Interaction, name: str):
        """Zeigt detaillierten Status für ein spezifisches Projekt"""
        await interaction.response.defer(ephemeral=False)
        try:
            if not hasattr(self.bot, 'project_monitor') or not self.bot.project_monitor:
                await interaction.followup.send("⚠️ Project Monitor nicht verfügbar", ephemeral=True)
                return

            status = self.bot.project_monitor.get_project_status(name)
            if not status:
                await interaction.followup.send(f"❌ Projekt '{name}' nicht gefunden.", ephemeral=True)
                return

            is_online = status['is_online']
            status_emoji = "🟢" if is_online else "🔴"
            embed = discord.Embed(title=f"{status_emoji} {status['name']} - Status", color=discord.Color.green() if is_online else discord.Color.red(), timestamp=datetime.now())
            embed.add_field(name="🔌 Status", value=f"{status_emoji} {'Online' if is_online else 'Offline'}", inline=True)
            embed.add_field(name="📈 Uptime", value=f"{status['uptime_percentage']:.2f}%", inline=True)
            
            if is_online:
                embed.add_field(name="⚡ Avg Response", value=f"{status['average_response_time_ms']:.0f}ms", inline=True)
            
            if not is_online and status['last_error']:
                embed.add_field(name="⚠️ Last Error", value=f"```{{status['last_error'][:200]}}...```", inline=False)

            await interaction.followup.send(embed=embed)
        except Exception as e:
            self.logger.error(f"❌ Fehler in /projekt-status: {e}", exc_info=True)
            await interaction.followup.send("❌ Fehler beim Abrufen des Projekt-Status", ephemeral=True)

    @app_commands.command(name="alle-projekte", description="📋 Zeige Übersicht aller überwachten Projekte")
    async def alle_projekte_command(self, interaction: discord.Interaction):
        """Zeigt Status-Übersicht für alle Projekte"""
        await interaction.response.defer(ephemeral=False)
        try:
            if not hasattr(self.bot, 'project_monitor') or not self.bot.project_monitor:
                await interaction.followup.send("⚠️ Project Monitor nicht verfügbar", ephemeral=True)
                return

            all_statuses = self.bot.project_monitor.get_all_projects_status()
            if not all_statuses:
                await interaction.followup.send("ℹ️ Keine Projekte werden derzeit überwacht", ephemeral=True)
                return

            online_count = sum(1 for s in all_statuses if s['is_online'])
            total_count = len(all_statuses)
            color = discord.Color.green() if online_count == total_count else (discord.Color.red() if online_count == 0 else discord.Color.orange())
            
            embed = discord.Embed(title="📋 Alle Projekte - Status-Übersicht", description=f"🟢 **{online_count}** Online | 🔴 **{total_count - online_count}** Offline", color=color, timestamp=datetime.now())

            for status in sorted(all_statuses, key=lambda s: (not s['is_online'], s['name'].lower())):
                status_emoji = "🟢" if status['is_online'] else "🔴"
                value = f"Uptime: {status['uptime_percentage']:.1f}%"
                if status['is_online']:
                     value += f" | Avg Resp: {status['average_response_time_ms']:.0f}ms"
                embed.add_field(name=f"{status_emoji} **{status['name']}**", value=value, inline=True)
            
            await interaction.followup.send(embed=embed)
        except Exception as e:
            self.logger.error(f"❌ Fehler in /alle-projekte: {e}", exc_info=True)
            await interaction.followup.send("❌ Fehler beim Abrufen der Projekt-Übersicht", ephemeral=True)


    @app_commands.command(name="agent-stats", description="🧠 Zeige Agent-Learning Statistiken")
    async def agent_stats_command(self, interaction: discord.Interaction):
        """Zeigt Learning-Pipeline Stats aller Agents."""
        await interaction.response.defer(ephemeral=False)
        try:
            embed = discord.Embed(
                title="🧠 Agent Learning — Dashboard",
                description="Übersicht über alle lernenden AI-Agents",
                color=0x9B59B6,
                timestamp=datetime.now(),
            )

            # ── Security ScanAgent (oder Legacy Analyst) ──
            scan_agent = getattr(self.bot, 'security_analyst', None)
            pool = None
            if scan_agent and hasattr(scan_agent, 'db') and scan_agent.db and scan_agent.db.pool:
                pool = scan_agent.db.pool
            if pool:
                try:
                    # Sessions + Tokens (direkte Query statt _get_30day_stats)
                    session_stats = await pool.fetchrow("""
                        SELECT COUNT(*) as cnt, COALESCE(SUM(tokens_used),0) as tokens,
                               COALESCE(SUM(auto_fixes_count),0) as fixes
                        FROM sessions WHERE started_at >= NOW() - INTERVAL '30 days'
                    """)
                    findings_open = await pool.fetchval("SELECT COUNT(*) FROM findings WHERE status='open'")
                    findings_fixed = await pool.fetchval(
                        "SELECT COUNT(*) FROM findings WHERE status='fixed' AND found_at >= NOW()-INTERVAL '30 days'")
                    tokens_display = f"{session_stats['tokens']:,}" if session_stats['tokens'] else "0"

                    # Fix-Versuche
                    fix_stats = await pool.fetchrow("""
                        SELECT COUNT(*) as total,
                               COUNT(*) FILTER (WHERE result='success') as success
                        FROM fix_attempts WHERE created_at >= NOW() - INTERVAL '30 days'
                    """)

                    # Coverage
                    coverage_count = await pool.fetchval(
                        "SELECT COUNT(DISTINCT area) FROM scan_coverage WHERE was_checked=TRUE"
                    ) or 0

                    analyst_text = (
                        f"**Sessions (30d):** {session_stats['cnt']}\n"
                        f"**Tokens (30d):** {tokens_display}\n"
                        f"**Findings:** {findings_open} offen / {findings_fixed} gefixt\n"
                        f"**Fix-Versuche:** {fix_stats['total']} ({fix_stats['success']}× ✅)\n"
                        f"**Scan-Abdeckung:** {coverage_count}/10 Bereiche"
                    )
                    embed.add_field(name="🔒 Security ScanAgent", value=analyst_text, inline=False)
                except Exception as e:
                    embed.add_field(name="🔒 Security ScanAgent", value=f"Fehler: {e}", inline=False)

            # ── Patch Notes ──
            try:
                from integrations.patch_notes_learning import PatchNotesLearning
                pn = PatchNotesLearning()
                await pn.connect()

                # Generierungen, Feedback, Beispiele kombiniert
                counts = await pn.pool.fetchrow("""
                    SELECT
                        (SELECT COUNT(*) FROM pn_generations) as gen_count,
                        (SELECT COUNT(*) FROM agent_feedback WHERE agent='patch_notes') as fb_count,
                        (SELECT COUNT(*) FROM pn_examples WHERE is_active=TRUE) as examples
                """)
                gen_count = counts['gen_count']
                fb_count = counts['fb_count']
                examples = counts['examples']

                # Varianten
                variants = await pn.get_variant_stats()
                variant_text = ""
                if variants:
                    top = variants[0]
                    variant_text = f"\n**Beste Variante:** `{top['variant_id']}` ({top['combined_weight']:.0f} Score)"

                await pn.close()

                pn_text = (
                    f"**Generierungen:** {gen_count}\n"
                    f"**Feedbacks:** {fb_count}\n"
                    f"**Beispiele:** {examples}"
                    f"{variant_text}"
                )
                embed.add_field(name="📝 Patch Notes", value=pn_text, inline=True)
            except Exception:
                embed.add_field(name="📝 Patch Notes", value="DB nicht verfügbar", inline=True)

            # ── SEO Agent ──
            try:
                from integrations.patch_notes_learning import PatchNotesLearning
                pn2 = PatchNotesLearning()
                await pn2.connect()
                seo_counts = await pn2.pool.fetchrow("""
                    SELECT
                        (SELECT COUNT(*) FROM seo_fix_impact) as impact_count,
                        (SELECT COUNT(*) FROM agent_knowledge) as cross_knowledge
                """)
                impact_count = seo_counts['impact_count']
                cross_knowledge = seo_counts['cross_knowledge']
                await pn2.close()

                seo_text = (
                    f"**Fix-Impacts gemessen:** {impact_count}\n"
                    f"**Cross-Agent Knowledge:** {cross_knowledge} Einträge"
                )
                embed.add_field(name="🔍 SEO Agent", value=seo_text, inline=True)
            except Exception:
                embed.add_field(name="🔍 SEO Agent", value="DB nicht verfügbar", inline=True)

            embed.set_footer(text="agent_learning DB | /agent-stats")
            await interaction.followup.send(embed=embed)

        except Exception as e:
            self.logger.error(f"❌ Fehler in /agent-stats: {e}", exc_info=True)
            await interaction.followup.send("❌ Fehler beim Abrufen der Agent-Stats", ephemeral=True)


    @app_commands.command(name='security-engine', description='Security Engine v6 Status + Stats')
    async def security_engine_status(self, interaction: discord.Interaction):
        """Zeigt Security Engine v6 Status und Statistiken"""
        engine = getattr(self.bot, 'security_engine', None)
        if not engine:
            await interaction.response.send_message("⚠️ Security Engine v6 nicht aktiv", ephemeral=True)
            return

        stats = engine.get_stats()

        embed = discord.Embed(
            title="🛡️ Security Engine v6",
            color=0x2ECC71 if not stats['circuit_breaker']['is_open'] else 0xE74C3C
        )

        # Engine Status
        cb = stats['circuit_breaker']
        status = "🟢 Aktiv" if not cb['is_open'] else f"🔴 Circuit Breaker offen ({', '.join(cb['open_keys'])})"
        embed.add_field(name="Status", value=status, inline=False)

        # Stats
        embed.add_field(
            name="📊 Events",
            value=f"Verarbeitet: {stats['events_processed']}\nÜbersprungen: {stats['events_skipped']}",
            inline=True
        )

        # Registrierte Fixer
        fixers = stats.get('registered_fixers', {})
        fixer_text = "\n".join([f"`{k}`: {', '.join(v)}" for k, v in fixers.items()]) or "Keine"
        embed.add_field(name="🔧 Fixer", value=fixer_text[:1024], inline=False)

        await interaction.response.send_message(embed=embed)


async def setup(bot):
    await bot.add_cog(InspectorCog(bot))
