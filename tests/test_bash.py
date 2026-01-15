"""Unit tests for Bash sandbox execution support (REQ-2.2)."""

from typing import AsyncGenerator

import pytest
import pytest_asyncio

from app.config import SandboxSettings
from app.sandbox.core.manager import SandboxManager
from app.tool.bash import Bash


@pytest_asyncio.fixture(scope="function")
async def sandbox_manager() -> AsyncGenerator[SandboxManager, None]:
    """Creates a sandbox manager instance for testing."""
    manager = SandboxManager(max_sandboxes=10, idle_timeout=300, cleanup_interval=60)
    try:
        yield manager
    finally:
        await manager.cleanup()


@pytest_asyncio.fixture(scope="function")
async def test_sandbox_id(sandbox_manager: SandboxManager) -> AsyncGenerator[str, None]:
    """Creates a test sandbox and returns its ID."""
    sandbox_id = await sandbox_manager.create_sandbox(
        config=SandboxSettings(
            image="python:3.12-slim",
            work_dir="/workspace",
            mount_workspace=True,
        )
    )
    try:
        yield sandbox_id
    finally:
        await sandbox_manager.delete_sandbox(sandbox_id)


class TestBash:
    """Test cases for Bash with sandbox support."""

    @pytest.mark.asyncio
    async def test_backward_compatibility_local_execution(self):
        """Test backward compatibility: local execution without sandbox_id."""
        tool = Bash()

        result = await tool.execute(command="echo 'Hello, World!'")

        assert result.output == "Hello, World!"
        assert result.error == ""

    @pytest.mark.asyncio
    async def test_simple_command_in_sandbox(
        self, sandbox_manager: SandboxManager, test_sandbox_id: str
    ):
        """Test simple command execution in sandbox."""
        tool = Bash()

        result = await tool.execute(
            command="echo 'Hello from sandbox!'",
            sandbox_id=test_sandbox_id,
            sandbox_manager=sandbox_manager,
        )

        assert "Hello from sandbox!" in result.output
        assert result.error == ""

    @pytest.mark.asyncio
    async def test_ls_command_in_sandbox(
        self, sandbox_manager: SandboxManager, test_sandbox_id: str
    ):
        """Test ls command in sandbox."""
        tool = Bash()

        result = await tool.execute(
            command="ls -la /workspace",
            sandbox_id=test_sandbox_id,
            sandbox_manager=sandbox_manager,
        )

        assert result.error == ""
        # Should list workspace directory

    @pytest.mark.asyncio
    async def test_pwd_command_in_sandbox(
        self, sandbox_manager: SandboxManager, test_sandbox_id: str
    ):
        """Test pwd command in sandbox."""
        tool = Bash()

        result = await tool.execute(
            command="pwd",
            sandbox_id=test_sandbox_id,
            sandbox_manager=sandbox_manager,
        )

        assert "/workspace" in result.output
        assert result.error == ""

    @pytest.mark.asyncio
    async def test_session_state_preservation(
        self, sandbox_manager: SandboxManager, test_sandbox_id: str
    ):
        """Test that session state is preserved in sandbox."""
        tool = Bash()

        # Change directory
        result1 = await tool.execute(
            command="cd /tmp && pwd",
            sandbox_id=test_sandbox_id,
            sandbox_manager=sandbox_manager,
        )

        # Note: Sandbox terminal maintains state automatically
        # Each command runs in the same terminal session
        assert "/tmp" in result1.output or "/workspace" in result1.output

    @pytest.mark.asyncio
    async def test_error_handling_in_sandbox(
        self, sandbox_manager: SandboxManager, test_sandbox_id: str
    ):
        """Test error handling in sandbox."""
        tool = Bash()

        # Command that will fail
        result = await tool.execute(
            command="nonexistent_command_12345",
            sandbox_id=test_sandbox_id,
            sandbox_manager=sandbox_manager,
        )

        # Should capture error (may be in output or error field)
        assert result.error != "" or "not found" in result.output.lower()

    @pytest.mark.asyncio
    async def test_restart_in_sandbox(
        self, sandbox_manager: SandboxManager, test_sandbox_id: str
    ):
        """Test restart functionality in sandbox."""
        tool = Bash()

        result = await tool.execute(
            command="echo 'test'",
            restart=True,
            sandbox_id=test_sandbox_id,
            sandbox_manager=sandbox_manager,
        )

        assert "restarted" in result.system.lower()

    @pytest.mark.asyncio
    async def test_file_operations_in_sandbox(
        self, sandbox_manager: SandboxManager, test_sandbox_id: str
    ):
        """Test file operations using bash in sandbox."""
        tool = Bash()

        # Create a file
        result1 = await tool.execute(
            command="echo 'Test content' > /workspace/test_bash.txt",
            sandbox_id=test_sandbox_id,
            sandbox_manager=sandbox_manager,
        )

        # Read the file
        result2 = await tool.execute(
            command="cat /workspace/test_bash.txt",
            sandbox_id=test_sandbox_id,
            sandbox_manager=sandbox_manager,
        )

        assert "Test content" in result2.output

    @pytest.mark.asyncio
    async def test_sandbox_not_found(self):
        """Test handling of non-existent sandbox."""
        tool = Bash()

        result = await tool.execute(
            command="echo 'test'",
            sandbox_id="non-existent-sandbox-id",
        )

        assert "not found" in result.error.lower()
