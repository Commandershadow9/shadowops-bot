"""
Command Executor - Sichere Shell-Command Ausführung

Provides safe command execution with:
- Timeout protection
- Output capturing (stdout/stderr)
- Error handling
- Dry-run mode for testing
- Command validation and sanitization
"""

import asyncio
import logging
import shlex
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger('shadowops.command_executor')


class ExecutionMode(Enum):
    """Command execution modes"""
    LIVE = "live"           # Actually execute commands
    DRY_RUN = "dry_run"     # Only log what would be executed
    VALIDATE = "validate"   # Validate command syntax only


@dataclass
class CommandResult:
    """Result of command execution"""
    command: str
    success: bool
    stdout: str
    stderr: str
    exit_code: int
    duration_seconds: float
    timestamp: datetime
    mode: ExecutionMode
    error_message: Optional[str] = None


@dataclass
class CommandExecutorConfig:
    """Configuration for command executor"""
    default_timeout: int = 300  # 5 minutes default
    max_timeout: int = 3600     # 1 hour maximum
    dry_run: bool = False       # Global dry-run mode
    require_sudo: bool = False  # Automatically prepend sudo
    shell: str = "/bin/bash"    # Shell to use
    working_dir: Optional[str] = None
    env_vars: Dict[str, str] = field(default_factory=dict)

    # Dangerous command patterns to block
    dangerous_patterns: List[str] = field(default_factory=lambda: [
        r'rm\s+-rf\s+/',          # rm -rf /
        r'dd\s+if=.*of=/dev/',    # dd to disk
        r'mkfs\.',                # filesystem formatting
        r':(){ :|:& };:',         # fork bomb
        r'chmod\s+-R\s+777',      # chmod 777 everything
        r'chown\s+-R\s+',         # recursive chown
        r'shutdown',              # system shutdown
        r'reboot',                # system reboot
        r'halt',                  # system halt
        r'init\s+0',              # init 0
        r'init\s+6',              # init 6
    ])


class CommandExecutor:
    """
    Executes shell commands safely with timeout, output capture, and validation

    Features:
    - Async/await support
    - Configurable timeout per command
    - stdout/stderr capturing
    - Dry-run mode for testing
    - Command validation (block dangerous patterns)
    - Automatic sudo handling
    - Working directory support
    - Environment variable injection
    """

    def __init__(self, config: Optional[CommandExecutorConfig] = None):
        """
        Initialize command executor

        Args:
            config: Executor configuration (uses defaults if not provided)
        """
        self.config = config or CommandExecutorConfig()
        self.execution_history: List[CommandResult] = []
        self.max_history = 1000  # Keep last 1000 commands

        logger.info(f"🔧 Command Executor initialized (mode: {'DRY-RUN' if self.config.dry_run else 'LIVE'})")

    async def execute(
        self,
        command: str,
        timeout: Optional[int] = None,
        mode: Optional[ExecutionMode] = None,
        sudo: Optional[bool] = None,
        working_dir: Optional[str] = None,
        env_vars: Optional[Dict[str, str]] = None,
        capture_output: bool = True
    ) -> CommandResult:
        """
        Execute a shell command

        Args:
            command: Shell command to execute
            timeout: Command timeout in seconds (uses default if not specified)
            mode: Execution mode (LIVE/DRY_RUN/VALIDATE)
            sudo: Whether to prepend sudo (uses config default if not specified)
            working_dir: Working directory for command execution
            env_vars: Additional environment variables
            capture_output: Whether to capture stdout/stderr

        Returns:
            CommandResult with execution details

        Raises:
            ValueError: If command is invalid or dangerous
            TimeoutError: If command exceeds timeout
        """
        start_time = datetime.now()

        # Determine execution mode
        exec_mode = mode or (ExecutionMode.DRY_RUN if self.config.dry_run else ExecutionMode.LIVE)

        # Determine timeout
        cmd_timeout = timeout or self.config.default_timeout
        if cmd_timeout > self.config.max_timeout:
            logger.warning(f"⚠️ Timeout {cmd_timeout}s exceeds max {self.config.max_timeout}s, capping")
            cmd_timeout = self.config.max_timeout

        # Validate command
        self._validate_command(command)

        # Add sudo if needed
        use_sudo = sudo if sudo is not None else self.config.require_sudo
        if use_sudo and not command.strip().startswith('sudo'):
            command = f"sudo {command}"

        # Determine working directory
        work_dir = working_dir or self.config.working_dir

        # Prepare environment
        env = self.config.env_vars.copy()
        if env_vars:
            env.update(env_vars)

        # Log command
        logger.info(f"🔧 Executing ({exec_mode.value}): {command}")
        if work_dir:
            logger.info(f"   📁 Working dir: {work_dir}")
        if env:
            logger.info(f"   🌍 Env vars: {', '.join(env.keys())}")

        # Execute based on mode
        if exec_mode == ExecutionMode.VALIDATE:
            # Only validate syntax
            result = await self._validate_syntax(command)
        elif exec_mode == ExecutionMode.DRY_RUN:
            # Simulate execution
            result = self._simulate_execution(command, cmd_timeout)
        else:  # LIVE mode
            # Actually execute
            result = await self._execute_live(
                command,
                cmd_timeout,
                work_dir,
                env,
                capture_output
            )

        # Calculate duration
        duration = (datetime.now() - start_time).total_seconds()
        result.duration_seconds = duration
        result.timestamp = start_time
        result.mode = exec_mode

        # Add to history
        self.execution_history.append(result)
        if len(self.execution_history) > self.max_history:
            self.execution_history.pop(0)

        # Log result
        if result.success:
            logger.info(f"✅ Command succeeded ({duration:.2f}s)")
            if result.stdout and capture_output:
                logger.debug(f"   stdout: {result.stdout[:200]}")
        else:
            logger.error(f"❌ Command failed ({duration:.2f}s): {result.error_message}")
            if result.stderr and capture_output:
                logger.error(f"   stderr: {result.stderr[:200]}")

        return result

    async def execute_batch(
        self,
        commands: List[str],
        stop_on_error: bool = True,
        **kwargs
    ) -> List[CommandResult]:
        """
        Execute multiple commands sequentially

        Args:
            commands: List of commands to execute
            stop_on_error: Stop batch if a command fails
            **kwargs: Additional arguments passed to execute()

        Returns:
            List of CommandResults
        """
        results = []

        logger.info(f"🔧 Executing batch of {len(commands)} commands")

        for idx, command in enumerate(commands, 1):
            logger.info(f"   [{idx}/{len(commands)}] {command}")

            result = await self.execute(command, **kwargs)
            results.append(result)

            if not result.success and stop_on_error:
                logger.error(f"❌ Batch stopped at command {idx} due to error")
                break

        success_count = sum(1 for r in results if r.success)
        logger.info(f"✅ Batch complete: {success_count}/{len(results)} successful")

        return results

    def _validate_command(self, command: str) -> None:
        """
        Validate command for dangerous patterns

        Args:
            command: Command to validate

        Raises:
            ValueError: If command contains dangerous patterns
        """
        import re

        # Check for dangerous patterns
        for pattern in self.config.dangerous_patterns:
            if re.search(pattern, command, re.IGNORECASE):
                raise ValueError(
                    f"🚨 BLOCKED: Command contains dangerous pattern '{pattern}': {command}"
                )

        # Basic syntax check
        if not command.strip():
            raise ValueError("🚨 BLOCKED: Empty command")

        # Check for null bytes
        if '\0' in command:
            raise ValueError("🚨 BLOCKED: Command contains null bytes")

    async def _validate_syntax(self, command: str) -> CommandResult:
        """
        Validate command syntax without execution

        Args:
            command: Command to validate

        Returns:
            CommandResult with validation status
        """
        try:
            # Try to parse with shlex (basic syntax validation)
            shlex.split(command)

            return CommandResult(
                command=command,
                success=True,
                stdout="Syntax validation passed",
                stderr="",
                exit_code=0,
                duration_seconds=0.0,
                timestamp=datetime.now(),
                mode=ExecutionMode.VALIDATE
            )
        except Exception as e:
            return CommandResult(
                command=command,
                success=False,
                stdout="",
                stderr=f"Syntax error: {str(e)}",
                exit_code=1,
                duration_seconds=0.0,
                timestamp=datetime.now(),
                mode=ExecutionMode.VALIDATE,
                error_message=str(e)
            )

    def _simulate_execution(self, command: str, timeout: int) -> CommandResult:
        """
        Simulate command execution (dry-run)

        Args:
            command: Command to simulate
            timeout: Timeout value (for logging only)

        Returns:
            CommandResult with simulated success
        """
        return CommandResult(
            command=command,
            success=True,
            stdout=f"[DRY-RUN] Would execute: {command}",
            stderr="",
            exit_code=0,
            duration_seconds=0.0,
            timestamp=datetime.now(),
            mode=ExecutionMode.DRY_RUN
        )

    async def _execute_live(
        self,
        command: str,
        timeout: int,
        working_dir: Optional[str],
        env_vars: Dict[str, str],
        capture_output: bool
    ) -> CommandResult:
        """
        Execute command in LIVE mode

        Args:
            command: Command to execute
            timeout: Timeout in seconds
            working_dir: Working directory
            env_vars: Environment variables
            capture_output: Whether to capture stdout/stderr

        Returns:
            CommandResult with execution details
        """
        try:
            # Prepare environment
            import os
            full_env = os.environ.copy()
            full_env.update(env_vars)

            # Execute command
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE if capture_output else asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE if capture_output else asyncio.subprocess.DEVNULL,
                cwd=working_dir,
                env=full_env,
                shell=True,
                executable=self.config.shell
            )

            # Wait for completion with timeout
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout
                )
            except asyncio.TimeoutError:
                # Kill process on timeout
                process.kill()
                await process.wait()
                raise TimeoutError(f"Command timed out after {timeout}s: {command}")

            # Decode output
            stdout = stdout_bytes.decode('utf-8', errors='replace') if stdout_bytes else ""
            stderr = stderr_bytes.decode('utf-8', errors='replace') if stderr_bytes else ""

            # Check exit code
            success = process.returncode == 0

            return CommandResult(
                command=command,
                success=success,
                stdout=stdout,
                stderr=stderr,
                exit_code=process.returncode,
                duration_seconds=0.0,  # Will be set by caller
                timestamp=datetime.now(),
                mode=ExecutionMode.LIVE,
                error_message=stderr if not success else None
            )

        except TimeoutError as e:
            return CommandResult(
                command=command,
                success=False,
                stdout="",
                stderr=str(e),
                exit_code=-1,
                duration_seconds=0.0,
                timestamp=datetime.now(),
                mode=ExecutionMode.LIVE,
                error_message=str(e)
            )
        except Exception as e:
            logger.error(f"❌ Command execution error: {e}", exc_info=True)
            return CommandResult(
                command=command,
                success=False,
                stdout="",
                stderr=str(e),
                exit_code=-1,
                duration_seconds=0.0,
                timestamp=datetime.now(),
                mode=ExecutionMode.LIVE,
                error_message=str(e)
            )

    def get_history(self, limit: int = 100) -> List[CommandResult]:
        """
        Get command execution history

        Args:
            limit: Maximum number of results to return

        Returns:
            List of recent CommandResults
        """
        return self.execution_history[-limit:]

    def get_stats(self) -> Dict:
        """
        Get execution statistics

        Returns:
            Dictionary with statistics
        """
        total = len(self.execution_history)
        successful = sum(1 for r in self.execution_history if r.success)
        failed = total - successful

        avg_duration = 0.0
        if total > 0:
            avg_duration = sum(r.duration_seconds for r in self.execution_history) / total

        return {
            'total_executions': total,
            'successful': successful,
            'failed': failed,
            'success_rate': successful / total if total > 0 else 0.0,
            'average_duration_seconds': avg_duration,
            'mode': self.config.dry_run and 'DRY_RUN' or 'LIVE'
        }
