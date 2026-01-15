"""Unit tests for SandboxFileOperator sandbox_id support (REQ-1.2)."""

import asyncio
from pathlib import Path
from typing import AsyncGenerator

import pytest
import pytest_asyncio

from app.config import SandboxSettings
from app.sandbox.core.manager import SandboxManager
from app.tool.file_operators import SandboxFileOperator


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


class TestSandboxFileOperator:
    """Test cases for SandboxFileOperator with sandbox_id support."""

    @pytest.mark.asyncio
    async def test_backward_compatibility_no_sandbox_id(self):
        """Test backward compatibility: no sandbox_id uses global SANDBOX_CLIENT."""
        operator = SandboxFileOperator()
        assert operator.sandbox_id is None
        assert operator.sandbox_manager is not None

        # Should not raise error (will use global SANDBOX_CLIENT)
        # Note: This test doesn't actually create a sandbox, just verifies
        # that the operator can be instantiated without sandbox_id

    @pytest.mark.asyncio
    async def test_with_sandbox_id(
        self, sandbox_manager: SandboxManager, test_sandbox_id: str
    ):
        """Test SandboxFileOperator with sandbox_id."""
        operator = SandboxFileOperator(
            sandbox_id=test_sandbox_id, sandbox_manager=sandbox_manager
        )
        assert operator.sandbox_id == test_sandbox_id
        assert operator.sandbox_manager is sandbox_manager

    @pytest.mark.asyncio
    async def test_file_operations_with_sandbox_id(
        self, sandbox_manager: SandboxManager, test_sandbox_id: str
    ):
        """Test file operations using sandbox_id."""
        operator = SandboxFileOperator(
            sandbox_id=test_sandbox_id, sandbox_manager=sandbox_manager
        )

        # Test write_file
        test_path = "/workspace/test_file.txt"
        test_content = "Hello, Sandbox!"
        await operator.write_file(test_path, test_content)

        # Test read_file
        content = await operator.read_file(test_path)
        assert content == test_content

        # Test exists
        assert await operator.exists(test_path) is True
        assert await operator.exists("/workspace/nonexistent.txt") is False

        # Test is_directory
        assert await operator.is_directory("/workspace") is True
        assert await operator.is_directory(test_path) is False

    @pytest.mark.asyncio
    async def test_run_command_with_sandbox_id(
        self, sandbox_manager: SandboxManager, test_sandbox_id: str
    ):
        """Test run_command using sandbox_id."""
        operator = SandboxFileOperator(
            sandbox_id=test_sandbox_id, sandbox_manager=sandbox_manager
        )

        returncode, stdout, stderr = await operator.run_command("echo 'test output'")
        assert returncode == 0
        assert "test output" in stdout
        assert stderr == ""

    @pytest.mark.asyncio
    async def test_multiple_sandbox_isolation(self, sandbox_manager: SandboxManager):
        """Test that multiple sandboxes are isolated."""
        # Create two sandboxes
        sandbox_id1 = await sandbox_manager.create_sandbox()
        sandbox_id2 = await sandbox_manager.create_sandbox()

        try:
            operator1 = SandboxFileOperator(
                sandbox_id=sandbox_id1, sandbox_manager=sandbox_manager
            )
            operator2 = SandboxFileOperator(
                sandbox_id=sandbox_id2, sandbox_manager=sandbox_manager
            )

            # Write different content to same path in different sandboxes
            test_path = "/workspace/isolation_test.txt"
            await operator1.write_file(test_path, "Content from sandbox 1")
            await operator2.write_file(test_path, "Content from sandbox 2")

            # Verify isolation: each sandbox has its own content
            content1 = await operator1.read_file(test_path)
            content2 = await operator2.read_file(test_path)

            assert content1 == "Content from sandbox 1"
            assert content2 == "Content from sandbox 2"
        finally:
            await sandbox_manager.delete_sandbox(sandbox_id1)
            await sandbox_manager.delete_sandbox(sandbox_id2)

    @pytest.mark.asyncio
    async def test_sandbox_manager_singleton(self):
        """Test that SandboxManager is a singleton."""
        manager1 = SandboxManager()
        manager2 = SandboxManager()

        # Should be the same instance
        assert manager1 is manager2
