"""
Unit Tests für AI Engine — Dual-Engine (Codex CLI + Claude CLI)
Ersetzt das alte Ollama-basierte AIService.
"""

import time
import pytest
import json
import asyncio
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from pathlib import Path


# ============================================================================
# TEST CONFIG FIXTURE
# ============================================================================

@pytest.fixture
def ai_config():
    """AI-Engine-spezifische Config mit Dual-Engine Struktur"""
    config = Mock()
    config.ai = {
        'primary': {
            'engine': 'codex',
            'models': {
                'fast': 'gpt-4o',
                'standard': 'gpt-5.3-codex',
                'thinking': 'o3',
            },
            'timeout': 60,
            'timeout_thinking': 300,
        },
        'fallback': {
            'engine': 'claude',
            'cli_path': '/home/cmdshadow/.local/bin/claude',
            'models': {
                'fast': 'claude-sonnet-4-6',
                'standard': 'claude-sonnet-4-6',
                'thinking': 'claude-opus-4-6',
            },
            'timeout': 120,
        },
        'routing': {
            'critical_analysis': {
                'engine': 'codex',
                'model_class': 'thinking',
                'schema': 'fix_strategy',
            },
            'high_analysis': {
                'engine': 'codex',
                'model_class': 'standard',
                'schema': 'fix_strategy',
            },
            'medium_analysis': {
                'engine': 'codex',
                'model_class': 'standard',
                'schema': 'fix_strategy',
            },
            'low_analysis': {
                'engine': 'codex',
                'model_class': 'fast',
                'schema': 'fix_strategy',
            },
            'verify': {
                'engine': 'claude',
                'model_class': 'thinking',
                'schema': 'fix_strategy',
            },
            'patch_notes': {
                'engine': 'codex',
                'model_class': 'standard',
                'schema': 'patch_notes',
            },
            'incident': {
                'engine': 'codex',
                'model_class': 'thinking',
                'schema': 'incident_analysis',
            },
        },
        'verification': {
            'enabled': True,
            'engine': 'claude',
            'model': 'claude-opus-4-6',
        },
        'queue': {
            'max_concurrent': 3,
            'retry_attempts': 2,
        },
    }
    config.channels = {
        'ai_learning': 111,
        'security_alerts': 222,
    }
    return config


@pytest.fixture
def schemas_dir():
    """Pfad zum Schemas-Verzeichnis"""
    return Path('/home/cmdshadow/shadowops-bot/src/schemas')


# ============================================================================
# MOCK PROCESS HELPERS
# ============================================================================

def make_mock_process(stdout: str = '', stderr: str = '', returncode: int = 0):
    """Erstellt einen Mock-asyncio-Process"""
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(
        stdout.encode('utf-8'),
        stderr.encode('utf-8'),
    ))
    proc.returncode = returncode
    return proc


# ============================================================================
# TEST TASK ROUTER
# ============================================================================

class TestTaskRouter:
    """Tests für den TaskRouter — Severity -> Engine/Model Routing"""

    def test_critical_routes_to_codex_thinking(self, ai_config, schemas_dir):
        """CRITICAL Events -> Codex mit Thinking-Modell (o3)"""
        from src.integrations.ai_engine import TaskRouter

        router = TaskRouter(ai_config.ai, schemas_dir)
        route = router.get_route('CRITICAL', 'analysis')

        assert route['engine'] == 'codex'
        assert route['model'] == 'o3'
        assert route['model_class'] == 'thinking'
        assert 'fix_strategy.json' in str(route['schema_path'])

    def test_high_routes_to_codex_standard(self, ai_config, schemas_dir):
        """HIGH Events -> Codex mit Standard-Modell"""
        from src.integrations.ai_engine import TaskRouter

        router = TaskRouter(ai_config.ai, schemas_dir)
        route = router.get_route('HIGH', 'analysis')

        assert route['engine'] == 'codex'
        assert route['model'] == 'gpt-5.3-codex'
        assert route['model_class'] == 'standard'

    def test_low_routes_to_codex_fast(self, ai_config, schemas_dir):
        """LOW Events -> Codex mit Fast-Modell (gpt-4o)"""
        from src.integrations.ai_engine import TaskRouter

        router = TaskRouter(ai_config.ai, schemas_dir)
        route = router.get_route('LOW', 'analysis')

        assert route['engine'] == 'codex'
        assert route['model'] == 'gpt-4o'
        assert route['model_class'] == 'fast'

    def test_verify_routes_to_claude(self, ai_config, schemas_dir):
        """Verification Tasks -> Claude mit Opus"""
        from src.integrations.ai_engine import TaskRouter

        router = TaskRouter(ai_config.ai, schemas_dir)
        route = router.get_route('HIGH', 'verify')

        assert route['engine'] == 'claude'
        assert route['model'] == 'claude-opus-4-6'
        assert route['model_class'] == 'thinking'

    def test_patch_notes_routing(self, ai_config, schemas_dir):
        """Patch Notes -> Codex mit Standard und patch_notes Schema"""
        from src.integrations.ai_engine import TaskRouter

        router = TaskRouter(ai_config.ai, schemas_dir)
        route = router.get_route('MEDIUM', 'patch_notes')

        assert route['engine'] == 'codex'
        assert 'patch_notes.json' in str(route['schema_path'])

    def test_incident_routing(self, ai_config, schemas_dir):
        """Incident Analysis -> Codex Thinking mit incident Schema"""
        from src.integrations.ai_engine import TaskRouter

        router = TaskRouter(ai_config.ai, schemas_dir)
        route = router.get_route('CRITICAL', 'incident')

        assert route['engine'] == 'codex'
        assert 'incident_analysis.json' in str(route['schema_path'])

    def test_default_routing_unknown_severity(self, ai_config, schemas_dir):
        """Unbekannte Severity -> Fallback auf Standard-Routing"""
        from src.integrations.ai_engine import TaskRouter

        router = TaskRouter(ai_config.ai, schemas_dir)
        route = router.get_route('UNKNOWN', 'analysis')

        # Sollte nicht crashen, Standard-Routing greifen
        assert route is not None
        assert route['engine'] in ('codex', 'claude')

    def test_default_routing_unknown_task(self, ai_config, schemas_dir):
        """Unbekannter Task-Typ -> Fallback auf Standard-Routing"""
        from src.integrations.ai_engine import TaskRouter

        router = TaskRouter(ai_config.ai, schemas_dir)
        route = router.get_route('HIGH', 'unknown_task_type')

        assert route is not None
        assert route['engine'] in ('codex', 'claude')

    def test_schema_path_resolution(self, ai_config, schemas_dir):
        """Schema-Pfade werden korrekt aufgeloest"""
        from src.integrations.ai_engine import TaskRouter

        router = TaskRouter(ai_config.ai, schemas_dir)

        # fix_strategy
        route_fix = router.get_route('HIGH', 'analysis')
        assert route_fix['schema_path'].name == 'fix_strategy.json'

        # patch_notes
        route_patch = router.get_route('MEDIUM', 'patch_notes')
        assert route_patch['schema_path'].name == 'patch_notes.json'


# ============================================================================
# TEST CODEX PROVIDER
# ============================================================================

class TestCodexProvider:
    """Tests fuer den Codex CLI Provider"""

    @pytest.mark.asyncio
    async def test_query_returns_parsed_json(self, ai_config):
        """Codex CLI gibt gueltiges JSON zurueck -> wird korrekt geparst"""
        from src.integrations.ai_engine import CodexProvider

        provider = CodexProvider(ai_config.ai['primary'])
        mock_result = {
            'description': 'Paket aktualisieren',
            'confidence': 0.92,
            'steps': [{'action': 'update', 'command': 'apt upgrade openssl', 'risk_level': 'low'}],
            'analysis': 'CVE behoben in Version 1.1.0',
            'severity_assessment': 'HIGH',
        }

        mock_proc = make_mock_process(stdout=json.dumps(mock_result))

        with patch('asyncio.create_subprocess_exec', return_value=mock_proc):
            result = await provider.query("Analysiere CVE-2024-1234", model='standard')

        assert result is not None
        assert result['description'] == 'Paket aktualisieren'
        assert result['confidence'] == 0.92
        assert len(result['steps']) == 1

    @pytest.mark.asyncio
    async def test_query_with_schema_includes_flag(self, ai_config, schemas_dir):
        """Bei Schema-Pfad wird --output-schema uebergeben"""
        from src.integrations.ai_engine import CodexProvider

        provider = CodexProvider(ai_config.ai['primary'])
        schema_path = schemas_dir / 'fix_strategy.json'

        mock_proc = make_mock_process(stdout='{"description": "test", "confidence": 0.5}')
        captured_args = []

        async def capture_exec(*args, **kwargs):
            captured_args.extend(args)
            return mock_proc

        with patch('asyncio.create_subprocess_exec', side_effect=capture_exec):
            await provider.query("Test", model='standard', schema_path=schema_path)

        # --output-schema muss in den Argumenten sein
        assert '--output-schema' in captured_args
        assert str(schema_path) in captured_args

    @pytest.mark.asyncio
    async def test_query_returns_none_on_error(self, ai_config):
        """Bei Fehlern wird None zurueckgegeben"""
        from src.integrations.ai_engine import CodexProvider

        provider = CodexProvider(ai_config.ai['primary'])

        mock_proc = make_mock_process(stderr='Fehler: Modell nicht verfuegbar', returncode=1)

        with patch('asyncio.create_subprocess_exec', return_value=mock_proc):
            result = await provider.query("Test", model='standard')

        assert result is None

    @pytest.mark.asyncio
    async def test_query_returns_none_on_timeout(self, ai_config):
        """Bei Timeout wird None zurueckgegeben"""
        from src.integrations.ai_engine import CodexProvider

        provider = CodexProvider(ai_config.ai['primary'])

        async def timeout_exec(*args, **kwargs):
            raise asyncio.TimeoutError()

        with patch('asyncio.create_subprocess_exec', side_effect=timeout_exec):
            result = await provider.query("Test", model='standard', timeout=5)

        assert result is None

    @pytest.mark.asyncio
    async def test_query_extracts_json_from_mixed_output(self, ai_config):
        """JSON aus Mixed-Output extrahieren (Fallback-Parser)"""
        from src.integrations.ai_engine import CodexProvider

        provider = CodexProvider(ai_config.ai['primary'])

        mixed_output = 'Analyse laeuft...\n```json\n{"description": "Fix", "confidence": 0.8}\n```\nFertig.'
        mock_proc = make_mock_process(stdout=mixed_output)

        with patch('asyncio.create_subprocess_exec', return_value=mock_proc):
            result = await provider.query("Test", model='standard')

        assert result is not None
        assert result['description'] == 'Fix'

    @pytest.mark.asyncio
    async def test_query_raw_returns_text(self, ai_config):
        """query_raw gibt Rohtext zurueck (kein JSON-Parsing)"""
        from src.integrations.ai_engine import CodexProvider

        provider = CodexProvider(ai_config.ai['primary'])

        mock_proc = make_mock_process(stdout='Das ist eine Text-Analyse ohne JSON.')

        with patch('asyncio.create_subprocess_exec', return_value=mock_proc):
            result = await provider.query_raw("Test", model='standard')

        assert result == 'Das ist eine Text-Analyse ohne JSON.'

    @pytest.mark.asyncio
    async def test_is_available(self, ai_config):
        """is_available prueft ob Codex CLI erreichbar ist"""
        from src.integrations.ai_engine import CodexProvider

        provider = CodexProvider(ai_config.ai['primary'])

        mock_proc = make_mock_process(stdout='codex 1.0.0')

        with patch('asyncio.create_subprocess_exec', return_value=mock_proc):
            available = await provider.is_available()

        assert available is True

    @pytest.mark.asyncio
    async def test_is_available_returns_false_on_error(self, ai_config):
        """is_available gibt False zurueck wenn CLI nicht verfuegbar"""
        from src.integrations.ai_engine import CodexProvider

        provider = CodexProvider(ai_config.ai['primary'])

        async def fail_exec(*args, **kwargs):
            raise FileNotFoundError("codex nicht gefunden")

        with patch('asyncio.create_subprocess_exec', side_effect=fail_exec):
            available = await provider.is_available()

        assert available is False

    @pytest.mark.asyncio
    async def test_env_excludes_claudecode(self, ai_config):
        """CLAUDECODE env var wird aus subprocess-Umgebung entfernt"""
        from src.integrations.ai_engine import CodexProvider

        provider = CodexProvider(ai_config.ai['primary'])
        captured_kwargs = {}

        async def capture_exec(*args, **kwargs):
            captured_kwargs.update(kwargs)
            return make_mock_process(stdout='{"description": "test"}')

        with patch.dict('os.environ', {'CLAUDECODE': '1', 'HOME': '/home/test'}):
            with patch('asyncio.create_subprocess_exec', side_effect=capture_exec):
                await provider.query("Test", model='standard')

        # CLAUDECODE darf NICHT in der Env sein
        env = captured_kwargs.get('env', {})
        assert 'CLAUDECODE' not in env
        # HOME muss aber noch da sein
        assert env.get('HOME') == '/home/test'

    @pytest.mark.asyncio
    async def test_thinking_model_uses_timeout_thinking(self, ai_config):
        """Thinking-Modelle nutzen den laengeren timeout_thinking"""
        from src.integrations.ai_engine import CodexProvider

        provider = CodexProvider(ai_config.ai['primary'])
        captured_timeout = []

        original_wait_for = asyncio.wait_for

        async def capture_wait_for(coro, timeout=None):
            captured_timeout.append(timeout)
            return await original_wait_for(coro, timeout=timeout)

        mock_proc = make_mock_process(stdout='{"description": "test"}')

        with patch('asyncio.create_subprocess_exec', return_value=mock_proc):
            with patch('asyncio.wait_for', side_effect=capture_wait_for):
                await provider.query("Test", model='thinking')

        # timeout_thinking = 300 aus Config
        assert captured_timeout[0] == 300


# ============================================================================
# TEST CLAUDE PROVIDER
# ============================================================================

class TestClaudeProvider:
    """Tests fuer den Claude CLI Provider"""

    @pytest.mark.asyncio
    async def test_query_parses_result_wrapper(self, ai_config):
        """Claude gibt {"result": "..."} Wrapper zurueck -> inner result parsen"""
        from src.integrations.ai_engine import ClaudeProvider

        provider = ClaudeProvider(ai_config.ai['fallback'])

        inner = {
            'description': 'Sicherheitspatch anwenden',
            'confidence': 0.88,
            'steps': [{'action': 'patch', 'command': 'apt upgrade', 'risk_level': 'medium'}],
            'analysis': 'Kritische Luecke',
            'severity_assessment': 'CRITICAL',
        }
        wrapper = {'result': json.dumps(inner)}
        mock_proc = make_mock_process(stdout=json.dumps(wrapper))

        with patch('asyncio.create_subprocess_exec', return_value=mock_proc):
            result = await provider.query("Analysiere CVE", model='standard')

        assert result is not None
        assert result['description'] == 'Sicherheitspatch anwenden'
        assert result['confidence'] == 0.88

    @pytest.mark.asyncio
    async def test_query_handles_direct_json(self, ai_config):
        """Claude gibt direkt JSON zurueck (ohne Wrapper)"""
        from src.integrations.ai_engine import ClaudeProvider

        provider = ClaudeProvider(ai_config.ai['fallback'])

        direct_json = {'description': 'Direkt', 'confidence': 0.7}
        mock_proc = make_mock_process(stdout=json.dumps(direct_json))

        with patch('asyncio.create_subprocess_exec', return_value=mock_proc):
            result = await provider.query("Test", model='standard')

        assert result is not None
        assert result['description'] == 'Direkt'

    @pytest.mark.asyncio
    async def test_query_returns_none_on_error(self, ai_config):
        """Bei Fehlern wird None zurueckgegeben"""
        from src.integrations.ai_engine import ClaudeProvider

        provider = ClaudeProvider(ai_config.ai['fallback'])
        mock_proc = make_mock_process(stderr='Claude Error', returncode=1)

        with patch('asyncio.create_subprocess_exec', return_value=mock_proc):
            result = await provider.query("Test", model='standard')

        assert result is None

    @pytest.mark.asyncio
    async def test_query_raw_returns_text(self, ai_config):
        """query_raw gibt Rohtext zurueck mit --output-format text"""
        from src.integrations.ai_engine import ClaudeProvider

        provider = ClaudeProvider(ai_config.ai['fallback'])
        captured_args = []

        mock_proc = make_mock_process(stdout='Textantwort ohne JSON')

        async def capture_exec(*args, **kwargs):
            captured_args.extend(args)
            return mock_proc

        with patch('asyncio.create_subprocess_exec', side_effect=capture_exec):
            result = await provider.query_raw("Test", model='standard')

        assert result == 'Textantwort ohne JSON'
        assert '--output-format' in captured_args
        assert 'text' in captured_args

    @pytest.mark.asyncio
    async def test_claude_cli_path_used(self, ai_config):
        """Claude CLI Pfad aus Config wird korrekt verwendet"""
        from src.integrations.ai_engine import ClaudeProvider

        provider = ClaudeProvider(ai_config.ai['fallback'])
        captured_args = []

        mock_proc = make_mock_process(stdout='{"result": "{}"}')

        async def capture_exec(*args, **kwargs):
            captured_args.extend(args)
            return mock_proc

        with patch('asyncio.create_subprocess_exec', side_effect=capture_exec):
            await provider.query("Test", model='standard')

        assert captured_args[0] == '/home/cmdshadow/.local/bin/claude'

    @pytest.mark.asyncio
    async def test_schema_in_prompt_for_claude(self, ai_config, schemas_dir):
        """Claude bekommt Schema-Anweisung im Prompt (nicht --output-schema)"""
        from src.integrations.ai_engine import ClaudeProvider

        provider = ClaudeProvider(ai_config.ai['fallback'])
        schema_path = schemas_dir / 'fix_strategy.json'
        captured_args = []

        mock_proc = make_mock_process(stdout='{"result": "{}"}')

        async def capture_exec(*args, **kwargs):
            captured_args.extend(args)
            return mock_proc

        with patch('asyncio.create_subprocess_exec', side_effect=capture_exec):
            await provider.query("Analysiere", model='standard', schema_path=schema_path)

        # --output-schema darf NICHT in den Args sein (das ist nur fuer Codex)
        assert '--output-schema' not in captured_args

    @pytest.mark.asyncio
    async def test_env_excludes_claudecode(self, ai_config):
        """CLAUDECODE env var wird auch bei Claude Provider entfernt"""
        from src.integrations.ai_engine import ClaudeProvider

        provider = ClaudeProvider(ai_config.ai['fallback'])
        captured_kwargs = {}

        async def capture_exec(*args, **kwargs):
            captured_kwargs.update(kwargs)
            return make_mock_process(stdout='{"result": "{}"}')

        with patch.dict('os.environ', {'CLAUDECODE': '1'}):
            with patch('asyncio.create_subprocess_exec', side_effect=capture_exec):
                await provider.query("Test", model='standard')

        env = captured_kwargs.get('env', {})
        assert 'CLAUDECODE' not in env


# ============================================================================
# TEST AI ENGINE (Hauptklasse)
# ============================================================================

class TestAIEngine:
    """Tests fuer die AIEngine Hauptklasse"""

    def test_init_creates_providers_and_router(self, ai_config):
        """AIEngine erstellt beide Provider und den Router"""
        from src.integrations.ai_engine import AIEngine, CodexProvider, ClaudeProvider, TaskRouter

        engine = AIEngine(ai_config)

        assert isinstance(engine.codex, CodexProvider)
        assert isinstance(engine.claude, ClaudeProvider)
        assert isinstance(engine.router, TaskRouter)

    def test_init_with_optional_dependencies(self, ai_config):
        """AIEngine akzeptiert optionale context_manager und discord_logger"""
        from src.integrations.ai_engine import AIEngine

        mock_ctx = Mock()
        mock_logger = Mock()

        engine = AIEngine(ai_config, context_manager=mock_ctx, discord_logger=mock_logger)

        assert engine.context_manager is mock_ctx
        assert engine.discord_logger is mock_logger

    def test_stats_initialized_to_zero(self, ai_config):
        """Stats-Counter starten bei 0"""
        from src.integrations.ai_engine import AIEngine

        engine = AIEngine(ai_config)

        assert engine.stats['codex_calls'] == 0
        assert engine.stats['codex_success'] == 0
        assert engine.stats['codex_failures'] == 0
        assert engine.stats['claude_calls'] == 0
        assert engine.stats['claude_success'] == 0
        assert engine.stats['claude_failures'] == 0

    @pytest.mark.asyncio
    async def test_run_analyst_claude_marks_quota_from_stdout(self, ai_config):
        """Claude-Limit aus stdout erkennen und cachen."""
        from src.integrations.ai_engine import AIEngine

        engine = AIEngine(ai_config)
        mock_proc = make_mock_process(
            stdout="You've hit your limit · resets 2pm (Europe/Berlin)",
            stderr='',
            returncode=1,
        )

        with patch('asyncio.create_subprocess_exec', return_value=mock_proc):
            result = await engine._run_analyst_claude(
                prompt='Test',
                schema_path=str(Path('/home/cmdshadow/shadowops-bot/src/schemas/analyst_session.json')),
                model='claude-opus-4-6',
                timeout=5,
                max_turns=1,
            )

        assert result is None
        assert engine.is_claude_quota_exhausted() is True

    @pytest.mark.asyncio
    async def test_run_analyst_session_skips_claude_when_quota_cached(self, ai_config):
        """Bei aktivem Claude-Quota-Cache wird Claude nicht erneut gestartet."""
        from src.integrations.ai_engine import AIEngine

        engine = AIEngine(ai_config)
        engine._claude_quota_exhausted_until = time.time() + 300
        engine._run_analyst_codex = AsyncMock(return_value=None)
        engine._run_analyst_claude = AsyncMock(return_value={'summary': 'should not happen'})

        result = await engine.run_analyst_session(
            prompt='Test',
            codex_model=None,
            claude_model='claude-opus-4-6',
        )

        assert result is None
        engine._run_analyst_codex.assert_not_awaited()
        engine._run_analyst_claude.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_run_analyst_codex_uses_current_cli_flags(self, ai_config):
        """Analyst-Codex nutzt keine veralteten CLI-Flags mehr."""
        from src.integrations.ai_engine import AIEngine

        engine = AIEngine(ai_config)
        captured_args = []
        mock_proc = make_mock_process(
            stdout=json.dumps({
                'summary': 'ok',
                'topics_investigated': [],
                'findings': [],
                'knowledge_updates': [],
                'health_check_passed': True,
                'next_priority': 'none',
                'areas_checked': [],
                'areas_deferred': [],
                'finding_assessments': [],
            }),
        )

        async def capture_exec(*args, **kwargs):
            captured_args.extend(args)
            return mock_proc

        with patch('asyncio.create_subprocess_exec', side_effect=capture_exec):
            result = await engine._run_analyst_codex(
                prompt='Test',
                schema_path='/home/cmdshadow/shadowops-bot/src/schemas/analyst_session.json',
                model='gpt-5.3-codex',
                timeout=5,
            )

        assert result is not None
        assert '--ephemeral' not in captured_args
        assert '-s' in captured_args
        assert 'workspace-write' in captured_args

    @pytest.mark.asyncio
    async def test_generate_fix_strategy_with_codex(self, ai_config):
        """generate_fix_strategy nutzt Codex und gibt Ergebnis zurueck"""
        from src.integrations.ai_engine import AIEngine

        engine = AIEngine(ai_config)

        mock_result = {
            'description': 'Paket aktualisieren',
            'confidence': 0.9,
            'steps': [{'action': 'update', 'command': 'apt upgrade', 'risk_level': 'low'}],
            'analysis': 'CVE fix verfuegbar',
            'severity_assessment': 'HIGH',
        }

        with patch.object(engine, '_execute_with_fallback', return_value=mock_result):
            context = {
                'event': {
                    'source': 'trivy',
                    'severity': 'HIGH',
                    'event_type': 'vulnerability',
                    'details': {'VulnerabilityID': 'CVE-2024-1234'},
                },
                'previous_attempts': [],
            }

            result = await engine.generate_fix_strategy(context)

        assert result is not None
        assert result['description'] == 'Paket aktualisieren'
        assert result['confidence'] == 0.9

    @pytest.mark.asyncio
    async def test_generate_fix_strategy_returns_none_on_failure(self, ai_config):
        """generate_fix_strategy gibt None zurueck wenn alles fehlschlaegt"""
        from src.integrations.ai_engine import AIEngine

        engine = AIEngine(ai_config)

        with patch.object(engine, '_execute_with_fallback', return_value=None):
            context = {
                'event': {'source': 'trivy', 'severity': 'HIGH'},
                'previous_attempts': [],
            }

            result = await engine.generate_fix_strategy(context)

        assert result is None

    @pytest.mark.asyncio
    async def test_generate_fix_strategy_discord_logging(self, ai_config):
        """generate_fix_strategy loggt ueber Discord-Logger"""
        from src.integrations.ai_engine import AIEngine

        mock_logger = Mock()
        mock_logger.log_ai_learning = Mock()
        engine = AIEngine(ai_config, discord_logger=mock_logger)

        mock_result = {
            'description': 'Test',
            'confidence': 0.8,
            'steps': [],
            'analysis': 'Test',
            'severity_assessment': 'HIGH',
        }

        with patch.object(engine, '_execute_with_fallback', return_value=mock_result):
            context = {
                'event': {'source': 'trivy', 'severity': 'HIGH'},
                'previous_attempts': [],
            }
            await engine.generate_fix_strategy(context)

        # Discord-Logger muss aufgerufen worden sein
        assert mock_logger.log_ai_learning.called

    @pytest.mark.asyncio
    async def test_execute_with_fallback_primary_success(self, ai_config):
        """_execute_with_fallback: Primary Engine erfolgreich -> kein Fallback"""
        from src.integrations.ai_engine import AIEngine

        engine = AIEngine(ai_config)
        mock_result = {'description': 'Codex Erfolg', 'confidence': 0.95}

        with patch.object(engine.codex, 'query', return_value=mock_result):
            route = {
                'engine': 'codex',
                'model': 'gpt-5.3-codex',
                'model_class': 'standard',
                'schema_path': Path('/home/cmdshadow/shadowops-bot/src/schemas/fix_strategy.json'),
            }
            result = await engine._execute_with_fallback("Test Prompt", route)

        assert result is not None
        assert result['description'] == 'Codex Erfolg'
        assert engine.stats['codex_calls'] == 1
        assert engine.stats['codex_success'] == 1

    @pytest.mark.asyncio
    async def test_execute_with_fallback_primary_fails_fallback_succeeds(self, ai_config):
        """_execute_with_fallback: Primary fehlgeschlagen -> Fallback erfolgreich"""
        from src.integrations.ai_engine import AIEngine

        engine = AIEngine(ai_config)
        mock_fallback_result = {'description': 'Claude Rettung', 'confidence': 0.8}

        with patch.object(engine.codex, 'query', return_value=None):
            with patch.object(engine.claude, 'query', return_value=mock_fallback_result):
                route = {
                    'engine': 'codex',
                    'model': 'gpt-5.3-codex',
                    'model_class': 'standard',
                    'schema_path': Path('/home/cmdshadow/shadowops-bot/src/schemas/fix_strategy.json'),
                }
                result = await engine._execute_with_fallback("Test Prompt", route)

        assert result is not None
        assert result['description'] == 'Claude Rettung'
        assert engine.stats['codex_failures'] == 1
        assert engine.stats['claude_success'] == 1

    @pytest.mark.asyncio
    async def test_execute_with_fallback_both_fail(self, ai_config):
        """_execute_with_fallback: Beide Engines fehlgeschlagen -> None"""
        from src.integrations.ai_engine import AIEngine

        engine = AIEngine(ai_config)

        with patch.object(engine.codex, 'query', return_value=None):
            with patch.object(engine.claude, 'query', return_value=None):
                route = {
                    'engine': 'codex',
                    'model': 'gpt-5.3-codex',
                    'model_class': 'standard',
                    'schema_path': Path('/home/cmdshadow/shadowops-bot/src/schemas/fix_strategy.json'),
                }
                result = await engine._execute_with_fallback("Test Prompt", route)

        assert result is None
        assert engine.stats['codex_failures'] == 1
        assert engine.stats['claude_failures'] == 1

    @pytest.mark.asyncio
    async def test_get_ai_analysis_returns_raw_text(self, ai_config):
        """get_ai_analysis gibt Rohtext zurueck"""
        from src.integrations.ai_engine import AIEngine

        engine = AIEngine(ai_config)

        with patch.object(engine.codex, 'query_raw', return_value='Analyse-Ergebnis'):
            result = await engine.get_ai_analysis("Analysiere dieses Event")

        assert result == 'Analyse-Ergebnis'

    @pytest.mark.asyncio
    async def test_get_ai_analysis_critical_uses_thinking(self, ai_config):
        """get_ai_analysis mit use_critical_model=True nutzt Thinking-Modell"""
        from src.integrations.ai_engine import AIEngine

        engine = AIEngine(ai_config)
        captured_model = []

        async def capture_query_raw(prompt, model, timeout=None):
            captured_model.append(model)
            return 'Ergebnis'

        with patch.object(engine.codex, 'query_raw', side_effect=capture_query_raw):
            await engine.get_ai_analysis("Test", use_critical_model=True)

        assert captured_model[0] == 'thinking'

    @pytest.mark.asyncio
    async def test_generate_raw_text_is_alias(self, ai_config):
        """generate_raw_text ist ein Alias fuer get_ai_analysis"""
        from src.integrations.ai_engine import AIEngine

        engine = AIEngine(ai_config)

        assert engine.generate_raw_text == engine.get_ai_analysis

    @pytest.mark.asyncio
    async def test_verify_fix_uses_claude(self, ai_config):
        """verify_fix verwendet Claude zur Verifizierung"""
        from src.integrations.ai_engine import AIEngine

        engine = AIEngine(ai_config)
        mock_result = {'description': 'Fix verifiziert', 'confidence': 0.95}

        with patch.object(engine.claude, 'query', return_value=mock_result):
            result = await engine.verify_fix(
                fix_description='Paket aktualisiert',
                fix_commands=['apt upgrade openssl'],
                event={'source': 'trivy', 'severity': 'HIGH'},
            )

        assert result is not None
        assert result['description'] == 'Fix verifiziert'

    @pytest.mark.asyncio
    async def test_generate_coordinated_plan(self, ai_config):
        """generate_coordinated_plan routet korrekt und gibt Ergebnis zurueck"""
        from src.integrations.ai_engine import AIEngine

        engine = AIEngine(ai_config)
        mock_result = {
            'description': 'Koordinierter Plan',
            'confidence': 0.88,
            'steps': [],
            'analysis': 'Batch-Analyse',
            'severity_assessment': 'HIGH',
        }

        with patch.object(engine, '_execute_with_fallback', return_value=mock_result):
            result = await engine.generate_coordinated_plan(
                prompt='Erstelle Plan fuer 3 Events',
                context={'severity': 'HIGH', 'batch_size': 3},
            )

        assert result is not None
        assert result['description'] == 'Koordinierter Plan'

    @pytest.mark.asyncio
    async def test_build_analysis_prompt(self, ai_config):
        """_build_analysis_prompt baut korrekten Prompt mit Event-Details"""
        from src.integrations.ai_engine import AIEngine

        engine = AIEngine(ai_config)

        event = {
            'source': 'trivy',
            'event_type': 'vulnerability',
            'severity': 'CRITICAL',
            'details': {
                'VulnerabilityID': 'CVE-2024-1234',
                'PkgName': 'openssl',
                'InstalledVersion': '1.0.0',
                'FixedVersion': '1.1.0',
            },
        }
        previous_attempts = []

        prompt = engine._build_analysis_prompt(event, previous_attempts)

        assert 'CVE-2024-1234' in prompt
        assert 'openssl' in prompt
        assert 'CRITICAL' in prompt

    @pytest.mark.asyncio
    async def test_build_analysis_prompt_with_context_manager(self, ai_config):
        """_build_analysis_prompt nutzt ContextManager wenn verfuegbar"""
        from src.integrations.ai_engine import AIEngine

        mock_ctx = Mock()
        mock_ctx.get_infrastructure_context.return_value = "Server: Debian 12, 8 GB RAM"
        engine = AIEngine(ai_config, context_manager=mock_ctx)

        event = {'source': 'trivy', 'severity': 'HIGH', 'details': {}}
        prompt = engine._build_analysis_prompt(event, [])

        assert 'Debian 12' in prompt or mock_ctx.get_infrastructure_context.called

    @pytest.mark.asyncio
    async def test_build_analysis_prompt_with_previous_attempts(self, ai_config):
        """_build_analysis_prompt enthaelt vorherige Versuche"""
        from src.integrations.ai_engine import AIEngine

        engine = AIEngine(ai_config)

        event = {'source': 'crowdsec', 'severity': 'HIGH', 'details': {}}
        previous_attempts = [
            {
                'timestamp': '2026-03-06T12:00:00',
                'strategy': {'description': 'Erster Versuch'},
                'result': 'failed',
                'error': 'Command nicht gefunden',
            }
        ]

        prompt = engine._build_analysis_prompt(event, previous_attempts)

        # Vorherige Versuche muessen erwaehnt werden
        assert 'Erster Versuch' in prompt or 'failed' in prompt.lower() or 'versuch' in prompt.lower()
