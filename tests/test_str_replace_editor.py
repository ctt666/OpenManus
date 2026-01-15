"""Unit tests for StrReplaceEditor sandbox_id support (REQ-2.3)."""

from typing import AsyncGenerator

import pytest
import pytest_asyncio

from app.config import SandboxSettings
from app.sandbox.core.manager import SandboxManager
from app.tool.str_replace_editor import StrReplaceEditor


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


class TestStrReplaceEditor:
    """Test cases for StrReplaceEditor with sandbox_id support."""

    @pytest.mark.asyncio
    async def test_create_file_with_sandbox_id(
        self, sandbox_manager: SandboxManager, test_sandbox_id: str
    ):
        """Test creating a file using sandbox_id."""
        tool = StrReplaceEditor()

        result = await tool.execute(
            command="create",
            path="/workspace/test_sandbox_id.txt",
            file_text="Hello from sandbox_id!",
            sandbox_id=test_sandbox_id,
        )

        assert "created successfully" in result.lower()

    @pytest.mark.asyncio
    async def test_read_file_with_sandbox_id(
        self, sandbox_manager: SandboxManager, test_sandbox_id: str
    ):
        """Test reading a file using sandbox_id."""
        tool = StrReplaceEditor()

        # First create a file
        await tool.execute(
            command="create",
            path="/workspace/test_read.txt",
            file_text="Test content for reading",
            sandbox_id=test_sandbox_id,
        )

        # Then read it
        result = await tool.execute(
            command="view",
            path="/workspace/test_read.txt",
            sandbox_id=test_sandbox_id,
        )

        assert "Test content for reading" in result

    @pytest.mark.asyncio
    async def test_str_replace_with_sandbox_id(
        self, sandbox_manager: SandboxManager, test_sandbox_id: str
    ):
        """Test string replacement using sandbox_id."""
        tool = StrReplaceEditor()

        # Create a file
        await tool.execute(
            command="create",
            path="/workspace/test_replace.txt",
            file_text="Old content\nMore old content",
            sandbox_id=test_sandbox_id,
        )

        # Replace string
        result = await tool.execute(
            command="str_replace",
            path="/workspace/test_replace.txt",
            old_str="Old content",
            new_str="New content",
            sandbox_id=test_sandbox_id,
        )

        assert "edited" in result.lower()

        # Verify replacement
        view_result = await tool.execute(
            command="view",
            path="/workspace/test_replace.txt",
            sandbox_id=test_sandbox_id,
        )

        assert "New content" in view_result
        assert "Old content" not in view_result

    @pytest.mark.asyncio
    async def test_multiple_agent_isolation(self, sandbox_manager: SandboxManager):
        """Test that multiple agents with different sandbox_id are isolated."""
        tool = StrReplaceEditor()

        # Create two sandboxes
        sandbox_id1 = await sandbox_manager.create_sandbox()
        sandbox_id2 = await sandbox_manager.create_sandbox()

        try:
            # Create file in sandbox 1
            await tool.execute(
                command="create",
                path="/workspace/isolation_test.txt",
                file_text="Content from agent 1",
                sandbox_id=sandbox_id1,
            )

            # Create file in sandbox 2
            await tool.execute(
                command="create",
                path="/workspace/isolation_test.txt",
                file_text="Content from agent 2",
                sandbox_id=sandbox_id2,
            )

            # Verify isolation
            content1 = await tool.execute(
                command="view",
                path="/workspace/isolation_test.txt",
                sandbox_id=sandbox_id1,
            )

            content2 = await tool.execute(
                command="view",
                path="/workspace/isolation_test.txt",
                sandbox_id=sandbox_id2,
            )

            assert "Content from agent 1" in content1
            assert "Content from agent 2" in content2
        finally:
            await sandbox_manager.delete_sandbox(sandbox_id1)
            await sandbox_manager.delete_sandbox(sandbox_id2)

    @pytest.mark.asyncio
    async def test_backward_compatibility_no_sandbox_id(self):
        """Test backward compatibility: no sandbox_id uses existing logic."""
        tool = StrReplaceEditor()

        # Should work with existing config-based logic
        # Note: This test verifies the method doesn't break, but actual
        # file operations depend on config.sandbox.use_sandbox setting
        operator = tool._get_operator()
        assert operator is not None

    @pytest.mark.asyncio
    async def test_operator_caching(
        self, sandbox_manager: SandboxManager, test_sandbox_id: str
    ):
        """Test that operators are cached per sandbox_id."""
        tool = StrReplaceEditor()

        # Get operator twice for same sandbox_id
        operator1 = tool._get_operator(sandbox_id=test_sandbox_id)
        operator2 = tool._get_operator(sandbox_id=test_sandbox_id)

        # Should be the same instance (cached)
        assert operator1 is operator2

        # Different sandbox_id should get different operator
        sandbox_id2 = await sandbox_manager.create_sandbox()
        try:
            operator3 = tool._get_operator(sandbox_id=sandbox_id2)
            assert operator3 is not operator1
        finally:
            await sandbox_manager.delete_sandbox(sandbox_id2)

    @pytest.mark.asyncio
    async def test_operator_lru_eviction(self, sandbox_manager: SandboxManager):
        """Test LRU eviction when sandbox operator cache exceeds limit."""
        tool = StrReplaceEditor()
        tool.MAX_CACHED_SANDBOX_OPERATORS = 2

        sandbox_id1 = await sandbox_manager.create_sandbox()
        sandbox_id2 = await sandbox_manager.create_sandbox()
        sandbox_id3 = await sandbox_manager.create_sandbox()

        try:
            op1 = tool._get_operator(sandbox_id=sandbox_id1)
            _ = tool._get_operator(sandbox_id=sandbox_id2)
            # Third insert should evict the least-recently-used (sandbox_id1)
            _ = tool._get_operator(sandbox_id=sandbox_id3)

            assert sandbox_id1 not in tool._sandbox_operators
            assert sandbox_id2 in tool._sandbox_operators
            assert sandbox_id3 in tool._sandbox_operators

            # Asking for sandbox_id1 again should create a new instance
            op1_new = tool._get_operator(sandbox_id=sandbox_id1)
            assert op1_new is not op1
        finally:
            await sandbox_manager.delete_sandbox(sandbox_id1)
            await sandbox_manager.delete_sandbox(sandbox_id2)
            await sandbox_manager.delete_sandbox(sandbox_id3)

    def test_file_history_lru_and_version_cap(self):
        """Test file history is bounded by LRU across files and max versions per file."""
        tool = StrReplaceEditor()
        tool.MAX_FILE_HISTORY_KEYS = 2
        tool.MAX_FILE_HISTORY_VERSIONS_PER_FILE = 2

        # Per-file version cap: keep only last 2 versions
        tool._push_file_history("/tmp/a.txt", "v1")
        tool._push_file_history("/tmp/a.txt", "v2")
        tool._push_file_history("/tmp/a.txt", "v3")
        assert list(tool._file_history["/tmp/a.txt"]) == ["v2", "v3"]

        # LRU across files: with max 2 keys, adding a 3rd path evicts the oldest path (/tmp/a.txt)
        tool._push_file_history("/tmp/b.txt", "b1")
        assert "/tmp/a.txt" in tool._file_history
        assert "/tmp/b.txt" in tool._file_history

        tool._push_file_history("/tmp/c.txt", "c1")
        assert "/tmp/a.txt" not in tool._file_history
        assert "/tmp/b.txt" in tool._file_history
        assert "/tmp/c.txt" in tool._file_history
