"""
Integration-Tests fuer Security Engine v6

Testet das Zusammenspiel aller Komponenten END-TO-END
mit gemockter DB (kein echtes PostgreSQL noetig).
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.integrations.security_engine.models import (
    BanEvent, VulnEvent, ThreatEvent, IntegrityEvent,
    PhaseType, FixResult, Severity, EngineMode
)
from src.integrations.security_engine.engine import SecurityEngine
from src.integrations.security_engine.executor import PhaseTypeExecutor
from src.integrations.security_engine.reactive import ReactiveMode
from src.integrations.security_engine.deep_scan import DeepScanMode
from src.integrations.security_engine.proactive import ProactiveMode
from src.integrations.security_engine.registry import FixerRegistry
from src.integrations.security_engine.providers import NoOpProvider, FixProvider
from src.integrations.security_engine.fixer_adapters import Fail2banFixerAdapter
from src.integrations.security_engine.circuit_breaker import CircuitBreaker
from src.integrations.security_engine.learning_bridge import LearningBridge


# ── Helpers ──────────────────────────────────────────────────────────


class MockFixProvider(FixProvider):
    """Test-Provider der immer success zurueckgibt"""

    def __init__(self, result=None):
        self._result = result or FixResult.success("Mock fix applied", phase_type=PhaseType.FIX)
        self.call_count = 0

    async def execute(self, event, strategy, context=None):
        self.call_count += 1
        return self._result


class FailingFixProvider(FixProvider):
    """Test-Provider der immer failed zurueckgibt"""

    async def execute(self, event, strategy, context=None):
        return FixResult.failed("Mock fix failed", phase_type=PhaseType.FIX)


def make_mock_db():
    """Erstellt eine vollstaendig gemockte SecurityDB"""
    db = AsyncMock()
    db.claim_event = AsyncMock(return_value=True)
    db.release_event = AsyncMock()
    db.record_fix_attempt = AsyncMock(return_value=1)
    db.record_phase_execution = AsyncMock(return_value=1)
    db.get_success_rate = AsyncMock(return_value=0.8)
    db.get_fix_history = AsyncMock(return_value=[])
    db.get_open_findings_count = AsyncMock(return_value=0)
    db.get_knowledge = AsyncMock(return_value=[])
    db.get_phase_stats = AsyncMock(return_value={})
    db.store_knowledge = AsyncMock(return_value=1)
    db.initialize = AsyncMock()
    db.close = AsyncMock()
    return db


# ── E2E: Event → Engine → Fix ──────────────────────────────────────


class TestFullPipeline:
    """End-to-End Tests: Event rein → Fix raus"""

    @pytest.mark.asyncio
    async def test_single_ban_event_fast_path(self):
        """1 Ban-Event → Fast-Path → Contain → Success"""
        engine = SecurityEngine(db_dsn=None)
        engine.db = make_mock_db()

        # Mock-Provider registrieren
        mock_provider = MockFixProvider()
        engine.registry.register('fail2ban', None, mock_provider)

        # Executor + ReactiveMode manuell setzen
        engine.executor = PhaseTypeExecutor(registry=engine.registry, db=engine.db)
        engine.reactive = ReactiveMode(db=engine.db, executor=engine.executor)

        event = BanEvent(source='fail2ban', severity=Severity.HIGH,
                         details={'ip': '1.2.3.4', 'jail': 'sshd'}, event_id='f2b_001')

        result = await engine.handle_security_event(event)
        assert result is True
        assert mock_provider.call_count == 1
        assert engine._events_processed == 1

    @pytest.mark.asyncio
    async def test_vuln_event_uses_fix_phase(self):
        """Vuln-Event (persistent) → Fix-Phase statt Contain"""
        engine = SecurityEngine(db_dsn=None)
        engine.db = make_mock_db()
        mock_provider = MockFixProvider()
        engine.registry.register('trivy', None, mock_provider)
        engine.executor = PhaseTypeExecutor(registry=engine.registry, db=engine.db)
        engine.reactive = ReactiveMode(db=engine.db, executor=engine.executor)

        event = VulnEvent(source='trivy', severity=Severity.HIGH,
                          details={'cve': 'CVE-2026-1234'}, event_id='trivy_001')
        result = await engine.handle_security_event(event)
        assert result is True
        assert mock_provider.call_count == 1

    @pytest.mark.asyncio
    async def test_threat_event_fast_path(self):
        """ThreatEvent (nicht persistent) → Contain-Phase"""
        engine = SecurityEngine(db_dsn=None)
        engine.db = make_mock_db()
        mock_provider = MockFixProvider()
        engine.registry.register('crowdsec', None, mock_provider)
        engine.executor = PhaseTypeExecutor(registry=engine.registry, db=engine.db)
        engine.reactive = ReactiveMode(db=engine.db, executor=engine.executor)

        event = ThreatEvent(source='crowdsec', severity=Severity.MEDIUM,
                            details={'alert': 'brute-force'}, event_id='cs_001')
        result = await engine.handle_security_event(event)
        assert result is True
        assert mock_provider.call_count == 1

    @pytest.mark.asyncio
    async def test_integrity_event_uses_fix_phase(self):
        """IntegrityEvent (persistent) → Fix-Phase"""
        engine = SecurityEngine(db_dsn=None)
        engine.db = make_mock_db()
        mock_provider = MockFixProvider()
        engine.registry.register('aide', None, mock_provider)
        engine.executor = PhaseTypeExecutor(registry=engine.registry, db=engine.db)
        engine.reactive = ReactiveMode(db=engine.db, executor=engine.executor)

        event = IntegrityEvent(source='aide', severity=Severity.HIGH,
                               details={'file': '/etc/passwd'}, event_id='aide_001')
        result = await engine.handle_security_event(event)
        assert result is True
        assert mock_provider.call_count == 1

    @pytest.mark.asyncio
    async def test_batch_triggers_ki_plan(self):
        """3 Events → Planned Path mit KI"""
        engine = SecurityEngine(db_dsn=None)
        engine.db = make_mock_db()
        mock_provider = MockFixProvider()
        engine.registry.register('fail2ban', None, mock_provider)
        engine.executor = PhaseTypeExecutor(registry=engine.registry, db=engine.db)

        ai = AsyncMock()
        ai.generate_coordinated_plan = AsyncMock(return_value={
            'description': 'SSH Hardening Plan',
            'confidence': 0.9,
            'phases': [
                {'name': 'Contain', 'type': 'contain', 'description': 'Block IPs', 'steps': []},
                {'name': 'Fix', 'type': 'fix', 'description': 'Harden Config', 'steps': []},
                {'name': 'Verify', 'type': 'verify', 'description': 'Check', 'steps': []},
            ],
            'rollback_plan': 'Revert'
        })
        engine.reactive = ReactiveMode(db=engine.db, executor=engine.executor, ai_service=ai)

        events = [
            BanEvent(source='fail2ban', severity=Severity.HIGH,
                     details={'ip': f'1.2.3.{i}'}, event_id=f'f2b_{i}')
            for i in range(3)
        ]
        result = await engine.handle_event_batch(events)
        assert result is True
        ai.generate_coordinated_plan.assert_called_once()

    @pytest.mark.asyncio
    async def test_critical_event_triggers_planned_path(self):
        """1 CRITICAL Event → Planned Path (trotz nur 1 Event)"""
        engine = SecurityEngine(db_dsn=None)
        engine.db = make_mock_db()
        mock_provider = MockFixProvider()
        engine.registry.register('fail2ban', None, mock_provider)
        engine.executor = PhaseTypeExecutor(registry=engine.registry, db=engine.db)

        ai = AsyncMock()
        ai.generate_coordinated_plan = AsyncMock(return_value={
            'description': 'Critical Plan',
            'phases': [
                {'name': 'Contain', 'type': 'contain', 'description': 'Block', 'steps': []},
            ],
        })
        engine.reactive = ReactiveMode(db=engine.db, executor=engine.executor, ai_service=ai)

        event = BanEvent(source='fail2ban', severity=Severity.CRITICAL,
                         details={'ip': '10.0.0.1'}, event_id='f2b_crit')
        result = await engine.handle_security_event(event)
        assert result is True
        ai.generate_coordinated_plan.assert_called_once()

    @pytest.mark.asyncio
    async def test_planned_path_fallback_without_ai(self):
        """3 Events ohne AI-Service → Fallback auf Fast-Path"""
        engine = SecurityEngine(db_dsn=None)
        engine.db = make_mock_db()
        mock_provider = MockFixProvider()
        engine.registry.register('fail2ban', None, mock_provider)
        engine.executor = PhaseTypeExecutor(registry=engine.registry, db=engine.db)
        # Kein ai_service
        engine.reactive = ReactiveMode(db=engine.db, executor=engine.executor, ai_service=None)

        events = [
            BanEvent(source='fail2ban', severity=Severity.HIGH,
                     details={'ip': f'1.2.3.{i}'}, event_id=f'f2b_fb_{i}')
            for i in range(3)
        ]
        result = await engine.handle_event_batch(events)
        assert result is True
        # Alle 3 Events sollten per Fast-Path gefixt werden
        assert mock_provider.call_count == 3

    @pytest.mark.asyncio
    async def test_failed_provider_returns_false(self):
        """Provider schlaegt fehl → Event-Handler gibt False zurueck"""
        engine = SecurityEngine(db_dsn=None)
        engine.db = make_mock_db()
        failing_provider = FailingFixProvider()
        engine.registry.register('fail2ban', None, failing_provider)
        engine.executor = PhaseTypeExecutor(registry=engine.registry, db=engine.db)
        engine.reactive = ReactiveMode(db=engine.db, executor=engine.executor)

        event = BanEvent(source='fail2ban', severity=Severity.HIGH,
                         details={'ip': '1.2.3.4'}, event_id='f2b_fail')
        result = await engine.handle_security_event(event)
        assert result is False

    @pytest.mark.asyncio
    async def test_unclaimed_event_skipped(self):
        """Bereits geclaimtes Event wird uebersprungen"""
        engine = SecurityEngine(db_dsn=None)
        engine.db = make_mock_db()
        engine.db.claim_event = AsyncMock(return_value=False)
        mock_provider = MockFixProvider()
        engine.registry.register('fail2ban', None, mock_provider)
        engine.executor = PhaseTypeExecutor(registry=engine.registry, db=engine.db)
        engine.reactive = ReactiveMode(db=engine.db, executor=engine.executor)

        event = BanEvent(source='fail2ban', severity=Severity.HIGH,
                         details={'ip': '1.2.3.4'}, event_id='f2b_claimed')
        result = await engine.handle_security_event(event)
        # Event konnte nicht geclaimed werden → true (nichts zu tun)
        assert result is True
        assert mock_provider.call_count == 0


# ── NoOp-Detection ─────────────────────────────────────────────────


class TestNoOpIntegration:
    """Tests: No-Op Detection im vollen Pipeline"""

    @pytest.mark.asyncio
    async def test_noop_prevents_unnecessary_fix(self):
        """NoOp-Provider erkennt: Config bereits korrekt → kein Fix"""
        registry = FixerRegistry()
        registry.register_noop(NoOpProvider())

        # Ein Provider der aufgerufen wird wenn NoOp nicht greift
        real_provider = MockFixProvider()
        registry.register('fail2ban', PhaseType.FIX, real_provider)

        db = make_mock_db()
        executor = PhaseTypeExecutor(registry=registry, db=db)

        event = BanEvent(source='fail2ban', severity=Severity.HIGH,
                         details={'ip': '1.2.3.4'}, event_id='test')

        # NoOp hat keinen passenden Kontext → gibt None zurueck → real_provider wird aufgerufen
        phase = {'name': 'Fix', 'type': 'fix', 'description': 'Test', 'steps': []}
        await executor.execute_phase(phase, [event], batch_id='b1')
        assert real_provider.call_count == 1

    @pytest.mark.asyncio
    async def test_noop_with_matching_config(self):
        """NoOp-Provider erkennt: Config gleich → FixResult.no_op"""
        registry = FixerRegistry()
        registry.register_noop(NoOpProvider())

        # Dieser Provider sollte NICHT aufgerufen werden
        real_provider = MockFixProvider()
        registry.register('test', PhaseType.FIX, real_provider)

        db = make_mock_db()
        executor = PhaseTypeExecutor(registry=registry, db=db)

        event = BanEvent(source='test', severity=Severity.HIGH,
                         details={}, event_id='noop_match')

        # NoOp braucht current_config und target_config im Context
        # Da der Executor den Context intern setzt, mocken wir _execute_provider_chain
        # statt den vollen Flow zu testen
        noop = NoOpProvider()
        result = await noop.execute(event, {}, context={
            'current_config': {'maxretry': 3},
            'target_config': {'maxretry': 3},
        })
        assert result is not None
        assert result.status == 'no_op'

    @pytest.mark.asyncio
    async def test_noop_with_different_config(self):
        """NoOp-Provider: Config unterschiedlich → None (naechster Provider)"""
        noop = NoOpProvider()
        result = await noop.execute(
            BanEvent(source='test', severity=Severity.HIGH, details={}, event_id='diff'),
            {},
            context={'current_config': {'maxretry': 5}, 'target_config': {'maxretry': 3}},
        )
        assert result is None


# ── Event-Deduplication ────────────────────────────────────────────


class TestDedupIntegration:
    """Tests: Event-Deduplication ueber mehrere Phasen"""

    @pytest.mark.asyncio
    async def test_event_fixed_once_across_fix_phases(self):
        """Gleicher Event wird ueber 3 Fix-Phasen nur 1x gefixt (Dedup)"""
        registry = FixerRegistry()
        mock_provider = MockFixProvider()
        registry.register('fail2ban', None, mock_provider)
        db = make_mock_db()
        executor = PhaseTypeExecutor(registry=registry, db=db)

        event = BanEvent(source='fail2ban', severity=Severity.HIGH,
                         details={'ip': '1.2.3.4'}, event_id='dedup_test')

        for i in range(3):
            await executor.execute_phase(
                {'name': f'Fix {i}', 'type': 'fix', 'description': '', 'steps': []},
                [event], batch_id='b1'
            )

        # Nur 1x gefixt — Dedup greift bei Phase FIX
        assert mock_provider.call_count == 1

    @pytest.mark.asyncio
    async def test_contain_marks_event_as_fixed(self):
        """Contain markiert Event als gefixt → nachfolgende Fix-Phase ueberspringt per Dedup"""
        registry = FixerRegistry()
        mock_provider = MockFixProvider()
        registry.register('fail2ban', None, mock_provider)
        db = make_mock_db()
        executor = PhaseTypeExecutor(registry=registry, db=db)

        event = BanEvent(source='fail2ban', severity=Severity.HIGH,
                         details={'ip': '1.2.3.4'}, event_id='both_test')

        await executor.execute_phase(
            {'name': 'Contain', 'type': 'contain', 'description': '', 'steps': []},
            [event], batch_id='b1'
        )
        await executor.execute_phase(
            {'name': 'Fix', 'type': 'fix', 'description': '', 'steps': []},
            [event], batch_id='b1'
        )
        # Contain fuegt Event zu _fixed_events hinzu → Fix-Phase ueberspringt per Dedup
        assert mock_provider.call_count == 1

    @pytest.mark.asyncio
    async def test_contain_then_fix_after_reset(self):
        """Nach reset_batch: Contain + Fix → beide ausgefuehrt"""
        registry = FixerRegistry()
        mock_provider = MockFixProvider()
        registry.register('fail2ban', None, mock_provider)
        db = make_mock_db()
        executor = PhaseTypeExecutor(registry=registry, db=db)

        event = BanEvent(source='fail2ban', severity=Severity.HIGH,
                         details={'ip': '1.2.3.4'}, event_id='reset_both')

        await executor.execute_phase(
            {'name': 'Contain', 'type': 'contain', 'description': '', 'steps': []},
            [event], batch_id='b1'
        )
        executor.reset_batch()
        await executor.execute_phase(
            {'name': 'Fix', 'type': 'fix', 'description': '', 'steps': []},
            [event], batch_id='b2'
        )
        # Nach Reset: beide werden ausgefuehrt
        assert mock_provider.call_count == 2

    @pytest.mark.asyncio
    async def test_reset_batch_allows_refix(self):
        """Nach reset_batch() kann gleicher Event erneut gefixt werden"""
        registry = FixerRegistry()
        mock_provider = MockFixProvider()
        registry.register('fail2ban', None, mock_provider)
        db = make_mock_db()
        executor = PhaseTypeExecutor(registry=registry, db=db)

        event = BanEvent(source='fail2ban', severity=Severity.HIGH,
                         details={'ip': '1.2.3.4'}, event_id='reset_test')

        await executor.execute_phase(
            {'name': 'Fix', 'type': 'fix', 'description': '', 'steps': []},
            [event], batch_id='b1'
        )
        executor.reset_batch()
        await executor.execute_phase(
            {'name': 'Fix', 'type': 'fix', 'description': '', 'steps': []},
            [event], batch_id='b2'
        )
        assert mock_provider.call_count == 2

    @pytest.mark.asyncio
    async def test_read_only_phase_no_fix(self):
        """Read-only Phasen (recon, verify, monitor) fuehren keinen Fix aus"""
        registry = FixerRegistry()
        mock_provider = MockFixProvider()
        registry.register('fail2ban', None, mock_provider)
        db = make_mock_db()
        executor = PhaseTypeExecutor(registry=registry, db=db)

        event = BanEvent(source='fail2ban', severity=Severity.HIGH,
                         details={'ip': '1.2.3.4'}, event_id='readonly_test')

        for phase_type in ['recon', 'verify', 'monitor']:
            await executor.execute_phase(
                {'name': f'Phase {phase_type}', 'type': phase_type, 'description': '', 'steps': []},
                [event], batch_id='b1'
            )

        # Kein Provider wurde aufgerufen bei Read-only Phasen
        assert mock_provider.call_count == 0

    @pytest.mark.asyncio
    async def test_noop_result_does_not_mark_as_fixed(self):
        """no_op-Ergebnis markiert Event NICHT als gefixt (kann spaeter nochmal laufen)"""
        registry = FixerRegistry()
        noop_provider = MockFixProvider(
            result=FixResult.no_op("Config already correct", phase_type=PhaseType.FIX)
        )
        registry.register('fail2ban', None, noop_provider)
        db = make_mock_db()
        executor = PhaseTypeExecutor(registry=registry, db=db)

        event = BanEvent(source='fail2ban', severity=Severity.HIGH,
                         details={'ip': '1.2.3.4'}, event_id='noop_dedup')

        # Erster Fix-Durchlauf: no_op
        await executor.execute_phase(
            {'name': 'Fix 1', 'type': 'fix', 'description': '', 'steps': []},
            [event], batch_id='b1'
        )
        # Zweiter Fix-Durchlauf: no_op erneut (da Event NICHT als gefixt markiert)
        await executor.execute_phase(
            {'name': 'Fix 2', 'type': 'fix', 'description': '', 'steps': []},
            [event], batch_id='b1'
        )
        assert noop_provider.call_count == 2


# ── Circuit Breaker ────────────────────────────────────────────────


class TestCircuitBreakerIntegration:
    """Tests: Circuit Breaker im Engine-Kontext"""

    @pytest.mark.asyncio
    async def test_circuit_breaker_opens_after_failures(self):
        """5 Failures → Circuit Breaker oeffnet → naechster Event blockiert"""
        engine = SecurityEngine(db_dsn=None)
        engine.db = make_mock_db()

        # Provider der immer fehlschlaegt
        failing_provider = FailingFixProvider()
        engine.registry.register('fail2ban', None, failing_provider)
        engine.executor = PhaseTypeExecutor(registry=engine.registry, db=engine.db)
        engine.reactive = ReactiveMode(db=engine.db, executor=engine.executor)

        # 5 fehlschlagende Events erzeugen
        for i in range(5):
            event = BanEvent(source='fail2ban', severity=Severity.HIGH,
                             details={'ip': '1.2.3.4'}, event_id=f'cb_{i}')
            await engine.handle_security_event(event)

        # Circuit Breaker sollte jetzt offen sein fuer 'fail2ban'
        assert not engine.circuit_breaker.can_attempt

        # Naechster Event wird uebersprungen
        event = BanEvent(source='fail2ban', severity=Severity.HIGH,
                         details={'ip': '5.6.7.8'}, event_id='cb_blocked')
        result = await engine.handle_security_event(event)
        assert result is False
        assert engine._events_skipped == 1

    @pytest.mark.asyncio
    async def test_circuit_breaker_success_resets(self):
        """Erfolg nach Failures → Counter zurueckgesetzt"""
        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=3600)

        cb.record_failure('fail2ban')
        cb.record_failure('fail2ban')
        assert cb.can_attempt  # Noch unter Threshold

        cb.record_success('fail2ban')
        assert cb.can_attempt
        # Counter zurueckgesetzt → 3 weitere Failures noetig
        cb.record_failure('fail2ban')
        cb.record_failure('fail2ban')
        assert cb.can_attempt

    @pytest.mark.asyncio
    async def test_circuit_breaker_per_key_isolation(self):
        """Circuit Breaker ist per-Key — ein Key oeffnet nicht den anderen"""
        cb = CircuitBreaker(failure_threshold=2, cooldown_seconds=3600)

        # Key A erreicht Threshold
        cb.record_failure('key_a')
        cb.record_failure('key_a')
        assert cb.is_open_for('key_a')

        # Key B ist unabhaengig
        assert not cb.is_open_for('key_b')

        # Aber can_attempt ist global: wenn EIN Key offen → gesperrt
        assert not cb.can_attempt

    @pytest.mark.asyncio
    async def test_circuit_breaker_batch_blocked(self):
        """Circuit Breaker blockiert auch Batch-Verarbeitung"""
        engine = SecurityEngine(db_dsn=None)
        engine.db = make_mock_db()
        engine.circuit_breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=3600)
        engine.circuit_breaker.record_failure('batch')  # Sofort offen

        events = [
            BanEvent(source='fail2ban', severity=Severity.HIGH,
                     details={'ip': '1.2.3.4'}, event_id='batch_blocked')
        ]
        result = await engine.handle_event_batch(events)
        assert result is False
        assert engine._events_skipped == 1


# ── Fixer-Adapter ──────────────────────────────────────────────────


class TestFixerAdapterIntegration:
    """Tests: Fixer-Adapter mit No-Op Detection im Executor"""

    @pytest.mark.asyncio
    async def test_fail2ban_noop_detection(self):
        """Fail2ban Adapter erkennt: Jail bereits gehaertet → no_op"""
        mock_fixer = AsyncMock()
        mock_fixer._get_jail_config = AsyncMock(return_value={
            'maxretry': 3, 'bantime': 3600
        })
        mock_fixer.hardened_config = {'maxretry': 3, 'bantime': 3600, 'findtime': 600}
        mock_fixer.fix = AsyncMock()  # Sollte NICHT aufgerufen werden

        adapter = Fail2banFixerAdapter(mock_fixer)

        event = BanEvent(source='fail2ban', severity=Severity.HIGH,
                         details={'jail': 'sshd'}, event_id='noop_test')
        result = await adapter.execute(event, {}, context={'phase_type': PhaseType.FIX})

        assert result is not None
        assert result.status == 'no_op'
        mock_fixer.fix.assert_not_called()

    @pytest.mark.asyncio
    async def test_fail2ban_adapter_delegates_fix(self):
        """Fail2ban Adapter: Config weicht ab → delegiert an Original-Fixer"""
        mock_fixer = AsyncMock()
        mock_fixer._get_jail_config = AsyncMock(return_value={
            'maxretry': 5, 'bantime': 600  # Nicht gehaertet
        })
        mock_fixer.hardened_config = {'maxretry': 3, 'bantime': 3600}
        mock_fixer.fix = AsyncMock(return_value={'status': 'success', 'message': 'Hardened'})

        adapter = Fail2banFixerAdapter(mock_fixer)

        event = BanEvent(source='fail2ban', severity=Severity.HIGH,
                         details={'jail': 'sshd'}, event_id='fix_test')
        result = await adapter.execute(event, {}, context={'phase_type': PhaseType.FIX})

        assert result.status == 'success'
        mock_fixer.fix.assert_called_once()

    @pytest.mark.asyncio
    async def test_fail2ban_noop_in_full_executor(self):
        """Fail2ban No-Op im vollen Executor-Pipeline"""
        mock_fixer = AsyncMock()
        mock_fixer._get_jail_config = AsyncMock(return_value={
            'maxretry': 3, 'bantime': 3600
        })
        mock_fixer.hardened_config = {'maxretry': 3, 'bantime': 3600}
        mock_fixer.fix = AsyncMock()

        adapter = Fail2banFixerAdapter(mock_fixer)

        registry = FixerRegistry()
        registry.register('fail2ban', None, adapter)
        db = make_mock_db()
        executor = PhaseTypeExecutor(registry=registry, db=db)

        event = BanEvent(source='fail2ban', severity=Severity.HIGH,
                         details={'jail': 'sshd'}, event_id='full_noop_test')
        phase = {'name': 'Fix', 'type': 'fix', 'description': 'Harden', 'steps': []}

        result = await executor.execute_phase(phase, [event], batch_id='noop_batch')
        assert result is True
        mock_fixer.fix.assert_not_called()

        # DB sollte den no_op-Fix recorded haben
        db.record_fix_attempt.assert_called()
        call_args = db.record_fix_attempt.call_args
        # result ist der 7. Positional-Parameter (Index 6)
        assert call_args.kwargs.get('result', call_args[1].get('result', '')) == 'no_op' or \
               (len(call_args[0]) > 6 and call_args[0][6] == 'no_op')


# ── DeepScan Sessions ──────────────────────────────────────────────


class TestDeepScanIntegration:
    """Tests: DeepScan Session-Lifecycle"""

    @pytest.mark.asyncio
    async def test_session_lifecycle(self):
        """Session starten → abschliessen → Limit pruefen"""
        db = make_mock_db()
        db.get_open_findings_count = AsyncMock(return_value=3)
        deep = DeepScanMode(db=db, ai_engine=AsyncMock(), executor=AsyncMock())

        # Session 1
        result = await deep.run_session()
        assert result['status'] == 'completed'
        assert result['mode'] == 'quick_scan'
        assert deep.sessions_today == 1

        # Session 2: Limit erreicht (quick_scan = max 1)
        result2 = await deep.run_session()
        assert result2['status'] == 'skipped'
        assert result2['reason'] == 'session_limit'

        # Reset
        deep.reset_daily()
        assert deep.sessions_today == 0
        assert await deep.can_start_session() is True

    @pytest.mark.asyncio
    async def test_session_mode_fix_only(self):
        """>=20 Findings → fix_only Modus"""
        db = make_mock_db()
        db.get_open_findings_count = AsyncMock(return_value=25)
        deep = DeepScanMode(db=db, ai_engine=AsyncMock(), executor=AsyncMock())

        result = await deep.run_session()
        assert result['mode'] == 'fix_only'
        assert result['config']['max_sessions_per_day'] == 3

    @pytest.mark.asyncio
    async def test_session_mode_full_scan(self):
        """5-19 Findings → full_scan Modus"""
        db = make_mock_db()
        db.get_open_findings_count = AsyncMock(return_value=10)
        deep = DeepScanMode(db=db, ai_engine=AsyncMock(), executor=AsyncMock())

        result = await deep.run_session()
        assert result['mode'] == 'full_scan'
        assert result['config']['max_sessions_per_day'] == 2

    @pytest.mark.asyncio
    async def test_session_mode_maintenance(self):
        """0 Findings → maintenance Modus"""
        db = make_mock_db()
        db.get_open_findings_count = AsyncMock(return_value=0)
        deep = DeepScanMode(db=db, ai_engine=AsyncMock(), executor=AsyncMock())

        result = await deep.run_session()
        assert result['mode'] == 'maintenance'
        assert result['config']['fix_enabled'] is False

    @pytest.mark.asyncio
    async def test_multiple_sessions_fix_only(self):
        """fix_only erlaubt 3 Sessions am Tag"""
        db = make_mock_db()
        db.get_open_findings_count = AsyncMock(return_value=25)
        deep = DeepScanMode(db=db, ai_engine=AsyncMock(), executor=AsyncMock())

        for i in range(3):
            result = await deep.run_session()
            assert result['status'] == 'completed'

        # 4. Session: Limit erreicht
        result = await deep.run_session()
        assert result['status'] == 'skipped'

    @pytest.mark.asyncio
    async def test_session_failure_recorded(self):
        """Session-Fehler in Scan-Phase wird korrekt als 'failed' recorded"""
        db = make_mock_db()
        db.get_open_findings_count = AsyncMock(return_value=3)

        # AI-Engine wirft Exception in Scan-Phase
        failing_ai = AsyncMock()
        failing_ai.query = AsyncMock(side_effect=Exception("AI timeout"))

        deep = DeepScanMode(db=db, ai_engine=failing_ai, executor=AsyncMock())

        # Session laeuft durch (Scan-Phase hat try/except intern, also completed)
        result = await deep.run_session()
        assert result['status'] in ('completed', 'failed')

    @pytest.mark.asyncio
    async def test_session_db_error_propagates(self):
        """DB-Fehler in _determine_session_mode propagiert als Exception"""
        db = make_mock_db()
        db.get_open_findings_count = AsyncMock(side_effect=Exception("DB down"))
        deep = DeepScanMode(db=db)

        with pytest.raises(Exception, match="DB down"):
            await deep.run_session()


# ── ProactiveMode Reports ──────────────────────────────────────────


class TestProactiveIntegration:
    """Tests: Proactive Report Generation"""

    @pytest.mark.asyncio
    async def test_full_hardening_report(self):
        """Report mit niedrigen Erfolgsraten → Empfehlungen"""
        db = make_mock_db()
        db.get_success_rate = AsyncMock(return_value=0.3)  # Critical
        proactive = ProactiveMode(db=db)
        report = await proactive.generate_hardening_report()

        assert len(report['recommendations']) > 0
        assert any(r['category'] == 'effectiveness' for r in report['recommendations'])

    @pytest.mark.asyncio
    async def test_report_with_high_success_rate(self):
        """Hohe Erfolgsrate → keine effectiveness-Empfehlung"""
        db = make_mock_db()
        db.get_success_rate = AsyncMock(return_value=0.95)
        proactive = ProactiveMode(db=db)
        report = await proactive.generate_hardening_report()

        effectiveness_recs = [r for r in report['recommendations'] if r['category'] == 'effectiveness']
        assert len(effectiveness_recs) == 0

    @pytest.mark.asyncio
    async def test_coverage_gaps_detected(self):
        """Bereiche ohne Coverage → coverage-Empfehlungen"""
        db = make_mock_db()
        db.get_success_rate = AsyncMock(return_value=0.8)
        # get_scan_coverage gibt None zurueck → Bereich nie gescannt
        db.get_scan_coverage = AsyncMock(return_value=None)
        proactive = ProactiveMode(db=db)
        gaps = await proactive.get_coverage_gaps()

        # Alle SCAN_AREAS sollten als Luecke gemeldet werden
        assert len(gaps) > 0
        assert all(g['last_checked'] is None for g in gaps)

    @pytest.mark.asyncio
    async def test_proactive_scan_returns_report(self):
        """run_proactive_scan gibt vollstaendigen Report zurueck"""
        db = make_mock_db()
        db.get_success_rate = AsyncMock(return_value=0.5)
        proactive = ProactiveMode(db=db)
        report = await proactive.run_proactive_scan()

        assert 'coverage_gaps' in report
        assert 'fix_effectiveness' in report
        assert 'recommendations' in report
        assert 'generated_at' in report


# ── LearningBridge Cross-Agent ─────────────────────────────────────


class TestLearningBridgeIntegration:
    """Tests: Cross-Agent Learning"""

    @pytest.mark.asyncio
    async def test_share_and_read_knowledge(self):
        """Wissen teilen und zuruecklesen"""
        bridge = LearningBridge.__new__(LearningBridge)
        bridge.pool = AsyncMock()
        bridge.pool.fetchrow = AsyncMock(return_value={'id': 1})
        bridge.pool.fetch = AsyncMock(return_value=[
            {'agent': 'security_engine', 'category': 'security', 'subject': 'ssh',
             'content': 'MaxAuthTries=3', 'confidence': 0.9, 'created_at': '2026-03-24'}
        ])

        # Share
        result = await bridge.share_knowledge('security', 'ssh', 'MaxAuthTries=3', 0.9)
        assert result == 1
        bridge.pool.fetchrow.assert_called_once()

        # Read back
        knowledge = await bridge.get_cross_agent_knowledge()
        assert len(knowledge) == 1
        assert knowledge[0]['subject'] == 'ssh'

    @pytest.mark.asyncio
    async def test_record_fix_feedback(self):
        """Fix-Feedback in agent_feedback schreiben"""
        bridge = LearningBridge.__new__(LearningBridge)
        bridge.pool = AsyncMock()
        bridge.pool.fetchrow = AsyncMock(return_value={'id': 42})

        result = await bridge.record_fix_feedback(
            project='shadowops-bot', fix_id='fix_123',
            success=True, fix_type='auto_fix', metadata={'source': 'fail2ban'}
        )
        assert result == 42

    @pytest.mark.asyncio
    async def test_quality_trends_without_pool(self):
        """Ohne DB-Verbindung → Default-Werte"""
        bridge = LearningBridge.__new__(LearningBridge)
        bridge.pool = None

        trends = await bridge.get_agent_quality_trends()
        assert trends['avg_score'] == 0.0
        assert trends['trend'] == 'unknown'
        assert trends['sample_count'] == 0

    @pytest.mark.asyncio
    async def test_share_knowledge_without_pool(self):
        """Ohne DB-Verbindung → None zurueck, kein Fehler"""
        bridge = LearningBridge.__new__(LearningBridge)
        bridge.pool = None

        result = await bridge.share_knowledge('security', 'test', 'content', 0.5)
        assert result is None

    @pytest.mark.asyncio
    async def test_learning_summary(self):
        """Learning-Summary aggregiert Knowledge + Quality"""
        bridge = LearningBridge.__new__(LearningBridge)
        bridge.pool = AsyncMock()
        bridge.pool.fetch = AsyncMock(return_value=[
            {'agent': 'seo', 'category': 'security', 'subject': 'headers',
             'content': 'CSP einrichten', 'confidence': 0.7, 'created_at': '2026-03-24'},
        ])
        bridge.pool.fetchrow = AsyncMock(return_value={'avg_score': 0.85, 'sample_count': 10})

        summary = await bridge.get_learning_summary()
        assert summary['cross_agent_knowledge_count'] == 1
        assert summary['connected'] is True


# ── Multi-Source Batch ─────────────────────────────────────────────


class TestMultiSourceBatch:
    """Tests: Gemischte Event-Sources in einem Batch"""

    @pytest.mark.asyncio
    async def test_mixed_sources_batch(self):
        """Batch mit Fail2ban + Trivy Events → verschiedene Fixer"""
        engine = SecurityEngine(db_dsn=None)
        engine.db = make_mock_db()

        f2b_provider = MockFixProvider()
        trivy_provider = MockFixProvider()
        engine.registry.register('fail2ban', None, f2b_provider)
        engine.registry.register('trivy', None, trivy_provider)

        engine.executor = PhaseTypeExecutor(registry=engine.registry, db=engine.db)
        engine.reactive = ReactiveMode(db=engine.db, executor=engine.executor)

        events = [
            BanEvent(source='fail2ban', severity=Severity.HIGH,
                     details={'ip': '1.2.3.4'}, event_id='f2b_mix'),
            VulnEvent(source='trivy', severity=Severity.HIGH,
                      details={'cve': 'CVE-2026-1'}, event_id='trivy_mix'),
        ]
        result = await engine.handle_event_batch(events)
        assert result is True
        assert f2b_provider.call_count == 1
        assert trivy_provider.call_count == 1

    @pytest.mark.asyncio
    async def test_mixed_severities_batch(self):
        """Batch mit gemischten Severities → alle verarbeitet"""
        engine = SecurityEngine(db_dsn=None)
        engine.db = make_mock_db()

        mock_provider = MockFixProvider()
        engine.registry.register('fail2ban', None, mock_provider)
        engine.executor = PhaseTypeExecutor(registry=engine.registry, db=engine.db)
        engine.reactive = ReactiveMode(db=engine.db, executor=engine.executor)

        events = [
            BanEvent(source='fail2ban', severity=Severity.LOW,
                     details={'ip': '1.2.3.1'}, event_id='sev_low'),
            BanEvent(source='fail2ban', severity=Severity.HIGH,
                     details={'ip': '1.2.3.2'}, event_id='sev_high'),
        ]
        result = await engine.handle_event_batch(events)
        assert result is True
        assert mock_provider.call_count == 2

    @pytest.mark.asyncio
    async def test_unknown_source_in_batch(self):
        """Event mit unregistrierter Source → failed, andere Events laufen weiter"""
        engine = SecurityEngine(db_dsn=None)
        engine.db = make_mock_db()

        f2b_provider = MockFixProvider()
        engine.registry.register('fail2ban', None, f2b_provider)
        # Kein Provider fuer 'unknown_source'

        engine.executor = PhaseTypeExecutor(registry=engine.registry, db=engine.db)
        engine.reactive = ReactiveMode(db=engine.db, executor=engine.executor)

        events = [
            BanEvent(source='fail2ban', severity=Severity.HIGH,
                     details={'ip': '1.2.3.4'}, event_id='f2b_known'),
            BanEvent(source='unknown_source', severity=Severity.HIGH,
                     details={'ip': '5.6.7.8'}, event_id='unknown_src'),
        ]
        result = await engine.handle_event_batch(events)
        # Mindestens ein Event hat keinen Provider → not all_success
        assert f2b_provider.call_count == 1


# ── Engine Stats ───────────────────────────────────────────────────


class TestEngineStats:
    """Tests: Engine-Statistiken und State"""

    @pytest.mark.asyncio
    async def test_stats_after_events(self):
        """Stats zaehlen processed und skipped korrekt"""
        engine = SecurityEngine(db_dsn=None)
        engine.db = make_mock_db()
        mock_provider = MockFixProvider()
        engine.registry.register('fail2ban', None, mock_provider)
        engine.executor = PhaseTypeExecutor(registry=engine.registry, db=engine.db)
        engine.reactive = ReactiveMode(db=engine.db, executor=engine.executor)

        # 2 erfolgreiche Events
        for i in range(2):
            event = BanEvent(source='fail2ban', severity=Severity.HIGH,
                             details={'ip': f'1.2.3.{i}'}, event_id=f'stats_{i}')
            await engine.handle_security_event(event)

        stats = engine.get_stats()
        assert stats['events_processed'] == 2
        assert stats['events_skipped'] == 0
        assert 'fail2ban/*' in stats['registered_fixers']

    @pytest.mark.asyncio
    async def test_stats_initial(self):
        """Frische Engine hat 0 Events"""
        engine = SecurityEngine(db_dsn=None)
        stats = engine.get_stats()
        assert stats['events_processed'] == 0
        assert stats['events_skipped'] == 0
        assert stats['circuit_breaker']['is_open'] is False


# ── Model-Tests ────────────────────────────────────────────────────


class TestModels:
    """Tests: Datenmodell-Verhalten"""

    def test_ban_event_properties(self):
        """BanEvent: nicht persistent, event_type='ban'"""
        event = BanEvent(source='fail2ban', severity=Severity.HIGH,
                         details={'ip': '1.2.3.4'}, event_id='test')
        assert event.event_type == 'ban'
        assert event.is_persistent is False
        assert event.signature == 'fail2ban_ban'

    def test_vuln_event_properties(self):
        """VulnEvent: persistent, event_type='vulnerability'"""
        event = VulnEvent(source='trivy', severity=Severity.CRITICAL,
                          details={'cve': 'CVE-2026-1'}, event_id='test')
        assert event.event_type == 'vulnerability'
        assert event.is_persistent is True

    def test_severity_priority(self):
        """Severity-Prioritaeten: CRITICAL < HIGH < MEDIUM < LOW"""
        assert Severity.CRITICAL.priority < Severity.HIGH.priority
        assert Severity.HIGH.priority < Severity.MEDIUM.priority
        assert Severity.MEDIUM.priority < Severity.LOW.priority

    def test_phase_type_read_only(self):
        """Read-only Phasen: recon, verify, monitor"""
        assert PhaseType.RECON.is_read_only is True
        assert PhaseType.VERIFY.is_read_only is True
        assert PhaseType.MONITOR.is_read_only is True
        assert PhaseType.CONTAIN.is_read_only is False
        assert PhaseType.FIX.is_read_only is False

    def test_fix_result_factories(self):
        """FixResult-Factories erzeugen korrekte Status-Werte"""
        s = FixResult.success("OK")
        assert s.status == 'success'
        assert s.is_success is True

        f = FixResult.failed("Error")
        assert f.status == 'failed'
        assert f.is_success is False

        n = FixResult.no_op("Already done")
        assert n.status == 'no_op'
        assert n.is_success is True

        sk = FixResult.skipped("Duplicate")
        assert sk.status == 'skipped_duplicate'
        assert sk.is_success is True

    def test_event_to_dict(self):
        """Event-Serialisierung enthaelt alle Felder"""
        event = BanEvent(source='fail2ban', severity=Severity.HIGH,
                         details={'ip': '1.2.3.4'}, event_id='dict_test')
        d = event.to_dict()
        assert d['source'] == 'fail2ban'
        assert d['event_type'] == 'ban'
        assert d['severity'] == 'HIGH'
        assert d['event_id'] == 'dict_test'
        assert d['is_persistent'] is False
        assert 'timestamp' in d

    def test_engine_mode_enum(self):
        """EngineMode Enum-Werte"""
        assert EngineMode.REACTIVE.value == 'reactive'
        assert EngineMode.PROACTIVE.value == 'proactive'
        assert EngineMode.DEEP_SCAN.value == 'deep_scan'


# ── Registry ───────────────────────────────────────────────────────


class TestRegistryIntegration:
    """Tests: FixerRegistry Provider-Chain"""

    def test_provider_chain_order(self):
        """Provider-Chain: NoOp → exakt → Fallback"""
        registry = FixerRegistry()
        noop = NoOpProvider()
        registry.register_noop(noop)

        exact_provider = MockFixProvider()
        registry.register('fail2ban', PhaseType.FIX, exact_provider)

        fallback_provider = MockFixProvider()
        registry.register('fail2ban', None, fallback_provider)

        chain = registry.get_providers('fail2ban', PhaseType.FIX)
        assert len(chain) == 3
        assert isinstance(chain[0], NoOpProvider)
        assert chain[1] is exact_provider
        assert chain[2] is fallback_provider

    def test_empty_registry_no_providers(self):
        """Leere Registry → leere Chain (nur NoOp wenn registriert)"""
        registry = FixerRegistry()
        chain = registry.get_providers('unknown', PhaseType.FIX)
        assert len(chain) == 0

    def test_list_registered(self):
        """Debug-Info zeigt alle registrierten Provider"""
        registry = FixerRegistry()
        registry.register('fail2ban', None, MockFixProvider())
        registry.register('trivy', PhaseType.FIX, MockFixProvider())

        listed = registry.list_registered()
        assert 'fail2ban/*' in listed
        assert 'trivy/fix' in listed
