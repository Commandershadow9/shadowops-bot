"""
Admin commands for AI Learning System management.
"""

import discord
from discord import app_commands
from discord.ext import commands
import logging
from pathlib import Path

logger = logging.getLogger('shadowops')


class AILearningAdmin(commands.Cog):
    """Admin commands for managing AI Learning System."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="ai-stats", description="Show AI Learning System statistics")
    @app_commands.checks.has_permissions(administrator=True)
    async def ai_stats(self, interaction: discord.Interaction):
        """Display comprehensive AI learning statistics."""
        await interaction.response.defer()

        try:
            embed = discord.Embed(
                title="🧠 AI Learning System Statistics",
                color=discord.Color.blue(),
                timestamp=discord.utils.utcnow()
            )

            # Training Statistics
            if hasattr(self.bot, 'patch_notes_trainer') and self.bot.patch_notes_trainer:
                stats = self.bot.patch_notes_trainer.get_statistics()

                training_info = f"""
**📊 Training Data:**
• Total Examples: {stats['total_examples']}
• High-Quality Examples (≥80): {stats['good_examples']}
• Average Quality Score: {stats['avg_quality_score']:.1f}/100
• Projects: {', '.join(stats['projects']) if stats['projects'] else 'None'}
"""
                embed.add_field(name="", value=training_info, inline=False)

            # A/B Testing Statistics
            if hasattr(self.bot, 'prompt_ab_testing') and self.bot.prompt_ab_testing:
                ab_stats = self.bot.prompt_ab_testing.get_variant_statistics()
                active_variants = [v for v in self.bot.prompt_ab_testing.variants.values() if v.active]

                ab_info = f"""
**🧪 A/B Testing:**
• Active Variants: {len(active_variants)}
• Total Tests Run: {sum(s['count'] for s in ab_stats.values())}
"""

                # Top performing variant
                if ab_stats:
                    best_id = max(ab_stats.keys(), key=lambda k: ab_stats[k].get('avg_total_score', 0))
                    best_stats = ab_stats[best_id]
                    best_variant = self.bot.prompt_ab_testing.variants.get(best_id)

                    if best_variant:
                        ab_info += f"\n• **Best Variant**: {best_variant.name}\n"
                        ab_info += f"  - Score: {best_stats.get('avg_total_score', 0):.1f}/100\n"
                        ab_info += f"  - Tests: {best_stats['count']}"

                embed.add_field(name="", value=ab_info, inline=False)

            # Feedback Statistics
            if hasattr(self.bot, 'feedback_collector') and self.bot.feedback_collector:
                tracked_count = len(self.bot.feedback_collector.tracked_messages)

                feedback_info = f"""
**👍 Feedback Collection:**
• Currently Tracking: {tracked_count} messages
• Reaction Types: 👍 👎 ❤️ 🔥
"""
                embed.add_field(name="", value=feedback_info, inline=False)

            embed.set_footer(text="Use /ai-tune to optimize prompts")

            await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.error(f"Failed to show AI stats: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @app_commands.command(name="ai-variants", description="List all prompt variants")
    @app_commands.checks.has_permissions(administrator=True)
    async def ai_variants(self, interaction: discord.Interaction):
        """List all A/B test prompt variants."""
        await interaction.response.defer()

        try:
            if not hasattr(self.bot, 'prompt_ab_testing') or not self.bot.prompt_ab_testing:
                await interaction.followup.send("❌ A/B Testing system not available", ephemeral=True)
                return

            ab_testing = self.bot.prompt_ab_testing
            stats = ab_testing.get_variant_statistics()

            embed = discord.Embed(
                title="🧪 Prompt Variants",
                description="A/B Testing variants and their performance",
                color=discord.Color.purple()
            )

            for variant in ab_testing.variants.values():
                variant_stats = stats.get(variant.id, {})

                status = "✅ Active" if variant.active else "❌ Inactive"
                tests_run = variant_stats.get('count', 0)
                avg_score = variant_stats.get('avg_total_score', 0)

                field_value = f"""
**Status:** {status}
**Description:** {variant.description}
**Tests Run:** {tests_run}
**Avg Score:** {avg_score:.1f}/100
**ID:** `{variant.id}`
"""
                embed.add_field(
                    name=f"📋 {variant.name}",
                    value=field_value,
                    inline=False
                )

            await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.error(f"Failed to list variants: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @app_commands.command(name="ai-tune", description="Trigger auto-tuning of prompts")
    @app_commands.checks.has_permissions(administrator=True)
    async def ai_tune(self, interaction: discord.Interaction, project: str = None):
        """Manually trigger prompt auto-tuning."""
        await interaction.response.defer()

        try:
            if not hasattr(self.bot, 'prompt_auto_tuner') or not self.bot.prompt_auto_tuner:
                await interaction.followup.send("❌ Auto-Tuner not available", ephemeral=True)
                return

            # Get insights first
            insights = self.bot.prompt_auto_tuner.suggest_prompt_improvements(project)

            if not insights:
                await interaction.followup.send(
                    "📊 No improvements suggested at this time. Need more training data.",
                    ephemeral=True
                )
                return

            # Create embed with suggestions
            embed = discord.Embed(
                title="🎯 Prompt Improvement Suggestions",
                description=f"Based on analysis of {project or 'all projects'}",
                color=discord.Color.gold()
            )

            for i, suggestion in enumerate(insights[:5], 1):
                embed.add_field(
                    name=f"{i}. {suggestion['type'].title()}",
                    value=f"**Suggestion:** {suggestion['suggestion']}\n**Rationale:** {suggestion['rationale']}",
                    inline=False
                )

            # Add button to apply tuning
            embed.set_footer(text="Auto-tuning will create improved variant automatically")

            await interaction.followup.send(embed=embed)

            logger.info(f"AI tuning suggestions shown to {interaction.user.name}")

        except Exception as e:
            logger.error(f"Failed to show tuning suggestions: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @app_commands.command(name="ai-export-finetune", description="Export data for fine-tuning")
    @app_commands.checks.has_permissions(administrator=True)
    async def ai_export_finetune(self, interaction: discord.Interaction,
                                  project: str = None,
                                  min_score: int = 80):
        """Export training data for fine-tuning llama3.1."""
        await interaction.response.defer()

        try:
            if not hasattr(self.bot, 'llm_fine_tuning') or not self.bot.llm_fine_tuning:
                await interaction.followup.send("❌ Fine-Tuning system not available", ephemeral=True)
                return

            await interaction.followup.send(f"🚀 Exporting fine-tuning data (min score: {min_score})...")

            # Export
            result = self.bot.llm_fine_tuning.export_and_prepare_fine_tuning(
                project=project,
                min_quality_score=float(min_score)
            )

            embed = discord.Embed(
                title="✅ Fine-Tuning Export Complete",
                description="Training data and scripts have been prepared",
                color=discord.Color.green()
            )

            export_info = f"""
**📁 Exported Files:**

• **Ollama Format**: `{result['ollama_data'].name}`
• **LoRA Format**: `{result['lora_data'].name}`
• **Fine-Tuning Script**: `{result['script'].name}`
• **README**: `{result['readme'].name}`

**📂 Location**: `{result['ollama_data'].parent}`

**🚀 Quick Start:**
```bash
cd {result['ollama_data'].parent}
./{result['script'].name}
```

See README_FINE_TUNING.md for detailed instructions.
"""

            embed.add_field(name="", value=export_info, inline=False)

            await interaction.followup.send(embed=embed)

            logger.info(f"Fine-tuning data exported by {interaction.user.name}")

        except Exception as e:
            logger.error(f"Failed to export fine-tuning data: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)


async def setup(bot: commands.Bot):
    """Setup function for loading the cog."""
    await bot.add_cog(AILearningAdmin(bot))
