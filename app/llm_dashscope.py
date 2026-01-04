"""
DashScope SDK 封装模块
统一使用 MultiModalConversation.call 接口处理图像生成和音频生成
"""

import json
import os
import time
from pathlib import Path
from typing import Dict, Literal, Optional

import dashscope
from dashscope import MultiModalConversation

from app.config import config
from app.logger import logger


class DashScopeGenerator:
    """
    DashScope统一生成器
    支持图像生成和音频生成，统一使用 MultiModalConversation.call 接口
    """

    def __init__(self, api_key: Optional[str] = None):
        """
        初始化生成器

        Args:
            api_key: DashScope API密钥，如果不提供则从环境变量读取
        """
        self.api_key = api_key or os.getenv("DASHSCOPE_API_KEY")

        # 设置API base URL（北京地域）
        dashscope.base_http_api_url = "https://dashscope.aliyuncs.com/api/v1"

        # 设置输出目录
        self.images_dir = config.workspace_root / "images"
        self.audios_dir = config.workspace_root / "audios"
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.audios_dir.mkdir(parents=True, exist_ok=True)

    def generate_image(
        self,
        prompt: str,
        model: str = "qwen-image-plus",
        negative_prompt: str = "",
        size: str = "1024*1024",
        watermark: bool = False,
        prompt_extend: bool = True,
        seed: Optional[int] = None,
    ) -> Dict:
        """
        生成图像（同步方法）

        Args:
            prompt: 图像描述（支持中英文）
            model: 模型名称，默认 qwen-image-plus
            negative_prompt: 负面提示词（不想要的内容）
            size: 图像尺寸，可选 1024*1024, 720*1280, 1280*720, 1328*1328
            watermark: 是否添加水印
            prompt_extend: 是否自动扩展提示词
            seed: 随机种子（可选，用于复现）

        Returns:
            {
                'success': bool,
                'images': List[str],  # 本地路径列表
                'urls': List[str],    # 远程URL列表
                'request_id': str,
                'message': str
            }
        """
        try:
            # 构建messages格式
            messages = [{"role": "user", "content": [{"text": prompt}]}]

            # 构建请求参数
            params = {
                "api_key": self.api_key,
                "model": model,
                "messages": messages,
                "result_format": "message",
                "stream": False,
                "watermark": watermark,
                "prompt_extend": prompt_extend,
                "negative_prompt": negative_prompt,
                "size": size,
            }

            # 添加seed（如果提供）
            if seed is not None:
                params["seed"] = seed

            logger.info(f"Generating image with prompt: {prompt[:50]}...")
            logger.debug(
                f"Image params: {json.dumps({k: v for k, v in params.items() if k != 'api_key'}, ensure_ascii=False)}"
            )

            # 调用API
            response = MultiModalConversation.call(**params)

            if response.status_code == 200:
                logger.info(
                    f"Image generation success, request_id: {response.request_id}"
                )

                # 解析响应，提取图像URL
                image_urls = []
                if hasattr(response.output, "choices") and response.output.choices:
                    message = response.output.choices[0].message
                    if hasattr(message, "content") and isinstance(
                        message.content, list
                    ):
                        for item in message.content:
                            if isinstance(item, dict) and "image" in item:
                                url = item["image"]
                                image_urls.append(url)
                                logger.info(f"Found image URL: {url}")

                # 下载图像到本地
                image_paths = []
                if image_urls:
                    import httpx

                    for idx, url in enumerate(image_urls):
                        try:
                            filename = f"{int(time.time())}_{idx}.png"
                            filepath = self.images_dir / filename

                            with httpx.Client(timeout=30.0) as client:
                                img_response = client.get(url)
                                if img_response.status_code == 200:
                                    filepath.write_bytes(img_response.content)
                                    image_paths.append(str(filepath))
                                    logger.info(f"Image saved to: {filepath}")
                                else:
                                    logger.warning(
                                        f"Failed to download image, status: {img_response.status_code}"
                                    )
                        except Exception as e:
                            logger.warning(f"Failed to download image: {e}")

                return {
                    "success": True,
                    "images": image_paths,
                    "urls": image_urls,
                    "request_id": response.request_id,
                    "message": f"Successfully generated {len(image_paths)} image(s)",
                }
            else:
                error_msg = f"HTTP返回码：{response.status_code}\n错误码：{response.code}\n错误信息：{response.message}"
                logger.error(error_msg)
                logger.error(
                    "请参考文档：https://help.aliyun.com/zh/model-studio/developer-reference/error-code"
                )
                return {
                    "success": False,
                    "images": [],
                    "urls": [],
                    "message": error_msg,
                }

        except Exception as e:
            error_msg = f"Error in image generation: {str(e)}"
            logger.exception(error_msg)
            return {"success": False, "images": [], "urls": [], "message": error_msg}

    def generate_audio(
        self,
        text: str,
        model: str = "qwen3-tts-flash",
        voice: str = "Cherry",
        language_type: str = "Chinese",
        stream: bool = False,
    ) -> Dict:
        """
        生成语音（同步方法）

        Args:
            text: 要合成的文本（最多5000字）
            model: 模型名称，默认 qwen3-tts-flash
            voice: 音色名称，可选: Cherry, Baixue, Zhichu等
            language_type: 语言类型，建议与文本语种一致（Chinese/English/Japanese等）
            stream: 是否流式返回

        Returns:
            {
                'success': bool,
                'audio_path': str,
                'audio_url': str,
                'duration': float,
                'message': str
            }
        """
        try:
            # 检查文本长度
            if len(text) > 5000:
                return {
                    "success": False,
                    "audio_path": None,
                    "audio_url": None,
                    "message": "Text exceeds 5000 characters limit",
                }

            logger.info(f"Generating audio for text: {text[:50]}...")

            # 构建请求参数（参考官方示例）
            params = {
                "api_key": self.api_key,
                "model": model,
                "text": text,
                "voice": voice,
                "language_type": language_type,
                "stream": stream,
            }

            logger.debug(
                f"Audio params: {json.dumps({k: v for k, v in params.items() if k != 'api_key'}, ensure_ascii=False)}"
            )

            # 调用API
            response = MultiModalConversation.call(**params)

            if response.status_code == 200:
                logger.info(
                    f"Audio generation success, request_id: {response.request_id}"
                )

                # 解析响应，提取音频URL（参考官方示例：response.output.audio.url）
                audio_url = None
                try:
                    if hasattr(response.output, "audio") and response.output.audio:
                        audio_obj = response.output.audio
                        # 尝试获取URL属性
                        if hasattr(audio_obj, "url"):
                            audio_url = audio_obj.url
                        # 如果没有url属性，尝试其他可能的属性
                        elif isinstance(audio_obj, str):
                            audio_url = audio_obj
                        else:
                            logger.debug(
                                f"Audio object type: {type(audio_obj)}, attributes: {dir(audio_obj)}"
                            )
                except Exception as e:
                    logger.debug(f"Failed to get audio URL: {e}")
                    logger.debug(f"Response output: {response.output}")

                logger.info(f"Audio URL: {audio_url}")

                # 下载音频到本地
                audio_path = None
                if audio_url:
                    try:
                        import httpx

                        filename = f"{int(time.time())}.mp3"
                        filepath = self.audios_dir / filename

                        with httpx.Client(timeout=30.0) as client:
                            audio_response = client.get(audio_url)
                            if audio_response.status_code == 200:
                                filepath.write_bytes(audio_response.content)
                                audio_path = str(filepath)
                                logger.info(f"Audio saved to: {filepath}")
                            else:
                                logger.warning(
                                    f"Failed to download audio, status: {audio_response.status_code}"
                                )
                    except Exception as e:
                        logger.warning(f"Failed to download audio: {e}")

                # 计算音频时长（可选）
                duration = None
                if audio_path:
                    duration = self._get_audio_duration(Path(audio_path))

                return {
                    "success": True,
                    "audio_path": audio_path,
                    "audio_url": audio_url,
                    "duration": duration,
                    "message": f"Audio generated successfully",
                }
            else:
                error_msg = f"HTTP返回码：{response.status_code}\n错误码：{response.code}\n错误信息：{response.message}"
                logger.error(error_msg)
                logger.error(
                    "请参考文档：https://help.aliyun.com/zh/model-studio/developer-reference/error-code"
                )
                return {
                    "success": False,
                    "audio_path": None,
                    "audio_url": None,
                    "message": error_msg,
                }

        except Exception as e:
            error_msg = f"Error in audio generation: {str(e)}"
            logger.exception(error_msg)
            return {
                "success": False,
                "audio_path": None,
                "audio_url": None,
                "message": error_msg,
            }

    def _get_audio_duration(self, filepath: Path) -> Optional[float]:
        """获取音频时长（秒）"""
        try:
            from pydub import AudioSegment

            audio = AudioSegment.from_file(str(filepath))
            return len(audio) / 1000.0
        except ImportError:
            logger.warning("pydub not installed, cannot calculate audio duration")
            return None
        except Exception as e:
            logger.warning(f"Failed to calculate audio duration: {e}")
            return None


# 全局单例
_generator = None


def get_generator() -> DashScopeGenerator:
    """获取全局生成器实例"""
    global _generator
    if _generator is None:
        _generator = DashScopeGenerator()
    return _generator


# 便捷函数
def generate_image(prompt: str, **kwargs) -> Dict:
    """便捷的图像生成函数（同步）"""
    generator = get_generator()
    return generator.generate_image(prompt, **kwargs)


def generate_audio(text: str, **kwargs) -> Dict:
    """便捷的音频生成函数（同步）"""
    generator = get_generator()
    return generator.generate_audio(text, **kwargs)
