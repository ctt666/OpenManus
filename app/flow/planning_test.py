import asyncio
import os
import sys

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.agent.flow_agent import FlowAgent
from app.flow.planning import PlanningFlow


async def test_create_initial_plan():
    agents = {}
    flow = None
    try:
        # 创建FlowAgent实例
        flow_agent = FlowAgent()
        agents = {"Flow": await flow_agent.create()}

        # 创建PlanningFlow实例
        flow = PlanningFlow(agents=agents)

        # 执行测试
        plan = await flow._create_initial_plan(
            "8月中旬我想去澳门旅游5天，帮我做一个攻略，包括旅游景点和入住酒店。入住的酒店我希望交通便利，舒适，最好是星级酒店。"
        )
        return plan
    finally:
        # 确保清理所有资源
        if agents:
            for agent in agents.values():
                try:
                    await agent.cleanup()
                except Exception as e:
                    print(f"Warning: Error during agent cleanup: {e}")

        # if flow:
        #     try:
        #         # 如果PlanningFlow有cleanup方法，也调用它
        #         if hasattr(flow, "cleanup"):
        #             await flow.cleanup()
        #     except Exception as e:
        #         print(f"Warning: Error during flow cleanup: {e}")


if __name__ == "__main__":
    plan = asyncio.run(test_create_initial_plan())
    print(plan)
