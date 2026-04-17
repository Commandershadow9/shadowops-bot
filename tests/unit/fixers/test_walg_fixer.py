import pytest
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
from src.integrations.fixers.walg_fixer import WalGFixer
from src.integrations.command_executor import CommandResult, ExecutionMode
from datetime import datetime

@pytest.fixture
def mock_executor():
    executor = MagicMock()
    executor.execute = AsyncMock()
    return executor

@pytest.mark.asyncio
async def test_walg_fixer_already_updated(mock_executor):
    # Setup: mock wal-g --version returning v3.0.8
    mock_executor.execute.side_effect = [
        CommandResult("/usr/local/bin/wal-g", True, "/usr/local/bin/wal-g", "", 0, 0.1, datetime.now(), ExecutionMode.LIVE), # which
        CommandResult("wal-g --version", True, "wal-g version v3.0.8", "", 0, 0.1, datetime.now(), ExecutionMode.LIVE)      # --version
    ]

    fixer = WalGFixer(executor=mock_executor)
    event = {'affected_files': ['/usr/local/bin/wal-g']}
    strategy = {}

    result = await fixer.fix(event, strategy)

    assert result['status'] == 'success'
    assert 'already at a secure version' in result['message']
    assert mock_executor.execute.await_count == 2

@pytest.mark.asyncio
async def test_walg_fixer_needs_update_success(mock_executor):
    # Mocking a sequence of commands for a successful update
    # 1. which wal-g
    # 2. wal-g --version (old)
    # 3. uname -m
    # 4. curl (20.04) -> success
    # 5. ls (current) -> exists
    # 6. sudo cp (backup)
    # 7. chmod
    # 8. sudo mkdir
    # 9. sudo mv
    # 10. sudo chown
    # 11. wal-g --version (new)

    mock_executor.execute.side_effect = [
        CommandResult("which", True, "/usr/local/bin/wal-g", "", 0, 0.1, datetime.now(), ExecutionMode.LIVE),
        CommandResult("version_old", True, "wal-g version v3.0.7", "", 0, 0.1, datetime.now(), ExecutionMode.LIVE),
        CommandResult("uname", True, "x86_64", "", 0, 0.1, datetime.now(), ExecutionMode.LIVE),
        CommandResult("curl", True, "", "", 0, 0.1, datetime.now(), ExecutionMode.LIVE),
        CommandResult("ls", True, "", "", 0, 0.1, datetime.now(), ExecutionMode.LIVE),
        CommandResult("cp", True, "", "", 0, 0.1, datetime.now(), ExecutionMode.LIVE),
        CommandResult("chmod", True, "", "", 0, 0.1, datetime.now(), ExecutionMode.LIVE),
        CommandResult("mkdir", True, "", "", 0, 0.1, datetime.now(), ExecutionMode.LIVE),
        CommandResult("mv", True, "", "", 0, 0.1, datetime.now(), ExecutionMode.LIVE),
        CommandResult("chown", True, "", "", 0, 0.1, datetime.now(), ExecutionMode.LIVE),
        CommandResult("version_new", True, "wal-g version v3.0.8", "", 0, 0.1, datetime.now(), ExecutionMode.LIVE),
    ]

    with patch.object(WalGFixer, '_calculate_sha256', return_value="9a09c2b1afad6a4e7d87444b34726dd098b60ec816032af921d2f887e6e285c5"):
        fixer = WalGFixer(executor=mock_executor)
        event = {}
        strategy = {}

        result = await fixer.fix(event, strategy)

        assert result['status'] == 'success'
        assert 'successfully updated' in result['message']
        assert result['details']['new_version'] == 'v3.0.8'

@pytest.mark.asyncio
async def test_walg_fixer_rollback_on_fail(mock_executor):
    # 1. which
    # 2. version old
    # 3. uname
    # 4. curl success
    # 5. ls exists
    # 6. cp backup success
    # 7. chmod success
    # 8. mkdir success
    # 9. mv success
    # 10. chown success
    # 11. version new -> FAIL (returns old version or error)
    # 12. ls backup exists
    # 13. mv backup (rollback)

    mock_executor.execute.side_effect = [
        CommandResult("which", True, "/usr/local/bin/wal-g", "", 0, 0.1, datetime.now(), ExecutionMode.LIVE),
        CommandResult("version_old", True, "wal-g version v3.0.7", "", 0, 0.1, datetime.now(), ExecutionMode.LIVE),
        CommandResult("uname", True, "x86_64", "", 0, 0.1, datetime.now(), ExecutionMode.LIVE),
        CommandResult("curl", True, "", "", 0, 0.1, datetime.now(), ExecutionMode.LIVE),
        CommandResult("ls", True, "", "", 0, 0.1, datetime.now(), ExecutionMode.LIVE),
        CommandResult("cp", True, "", "", 0, 0.1, datetime.now(), ExecutionMode.LIVE),
        CommandResult("chmod", True, "", "", 0, 0.1, datetime.now(), ExecutionMode.LIVE),
        CommandResult("mkdir", True, "", "", 0, 0.1, datetime.now(), ExecutionMode.LIVE),
        CommandResult("mv", True, "", "", 0, 0.1, datetime.now(), ExecutionMode.LIVE),
        CommandResult("chown", True, "", "", 0, 0.1, datetime.now(), ExecutionMode.LIVE),
        CommandResult("version_new", True, "wal-g version v3.0.7", "", 0, 0.1, datetime.now(), ExecutionMode.LIVE), # Verification failed
        CommandResult("ls_bak", True, "", "", 0, 0.1, datetime.now(), ExecutionMode.LIVE), # Check backup
        CommandResult("rollback", True, "", "", 0, 0.1, datetime.now(), ExecutionMode.LIVE), # Perform rollback
    ]

    with patch.object(WalGFixer, '_calculate_sha256', return_value="9a09c2b1afad6a4e7d87444b34726dd098b60ec816032af921d2f887e6e285c5"):
        fixer = WalGFixer(executor=mock_executor)
        result = await fixer.fix({}, {})

        assert result['status'] == 'failed'
        assert 'verification failed' in result['error']
        # Rollback happened? Last command should be mv rollback
        assert "sudo mv" in mock_executor.execute.call_args_list[-1][0][0]
        assert ".bak_security_update" in mock_executor.execute.call_args_list[-1][0][0]
