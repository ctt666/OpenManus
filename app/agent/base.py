import asyncio
from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from typing import Callable, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agent.guardrail import (
    GuardrailAction,
    GuardrailAgent,
    GuardrailBoundary,
    GuardrailEngine,
)
from app.config import config
from app.exceptions import TokenLimitExceeded
from app.llm import LLM
from app.logger import logger
from app.schema import AgentState, Memory, Message


class BaseAgent(BaseModel, ABC):
    """Abstract base class for managing agent state and execution.

    Provides foundational functionality for state transitions, memory management,
    and a step-based execution loop. Subclasses must implement the `step` method.
    """

    # Core attributes
    name: str = Field(..., description="Unique name of the agent")
    description: Optional[str] = Field(None, description="Optional agent description")

    # Prompts
    system_prompt: Optional[str] = Field(
        None, description="System-level instruction prompt"
    )
    next_step_prompt: Optional[str] = Field(
        None, description="Prompt for determining next action"
    )

    # Dependencies
    llm: LLM = Field(default_factory=LLM, description="Language model instance")
    memory: Memory = Field(default_factory=Memory, description="Agent's memory store")
    state: AgentState = Field(
        default=AgentState.IDLE, description="Current agent state"
    )

    # Execution control
    max_steps: int = Field(default=10, description="Maximum steps before termination")
    current_step: int = Field(default=0, description="Current step in execution")

    # 多模态参数（可选）
    multimodal_paths: Optional[dict] = Field(
        default=None, description="多模态参数：modalities, audio, stream_options"
    )

    duplicate_threshold: int = 2
    enable_guardrail: bool = False
    input_guardrails: List[str | Callable] = Field(default_factory=list)
    output_guardrails: List[str | Callable] = Field(default_factory=list)
    guardrail_retry: int = 3
    guardrail_agent: GuardrailAgent = Field(default_factory=GuardrailAgent)

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="allow",  # Allow extra fields for flexibility in subclasses
    )

    @model_validator(mode="after")
    def initialize_agent(self) -> "BaseAgent":
        """Initialize agent with default settings if not provided."""
        if self.llm is None or not isinstance(self.llm, LLM):
            self.llm = LLM(config_name=self.name.lower())
        if not isinstance(self.memory, Memory):
            self.memory = Memory()
        # Ensure guardrail repair uses the same LLM config as this agent
        try:
            if getattr(self, "guardrail_agent", None) is not None:
                self.guardrail_agent.llm = self.llm
        except Exception:
            # Avoid failing initialization due to guardrail wiring issues
            pass
        return self

    @asynccontextmanager
    async def state_context(self, new_state: AgentState):
        """Context manager for safe agent state transitions.

        Args:
            new_state: The state to transition to during the context.

        Yields:
            None: Allows execution within the new state.

        Raises:
            ValueError: If the new_state is invalid.
        """
        if not isinstance(new_state, AgentState):
            raise ValueError(f"Invalid state: {new_state}")

        previous_state = self.state
        self.state = new_state
        try:
            yield
        except Exception as e:
            self.state = AgentState.ERROR  # Transition to ERROR on failure
            raise e
        finally:
            self.state = previous_state  # Revert to previous state

    def update_memory(
        self,
        role: Literal["user", "system", "assistant", "tool"],
        content: str,
        base64_image: Optional[str] = None,
        **kwargs,
    ) -> None:
        """Add a message to the agent's memory.

        Args:
            role: The role of the message sender (user, system, assistant, tool).
            content: The message content.
            base64_image: Optional base64 encoded image.
            **kwargs: Additional arguments (e.g., tool_call_id for tool messages).

        Raises:
            ValueError: If the role is unsupported.
        """
        message_map = {
            "user": Message.user_message,
            "system": Message.system_message,
            "assistant": Message.assistant_message,
            "tool": lambda content, **kw: Message.tool_message(content, **kw),
        }

        if role not in message_map:
            raise ValueError(f"Unsupported message role: {role}")
        msg_factory = message_map[role]
        msg = msg_factory(content, **kwargs) if role == "tool" else msg_factory(content)
        # Create message with appropriate parameters based on role
        kwargs = {"base64_image": base64_image, **(kwargs if role == "tool" else {})}
        self.memory.add_message(msg)

    async def run(
        self,
        request: Optional[str] = None,
        stream_callback=None,
        multimodal_paths: Optional[dict] = None,
        context: Optional[str] = None,
    ) -> str:
        """
        运行代理的主要执行循环

        Args:
            request: 用户请求
            stream_callback: 流式回调函数，用于实时推送总结的chunk
            multimodal_paths: 多模态参数（可选），如 audio, image, text
            context: 上下文信息，用于生成 next_step_prompt

        Returns:
            任务执行结果
        """
        # 存储多模态参数到实例（无论是否为None都要设置，避免保留上次的值）
        self.multimodal_paths = multimodal_paths
        if request:
            self.memory.add_message(Message.user_message(request))

        # ---------------- Guardrails: user-level input ----------------
        guard_settings = getattr(config, "guardrail", None)
        agent_in_enabled = bool(self.enable_guardrail) or bool(
            guard_settings
            and getattr(guard_settings, "enabled", False)
            and getattr(getattr(guard_settings, "agent_input", None), "enabled", False)
        )
        agent_out_enabled = bool(self.enable_guardrail) or bool(
            guard_settings
            and getattr(guard_settings, "enabled", False)
            and getattr(getattr(guard_settings, "agent_output", None), "enabled", False)
        )

        agent_input_specs = []
        if (
            guard_settings
            and getattr(guard_settings, "enabled", False)
            and getattr(getattr(guard_settings, "agent_input", None), "enabled", False)
        ):
            agent_input_specs.extend(getattr(guard_settings.agent_input, "guards", []))
        agent_input_specs.extend(self.input_guardrails)

        agent_output_specs = []
        if (
            guard_settings
            and getattr(guard_settings, "enabled", False)
            and getattr(getattr(guard_settings, "agent_output", None), "enabled", False)
        ):
            agent_output_specs.extend(
                getattr(guard_settings.agent_output, "guards", [])
            )
        agent_output_specs.extend(self.output_guardrails)

        max_retries = int(self.guardrail_retry or 0) if self.enable_guardrail else 0
        if guard_settings and getattr(guard_settings, "enabled", False):
            max_retries = max(
                max_retries,
                int(
                    getattr(getattr(guard_settings, "retry", None), "max_attempts", 0)
                    or 0
                ),
            )

        if agent_in_enabled:
            if request:
                in_res = await GuardrailEngine.enforce(
                    boundary=GuardrailBoundary.AGENT_INPUT,
                    text=request,
                    guardrails=agent_input_specs,
                    enabled=agent_in_enabled,
                    policy=[GuardrailAction.REDACT, GuardrailAction.BLOCK],
                    max_retries=0,
                    guardrail_agent=self.guardrail_agent,
                    agent_name=self.name,
                )
                if in_res.blocked:
                    return in_res.text
                request = in_res.text

            if context:
                ctx_res = await GuardrailEngine.enforce(
                    boundary=GuardrailBoundary.AGENT_INPUT,
                    text=context,
                    guardrails=agent_input_specs,
                    enabled=agent_in_enabled,
                    policy=[GuardrailAction.REDACT, GuardrailAction.BLOCK],
                    max_retries=0,
                    guardrail_agent=self.guardrail_agent,
                    agent_name=self.name,
                )
                if ctx_res.blocked:
                    return ctx_res.text
                context = ctx_res.text

        step = 0
        consecutive_duplicates = 0
        last_response = None

        while step < self.max_steps and self.state != AgentState.FINISHED:
            try:
                logger.info(f"Step {step} of {self.max_steps}")

                # 检查是否被取消
                try:
                    # 检查当前任务是否被取消
                    asyncio.current_task().get_name()
                except asyncio.CancelledError:
                    logger.info("Agent execution was cancelled")
                    raise

                # 思考阶段
                should_continue, content = await self.think(
                    request=request, context=context
                )
                if not should_continue:
                    break

                # 行动阶段
                if self.tool_calls:
                    result = await self.act()

                    # 检查行动结果是否包含交互需求
                    if result and "INTERACTION_REQUIRED:" in result:
                        logger.info(
                            "🔄 Interaction required in act result, pausing execution..."
                        )
                        self.state = AgentState.IDLE
                        return result

                # 检查是否被取消（在行动后）
                try:
                    asyncio.current_task().get_name()
                except asyncio.CancelledError:
                    logger.info("Agent execution was cancelled after action")
                    raise

                # 检查重复响应
                if content and content == last_response:
                    logger.info(f"Duplicate response: {content}")
                    consecutive_duplicates += 1
                    if consecutive_duplicates >= self.duplicate_threshold:
                        break
                else:
                    consecutive_duplicates = 0

                last_response = content
                step += 1

            except Exception as e:
                logger.error(f"🚨 Error in step {step}: {e}")
                # 对于 TokenLimitExceeded：直接抛出，让 server/flow 标记任务失败并提示用户重新开启会话
                if isinstance(e, TokenLimitExceeded):
                    raise
                self.memory.add_message(
                    Message.assistant_message(f"Error encountered: {str(e)}")
                )
                break

        # 总结并返回结果（支持流式）
        summary = await self.summarize(request, stream_callback=stream_callback)

        # ---------------- Guardrails: user-level output ----------------
        if agent_out_enabled and summary:
            out_res = await GuardrailEngine.enforce(
                boundary=GuardrailBoundary.AGENT_OUTPUT,
                text=summary,
                guardrails=agent_output_specs,
                enabled=agent_out_enabled,
                policy=[
                    GuardrailAction.REDACT,
                    GuardrailAction.RETRY,
                    GuardrailAction.BLOCK,
                ],
                max_retries=max(0, max_retries),
                guardrail_agent=self.guardrail_agent,
                agent_name=self.name,
            )
            return out_res.text

        return summary

    @abstractmethod
    async def step(self) -> str:
        """Execute a single step in the agent's workflow.

        Must be implemented by subclasses to define specific behavior.
        """

    @abstractmethod
    async def summarize(self, request: str) -> str:
        """Summarize the agent's work"""

    def handle_stuck_state(self):
        """Handle stuck state by adding a prompt to change strategy"""
        stuck_prompt = "\
        Observed duplicate responses. Consider new strategies and avoid repeating ineffective paths already attempted."
        self.next_step_prompt = f"{stuck_prompt}\n{self.next_step_prompt}"
        logger.warning(f"Agent detected stuck state. Added prompt: {stuck_prompt}")

    def is_stuck(self) -> bool:
        """Check if the agent is stuck in a loop by detecting duplicate content"""
        if len(self.memory.messages) < 2:
            return False

        last_message = self.memory.messages[-1]
        if not last_message.content:
            return False

        # Count identical content occurrences
        duplicate_count = sum(
            1
            for msg in reversed(self.memory.messages[:-1])
            if msg.role == "assistant" and msg.content == last_message.content
        )

        return duplicate_count >= self.duplicate_threshold

    @property
    def messages(self) -> List[Message]:
        """Retrieve a list of messages from the agent's memory."""
        return self.memory.messages

    @messages.setter
    def messages(self, value: List[Message]):
        """Set the list of messages in the agent's memory."""
        self.memory.messages = value

    def format_messages(self) -> str:
        format_str = ""
        for message in self.messages:
            format_str += f"{message.content},\n"
        return format_str
