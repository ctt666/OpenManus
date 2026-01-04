"""
MultimodalAgent单元测试
测试多模态Agent的初始化和基本功能
"""

from unittest.mock import AsyncMock, MagicMock, create_autospec

import pytest

from app.agent.multimodal import MultimodalAgent
from app.llm import LLM
from app.schema import Message


class TestMultimodalAgent:
    """MultimodalAgent单元测试类"""

    @pytest.fixture
    def mock_llm(self):
        """Mock LLM对象 - 使用 spec 确保类型检查通过"""
        # 使用 create_autospec 创建符合 LLM 接口的 mock
        llm = create_autospec(LLM, instance=True)
        llm.ask_tool = AsyncMock()
        llm.stream_ask = AsyncMock()
        return llm

    @pytest.fixture
    def agent(self, mock_llm):
        """创建测试Agent（同步fixture）"""
        # 直接传递 mock_llm 实例，不再 patch LLM 类
        agent = MultimodalAgent(llm=mock_llm)
        return agent

    def test_agent_initialization(self, mock_llm):
        """测试Agent初始化"""
        agent = MultimodalAgent(llm=mock_llm)

        assert agent.name == "MultimodalAgent"
        assert "multimodal" in agent.description.lower()
        assert agent.max_steps == 40  # 多模态任务需要更多步骤

    def test_agent_has_required_tools(self, mock_llm):
        """测试Agent包含必要的工具"""
        agent = MultimodalAgent(llm=mock_llm)

        tool_names = agent.available_tools.tool_map.keys()
        print(tool_names)
        assert "python_execute" in tool_names
        assert "ask_human" in tool_names
        assert "terminate" in tool_names

    def test_prompt_templates(self, agent):
        """测试提示词模板"""
        # 验证 prompt 模板已正确设置
        assert agent.system_prompt is not None
        assert agent.next_step_prompt is not None
        # 验证 next_step_prompt 是模板（包含占位符）
        assert "{request}" in agent.next_step_prompt or "{context}" in agent.next_step_prompt

    @pytest.mark.asyncio
    async def test_create_factory_method(self, mock_llm):
        """测试异步工厂方法"""
        agent = await MultimodalAgent.create(llm=mock_llm)

        assert isinstance(agent, MultimodalAgent)

    def test_agent_system_prompt_content(self, mock_llm):
        """测试系统提示词内容"""
        agent = MultimodalAgent(llm=mock_llm)

        # 验证系统提示词包含关键内容
        system_prompt = agent.system_prompt
        print(system_prompt)
        assert "multimodal" in system_prompt.lower() or "多模态" in system_prompt
        assert "image" in system_prompt.lower() or "图像" in system_prompt
        assert "audio" in system_prompt.lower() or "音频" in system_prompt


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
