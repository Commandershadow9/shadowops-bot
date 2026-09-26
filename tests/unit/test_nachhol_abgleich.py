"""Nachhol-Abgleich nach CI-Timeout (ZERODOX#2891).

Endete das CI-Warten mit "timeout", gab der Deploy die Reservierung frei und
meldete Alarm — danach holte nichts den Stand nach (36 Timeouts in 30 Tagen,
27 davon mit später grünem Lauf). Jetzt wird EINMAL verzögert nachgeholt,
über denselben Kern wie der Start-Abgleich.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from src.integrations.github_integration import GitHubIntegration
from src.utils.config import Config
from tests.unit.test_start_abgleich import REMOTE, _Harness


# --- Mixin-Kern --------------------------------------------------------------

@pytest.mark.asyncio
async def test_timeout_plant_genau_einen_task_und_triggert_nach_ablauf(tmp_path):
    h = _Harness(tmp_path)
    assert h.plane_nachhol_abgleich("ZERODOX", delay_sec=0) is True
    task = h._nachhol_abgleich_tasks["zerodox"]
    await task
    assert len(h.trigger_calls) == 1
    assert h.trigger_calls[0]["full_sha"] == REMOTE
    assert h.trigger_calls[0]["repo_full_name"] == "Commandershadow9/ZERODOX"
    # Nach Ablauf ist der Platz wieder frei.
    assert "zerodox" not in h._nachhol_abgleich_tasks


@pytest.mark.asyncio
async def test_nach_ablauf_ohne_abweichung_kein_trigger(tmp_path):
    h = _Harness(tmp_path, deployed=REMOTE)
    assert h.plane_nachhol_abgleich("zerodox", delay_sec=0) is True
    await h._nachhol_abgleich_tasks["zerodox"]
    assert h.trigger_calls == []


@pytest.mark.asyncio
async def test_zwei_timeouts_nur_ein_ausstehender_task(tmp_path):
    h = _Harness(tmp_path)
    assert h.plane_nachhol_abgleich("zerodox", delay_sec=3600) is True
    erster = h._nachhol_abgleich_tasks["zerodox"]
    assert h.plane_nachhol_abgleich("ZERODOX", delay_sec=3600) is False
    assert h._nachhol_abgleich_tasks["zerodox"] is erster
    assert len(h._nachhol_abgleich_tasks) == 1
    erster.cancel()
    with pytest.raises(asyncio.CancelledError):
        await erster


@pytest.mark.asyncio
async def test_shadowops_bot_kein_nachhol(tmp_path):
    deploy_baum = tmp_path / "bot-deploy"
    deploy_baum.mkdir()
    projekte = {
        "shadowops-bot": {
            "enabled": True,
            "deploy_path": str(deploy_baum),
            "repo_url": "https://github.com/Commandershadow9/shadowops-bot",
        },
    }
    h = _Harness(tmp_path, projekte=projekte)
    assert h.plane_nachhol_abgleich("shadowops-bot", delay_sec=0) is False
    assert h.plane_nachhol_abgleich("shadowops_bot", delay_sec=0) is False
    assert not getattr(h, "_nachhol_abgleich_tasks", {})


@pytest.mark.asyncio
async def test_ohne_auto_deploy_kein_nachhol(tmp_path):
    h = _Harness(tmp_path)
    h.auto_deploy_enabled = False
    assert h.plane_nachhol_abgleich("zerodox", delay_sec=0) is False


@pytest.mark.asyncio
async def test_exception_im_kern_wird_warning(tmp_path, caplog):
    h = _Harness(tmp_path, remote_fehler=True)
    assert h.plane_nachhol_abgleich("zerodox", delay_sec=0) is True
    await h._nachhol_abgleich_tasks["zerodox"]
    assert h.trigger_calls == []
    assert any("Nachhol-Abgleich" in r.message for r in caplog.records)


# --- Verdrahtung in _trigger_deployment ------------------------------------

@pytest.fixture
def mock_bot():
    channel = Mock()
    channel.send = AsyncMock()
    bot = Mock()
    bot.get_channel = Mock(return_value=channel)
    return bot


def _integration(mock_bot, tmp_path, repo="zerodox", url="https://github.com/Commandershadow9/ZERODOX"):
    cfg = MagicMock(spec=Config)
    cfg.github = {
        'enabled': True,
        'webhook_secret': 'secret',
        'auto_deploy': True,
        'deploy_branches': ['main'],
    }
    cfg.channels = {'deployment_log': 99999, 'code_fixes': 99998}
    deploy_baum = tmp_path / f"{repo}-deploy"
    deploy_baum.mkdir(exist_ok=True)
    cfg.projects = {
        repo: {
            'enabled': True,
            'ci_workflows': ['Web Quality'],
            'ci_channel_id': 88888,
            'deploy_path': str(deploy_baum),
            'repo_url': url,
        },
    }
    integration = GitHubIntegration(mock_bot, cfg)
    integration.deployment_manager = MagicMock()
    integration.deployment_manager.deploy_project = AsyncMock(return_value={'success': True})
    integration._send_ci_wait_alert = AsyncMock()
    integration.plane_nachhol_abgleich = Mock(wraps=integration.plane_nachhol_abgleich)
    return integration


async def _trigger(integration, repo_name='zerodox', full_name='Commandershadow9/ZERODOX'):
    return await integration._trigger_deployment(
        repo_name=repo_name,
        branch='main',
        commit_sha='abc1234',
        repo_full_name=full_name,
        full_sha='a' * 40,
    )


def _aufraeumen(integration):
    for task in list(getattr(integration, '_nachhol_abgleich_tasks', {}).values()):
        task.cancel()


@pytest.mark.asyncio
async def test_trigger_timeout_plant_nachhol_und_alarm_nennt_es(mock_bot, tmp_path):
    integration = _integration(mock_bot, tmp_path)
    integration._wait_for_ci_completion = AsyncMock(return_value='timeout')
    try:
        assert await _trigger(integration) == 'blocked'
        assert len(integration._nachhol_abgleich_tasks) == 1
        kwargs = integration._send_ci_wait_alert.call_args.kwargs
        assert kwargs['outcome'] == 'timeout'
        assert kwargs['nachhol_in_min'] == 15
        # Zweiter Timeout: weiterhin nur EIN ausstehender Task, Alarm ohne Satz.
        assert await _trigger(integration) == 'blocked'
        assert len(integration._nachhol_abgleich_tasks) == 1
        assert integration._send_ci_wait_alert.call_args.kwargs['nachhol_in_min'] is None
    finally:
        _aufraeumen(integration)


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', ['success', 'failure', 'missing', 'api_unavailable'])
async def test_trigger_ohne_timeout_kein_nachhol(mock_bot, tmp_path, outcome):
    integration = _integration(mock_bot, tmp_path)
    integration._wait_for_ci_completion = AsyncMock(return_value=outcome)
    try:
        await _trigger(integration)
        integration.plane_nachhol_abgleich.assert_not_called()
        assert not integration._nachhol_abgleich_tasks
    finally:
        _aufraeumen(integration)


@pytest.mark.asyncio
async def test_trigger_timeout_shadowops_bot_kein_nachhol(mock_bot, tmp_path):
    integration = _integration(
        mock_bot, tmp_path, repo='shadowops-bot',
        url='https://github.com/Commandershadow9/shadowops-bot',
    )
    integration._wait_for_ci_completion = AsyncMock(return_value='timeout')
    try:
        await _trigger(integration, repo_name='shadowops-bot',
                       full_name='Commandershadow9/shadowops-bot')
        assert not integration._nachhol_abgleich_tasks
        assert integration._send_ci_wait_alert.call_args.kwargs['nachhol_in_min'] is None
    finally:
        _aufraeumen(integration)


@pytest.mark.asyncio
async def test_alarmtext_nennt_nachhol_nur_wenn_eingeplant(mock_bot, tmp_path):
    integration = _integration(mock_bot, tmp_path)
    del integration._send_ci_wait_alert  # echte Methode
    channel = mock_bot.get_channel.return_value
    await integration._send_ci_wait_alert(
        outcome='timeout', repo_name='zerodox', repo_full_name='Commandershadow9/ZERODOX',
        branch='main', merged_sha='a' * 40, workflow_names=['Web Quality'],
        max_wait_min=30, nachhol_in_min=15,
    )
    embed = channel.send.call_args.kwargs['embed']
    assert 'Nachhol-Abgleich in 15 min eingeplant.' in embed.description
    await integration._send_ci_wait_alert(
        outcome='timeout', repo_name='zerodox', repo_full_name='Commandershadow9/ZERODOX',
        branch='main', merged_sha='a' * 40, workflow_names=['Web Quality'],
        max_wait_min=30,
    )
    assert 'Nachhol-Abgleich' not in channel.send.call_args.kwargs['embed'].description
