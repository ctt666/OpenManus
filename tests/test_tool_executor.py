import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agent.tool_executor import ToolExecutor
from app.exceptions import ToolError
from app.schema import Function, Message, ToolCall
from app.tool.base import BaseTool, ToolFailure, ToolResult
from app.tool.tool_collection import ToolCollection


class _InputErrorTool(BaseTool):
    name: str = "input_error_tool"
    description: str = "tool that fails when required param missing"
    parameters: dict = {
        "type": "object",
        "properties": {"x": {"type": "string"}},
        "required": ["x"],
    }

    async def execute(self, x: str = None, **kwargs):
        if not x:
            raise ToolError("Parameter `x` is required for command: run")
        return ToolResult(output=f"ok:{x}")


class _RateLimitTool(BaseTool):
    name: str = "rate_limit_tool"
    description: str = "rate limit tool"
    parameters: dict = {"type": "object", "properties": {}}

    async def execute(self, **kwargs):
        return ToolFailure(error="429 Too Many Requests")


class _ServiceErrorTool(BaseTool):
    name: str = "service_error_tool"
    description: str = "service error tool"
    parameters: dict = {"type": "object", "properties": {}}

    async def execute(self, **kwargs):
        return ToolFailure(error="503 Service Unavailable")


@pytest.mark.asyncio
async def test_input_format_triggers_llm_repair_and_executes():
    tools = ToolCollection(_InputErrorTool())
    executor = ToolExecutor()

    # LLM repair returns a tool_call with corrected arguments (same tool)
    fixed_args = json.dumps({"x": "hello"})
    llm = SimpleNamespace()
    llm.ask_tool = AsyncMock(
        return_value=SimpleNamespace(
            tool_calls=[ToolCall(id="repair-1", function=Function(name="input_error_tool", arguments=fixed_args))]
        )
    )

    cmd = ToolCall(id="c1", function=Function(name="input_error_tool", arguments="{}"))

    async def _prepare_args(_name, args):
        return args

    result, used_args_str = await executor.execute_tool_call(
        command=cmd,
        available_tools=tools,
        llm=llm,
        messages=[Message.user_message("do it")],
        agent_name="agent",
        prepare_args=_prepare_args,
        tool_choice_required="required",
    )

    assert isinstance(result, ToolResult)
    assert "ok:hello" in str(result)
    assert json.loads(used_args_str) == {"x": "hello"}
    # Ensure repair path called
    assert llm.ask_tool.await_count >= 1


@pytest.mark.asyncio
async def test_rate_limit_timeout_backoff_and_disable():
    tools = ToolCollection(_RateLimitTool())
    slept = []

    async def _sleep_stub(sec):
        slept.append(sec)

    now = {"t": 1000.0}

    def _now():
        return now["t"]

    executor = ToolExecutor(
        rate_limit_timeout_attempts=2,
        disable_cooldown_seconds=120.0,
        now_fn=_now,
        sleep_fn=_sleep_stub,
    )

    llm = SimpleNamespace()
    llm.ask_tool = AsyncMock()

    cmd = ToolCall(id="c2", function=Function(name="rate_limit_tool", arguments="{}"))

    async def _prepare_args(_name, args):
        return args

    result, _ = await executor.execute_tool_call(
        command=cmd,
        available_tools=tools,
        llm=llm,
        messages=[Message.user_message("do it")],
        agent_name="agent",
        prepare_args=_prepare_args,
        tool_choice_required="required",
    )

    # backoff: 1s, 2s (attempt 0, 1)
    assert slept == [1.0, 2.0]
    assert isinstance(result, ToolResult) and result.error
    assert not executor.is_tool_enabled("rate_limit_tool")

    # tool list should be filtered
    assert executor.get_tools_params(tools) == []

    # after cooldown, tool should reappear
    now["t"] = 2000.0
    assert executor.is_tool_enabled("rate_limit_tool")
    assert len(executor.get_tools_params(tools)) == 1


@pytest.mark.asyncio
async def test_service_error_retries_without_disable():
    tools = ToolCollection(_ServiceErrorTool())
    slept = []

    async def _sleep_stub(sec):
        slept.append(sec)

    executor = ToolExecutor(service_retry_attempts=2, sleep_fn=_sleep_stub)
    llm = SimpleNamespace()
    llm.ask_tool = AsyncMock()

    cmd = ToolCall(id="c3", function=Function(name="service_error_tool", arguments="{}"))

    async def _prepare_args(_name, args):
        return args

    result, _ = await executor.execute_tool_call(
        command=cmd,
        available_tools=tools,
        llm=llm,
        messages=[Message.user_message("do it")],
        agent_name="agent",
        prepare_args=_prepare_args,
        tool_choice_required="required",
    )

    assert slept == [1.0, 2.0]
    assert isinstance(result, ToolResult) and result.error
    assert executor.is_tool_enabled("service_error_tool")


@pytest.mark.asyncio
async def test_disabled_tool_returns_immediately():
    tools = ToolCollection(_RateLimitTool())
    now = {"t": 1000.0}

    def _now():
        return now["t"]

    executor = ToolExecutor(now_fn=_now)
    executor.disabled_until["rate_limit_tool"] = 9999.0

    llm = SimpleNamespace()
    llm.ask_tool = AsyncMock()

    cmd = ToolCall(id="c4", function=Function(name="rate_limit_tool", arguments="{}"))

    async def _prepare_args(_name, args):
        return args

    result, _ = await executor.execute_tool_call(
        command=cmd,
        available_tools=tools,
        llm=llm,
        messages=[Message.user_message("do it")],
        agent_name="agent",
        prepare_args=_prepare_args,
        tool_choice_required="required",
    )

    assert isinstance(result, ToolResult) and result.error
    assert "temporarily disabled" in (result.error or "").lower()
    assert llm.ask_tool.await_count == 0

