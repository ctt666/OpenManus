import json

import pytest

from app.agent.base import BaseAgent
from app.flow.planning import PlanningFlow


class DummyExecutorAgent(BaseAgent):
    name: str = "manus"
    description: str = "dummy executor"
    tool_calls: list = []

    async def think(self, request=None, context=None):
        return False, ""

    async def act(self) -> str:  # pragma: no cover
        return ""

    async def step(self) -> str:  # pragma: no cover
        return ""

    async def summarize(
        self, request: str, stream_callback=None
    ) -> str:  # pragma: no cover
        return ""

    async def run(
        self, request=None, stream_callback=None, multimodal_paths=None, context=None
    ):
        return "out1"


@pytest.mark.asyncio
async def test_heuristic_planning_one_shot_end_true(monkeypatch):
    agents = {"manus": DummyExecutorAgent()}
    flow = PlanningFlow(agents)

    async def _ask_stub(*args, **kwargs):
        return json.dumps({"end": True, "agent": "", "reason": "final answer"})

    monkeypatch.setattr(flow.llm, "ask", _ask_stub)

    out = await flow.execute("hello")
    assert out == "final answer"
    plan = flow.planning_tool.plans[flow.active_plan_id]
    assert plan["steps"][-1].startswith("[HEURISTIC] final")
    note = json.loads(plan["step_notes"][-1])
    # end=true 时 agent_name 应等于 LLM 输出的 agent 字段值（这里 stub 为 ""）
    assert note["agent_name"] == ""
    assert note["step_output"] == "final answer"


@pytest.mark.asyncio
async def test_heuristic_planning_two_loops(monkeypatch):
    agents = {"manus": DummyExecutorAgent()}
    flow = PlanningFlow(agents)

    calls = {"n": 0}

    async def _ask_stub(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return json.dumps({"end": False, "agent": "manus", "reason": "do x"})
        return json.dumps({"end": True, "agent": "", "reason": "done"})

    monkeypatch.setattr(flow.llm, "ask", _ask_stub)

    out = await flow.execute("req")
    assert out == "done"
    plan = flow.planning_tool.plans[flow.active_plan_id]
    # init + iter1 + final
    assert len(plan["steps"]) >= 3
    iter1_note = json.loads(plan["step_notes"][1])
    assert iter1_note["agent_name"] == "manus"
    assert iter1_note["step_output"] == "out1"
    final_note = json.loads(plan["step_notes"][-1])
    assert "out1" in final_note["context"]


@pytest.mark.asyncio
async def test_heuristic_planning_reaches_max_loops(monkeypatch):
    agents = {"manus": DummyExecutorAgent()}
    flow = PlanningFlow(agents, max_loops=3)

    async def _ask_stub(*args, **kwargs):
        return json.dumps({"end": False, "agent": "manus", "reason": "keep going"})

    monkeypatch.setattr(flow.llm, "ask", _ask_stub)

    out = await flow.execute("req")
    assert "未收敛" in out
    assert f"{flow.max_loops}" in out
