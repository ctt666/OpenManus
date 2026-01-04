"""
文件处理模块单元测试
测试FileHandler的各项功能
"""

import shutil
import tempfile
from pathlib import Path

import pytest

from app.schema import Message
from app.utils.file_handler import FileHandler


class TestFileHandler:
    """FileHandler单元测试类"""

    @pytest.fixture
    def temp_workspace(self):
        """创建临时workspace目录"""
        temp_dir = tempfile.mkdtemp()
        workspace = Path(temp_dir) / "workspace"
        workspace.mkdir()

        # 创建测试文件
        images_dir = workspace / "images"
        audios_dir = workspace / "audios"
        images_dir.mkdir()
        audios_dir.mkdir()

        # 创建测试图像文件
        test_image = images_dir / "test.jpg"
        test_image.write_bytes(b"fake image data")

        # 创建测试音频文件
        test_audio = audios_dir / "test.mp3"
        test_audio.write_bytes(b"fake audio data")

        # 创建测试文本文件
        test_text = workspace / "test.txt"
        test_text.write_text("Hello, World!")

        yield str(workspace)

        # 清理
        shutil.rmtree(temp_dir)

    def test_validate_file_paths_success(self):
        """测试文件路径验证 - 成功场景"""
        file_paths = ["images/test.jpg", "test.txt"]

        validated = FileHandler.validate_file_paths(file_paths)

        assert len(validated) == 2
        assert all(p.exists() for p in validated)

    def test_validate_file_paths_too_many_files(self, temp_workspace):
        """测试文件路径验证 - 文件数量超限"""
        file_paths = ["images/test.jpg"] * 4  # 超过MAX_FILES

        with pytest.raises(ValueError, match="最多支持"):
            FileHandler.validate_file_paths(file_paths)

    def test_validate_file_paths_file_not_found(self, temp_workspace):
        """测试文件路径验证 - 文件不存在"""
        file_paths = ["images/nonexistent.jpg"]

        with pytest.raises(FileNotFoundError, match="文件不存在"):
            FileHandler.validate_file_paths(file_paths)

    def test_validate_file_paths_outside_workspace(self):
        """测试文件路径验证 - 文件在workspace外"""
        import os

        file_paths = [
            os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
        ]  # 项目根目录路径，尝试访问workspace外的文件

        with pytest.raises(ValueError, match="安全限制"):
            FileHandler.validate_file_paths(file_paths)

    def test_get_file_type(self, temp_workspace):
        """测试文件类型识别"""
        workspace = Path(temp_workspace)

        assert FileHandler.get_file_type(workspace / "images/test.jpg") == "image"
        assert FileHandler.get_file_type(workspace / "audios/test.mp3") == "audio"
        assert FileHandler.get_file_type(workspace / "test.txt") == "text"

    def test_file_to_base64(self, temp_workspace):
        """测试文件转base64"""
        workspace = Path(temp_workspace)
        test_file = workspace / "test.txt"

        base64_data = FileHandler.file_to_base64(test_file)

        assert isinstance(base64_data, str)
        assert len(base64_data) > 0
        # 验证是否为有效的base64
        import base64

        decoded = base64.b64decode(base64_data)
        assert decoded == b"Hello, World!"

    def test_create_multimodal_content_text_only(self):
        """测试多模态内容创建 - 纯文本"""
        content = FileHandler.create_multimodal_content("Hello", [])

        assert content == "Hello"

    def test_create_multimodal_content_with_files(self, temp_workspace):
        """测试多模态内容创建 - 包含文件"""
        workspace = Path(temp_workspace)
        file_paths = [workspace / "images/test.jpg", workspace / "test.txt"]

        content = FileHandler.create_multimodal_content("Analyze these", file_paths)

        assert isinstance(content, list)
        assert len(content) >= 3  # 文本 + 图像 + 文本文件
        assert any(part.get("type") == "text" for part in content)
        assert any(part.get("type") == "image_url" for part in content)

    def test_create_multimodal_message(self, temp_workspace):
        """测试多模态消息创建"""
        workspace = Path(temp_workspace)
        file_paths = [workspace / "images/test.jpg"]

        msg = FileHandler.create_multimodal_message("Test", file_paths)

        assert isinstance(msg, Message)
        assert msg.role == "user"
        assert msg.content is not None

    def test_extract_multimodal_outputs_empty(self):
        """测试多模态输出提取 - 无输出"""
        messages = [Message.user_message("Hello")]

        result = FileHandler.extract_multimodal_outputs(messages)

        assert result is None

    def test_extract_multimodal_outputs_with_images(self):
        """测试多模态输出提取 - 包含图像"""
        messages = [
            Message.tool_message(
                content="Generated image: workspace/images/output.png",
                name="image_gen",
                tool_call_id="123",
            )
        ]

        result = FileHandler.extract_multimodal_outputs(messages)

        assert result is not None
        assert "images" in result
        assert len(result["images"]) == 1
        assert "workspace/images/output.png" in result["images"]

    def test_extract_multimodal_outputs_with_audios(self):
        """测试多模态输出提取 - 包含音频"""
        messages = [
            Message.tool_message(
                content="Generated audio: workspace/audios/speech.mp3",
                name="audio_gen",
                tool_call_id="456",
            )
        ]

        result = FileHandler.extract_multimodal_outputs(messages)

        assert result is not None
        assert "audios" in result
        assert len(result["audios"]) == 1
        assert "workspace/audios/speech.mp3" in result["audios"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
