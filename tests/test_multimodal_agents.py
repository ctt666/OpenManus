"""
测试多模态Agent功能
包括图像生成和音频生成
"""

import asyncio
import os
from pathlib import Path

from app.agent.flow_agent import FlowAgent

# 设置环境变量（如果需要）
# os.environ["DASHSCOPE_API_KEY"] = "your-api-key-here"


async def test_image_generation():
    """测试图像生成Agent"""
    print("\n" + "=" * 60)
    print("测试 ImageGenerationAgent")
    print("=" * 60)

    from app.agent.image_generation import ImageGenerationAgent

    agent = ImageGenerationAgent()

    # 测试用例1：简单描述
    request1 = "生成一只可爱的橘猫，坐在窗台上晒太阳"
    print(f"\n请求: {request1}")
    result1 = await agent.run(request1)
    print(f"\n结果:\n{result1}")

    # 测试用例2：详细描述
    request2 = "创作一幅中国风水墨画：远山如黛，近水含烟，一叶扁舟漂浮于湖面，渔翁独坐船头，画面意境悠远"
    print(f"\n请求: {request2}")
    result2 = await agent.run(request2)
    print(f"\n结果:\n{result2}")

    await agent.cleanup()


async def test_audio_generation():
    """测试音频生成Agent"""
    print("\n" + "=" * 60)
    print("测试 AudioGenerationAgent")
    print("=" * 60)

    from app.agent.audio_generation import AudioGenerationAgent

    agent = AudioGenerationAgent()

    # 测试用例1：简单文本
    request1 = "请用女声合成：欢迎使用OpenManus多模态AI系统，我们为您提供图像生成和语音合成服务。"
    print(f"\n请求: {request1}")
    result1 = await agent.run(request1)
    print(f"\n结果:\n{result1}")

    # 测试用例2：指定参数
    request2 = """请用男声合成以下内容：

    在这个快速变化的时代，人工智能技术正在深刻改变着我们的生活方式。
    从智能助手到自动驾驶，从医疗诊断到艺术创作，AI的应用无处不在。
    让我们共同探索这个充满无限可能的未来世界。
    """
    print(f"\n请求: {request2}")
    result2 = await agent.run(request2)
    print(f"\n结果:\n{result2}")

    await agent.cleanup()


async def test_planning_flow_with_multimodal():
    """测试PlanningFlow使用多模态Agent"""
    print("\n" + "=" * 60)
    print("测试 PlanningFlow 多Agent协作")
    print("=" * 60)

    from app.agent.audio_generation import AudioGenerationAgent
    from app.agent.image_generation import ImageGenerationAgent
    from app.agent.manus import Manus
    from app.flow.planning import PlanningFlow

    # 创建多个Agent
    agents = {
        "flow": await FlowAgent().create(),
        "image_gen": ImageGenerationAgent(),
        "audio_gen": AudioGenerationAgent(),
    }

    # 创建PlanningFlow
    flow = PlanningFlow(agents)

    # 执行复杂任务
    request = """
    请完成以下任务：
    1. 生成一张"夕阳下的海滩，椰树婆娑，海浪轻柔"的图片
    2. 为这张图片创作一段解说音频（约50字）
    """

    print(f"\n请求: {request}")
    result = await flow.execute(request)
    print(f"\n最终结果:\n{result}")

    # 清理
    for agent in agents.values():
        await agent.cleanup()


async def main():
    """主测试函数"""
    print("\n🚀 OpenManus 多模态Agent测试套件")

    # 检查API密钥
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key or api_key == "YOUR_DASHSCOPE_API_KEY":
        print("\n⚠️  警告: 未设置DASHSCOPE_API_KEY环境变量")
        print("请设置环境变量或在config/config.toml中配置API密钥")
        print("\n示例:")
        print("  export DASHSCOPE_API_KEY='your-api-key'")
        print("  或在config/config.toml中修改api_key配置")
        return

    print(f"\n✅ API密钥已配置: {api_key[:10]}...")

    # 选择要运行的测试
    print("\n请选择要运行的测试:")
    print("1. 测试图像生成Agent")
    print("2. 测试音频生成Agent")
    print("3. 测试PlanningFlow多Agent协作")

    choice = input("\n请输入选项 (1-3): ").strip()

    try:
        if choice == "1":
            await test_image_generation()
        elif choice == "2":
            await test_audio_generation()
        elif choice == "3":
            await test_planning_flow_with_multimodal()
        else:
            print("无效的选项")
    except Exception as e:
        print(f"\n❌ 测试过程中出现错误: {e}")
        import traceback

        traceback.print_exc()

    print("\n✅ 测试完成")


if __name__ == "__main__":
    asyncio.run(main())
