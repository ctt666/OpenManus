"""音频生成Agent - 不使用Tool，直接调用llm_dashscope"""

import asyncio
from typing import Optional, Tuple

from pydantic import Field

from app.agent.toolcall import ToolCallAgent
from app.config import config
from app.llm import LLM
from app.llm_dashscope import DashScopeGenerator
from app.logger import logger
from app.prompt.audiogen import NEXT_STEP_PROMPT, SYSTEM_PROMPT
from app.schema import AgentState, Message


class AudioGenerationAgent(ToolCallAgent):
    """
    音频生成专用Agent

    使用qwen系列LLM理解需求，调用DashScope MultiModalConversation API合成语音
    """

    name: str = "Audio_Generation"
    description: str = (
        "专门处理语音合成任务的Agent，能够将文本转换为自然流畅的语音。语音合成需要提供文本内容作为输入。"
    )

    max_steps: int = 5
    support_multimodal_input: bool = False

    # 语音合成器
    audio_generator: Optional[DashScopeGenerator] = Field(default=None)

    def __init__(self, **data):
        # 使用audio_gen配置的LLM（如果有）
        if "llm" not in data:
            try:
                data["llm"] = LLM(config_name="audio_gen")
                logger.info("AudioGenerationAgent: Using 'audio_gen' LLM config")
            except Exception as e:
                logger.warning(
                    f"AudioGenerationAgent: Failed to load 'audio_gen' config, using default: {e}"
                )
                data["llm"] = LLM()  # fallback

        super().__init__(**data)

        # 初始化语音合成器
        api_key = None
        if "audio_gen" in config.llm:
            api_key = config.llm["audio_gen"].api_key

        self.audio_generator = DashScopeGenerator(api_key=api_key)

    async def run(self, request: Optional[str] = None) -> str:
        """
        重写run方法，直接执行语音合成

        Args:
            request: 用户的语音合成需求

        Returns:
            生成结果描述
        """
        try:
            if not request:
                return "请提供要合成的文本内容"

            logger.info(f"AudioGenerationAgent received request: {request}")

            # 解析参数（使用LLM提取文本和参数）
            text, voice, language_type = await self._parse_request(request)

            logger.info(
                f"Parsed params - text: {text[:50]}..., voice: {voice}, language: {language_type}"
            )

            # 在executor中调用同步的音频生成方法
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: self.audio_generator.generate_audio(
                    text=text, voice=voice, language_type=language_type, stream=False
                ),
            )

            if result["success"]:
                audio_path = result["audio_path"]
                audio_url = result["audio_url"]
                duration = result.get("duration")

                response = f"✅ 语音合成成功！\n\n"
                response += f"**音频文件**：`{audio_path}`\n"
                if audio_url:
                    response += f"**在线链接**：{audio_url}\n"
                if duration:
                    response += f"**时长**：{duration:.2f} 秒\n"
                response += f"**音色**：{voice}\n"
                response += f"**语言**：{language_type}\n"
                response += f"\n文本内容（前100字）：{text[:100]}..."

                self.state = AgentState.FINISHED
                return response
            else:
                error_msg = f"❌ 语音合成失败: {result['message']}"
                logger.error(error_msg)
                self.state = AgentState.ERROR
                return error_msg

        except Exception as e:
            error_msg = f"❌ 语音合成异常: {str(e)}"
            logger.exception(error_msg)
            self.state = AgentState.ERROR
            return error_msg

    async def _parse_request(self, request: str) -> Tuple[str, str, str]:
        """解析用户请求，提取文本、音色、语言类型"""
        try:
            parse_prompt = f"""从以下用户需求中提取语音合成的参数。

用户需求：
{request}

请提取：
1. 要合成的文本内容（必需）
2. 音色：Cherry（女声）/ Baixue（知性女声）/ Roy（男声）/ Peter（青年男声），如果没有指定则默认 Cherry
3. 语言类型：Chinese / English / Japanese，根据文本内容判断，默认 Chinese

以JSON格式输出（只输出JSON，不要其他内容）：
{{
    "text": "要合成的文本内容",
    "voice": "Cherry",
    "language_type": "Chinese"
}}"""

            response = await self.llm.ask(
                messages=[Message.user_message(parse_prompt)],
                system_msgs=[
                    Message.system_message(
                        "你是参数提取专家，严格按要求输出JSON格式，不添加任何其他文字。"
                    )
                ],
            )

            import json
            import re

            # 提取JSON内容
            if isinstance(response, str):
                content = response
            elif hasattr(response, "content"):
                content = response.content
            else:
                content = str(response)

            # 尝试提取JSON（处理可能包含代码块的情况）
            json_match = re.search(r"\{[\s\S]*\}", content)
            if json_match:
                json_str = json_match.group()
                parsed = json.loads(json_str)
            else:
                parsed = {}

            text = parsed.get("text", request)
            voice = parsed.get("voice", "Cherry")
            language_type = parsed.get("language_type", "Chinese")

            # 参数验证
            valid_voices = [
                "Cherry",
                "Baixue",
                "Zhichu",
                "Zhixiaobai",
                "Zhixiaoyao",
                "Zhiyan",
            ]
            if voice not in valid_voices:
                logger.warning(f"Invalid voice '{voice}', using default 'Cherry'")
                voice = "Cherry"

            valid_languages = [
                "Chinese",
                "English",
                "Japanese",
                "Korean",
                "Spanish",
                "French",
                "German",
            ]
            if language_type not in valid_languages:
                logger.warning(
                    f"Invalid language_type '{language_type}', using default 'Chinese'"
                )
                language_type = "Chinese"

            return text, voice, language_type

        except Exception as e:
            logger.warning(f"Request parsing failed: {e}, using defaults")
            return request, "Cherry", "Chinese"
