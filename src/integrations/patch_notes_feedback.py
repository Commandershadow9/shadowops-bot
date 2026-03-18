"""
Patch Notes Feedback Collection System.

Collects user feedback through:
- Discord buttons (Like, Rate) — direkt am Patch Notes Embed (v3)
- Text feedback via Discord Modal
- Legacy: Discord reactions (emoji scoring)
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

        # Learning DB
        import asyncio
        score = (rating_value or 3) * 20 - 50  # 1→-30, 3→10, 5→50
        asyncio.ensure_future(self.collector._record_feedback_to_db(
            self.project, self.version, 'rating',
            {**feedback_data, 'score_delta': score},
        ))

        logger.info(
            f"📝 Text-Feedback für {self.project} v{self.version}: "
            f"Rating={rating_value}, Text='{feedback_text[:50]}...'"
        )

        await interaction.response.send_message(
            "✅ **Danke für dein Feedback!** Wir nutzen es um die Patch Notes zu verbessern.",
            ephemeral=True
        )


class PatchNotesView(ui.View):
    """Persistent Discord View mit Buttons direkt am Patch Notes Embed.

    Überlebt Bot-Restarts dank statischer custom_id + Lookup in tracked_messages.
    Gleicher Ansatz wie auto_fix_manager.py ProposalView.
    """

    def __init__(self, collector: 'PatchNotesFeedbackCollector',
                 changelog_url: str = ''):
        super().__init__(timeout=None)  # Persistent View — kein Timeout
        self.collector = collector
        self.like_count = 0
        self._liked_users: set = set()

        # URL-Button für Changelog (nur wenn URL vorhanden)
        if changelog_url:
            self.add_item(ui.Button(
                label="🔗 Changelog öffnen",
                style=discord.ButtonStyle.link,
                url=changelog_url,
            ))

    def _get_message_data(self, interaction: discord.Interaction) -> Optional[Dict]:
        """Hole Projekt/Version aus tracked_messages via message_id."""
        if not self.collector:
            return None
        return self.collector.tracked_messages.get(interaction.message.id)

    @ui.button(label="👍 Gefällt mir", style=discord.ButtonStyle.success,
               custom_id="patchnotes_like")
    async def like_button(self, interaction: discord.Interaction, button: ui.Button):
        """Quick-Reaction, Zähler im Label. Jeder User nur einmal."""
        user_id = interaction.user.id

        if user_id in self._liked_users:
            await interaction.response.send_message(
                "Du hast bereits geliked! 👍", ephemeral=True
            )
            return

        self._liked_users.add(user_id)
        self.like_count += 1
        button.label = f"👍 Gefällt mir ({self.like_count})"
        await interaction.response.edit_message(view=self)

        # Feedback aufzeichnen (Projekt/Version aus tracked_messages)
        msg_data = self._get_message_data(interaction)
        if msg_data and self.collector:
            feedback_data = {
                'emoji': '👍',
                'score_delta': 10,
                'user_id': user_id,
                'added': True,
            }
            # Legacy JSONL
            if self.collector.trainer:
                self.collector.trainer.record_feedback(
                    version=msg_data['version'],
                    project=msg_data['project'],
                    feedback_type='reaction',
                    feedback_data=feedback_data,
                )
            # Learning DB
            import asyncio
            asyncio.ensure_future(self.collector._record_feedback_to_db(
                msg_data['project'], msg_data['version'], 'like', feedback_data,
            ))

    @ui.button(label="⭐ Bewerten", style=discord.ButtonStyle.secondary,
               custom_id="patchnotes_rate")
    async def rate_button(self, interaction: discord.Interaction, button: ui.Button):
        """Öffnet TextFeedbackModal."""
        msg_data = self._get_message_data(interaction)
        project = msg_data['project'] if msg_data else 'unknown'
        version = msg_data['version'] if msg_data else 'unknown'

        modal = TextFeedbackModal(self.collector, project, version)
        await interaction.response.send_modal(modal)


class PatchNotesFeedbackView(ui.View):
    """Legacy: Discord View mit Feedback-Button unter Patch Notes."""

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
    Collects user feedback on patch notes through Discord buttons and text.
    Speichert in agent_learning DB (PostgreSQL) + Legacy JSONL.
    """

    def __init__(self, bot: discord.Client, patch_notes_trainer=None):
        self.bot = bot
        self.trainer = patch_notes_trainer

        # Learning DB (async, wird bei Bedarf verbunden)
        self._learning_db = None

        # Track messages we're collecting feedback on
        # {message_id: {'project': str, 'version': str, 'timestamp': datetime}}
        self.tracked_messages: Dict[int, Dict] = {}

        # Reaction -> Score mapping (Legacy, für bestehende Reaction-Handler)
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

    async def _get_learning_db(self):
        """Lazy-Init der Learning DB."""
        if self._learning_db is None:
            try:
                from integrations.patch_notes_learning import PatchNotesLearning
                self._learning_db = PatchNotesLearning()
                await self._learning_db.connect()
            except Exception as e:
                logger.debug("Learning DB nicht verfuegbar: %s", e)
                self._learning_db = False  # Nicht nochmal versuchen
        return self._learning_db if self._learning_db else None

    async def _record_feedback_to_db(self, project: str, version: str,
                                      feedback_type: str, feedback_data: Dict):
        """Feedback parallel in Learning DB speichern."""
        try:
            db = await self._get_learning_db()
            if db:
                await db.record_feedback(
                    project=project,
                    version=version,
                    feedback_type=feedback_type,
                    user_id=feedback_data.get('user_id'),
                    user_name=feedback_data.get('user_name'),
                    score_delta=feedback_data.get('score_delta', 0),
                    rating=feedback_data.get('rating'),
                    text_content=feedback_data.get('text'),
                    metadata=feedback_data,
                )
        except Exception as e:
            logger.debug("Feedback-DB-Write fehlgeschlagen: %s", e)

    def create_view(self, changelog_url: str = '') -> PatchNotesView:
        """Erstellt ein PatchNotesView für die Embed-Nachricht."""
        return PatchNotesView(self, changelog_url)

    def register_persistent_view(self):
        """Registriere Persistent View damit Buttons nach Bot-Restart funktionieren."""
        try:
            self.bot.add_view(PatchNotesView(self))
            logger.info("✅ Persistent view für Patch Notes Buttons registriert")
        except Exception as e:
            logger.warning(f"Could not register persistent view: {e}")

    async def track_patch_notes_message(self, message: Message, project: str,
                                         version: str, changelog_url: str = '') -> None:
        """Start tracking a patch notes message. Buttons are now attached by the caller."""
        self.tracked_messages[message.id] = {
            'project': project,
            'version': version,
            'timestamp': datetime.now(timezone.utc),
            'channel_id': message.channel.id,
        }
        logger.info(f"📊 Tracking feedback for {project} v{version} (message {message.id})")

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

        # Learning DB
        await self._record_feedback_to_db(
            message_data['project'], message_data['version'], 'reaction', feedback_data,
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


    async def close_old_feedback_windows(self):
        """Schliesst Feedback-Windows fuer Generierungen die aelter als 7 Tage sind.

        Berechnet finale Scores, aktualisiert Varianten-Gewichtung und
        Beispiel-Ranking. Sollte periodisch aufgerufen werden (z.B. beim Bot-Start).
        """
        try:
            db = await self._get_learning_db()
            if not db:
                return

            unclosed = await db.get_unclosed_generations(min_age_hours=168)
            if not unclosed:
                return

            for gen in unclosed:
                await db.close_feedback_window(gen['project'], gen['version'])

                # Learning-Notification posten
                try:
                    notifier = getattr(self.bot, 'learning_notifier', None)
                    if notifier:
                        # Score aus DB holen
                        score_row = await db.pool.fetchrow(
                            "SELECT * FROM pn_generations WHERE project=$1 AND version=$2",
                            gen['project'], gen['version'],
                        )
                        if score_row:
                            fb = await db.get_aggregated_feedback(gen['project'], gen['version'])
                            await notifier.notify_feedback_evaluated(
                                project=gen['project'],
                                version=gen['version'],
                                variant_id=score_row.get('variant_id'),
                                auto_score=score_row.get('auto_quality', 50),
                                feedback_score=score_row.get('feedback_score', 50),
                                combined_score=(score_row.get('auto_quality', 50) * 0.6 + (score_row.get('feedback_score', 50)) * 0.4),
                                feedback_count=fb.get('feedback_count', 0),
                            )
                except Exception:
                    pass

            logger.info(
                "Feedback-Windows geschlossen: %d Generierungen ausgewertet",
                len(unclosed),
            )
        except Exception as e:
            logger.debug("Feedback-Window-Close fehlgeschlagen: %s", e)


def get_feedback_collector(bot: discord.Client, trainer=None) -> PatchNotesFeedbackCollector:
    """Get or create PatchNotesFeedbackCollector instance."""
    return PatchNotesFeedbackCollector(bot, trainer)
