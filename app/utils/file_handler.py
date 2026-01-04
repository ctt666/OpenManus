"""
File Handler for Multimodal Inputs
处理多模态文件输入（图像、音频、文本）
"""

import base64
import mimetypes
from pathlib import Path
from typing import Dict, List, Optional, Union

from app.logger import logger
from app.schema import Message, Role


class FileHandler:
    """处理多模态文件输入"""

    SUPPORTED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
    SUPPORTED_AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".ogg", ".flac"}
    SUPPORTED_TEXT_EXTS = {
        ".txt",
        ".md",
        ".json",
        ".csv",
        ".py",
        ".js",
        ".ts",
        ".html",
        ".css",
    }
    MAX_FILES = 3
    MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB

    @staticmethod
    def validate_file_paths(file_paths: List[str]) -> List[Union[Path, str]]:
        """
        验证文件路径（同时支持url和本地绝对文件路径）

        Args:
            file_paths: 文件路径列表，可以包含文件绝对路径或URL

        Returns:
            验证通过的Path对象或URL字符串列表

        Raises:
            ValueError: 文件数量超限或路径不在workspace下
            FileNotFoundError: 文件不存在
        """
        import re

        def is_url(path: str) -> bool:
            return bool(re.match(r"^https?://", path, re.IGNORECASE))

        if not file_paths:
            return []

        if len(file_paths) > FileHandler.MAX_FILES:
            raise ValueError(
                f"最多支持{FileHandler.MAX_FILES}个文件，当前提供了{len(file_paths)}个"
            )

        validated_paths = []

        for file_path in file_paths:
            file_path = file_path.strip()
            if not file_path:
                continue

            if is_url(file_path):
                # URL路径直接接受，不检查存在性
                validated_paths.append(file_path)
                # logger.info(f"✅ 验证通过: {file_path} (url)")
                continue

            # 绝对路径处理
            full_path = Path(file_path)
            if not full_path.exists():
                raise FileNotFoundError(f"文件不存在: {file_path}")

            if not full_path.is_file():
                raise ValueError(f"路径不是文件: {file_path}")

            file_size = full_path.stat().st_size
            if file_size > FileHandler.MAX_FILE_SIZE:
                size_mb = file_size / (1024 * 1024)
                limit_mb = FileHandler.MAX_FILE_SIZE / (1024 * 1024)
                raise ValueError(
                    f"文件 {full_path.name} 大小 ({size_mb:.2f}MB) 超过限制 ({limit_mb}MB)"
                )

            validated_paths.append(full_path)
            # logger.info(f"✅ 验证通过: {full_path.name} ({file_size/1024:.2f}KB)")

        return validated_paths

    @staticmethod
    def classify_file_paths(file_paths: List[Path]) -> Dict[str, List[Path]]:
        """
        分类文件路径
        """
        classified_paths = {"image": [], "audio": [], "text": []}
        for file_path in file_paths:
            file_type = FileHandler.get_file_type(file_path)
            classified_paths[file_type].append(file_path)
        return classified_paths

    @staticmethod
    def file_to_base64(file_path: Path) -> str:
        """
        读取文件并转为base64编码

        Args:
            file_path: 文件路径

        Returns:
            base64编码字符串
        """
        try:
            with open(file_path, "rb") as f:
                return base64.b64encode(f.read()).decode("utf-8")
        except Exception as e:
            logger.error(f"读取文件失败 {file_path}: {e}")
            raise

    @staticmethod
    def get_file_type(file_path) -> str:
        """
        获取文件类型，兼容本地Path和URL字符串

        Args:
            file_path: 文件路径（Path或str支持url）

        Returns:
            文件类型: 'image' | 'audio' | 'text' | 'unknown'
        """
        import re
        from urllib.parse import urlparse

        # 允许传入str或Path
        path_str = str(file_path)

        # 判断是否为URL
        is_url = False
        try:
            parsed = urlparse(path_str)
            if parsed.scheme in ("http", "https"):
                is_url = True
        except Exception:
            pass

        if is_url:
            # 取path部分的后缀，例如 .../filename.jpg
            ext = Path(urlparse(path_str).path).suffix.lower()
        else:
            ext = Path(path_str).suffix.lower()

        if ext in FileHandler.SUPPORTED_IMAGE_EXTS:
            return "image"
        elif ext in FileHandler.SUPPORTED_AUDIO_EXTS:
            return "audio"
        elif ext in FileHandler.SUPPORTED_TEXT_EXTS:
            return "text"
        else:
            logger.warning(f"未知文件类型: {ext} ({file_path})")
            return "unknown"

    @staticmethod
    def extract_audio_format(file_path: Path) -> str:
        """
        提取音频文件格式

        Args:
            file_path: 音频文件路径

        Returns:
            音频格式字符串（如: 'wav', 'mp3', 'm4a'）
        """
        ext = file_path.suffix.lower().lstrip(".")
        # 映射常见格式
        format_map = {
            "mp3": "mp3",
            "wav": "wav",
            "m4a": "m4a",
            "ogg": "ogg",
            "flac": "flac",
        }
        return format_map.get(ext, ext or "wav")

    @staticmethod
    def create_multimodal_content(
        prompt: str, file_paths: List[Path]
    ) -> Union[str, List[Dict]]:
        """
        构造多模态content（OpenAI格式）

        Args:
            prompt: 用户文本输入
            file_paths: 验证后的文件路径列表

        Returns:
            - 如果没有文件：返回纯文本字符串
            - 如果有文件：返回content数组格式
        """
        if not file_paths:
            return prompt

        content_parts = []

        # 添加文本部分
        if prompt and prompt.strip():
            content_parts.append({"type": "text", "text": prompt})

        # 添加文件部分
        for i, file_path in enumerate(file_paths):
            file_type = FileHandler.get_file_type(file_path)

            if file_type == "image":
                # 图像文件 - OpenAI格式
                mime_type = mimetypes.guess_type(file_path)[0] or "image/jpeg"
                base64_data = FileHandler.file_to_base64(file_path)
                content_parts.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{base64_data}"},
                    }
                )
                logger.info(f"📷 添加图像: {file_path.name} ({mime_type})")

            elif file_type == "audio":
                # 音频文件 - OpenAI兼容格式
                # 根据示例，data字段应该是纯base64字符串（不带data:前缀）
                # 或者是URL（如果是远程文件）
                audio_format = FileHandler.extract_audio_format(file_path)
                base64_data = FileHandler.file_to_base64(file_path)
                content_parts.append(
                    {
                        "type": "input_audio",
                        "input_audio": {
                            "data": base64_data,  # 纯base64字符串
                            "format": audio_format,
                        },
                    }
                )
                logger.info(f"🎵 添加音频: {file_path.name} (格式: {audio_format})")

            elif file_type == "text":
                # 文本文件 - 读取内容并整合到text中
                try:
                    # 读取文件内容，使用utf-8编码，忽略无法解码的字符
                    file_content = file_path.read_text(
                        encoding="utf-8", errors="ignore"
                    )

                    # 整合到第一个text部分，添加清晰的文件边界标记
                    content_parts[0]["text"] += f"\n\n{'='*50}\n"
                    content_parts[0]["text"] += f"📄 文件: {file_path.name}\n"
                    content_parts[0]["text"] += f"{'='*50}\n"
                    content_parts[0]["text"] += f"{file_content}\n"
                    content_parts[0]["text"] += f"{'='*50}\n"

                    logger.info(
                        f"📄 添加文本文件内容: {file_path.name} ({len(file_content)} 字符)"
                    )
                except Exception as e:
                    # 如果读取失败，至少记录路径
                    path_str = str(file_path)
                    content_parts[0][
                        "text"
                    ] += f"\n\n⚠️ 无法读取文件 {file_path.name}: {str(e)}\n"
                    logger.warning(f"读取文本文件失败 {file_path.name}: {e}")

            else:
                logger.warning(f"跳过不支持的文件类型: {file_path.name}")

        return content_parts

    @staticmethod
    def create_multimodal_message(
        prompt: str, file_paths: List[Path], role: str = "user"
    ) -> Message:
        """
        构造多模态消息对象

        Args:
            prompt: 用户文本输入
            file_paths: 验证后的文件路径列表
            role: 消息角色，默认"user"

        Returns:
            Message对象

        Note:
            目前Message.content是str类型，但LLM.ask_tool会处理多模态content
            这里将content数组序列化为JSON字符串存储
        """
        import json

        content = FileHandler.create_multimodal_content(prompt, file_paths)

        # 如果content是list，需要特殊处理
        # 因为当前Message.content是Optional[str]
        if isinstance(content, list):
            # 标记这是多模态消息，在LLM调用时特殊处理
            return Message(
                role=role,
                content=json.dumps(
                    {"multimodal": True, "content": content}, ensure_ascii=False
                ),
            )
        else:
            return Message(role=role, content=content)

    @staticmethod
    def extract_multimodal_outputs(
        messages: List[Message],
    ) -> Optional[Dict[str, List[str]]]:
        """
        从消息列表中提取多模态输出（图像、音频路径）

        Args:
            messages: 消息列表

        Returns:
            包含images和audios路径列表的字典，如果没有则返回None
        """
        import re

        outputs = {"images": [], "audios": []}

        # 遍历消息查找生成的文件路径
        for msg in messages:
            if msg.content:
                content = str(msg.content)

                # 检测图像输出（workspace/images/路径）
                image_patterns = [
                    r'workspace[/\\]images[/\\][^\s\'"]+\.(?:png|jpg|jpeg|gif|webp|bmp)',
                    r'/workspace/images/[^\s\'"]+\.(?:png|jpg|jpeg|gif|webp|bmp)',
                ]
                for pattern in image_patterns:
                    images = re.findall(pattern, content, re.IGNORECASE)
                    outputs["images"].extend(images)

                # 检测音频输出（workspace/audios/路径）
                audio_patterns = [
                    r'workspace[/\\]audios[/\\][^\s\'"]+\.(?:mp3|wav|m4a|ogg|flac)',
                    r'/workspace/audios/[^\s\'"]+\.(?:mp3|wav|m4a|ogg|flac)',
                ]
                for pattern in audio_patterns:
                    audios = re.findall(pattern, content, re.IGNORECASE)
                    outputs["audios"].extend(audios)

        # 去重并规范化路径
        outputs["images"] = list(set([p.replace("\\", "/") for p in outputs["images"]]))
        outputs["audios"] = list(set([p.replace("\\", "/") for p in outputs["audios"]]))

        # 如果没有多模态输出，返回None
        if not outputs["images"] and not outputs["audios"]:
            return None

        logger.info(
            f"📦 提取到多模态输出: {len(outputs['images'])}个图像, {len(outputs['audios'])}个音频"
        )
        return outputs
