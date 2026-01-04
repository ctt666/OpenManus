"""
多模态功能端到端测试
测试Task和Flow的完整多模态流程
"""

import asyncio
import shutil
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import config
from server import TaskManager


class TestMultimodalTaskE2E:
    """多模态Task端到端测试"""

    @pytest.mark.asyncio
    # @pytest.mark.skip(reason="需要真实的LLM API，仅用于手动测试")
    async def test_task_with_text_file_input(self):
        """测试Task处理文本文件输入（.txt、.py、.json）"""
        from app.agent.multimodal import MultimodalAgent
        from app.config import config
        from app.utils.file_handler import FileHandler

        # 准备文本文件路径（绝对路径）
        file_paths = [
            r"D:\python_project\openmanus-test\tests\data\docs\requirements.txt",
        ]
        input_text = "请根据这些文档分析项目需求并生成实现方案"

        # 验证文件路径
        validated_paths = FileHandler.validate_file_paths(file_paths)

        # 验证所有文件都被正确识别为文本类型
        for path in validated_paths:
            file_type = FileHandler.get_file_type(path)
            assert file_type == "text", f"{path.name} 应该被识别为text类型"

        # 创建MultimodalAgent
        agent = await MultimodalAgent.create()

        # 构造多模态消息
        classified_paths = FileHandler.classify_file_paths(validated_paths)
        if "text" in classified_paths:
            for file_path in classified_paths.get("text"):
                file_content = file_path.read_text(encoding="utf-8", errors="ignore")

                # 整合到第一个text部分，添加清晰的文件边界标记
                input_text += f"\n\n{'='*50}\n"
                input_text += f"📄 文件: {file_path.name}\n"
                input_text += f"{'='*50}\n"
                input_text += f"{file_content}\n"
                input_text += f"{'='*50}\n"

        task_manager = TaskManager()

        # 定义流式回调函数 - 实时推送总结的chunk到前端
        async def stream_summary_callback(chunk: str):
            """推送summary的每个chunk到SSE事件流"""
            print("chunk:", chunk)

        # 执行（这需要真实的LLM API）
        result = await agent.run(
            request=input_text, stream_callback=stream_summary_callback
        )
        assert result is not None
        print("result:", result)

        await agent.cleanup()

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="需要真实的LLM API，仅用于手动测试")
    async def test_task_with_image_input(self):
        """测试Task处理图像输入"""
        from app.agent.multimodal import MultimodalAgent
        from app.config import config
        from app.schema import Message
        from app.utils.file_handler import FileHandler

        # 准备文件路径
        file_paths = [
            r"D:\python_project\openmanus-test\tests\data\images\1763629520_0.png",
        ]
        validated_paths = FileHandler.validate_file_paths(file_paths)
        classified_paths = FileHandler.classify_file_paths(validated_paths)

        # 创建MultimodalAgent
        agent = await MultimodalAgent.create()

        # 执行（这需要真实的LLM API）
        result = await agent.run(
            request="请分析这张图片", multimodal_paths=classified_paths
        )
        assert result is not None
        print("result:", result)

        # 验证消息已添加到memory
        assert len(agent.memory.messages) > 0
        assert agent.memory.messages[0].role == "user"

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="需要真实的LLM API，仅用于手动测试")
    async def test_task_api_endpoint(self):
        """测试/task接口接受file_paths参数"""
        from fastapi.testclient import TestClient

        # Mock掉run_task以避免实际执行
        with patch("server.run_task", new_callable=AsyncMock):
            # 动态导入server（避免在import时就启动）
            import importlib

            import server as server_module

            importlib.reload(server_module)

            client = TestClient(server_module.app)

            # 发送请求
            response = client.post(
                "/task",
                json={
                    "prompt": "分析这张图片",
                    "file_paths": [
                        "https://img.alicdn.com/imgextra/i1/O1CN01gDEY8M1W114Hi3XcN_!!6000000002727-0-tps-1024-406.jpg"
                    ],
                    "session_id": "test-session",
                },
            )

            assert response.status_code == 200
            assert "task_id" in response.json()


class TestMultimodalFlowE2E:
    """多模态Flow端到端测试"""

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="需要真实的LLM API，仅用于手动测试")
    async def test_flow_with_multimodal_input(self):
        """测试Flow处理多模态输入"""
        from app.agent.flow_agent import FlowAgent
        from app.agent.image_generation import ImageGenerationAgent
        from app.config import config
        from app.flow.flow_factory import FlowFactory, FlowType
        from app.utils.file_handler import FileHandler

        # 准备文件路径
        file_paths = [
            "https://img.alicdn.com/imgextra/i1/O1CN01gDEY8M1W114Hi3XcN_!!6000000002727-0-tps-1024-406.jpg"
        ]
        validated_paths = FileHandler.validate_file_paths(file_paths)
        classified_paths = FileHandler.classify_file_paths(validated_paths)

        # 创建Agents
        agents = [await FlowAgent().create(), ImageGenerationAgent()]

        # 创建PlanningFlow
        flow = FlowFactory.create_flow(flow_type=FlowType.PLANNING, agents=agents)

        # 执行Flow（需要真实LLM API）
        result = await flow.execute(
            "这道题怎么解答？", multimodal_paths=classified_paths
        )
        assert result is not None
        print("result:", result)

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="需要真实的LLM API，仅用于手动测试")
    async def test_flow_with_text_file_input(self):
        """测试Flow处理文本文件输入（.txt、.py、.json）"""
        from app.agent.flow_agent import FlowAgent
        from app.agent.image_generation import ImageGenerationAgent
        from app.config import config
        from app.flow.flow_factory import FlowFactory, FlowType
        from app.utils.file_handler import FileHandler

        # 准备三个文本文件路径
        file_paths = [
            r"D:\python_project\openmanus-test\tests\data\docs\requirements.txt",
        ]
        validated_paths = FileHandler.validate_file_paths(file_paths)
        classified_paths = FileHandler.classify_file_paths(validated_paths)

        # 创建Agents
        agents = {
            "flow": await FlowAgent().create(),
        }

        # 创建PlanningFlow
        flow = FlowFactory.create_flow(flow_type=FlowType.PLANNING, agents=agents)

        # 构造包含多个文本文件的多模态消息
        request = "请根据这些文档分析项目需求并生成实现方案"
        if "text" in classified_paths:
            for file_path in classified_paths.get("text"):
                file_content = file_path.read_text(encoding="utf-8", errors="ignore")
                request += f"\n\n{'='*50}\n"
                request += f"📄 文件: {file_path.name}\n"
                request += f"{'='*50}\n"
                request += f"{file_content}\n"
                request += f"{'='*50}\n"

        result = await flow.execute(request)
        assert result is not None
        print("result:", result)

        # 验证PlanningFlow已初始化
        assert flow is not None
        assert hasattr(flow, "execute")

    @pytest.mark.skip(reason="需要真实的LLM API，仅用于手动测试")
    @pytest.mark.asyncio
    async def test_flow_api_endpoint(self):
        """测试/flow接口接受file_paths参数"""
        import sys
        from pathlib import Path

        from fastapi.testclient import TestClient

        # 添加项目根目录到Python路径
        project_root = Path(__file__).parent.parent
        sys.path.insert(0, str(project_root))

        # 动态导入server
        import importlib

        import server as server_module

        importlib.reload(server_module)

        # 在reload之后Mock掉run_flow_task以避免实际执行
        with patch.object(server_module, "run_flow_task", new_callable=AsyncMock):
            client = TestClient(server_module.app)

            # 发送请求
            response = client.post(
                "/flow",
                json={
                    "prompt": "描述图像内容",
                    "file_paths": [
                        "https://img.alicdn.com/imgextra/i1/O1CN01gDEY8M1W114Hi3XcN_!!6000000002727-0-tps-1024-406.jpg"
                    ],
                    "session_id": "test-session",
                },
            )

            assert response.status_code == 200
            assert "flow_id" in response.json()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
