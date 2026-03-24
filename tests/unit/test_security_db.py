import pytest
from unittest.mock import AsyncMock
from src.integrations.security_engine.db import SecurityDB


class TestSecurityDB:
    @pytest.mark.asyncio
    async def test_record_fix_attempt(self):
        db = SecurityDB.__new__(SecurityDB)
        db.pool = AsyncMock()
        db.pool.fetchrow = AsyncMock(return_value={'id': 42})
        fix_id = await db.record_fix_attempt(
            event_source='fail2ban', event_type='ban', event_signature='fail2ban_ban',
            phase_type='fix', approach='harden_config',
            commands=['sudo fail2ban-client set sshd maxretry 3'],
            result='success', duration_ms=150)
        assert fix_id == 42

    @pytest.mark.asyncio
    async def test_record_no_op(self):
        db = SecurityDB.__new__(SecurityDB)
        db.pool = AsyncMock()
        db.pool.fetchrow = AsyncMock(return_value={'id': 43})
        fix_id = await db.record_fix_attempt(
            event_source='fail2ban', event_type='ban', event_signature='fail2ban_ban',
            phase_type='fix', approach='harden_config', commands=[], result='no_op',
            duration_ms=5, metadata={'reason': 'Config bereits korrekt'})
        assert fix_id == 43

    @pytest.mark.asyncio
    async def test_claim_event_success(self):
        db = SecurityDB.__new__(SecurityDB)
        db.pool = AsyncMock()
        db.pool.fetchrow = AsyncMock(return_value={'id': 1})
        claimed = await db.claim_event('evt_001', 'reactive')
        assert claimed is True

    @pytest.mark.asyncio
    async def test_claim_event_already_claimed(self):
        db = SecurityDB.__new__(SecurityDB)
        db.pool = AsyncMock()
        db.pool.fetchrow = AsyncMock(return_value=None)
        claimed = await db.claim_event('evt_001', 'reactive')
        assert claimed is False

    @pytest.mark.asyncio
    async def test_get_success_rate(self):
        db = SecurityDB.__new__(SecurityDB)
        db.pool = AsyncMock()
        db.pool.fetchrow = AsyncMock(return_value={'total': 10, 'successes': 8, 'no_ops': 1})
        rate = await db.get_success_rate('fail2ban_ban', days=30)
        assert abs(rate - 0.9) < 0.01

    @pytest.mark.asyncio
    async def test_get_success_rate_no_history(self):
        db = SecurityDB.__new__(SecurityDB)
        db.pool = AsyncMock()
        db.pool.fetchrow = AsyncMock(return_value={'total': 0, 'successes': 0, 'no_ops': 0})
        rate = await db.get_success_rate('unknown_sig', days=30)
        assert rate == 0.5

    @pytest.mark.asyncio
    async def test_record_phase_execution(self):
        db = SecurityDB.__new__(SecurityDB)
        db.pool = AsyncMock()
        db.pool.fetchrow = AsyncMock(return_value={'id': 1})
        phase_id = await db.record_phase_execution(
            batch_id='batch_123', phase_type='contain', phase_name='IP blocken',
            events_processed=3, result='success', duration_ms=500)
        assert phase_id == 1

    @pytest.mark.asyncio
    async def test_release_event(self):
        db = SecurityDB.__new__(SecurityDB)
        db.pool = AsyncMock()
        db.pool.execute = AsyncMock()
        await db.release_event('evt_001', 'completed')
        db.pool.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_is_event_claimed_found(self):
        db = SecurityDB.__new__(SecurityDB)
        db.pool = AsyncMock()
        db.pool.fetchrow = AsyncMock(return_value={'handler': 'reactive'})
        handler = await db.is_event_claimed('evt_001')
        assert handler == 'reactive'

    @pytest.mark.asyncio
    async def test_is_event_claimed_not_found(self):
        db = SecurityDB.__new__(SecurityDB)
        db.pool = AsyncMock()
        db.pool.fetchrow = AsyncMock(return_value=None)
        handler = await db.is_event_claimed('evt_999')
        assert handler is None

    @pytest.mark.asyncio
    async def test_get_open_findings_count(self):
        db = SecurityDB.__new__(SecurityDB)
        db.pool = AsyncMock()
        db.pool.fetchrow = AsyncMock(return_value={'cnt': 7})
        count = await db.get_open_findings_count()
        assert count == 7

    @pytest.mark.asyncio
    async def test_store_knowledge(self):
        db = SecurityDB.__new__(SecurityDB)
        db.pool = AsyncMock()
        db.pool.fetchrow = AsyncMock(return_value={'id': 10})
        kid = await db.store_knowledge('firewall', 'ufw_rules', 'UFW aktiv mit deny default', 0.8)
        assert kid == 10

    @pytest.mark.asyncio
    async def test_get_knowledge(self):
        db = SecurityDB.__new__(SecurityDB)
        db.pool = AsyncMock()
        db.pool.fetch = AsyncMock(return_value=[
            {'subject': 'ufw_rules', 'content': 'UFW aktiv', 'confidence': 0.8, 'last_verified': None}
        ])
        results = await db.get_knowledge('firewall', min_confidence=0.5)
        assert len(results) == 1
        assert results[0]['subject'] == 'ufw_rules'

    @pytest.mark.asyncio
    async def test_get_phase_stats(self):
        db = SecurityDB.__new__(SecurityDB)
        db.pool = AsyncMock()
        db.pool.fetch = AsyncMock(return_value=[
            {'phase_type': 'contain', 'total': 5, 'successes': 4, 'no_ops': 0, 'avg_duration': 300.0}
        ])
        stats = await db.get_phase_stats(days=7)
        assert 'contain' in stats
        assert stats['contain']['total'] == 5

    @pytest.mark.asyncio
    async def test_update_strategy_stats(self):
        db = SecurityDB.__new__(SecurityDB)
        db.pool = AsyncMock()
        db.pool.execute = AsyncMock()
        await db.update_strategy_stats('block_ip', 'crowdsec_ban', success=True)
        db.pool.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_claim_event_exception_returns_false(self):
        db = SecurityDB.__new__(SecurityDB)
        db.pool = AsyncMock()
        db.pool.fetchrow = AsyncMock(side_effect=Exception("DB down"))
        claimed = await db.claim_event('evt_err', 'reactive')
        assert claimed is False

    @pytest.mark.asyncio
    async def test_get_fix_history(self):
        db = SecurityDB.__new__(SecurityDB)
        db.pool = AsyncMock()
        db.pool.fetch = AsyncMock(return_value=[
            {'id': 1, 'approach': 'block', 'result': 'success', 'phase_type': 'fix',
             'duration_ms': 100, 'error_message': None, 'was_fast_path': False,
             'created_at': '2026-03-20T12:00:00+00:00'}
        ])
        history = await db.get_fix_history('fail2ban_ban', days=7, limit=5)
        assert len(history) == 1
        assert history[0]['result'] == 'success'
