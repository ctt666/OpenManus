from abc import ABC, abstractmethod
from typing import Optional

from pydantic import Field

from app.agent.base import BaseAgent
from app.config import config
from app.llm import LLM
from app.logger import logger
from app.prompt.react import SUMMARIZE_PROMPT
from app.schema import AgentState, Memory, Message


class ReActAgent(BaseAgent, ABC):
    name: str
    description: Optional[str] = None

    system_prompt: Optional[str] = None
    next_step_prompt: Optional[str] = None
    summarize_prompt: str = SUMMARIZE_PROMPT

    llm: Optional[LLM] = Field(default_factory=LLM)
    memory: Memory = Field(default_factory=Memory)
    state: AgentState = AgentState.IDLE

    max_steps: int = 10
    current_step: int = 0

    @abstractmethod
    async def think(
        self, request: Optional[str] = None, context: Optional[str] = None
    ) -> (bool, str):
        """Process current state and decide next action

        Args:
            request: 用户请求
            context: 上下文信息

        Returns:
            (should_continue, content): 是否继续执行和思考内容
        """

    @abstractmethod
    async def act(self) -> str:
        """Execute decided actions"""

    async def step(
        self, request: Optional[str] = None, context: Optional[str] = None
    ) -> str:
        """Execute a single step: think and act.

        Args:
            request: 用户请求
            context: 上下文信息

        Returns:
            执行结果
        """
        should_act, thought = await self.think(request=request, context=context)
        if not should_act:
            return thought
        act_result = await self.act()
        return f"{thought}\n{act_result}"

    async def summarize(self, request: str, stream_callback=None) -> str:
        """
        总结任务执行结果

        Args:
            request: 原始任务请求
            stream_callback: 流式回调函数，接收每个chunk

        Returns:
            完整的总结消息
        """
        summarize_prompt = self.summarize_prompt.format(
            request=request, directory=config.workspace_root
        )
        user_msg = Message.user_message(summarize_prompt)
        self.messages += [user_msg]

        try:
            # 如果提供了stream_callback，使用流式输出
            if stream_callback:
                logger.info(f"📤 [DEBUG] 使用流式模式进行summarize")
                full_summary = []
                chunk_count = 0
                async for chunk in self.llm.stream_ask(messages=self.messages):
                    chunk_count += 1
                    full_summary.append(chunk)
                    logger.info(
                        f"📤 [DEBUG] 收到第{chunk_count}个chunk: {chunk[:50]}..."
                    )
                    await stream_callback(chunk)  # 实时推送到前端
                    logger.info(f"📤 [DEBUG] chunk已发送到callback")

                final_content = "".join(full_summary)
                logger.info(
                    f"📤 [DEBUG] 流式输出完成，共{chunk_count}个chunk，总长度{len(final_content)}字符"
                )
                response = Message.assistant_message(final_content)
            else:
                logger.info(f"📤 [DEBUG] 使用批量模式进行summarize")
                # 回退到批量模式
                response = await self.llm.ask(messages=self.messages)

            logger.info(f"🎯 Summarization completed! Result: {response}")
            return response
        except Exception as e:
            logger.error(f"🚨 Oops! The {self.name}'s thinking process hit a snag: {e}")
            self.memory.add_message(
                Message.assistant_message(
                    f"Error encountered while processing: {str(e)}"
                )
            )
            return Message.assistant_message(
                "summary encountered an error, please try again"
            )
