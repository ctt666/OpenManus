import json
import time
from enum import Enum
from typing import Any, Dict, List, Optional, Union

from pydantic import Field

from app.agent.base import BaseAgent
from app.agent.guardrail import (
    GuardrailAction,
    GuardrailAgent,
    GuardrailBoundary,
    GuardrailEngine,
)
from app.config import config
from app.exceptions import TokenLimitExceeded
from app.flow.base import BaseFlow
from app.llm import LLM
from app.logger import logger
from app.prompt import planning_flow
from app.schema import AgentState, Memory, Message
from app.tool import PlanningTool


class PlanStepStatus(str, Enum):
    """Enum class defining possible statuses of a plan step"""

    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    BLOCKED = "blocked"

    @classmethod
    def get_all_statuses(cls) -> list[str]:
        """Return a list of all possible step status values"""
        return [status.value for status in cls]

    @classmethod
    def get_active_statuses(cls) -> list[str]:
        """Return a list of values representing active statuses (not started or in progress)"""
        return [cls.NOT_STARTED.value, cls.IN_PROGRESS.value]

    @classmethod
    def get_status_marks(cls) -> Dict[str, str]:
        """Return a mapping of statuses to their marker symbols"""
        return {
            cls.COMPLETED.value: "[✓]",
            cls.IN_PROGRESS.value: "[→]",
            cls.BLOCKED.value: "[!]",
            cls.NOT_STARTED.value: "[ ]",
        }


class PlanningFlow(BaseFlow):
    """A flow that manages planning and execution of tasks using agents."""

    llm: LLM = Field(default_factory=lambda: LLM(config_name="multimodal"))
    memory: Memory = Field(default_factory=Memory, description="Flow's memory store")
    planning_tool: PlanningTool = Field(default_factory=PlanningTool)
    executor_keys: List[str] = Field(default_factory=list)
    active_plan_id: str = Field(default_factory=lambda: f"plan_{int(time.time())}")
    current_step_index: Optional[int] = None
    max_loops: int = Field(default=10, description="Maximum heuristic planning loops")
    planner_parse_retries: int = Field(
        default=2, description="Retries when planner output is invalid"
    )

    def __init__(
        self, agents: Union[BaseAgent, List[BaseAgent], Dict[str, BaseAgent]], **data
    ):
        # Set executor keys before super().__init__
        if "executors" in data:
            data["executor_keys"] = data.pop("executors")

        # Set plan ID if provided
        if "plan_id" in data:
            data["active_plan_id"] = data.pop("plan_id")

        # Initialize the planning tool if not provided
        if "planning_tool" not in data:
            planning_tool = PlanningTool()
            data["planning_tool"] = planning_tool

        # Call parent's init with the processed data
        super().__init__(agents, **data)

        # Set executor_keys to all agent keys if not specified
        if not self.executor_keys:
            self.executor_keys = list(self.agents.keys())

    def get_executor(self, step_type: Optional[str] = None) -> BaseAgent:
        """
        Get an appropriate executor agent for the current step.
        Can be extended to select agents based on step type/requirements.
        """
        # If step type is provided and matches an agent key, use that agent
        if step_type and step_type in self.agents:
            return self.agents[step_type]

        # Otherwise use the first available executor or fall back to primary agent
        for key in self.executor_keys:
            if key in self.agents:
                return self.agents[key]

        # Fallback to primary agent
        return self.primary_agent

    async def execute(
        self,
        input_text: str,
        multimodal_paths: Optional[dict] = None,
        stream_callback=None,
    ) -> str:
        """Execute the planning flow with heuristic planning loop."""
        try:
            if not self.primary_agent:
                raise ValueError("No primary agent available")

            # Create initial plan if input provided
            if not input_text:
                return "Cannot create plan for empty input"

            # ---------------- Guardrails: user-level flow input ----------------
            guard_settings = getattr(config, "guardrail", None)
            if (
                guard_settings
                and getattr(guard_settings, "enabled", False)
                and getattr(
                    getattr(guard_settings, "flow_input", None), "enabled", False
                )
            ):
                in_res = await GuardrailEngine.enforce(
                    boundary=GuardrailBoundary.FLOW_INPUT,
                    text=input_text,
                    guardrails=getattr(guard_settings.flow_input, "guards", []),
                    enabled=True,
                    policy=[GuardrailAction.REDACT, GuardrailAction.BLOCK],
                    max_retries=0,
                    guardrail_agent=GuardrailAgent(llm=self.primary_agent.llm),
                    flow_type="planning",
                )
                if in_res.blocked:
                    return in_res.text
                input_text = in_res.text

            # Initialize plan (metadata + placeholder step) if needed
            if input_text and self.active_plan_id not in self.planning_tool.plans:
                # 创建workspace下的工作目录
                workspace_dir = config.workspace_root / self.active_plan_id
                workspace_dir.mkdir(parents=True, exist_ok=True)
                await self._init_heuristic_plan(origin_request=input_text)

            # Heuristic planning loop
            origin_request = input_text
            last_output = ""
            for iter_idx in range(1, int(self.max_loops) + 1):
                decision = await self._heuristic_planner_decide(
                    origin_request=origin_request,
                    last_output=last_output,
                    iter_idx=iter_idx,
                )

                # end=true (or missing): directly return final answer (reason)
                if decision.get("end", True):
                    final_answer = str(decision.get("reason", "") or "").strip()
                    note = {
                        "iter": iter_idx,
                        "planner": decision,
                        "context": self._build_executor_context(
                            origin_request=origin_request,
                            last_output=last_output,
                            objective=str(decision.get("reason", "") or ""),
                        ),
                        "agent_name": str(decision.get("agent", "") or ""),
                        "step_output": final_answer,
                    }
                    await self._append_step_and_write_note(
                        step_text=f"[HEURISTIC] final iter={iter_idx}",
                        note_obj=note,
                        step_status=PlanStepStatus.COMPLETED.value,
                    )
                    return await self._apply_flow_output_guardrail(final_answer)

                # end=false: select agent strictly and execute
                agent_key = decision.get("agent")
                if not agent_key or agent_key not in self.agents:
                    # This should be prevented by _heuristic_planner_decide; treat as blocked.
                    raise ValueError(f"Invalid agent selected by planner: {agent_key}")

                executor = self.get_executor(str(agent_key))
                executor.memory.add_messages(self.memory.get_recent_messages(1))
                objective = str(decision.get("reason", "") or "").strip()
                context = self._build_executor_context(
                    origin_request=origin_request,
                    last_output=last_output,
                    objective=objective,
                )
                request = (
                    f"请完成当前目标：{objective}" if objective else "请继续完成任务。"
                )
                step_output = await executor.run(
                    request=request,
                    context=context,
                    multimodal_paths=multimodal_paths,
                    stream_callback=stream_callback,
                )

                note = {
                    "iter": iter_idx,
                    "planner": decision,
                    "context": context,
                    "agent_name": str(agent_key),
                    "step_output": step_output,
                }
                await self._append_step_and_write_note(
                    step_text=f"[HEURISTIC] iter={iter_idx} agent={agent_key}",
                    note_obj=note,
                    step_status=PlanStepStatus.COMPLETED.value,
                )

                # 判断ask_human
                if step_output and "INTERACTION_REQUIRED:" in str(step_output):
                    return str(step_output)

                last_output = str(step_output or "")

            # Reached max loops without end=true
            progress = self._generate_plan_text_from_storage()
            err_text = (
                f"未收敛：已达到最大循环次数 {self.max_loops}，仍未得到 end=true。\n"
                f"PlanID: {self.active_plan_id}\n\n当前进度：\n{progress}"
            )
            return err_text
        except Exception as e:
            # 按需求：token 超限时直接抛出，让 server 标记 flow 失败并提示用户重新开启会话
            if isinstance(e, TokenLimitExceeded):
                raise
            logger.error(f"Error in PlanningFlow: {str(e)}")
            return f"Execution failed: {str(e)}"

    async def _apply_flow_output_guardrail(self, text: str) -> str:
        """Apply flow output guardrails (if enabled) and return processed text."""
        guard_settings = getattr(config, "guardrail", None)
        if (
            guard_settings
            and getattr(guard_settings, "enabled", False)
            and getattr(getattr(guard_settings, "flow_output", None), "enabled", False)
            and text
        ):
            out_res = await GuardrailEngine.enforce(
                boundary=GuardrailBoundary.FLOW_OUTPUT,
                text=text,
                guardrails=getattr(guard_settings.flow_output, "guards", []),
                enabled=True,
                policy=[
                    GuardrailAction.REDACT,
                    GuardrailAction.RETRY,
                    GuardrailAction.BLOCK,
                ],
                max_retries=int(
                    getattr(getattr(guard_settings, "retry", None), "max_attempts", 0)
                    or 0
                ),
                guardrail_agent=GuardrailAgent(llm=self.primary_agent.llm),
                flow_type="planning",
            )
            return out_res.text
        return text

    async def _init_heuristic_plan(self, origin_request: str) -> None:
        """Initialize a heuristic plan with metadata and a placeholder step."""
        title = f"Heuristic plan: {origin_request[:50]}{'...' if len(origin_request) > 50 else ''}"
        await self.planning_tool.execute(
            command="create",
            plan_id=self.active_plan_id,
            title=title,
            request=origin_request,
            steps=["[HEURISTIC] init"],
        )
        # Mark placeholder step completed and store metadata note
        meta_note = {
            "iter": 0,
            "planner": {"end": False, "agent": "", "reason": "init plan metadata"},
            "context": "",
            "agent_name": "planner",
            "step_output": "",
        }
        await self.planning_tool.execute(
            command="mark_step",
            plan_id=self.active_plan_id,
            step_index=0,
            step_status=PlanStepStatus.COMPLETED.value,
            step_notes=json.dumps(meta_note, ensure_ascii=False),
        )

    def _get_agents_description(self) -> str:
        agents_description = ""
        for key in self.executor_keys:
            if key in self.agents:
                agents_description += f"- {key}: {self.agents[key].description}\n"
        return agents_description.strip()

    async def _heuristic_planner_decide(
        self, *, origin_request: str, last_output: str, iter_idx: int
    ) -> Dict[str, Any]:
        """
        Ask LLM to output a strict JSON decision: {end: bool, agent: str, reason: str}.
        Retries on parse errors or invalid agent selection up to planner_parse_retries.
        """
        agents_info = self._get_agents_description()
        system_msg = Message.system_message(
            planning_flow.HEURISTIC_PLANNING_SYSTEM_PROMPT
        )

        last_error: Optional[str] = None
        for attempt in range(int(self.planner_parse_retries) + 1):
            user_prompt = planning_flow.HEURISTIC_PLANNING_USER_PROMPT.format(
                request=origin_request,
                last_output=last_output or "",
                agents_info=agents_info,
            )
            if attempt > 0 and last_error:
                user_prompt += (
                    "\n\n### 注意\n"
                    f"上一次输出不符合要求：{last_error}\n"
                    "请严格只输出单个 JSON 对象。"
                )

            raw = await self.llm.ask(
                messages=[Message.user_message(user_prompt)],
                system_msgs=[system_msg],
            )
            try:
                decision = self._parse_planner_json(raw)
                decision["raw"] = raw

                # Validate schema
                if "end" not in decision:
                    decision["end"] = True
                if not isinstance(decision.get("end"), bool):
                    raise ValueError("field `end` must be boolean")
                if not isinstance(decision.get("reason"), str):
                    raise ValueError("field `reason` must be string")
                if decision["end"] is False:
                    agent = decision.get("agent")
                    if not isinstance(agent, str) or not agent.strip():
                        raise ValueError(
                            "field `agent` must be non-empty string when end=false"
                        )
                    if agent not in self.agents:
                        raise ValueError(
                            f"selected agent '{agent}' not found; must exactly match an available executor key"
                        )
                else:
                    # end=true: allow empty agent
                    if "agent" not in decision:
                        decision["agent"] = ""
                return decision
            except Exception as e:
                last_error = str(e)
                logger.warning(
                    f"Heuristic planner output invalid (attempt {attempt + 1}/{int(self.planner_parse_retries) + 1}): {last_error}"
                )

        # Exceeded retries
        progress = self._generate_plan_text_from_storage()
        raise ValueError(
            "Planner 输出无法解析或 agent 不合法，已超过重试次数。"
            f"\nLastError: {last_error}\n\n当前进度：\n{progress}"
        )

    def _parse_planner_json(self, raw: Optional[str]) -> Dict[str, Any]:
        """Parse planner output into JSON dict, tolerating accidental code fences."""
        if raw is None:
            raise ValueError("planner returned empty response")
        text = str(raw).strip()
        if not text:
            raise ValueError("planner returned empty response")

        # Strip accidental markdown code fences
        if text.startswith("```"):
            parts = text.split("```")
            # try to find the largest non-empty segment
            candidates = [
                p.strip() for p in parts if p.strip() and "{" in p and "}" in p
            ]
            if candidates:
                text = candidates[0]

        # Try direct json
        try:
            obj = json.loads(text)
            if not isinstance(obj, dict):
                raise ValueError("planner JSON must be an object")
            return obj
        except json.JSONDecodeError as e:
            logger.debug(
                "Planner JSON direct parse failed; will try extracting JSON object substring. "
                f"error={e}; text_preview={text[:300]!r}"
            )

        # Try to extract first JSON object substring
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            obj_text = text[start : end + 1]
            obj = json.loads(obj_text)
            if not isinstance(obj, dict):
                raise ValueError("planner JSON must be an object")
            return obj

        raise ValueError("planner output is not valid JSON object")

    def _build_executor_context(
        self, *, origin_request: str, last_output: str, objective: str
    ) -> str:
        """Build per-step context combining origin request + last output + current objective."""
        parts = [
            "### Task",
            origin_request.strip(),
            "",
            "### CurrentObjective",
            (objective or "").strip(),
        ]
        if last_output:
            parts += ["", "### PreviousOutput", last_output.strip()]
        parts += [
            "",
            "### Constraints",
            "- 只专注当前目标，不要偏离。",
            "",
            "### OutputRequirements",
            "- 直接给出可用的结果或下一步产出，不要输出无关内容。",
        ]
        return "\n".join(parts).strip()

    async def _append_step_and_write_note(
        self, *, step_text: str, note_obj: Dict[str, Any], step_status: str
    ) -> None:
        """Append a new step to plan and write JSON note, then mark status."""
        if self.active_plan_id not in self.planning_tool.plans:
            raise ValueError(f"Plan with ID {self.active_plan_id} not found")

        plan_data = self.planning_tool.plans[self.active_plan_id]
        steps = list(plan_data.get("steps", []) or [])
        steps.append(step_text)
        await self.planning_tool.execute(
            command="update", plan_id=self.active_plan_id, steps=steps
        )
        step_index = len(steps) - 1
        await self.planning_tool.execute(
            command="mark_step",
            plan_id=self.active_plan_id,
            step_index=step_index,
            step_status=step_status,
            step_notes=json.dumps(note_obj, ensure_ascii=False),
        )

    async def _create_initial_plan(
        self, request: str, multimodal_paths: Optional[dict] = None
    ) -> None:
        """Create an initial plan based on the request using the flow's LLM and PlanningTool."""
        logger.info(f"Creating initial plan with ID: {self.active_plan_id}")

        agents_description = ""
        for key in self.executor_keys:
            if key in self.agents:
                agents_description += (
                    f"- {key.upper()}: {self.agents[key].description}\n"
                )
        system_message_content = planning_flow.PLANNING_SYSTEM_PROMPT
        # logger.info(f"_create_initial_plan prompt:\n{system_message_content}")
        # Create a system message for plan creation
        system_message = Message.system_message(system_message_content)

        # if multimodal_paths:
        #     request_details = await self.llm.ask(
        #         messages=[
        #             Message.user_message(
        #                 "请描述输入文件内容", multimodal_paths=multimodal_paths
        #             )
        #         ],
        #     )
        #     request = f"{request}.问题细节：{request_details}"
        # Create a user message with the request
        user_message = Message.user_message(
            planning_flow.PLANNING_USER_PROMPT.format(
                request=request,
                agents_info=agents_description,
                agents_len=len(self.executor_keys),
            ),
            multimodal_paths=multimodal_paths,
        )
        self.memory.add_message(user_message)

        # Call LLM with PlanningTool
        response = await self.llm.ask_tool(
            messages=[user_message],
            system_msgs=[system_message],
            tools=[self.planning_tool.to_param()],
            tool_choice={"type": "function", "function": {"name": "planning"}},
        )

        # print(f"create initial plan prompt: {system_message},/n {user_message},/nresponse: {response}")
        # Process tool calls if present
        max_retries = 3
        retry_count = 0

        while retry_count < max_retries:
            if response.tool_calls:
                print(
                    f"Tool calls: {[call.function.name for call in response.tool_calls]}"
                )
                for tool_call in response.tool_calls:
                    if tool_call.function.name == "planning":
                        # Parse the arguments
                        args = tool_call.function.arguments
                        if isinstance(args, str):
                            try:
                                args = json.loads(args)
                            except json.JSONDecodeError:
                                logger.error(f"Failed to parse tool arguments: {args}")
                                continue

                            # Ensure plan_id is set correctly and execute the tool
                            args["plan_id"] = self.active_plan_id

                            # Execute the tool via ToolCollection instead of directly
                            result = await self.planning_tool.execute(**args)

                        logger.info(
                            f"Plan creation result: {self._format_plan(result.output)}"
                        )
                        return
            else:
                logger.warning(
                    f"No tool calls returned, retrying... (attempt {retry_count + 1}/{max_retries})"
                )
                response = await self.llm.ask_tool(
                    messages=[user_message],
                    system_msgs=[system_message],
                    tools=[self.planning_tool.to_param()],
                    tool_choice={"type": "function", "function": {"name": "planning"}},
                )
                retry_count += 1

        # # If execution reached here, create a default plan
        # logger.warning("Creating default plan")

        # # Create default plan using the ToolCollection
        # await self.planning_tool.execute(
        #     **{
        #         "command": "create",
        #         "plan_id": self.active_plan_id,
        #         "title": f"Plan for: {request[:50]}{'...' if len(request) > 50 else ''}",
        #         "steps": ["Analyze request", "Execute task", "Verify results"],
        #         "request": request,
        #     }
        # )

    async def _get_current_step_info(self) -> tuple[Optional[int], Optional[dict]]:
        """
        Parse the current plan to identify the first non-completed step's index and info.
        Returns (None, None) if no active step is found.
        """
        if (
            not self.active_plan_id
            or self.active_plan_id not in self.planning_tool.plans
        ):
            logger.error(f"Plan with ID {self.active_plan_id} not found")
            return None, None

        try:
            # Direct access to plan data from planning tool storage
            plan_data = self.planning_tool.plans[self.active_plan_id]
            steps = plan_data.get("steps", [])
            step_statuses = plan_data.get("step_statuses", [])

            # Find first non-completed step
            for i, step in enumerate(steps):
                if i >= len(step_statuses):
                    status = PlanStepStatus.NOT_STARTED.value
                else:
                    status = step_statuses[i]

                if status in PlanStepStatus.get_active_statuses():
                    # Extract step type/category if available
                    step_info = {"text": step}

                    # Try to extract step type from the text (e.g., [SEARCH] or [CODE])
                    import re

                    # 匹配方括号中的类型标记,支持中文描述后的类型标记
                    type_match = re.search(r".*\[([A-Z_]+)\]", step)
                    if type_match:
                        step_info["type"] = type_match.group(1).lower()

                    # Mark current step as in_progress
                    try:
                        await self.planning_tool.execute(
                            command="mark_step",
                            plan_id=self.active_plan_id,
                            step_index=i,
                            step_status=PlanStepStatus.IN_PROGRESS.value,
                        )
                    except Exception as e:
                        logger.warning(f"Error marking step as in_progress: {e}")
                        # Update step status directly if needed
                        if i < len(step_statuses):
                            step_statuses[i] = PlanStepStatus.IN_PROGRESS.value
                        else:
                            while len(step_statuses) < i:
                                step_statuses.append(PlanStepStatus.NOT_STARTED.value)
                            step_statuses.append(PlanStepStatus.IN_PROGRESS.value)

                        plan_data["step_statuses"] = step_statuses
                    return i, step_info

            return None, None  # No active step found

        except Exception as e:
            logger.warning(f"Error finding current step index: {e}")
            return None, None

    async def _execute_step(
        self,
        executor: BaseAgent,
        precede_step_result: str,
        multimodal_paths: Optional[dict] = None,
        stream_callback=None,
    ) -> str:
        """Execute the current step with the specified agent using agent.run()."""

        plan_step = await self._get_plan_step()
        plan = await self._get_plan()
        # Create a prompt for the agent to execute the current step
        step_prompt = self._format_plan_step(plan)
        logger.info(f"Step prompt: {step_prompt}, context: {precede_step_result}")

        # Use agent.run() to execute the step
        try:
            logger.info(f"Start executing step:{plan_step}")
            executor.state = AgentState.IDLE
            if executor.support_multimodal_input:
                results = await executor.run(
                    request=step_prompt,
                    context=precede_step_result,
                    multimodal_paths=multimodal_paths,
                    # stream_callback=stream_callback,
                )
            else:
                results = await executor.run(
                    request=step_prompt, context=precede_step_result
                )

            # Mark the step as completed after successful execution
            # 判断是否式因为交互而暂停的
            if results and "INTERACTION_REQUIRED:" not in results:
                await self._mark_step_completed()
                await executor.cleanup()
                logger.info(f"Finish executing step:{plan_step}")

            return results
        except Exception as e:
            if isinstance(e, TokenLimitExceeded):
                raise
            logger.error(f"Error executing step {self.current_step_index}: {e}")
            return f"Error executing step {self.current_step_index}: {str(e)}"

    async def __update_current_step_result(self, step_result: str) -> str:
        """Update the result of the current step."""
        plan_data = self.planning_tool.plans[self.active_plan_id]
        plan_data["step_notes"][self.current_step_index] = step_result

    async def __get_precede_step_result(self, step_index: int) -> str:
        """Get the result of the previous step."""
        logger.info(f"Get the result of the previous step: {step_index}")
        plan_data = self.planning_tool.plans[self.active_plan_id]
        result = ""
        for i, (step, status, notes) in enumerate(
            zip(plan_data["steps"], plan_data["step_statuses"], plan_data["step_notes"])
        ):
            if i <= step_index:
                result += f"{step}: {status}: {notes}\n"
        return result

    async def _mark_step_completed(self) -> None:
        """Mark the current step as completed."""
        if self.current_step_index is None:
            return

        try:
            # Mark the step as completed
            await self.planning_tool.execute(
                command="mark_step",
                plan_id=self.active_plan_id,
                step_index=self.current_step_index,
                step_status=PlanStepStatus.COMPLETED.value,
            )
            logger.info(
                f"Marked step {self.current_step_index} as completed in plan {self.active_plan_id}"
            )
        except Exception as e:
            logger.warning(f"Failed to update plan status: {e}")
            # Update step status directly in planning tool storage
            if self.active_plan_id in self.planning_tool.plans:
                plan_data = self.planning_tool.plans[self.active_plan_id]
                step_statuses = plan_data.get("step_statuses", [])

                # Ensure the step_statuses list is long enough
                while len(step_statuses) <= self.current_step_index:
                    step_statuses.append(PlanStepStatus.NOT_STARTED.value)

                # Update the status
                step_statuses[self.current_step_index] = PlanStepStatus.COMPLETED.value
                plan_data["step_statuses"] = step_statuses

    async def _get_plan_step(self) -> str:
        """Get the current step for the plan as formatted text."""
        try:
            result = await self.planning_tool.execute(
                command="get_step", plan_id=self.active_plan_id
            )
            return result.output if hasattr(result, "output") else str(result)
        except Exception as e:
            logger.error(f"Error getting plan step: {e}")
            return ""

    async def _get_plan(self) -> dict:
        """Get the current plan as formatted text."""
        try:
            result = await self.planning_tool.execute(
                command="get", plan_id=self.active_plan_id
            )
            return result.output if hasattr(result, "output") else None
        except Exception as e:
            logger.error(f"Error getting plan: {e}")
            return None

    def _generate_plan_text_from_storage(self) -> str:
        """Generate plan text directly from storage if the planning tool fails."""
        try:
            if self.active_plan_id not in self.planning_tool.plans:
                return f"Error: Plan with ID {self.active_plan_id} not found"

            plan_data = self.planning_tool.plans[self.active_plan_id]
            title = plan_data.get("title", "Untitled Plan")
            steps = plan_data.get("steps", [])
            step_statuses = plan_data.get("step_statuses", [])
            step_notes = plan_data.get("step_notes", [])

            # Ensure step_statuses and step_notes match the number of steps
            while len(step_statuses) < len(steps):
                step_statuses.append(PlanStepStatus.NOT_STARTED.value)
            while len(step_notes) < len(steps):
                step_notes.append("")

            # Count steps by status
            status_counts = {status: 0 for status in PlanStepStatus.get_all_statuses()}

            for status in step_statuses:
                if status in status_counts:
                    status_counts[status] += 1

            completed = status_counts[PlanStepStatus.COMPLETED.value]
            total = len(steps)
            progress = (completed / total) * 100 if total > 0 else 0

            plan_text = f"Plan: {title} (ID: {self.active_plan_id})\n"
            plan_text += "=" * len(plan_text) + "\n\n"

            plan_text += (
                f"Progress: {completed}/{total} steps completed ({progress:.1f}%)\n"
            )
            plan_text += f"Status: {status_counts[PlanStepStatus.COMPLETED.value]} completed, {status_counts[PlanStepStatus.IN_PROGRESS.value]} in progress, "
            plan_text += f"{status_counts[PlanStepStatus.BLOCKED.value]} blocked, {status_counts[PlanStepStatus.NOT_STARTED.value]} not started\n\n"
            plan_text += "Steps:\n"

            status_marks = PlanStepStatus.get_status_marks()

            for i, (step, status, notes) in enumerate(
                zip(steps, step_statuses, step_notes)
            ):
                # Use status marks to indicate step status
                status_mark = status_marks.get(
                    status, status_marks[PlanStepStatus.NOT_STARTED.value]
                )

                plan_text += f"{i}. {status_mark} {step}\n"
                if notes:
                    plan_text += f"   Notes: {notes}\n"

            return plan_text
        except Exception as e:
            logger.error(f"Error generating plan text from storage: {e}")
            return f"Error: Unable to retrieve plan with ID {self.active_plan_id}"

    async def _get_plan_text(self) -> str:
        """Get the plan text representation."""
        try:
            # 首先尝试从存储中获取计划文本
            return self._generate_plan_text_from_storage()
        except Exception as e:
            logger.error(f"Error getting plan text: {e}")
            return f"Error: Unable to retrieve plan with ID {self.active_plan_id}"

    async def _finalize_plan(
        self, multimodal_paths: Optional[dict] = None, stream_callback=None
    ) -> str:
        """Finalize the plan and provide a summary using the flow's LLM directly."""
        plan_text = await self._get_plan_text()

        try:
            summary_prompt = planning_flow.FINALIZE_STEP_PROMPT.format(
                workspace=config.workspace_root / self.active_plan_id,
                plan_text=plan_text,
            )
            return await self.primary_agent.run(
                summary_prompt,
                multimodal_paths=multimodal_paths,
                stream_callback=stream_callback,
            )
        except Exception as e2:
            logger.error(f"Error finalizing plan with agent: {e2}")
            return "Plan completed. Error generating summary."

    def _format_plan_step(self, plan: Dict):
        """Format a todo step of the plan for display."""
        # output = f"Plan: {plan['title']} (ID: {plan['plan_id']})\n"
        output = ""
        # output += "=" * len(output) + "\n\n"

        for i, (step, status, notes) in enumerate(
            zip(plan["steps"], plan["step_statuses"], plan["step_notes"])
        ):
            if status == "not_started" or status == "in_progress":
                output = f"There is a plan flow containing a couple of steps to finish the request about '{plan['request']}'. Now you are on the step about '{step}', and only concentrate on it. "
                # output = f"you are working on '{step}' to finish the request about '{plan['request']}', consider how to execute '{step}' exactly."
                break

        return output

    def _format_plan(self, plan: Dict) -> str:
        """Format a plan for display."""
        output = f"Plan: {plan['title']} (ID: {plan['plan_id']})\n"
        output += f"Original request: {plan['request']}\n"
        output += "=" * len(output) + "\n\n"

        # Calculate progress statistics
        total_steps = len(plan["steps"])
        completed = sum(1 for status in plan["step_statuses"] if status == "completed")
        in_progress = sum(
            1 for status in plan["step_statuses"] if status == "in_progress"
        )
        blocked = sum(1 for status in plan["step_statuses"] if status == "blocked")
        not_started = sum(
            1 for status in plan["step_statuses"] if status == "not_started"
        )

        output += f"Progress: {completed}/{total_steps} steps completed "
        if total_steps > 0:
            percentage = (completed / total_steps) * 100
            output += f"({percentage:.1f}%)\n"
        else:
            output += "(0%)\n"

        output += f"Status: {completed} completed, {in_progress} in progress, {blocked} blocked, {not_started} not started\n\n"
        output += "Steps:\n"

        # Add each step with its status and notes
        for i, (step, status, notes) in enumerate(
            zip(plan["steps"], plan["step_statuses"], plan["step_notes"])
        ):
            status_symbol = {
                "not_started": "[ ]",
                "in_progress": "[→]",
                "completed": "[✓]",
                "blocked": "[!]",
            }.get(status, "[ ]")

            output += f"{i}. {status_symbol} {step}\n"
            if notes:
                output += f"   Notes: {notes}\n"

        return output
