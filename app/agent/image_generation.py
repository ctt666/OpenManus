"""图像生成Agent - 不使用Tool，直接调用llm_dashscope"""

import asyncio
from typing import Optional

from pydantic import Field

from app.agent.toolcall import ToolCallAgent
from app.config import config
from app.llm import LLM
from app.llm_dashscope import DashScopeGenerator
from app.logger import logger
from app.prompt.imagegen import NEXT_STEP_PROMPT, SYSTEM_PROMPT
from app.schema import AgentState, Message


class ImageGenerationAgent(ToolCallAgent):
    """
    图像生成专用Agent

    使用qwen系列LLM理解需求，调用DashScope MultiModalConversation API生成图像
    """

    name: str = "Image_Generation"
    description: str = "专门处理图像生成任务的Agent，能够根据文本描述生成高质量图像"
    support_multimodal_input: bool = False

    max_steps: int = 5

    # 图像生成器
    image_generator: Optional[DashScopeGenerator] = Field(default=None)

    def __init__(self, **data):
        # 使用image_gen配置的LLM（如果有）
        if "llm" not in data:
            try:
                data["llm"] = LLM(config_name="image_gen")
                logger.info("ImageGenerationAgent: Using 'image_gen' LLM config")
            except Exception as e:
                logger.warning(
                    f"ImageGenerationAgent: Failed to load 'image_gen' config, using default: {e}"
                )
                data["llm"] = LLM()  # fallback

        super().__init__(**data)

        # 初始化图像生成器
        api_key = None
        if "image_gen" in config.llm:
            api_key = config.llm["image_gen"].api_key

        self.image_generator = DashScopeGenerator(api_key=api_key)

    def set_prompt(self, render: dict):
        """Set the prompt for the agent"""
        self.next_step_prompt = NEXT_STEP_PROMPT.format(**render)
        self.system_prompt = SYSTEM_PROMPT.format(**render)

    async def run(self, request: Optional[str] = None) -> str:
        """
        重写run方法，直接执行图像生成

        Args:
            request: 用户的图像生成需求

        Returns:
            生成结果描述
        """
        try:
            if not request:
                return "请提供图像描述需求"

            logger.info(f"ImageGenerationAgent received request: {request}")

            # 使用LLM优化prompt（可选，提升生成质量）
            optimized_prompt = await self._optimize_prompt(request)
            logger.info(f"Optimized prompt: {optimized_prompt}")

            # 在executor中调用同步的图像生成方法
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: self.image_generator.generate_image(
                    prompt=optimized_prompt,
                    size="1472*1140",
                    watermark=False,
                    prompt_extend=True,
                ),
            )

            if result["success"]:
                images = result["images"]
                urls = result["urls"]

                response = f"✅ 图像生成成功！\n\n"
                response += f"**生成的图像**：\n"
                for idx, (path, url) in enumerate(zip(images, urls), 1):
                    response += f"{idx}. 本地路径: `{path}`\n"
                    response += f"   在线链接: {url}\n\n"

                response += f"**请求ID**: {result['request_id']}\n"
                response += f"**提示词**: {optimized_prompt}"

                self.state = AgentState.FINISHED
                return response
            else:
                error_msg = f"❌ 图像生成失败: {result['message']}"
                logger.error(error_msg)
                self.state = AgentState.ERROR
                return error_msg

        except Exception as e:
            error_msg = f"❌ 图像生成异常: {str(e)}"
            logger.exception(error_msg)
            self.state = AgentState.ERROR
            return error_msg

    async def _optimize_prompt(self, user_request: str) -> str:
        """使用LLM优化图像提示词"""
        try:
            optimization_prompt = f"""请将以下用户需求转换为详细的图像生成提示词。

用户需求：{user_request}

要求：
1. 提取关键视觉元素（主体、背景、环境）
2. 补充合理的细节（光线、色彩、构图、风格、氛围等）
3. 使用清晰、具体、富有画面感的描述
4. 保持简洁，200字以内
5. 只输出优化后的提示词，不要任何其他解释

优化后的提示词："""

            response = await self.llm.ask(
                messages=[Message.user_message(optimization_prompt)],
                system_msgs=[
                    Message.system_message(
                        "你是专业的图像提示词优化专家，擅长将简单描述转换为详细的视觉化描述。"
                    )
                ],
            )

            if isinstance(response, str):
                return response.strip()
            elif hasattr(response, "content"):
                return response.content.strip()
            else:
                logger.warning("LLM response format unexpected, using original request")
                return user_request

        except Exception as e:
            logger.warning(f"Prompt optimization failed: {e}, using original request")
            return user_request
