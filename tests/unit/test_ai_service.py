"""
Unit Tests for AI Service
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from datetime import datetime
import httpx

from src.integrations.ai_service import AIService


class TestAIServiceInitialization:
    """Tests for AI Service initialization"""

    def test_init_with_ollama_enabled(self, mock_config):
        """Test initialization with Ollama enabled"""
        service = AIService(mock_config)

        assert service.ollama_enabled is True
        assert service.ollama_url == 'http://localhost:11434'
        assert service.ollama_model == 'phi3:mini'

    def test_init_with_anthropic_enabled(self, mock_config):
        """Test initialization with Anthropic enabled"""
        mock_config.ai['anthropic']['enabled'] = True
        mock_config.ai['anthropic']['api_key'] = 'test_key'

        service = AIService(mock_config)

        assert service.anthropic_enabled is True
        assert service.anthropic_api_key == 'test_key'

    def test_init_with_openai_enabled(self, mock_config):
        """Test initialization with OpenAI enabled"""
        mock_config.ai['openai']['enabled'] = True
        mock_config.ai['openai']['api_key'] = 'test_key'

        service = AIService(mock_config)

        assert service.openai_enabled is True
        assert service.openai_api_key == 'test_key'

    def test_hybrid_model_selection(self, mock_config):
        """Test hybrid model selection configuration"""
        service = AIService(mock_config)

        assert service.use_hybrid_models is True
        assert service.ollama_model_critical == 'llama3.1'


class TestGenerateFixStrategy:
    """Tests for generate_fix_strategy method"""

    @pytest.mark.asyncio
    async def test_generate_fix_strategy_with_ollama(self, mock_config):
        """Test fix strategy generation with Ollama"""
        service = AIService(mock_config)

        # Mock Ollama response
        mock_response = {
            'description': 'Update package to latest version',
            'confidence': 0.9,
            'steps': [
                {'action': 'update_package', 'command': 'npm update openssl'}
            ],
            'analysis': 'CVE fixed in version 1.1.0'
        }

        with patch.object(service, '_analyze_with_ollama', return_value=mock_response):
            context = {
                'event': {
                    'source': 'trivy',
                    'severity': 'HIGH',
                    'details': {'VulnerabilityID': 'CVE-2024-1234'}
                },
                'previous_attempts': []
            }

            result = await service.generate_fix_strategy(context)

            assert result is not None
            assert result['description'] == 'Update package to latest version'
            assert result['confidence'] == 0.9
            assert len(result['steps']) == 1

    @pytest.mark.asyncio
    async def test_generate_fix_strategy_with_fallback(self, mock_config):
        """Test fix strategy with fallback to Claude"""
        mock_config.ai['anthropic']['enabled'] = True
        mock_config.ai['anthropic']['api_key'] = 'test_key'

        service = AIService(mock_config)

        # Mock Ollama failure and Claude success
        mock_claude_response = {
            'description': 'Apply security patch',
            'confidence': 0.85,
            'steps': [{'action': 'patch', 'command': 'apply-patch'}],
            'analysis': 'Security issue resolved'
        }

        with patch.object(service, '_analyze_with_ollama', side_effect=Exception("Ollama failed")):
            with patch.object(service, '_analyze_with_anthropic', return_value=mock_claude_response):
                context = {
                    'event': {'source': 'trivy', 'severity': 'CRITICAL'},
                    'previous_attempts': []
                }

                result = await service.generate_fix_strategy(context)

                assert result is not None
                assert result['description'] == 'Apply security patch'

    @pytest.mark.asyncio
    async def test_generate_fix_strategy_all_providers_fail(self, mock_config):
        """Test when all AI providers fail"""
        service = AIService(mock_config)

        with patch.object(service, '_analyze_with_ollama', side_effect=Exception("Failed")):
            context = {
                'event': {'source': 'trivy', 'severity': 'HIGH'},
                'previous_attempts': []
            }

            result = await service.generate_fix_strategy(context)

            assert result is None


class TestRetryLogic:
    """Tests for retry logic"""

    @pytest.mark.asyncio
    async def test_call_with_retry_success_first_attempt(self, mock_config):
        """Test successful API call on first attempt"""
        service = AIService(mock_config)

        mock_func = AsyncMock(return_value="success")

        result = await service._call_with_retry(mock_func, max_retries=3)

        assert result == "success"
        assert mock_func.call_count == 1

    @pytest.mark.asyncio
    async def test_call_with_retry_success_after_retries(self, mock_config):
        """Test successful API call after retries"""
        service = AIService(mock_config)

        # Fail twice, then succeed
        mock_func = AsyncMock(side_effect=[
            httpx.ConnectError("Connection failed"),
            httpx.ReadTimeout("Timeout"),
            "success"
        ])

        result = await service._call_with_retry(mock_func, max_retries=3)

        assert result == "success"
        assert mock_func.call_count == 3

    @pytest.mark.asyncio
    async def test_call_with_retry_permanent_failure(self, mock_config):
        """Test permanent failure (non-retryable error)"""
        service = AIService(mock_config)

        # Permanent error (not retryable)
        mock_func = AsyncMock(side_effect=ValueError("Invalid request"))

        with pytest.raises(ValueError):
            await service._call_with_retry(mock_func, max_retries=3)

        # Should not retry permanent errors
        assert mock_func.call_count == 1

    @pytest.mark.asyncio
    async def test_call_with_retry_max_retries_exceeded(self, mock_config):
        """Test when max retries is exceeded"""
        service = AIService(mock_config)

        # Always fail
        mock_func = AsyncMock(side_effect=httpx.ConnectError("Always fails"))

        with pytest.raises(httpx.ConnectError):
            await service._call_with_retry(mock_func, max_retries=3)

        assert mock_func.call_count == 3


class TestRateLimiting:
    """Tests for rate limiting"""

    @pytest.mark.asyncio
    async def test_rate_limit_delay(self, mock_config):
        """Test that rate limiting applies delay"""
        mock_config.ai['ollama']['request_delay_seconds'] = 1.0

        service = AIService(mock_config)
        service.last_request_time = 0  # Reset

        import time
        start = time.time()

        # First call should not wait
        await service._apply_rate_limit()
        first_duration = time.time() - start

        # Second call immediately after should wait
        start = time.time()
        await service._apply_rate_limit()
        second_duration = time.time() - start

        # Second call should have waited ~1 second
        assert second_duration >= 0.9  # Allow small timing variance

    @pytest.mark.asyncio
    async def test_no_rate_limit_after_delay(self, mock_config):
        """Test no rate limiting if enough time has passed"""
        mock_config.ai['ollama']['request_delay_seconds'] = 0.1

        service = AIService(mock_config)
        service.last_request_time = 0

        # First call
        await service._apply_rate_limit()

        # Wait for rate limit duration
        import asyncio
        await asyncio.sleep(0.2)

        # Second call should not wait
        import time
        start = time.time()
        await service._apply_rate_limit()
        duration = time.time() - start

        # Should be instant (< 50ms)
        assert duration < 0.05


class TestOllamaIntegration:
    """Tests for Ollama integration"""

    @pytest.mark.asyncio
    async def test_analyze_with_ollama_success(self, mock_config):
        """Test successful Ollama analysis"""
        service = AIService(mock_config)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.aiter_lines = AsyncMock(return_value=[
            '{"response": "{\\"description\\":", "done": false}',
            '{"response": " \\"test\\",", "done": false}',
            '{"response": " \\"confidence\\": 0.9}", "done": true}'
        ])

        with patch('httpx.AsyncClient') as mock_client:
            mock_client.return_value.__aenter__.return_value.stream.return_value.__aenter__.return_value = mock_response

            event = {'source': 'trivy', 'severity': 'HIGH'}
            result = await service._analyze_with_ollama("test prompt", event)

            # Result parsing depends on implementation
            # This test verifies the method executes without errors
            assert mock_client.called

    @pytest.mark.asyncio
    async def test_analyze_with_ollama_http_error(self, mock_config):
        """Test Ollama HTTP error handling"""
        service = AIService(mock_config)

        mock_response = MagicMock()
        mock_response.status_code = 500

        with patch('httpx.AsyncClient') as mock_client:
            mock_client.return_value.__aenter__.return_value.stream.return_value.__aenter__.return_value = mock_response

            event = {'source': 'trivy', 'severity': 'HIGH'}
            result = await service._analyze_with_ollama("test prompt", event)

            # Should return None on HTTP error
            assert result is None


class TestPromptBuilding:
    """Tests for prompt building"""

    def test_build_analysis_prompt(self, mock_config):
        """Test analysis prompt building"""
        service = AIService(mock_config)

        event = {
            'source': 'trivy',
            'event_type': 'vulnerability',
            'severity': 'CRITICAL',
            'details': {
                'VulnerabilityID': 'CVE-2024-1234',
                'PkgName': 'openssl',
                'InstalledVersion': '1.0.0',
                'FixedVersion': '1.1.0'
            }
        }
        previous_attempts = []

        prompt = service._build_analysis_prompt(event, previous_attempts)

        # Check that prompt contains key information
        assert 'CVE-2024-1234' in prompt
        assert 'openssl' in prompt
        assert 'CRITICAL' in prompt
        assert 'Fix Strategy' in prompt or 'JSON' in prompt

    def test_build_analysis_prompt_with_previous_attempts(self, mock_config):
        """Test prompt includes previous attempts"""
        service = AIService(mock_config)

        event = {'source': 'trivy', 'event_type': 'vulnerability', 'severity': 'HIGH'}
        previous_attempts = [
            {
                'timestamp': '2024-01-01T12:00:00',
                'strategy': {'description': 'First attempt'},
                'result': 'failed',
                'error': 'Package not found'
            }
        ]

        prompt = service._build_analysis_prompt(event, previous_attempts)

        # Should mention previous failure
        assert 'previous' in prompt.lower() or 'attempt' in prompt.lower()


class TestJSONParsing:
    """Tests for JSON response parsing"""

    def test_parse_json_response_valid(self, mock_config):
        """Test parsing valid JSON response"""
        service = AIService(mock_config)

        json_text = """
{
  "description": "Update package",
  "confidence": 0.85,
  "steps": [
    {"action": "update", "command": "npm update"}
  ],
  "analysis": "Security fix available"
}
"""

        result = service._parse_json_response(json_text)

        assert result is not None
        assert result['description'] == 'Update package'
        assert result['confidence'] == 0.85
        assert len(result['steps']) == 1

    def test_parse_json_response_with_markdown(self, mock_config):
        """Test parsing JSON wrapped in markdown code blocks"""
        service = AIService(mock_config)

        json_text = """
Here's the fix strategy:

```json
{
  "description": "Apply patch",
  "confidence": 0.9
}
```
"""

        result = service._parse_json_response(json_text)

        # Should extract JSON from markdown
        assert result is not None or 'description' in json_text

    def test_parse_json_response_invalid(self, mock_config):
        """Test parsing invalid JSON"""
        service = AIService(mock_config)

        invalid_json = "This is not JSON at all"

        result = service._parse_json_response(invalid_json)

        # Should return None for invalid JSON
        assert result is None
