"""
Pytest Configuration and Shared Fixtures
"""

import pytest
import asyncio
import tempfile
import shutil
from pathlib import Path
from unittest.mock import Mock, AsyncMock, MagicMock
from datetime import datetime


# ============================================================================
# EVENT LOOP FIXTURES
# ============================================================================

@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests"""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


# ============================================================================
# MOCK CONFIG FIXTURES
# ============================================================================

@pytest.fixture
def mock_config():
    """Mock configuration object"""
    config = Mock()

    # Discord config
    config.discord = {
        'token': 'test_token',
        'guild_id': 12345,
    }

    # Channels config
    config.channels = {
        'ai_learning': 111,
        'security_alerts': 222,
        'approval_requests': 333,
        'execution_logs': 444,
    }

    # AI config
    config.ai = {
        'ollama': {
            'enabled': True,
            'url': 'http://localhost:11434',
            'model': 'phi3:mini',
            'model_critical': 'llama3.1',
            'hybrid_models': True,
            'request_delay_seconds': 4.0,
        },
        'anthropic': {
            'enabled': False,
            'api_key': None,
            'model': 'claude-3-5-sonnet-20241022',
        },
        'openai': {
            'enabled': False,
            'api_key': None,
            'model': 'gpt-4o',
        },
    }

    # Auto-remediation config
    config.auto_remediation = {
        'enabled': True,
        'dry_run': False,
        'approval_mode': 'paranoid',
        'max_batch_size': 10,
        'collection_window_seconds': 300,
        'scan_intervals': {
            'trivy': 21600,
            'crowdsec': 30,
            'fail2ban': 30,
            'aide': 900,
        },
    }

    # Projects config
    config.projects = {
        'shadowops-bot': {
            'enabled': True,
            'path': '/home/user/shadowops-bot',
        },
        'guildscout': {
            'enabled': True,
            'path': '/home/user/guildscout',
        },
        'nexus': {
            'enabled': False,
            'path': '/home/user/nexus',
        },
    }

    # Log paths
    config.log_paths = {
        'fail2ban': '/var/log/fail2ban/fail2ban.log',
        'crowdsec': '/var/log/crowdsec/crowdsec.log',
        'docker': '/var/log/docker.log',
        'shadowops': 'logs/shadowops.log',
    }

    return config


@pytest.fixture
def mock_config_minimal():
    """Minimal mock configuration for basic tests"""
    config = Mock()
    config.discord = {'token': 'test', 'guild_id': 1}
    config.channels = {}
    config.ai = {'ollama': {'enabled': True, 'url': 'http://localhost:11434'}}
    config.auto_remediation = {'enabled': True, 'dry_run': True}
    return config


# ============================================================================
# MOCK INTEGRATIONS
# ============================================================================

@pytest.fixture
def mock_discord_client():
    """Mock Discord client"""
    client = AsyncMock()
    client.user = Mock()
    client.user.id = 123456789
    client.user.name = "TestBot"
    return client


@pytest.fixture
def mock_command_executor():
    """Mock CommandExecutor"""
    executor = AsyncMock()

    # Default success result
    result = Mock()
    result.success = True
    result.returncode = 0
    result.stdout = ""
    result.stderr = ""
    result.error_message = None

    executor.execute.return_value = result
    executor.execute_async.return_value = result

    return executor


@pytest.fixture
def mock_ai_service():
    """Mock AI Service"""
    service = AsyncMock()

    # Default strategy response
    service.generate_fix_strategy.return_value = {
        'description': 'Test fix strategy',
        'confidence': 0.85,
        'steps': [
            {'action': 'test_action', 'command': 'echo test'}
        ],
        'analysis': 'Test analysis',
        'ai_model': 'test-model',
        'ai_provider': 'test',
    }

    return service


@pytest.fixture
def mock_context_manager():
    """Mock ContextManager"""
    manager = Mock()
    manager.get_project_context.return_value = "Test project context"
    manager.get_infrastructure_context.return_value = "Test infrastructure context"
    manager.get_do_not_touch_list.return_value = ["/etc/passwd", "/etc/shadow"]
    return manager


# ============================================================================
# SAMPLE DATA FIXTURES
# ============================================================================

@pytest.fixture
def sample_security_event():
    """Sample SecurityEvent for testing"""
    from src.integrations.event_watcher import SecurityEvent

    return SecurityEvent(
        source='trivy',
        event_type='vulnerability',
        severity='CRITICAL',
        details={
            'VulnerabilityID': 'CVE-2024-1234',
            'PkgName': 'openssl',
            'InstalledVersion': '1.0.0',
            'FixedVersion': '1.1.0',
            'total_critical': 5,
            'total_high': 10,
        },
        is_persistent=True,
    )


@pytest.fixture
def sample_trivy_scan():
    """Sample Trivy scan result"""
    return {
        'Results': [
            {
                'Target': 'test-image:latest',
                'Vulnerabilities': [
                    {
                        'VulnerabilityID': 'CVE-2024-1234',
                        'PkgName': 'openssl',
                        'InstalledVersion': '1.0.0',
                        'FixedVersion': '1.1.0',
                        'Severity': 'CRITICAL',
                        'Title': 'Test vulnerability',
                        'Description': 'Test description',
                    },
                    {
                        'VulnerabilityID': 'CVE-2024-5678',
                        'PkgName': 'curl',
                        'InstalledVersion': '7.0.0',
                        'FixedVersion': '7.1.0',
                        'Severity': 'HIGH',
                        'Title': 'Another vulnerability',
                        'Description': 'Another description',
                    },
                ],
            }
        ]
    }


@pytest.fixture
def sample_git_commits():
    """Sample git commit history"""
    return [
        {
            'hash': 'abc123',
            'author': 'Test User',
            'date': '2024-01-01 12:00:00',
            'message': 'fix: Security vulnerability in auth module',
            'files': ['src/auth.py'],
        },
        {
            'hash': 'def456',
            'author': 'Test User',
            'date': '2024-01-02 14:00:00',
            'message': 'feat: Add new feature',
            'files': ['src/feature.py'],
        },
    ]


# ============================================================================
# TEMPORARY DIRECTORY FIXTURES
# ============================================================================

@pytest.fixture
def temp_dir():
    """Temporary directory for tests"""
    temp = tempfile.mkdtemp()
    yield Path(temp)
    shutil.rmtree(temp)


@pytest.fixture
def temp_config_file(temp_dir):
    """Temporary config file"""
    config_path = temp_dir / "config.yaml"
    config_content = """
discord:
  token: test_token
  guild_id: 12345

channels:
  ai_learning: 111
  security_alerts: 222

ai:
  ollama:
    enabled: true
    url: http://localhost:11434
"""
    config_path.write_text(config_content)
    return config_path


# ============================================================================
# MOCK SUBPROCESS FIXTURES
# ============================================================================

@pytest.fixture
def mock_subprocess_success(monkeypatch):
    """Mock subprocess.run to return success"""
    def mock_run(*args, **kwargs):
        result = Mock()
        result.returncode = 0
        result.stdout = "Success"
        result.stderr = ""
        return result

    monkeypatch.setattr("subprocess.run", mock_run)
    return mock_run


@pytest.fixture
def mock_subprocess_failure(monkeypatch):
    """Mock subprocess.run to return failure"""
    def mock_run(*args, **kwargs):
        result = Mock()
        result.returncode = 1
        result.stdout = ""
        result.stderr = "Error"
        return result

    monkeypatch.setattr("subprocess.run", mock_run)
    return mock_run


# ============================================================================
# DATABASE FIXTURES
# ============================================================================

@pytest.fixture
def mock_knowledge_base(temp_dir):
    """Mock knowledge base with temporary database"""
    from src.integrations.knowledge_base import KnowledgeBase

    db_path = temp_dir / "test_knowledge.db"
    kb = KnowledgeBase(db_path=str(db_path))

    yield kb

    # Cleanup
    kb.close()


# ============================================================================
# ASYNC HELPERS
# ============================================================================

@pytest.fixture
def async_return():
    """Helper to create async functions that return a value"""
    def _async_return(value):
        async def func(*args, **kwargs):
            return value
        return func
    return _async_return


@pytest.fixture
def async_raise():
    """Helper to create async functions that raise an exception"""
    def _async_raise(exception):
        async def func(*args, **kwargs):
            raise exception
        return func
    return _async_raise
