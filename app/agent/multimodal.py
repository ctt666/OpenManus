"""
Multimodal Agent
支持图像、音频、文本多模态输入的理解Agent
"""

from typing import Optional

from pydantic import Field

from app.agent.manus import Manus
from app.llm import LLM
from app.logger import logger
from app.prompt import multimodal
from app.tool.ask_human import AskHuman
from app.tool.str_replace_editor import StrReplaceEditor
from app.tool.terminate import Terminate
from app.tool.tool_collection import ToolCollection


class MultimodalAgent(Manus):
    """
    多模态理解Agent

    专门用于处理包含图像、音频、文本的多模态输入任务
    使用qwen3-omni-flash等多模态LLM进行理解和推理

    Attributes:
        name: Agent名称
        description: Agent描述
        system_prompt: 系统提示词
        next_step_prompt: 下一步行动提示词模板
    """

    name: str = "MultimodalAgent"
    description: str = (
        "Specialized agent for understanding and processing multimodal inputs "
        "(images, audio, text). Capable of cross-modal reasoning and analysis."
    )

    system_prompt: str = multimodal.SYSTEM_PROMPT
    next_step_prompt: str = multimodal.NEXT_STEP_PROMPT

    # 最大步数（多模态任务可能需要更多步骤）
    max_steps: int = 40

    def __init__(self, **data):
        """初始化MultimodalAgent"""
        # 使用multimodal配置的LLM（qwen3-omni-flash）
        if "llm" not in data:
            try:
                data["llm"] = LLM(config_name="multimodal")
                logger.info(
                    "✨ MultimodalAgent: Using 'multimodal' LLM config (qwen3-omni-flash)"
                )
            except Exception as e:
                logger.warning(f"⚠️ Failed to load 'multimodal' LLM config: {e}")
                logger.info("📌 Falling back to default LLM config")
                data["llm"] = LLM()

        # 初始化工具集（多模态任务常用工具）
        if "available_tools" not in data:
            tools = ToolCollection()
            # tools.add_tool(PythonExecute())  # 用于数据处理、文件操作
            tools.add_tools(StrReplaceEditor())
            tools.add_tool(AskHuman())  # 用于澄清需求
            tools.add_tool(Terminate())  # 用于结束任务
            data["available_tools"] = tools
            logger.info(f"🛠️ MultimodalAgent: Loaded {len(tools.tool_map)} tools")

        super().__init__(**data)

    async def summarize(
        self, request: Optional[str] = None, stream_callback=None
    ) -> str:
        """
        总结任务执行结果

        对于多模态任务，总结时会特别关注从各种模态中提取的信息

        Args:
            request: 原始任务请求
            stream_callback: 流式回调函数，用于实时推送总结的chunk

        Returns:
            总结文本
        """
        logger.info("📊 MultimodalAgent: Generating summary...")

        # 使用父类的总结逻辑，但可以添加多模态特定的处理
        summary = await super().summarize(request, stream_callback=stream_callback)

        return summary
