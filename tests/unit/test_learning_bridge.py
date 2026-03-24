import pytest
from unittest.mock import AsyncMock
from src.integrations.security_engine.learning_bridge import LearningBridge


class TestLearningBridgeRead:
    @pytest.mark.asyncio
    async def test_get_cross_agent_knowledge(self):
        bridge = LearningBridge.__new__(LearningBridge)
        bridge.pool = AsyncMock()
        bridge.pool.fetch = AsyncMock(return_value=[
            {'agent': 'seo', 'category': 'security', 'subject': 'headers',
             'content': 'CSP missing', 'confidence': 0.8, 'created_at': '2026-03-01'}
        ])
        result = await bridge.get_cross_agent_knowledge()
        assert len(result) == 1
        assert result[0]['agent'] == 'seo'

    @pytest.mark.asyncio
    async def test_get_knowledge_without_connection(self):
        bridge = LearningBridge.__new__(LearningBridge)
        bridge.pool = None
        result = await bridge.get_cross_agent_knowledge()
        assert result == []

    @pytest.mark.asyncio
    async def test_get_quality_trends(self):
        bridge = LearningBridge.__new__(LearningBridge)
        bridge.pool = AsyncMock()
        bridge.pool.fetchrow = AsyncMock(return_value={'avg_score': 0.85, 'sample_count': 10})
        result = await bridge.get_agent_quality_trends()
        assert result['avg_score'] == 0.85
        assert result['trend'] == 'improving'

    @pytest.mark.asyncio
    async def test_quality_trends_declining(self):
        bridge = LearningBridge.__new__(LearningBridge)
        bridge.pool = AsyncMock()
        bridge.pool.fetchrow = AsyncMock(return_value={'avg_score': 0.3, 'sample_count': 5})
        result = await bridge.get_agent_quality_trends()
        assert result['trend'] == 'declining'

    @pytest.mark.asyncio
    async def test_quality_trends_no_connection(self):
        bridge = LearningBridge.__new__(LearningBridge)
        bridge.pool = None
        result = await bridge.get_agent_quality_trends()
        assert result['trend'] == 'unknown'


class TestLearningBridgeWrite:
    @pytest.mark.asyncio
    async def test_record_fix_feedback_success(self):
        bridge = LearningBridge.__new__(LearningBridge)
        bridge.pool = AsyncMock()
        bridge.pool.fetchrow = AsyncMock(return_value={'id': 42})
        result = await bridge.record_fix_feedback('shadowops-bot', 'fix_123', success=True)
        assert result == 42

    @pytest.mark.asyncio
    async def test_record_fix_feedback_failure(self):
        bridge = LearningBridge.__new__(LearningBridge)
        bridge.pool = AsyncMock()
        bridge.pool.fetchrow = AsyncMock(return_value={'id': 43})
        result = await bridge.record_fix_feedback('shadowops-bot', 'fix_456', success=False)
        assert result == 43

    @pytest.mark.asyncio
    async def test_record_feedback_no_connection(self):
        bridge = LearningBridge.__new__(LearningBridge)
        bridge.pool = None
        result = await bridge.record_fix_feedback('project', 'fix_1', True)
        assert result is None

    @pytest.mark.asyncio
    async def test_record_quality_score(self):
        bridge = LearningBridge.__new__(LearningBridge)
        bridge.pool = AsyncMock()
        bridge.pool.fetchrow = AsyncMock(return_value={'id': 1})
        result = await bridge.record_quality_score('shadowops-bot', 'fix_123', auto_score=0.9)
        assert result == 1

    @pytest.mark.asyncio
    async def test_record_quality_combined(self):
        bridge = LearningBridge.__new__(LearningBridge)
        bridge.pool = AsyncMock()
        bridge.pool.fetchrow = AsyncMock(return_value={'id': 2})
        result = await bridge.record_quality_score('project', 'fix_1', auto_score=0.8, feedback_score=0.6)
        assert result == 2
        # Combined = 0.8 * 0.6 + 0.6 * 0.4 = 0.72
        call_args = bridge.pool.fetchrow.call_args[0]
        assert abs(call_args[-1] - 0.72) < 0.01  # combined_score


class TestLearningBridgeShare:
    @pytest.mark.asyncio
    async def test_share_knowledge(self):
        bridge = LearningBridge.__new__(LearningBridge)
        bridge.pool = AsyncMock()
        bridge.pool.fetchrow = AsyncMock(return_value={'id': 10})
        result = await bridge.share_knowledge('security', 'ssh_hardening', 'MaxAuthTries=3 empfohlen', 0.9)
        assert result == 10

    @pytest.mark.asyncio
    async def test_share_no_connection(self):
        bridge = LearningBridge.__new__(LearningBridge)
        bridge.pool = None
        result = await bridge.share_knowledge('security', 'test', 'content')
        assert result is None


class TestLearningBridgeSummary:
    @pytest.mark.asyncio
    async def test_learning_summary(self):
        bridge = LearningBridge.__new__(LearningBridge)
        bridge.pool = AsyncMock()
        bridge.pool.fetch = AsyncMock(return_value=[{'agent': 'seo', 'category': 'security',
            'subject': 'test', 'content': 'test', 'confidence': 0.8, 'created_at': '2026-01-01'}])
        bridge.pool.fetchrow = AsyncMock(return_value={'avg_score': 0.7, 'sample_count': 5})
        result = await bridge.get_learning_summary()
        assert result['connected'] is True
        assert result['cross_agent_knowledge_count'] == 1


class TestLearningBridgeConnection:
    def test_is_connected_true(self):
        bridge = LearningBridge.__new__(LearningBridge)
        bridge.pool = AsyncMock()
        assert bridge.is_connected is True

    def test_is_connected_false(self):
        bridge = LearningBridge.__new__(LearningBridge)
        bridge.pool = None
        assert bridge.is_connected is False
