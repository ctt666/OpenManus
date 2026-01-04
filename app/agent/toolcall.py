import asyncio
import json
from typing import Any, List, Optional, Union

from pydantic import Field

from app.agent.react import ReActAgent
from app.config import config
from app.exceptions import TokenLimitExceeded
from app.logger import logger
from app.prompt.toolcall import NEXT_STEP_PROMPT, SYSTEM_PROMPT
from app.schema import TOOL_CHOICE_TYPE, AgentState, Message, ToolCall, ToolChoice
from app.tool import CreateChatCompletion, Terminate, ToolCollection

TOOL_CALL_REQUIRED = "Tool calls required but none provided"


class ToolCallAgent(ReActAgent):
    """Base agent class for handling tool/function calls with enhanced abstraction"""

    name: str = "toolcall"
    description: str = "an agent that can execute tool calls."

    system_prompt: str = SYSTEM_PROMPT
    next_step_prompt: str = NEXT_STEP_PROMPT

    available_tools: ToolCollection = ToolCollection(
        CreateChatCompletion(), Terminate()
    )
    tool_choices: TOOL_CHOICE_TYPE = ToolChoice.AUTO.value  # type: ignore
    special_tool_names: List[str] = Field(default_factory=lambda: [Terminate().name])

    tool_calls: List[ToolCall] = Field(default_factory=list)
    _current_base64_image: Optional[str] = None

    max_steps: int = 30
    max_observe: Optional[Union[int, bool]] = None
    multimodal_paths: Optional[dict] = None

    def _get_directory(self) -> str:
        """获取工作目录，可被子类覆盖

        Returns:
            工作目录路径字符串
        """
        return str(config.workspace_root)

    def _format_prompt(
        self, template: str, request: Optional[str] = None, context: Optional[str] = None
    ) -> str:
        """根据模板和参数生成最终 prompt

        Args:
            template: Prompt 模板字符串
            request: 用户请求
            context: 上下文信息

        Returns:
            格式化后的 prompt
        """
        directory = self._get_directory()
        # 安全地格式化，如果模板中没有占位符也不会报错
        try:
            return template.format(
                request=request or "",
                context=context or "",
                directory=directory,
            )
        except KeyError:
            # 如果模板中没有某些占位符，尝试只格式化存在的占位符
            formatted = template
            if "{request}" in template:
                formatted = formatted.replace("{request}", request or "")
            if "{context}" in template:
                formatted = formatted.replace("{context}", context or "")
            if "{directory}" in template:
                formatted = formatted.replace("{directory}", directory)
            return formatted

    async def think(
        self, request: Optional[str] = None, context: Optional[str] = None
    ) -> (bool, str):
        """Process current state and decide next actions using tools

        Args:
            request: 用户请求
            context: 上下文信息

        Returns:
            (should_continue, content): 是否继续执行和思考内容
        """
        # 根据模板生成最终 prompt
        final_next_step_prompt = (
            self._format_prompt(self.next_step_prompt, request, context)
            if self.next_step_prompt
            else None
        )
        final_system_prompt = (
            self._format_prompt(self.system_prompt, request, context)
            if self.system_prompt
            else None
        )

        if final_next_step_prompt:
            user_msg = Message.user_message(
                final_next_step_prompt, multimodal_paths=self.multimodal_paths
            )
            self.messages += [user_msg]

        try:
            # Get response with tool options
            response = await self.llm.ask_tool(
                messages=self.messages,
                system_msgs=(
                    [Message.system_message(final_system_prompt)]
                    if final_system_prompt
                    else None
                ),
                tools=self.available_tools.to_params(),
                tool_choice=self.tool_choices,
            )
            # print(f"tool call agent system messages===============: {self.system_prompt}\n, user massages================: {self.format_messages()}")
            # print(f"think-ask tool response: {response}")
        except asyncio.CancelledError:
            logger.info("Think operation was cancelled")
            raise
        except ValueError:
            raise
        except Exception as e:
            # Check if this is a RetryError containing TokenLimitExceeded
            if hasattr(e, "__cause__") and isinstance(e.__cause__, TokenLimitExceeded):
                token_limit_error = e.__cause__
                logger.error(
                    f"🚨 Token limit error (from RetryError): {token_limit_error}"
                )
                self.memory.add_message(
                    Message.assistant_message(
                        f"Maximum token limit reached, cannot continue execution: {str(token_limit_error)}"
                    )
                )
                self.state = AgentState.FINISHED
                return False, ""
            raise

        self.tool_calls = tool_calls = (
            response.tool_calls if response and response.tool_calls else []
        )
        content = response.content
        if response is not None and hasattr(response, "reasoning_content"):
            logger.info(f"✨ {self.name}'s thoughts: {response.reasoning_content}")

        if content:
            logger.info(f"Act content: {content}")
        # Log response info
        logger.info(
            f"🛠️ {self.name} selected {len(tool_calls) if tool_calls else 0} tools to use"
        )
        if tool_calls:
            logger.info(
                f"🧰 Tools being prepared: {[call.function.name for call in tool_calls]}"
            )

        try:
            if response is None:
                raise RuntimeError("No response received from the LLM")

            # Handle different tool_choices modes
            if self.tool_choices == ToolChoice.NONE:
                if tool_calls:
                    logger.warning(
                        f"🤔 Hmm, {self.name} tried to use tools when they weren't available!"
                    )
                if content:
                    self.memory.add_message(Message.assistant_message(content))
                    return True, content
                return False, ""

            # Create and add assistant message
            assistant_msg = (
                Message.from_tool_calls(content=content, tool_calls=self.tool_calls)
                if self.tool_calls
                else Message.assistant_message(content)
            )
            self.memory.add_message(assistant_msg)

            if self.tool_choices == ToolChoice.REQUIRED and not self.tool_calls:
                return True, content  # Will be handled in act()

            # For 'auto' mode, continue with content if no commands but content exists
            if self.tool_choices == ToolChoice.AUTO and not self.tool_calls:
                return bool(content), content

            return bool(self.tool_calls), content
        except Exception as e:
            logger.error(f"🚨 Oops! The {self.name}'s thinking process hit a snag: {e}")
            self.memory.add_message(
                Message.assistant_message(
                    f"Error encountered while processing: {str(e)}"
                )
            )
            return False, ""

    async def act(self) -> str:
        """Execute tool calls and handle their results"""
        if not self.tool_calls:
            if self.tool_choices == ToolChoice.REQUIRED:
                raise ValueError(TOOL_CALL_REQUIRED)

            # Return last message content if no tool calls
            return self.messages[-1].content or "No content or commands to execute"

        results = []
        for command in self.tool_calls:
            # Reset base64_image for each tool call
            self._current_base64_image = None

            try:
                result = await self.execute_tool(command)
            except asyncio.CancelledError:
                logger.info("Tool execution was cancelled")
                raise

            if self.max_observe:
                # todo: 需要优化，压缩，网页统一格式
                result = result[: self.max_observe]

            logger.info(
                f"🎯 Tool '{command.function.name}' completed its mission! Result: {result}"
            )
            # Add tool response to memory
            tool_msg = Message.tool_message(
                content=result,
                name=command.function.name,
                tool_call_id=command.id,
                arguments=command.function.arguments,
            )
            self.memory.add_message(tool_msg)
            results.append(result)

        return "\n\n".join(results)

    async def execute_tool(self, command: ToolCall) -> str:
        """Execute a single tool call with robust error handling"""
        if not command or not command.function or not command.function.name:
            return "Error: Invalid command format"

        name = command.function.name
        if name not in self.available_tools.tool_map:
            return f"Error: Unknown tool '{name}'"

        try:
            # Parse arguments
            args = json.loads(command.function.arguments or "{}")

            # Execute the tool
            logger.info(f"🔧 Activating tool: '{name}', args: {args}")
            result = await self.available_tools.execute(name=name, tool_input=args)

            # 特殊处理 ask_human 工具 - 设置标志让 agent 暂停执行
            if (
                name.lower() == "ask_human"
                and isinstance(result, str)
                and "INTERACTION_REQUIRED:" in result
            ):
                # 当 ask_human 工具返回 INTERACTION_REQUIRED 时，设置标志
                # 让 BaseAgent 知道需要暂停执行
                logger.info(f"🔄 AskHuman tool executed, setting interaction flag...")

                # 设置一个标志，让 BaseAgent 知道需要暂停
                self._interaction_required = True
                self._interaction_message = result

                # 返回特殊结果，让外部逻辑知道需要用户交互
                return result

            # Handle special tools
            await self._handle_special_tool(name=name, result=result)

            # Check if result is a ToolResult with base64_image
            if hasattr(result, "base64_image") and result.base64_image:
                # Store the base64_image for later use in tool_message
                self._current_base64_image = result.base64_image

            # Format result for display (standard case)
            observation = (
                f"Observed output of cmd `{name}` executed:\n{str(result)}"
                if result
                else f"Cmd `{name}` completed with no output"
            )

            return observation
        except json.JSONDecodeError:
            error_msg = f"Error parsing arguments for {name}: Invalid JSON format"
            logger.error(
                f"📝 Oops! The arguments for '{name}' don't make sense - invalid JSON, arguments:{command.function.arguments}"
            )
            return f"Error: {error_msg}"
        except Exception as e:
            error_msg = f"⚠️ Tool '{name}' encountered a problem: {str(e)}"
            logger.exception(error_msg)
            return f"Error: {error_msg}"

    async def _handle_special_tool(self, name: str, result: Any, **kwargs):
        """Handle special tool execution and state changes"""
        if not self._is_special_tool(name):
            return

        # 特殊处理 ask_human 工具
        if name.lower() == "ask_human":
            # 当 ask_human 工具被执行时，暂停执行等待用户响应
            # 这里我们需要通过某种机制来暂停执行
            # 由于我们无法直接在这里暂停，我们需要依赖外部的交互机制
            logger.info(f"🔄 AskHuman tool executed, waiting for user response...")
            # 不设置 FINISHED 状态，让执行继续
            return

        if self._should_finish_execution(name=name, result=result, **kwargs):
            # Set agent state to finished
            self.state = AgentState.FINISHED

    @staticmethod
    def _should_finish_execution(**kwargs) -> bool:
        """Determine if tool execution should finish the agent"""
        return True

    def _is_special_tool(self, name: str) -> bool:
        """Check if tool name is in special tools list"""
        return name.lower() in [n.lower() for n in self.special_tool_names]

    async def cleanup(self):
        """Clean up resources used by the agent's tools."""
        logger.info(f"🧹 Cleaning up resources for agent '{self.name}'...")
        for tool_name, tool_instance in self.available_tools.tool_map.items():
            if hasattr(tool_instance, "cleanup") and asyncio.iscoroutinefunction(
                tool_instance.cleanup
            ):
                try:
                    logger.debug(f"🧼 Cleaning up tool: {tool_name}")
                    await tool_instance.cleanup()
                except Exception as e:
                    logger.error(
                        f"🚨 Error cleaning up tool '{tool_name}': {e}", exc_info=True
                    )
        self.state = AgentState.IDLE
        logger.info(f"✨ Cleanup complete for agent '{self.name}'.")

    async def run(
        self,
        request: Optional[str] = None,
        stream_callback=None,
        multimodal_paths: Optional[dict] = None,
        context: Optional[str] = None,
    ) -> str:
        """Run the agent with cleanup when done."""
        return await super().run(
            request, stream_callback, multimodal_paths, context
        )
