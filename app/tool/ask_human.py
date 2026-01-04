import asyncio
from typing import Optional

from app.tool.base import BaseTool


class AskHuman(BaseTool):
    """Add a tool to ask human for help."""

    name: str = "ask_human"
    description: str = (
        "Use this tool when you need more information from the user to proceed with the task. Use this tool when you are stuck, confused, or need clarification about the user's request. This tool allows you to ask user specific questions to get the information you need."
    )
    parameters: str = {
        "type": "object",
        "properties": {
            "inquire": {
                "type": "string",
                "description": "The specific question you want to ask the user to get more information or clarification.",
            }
        },
        "required": ["inquire"],
    }

    def __init__(self):
        super().__init__()
        self._pending_responses = {}
        self._user_response = None
        self._response_event = asyncio.Event()

    async def execute(self, inquire: str) -> str:
        """执行工具，返回特殊标记表示需要用户交互"""
        # 返回特殊标记，表示需要用户交互
        # 前端会检测到这个标记并显示交互提示
        # 后端会通过交互机制来处理用户响应

        # 注意：这里我们不能直接等待，因为工具执行是在 agent 的上下文中
        # 我们需要依赖外部的交互机制来暂停执行
        return f"INTERACTION_REQUIRED: {inquire}"

    async def set_user_response(self, response: str):
        """设置用户响应并通知等待的任务"""
        self._user_response = response
        self._response_event.set()

    async def wait_for_response(self, timeout: Optional[float] = None) -> str:
        """等待用户响应"""
        try:
            await asyncio.wait_for(self._response_event.wait(), timeout=timeout)
            return self._user_response
        except asyncio.TimeoutError:
            return "TIMEOUT: No response received from user"

    def get_user_response(self) -> Optional[str]:
        """获取用户响应"""
        return self._user_response

    def reset(self):
        """重置工具状态"""
        self._user_response = None
        self._response_event.clear()

    def __str__(self):
        """返回工具的字符串表示，便于日志记录"""
        return f"AskHuman tool - waiting for user interaction"
