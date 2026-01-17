import asyncio
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from app.logger import logger
from app.schema import Message
from app.tool.base import ToolFailure, ToolResult


class ToolErrorCategory(str, Enum):
    INPUT_FORMAT = "input_format"  # arguments JSON/required params/schema-like
    SERVICE = "service"  # transient service instability
    RATE_LIMIT_TIMEOUT = "rate_limit_timeout"  # rate limit or timeout
    OTHER = "other"


@dataclass
class ToolTraceEntry:
    ts: float
    tool_call_id: str
    tool_name: str
    phase: str  # parse/execute/retry/llm_repair/disable
    attempt: int
    category: ToolErrorCategory = ToolErrorCategory.OTHER
    ok: bool = False
    error: Optional[str] = None
    detail: Dict[str, Any] = field(default_factory=dict)


class ToolExecutor:
    """
    ToolExecutor 负责 tool 调用容错：入参容错 + 分类重试 + 指数退避 + 临时摘除 + trace 记录。

    设计约束（最小改动）：
    - 不修改 tool 实现；不依赖 server 返回结构；只通过 logger 记录链路
    - “指定调用工具”通过 prompt 指定 + 仅提供目标 tool 的 tools 列表（而不是 tool_choice dict）
    - 工具摘除仅对当前 agent 实例生效（ToolExecutor 实例内状态）
    """

    def __init__(
        self,
        *,
        llm_fix_attempts: int = 2,
        service_retry_attempts: int = 2,
        service_retry_delays: Tuple[float, ...] = (1.0, 2.0),
        rate_limit_timeout_attempts: int = 2,
        rate_limit_base_delay: float = 1.0,
        rate_limit_backoff_factor: float = 2.0,
        disable_cooldown_seconds: float = 120.0,
        now_fn: Callable[[], float] = time.time,
        sleep_fn: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ):
        self.llm_fix_attempts = int(llm_fix_attempts)
        self.service_retry_attempts = int(service_retry_attempts)
        self.service_retry_delays = tuple(service_retry_delays)
        self.rate_limit_timeout_attempts = int(rate_limit_timeout_attempts)
        self.rate_limit_base_delay = float(rate_limit_base_delay)
        self.rate_limit_backoff_factor = float(rate_limit_backoff_factor)
        self.disable_cooldown_seconds = float(disable_cooldown_seconds)

        self._now = now_fn
        self._sleep = sleep_fn

        # tool_name -> unix timestamp (seconds) until enabled
        self.disabled_until: Dict[str, float] = {}
        self.trace: List[ToolTraceEntry] = []

    # ---------------- public APIs ----------------
    def get_tools_params(self, available_tools) -> List[dict]:
        """返回过滤后的 tools params（摘除期内工具不会出现在 LLM 可见 tools 中）。"""
        self._prune_disabled()
        params = []
        for tool in getattr(available_tools, "tools", []) or []:
            if not self.is_tool_enabled(tool.name):
                continue
            params.append(tool.to_param())
        return params

    def is_tool_enabled(self, tool_name: str) -> bool:
        until = self.disabled_until.get(tool_name)
        return (until is None) or (self._now() >= until)

    async def execute_tool_call(
        self,
        *,
        command,
        available_tools,
        llm,
        messages: List[Message],
        agent_name: str,
        prepare_args: Callable[[str, Dict[str, Any]], Awaitable[Dict[str, Any]]],
        tool_choice_required: str = "required",
    ) -> Tuple[Any, Optional[str]]:
        """
        执行一个 tool call（带容错与重试）。

        Returns:
            (result, used_arguments_str)
            - result: tool 执行返回（可能是 str / ToolResult / 任意）
            - used_arguments_str: 最终用于执行的 arguments JSON 字符串（用于写回 command.function.arguments）
        """
        tool_call_id = getattr(command, "id", "") or ""
        function = getattr(command, "function", None)
        tool_name = getattr(function, "name", None) if function else None
        if not tool_name:
            return ToolFailure(error="Invalid command format"), None

        if not self.is_tool_enabled(tool_name):
            until = self.disabled_until.get(tool_name, 0.0)
            msg = f"Tool temporarily disabled until {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(until))}"
            self._trace(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                phase="disabled",
                attempt=0,
                ok=False,
                category=ToolErrorCategory.RATE_LIMIT_TIMEOUT,
                error=msg,
                detail={"disabled_until": until},
            )
            return ToolFailure(error=msg), getattr(function, "arguments", None)

        # ---- parse args (with LLM repair on format errors) ----
        original_args_str = getattr(function, "arguments", None) or "{}"
        args_str = original_args_str
        args: Optional[Dict[str, Any]] = None

        def _fail_parse(_err_text: Optional[str], _err_kind: Optional[str]):
            if _err_kind == "json":
                return (
                    ToolFailure(
                        error=f"Error parsing arguments for {tool_name}: Invalid JSON format"
                    ),
                    args_str,
                )
            return (
                ToolFailure(
                    error=f"Error parsing arguments for {tool_name}: {_err_text}"
                ),
                args_str,
            )

        def _trace_parse_ok(_attempt: int, _args: Dict[str, Any]) -> None:
            self._trace(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                phase="parse_args",
                attempt=_attempt,
                ok=True,
                detail={"args_keys": list(_args.keys())[:20]},
            )

        def _trace_parse_fail(_attempt: int, _err: str) -> None:
            self._trace(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                phase="parse_args",
                attempt=_attempt,
                ok=False,
                category=ToolErrorCategory.INPUT_FORMAT,
                error=_err,
                detail={"arguments": args_str},
            )

        def _try_parse(
            _attempt: int,
        ) -> Tuple[Optional[Dict[str, Any]], Optional[str], Optional[str]]:
            """
            Returns: (args_dict, err_text, err_kind)
              - err_kind: "json" | "other"
            """
            try:
                parsed = json.loads(args_str or "{}")
                if not isinstance(parsed, dict):
                    raise ValueError("Tool arguments must be a JSON object")
                _trace_parse_ok(_attempt, parsed)
                return parsed, None, None
            except json.JSONDecodeError as e:
                err = f"Invalid JSON format: {e}"
                _trace_parse_fail(_attempt, err)
                return None, err, "json"
            except Exception as e:
                err = f"Invalid arguments: {e}"
                _trace_parse_fail(_attempt, err)
                return None, err, "other"

        # 先在 for 循环外做一次格式校验；失败后才进入修复重试循环（更清晰）
        args, err_text, err_kind = _try_parse(0)
        if args is None:
            if self.llm_fix_attempts <= 0:
                return _fail_parse(err_text, err_kind)

            # 失败后再进入修复重试：attempt 从 1 开始
            for fix_attempt in range(1, self.llm_fix_attempts + 1):
                repaired = await self._repair_arguments_via_llm(
                    llm=llm,
                    messages=messages,
                    available_tools=available_tools,
                    tool_name=tool_name,
                    tool_call_id=tool_call_id,
                    agent_name=agent_name,
                    error=err_text or "Invalid arguments",
                    original_arguments=args_str,
                    tool_choice_required=tool_choice_required,
                )
                # repair 返回 None：不提前终止，继续下一次修复尝试，直到用尽 llm_fix_attempts
                if repaired is None:
                    continue

                args_str = repaired
                args, err_text, err_kind = _try_parse(fix_attempt)
                if args is not None:
                    break

            if args is None:
                return _fail_parse(err_text, err_kind)

        # ---- execute with retries ----
        # We may further LLM-repair on ToolFailure that looks like input-format issues.
        # 重要：不同重试策略的计数需要解耦，否则会互相“抢次数”
        exec_attempt = 0  # 每次真正执行 tool 的次数（用于 trace / 日志）
        llm_repair_used = 0  # INPUT_FORMAT 的 LLM 修复次数
        service_retry_used = 0  # 服务异常重试次数
        rate_retry_used = 0  # 限流/超时重试次数

        async def _do_execute(_args: Dict[str, Any], attempt: int) -> Any:
            prepared = await prepare_args(tool_name, dict(_args))
            self._trace(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                phase="execute",
                attempt=attempt,
                ok=True,
                detail={"args_keys": list(prepared.keys())[:20]},
            )
            return await available_tools.execute(name=tool_name, tool_input=prepared)

        # First attempt + potential retries
        while True:
            try:
                result = await _do_execute(args, attempt=exec_attempt)
            except Exception as e:
                err_text = str(e)
                category = self._classify_error(err_text)
                self._trace(
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    phase="execute_exception",
                    attempt=exec_attempt,
                    ok=False,
                    category=category,
                    error=err_text,
                )
                result = ToolFailure(error=err_text)
            # Determine if this is a failure we should handle
            failure_text = self._extract_error_text(result)
            if not failure_text:
                # success
                self._trace(
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    phase="final",
                    attempt=exec_attempt,
                    ok=True,
                )
                return result, args_str

            category = self._classify_error(failure_text)
            self._trace(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                phase="failure",
                attempt=exec_attempt,
                ok=False,
                category=category,
                error=failure_text,
            )

            # INPUT_FORMAT -> ask LLM to repair args and retry, up to llm_fix_attempts
            if (
                category == ToolErrorCategory.INPUT_FORMAT
                and llm_repair_used < self.llm_fix_attempts
            ):
                # 可能出现 LLM 一次返回空/格式不对：这里不提前退出，直到用尽 llm_fix_attempts
                repaired_args_str: Optional[str] = None
                while (
                    llm_repair_used < self.llm_fix_attempts
                    and repaired_args_str is None
                ):
                    llm_repair_used += 1
                    repaired_args_str = await self._repair_arguments_via_llm(
                        llm=llm,
                        messages=messages,
                        available_tools=available_tools,
                        tool_name=tool_name,
                        tool_call_id=tool_call_id,
                        agent_name=agent_name,
                        error=failure_text,
                        original_arguments=args_str,
                        tool_choice_required=tool_choice_required,
                    )

                if repaired_args_str is None:
                    return result, args_str

                args_str = repaired_args_str
                try:
                    args = json.loads(args_str or "{}")
                    if not isinstance(args, dict):
                        raise ValueError("Tool arguments must be a JSON object")
                except Exception as e:
                    return (
                        ToolFailure(
                            error=f"Error parsing repaired arguments for {tool_name}: {e}"
                        ),
                        args_str,
                    )

                exec_attempt += 1
                continue

            # SERVICE -> fixed retry
            if (
                category == ToolErrorCategory.SERVICE
                and service_retry_used < self.service_retry_attempts
            ):
                delay = self._service_delay(service_retry_used)
                self._trace(
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    phase="service_retry_wait",
                    attempt=exec_attempt,
                    ok=True,
                    category=category,
                    detail={"delay": delay},
                )
                logger.warning(
                    f"[ToolExecutor] {agent_name} tool={tool_name} call_id={tool_call_id} "
                    f"SERVICE error, retrying after {delay:.1f}s: {failure_text}"
                )
                await self._sleep(delay)
                service_retry_used += 1
                exec_attempt += 1
                continue

            # RATE_LIMIT/TIMEOUT -> exponential backoff
            if (
                category == ToolErrorCategory.RATE_LIMIT_TIMEOUT
                and rate_retry_used < self.rate_limit_timeout_attempts
            ):
                delay = self._rate_limit_delay(rate_retry_used)
                self._trace(
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    phase="rate_limit_retry_wait",
                    attempt=exec_attempt,
                    ok=True,
                    category=category,
                    detail={"delay": delay},
                )
                logger.warning(
                    f"[ToolExecutor] {agent_name} tool={tool_name} call_id={tool_call_id} "
                    f"RATE_LIMIT/TIMEOUT, backing off {delay:.1f}s: {failure_text}"
                )
                await self._sleep(delay)
                rate_retry_used += 1
                exec_attempt += 1
                continue

            # Exhausted retries: disable tool if rate/timeout
            if category == ToolErrorCategory.RATE_LIMIT_TIMEOUT:
                until = self._now() + self.disable_cooldown_seconds
                self.disabled_until[tool_name] = until
                self._trace(
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    phase="disable",
                    attempt=exec_attempt,
                    ok=True,
                    category=category,
                    detail={
                        "disabled_until": until,
                        "cooldown": self.disable_cooldown_seconds,
                    },
                )
                logger.warning(
                    f"[ToolExecutor] {agent_name} tool={tool_name} disabled for "
                    f"{self.disable_cooldown_seconds:.0f}s until {until} due to: {failure_text}"
                )
            return result, args_str

    # ---------------- internal helpers ----------------
    def _trace(
        self,
        *,
        tool_call_id: str,
        tool_name: str,
        phase: str,
        attempt: int,
        ok: bool,
        category: ToolErrorCategory = ToolErrorCategory.OTHER,
        error: Optional[str] = None,
        detail: Optional[Dict[str, Any]] = None,
    ) -> None:
        entry = ToolTraceEntry(
            ts=self._now(),
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            phase=phase,
            attempt=attempt,
            ok=ok,
            category=category,
            error=error,
            detail=detail or {},
        )
        self.trace.append(entry)
        # 用结构化日志输出关键字段（避免打太长内容）
        if ok:
            logger.debug(
                f"[ToolExecutor.trace] tool={tool_name} call_id={tool_call_id} "
                f"phase={phase} attempt={attempt} ok=1"
            )
        else:
            logger.warning(
                f"[ToolExecutor.trace] tool={tool_name} call_id={tool_call_id} "
                f"phase={phase} attempt={attempt} ok=0 category={category} error={error}"
            )

    def _prune_disabled(self) -> None:
        now = self._now()
        expired = [k for k, v in self.disabled_until.items() if now >= v]
        for k in expired:
            self.disabled_until.pop(k, None)

    def _extract_error_text(self, result: Any) -> Optional[str]:
        if isinstance(result, ToolResult) and getattr(result, "error", None):
            return str(result.error)
        # 某些 tool 可能直接返回 "Error: ..."
        if isinstance(result, str) and result.strip().lower().startswith("error"):
            return result.strip()
        return None

    def _classify_error(self, error_text: str) -> ToolErrorCategory:
        if not error_text:
            return ToolErrorCategory.OTHER
        t = error_text.lower()

        # input format / schema-like (your agreed rules)
        if (
            "invalid json" in t
            or ("parameter" in t and ("is required" in t or "must be" in t))
            or "unrecognized command" in t
            or "tool arguments must be" in t
        ):
            return ToolErrorCategory.INPUT_FORMAT

        # rate limit / timeout
        if (
            "429" in t
            or "rate limit" in t
            or "too many requests" in t
            or "timeout" in t
            or "timed out" in t
            or "deadline exceeded" in t
        ):
            return ToolErrorCategory.RATE_LIMIT_TIMEOUT

        # service errors
        if (
            "500" in t
            or "502" in t
            or "503" in t
            or "504" in t
            or "service unavailable" in t
            or "bad gateway" in t
            or "connection reset" in t
            or "not connected to mcp server" in t
        ):
            return ToolErrorCategory.SERVICE

        return ToolErrorCategory.OTHER

    def _service_delay(self, attempt: int) -> float:
        # attempt starts from 0; retry delay for attempt 0 should be first value
        idx = min(attempt, max(0, len(self.service_retry_delays) - 1))
        return (
            float(self.service_retry_delays[idx]) if self.service_retry_delays else 1.0
        )

    def _rate_limit_delay(self, attempt: int) -> float:
        # exponential: base * factor^attempt
        return float(
            self.rate_limit_base_delay * (self.rate_limit_backoff_factor**attempt)
        )

    async def _repair_arguments_via_llm(
        self,
        *,
        llm,
        messages: List[Message],
        available_tools,
        tool_name: str,
        tool_call_id: str,
        agent_name: str,
        error: str,
        original_arguments: str,
        tool_choice_required: str,
    ) -> Optional[str]:
        """调用 LLM 修复同一 tool 的 arguments（通过 prompt 指定 + 仅给该 tool）。"""
        tool = getattr(available_tools, "get_tool", lambda _n: None)(tool_name)
        if tool is None:
            return None
        single_tool_params = [tool.to_param()]

        sys_prompt = (
            f"你是一个工具调用修复器。你必须且只能调用工具 `{tool_name}`。\n"
            f"请修复该工具调用的 arguments，使其为合法 JSON 且符合工具参数 schema。\n"
            f"不要输出任何解释文字，只输出 tool call。\n"
            f"已知错误信息：{error}\n"
        )
        user_prompt = (
            f"需要修复的工具：{tool_name}\n"
            f"tool_call_id: {tool_call_id}\n"
            f"原始 arguments：{original_arguments}\n"
            f"工具参数 schema（JSON Schema）：{json.dumps(getattr(tool, 'parameters', None), ensure_ascii=False)}\n"
            f"请返回对 `{tool_name}` 的 tool call，arguments 必须是 JSON object。"
        )

        self._trace(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            phase="llm_repair_start",
            attempt=0,
            ok=True,
            category=ToolErrorCategory.INPUT_FORMAT,
            detail={"agent": agent_name},
        )

        try:
            resp = await llm.ask_tool(
                messages=[*messages, Message.user_message(user_prompt)],
                system_msgs=[Message.system_message(sys_prompt)],
                tools=single_tool_params,
                tool_choice=tool_choice_required,
            )
        except Exception as e:
            self._trace(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                phase="llm_repair_error",
                attempt=0,
                ok=False,
                category=ToolErrorCategory.INPUT_FORMAT,
                error=str(e),
            )
            return None

        tool_calls = getattr(resp, "tool_calls", None) if resp else None
        if not tool_calls:
            self._trace(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                phase="llm_repair_no_tool_calls",
                attempt=0,
                ok=False,
                category=ToolErrorCategory.INPUT_FORMAT,
                error="No tool calls returned from LLM repair",
            )
            return None

        call0 = tool_calls[0]
        fn = getattr(call0, "function", None)
        name0 = getattr(fn, "name", None) if fn else None
        args0 = getattr(fn, "arguments", None) if fn else None
        if name0 != tool_name or not args0:
            self._trace(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                phase="llm_repair_mismatch",
                attempt=0,
                ok=False,
                category=ToolErrorCategory.INPUT_FORMAT,
                error=f"LLM repair returned mismatched tool: {name0}",
                detail={"returned_args": args0},
            )
            return None

        self._trace(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            phase="llm_repair_ok",
            attempt=0,
            ok=True,
            category=ToolErrorCategory.INPUT_FORMAT,
        )
        return args0
