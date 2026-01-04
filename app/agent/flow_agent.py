from typing import List

from pydantic import Field

from app.agent.manus import Manus
from app.llm import LLM
from app.logger import logger
from app.prompt.flow_step_agent import NEXT_STEP_PROMPT, SYSTEM_PROMPT
from app.tool import (
    AskHuman,
    Bash,
    PythonExecute,
    StrReplaceEditor,
    Terminate,
    ToolCollection,
)

# from app.tool import (
#     AskHuman,
#     Bash,
#     PythonExecute,
#     StrReplaceEditor,
#     Terminate,
#     ToolCollection,
# )


class FlowAgent(Manus):
    """An agent that extends from the ManusAgent paradigm for every flow step."""

    name: str = "flow"
    description: str = (
        "处理文本任务的Agent，能够使用多种工具来完成任务，能够接收多模态输入。"
    )

    system_prompt: str = SYSTEM_PROMPT
    support_multimodal_input: bool = True

    available_tools: ToolCollection = Field(
        default_factory=lambda: ToolCollection(
            PythonExecute(),
            StrReplaceEditor(),
            AskHuman(),
            Terminate(),
            Bash(),
        )
    )
    max_steps: int = 30

    def __init__(self, **data):
        """初始化FlowAgent"""
        # 使用multimodal配置的LLM（qwen3-omni-flash）
        if "llm" not in data:
            try:
                data["llm"] = LLM(config_name="multimodal")
                logger.info(
                    "✨ FlowAgent: Using 'multimodal' LLM config (qwen3-omni-flash)"
                )
            except Exception as e:
                logger.warning(f"⚠️ Failed to load 'multimodal' LLM config: {e}")
                logger.info("📌 Falling back to default LLM config")
                data["llm"] = LLM()

        super().__init__(**data)
