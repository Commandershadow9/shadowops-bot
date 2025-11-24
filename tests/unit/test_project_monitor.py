"""
Unit Tests for Project Monitor
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from datetime import datetime, timedelta
import aiohttp

from src.integrations.project_monitor import ProjectMonitor, ProjectStatus


class TestProjectStatusClass:
    """Tests for ProjectStatus class"""

    def test_init(self):
        """Test ProjectStatus initialization"""
        config = {
            'url': 'https://example.com/health',
            'expected_status': 200,
            'check_interval': 60,
            'timeout': 10
        }

        status = ProjectStatus('test-project', config)

        assert status.name == 'test-project'
        assert status.url == 'https://example.com/health'
        assert status.expected_status == 200
        assert status.check_interval == 60
        assert status.is_online is False
        assert status.total_checks == 0

    def test_update_online(self):
        """Test updating status when health check succeeds"""
        config = {'url': 'https://example.com'}
        status = ProjectStatus('test-project', config)

        # First successful check
        was_recovering = status.update_online(response_time_ms=150.0)

        assert status.is_online is True
        assert status.total_checks == 1
        assert status.successful_checks == 1
        assert status.failed_checks == 0
        assert status.consecutive_failures == 0
        assert len(status.response_times) == 1
        assert was_recovering is False  # Was already offline, so this is initial state

    def test_update_offline(self):
        """Test updating status when health check fails"""
        config = {'url': 'https://example.com'}
        status = ProjectStatus('test-project', config)

        # Set as online first
        status.update_online(100.0)

        # Now fail
        was_new_incident = status.update_offline('Connection timeout')

        assert status.is_online is False
        assert status.total_checks == 2
        assert status.successful_checks == 1
        assert status.failed_checks == 1
        assert status.consecutive_failures == 1
        assert status.last_error == 'Connection timeout'
        assert was_new_incident is True  # This was a new failure

    def test_uptime_percentage(self):
        """Test uptime percentage calculation"""
        config = {'url': 'https://example.com'}
        status = ProjectStatus('test-project', config)

        # 8 successful, 2 failed = 80% uptime
        for _ in range(8):
            status.update_online(100.0)

        for _ in range(2):
            status.update_offline('Error')

        assert status.total_checks == 10
        assert status.successful_checks == 8
        assert status.failed_checks == 2
        assert status.uptime_percentage == 80.0

    def test_average_response_time(self):
        """Test average response time calculation"""
        config = {'url': 'https://example.com'}
        status = ProjectStatus('test-project', config)

        # Add response times: 100, 150, 200
        status.update_online(100.0)
        status.update_online(150.0)
        status.update_online(200.0)

        # Average should be 150
        assert status.average_response_time == 150.0

    def test_current_downtime_duration(self):
        """Test current downtime duration calculation"""
        config = {'url': 'https://example.com'}
        status = ProjectStatus('test-project', config)

        # Set online first
        status.update_online(100.0)

        # Go offline
        status.update_offline('Error')

        # Should have downtime duration
        duration = status.current_downtime_duration
        assert duration is not None
        assert isinstance(duration, timedelta)


class TestProjectMonitorInit:
    """Tests for ProjectMonitor initialization"""

    def test_init_with_projects(self):
        """Test initialization with project configurations"""
        config = MagicMock()
        config.projects = {
            'project1': {
                'enabled': True,
                'monitor': {
                    'enabled': True,
                    'url': 'https://project1.com/health',
                    'expected_status': 200,
                    'check_interval': 60
                }
            },
            'project2': {
                'enabled': True,
                'monitor': {
                    'enabled': False  # Monitoring disabled
                }
            },
            'project3': {
                'enabled': False  # Project disabled
            }
        }
        config.customer_status_channel = 12345

        mock_bot = Mock()
        monitor = ProjectMonitor(mock_bot, config)

        # Only project1 should be monitored
        assert len(monitor.projects) == 1
        assert 'project1' in monitor.projects


class TestHealthChecks:
    """Tests for health check functionality"""

    @pytest.mark.asyncio
    async def test_check_project_health_success(self):
        """Test successful health check"""
        config = MagicMock()
        config.projects = {
            'test-project': {
                'enabled': True,
                'monitor': {
                    'enabled': True,
                    'url': 'https://example.com/health',
                    'expected_status': 200,
                    'check_interval': 60,
                    'timeout': 10
                }
            }
        }
        config.customer_status_channel = 12345

        mock_bot = Mock()
        monitor = ProjectMonitor(mock_bot, config)

        project = monitor.projects['test-project']

        # Mock successful HTTP response
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.__aenter__.return_value = mock_response
        mock_response.__aexit__.return_value = None

        mock_session = MagicMock()
        mock_session.get.return_value = mock_response
        mock_session.__aenter__.return_value = mock_session
        mock_session.__aexit__.return_value = None

        with patch('aiohttp.ClientSession', return_value=mock_session):
            await monitor._check_project_health(project)

        assert project.is_online is True
        assert project.successful_checks == 1

    @pytest.mark.asyncio
    async def test_check_project_health_failure(self):
        """Test failed health check"""
        config = MagicMock()
        config.projects = {
            'test-project': {
                'enabled': True,
                'monitor': {
                    'enabled': True,
                    'url': 'https://example.com/health',
                    'expected_status': 200,
                    'check_interval': 60,
                    'timeout': 10
                }
            }
        }
        config.customer_status_channel = 12345

        mock_bot = Mock()
        monitor = ProjectMonitor(mock_bot, config)
        monitor._send_incident_alert = AsyncMock()

        project = monitor.projects['test-project']

        # Set project as online first
        project.update_online(100.0)

        # Mock failed HTTP response
        mock_session = MagicMock()
        mock_session.get.side_effect = aiohttp.ClientError("Connection failed")
        mock_session.__aenter__.return_value = mock_session
        mock_session.__aexit__.return_value = None

        with patch('aiohttp.ClientSession', return_value=mock_session):
            await monitor._check_project_health(project)

        assert project.is_online is False
        assert project.failed_checks == 1
        # Should send alert on new incident
        monitor._send_incident_alert.assert_called_once()

    @pytest.mark.asyncio
    async def test_check_project_health_timeout(self):
        """Test health check timeout"""
        config = MagicMock()
        config.projects = {
            'test-project': {
                'enabled': True,
                'monitor': {
                    'enabled': True,
                    'url': 'https://example.com/health',
                    'expected_status': 200,
                    'check_interval': 60,
                    'timeout': 1  # Short timeout
                }
            }
        }
        config.customer_status_channel = 12345

        mock_bot = Mock()
        monitor = ProjectMonitor(mock_bot, config)
        monitor._send_incident_alert = AsyncMock()

        project = monitor.projects['test-project']
        project.update_online(100.0)  # Set online first

        # Mock timeout
        import asyncio
        mock_session = MagicMock()
        mock_session.get.side_effect = asyncio.TimeoutError()
        mock_session.__aenter__.return_value = mock_session
        mock_session.__aexit__.return_value = None

        with patch('aiohttp.ClientSession', return_value=mock_session):
            await monitor._check_project_health(project)

        assert project.is_online is False
        assert 'Timeout' in project.last_error


class TestAlerts:
    """Tests for alert functionality"""

    @pytest.mark.asyncio
    async def test_send_incident_alert(self):
        """Test sending incident alert to Discord"""
        config = MagicMock()
        config.projects = {}
        config.customer_alerts_channel = 12345

        mock_bot = Mock()
        mock_channel = AsyncMock()
        mock_bot.get_channel.return_value = mock_channel

        monitor = ProjectMonitor(mock_bot, config)

        project_status = ProjectStatus('test-project', {'url': 'https://example.com'})
        project_status.update_offline('Connection timeout')

        await monitor._send_incident_alert(project_status, 'Connection timeout')

        # Should send message to channel
        mock_channel.send.assert_called_once()
        # Check that embed was created
        call_args = mock_channel.send.call_args
        assert 'embed' in call_args[1]

    @pytest.mark.asyncio
    async def test_send_recovery_alert(self):
        """Test sending recovery alert to Discord"""
        config = MagicMock()
        config.projects = {}
        config.customer_alerts_channel = 12345
        
        mock_bot = Mock()
        mock_channel = AsyncMock()
        mock_bot.get_channel.return_value = mock_channel

        monitor = ProjectMonitor(mock_bot, config)

        project_status = ProjectStatus('test-project', {'url': 'https://example.com'})
        project_status.update_online(150.0)

        await monitor._send_recovery_alert(project_status)

        # Should send message to channel
        mock_channel.send.assert_called_once()


class TestStatusRetrieval:
    """Tests for status retrieval methods"""

    def test_get_project_status(self):
        """Test getting status for specific project"""
        config = MagicMock()
        config.projects = {
            'test-project': {
                'enabled': True,
                'monitor': {
                    'enabled': True,
                    'url': 'https://example.com/health'
                }
            }
        }
        config.customer_status_channel = 12345

        mock_bot = Mock()
        monitor = ProjectMonitor(mock_bot, config)

        # Update project status
        project = monitor.projects['test-project']
        project.update_online(100.0)

        # Get status
        status = monitor.get_project_status('test-project')

        assert status is not None
        assert status['name'] == 'test-project'
        assert status['is_online'] is True

    def test_get_all_projects_status(self):
        """Test getting status for all projects"""
        config = MagicMock()
        config.projects = {
            'project1': {
                'enabled': True,
                'monitor': {
                    'enabled': True,
                    'url': 'https://project1.com'
                }
            },
            'project2': {
                'enabled': True,
                'monitor': {
                    'enabled': True,
                    'url': 'https://project2.com'
                }
            }
        }
        config.customer_status_channel = 12345

        mock_bot = Mock()
        monitor = ProjectMonitor(mock_bot, config)

        # Update statuses
        monitor.projects['project1'].update_online(100.0)
        monitor.projects['project2'].update_offline('Error')

        # Get all statuses
        all_statuses = monitor.get_all_projects_status()

        assert len(all_statuses) == 2
        assert any(s['name'] == 'project1' and s['is_online'] for s in all_statuses)
        assert any(s['name'] == 'project2' and not s['is_online'] for s in all_statuses)
