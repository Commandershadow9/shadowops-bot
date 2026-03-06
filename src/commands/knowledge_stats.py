"""
Knowledge Stats Commands - Discord Slash Commands für Knowledge Base Statistiken

Bietet Commands um gelernte Patterns und Empfehlungen anzuzeigen:
- /knowledge-stats: Gesamtübersicht aller gelernten Patterns
- /knowledge-stats project:<name>: Stats für spezifisches Projekt
- /knowledge-stats model:<name>: RAM Stats für spezifisches Modell
"""

import logging
from discord import app_commands
from discord.ext import commands
import discord
from typing import Optional

logger = logging.getLogger('shadowops')


class KnowledgeStatsCommands(commands.Cog):
    """Commands für Knowledge Base Statistiken und Empfehlungen."""

    def __init__(self, bot: commands.Bot, knowledge_synthesizer, config):
        """
        Initialize knowledge stats commands.

        Args:
            bot: Discord bot instance
            knowledge_synthesizer: KnowledgeSynthesizer instance
            config: Bot configuration
        """
        self.bot = bot
        self.knowledge_synthesizer = knowledge_synthesizer
        self.config = config

        # Admin role/user restrictions (optional - könnte auch public sein)
        permissions = getattr(config, 'permissions', {})
        self.admin_user_ids = permissions.get('admins', [])
        self.admin_role_ids = permissions.get('admin_roles', [])

        logger.info("✅ Knowledge Stats Commands loaded")

    def _is_admin(self, interaction: discord.Interaction) -> bool:
        """Check if user has admin permissions."""
        # Check if user ID is in admin list
        if interaction.user.id in self.admin_user_ids:
            return True

        # Check if user has admin role
        if hasattr(interaction.user, 'roles'):
            for role in interaction.user.roles:
                if role.id in self.admin_role_ids:
                    return True

        return False

    @app_commands.command(name="knowledge-stats", description="📊 Zeige Knowledge Base Statistiken")
    @app_commands.describe(
        project="Optional: Projektname für detaillierte Fix-Stats",
        model="Optional: Modellname für detaillierte RAM-Stats"
    )
    async def knowledge_stats(
        self,
        interaction: discord.Interaction,
        project: Optional[str] = None,
        model: Optional[str] = None
    ):
        """Zeige Knowledge Base Statistiken"""

        # Knowledge Stats sind für alle sichtbar (read-only, harmlos)
        # Keine Admin-Prüfung nötig
        await interaction.response.defer(thinking=True)

        try:
            kb = self.knowledge_synthesizer.knowledge

            # Spezifische Projekt-Stats
            if project:
                embed = await self._create_project_stats_embed(project)
                await interaction.followup.send(embed=embed)
                return

            # Spezifische Modell-Stats
            if model:
                embed = await self._create_model_stats_embed(model)
                await interaction.followup.send(embed=embed)
                return

            # Gesamtübersicht
            embed = await self._create_summary_embed()
            await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.error(f"Error in knowledge-stats command: {e}", exc_info=True)
            await interaction.followup.send(
                f"❌ Fehler beim Abrufen der Knowledge Stats: {e}",
                ephemeral=True
            )

    async def _create_summary_embed(self) -> discord.Embed:
        """Erstelle Gesamtübersicht Embed"""
        kb = self.knowledge_synthesizer.knowledge

        embed = discord.Embed(
            title="🧠 Knowledge Base Statistics",
            description="Übersicht aller gelernten Patterns und Empfehlungen",
            color=0x9B59B6,  # Purple
        )

        # Auto-Fix Patterns Section
        fix_patterns = kb.get("fix_patterns", {})
        if fix_patterns:
            # Calculate average success rate
            success_rates = [p["success_rate"] for p in fix_patterns.values()]
            avg_success_rate = sum(success_rates) / len(success_rates) if success_rates else 0

            # Total samples
            total_samples = sum(p["sample_size"] for p in fix_patterns.values())

            # Top projects by success rate
            sorted_projects = sorted(
                fix_patterns.items(),
                key=lambda x: x[1]["success_rate"],
                reverse=True
            )[:5]

            projects_text = ""
            for project_name, pattern in sorted_projects:
                emoji = "🟢" if pattern["success_rate"] >= 0.8 else "🟡" if pattern["success_rate"] >= 0.5 else "🔴"
                projects_text += f"{emoji} **{project_name}**: {pattern['success_rate']*100:.1f}% ({pattern['sample_size']} samples)\n"

            embed.add_field(
                name="📊 Auto-Fix Patterns",
                value=f"**Projects tracked:** {len(fix_patterns)}\n"
                      f"**Total fixes analyzed:** {total_samples}\n"
                      f"**Average success rate:** {avg_success_rate*100:.1f}%\n\n"
                      f"**Top Projects:**\n{projects_text or 'Keine Daten'}",
                inline=False
            )
        else:
            embed.add_field(
                name="📊 Auto-Fix Patterns",
                value="Noch keine Auto-Fix Patterns gelernt",
                inline=False
            )

        # RAM Management Patterns Section
        ram_patterns = kb.get("ram_patterns", {})
        if ram_patterns:
            models_text = ""
            for model_name, pattern in list(ram_patterns.items())[:5]:
                avg_ram = pattern.get("avg_ram_total_gb", 0)
                best_method = pattern.get("best_cleanup_method", "unknown")
                models_text += f"• **{model_name}**: ~{avg_ram:.1f}GB, cleanup: `{best_method}`\n"

            embed.add_field(
                name="🧠 RAM Management Patterns",
                value=f"**Models tracked:** {len(ram_patterns)}\n"
                      f"**Total RAM events:** {sum(p['total_failures'] for p in ram_patterns.values())}\n\n"
                      f"**Models:**\n{models_text or 'Keine Daten'}",
                inline=False
            )
        else:
            embed.add_field(
                name="🧠 RAM Management Patterns",
                value="Noch keine RAM Patterns gelernt",
                inline=False
            )

        # Meta-Learning Section
        meta_learning = kb.get("meta_learning", {})
        synthesis_count = kb.get("synthesis_count", 0)
        learning_velocity = meta_learning.get("learning_velocity")

        meta_text = f"**Total syntheses:** {synthesis_count}\n"
        if learning_velocity:
            meta_text += f"**Learning velocity:** {learning_velocity:.2f} patterns/day\n"
            meta_text += "✅ System improving continuously!"
        else:
            meta_text += "⏳ Collecting data for velocity calculation..."

        embed.add_field(
            name="📈 Meta-Learning",
            value=meta_text,
            inline=False
        )

        embed.set_footer(text="Knowledge Synthesizer • Long-term Learning System")
        return embed

    async def _create_project_stats_embed(self, project: str) -> discord.Embed:
        """Erstelle Projekt-spezifische Stats"""
        recommendations = self.knowledge_synthesizer.get_fix_recommendations(project)

        if not recommendations["has_data"]:
            embed = discord.Embed(
                title=f"📊 Knowledge Stats: {project}",
                description="❌ Noch keine Daten für dieses Projekt vorhanden",
                color=0xE74C3C  # Red
            )
            return embed

        # Confidence emoji
        confidence = recommendations["confidence"]
        confidence_emoji = "🟢" if confidence == "high" else "🟡" if confidence == "medium" else "🔴"

        embed = discord.Embed(
            title=f"📊 Knowledge Stats: {project}",
            description=f"Gelernte Empfehlungen basierend auf {recommendations['sample_size']} historischen Fixes",
            color=0x2ECC71 if recommendations['success_rate'] >= 0.8 else 0xF39C12 if recommendations['success_rate'] >= 0.5 else 0xE74C3C
        )

        # Success Rate
        embed.add_field(
            name="✅ Success Rate",
            value=f"**{recommendations['success_rate']*100:.1f}%** ({recommendations['sample_size']} samples)",
            inline=True
        )

        # Confidence
        embed.add_field(
            name=f"{confidence_emoji} Confidence Level",
            value=f"**{confidence.upper()}**",
            inline=True
        )

        # Strategy
        success_rate = recommendations['success_rate']
        if success_rate >= 0.8:
            strategy = "🚀 Aggressive (high success rate)"
        elif success_rate >= 0.5:
            strategy = "⚖️ Standard (moderate success rate)"
        else:
            strategy = "⚠️ Careful (low success rate)"

        embed.add_field(
            name="🎯 Recommended Strategy",
            value=strategy,
            inline=False
        )

        # Best Practices
        best_practices = recommendations.get("best_practices", [])
        if best_practices:
            practices_text = "\n".join(f"• {practice}" for practice in best_practices[:10])
            embed.add_field(
                name="💡 Best Practices (from successful fixes)",
                value=practices_text,
                inline=False
            )
        else:
            embed.add_field(
                name="💡 Best Practices",
                value="Noch keine spezifischen Patterns identifiziert",
                inline=False
            )

        embed.set_footer(text=f"Knowledge Synthesizer • Project: {project}")
        return embed

    async def _create_model_stats_embed(self, model: str) -> discord.Embed:
        """Erstelle Modell-spezifische RAM Stats"""
        recommendations = self.knowledge_synthesizer.get_ram_recommendations(model)

        if not recommendations["has_data"]:
            embed = discord.Embed(
                title=f"🧠 RAM Stats: {model}",
                description="❌ Noch keine RAM-Daten für dieses Modell vorhanden",
                color=0xE74C3C  # Red
            )
            return embed

        embed = discord.Embed(
            title=f"🧠 RAM Stats: {model}",
            description=f"Gelernte RAM-Anforderungen basierend auf {recommendations['total_failures_tracked']} Events",
            color=0x3498DB  # Blue
        )

        # Average RAM Required
        avg_ram = recommendations.get("avg_ram_required_gb")
        if avg_ram:
            embed.add_field(
                name="💾 Average RAM Required",
                value=f"**~{avg_ram:.1f} GB**",
                inline=True
            )

        # Best Cleanup Method
        best_method = recommendations.get("best_cleanup_method")
        if best_method:
            method_emoji = "🔧" if best_method == "restart_service" else "🔄"
            embed.add_field(
                name=f"{method_emoji} Best Cleanup Method",
                value=f"`{best_method}`",
                inline=True
            )

        # Cleanup Success Rate
        cleanup_rate = recommendations.get("cleanup_success_rate", 0)
        embed.add_field(
            name="✅ Cleanup Success Rate",
            value=f"**{cleanup_rate*100:.1f}%**",
            inline=True
        )

        # Events Tracked
        embed.add_field(
            name="📊 Total Events Tracked",
            value=f"**{recommendations['total_failures_tracked']}** RAM failures",
            inline=False
        )

        # Recommendation
        if avg_ram:
            embed.add_field(
                name="💡 Recommendation",
                value=f"Ensure at least **{avg_ram:.1f}GB** RAM is available before running this model.\n"
                      f"Use `{best_method}` for cleanup if needed.",
                inline=False
            )

        embed.set_footer(text=f"Knowledge Synthesizer • Model: {model}")
        return embed


async def setup(bot: commands.Bot, knowledge_synthesizer, config):
    """Setup function for loading the cog."""
    await bot.add_cog(KnowledgeStatsCommands(bot, knowledge_synthesizer, config))
