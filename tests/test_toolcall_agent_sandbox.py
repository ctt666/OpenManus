"""Unit tests for ToolCallAgent sandbox integration (Stage 3: REQ-3.1 ~ REQ-3.3)."""

import json
import uuid
from pathlib import Path
from typing import AsyncGenerator

import pytest
import pytest_asyncio

from app.agent.toolcall import ToolCallAgent
from app.config import config
from app.sandbox.core.manager import SandboxManager
from app.schema import Function, ToolCall
from app.tool import ToolCollection
from app.tool.bash import Bash
from app.tool.python_execute import PythonExecute
from app.tool.str_replace_editor import StrReplaceEditor


@pytest_asyncio.fixture(scope="function")
async def sandbox_manager() -> AsyncGenerator[SandboxManager, None]:
    """Creates a sandbox manager instance for testing."""
    manager = SandboxManager(max_sandboxes=10, idle_timeout=300, cleanup_interval=60)
    try:
        yield manager
    finally:
        await manager.cleanup()


@pytest.fixture(scope="function")
def enable_sandbox():
    """Temporarily enable sandbox in config for tests."""
    original_use_sandbox = config.sandbox.use_sandbox
    config.sandbox.use_sandbox = True
    try:
        yield
    finally:
        config.sandbox.use_sandbox = original_use_sandbox


class TestToolCallAgentSandbox:
    """Test cases for ToolCallAgent sandbox management and routing."""

    @pytest.mark.asyncio
    async def test_ensure_sandbox_lazy_creation(self, enable_sandbox):
        """REQ-3.1: Test lazy sandbox creation and reuse."""
        agent = ToolCallAgent(
            name="test-agent",
            description="Test agent for sandbox",
        )

        # Initially sandbox should not be initialized
        assert agent.sandbox_id is None
        assert agent._sandbox_initialized is False

        # First call should create sandbox
        sandbox_id_1 = await agent._ensure_sandbox()
        assert sandbox_id_1 is not None
        assert agent.sandbox_id == sandbox_id_1
        assert agent._sandbox_initialized is True

        # Second call should reuse existing sandbox
        sandbox_id_2 = await agent._ensure_sandbox()
        assert sandbox_id_2 == sandbox_id_1

        # Cleanup should delete sandbox and reset flags
        await agent.cleanup()
        assert agent.sandbox_id is None
        assert agent._sandbox_initialized is False

    @pytest.mark.asyncio
    async def test_multiple_agents_isolation(self, enable_sandbox):
        """REQ-3.1: Test multiple agents have isolated sandboxes."""
        agent1 = ToolCallAgent(
            name="agent-1",
            description="Agent 1",
        )
        agent2 = ToolCallAgent(
            name="agent-2",
            description="Agent 2",
        )

        sandbox_id_1 = await agent1._ensure_sandbox()
        sandbox_id_2 = await agent2._ensure_sandbox()

        assert sandbox_id_1 is not None
        assert sandbox_id_2 is not None
        assert sandbox_id_1 != sandbox_id_2

        await agent1.cleanup()
        await agent2.cleanup()

    @pytest.mark.asyncio
    async def test_ensure_sandbox_disabled_in_config(self):
        """REQ-3.1: Test behavior when sandbox is disabled in config."""
        agent = ToolCallAgent(
            name="test-agent",
            description="Test agent for sandbox disabled",
        )

        # Ensure sandbox is disabled
        original_use_sandbox = config.sandbox.use_sandbox
        config.sandbox.use_sandbox = False
        try:
            sandbox_id = await agent._ensure_sandbox()
            assert sandbox_id is None
            assert agent.sandbox_id is None
            assert agent._sandbox_initialized is False
        finally:
            config.sandbox.use_sandbox = original_use_sandbox

    @pytest.mark.asyncio
    async def test_tool_routing_to_sandbox_python_execute(self, enable_sandbox):
        """REQ-3.2: Test PythonExecute is routed to sandbox and creates sandbox automatically."""
        agent = ToolCallAgent(
            name="tool-agent",
            description="Tool routing agent",
        )
        # Override available tools to include PythonExecute
        agent.available_tools = ToolCollection(PythonExecute())

        func = Function(
            name="python_execute",
            arguments=json.dumps({"code": "print('Hello from routed sandbox!')"}),
        )
        tool_call = ToolCall(id="1", function=func)

        result = await agent.execute_tool(tool_call)

        # Should have created sandbox
        assert agent.sandbox_id is not None
        assert "Hello from routed sandbox!" in result

        await agent.cleanup()

    @pytest.mark.asyncio
    async def test_tool_routing_to_sandbox_bash(self, enable_sandbox):
        """REQ-3.2: Test Bash is routed to sandbox."""
        agent = ToolCallAgent(
            name="tool-agent-bash",
            description="Tool routing agent for bash",
        )
        agent.available_tools = ToolCollection(Bash())

        func = Function(
            name="bash",
            arguments=json.dumps({"command": "echo 'Hello from bash sandbox!'"}),
        )
        tool_call = ToolCall(id="2", function=func)

        result = await agent.execute_tool(tool_call)

        assert agent.sandbox_id is not None
        assert "Hello from bash sandbox!" in result

        await agent.cleanup()

    @pytest.mark.asyncio
    async def test_tool_routing_str_replace_editor_respects_config(
        self, enable_sandbox
    ):
        """REQ-3.2: Test StrReplaceEditor routing respects config.sandbox.use_sandbox."""
        agent = ToolCallAgent(
            name="tool-agent-editor",
            description="Tool routing agent for editor",
        )
        agent.available_tools = ToolCollection(StrReplaceEditor())

        # Use a unique filename to avoid cross-run pollution because /workspace is
        # typically bound to host config.workspace_root.
        filename = f"test_routed_editor_{uuid.uuid4().hex}.txt"
        sandbox_path = f"/workspace/{filename}"

        func = Function(
            name="str_replace_editor",
            arguments=json.dumps(
                {
                    "command": "create",
                    "path": sandbox_path,
                    "file_text": "Hello from editor sandbox!",
                }
            ),
        )
        tool_call = ToolCall(id="3", function=func)

        result = await agent.execute_tool(tool_call)

        # When sandbox is enabled, editor should run in sandbox and sandbox_id should be set
        assert agent.sandbox_id is not None
        assert "created successfully" in result.lower()

        await agent.cleanup()

        # Cleanup host file if /workspace is mounted to host workspace_root.
        host_file: Path = config.workspace_root / filename
        if host_file.exists():
            host_file.unlink()
