import asyncio
import os
from typing import Optional

from app.exceptions import ToolError
from app.sandbox.core.manager import SandboxManager
from app.tool.base import BaseTool, CLIResult

_BASH_DESCRIPTION = """Execute a bash command in the terminal.
* Long running commands: For commands that may run indefinitely, it should be run in the background and the output should be redirected to a file, e.g. command = `python3 app_demo.py > server.log 2>&1 &`.
* Interactive: If a bash command returns exit code `-1`, this means the process is not yet finished. The assistant must then send a second call to terminal with an empty `command` (which will retrieve any additional logs), or it can send additional text (set `command` to the text) to STDIN of the running process, or it can send command=`ctrl+c` to interrupt the process.
* Timeout: If a command execution result says "Command timed out. Sending SIGINT to the process", the assistant should retry running the command in the background.
"""


class _BashSession:
    """A session of a bash shell."""

    _started: bool
    _process: asyncio.subprocess.Process

    command: str = "/bin/bash"
    _output_delay: float = 0.2  # seconds
    _timeout: float = 120.0  # seconds
    _sentinel: str = "<<exit>>"

    def __init__(self):
        self._started = False
        self._timed_out = False

    async def start(self):
        if self._started:
            return

        self._process = await asyncio.create_subprocess_shell(
            self.command,
            preexec_fn=os.setsid,
            shell=True,
            bufsize=0,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        self._started = True

    def stop(self):
        """Terminate the bash shell."""
        if not self._started:
            raise ToolError("Session has not started.")
        if self._process.returncode is not None:
            return
        self._process.terminate()

    async def run(self, command: str):
        """Execute a command in the bash shell."""
        if not self._started:
            raise ToolError("Session has not started.")
        if self._process.returncode is not None:
            return CLIResult(
                system="tool must be restarted",
                error=f"bash has exited with returncode {self._process.returncode}",
            )
        if self._timed_out:
            raise ToolError(
                f"timed out: bash has not returned in {self._timeout} seconds and must be restarted",
            )

        # we know these are not None because we created the process with PIPEs
        assert self._process.stdin
        assert self._process.stdout
        assert self._process.stderr

        # send command to the process
        self._process.stdin.write(
            command.encode() + f"; echo '{self._sentinel}'\n".encode()
        )
        await self._process.stdin.drain()

        # read output from the process, until the sentinel is found
        try:
            async with asyncio.timeout(self._timeout):
                while True:
                    await asyncio.sleep(self._output_delay)
                    # if we read directly from stdout/stderr, it will wait forever for
                    # EOF. use the StreamReader buffer directly instead.
                    output = (
                        self._process.stdout._buffer.decode()
                    )  # pyright: ignore[reportAttributeAccessIssue]
                    if self._sentinel in output:
                        # strip the sentinel and break
                        output = output[: output.index(self._sentinel)]
                        break
        except asyncio.TimeoutError:
            self._timed_out = True
            raise ToolError(
                f"timed out: bash has not returned in {self._timeout} seconds and must be restarted",
            ) from None

        if output.endswith("\n"):
            output = output[:-1]

        error = (
            self._process.stderr._buffer.decode()
        )  # pyright: ignore[reportAttributeAccessIssue]
        if error.endswith("\n"):
            error = error[:-1]

        # clear the buffers so that the next output can be read correctly
        self._process.stdout._buffer.clear()  # pyright: ignore[reportAttributeAccessIssue]
        self._process.stderr._buffer.clear()  # pyright: ignore[reportAttributeAccessIssue]

        return CLIResult(output=output, error=error)


class Bash(BaseTool):
    """A tool for executing bash commands"""

    name: str = "bash"
    description: str = _BASH_DESCRIPTION
    parameters: dict = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The bash command to execute. Can be empty to view additional logs when previous exit code is `-1`. Can be `ctrl+c` to interrupt the currently running process.",
            },
        },
        "required": ["command"],
    }

    _session: Optional[_BashSession] = None
    _sandbox_sessions: dict[str, dict] = (
        {}
    )  # Track sandbox sessions for state (working_dir, etc.)

    async def execute(
        self,
        command: str | None = None,
        restart: bool = False,
        sandbox_id: Optional[str] = None,
        sandbox_manager: Optional[SandboxManager] = None,
        **kwargs,
    ) -> CLIResult:
        """
        Execute a bash command.

        Args:
            command: The bash command to execute.
            restart: Whether to restart the session.
            sandbox_id: Optional sandbox ID to execute in Docker sandbox.
                If provided, command will be executed in the sandbox.
            sandbox_manager: Optional SandboxManager instance.
            **kwargs: Additional arguments.

        Returns:
            CLIResult: Command execution result.
        """
        # If sandbox_id is provided, execute in Docker sandbox
        if sandbox_id:
            return await self._execute_in_sandbox(
                command, restart, sandbox_id, sandbox_manager
            )

        # Otherwise, use local execution (backward compatible)
        return await self._execute_locally(command, restart)

    async def _execute_in_sandbox(
        self,
        command: str | None,
        restart: bool,
        sandbox_id: str,
        sandbox_manager: Optional[SandboxManager],
    ) -> CLIResult:
        """Execute command in Docker sandbox.

        Tracks sandbox session state (like working directory) in _sandbox_sessions.
        Note: Sandbox terminal maintains session state automatically, but we track
        additional metadata here for potential future use.
        """
        manager = sandbox_manager or SandboxManager()

        if restart:
            # Clear sandbox session state
            if sandbox_id in self._sandbox_sessions:
                del self._sandbox_sessions[sandbox_id]
            return CLIResult(system="tool has been restarted.")

        if command is None:
            raise ToolError("no command provided.")

        # Initialize session state if not exists
        if sandbox_id not in self._sandbox_sessions:
            self._sandbox_sessions[sandbox_id] = {
                "working_dir": "/workspace",  # Default working directory
                "initialized": False,
            }

        try:
            # Get sandbox instance
            async with manager.sandbox_operation(sandbox_id) as sandbox:
                # Execute command in sandbox
                # Note: Sandbox terminal maintains session state automatically
                # We track working directory changes for reference
                try:
                    # If command changes directory, update our tracking
                    if command.strip().startswith("cd "):
                        # Extract target directory (simplified, may not handle all cases)
                        parts = command.strip().split(None, 1)
                        if len(parts) > 1:
                            target_dir = parts[1].strip().strip("'\"")
                            self._sandbox_sessions[sandbox_id][
                                "working_dir"
                            ] = target_dir

                    output = await sandbox.run_command(command, timeout=120)

                    # Update session state if needed (e.g., after pwd command)
                    if command.strip() == "pwd":
                        if output.strip():
                            self._sandbox_sessions[sandbox_id][
                                "working_dir"
                            ] = output.strip()

                    self._sandbox_sessions[sandbox_id]["initialized"] = True
                    return CLIResult(output=output, error="")
                except Exception as e:
                    error_msg = str(e)
                    return CLIResult(
                        output="",
                        error=f"Error executing command in sandbox: {error_msg}",
                    )
        except KeyError:
            # Sandbox not found, clean up session state
            if sandbox_id in self._sandbox_sessions:
                del self._sandbox_sessions[sandbox_id]
            return CLIResult(output="", error=f"Sandbox {sandbox_id} not found")
        except Exception as e:
            return CLIResult(output="", error=f"Error accessing sandbox: {str(e)}")

    async def _execute_locally(self, command: str | None, restart: bool) -> CLIResult:
        """Execute command locally (backward compatible)."""
        if restart:
            if self._session:
                self._session.stop()
            self._session = _BashSession()
            await self._session.start()

            return CLIResult(system="tool has been restarted.")

        if self._session is None:
            self._session = _BashSession()
            await self._session.start()

        if command is not None:
            return await self._session.run(command)

        raise ToolError("no command provided.")


if __name__ == "__main__":
    bash = Bash()
    rst = asyncio.run(bash.execute("ls -l"))
    print(rst)
