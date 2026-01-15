"""Unit tests for PythonExecute sandbox execution support (REQ-2.1)."""

from typing import AsyncGenerator

import pytest
import pytest_asyncio

from app.config import SandboxSettings
from app.sandbox.core.manager import SandboxManager
from app.tool.python_execute import PythonExecute


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


class TestPythonExecute:
    """Test cases for PythonExecute with sandbox support."""

    @pytest.mark.asyncio
    async def test_backward_compatibility_local_execution(self):
        """Test backward compatibility: local execution without sandbox_id."""
        tool = PythonExecute()

        # Simple code execution
        result = await tool.execute(code="print('Hello, World!')", timeout=5)

        assert result["success"] is True
        assert "Hello, World!" in result["observation"]

    @pytest.mark.asyncio
    async def test_simple_code_execution_in_sandbox(
        self, sandbox_manager: SandboxManager, test_sandbox_id: str
    ):
        """Test simple Python code execution in sandbox."""
        tool = PythonExecute()

        result = await tool.execute(
            code="print('Hello from sandbox!')",
            timeout=5,
            sandbox_id=test_sandbox_id,
            sandbox_manager=sandbox_manager,
        )

        assert result["success"] is True
        assert "Hello from sandbox!" in result["observation"]

    @pytest.mark.asyncio
    async def test_code_with_error_in_sandbox(
        self, sandbox_manager: SandboxManager, test_sandbox_id: str
    ):
        """Test Python code with error in sandbox."""
        tool = PythonExecute()

        result = await tool.execute(
            code="print(1/0)",  # Division by zero
            timeout=5,
            sandbox_id=test_sandbox_id,
            sandbox_manager=sandbox_manager,
        )

        # Should capture the error
        assert result["success"] is False
        assert (
            "Error" in result["observation"]
            or "ZeroDivisionError" in result["observation"]
        )

    @pytest.mark.asyncio
    async def test_file_operations_in_sandbox(
        self, sandbox_manager: SandboxManager, test_sandbox_id: str
    ):
        """Test file operations in sandbox."""
        tool = PythonExecute()

        code = """
with open('/workspace/test_file.txt', 'w') as f:
    f.write('Test content')
with open('/workspace/test_file.txt', 'r') as f:
    content = f.read()
print(content)
"""
        result = await tool.execute(
            code=code,
            timeout=10,
            sandbox_id=test_sandbox_id,
            sandbox_manager=sandbox_manager,
        )

        assert result["success"] is True
        assert "Test content" in result["observation"]

    @pytest.mark.asyncio
    async def test_timeout_handling_in_sandbox(
        self, sandbox_manager: SandboxManager, test_sandbox_id: str
    ):
        """Test timeout handling in sandbox."""
        tool = PythonExecute()

        # Code that runs longer than timeout
        code = "import time; time.sleep(10); print('Done')"

        result = await tool.execute(
            code=code,
            timeout=2,  # Short timeout
            sandbox_id=test_sandbox_id,
            sandbox_manager=sandbox_manager,
        )

        # Should timeout
        assert result["success"] is False
        assert (
            "timeout" in result["observation"].lower()
            or "Error" in result["observation"]
        )

    @pytest.mark.asyncio
    async def test_complex_code_in_sandbox(
        self, sandbox_manager: SandboxManager, test_sandbox_id: str
    ):
        """Test complex Python code execution in sandbox."""
        tool = PythonExecute()

        code = """
def fibonacci(n):
    if n <= 1:
        return n
    return fibonacci(n-1) + fibonacci(n-2)

result = fibonacci(10)
print(f'Fibonacci(10) = {result}')
"""
        result = await tool.execute(
            code=code,
            timeout=10,
            sandbox_id=test_sandbox_id,
            sandbox_manager=sandbox_manager,
        )

        assert result["success"] is True
        assert "Fibonacci(10) = 55" in result["observation"]

    @pytest.mark.asyncio
    async def test_sandbox_not_found(self):
        """Test handling of non-existent sandbox."""
        tool = PythonExecute()

        result = await tool.execute(
            code="print('test')",
            timeout=5,
            sandbox_id="non-existent-sandbox-id",
        )

        assert result["success"] is False
        assert "not found" in result["observation"].lower()
