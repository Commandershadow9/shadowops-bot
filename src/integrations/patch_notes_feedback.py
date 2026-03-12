"""
Patch Notes Feedback Collection System.

Collects user feedback through:
- Discord reactions (emoji scoring)
- Text feedback via Discord Modal
"""

import asyncio
import logging
import discord
from discord import ui, Message, Reaction, User
from typing import Dict, Optional
from datetime import datetime, timedelta, timezone

logger = logging.getLogger('shadowops')


class TextFeedbackModal(ui.Modal, title="📝 Patch Notes Feedback"):
    """Discord Modal für Text-Feedback zu Patch Notes."""

    feedback_text = ui.TextInput(
        label="Was denkst du über diese Patch Notes?",
        style=discord.TextStyle.paragraph,
        placeholder="z.B. 'Mehr Details zu Feature X wären gut' oder 'Perfekte Länge!'",
        required=True,
        min_length=5,
        max_length=500,
    )

    rating = ui.TextInput(
        label="Bewertung (1-5 Sterne)",
        style=discord.TextStyle.short,
        placeholder="1-5",
        required=False,
        max_length=1,
    )

    def __init__(self, collector: 'PatchNotesFeedbackCollector',
                 project: str, version: str):
        super().__init__()
        self.collector = collector
        self.project = project
        self.version = version

    async def on_submit(self, interaction: discord.Interaction):
        """Verarbeite das Text-Feedback."""
        feedback_text = self.feedback_text.value.strip()
        rating_value = None

        if self.rating.value:
            try:
                rating_value = int(self.rating.value)
                if rating_value < 1 or rating_value > 5:
                    rating_value = None
            except ValueError:
                rating_value = None

        # Feedback speichern
        feedback_data = {
            'text': feedback_text,
            'rating': rating_value,
            'user_id': interaction.user.id,
            'user_name': str(interaction.user),
        }

        if self.collector.trainer:
            self.collector.trainer.record_feedback(
                version=self.version,
                project=self.project,
                feedback_type='text',
                feedback_data=feedback_data
            )

        # Score-Delta berechnen (für A/B Testing)
        score_delta = 0
        if rating_value:
            # 1=schlecht, 5=super → Mapping zu Score
            score_map = {1: -15, 2: -5, 3: 0, 4: 10, 5: 20}
            score_delta = score_map.get(rating_value, 0)

        logger.info(
            f"📝 Text-Feedback für {self.project} v{self.version}: "
            f"Rating={rating_value}, Text='{feedback_text[:50]}...'"
        )

        await interaction.response.send_message(
            "✅ **Danke für dein Feedback!** Wir nutzen es um die Patch Notes zu verbessern.",
            ephemeral=True
        )


class PatchNotesFeedbackView(ui.View):
    """Discord View mit Feedback-Button unter Patch Notes."""

    def __init__(self, collector: 'PatchNotesFeedbackCollector',
                 project: str, version: str):
        super().__init__(timeout=604800)  # 7 Tage
        self.collector = collector
        self.project = project
        self.version = version

    @ui.button(label="📝 Feedback geben", style=discord.ButtonStyle.secondary)
    async def feedback_button(self, interaction: discord.Interaction, button: ui.Button):
        """Öffne das Feedback-Modal."""
        modal = TextFeedbackModal(self.collector, self.project, self.version)
        await interaction.response.send_modal(modal)


class PatchNotesFeedbackCollector:
    """
    Collects user feedback on patch notes through Discord reactions and text.
    """

    def __init__(self, bot: discord.Client, patch_notes_trainer=None):
        self.bot = bot
        self.trainer = patch_notes_trainer

        # Track messages we're collecting feedback on
        # {message_id: {'project': str, 'version': str, 'timestamp': datetime}}
        self.tracked_messages: Dict[int, Dict] = {}

        # Reaction -> Score mapping
        self.reaction_scores = {
            '👍': 10,      # Good
            '❤️': 15,      # Love it
            '🔥': 20,      # Amazing
            '👎': -10,     # Bad
            '😐': -5,      # Meh
            '❌': -15,     # Terrible
        }

        # Register event handlers
        self.bot.event(self.on_raw_reaction_add)
        self.bot.event(self.on_raw_reaction_remove)

        trainer_status = "mit Trainer" if patch_notes_trainer else "standalone, ohne Trainer"
        logger.info(f"✅ Patch Notes Feedback Collector initialized ({trainer_status})")

    async def track_patch_notes_message(self, message: Message, project: str,
                                         version: str, add_feedback_button: bool = True) -> None:
        """
        Start tracking reactions on a patch notes message.

        Args:
            message: Discord message containing patch notes
            project: Project name
            version: Version number
            add_feedback_button: Whether to add the text feedback button
        """
        self.tracked_messages[message.id] = {
            'project': project,
            'version': version,
            'timestamp': datetime.now(timezone.utc),
            'channel_id': message.channel.id,
        }

        # Add reaction buttons
        try:
            for emoji in ['👍', '👎', '❤️', '🔥']:
                await message.add_reaction(emoji)

            logger.info(f"📊 Tracking feedback for {project} v{version} (message {message.id})")
        except Exception as e:
            logger.error(f"Failed to add reaction buttons: {e}")

        # Send feedback button as follow-up message
        if add_feedback_button:
            try:
                view = PatchNotesFeedbackView(self, project, version)
                await message.channel.send(
                    "💬 *Wie findest du diese Patch Notes?*",
                    view=view,
                    reference=message,
                    mention_author=False,
                )
            except Exception as e:
                logger.warning(f"Could not add feedback button: {e}")

    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        """Handle reaction added to tracked message."""
        # Ignore bot's own reactions
        if payload.user_id == self.bot.user.id:
            return

        # Check if this is a tracked message
        if payload.message_id not in self.tracked_messages:
            return

        message_data = self.tracked_messages[payload.message_id]
        emoji = str(payload.emoji)

        # Check if this is a feedback emoji
        if emoji not in self.reaction_scores:
            return

        score_delta = self.reaction_scores[emoji]

        # Record feedback
        feedback_data = {
            'emoji': emoji,
            'score_delta': score_delta,
            'user_id': payload.user_id,
            'added': True,
        }

        if self.trainer:
            self.trainer.record_feedback(
                version=message_data['version'],
                project=message_data['project'],
                feedback_type='reaction',
                feedback_data=feedback_data
            )

        logger.info(f"👍 Feedback recorded: {emoji} on {message_data['project']} v{message_data['version']} (score delta: {score_delta:+d})")

    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        """Handle reaction removed from tracked message."""
        # Ignore bot's own reactions
        if payload.user_id == self.bot.user.id:
            return

        # Check if this is a tracked message
        if payload.message_id not in self.tracked_messages:
            return

        message_data = self.tracked_messages[payload.message_id]
        emoji = str(payload.emoji)

        # Check if this is a feedback emoji
        if emoji not in self.reaction_scores:
            return

        score_delta = -self.reaction_scores[emoji]  # Reverse score

        # Record feedback removal
        feedback_data = {
            'emoji': emoji,
            'score_delta': score_delta,
            'user_id': payload.user_id,
            'added': False,
        }

        if self.trainer:
            self.trainer.record_feedback(
                version=message_data['version'],
                project=message_data['project'],
                feedback_type='reaction',
                feedback_data=feedback_data
            )

        logger.debug(f"Feedback removed: {emoji} on {message_data['project']} v{message_data['version']}")

    def cleanup_old_tracked_messages(self, max_age_hours: int = 168) -> None:
        """
        Remove tracking for old messages (default: 7 days).

        Args:
            max_age_hours: Maximum age in hours before cleanup
        """
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)

        to_remove = []
        for msg_id, data in self.tracked_messages.items():
            if data['timestamp'] < cutoff:
                to_remove.append(msg_id)

        for msg_id in to_remove:
            del self.tracked_messages[msg_id]

        if to_remove:
            logger.info(f"🧹 Cleaned up {len(to_remove)} old tracked messages")

    def get_aggregated_feedback(self, project: str, version: str) -> Dict:
        """
        Get aggregated feedback for a specific version.

        Returns:
            Dict with total_score, reaction_counts, user_count, text_feedbacks
        """
        if not self.trainer:
            return {'total_score': 0, 'reactions': {}, 'user_count': 0, 'text_feedbacks': []}

        # Read feedback from trainer's feedback file
        feedback_file = self.trainer.feedback_file

        if not feedback_file.exists():
            return {'total_score': 0, 'reactions': {}, 'user_count': 0, 'text_feedbacks': []}

        total_score = 0
        reactions = {}
        users = set()
        text_feedbacks = []

        try:
            import json
            with open(feedback_file, 'r', encoding='utf-8') as f:
                for line in f:
                    try:
                        feedback = json.loads(line)

                        if feedback.get('project') != project or feedback.get('version') != version:
                            continue

                        data = feedback.get('data', {})
                        user_id = data.get('user_id')
                        if user_id:
                            users.add(user_id)

                        if feedback.get('type') == 'reaction':
                            emoji = data.get('emoji')
                            score_delta = data.get('score_delta', 0)
                            total_score += score_delta
                            if emoji:
                                reactions[emoji] = reactions.get(emoji, 0) + 1

                        elif feedback.get('type') == 'text':
                            text_feedbacks.append({
                                'text': data.get('text', ''),
                                'rating': data.get('rating'),
                                'user': data.get('user_name', 'Anonym'),
                                'timestamp': feedback.get('timestamp'),
                            })

                    except Exception:
                        continue
        except Exception as e:
            logger.error(f"Failed to read feedback: {e}")

        return {
            'total_score': total_score,
            'reactions': reactions,
            'user_count': len(users),
            'text_feedbacks': text_feedbacks,
        }


def get_feedback_collector(bot: discord.Client, trainer=None) -> PatchNotesFeedbackCollector:
    """Get or create PatchNotesFeedbackCollector instance."""
    return PatchNotesFeedbackCollector(bot, trainer)
