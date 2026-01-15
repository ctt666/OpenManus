"""File operation interfaces and implementations for local and sandbox environments."""

import asyncio
from pathlib import Path
from typing import Optional, Protocol, Tuple, Union, runtime_checkable

from app.config import SandboxSettings
from app.exceptions import ToolError
from app.sandbox.client import SANDBOX_CLIENT
from app.sandbox.core.manager import SandboxManager

PathLike = Union[str, Path]


@runtime_checkable
class FileOperator(Protocol):
    """Interface for file operations in different environments."""

    async def read_file(self, path: PathLike) -> str:
        """Read content from a file."""
        ...

    async def write_file(self, path: PathLike, content: str) -> None:
        """Write content to a file."""
        ...

    async def is_directory(self, path: PathLike) -> bool:
        """Check if path points to a directory."""
        ...

    async def exists(self, path: PathLike) -> bool:
        """Check if path exists."""
        ...

    async def run_command(
        self, cmd: str, timeout: Optional[float] = 120.0
    ) -> Tuple[int, str, str]:
        """Run a shell command and return (return_code, stdout, stderr)."""
        ...


class LocalFileOperator(FileOperator):
    """File operations implementation for local filesystem."""

    encoding: str = "utf-8"

    async def read_file(self, path: PathLike) -> str:
        """Read content from a local file."""
        try:
            return Path(path).read_text(encoding=self.encoding)
        except Exception as e:
            raise ToolError(f"Failed to read {path}: {str(e)}") from None

    async def write_file(self, path: PathLike, content: str) -> None:
        """Write content to a local file."""
        try:
            Path(path).write_text(content, encoding=self.encoding)
        except Exception as e:
            raise ToolError(f"Failed to write to {path}: {str(e)}") from None

    async def is_directory(self, path: PathLike) -> bool:
        """Check if path points to a directory."""
        return Path(path).is_dir()

    async def exists(self, path: PathLike) -> bool:
        """Check if path exists."""
        return Path(path).exists()

    async def run_command(
        self, cmd: str, timeout: Optional[float] = 120.0
    ) -> Tuple[int, str, str]:
        """Run a shell command locally."""
        process = await asyncio.create_subprocess_shell(
            cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=timeout
            )
            return (
                process.returncode or 0,
                stdout.decode(),
                stderr.decode(),
            )
        except asyncio.TimeoutError as exc:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            raise TimeoutError(
                f"Command '{cmd}' timed out after {timeout} seconds"
            ) from exc


class SandboxFileOperator(FileOperator):
    """File operations implementation for sandbox environment.

    Supports both global SANDBOX_CLIENT (backward compatible) and
    sandbox_id-based access through SandboxManager.
    """

    def __init__(
        self,
        sandbox_id: Optional[str] = None,
        sandbox_manager: Optional[SandboxManager] = None,
    ):
        """Initialize SandboxFileOperator.

        Args:
            sandbox_id: Optional sandbox ID to use. If provided, uses SandboxManager
                to access the specific sandbox. If None, uses global SANDBOX_CLIENT
                (backward compatible).
            sandbox_manager: Optional SandboxManager instance. If None, uses
                global singleton instance.
        """
        self.sandbox_id = sandbox_id
        self.sandbox_manager = sandbox_manager or SandboxManager()
        self.sandbox_client = SANDBOX_CLIENT

    async def _get_sandbox_client(self):
        """Get the appropriate sandbox client for the current operation.

        Returns:
            Sandbox client instance (either from SandboxManager or global SANDBOX_CLIENT).
        """
        if self.sandbox_id:
            # Use SandboxManager to get the specific sandbox
            # Note: get_sandbox uses context manager, so we need to use it directly
            # in each operation method, not here
            raise RuntimeError(
                "Cannot get sandbox client directly when using sandbox_id. "
                "Use _execute_with_sandbox() instead."
            )
        else:
            # Use global SANDBOX_CLIENT (backward compatible)
            await self._ensure_sandbox_initialized()
            return self.sandbox_client

    async def _execute_with_sandbox(self, operation):
        """Execute an operation with sandbox from SandboxManager.

        Args:
            operation: Async function that takes a sandbox client and returns result.

        Returns:
            Result of the operation.
        """
        if self.sandbox_id:
            # Use SandboxManager context manager to get sandbox
            async with self.sandbox_manager.sandbox_operation(
                self.sandbox_id
            ) as sandbox:
                wrapper = _SandboxWrapper(sandbox)
                return await operation(wrapper)
        else:
            # Use global SANDBOX_CLIENT
            await self._ensure_sandbox_initialized()
            return await operation(self.sandbox_client)

    async def _ensure_sandbox_initialized(self):
        """Ensure sandbox is initialized (for backward compatibility)."""
        if not self.sandbox_client.sandbox:
            await self.sandbox_client.create(config=SandboxSettings())

    async def read_file(self, path: PathLike) -> str:
        """Read content from a file in sandbox."""

        async def _read(client):
            return await client.read_file(str(path))

        try:
            return await self._execute_with_sandbox(_read)
        except Exception as e:
            raise ToolError(f"Failed to read {path} in sandbox: {str(e)}") from None

    async def write_file(self, path: PathLike, content: str) -> None:
        """Write content to a file in sandbox."""

        async def _write(client):
            await client.write_file(str(path), content)

        try:
            await self._execute_with_sandbox(_write)
        except Exception as e:
            raise ToolError(f"Failed to write to {path} in sandbox: {str(e)}") from None

    async def is_directory(self, path: PathLike) -> bool:
        """Check if path points to a directory in sandbox."""

        async def _check(client):
            result = await client.run_command(
                f"test -d {path} && echo 'true' || echo 'false'",
                timeout=120,
            )
            return result.strip() == "true"

        return await self._execute_with_sandbox(_check)

    async def exists(self, path: PathLike) -> bool:
        """Check if path exists in sandbox."""

        async def _check(client):
            result = await client.run_command(
                f"test -e {path} && echo 'true' || echo 'false'",
                timeout=120,
            )
            return result.strip() == "true"

        return await self._execute_with_sandbox(_check)

    async def run_command(
        self, cmd: str, timeout: Optional[float] = 120.0
    ) -> Tuple[int, str, str]:
        """Run a command in sandbox environment."""

        async def _run(client):
            stdout = await client.run_command(
                cmd, timeout=int(timeout) if timeout else None
            )
            return (
                0,  # Always return 0 since we don't have explicit return code from sandbox
                stdout,
                "",  # No stderr capture in the current sandbox implementation
            )

        try:
            return await self._execute_with_sandbox(_run)
        except TimeoutError as exc:
            raise TimeoutError(
                f"Command '{cmd}' timed out after {timeout} seconds in sandbox"
            ) from exc
        except Exception as exc:
            return 1, "", f"Error executing command in sandbox: {str(exc)}"


class _SandboxWrapper:
    """Wrapper to make DockerSandbox compatible with SANDBOX_CLIENT interface."""

    def __init__(self, sandbox):
        """Initialize wrapper with DockerSandbox instance."""
        self.sandbox = sandbox

    async def read_file(self, path: str) -> str:
        """Read file from sandbox."""
        return await self.sandbox.read_file(path)

    async def write_file(self, path: str, content: str) -> None:
        """Write file to sandbox."""
        await self.sandbox.write_file(path, content)

    async def run_command(self, command: str, timeout: Optional[int] = None) -> str:
        """Run command in sandbox."""
        return await self.sandbox.run_command(command, timeout=timeout)
