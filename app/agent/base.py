import asyncio
from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from typing import List, Optional

from pydantic import BaseModel, Field, model_validator

from app.llm import LLM
from app.logger import logger
from app.sandbox.client import SANDBOX_CLIENT
from app.schema import ROLE_TYPE, AgentState, Memory, Message


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

    class Config:
        arbitrary_types_allowed = True
        extra = "allow"  # Allow extra fields for flexibility in subclasses

    @abstractmethod
    def set_prompt(self, render: dict):
        """Set the prompt for the agent"""
        pass

    @model_validator(mode="after")
    def initialize_agent(self) -> "BaseAgent":
        """Initialize agent with default settings if not provided."""
        if self.llm is None or not isinstance(self.llm, LLM):
            self.llm = LLM(config_name=self.name.lower())
        if not isinstance(self.memory, Memory):
            self.memory = Memory()
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
        role: ROLE_TYPE,  # type: ignore
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

        # Create message with appropriate parameters based on role
        kwargs = {"base64_image": base64_image, **(kwargs if role == "tool" else {})}
        self.memory.add_message(message_map[role](content, **kwargs))

    async def run(
        self,
        request: Optional[str] = None,
        stream_callback=None,
        multimodal_paths: Optional[dict] = None,
    ) -> str:
        """
        运行代理的主要执行循环

        Args:
            request: 用户请求
            stream_callback: 流式回调函数，用于实时推送总结的chunk
            multimodal_paths: 多模态参数（可选），如 audio, image, text

        Returns:
            任务执行结果
        """
        # 存储多模态参数到实例（无论是否为None都要设置，避免保留上次的值）
        self.multimodal_paths = multimodal_paths
        # if request:
        #     self.memory.add_message(Message.user_message(request))

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
                should_continue, content = await self.think()
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
                self.memory.add_message(
                    Message.assistant_message(f"Error encountered: {str(e)}")
                )
                break

        # 总结并返回结果（支持流式）
        summary = await self.summarize(request, stream_callback=stream_callback)
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
