"""
Incident Management System for ShadowOps Bot
Tracks and manages security incidents with Discord thread integration
"""

import asyncio
import logging
import json
import hashlib
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from pathlib import Path
from enum import Enum
import discord

logger = logging.getLogger(__name__)


class IncidentStatus(Enum):
    """Incident status states"""
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CLOSED = "closed"


class IncidentSeverity(Enum):
    """Incident severity levels"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Incident:
    """Represents a tracked incident"""

    def __init__(
        self, incident_id: str, title: str, description: str,
        severity: IncidentSeverity, affected_projects: List[str],
        event_type: str
    ):
        """
        Initialize incident

        Args:
            incident_id: Unique incident identifier
            title: Incident title
            description: Incident description
            severity: Severity level
            affected_projects: List of affected project names
            event_type: Type of incident (downtime, vulnerability, deployment_failure)
        """
        self.id = incident_id
        self.title = title
        self.description = description
        self.severity = severity
        self.affected_projects = affected_projects
        self.event_type = event_type

        self.status = IncidentStatus.OPEN
        self.created_at = datetime.utcnow()
        self.updated_at = datetime.utcnow()
        self.resolved_at: Optional[datetime] = None

        # Discord thread info
        self.thread_id: Optional[int] = None
        self.original_message_id: Optional[int] = None

        # Timeline and notes
        self.timeline: List[Dict] = []
        self.resolution_notes: Optional[str] = None

        # Add creation to timeline
        self.add_timeline_event("Incident created", "system")

    def add_timeline_event(self, event: str, author: str = "system"):
        """Add event to incident timeline"""
        self.timeline.append({
            'timestamp': datetime.utcnow().isoformat(),
            'event': event,
            'author': author
        })
        self.updated_at = datetime.utcnow()

    def update_status(self, new_status: IncidentStatus, author: str = "system"):
        """Update incident status"""
        old_status = self.status
        self.status = new_status
        self.updated_at = datetime.utcnow()

        if new_status == IncidentStatus.RESOLVED:
            self.resolved_at = datetime.utcnow()

        self.add_timeline_event(
            f"Status changed: {old_status.value} → {new_status.value}",
            author
        )

    def set_resolution(self, notes: str, author: str = "system"):
        """Set resolution notes"""
        self.resolution_notes = notes
        self.update_status(IncidentStatus.RESOLVED, author)
        self.add_timeline_event(f"Resolution: {notes}", author)

    @property
    def duration(self) -> Optional[timedelta]:
        """Get incident duration"""
        if self.resolved_at:
            return self.resolved_at - self.created_at
        return datetime.utcnow() - self.created_at

    def to_dict(self) -> Dict:
        """Serialize incident to dictionary"""
        return {
            'id': self.id,
            'title': self.title,
            'description': self.description,
            'severity': self.severity.value,
            'affected_projects': self.affected_projects,
            'event_type': self.event_type,
            'status': self.status.value,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat(),
            'resolved_at': self.resolved_at.isoformat() if self.resolved_at else None,
            'thread_id': self.thread_id,
            'original_message_id': self.original_message_id,
            'timeline': self.timeline,
            'resolution_notes': self.resolution_notes
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'Incident':
        """Deserialize incident from dictionary"""
        incident = cls(
            incident_id=data['id'],
            title=data['title'],
            description=data['description'],
            severity=IncidentSeverity(data['severity']),
            affected_projects=data['affected_projects'],
            event_type=data['event_type']
        )

        incident.status = IncidentStatus(data['status'])
        incident.created_at = datetime.fromisoformat(data['created_at'])
        incident.updated_at = datetime.fromisoformat(data['updated_at'])

        if data.get('resolved_at'):
            incident.resolved_at = datetime.fromisoformat(data['resolved_at'])

        incident.thread_id = data.get('thread_id')
        incident.original_message_id = data.get('original_message_id')
        incident.timeline = data.get('timeline', [])
        incident.resolution_notes = data.get('resolution_notes')

        return incident


class IncidentManager:
    """
    Incident management system

    Features:
    - Automatic incident detection
    - Status tracking and updates
    - Discord thread per incident
    - Timeline tracking
    - Resolution management
    """

    def __init__(self, bot, config: Dict):
        """
        Initialize incident manager

        Args:
            bot: Discord bot instance
            config: Configuration dictionary
        """
        self.bot = bot
        self.config = config
        self.logger = logger

        # Active incidents (in-memory)
        self.incidents: Dict[str, Incident] = {}

        # Discord channel for incidents
        self.incident_channel_id = config.get('channels', {}).get('customer_alerts', 0)

        # Persistence
        self.state_file = Path('data/incidents.json')
        self.state_file.parent.mkdir(exist_ok=True)
        self._load_incidents()

        # Auto-close resolved incidents after N hours
        self.auto_close_after_hours = config.get('incidents', {}).get('auto_close_hours', 24)

        self.logger.info(f"🔧 Incident Manager initialized with {len(self.incidents)} active incidents")

    def _load_incidents(self):
        """Load persisted incidents from disk"""
        if not self.state_file.exists():
            return

        try:
            with open(self.state_file, 'r') as f:
                data = json.load(f)

            for incident_data in data:
                incident = Incident.from_dict(incident_data)
                self.incidents[incident.id] = incident

            self.logger.info(f"📂 Loaded {len(self.incidents)} incidents from disk")

        except Exception as e:
            self.logger.error(f"❌ Error loading incidents: {e}", exc_info=True)

    def _save_incidents(self):
        """Persist incidents to disk"""
        try:
            data = [incident.to_dict() for incident in self.incidents.values()]

            with open(self.state_file, 'w') as f:
                json.dump(data, f, indent=2)

        except Exception as e:
            self.logger.error(f"❌ Error saving incidents: {e}", exc_info=True)

    def _generate_incident_id(self, title: str, event_type: str) -> str:
        """Generate unique incident ID"""
        content = f"{title}_{event_type}_{datetime.utcnow().date()}"
        return hashlib.md5(content.encode()).hexdigest()[:12]

    async def create_incident(
        self, title: str, description: str, severity: IncidentSeverity,
        affected_projects: List[str], event_type: str,
        auto_create_thread: bool = True
    ) -> Incident:
        """
        Create new incident

        Args:
            title: Incident title
            description: Incident description
            severity: Severity level
            affected_projects: List of affected projects
            event_type: Type of incident
            auto_create_thread: Whether to automatically create Discord thread

        Returns:
            Created Incident instance
        """
        # Generate ID
        incident_id = self._generate_incident_id(title, event_type)

        # Check if incident already exists
        if incident_id in self.incidents:
            existing = self.incidents[incident_id]
            if existing.status != IncidentStatus.RESOLVED:
                self.logger.info(f"ℹ️ Incident {incident_id} already exists and is active")
                return existing

        # Create new incident
        incident = Incident(
            incident_id=incident_id,
            title=title,
            description=description,
            severity=severity,
            affected_projects=affected_projects,
            event_type=event_type
        )

        self.incidents[incident_id] = incident
        self._save_incidents()

        self.logger.info(f"🚨 Created incident {incident_id}: {title} ({severity.value})")

        # Create Discord thread
        if auto_create_thread:
            await self._create_incident_thread(incident)

        return incident

    async def _create_incident_thread(self, incident: Incident):
        """
        Create Discord thread for incident

        Args:
            incident: Incident instance
        """
        channel = self.bot.get_channel(self.incident_channel_id)
        if not channel:
            self.logger.warning(f"⚠️ Incident channel not found: {self.incident_channel_id}")
            return

        # Create initial incident message
        embed = self._create_incident_embed(incident)

        try:
            message = await channel.send(embed=embed)
            incident.original_message_id = message.id

            # Create thread from message
            thread_name = f"🚨 {incident.title[:80]}"  # Discord limit: 100 chars
            thread = await message.create_thread(
                name=thread_name,
                auto_archive_duration=1440  # 24 hours
            )

            incident.thread_id = thread.id

            # Send initial thread message
            await thread.send(
                f"**Incident Tracking Thread**\n\n"
                f"This thread tracks incident `{incident.id}`.\n"
                f"Updates will be posted here automatically.\n\n"
                f"**Timeline:**"
            )

            # Save updated incident
            self._save_incidents()

            self.logger.info(f"📌 Created thread for incident {incident.id}: {thread.id}")

        except Exception as e:
            self.logger.error(f"❌ Error creating incident thread: {e}", exc_info=True)

    def _create_incident_embed(self, incident: Incident) -> discord.Embed:
        """Create Discord embed for incident"""
        # Color based on severity
        severity_colors = {
            IncidentSeverity.CRITICAL: discord.Color.dark_red(),
            IncidentSeverity.HIGH: discord.Color.red(),
            IncidentSeverity.MEDIUM: discord.Color.orange(),
            IncidentSeverity.LOW: discord.Color.yellow()
        }

        color = severity_colors.get(incident.severity, discord.Color.red())

        # Status emoji
        status_emojis = {
            IncidentStatus.OPEN: '🔴',
            IncidentStatus.IN_PROGRESS: '🟡',
            IncidentStatus.RESOLVED: '🟢',
            IncidentStatus.CLOSED: '⚫'
        }

        status_emoji = status_emojis.get(incident.status, '🔴')

        embed = discord.Embed(
            title=f"🚨 Incident: {incident.title}",
            description=incident.description,
            color=color,
            timestamp=incident.created_at
        )

        embed.add_field(
            name="📊 Status",
            value=f"{status_emoji} {incident.status.value.replace('_', ' ').title()}",
            inline=True
        )

        embed.add_field(
            name="⚠️ Severity",
            value=incident.severity.value.upper(),
            inline=True
        )

        embed.add_field(
            name="🔖 Incident ID",
            value=f"`{incident.id}`",
            inline=True
        )

        # Affected projects
        projects_str = ", ".join(incident.affected_projects) if incident.affected_projects else "N/A"
        embed.add_field(
            name="🎯 Affected Projects",
            value=projects_str,
            inline=False
        )

        # Duration
        if incident.duration:
            duration = incident.duration
            hours = int(duration.total_seconds() // 3600)
            minutes = int((duration.total_seconds() % 3600) // 60)

            if hours > 0:
                duration_str = f"{hours}h {minutes}m"
            else:
                duration_str = f"{minutes}m"

            embed.add_field(
                name="⏱️ Duration",
                value=duration_str,
                inline=True
            )

        # Resolution (if resolved)
        if incident.status == IncidentStatus.RESOLVED and incident.resolution_notes:
            embed.add_field(
                name="✅ Resolution",
                value=incident.resolution_notes,
                inline=False
            )

        embed.set_footer(text=f"Created at")

        return embed

    async def update_incident(
        self, incident_id: str, status: Optional[IncidentStatus] = None,
        update_message: Optional[str] = None, author: str = "system"
    ):
        """
        Update incident status and post to thread

        Args:
            incident_id: Incident ID to update
            status: New status (optional)
            update_message: Update message to post
            author: Author of the update
        """
        if incident_id not in self.incidents:
            self.logger.warning(f"⚠️ Incident {incident_id} not found")
            return

        incident = self.incidents[incident_id]

        # Update status
        if status:
            incident.update_status(status, author)
            self.logger.info(f"📝 Updated incident {incident_id} status: {status.value}")

        # Post update to thread
        if update_message:
            incident.add_timeline_event(update_message, author)
            await self._post_thread_update(incident, update_message)

        # Update original message embed
        await self._update_incident_message(incident)

        # Save changes
        self._save_incidents()

    async def _post_thread_update(self, incident: Incident, message: str):
        """Post update message to incident thread"""
        if not incident.thread_id:
            return

        try:
            thread = self.bot.get_channel(incident.thread_id)
            if not thread:
                # Try to fetch thread
                channel = self.bot.get_channel(self.incident_channel_id)
                if channel:
                    thread = await channel.fetch_channel(incident.thread_id)

            if thread:
                timestamp = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')
                await thread.send(f"**[{timestamp}]** {message}")

        except discord.NotFound:
            self.logger.warning(f"⚠️ Thread not found for incident {incident.id}")
        except Exception as e:
            self.logger.error(f"❌ Error posting to thread: {e}", exc_info=True)

    async def _update_incident_message(self, incident: Incident):
        """Update the original incident message embed"""
        if not incident.original_message_id:
            return

        try:
            channel = self.bot.get_channel(self.incident_channel_id)
            if not channel:
                return

            message = await channel.fetch_message(incident.original_message_id)
            embed = self._create_incident_embed(incident)
            await message.edit(embed=embed)

        except discord.NotFound:
            self.logger.warning(f"⚠️ Original message not found for incident {incident.id}")
        except Exception as e:
            self.logger.error(f"❌ Error updating incident message: {e}", exc_info=True)

    async def resolve_incident(
        self, incident_id: str, resolution_notes: str, author: str = "system"
    ):
        """
        Resolve an incident

        Args:
            incident_id: Incident ID
            resolution_notes: Resolution description
            author: Author of the resolution
        """
        if incident_id not in self.incidents:
            self.logger.warning(f"⚠️ Incident {incident_id} not found")
            return

        incident = self.incidents[incident_id]
        incident.set_resolution(resolution_notes, author)

        # Post resolution to thread
        await self._post_thread_update(
            incident,
            f"✅ **RESOLVED** by {author}: {resolution_notes}"
        )

        # Update message
        await self._update_incident_message(incident)

        # Save changes
        self._save_incidents()

        self.logger.info(f"✅ Resolved incident {incident_id}")

    async def auto_close_old_incidents(self):
        """Automatically close resolved incidents after configured time"""
        cutoff_time = datetime.utcnow() - timedelta(hours=self.auto_close_after_hours)

        for incident in list(self.incidents.values()):
            if incident.status == IncidentStatus.RESOLVED and incident.resolved_at:
                if incident.resolved_at < cutoff_time:
                    incident.update_status(IncidentStatus.CLOSED)
                    await self._post_thread_update(
                        incident,
                        f"⚫ Incident automatically closed after {self.auto_close_after_hours}h"
                    )
                    await self._update_incident_message(incident)

                    self.logger.info(f"⚫ Auto-closed incident {incident.id}")

        self._save_incidents()

    def get_active_incidents(self) -> List[Incident]:
        """Get all active (non-closed) incidents"""
        return [
            i for i in self.incidents.values()
            if i.status != IncidentStatus.CLOSED
        ]

    def get_incident(self, incident_id: str) -> Optional[Incident]:
        """Get specific incident by ID"""
        return self.incidents.get(incident_id)

    async def detect_project_down_incident(self, project_name: str, error: str):
        """
        Detect and create incident for project downtime

        Args:
            project_name: Name of the down project
            error: Error message
        """
        title = f"{project_name} Service Unavailable"
        description = f"Health check failed for {project_name}"

        await self.create_incident(
            title=title,
            description=description,
            severity=IncidentSeverity.HIGH,
            affected_projects=[project_name],
            event_type="downtime"
        )

    async def detect_critical_vulnerability_incident(
        self, project_name: str, vulnerability_id: str, details: Dict
    ):
        """
        Detect and create incident for critical vulnerability

        Args:
            project_name: Affected project
            vulnerability_id: CVE or vulnerability ID
            details: Vulnerability details
        """
        title = f"Critical Vulnerability: {vulnerability_id}"
        description = f"Critical vulnerability detected in {project_name}: {details.get('Title', 'Unknown')}"

        await self.create_incident(
            title=title,
            description=description,
            severity=IncidentSeverity.CRITICAL,
            affected_projects=[project_name],
            event_type="vulnerability"
        )

    async def detect_deployment_failure_incident(
        self, project_name: str, error: str
    ):
        """
        Detect and create incident for deployment failure

        Args:
            project_name: Project that failed to deploy
            error: Deployment error message
        """
        title = f"Deployment Failed: {project_name}"
        description = f"Deployment to {project_name} failed"

        await self.create_incident(
            title=title,
            description=description,
            severity=IncidentSeverity.HIGH,
            affected_projects=[project_name],
            event_type="deployment_failure"
        )
