from typing import List, Optional

from pydantic import Field

from app.agent.toolcall import ToolCallAgent
from app.prompt.swe import NEXT_STEP_TEMPLATE, SYSTEM_PROMPT
from app.tool import Bash, StrReplaceEditor, Terminate, ToolCollection


class SWEAgent(ToolCallAgent):
    """An agent that implements the SWEAgent paradigm for executing code and natural conversations."""

    name: str = "swe"
    description: str = (
        "an autonomous AI programmer that interacts directly with the computer to solve tasks."
    )

    system_prompt: str = SYSTEM_PROMPT
    next_step_prompt: str = NEXT_STEP_TEMPLATE

    available_tools: ToolCollection = ToolCollection(
        Bash(), StrReplaceEditor(), Terminate()
    )
    special_tool_names: List[str] = Field(default_factory=lambda: [Terminate().name])
    max_steps: int = 30

    bash: Bash = Field(default_factory=Bash)
    working_dir: str = "."

    def _format_prompt(
        self,
        template: str,
        request: Optional[str] = None,
        context: Optional[str] = None,
    ) -> str:
        """根据模板和参数生成最终 prompt，SWE Agent 特殊处理 current_dir

        Args:
            template: Prompt 模板字符串
            request: 用户请求
            context: 上下文信息

        Returns:
            格式化后的 prompt
        """
        # 先调用父类方法获取基础格式化结果
        formatted = super()._format_prompt(template, request, context)

        # SWE Agent 特殊处理：添加 current_dir
        # 如果模板中包含 {current_dir}，需要替换
        if "{current_dir}" in formatted:
            formatted = formatted.replace("{current_dir}", self.working_dir)

        return formatted

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
        # Update working directory
        self.working_dir = await self.bash.execute("pwd")

        return await super().think(request=request, context=context)

    max_steps: int = 20
