"""
Unit Tests for Incident Manager
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from datetime import datetime, timedelta
from pathlib import Path

from src.integrations.incident_manager import (
    IncidentManager, Incident, IncidentStatus, IncidentSeverity
)


class TestIncidentClass:
    """Tests for Incident class"""

    def test_init(self):
        """Test Incident initialization"""
        incident = Incident(
            incident_id='test123',
            title='Test Incident',
            description='Test description',
            severity=IncidentSeverity.HIGH,
            affected_projects=['project1', 'project2'],
            event_type='downtime'
        )

        assert incident.id == 'test123'
        assert incident.title == 'Test Incident'
        assert incident.severity == IncidentSeverity.HIGH
        assert incident.status == IncidentStatus.OPEN
        assert len(incident.affected_projects) == 2
        assert incident.resolved_at is None
        assert len(incident.timeline) == 1  # Creation event

    def test_add_timeline_event(self):
        """Test adding timeline event"""
        incident = Incident(
            'test123', 'Test', 'Description',
            IncidentSeverity.MEDIUM, ['project'], 'test'
        )

        initial_timeline_length = len(incident.timeline)

        incident.add_timeline_event('Investigation started', 'admin')

        assert len(incident.timeline) == initial_timeline_length + 1
        assert incident.timeline[-1]['event'] == 'Investigation started'
        assert incident.timeline[-1]['author'] == 'admin'

    def test_update_status(self):
        """Test status update"""
        incident = Incident(
            'test123', 'Test', 'Description',
            IncidentSeverity.HIGH, ['project'], 'test'
        )

        assert incident.status == IncidentStatus.OPEN

        incident.update_status(IncidentStatus.IN_PROGRESS, 'operator')

        assert incident.status == IncidentStatus.IN_PROGRESS
        assert any('IN_PROGRESS' in event['event'].upper() for event in incident.timeline)

    def test_set_resolution(self):
        """Test setting resolution"""
        incident = Incident(
            'test123', 'Test', 'Description',
            IncidentSeverity.HIGH, ['project'], 'test'
        )

        incident.set_resolution('Fixed by restarting service', 'admin')

        assert incident.status == IncidentStatus.RESOLVED
        assert incident.resolution_notes == 'Fixed by restarting service'
        assert incident.resolved_at is not None

    def test_duration_calculation(self):
        """Test incident duration calculation"""
        incident = Incident(
            'test123', 'Test', 'Description',
            IncidentSeverity.HIGH, ['project'], 'test'
        )

        # Should have duration even when not resolved
        duration = incident.duration
        assert duration is not None
        assert isinstance(duration, timedelta)

    def test_to_dict_serialization(self):
        """Test serialization to dictionary"""
        incident = Incident(
            'test123', 'Test Incident', 'Description',
            IncidentSeverity.CRITICAL, ['project1'], 'downtime'
        )

        data = incident.to_dict()

        assert data['id'] == 'test123'
        assert data['title'] == 'Test Incident'
        assert data['severity'] == 'critical'
        assert data['status'] == 'open'
        assert data['affected_projects'] == ['project1']

    def test_from_dict_deserialization(self):
        """Test deserialization from dictionary"""
        data = {
            'id': 'test123',
            'title': 'Test Incident',
            'description': 'Test description',
            'severity': 'high',
            'affected_projects': ['project1'],
            'event_type': 'downtime',
            'status': 'in_progress',
            'created_at': datetime.utcnow().isoformat(),
            'updated_at': datetime.utcnow().isoformat(),
            'resolved_at': None,
            'thread_id': 12345,
            'original_message_id': 67890,
            'timeline': [
                {'timestamp': datetime.utcnow().isoformat(), 'event': 'Created', 'author': 'system'}
            ],
            'resolution_notes': None
        }

        incident = Incident.from_dict(data)

        assert incident.id == 'test123'
        assert incident.title == 'Test Incident'
        assert incident.severity == IncidentSeverity.HIGH
        assert incident.status == IncidentStatus.IN_PROGRESS
        assert incident.thread_id == 12345


class TestIncidentManagerInit:
    """Tests for IncidentManager initialization"""

    def test_init(self, tmp_path):
        """Test IncidentManager initialization"""
        config = {
            'channels': {
                'customer_alerts': 12345
            },
            'incidents': {
                'auto_close_hours': 24
            }
        }

        mock_bot = Mock()

        # Use tmp_path for state file
        with patch('src.integrations.incident_manager.Path') as mock_path:
            mock_path.return_value = tmp_path / 'incidents.json'
            manager = IncidentManager(mock_bot, config)

        assert manager.incident_channel_id == 12345
        assert manager.auto_close_after_hours == 24
        assert len(manager.incidents) == 0


class TestIncidentCreation:
    """Tests for incident creation"""

    @pytest.mark.asyncio
    async def test_create_incident(self, tmp_path):
        """Test creating new incident"""
        config = {
            'channels': {'customer_alerts': 12345},
            'incidents': {}
        }

        mock_bot = Mock()
        mock_bot.get_channel = Mock(return_value=None)

        with patch('src.integrations.incident_manager.Path') as mock_path:
            state_file = tmp_path / 'incidents.json'
            mock_path.return_value = state_file
            manager = IncidentManager(mock_bot, config)

        manager._create_incident_thread = AsyncMock()

        incident = await manager.create_incident(
            title='Service Down',
            description='Project is not responding',
            severity=IncidentSeverity.HIGH,
            affected_projects=['test-project'],
            event_type='downtime',
            auto_create_thread=False
        )

        assert incident is not None
        assert incident.title == 'Service Down'
        assert incident.severity == IncidentSeverity.HIGH
        assert incident.status == IncidentStatus.OPEN
        assert 'test-project' in incident.affected_projects

    @pytest.mark.asyncio
    async def test_create_duplicate_incident(self, tmp_path):
        """Test creating duplicate incident (should return existing)"""
        config = {
            'channels': {'customer_alerts': 12345},
            'incidents': {}
        }

        mock_bot = Mock()

        with patch('src.integrations.incident_manager.Path') as mock_path:
            state_file = tmp_path / 'incidents.json'
            mock_path.return_value = state_file
            manager = IncidentManager(mock_bot, config)

        # Create first incident
        incident1 = await manager.create_incident(
            title='Service Down',
            description='Project not responding',
            severity=IncidentSeverity.HIGH,
            affected_projects=['test-project'],
            event_type='downtime',
            auto_create_thread=False
        )

        # Try to create same incident again (same title, type, date)
        incident2 = await manager.create_incident(
            title='Service Down',
            description='Project not responding',
            severity=IncidentSeverity.HIGH,
            affected_projects=['test-project'],
            event_type='downtime',
            auto_create_thread=False
        )

        # Should return the same incident
        assert incident1.id == incident2.id


class TestIncidentUpdates:
    """Tests for incident updates"""

    @pytest.mark.asyncio
    async def test_update_incident_status(self, tmp_path):
        """Test updating incident status"""
        config = {
            'channels': {'customer_alerts': 12345},
            'incidents': {}
        }

        mock_bot = Mock()

        with patch('src.integrations.incident_manager.Path') as mock_path:
            state_file = tmp_path / 'incidents.json'
            mock_path.return_value = state_file
            manager = IncidentManager(mock_bot, config)

        manager._post_thread_update = AsyncMock()
        manager._update_incident_message = AsyncMock()

        # Create incident
        incident = await manager.create_incident(
            title='Test Incident',
            description='Test',
            severity=IncidentSeverity.MEDIUM,
            affected_projects=['project'],
            event_type='test',
            auto_create_thread=False
        )

        # Update status
        await manager.update_incident(
            incident.id,
            status=IncidentStatus.IN_PROGRESS,
            author='operator'
        )

        # Verify status changed
        updated_incident = manager.get_incident(incident.id)
        assert updated_incident.status == IncidentStatus.IN_PROGRESS

    @pytest.mark.asyncio
    async def test_resolve_incident(self, tmp_path):
        """Test resolving incident"""
        config = {
            'channels': {'customer_alerts': 12345},
            'incidents': {}
        }

        mock_bot = Mock()

        with patch('src.integrations.incident_manager.Path') as mock_path:
            state_file = tmp_path / 'incidents.json'
            mock_path.return_value = state_file
            manager = IncidentManager(mock_bot, config)

        manager._post_thread_update = AsyncMock()
        manager._update_incident_message = AsyncMock()

        # Create incident
        incident = await manager.create_incident(
            title='Test Incident',
            description='Test',
            severity=IncidentSeverity.HIGH,
            affected_projects=['project'],
            event_type='test',
            auto_create_thread=False
        )

        # Resolve incident
        await manager.resolve_incident(
            incident.id,
            resolution_notes='Fixed by restarting service',
            author='admin'
        )

        # Verify resolved
        resolved_incident = manager.get_incident(incident.id)
        assert resolved_incident.status == IncidentStatus.RESOLVED
        assert resolved_incident.resolution_notes == 'Fixed by restarting service'
        assert resolved_incident.resolved_at is not None


class TestIncidentRetrieval:
    """Tests for incident retrieval"""

    @pytest.mark.asyncio
    async def test_get_active_incidents(self, tmp_path):
        """Test getting active incidents"""
        config = {
            'channels': {'customer_alerts': 12345},
            'incidents': {}
        }

        mock_bot = Mock()

        with patch('src.integrations.incident_manager.Path') as mock_path:
            state_file = tmp_path / 'incidents.json'
            mock_path.return_value = state_file
            manager = IncidentManager(mock_bot, config)

        # Create multiple incidents
        incident1 = await manager.create_incident(
            title='Incident 1', description='Test',
            severity=IncidentSeverity.HIGH,
            affected_projects=['p1'], event_type='test',
            auto_create_thread=False
        )

        incident2 = await manager.create_incident(
            title='Incident 2', description='Test',
            severity=IncidentSeverity.MEDIUM,
            affected_projects=['p2'], event_type='test',
            auto_create_thread=False
        )

        # Resolve one incident
        manager._post_thread_update = AsyncMock()
        manager._update_incident_message = AsyncMock()
        await manager.resolve_incident(incident1.id, 'Fixed', 'admin')

        # Close the resolved incident
        incident1.update_status(IncidentStatus.CLOSED)

        # Get active incidents (should only return incident2)
        active = manager.get_active_incidents()

        assert len(active) == 1
        assert active[0].id == incident2.id


class TestIncidentDetection:
    """Tests for automatic incident detection"""

    @pytest.mark.asyncio
    async def test_detect_project_down_incident(self, tmp_path):
        """Test detecting project down incident"""
        config = {
            'channels': {'customer_alerts': 12345},
            'incidents': {}
        }

        mock_bot = Mock()

        with patch('src.integrations.incident_manager.Path') as mock_path:
            state_file = tmp_path / 'incidents.json'
            mock_path.return_value = state_file
            manager = IncidentManager(mock_bot, config)

        manager._create_incident_thread = AsyncMock()

        # Detect project down
        await manager.detect_project_down_incident(
            project_name='test-project',
            error='Connection timeout'
        )

        # Should have created an incident
        assert len(manager.incidents) == 1
        incident = list(manager.incidents.values())[0]
        assert incident.event_type == 'downtime'
        assert 'test-project' in incident.affected_projects

    @pytest.mark.asyncio
    async def test_detect_critical_vulnerability_incident(self, tmp_path):
        """Test detecting critical vulnerability incident"""
        config = {
            'channels': {'customer_alerts': 12345},
            'incidents': {}
        }

        mock_bot = Mock()

        with patch('src.integrations.incident_manager.Path') as mock_path:
            state_file = tmp_path / 'incidents.json'
            mock_path.return_value = state_file
            manager = IncidentManager(mock_bot, config)

        manager._create_incident_thread = AsyncMock()

        # Detect vulnerability
        await manager.detect_critical_vulnerability_incident(
            project_name='test-project',
            vulnerability_id='CVE-2024-1234',
            details={'Title': 'Critical Security Flaw'}
        )

        # Should have created an incident
        assert len(manager.incidents) == 1
        incident = list(manager.incidents.values())[0]
        assert incident.event_type == 'vulnerability'
        assert incident.severity == IncidentSeverity.CRITICAL
        assert 'CVE-2024-1234' in incident.title


class TestAutoClose:
    """Tests for auto-closing resolved incidents"""

    @pytest.mark.asyncio
    async def test_auto_close_old_incidents(self, tmp_path):
        """Test auto-closing old resolved incidents"""
        config = {
            'channels': {'customer_alerts': 12345},
            'incidents': {'auto_close_hours': 24}
        }

        mock_bot = Mock()

        with patch('src.integrations.incident_manager.Path') as mock_path:
            state_file = tmp_path / 'incidents.json'
            mock_path.return_value = state_file
            manager = IncidentManager(mock_bot, config)

        manager._post_thread_update = AsyncMock()
        manager._update_incident_message = AsyncMock()

        # Create and resolve incident
        incident = await manager.create_incident(
            title='Old Incident', description='Test',
            severity=IncidentSeverity.MEDIUM,
            affected_projects=['project'], event_type='test',
            auto_create_thread=False
        )

        await manager.resolve_incident(incident.id, 'Fixed', 'admin')

        # Manually set resolved_at to 25 hours ago
        incident.resolved_at = datetime.utcnow() - timedelta(hours=25)

        # Run auto-close
        await manager.auto_close_old_incidents()

        # Should be closed now
        assert incident.status == IncidentStatus.CLOSED
