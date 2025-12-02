"""
Patch Notes Feedback Collection System.

Collects user feedback through Discord reactions and manual ratings.
"""

import asyncio
import logging
import discord
from discord import Message, Reaction, User
from typing import Dict, Optional
from datetime import datetime, timedelta

logger = logging.getLogger('shadowops')


class PatchNotesFeedbackCollector:
    """
    Collects user feedback on patch notes through Discord reactions.
    """

    def __init__(self, bot: discord.Client, patch_notes_trainer):
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

        logger.info("✅ Patch Notes Feedback Collector initialized")

    async def track_patch_notes_message(self, message: Message, project: str, version: str) -> None:
        """
        Start tracking reactions on a patch notes message.

        Args:
            message: Discord message containing patch notes
            project: Project name
            version: Version number
        """
        self.tracked_messages[message.id] = {
            'project': project,
            'version': version,
            'timestamp': datetime.utcnow(),
            'channel_id': message.channel.id,
        }

        # Add reaction buttons
        try:
            for emoji in ['👍', '👎', '❤️', '🔥']:
                await message.add_reaction(emoji)

            logger.info(f"📊 Tracking feedback for {project} v{version} (message {message.id})")
        except Exception as e:
            logger.error(f"Failed to add reaction buttons: {e}")

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
        cutoff = datetime.utcnow() - timedelta(hours=max_age_hours)

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
            Dict with total_score, reaction_counts, user_count
        """
        # Read feedback from trainer's feedback file
        feedback_file = self.trainer.feedback_file

        if not feedback_file.exists():
            return {'total_score': 0, 'reactions': {}, 'user_count': 0}

        total_score = 0
        reactions = {}
        users = set()

        try:
            with open(feedback_file, 'r', encoding='utf-8') as f:
                for line in f:
                    import json
                    try:
                        feedback = json.loads(line)

                        if feedback.get('project') != project or feedback.get('version') != version:
                            continue

                        if feedback.get('type') == 'reaction':
                            data = feedback.get('data', {})
                            emoji = data.get('emoji')
                            score_delta = data.get('score_delta', 0)
                            user_id = data.get('user_id')

                            total_score += score_delta

                            if emoji:
                                reactions[emoji] = reactions.get(emoji, 0) + 1

                            if user_id:
                                users.add(user_id)
                    except:
                        continue
        except Exception as e:
            logger.error(f"Failed to read feedback: {e}")

        return {
            'total_score': total_score,
            'reactions': reactions,
            'user_count': len(users)
        }


def get_feedback_collector(bot: discord.Client, trainer) -> PatchNotesFeedbackCollector:
    """Get or create PatchNotesFeedbackCollector instance."""
    return PatchNotesFeedbackCollector(bot, trainer)
