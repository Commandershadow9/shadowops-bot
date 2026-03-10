"""
DiscordUIMixin — Discord-Interaktion, Status-Messages und Approval-Flow
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import SecurityEventBatch, RemediationPlan

logger = logging.getLogger('shadowops')


class DiscordUIMixin:
    """Mixin für Discord UI-Interaktionen: Status-Updates und Approval-Flow"""

    def _get_status_channel(self):
        """Holt den Status-Channel für Live-Updates"""
        if not self.bot:
            return None
        if not self.config:
            logger.warning("⚠️ Keine Config verfügbar - Status-Channel kann nicht geladen werden")
            return None
        # Verwende den Approval-Channel für Live-Updates
        try:
            channel = self.bot.get_channel(self.config.approvals_channel)
            return channel
        except Exception as e:
            logger.error(f"Fehler beim Holen des Status-Channels: {e}")
        return None

    async def _send_batch_status(self, batch: SecurityEventBatch, status_text: str, color: int = 0xFFAA00):
        """Sendet oder updated Status-Message für einen Batch"""
        import discord

        channel = self._get_status_channel()
        if not channel:
            logger.warning("⚠️ Status-Channel nicht verfügbar - überspringe Discord-Update")
            return

        try:
            embed = discord.Embed(
                title="🔄 Koordinierte Remediation läuft",
                description=status_text,
                color=color,
                timestamp=datetime.now()
            )
            embed.set_footer(text=f"Batch ID: {batch.batch_id}")

            if batch.status_message_id:
                # Update existing message
                try:
                    message = await channel.fetch_message(batch.status_message_id)
                    await message.edit(embed=embed)
                    logger.debug(f"📝 Discord-Status updated (Message ID: {batch.status_message_id})")
                except:
                    # Message not found, send new one
                    message = await channel.send(embed=embed)
                    batch.status_message_id = message.id
                    batch.status_channel_id = channel.id
                    logger.info(f"📤 Neue Discord-Status-Message gesendet (ID: {message.id})")
            else:
                # Send new message
                message = await channel.send(embed=embed)
                batch.status_message_id = message.id
                batch.status_channel_id = channel.id
                logger.info(f"📤 Neue Discord-Status-Message gesendet (ID: {message.id})")

        except Exception as e:
            logger.error(f"Fehler beim Senden der Status-Message: {e}")

    async def _request_approval(self, batch: SecurityEventBatch, plan: RemediationPlan) -> bool:
        """
        Fordert User-Approval für den gesamten koordinierten Plan an

        Zeigt ein schönes Discord Embed mit:
        - Zusammenfassung aller Events
        - Alle Phasen des Plans
        - Geschätzte Dauer
        - Risiko-Level
        - Approve/Reject Buttons
        """
        import discord

        logger.info(f"👤 Fordere Approval an für Batch {batch.batch_id}")

        # Build Discord Embed
        embed = discord.Embed(
            title="🎯 Koordinierter Remediation-Plan",
            description=f"**{plan.description}**\n\nDieser Plan behandelt **{len(batch.events)} Security-Events** koordiniert und sequentiell.",
            color=discord.Color.gold(),
            timestamp=datetime.now()
        )

        # Events Summary
        sources_summary = {}
        for event in batch.events:
            source = event.source
            if source not in sources_summary:
                sources_summary[source] = {'count': 0, 'severity': event.severity}
            sources_summary[source]['count'] += 1

        events_text = "\n".join([
            f"**{source.upper()}:** {info['count']} Event(s) ({info['severity']})"
            for source, info in sources_summary.items()
        ])

        embed.add_field(
            name="📦 Events im Batch",
            value=events_text,
            inline=False
        )

        # Execution Plan (Phasen) - Discord Field limit: 1024 characters
        phases_text = ""
        total_minutes = 0
        max_desc_length = 120  # Max chars per phase description

        for i, phase in enumerate(plan.phases[:5], 1):  # Max 5 Phasen anzeigen
            name = phase.get('name', f'Phase {i}')
            desc = phase.get('description', 'N/A')
            minutes = phase.get('estimated_minutes', 5)
            total_minutes += minutes

            # Truncate description if too long
            if len(desc) > max_desc_length:
                desc = desc[:max_desc_length] + "..."

            phase_text = f"**{i}. {name}** (~{minutes}min)\n{desc}\n\n"

            # Check if adding this phase would exceed Discord's 1024 char limit
            if len(phases_text) + len(phase_text) > 1020:  # Leave some margin
                phases_text += f"_...und {len(plan.phases) - (i-1)} weitere Phasen_\n"
                break

            phases_text += phase_text

        if len(plan.phases) > 5 and len(phases_text) < 1020:
            phases_text += f"_...und {len(plan.phases) - 5} weitere Phasen_\n"

        # Ensure we never exceed 1024 characters (Discord limit)
        if len(phases_text) > 1024:
            phases_text = phases_text[:1020] + "..."

        embed.add_field(
            name="⚙️ Ausführungs-Plan",
            value=phases_text or "Keine Phasen definiert",
            inline=False
        )

        # Metadata
        confidence_color = "🟢" if plan.confidence >= 0.8 else "🟡" if plan.confidence >= 0.6 else "🔴"

        embed.add_field(
            name="📊 Plan-Details",
            value=f"**Confidence:** {confidence_color} {plan.confidence:.0%}\n"
                  f"**Geschätzte Dauer:** ⏱️ ~{total_minutes} Minuten\n"
                  f"**Neustart erforderlich:** {'✅ Ja' if plan.requires_restart else '❌ Nein'}\n"
                  f"**KI-Modell:** {plan.ai_model}",
            inline=False
        )

        # Rollback Info
        if plan.rollback_plan:
            embed.add_field(
                name="🔄 Rollback-Strategie",
                value=plan.rollback_plan[:200] + ("..." if len(plan.rollback_plan) > 200 else ""),
                inline=False
            )

        embed.set_footer(text=f"Batch ID: {batch.batch_id} | Orchestrator v1.0")

        # Send to approval channel with buttons
        try:
            if not self.bot:
                logger.warning("⚠️ Kein Bot verfügbar für Approval - Auto-Approve")
                return True

            if not self.config:
                logger.error("❌ Keine Config vorhanden - Approval-Channel kann nicht bestimmt werden")
                return False

            # Get approval channel from config (includes fallback logic)
            channel = self.bot.get_channel(self.config.approvals_channel)

            if not channel:
                logger.error(f"❌ Approval Channel {self.config.approvals_channel} nicht gefunden")
                return False

            # Create approval buttons
            import discord

            class ApprovalView(discord.ui.View):
                def __init__(self, orchestrator, batch_id):
                    super().__init__(timeout=1800)  # 30 minutes
                    self.orchestrator = orchestrator
                    self.batch_id = batch_id
                    self.approved = None

                @discord.ui.button(label="✅ Approve & Execute", style=discord.ButtonStyle.green, custom_id="approve")
                async def approve_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                    await interaction.response.send_message(
                        f"✅ **Plan approved!** Starte koordinierte Remediation...",
                        ephemeral=True
                    )
                    self.approved = True
                    self.stop()

                @discord.ui.button(label="❌ Reject", style=discord.ButtonStyle.red, custom_id="reject")
                async def reject_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                    await interaction.response.send_message(
                        f"❌ **Plan abgelehnt.** Remediation wird nicht ausgeführt.",
                        ephemeral=True
                    )
                    self.approved = False
                    self.stop()

                @discord.ui.button(label="📋 Details anzeigen", style=discord.ButtonStyle.gray, custom_id="details")
                async def details_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                    # Build detailed view
                    details_text = f"**Batch {self.batch_id} - Detaillierte Phasen:**\n\n"

                    # Get plan from orchestrator
                    # For now, just acknowledge
                    await interaction.response.send_message(
                        f"📋 Detaillierte Phasen-Informationen für Batch `{self.batch_id}`\n\n"
                        f"Siehe Embed oben für vollständige Details.",
                        ephemeral=True
                    )

            # Create view instance
            view = ApprovalView(self, batch.batch_id)

            # Send message with embed and buttons
            approval_message = await channel.send(embed=embed, view=view)
            logger.info(f"📬 Approval-Request gesendet an Channel {channel.name}")

            # Wait for user interaction
            logger.info(f"⏳ Warte auf User-Approval (Timeout: 30min)...")
            await view.wait()

            # Update message to show result
            if view.approved is True:
                # Update embed color to green
                embed.color = discord.Color.green()
                embed.title = "✅ Plan Approved - Wird ausgeführt"
                await approval_message.edit(embed=embed, view=None)
                logger.info(f"✅ Batch {batch.batch_id} wurde approved")
                return True

            elif view.approved is False:
                # Update embed color to red
                embed.color = discord.Color.red()
                embed.title = "❌ Plan Rejected"
                await approval_message.edit(embed=embed, view=None)
                logger.warning(f"❌ Batch {batch.batch_id} wurde rejected")
                return False

            else:
                # Timeout — wird zu GitHub Issue eskaliert
                embed.color = discord.Color.dark_gray()
                embed.title = "⏰ Approval Timeout — wird zu GitHub Issue eskaliert"
                await approval_message.edit(embed=embed, view=None)
                logger.warning(f"⏰ Batch {batch.batch_id} - Approval Timeout, eskaliere zu GitHub Issue")
                return None  # None = Timeout (wird eskaliert), False = Rejected

        except Exception as e:
            logger.error(f"❌ Fehler bei Approval-Request: {e}", exc_info=True)
            return False
