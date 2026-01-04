import json
import time
from enum import Enum
from typing import Dict, List, Optional, Union

from pydantic import Field

from app.agent.base import BaseAgent
from app.config import config
from app.flow.base import BaseFlow
from app.llm import LLM
from app.logger import logger
from app.prompt import planning_flow
from app.schema import AgentState, Memory, Message, ToolChoice
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
        """Execute the planning flow with agents."""
        try:
            if not self.primary_agent:
                raise ValueError("No primary agent available")

            precede_step_result = ""
            # Create initial plan if input provided
            if not input_text:
                return f"Cannot create plan for empty input"

            if input_text and self.active_plan_id not in self.planning_tool.plans:
                # 创建workspace下的工作目录
                workspace_dir = config.workspace_root / self.active_plan_id
                workspace_dir.mkdir(parents=True, exist_ok=True)

                await self._create_initial_plan(input_text, multimodal_paths)

                # Verify plan was created successfully
                if self.active_plan_id not in self.planning_tool.plans:
                    logger.error(
                        f"Plan creation failed. Plan ID {self.active_plan_id} not found in planning tool."
                    )
                    return f"Failed to create plan for: {input_text}"
            else:
                precede_step_result = await self.__get_precede_step_result(
                    self.current_step_index
                )
                precede_step_result += input_text

            while True:
                # Get current step to execute
                self.current_step_index, step_info = await self._get_current_step_info()

                # Exit if no more steps or plan completed
                if self.current_step_index is None:
                    summary_result = await self._finalize_plan(
                        multimodal_paths=multimodal_paths,
                        stream_callback=stream_callback,
                    )
                    logger.info(f"Flow summary result: {summary_result}")
                    return summary_result

                # logger.info(f"Start executing step: {step_info['text']}")
                # Execute current step with appropriate agent
                step_type = step_info.get("type") if step_info else None
                logger.debug(f"Step type: {step_type}")
                executor = self.get_executor(step_type)
                executor.memory.add_messages(self.memory.get_recent_messages(1))
                step_result = await self._execute_step(
                    executor,
                    precede_step_result,
                    multimodal_paths=multimodal_paths,
                    stream_callback=stream_callback,
                )
                logger.info(f"Step result: {step_result}")
                await self.__update_current_step_result(step_result)

                # 判断ask_human
                if step_result and "INTERACTION_REQUIRED:" in step_result:
                    return step_result

                precede_step_result = await self.__get_precede_step_result(
                    self.current_step_index
                )
                # logger.info(
                #     f"Finish executing step {self.current_step_index}: {step_info['text']}"
                # )
        except Exception as e:
            logger.error(f"Error in PlanningFlow: {str(e)}")
            return f"Execution failed: {str(e)}"

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
        executor.set_prompt(
            {
                "context": precede_step_result,
                "request": step_prompt,
                "directory": config.workspace_root / self.active_plan_id,
            }
        )
        logger.info(f"Step prompt: {step_prompt}, context: {precede_step_result}")

        # Use agent.run() to execute the step
        try:
            logger.info(f"Start executing step:{plan_step}")
            executor.state = AgentState.IDLE
            if executor.support_multimodal_input:
                results = await executor.run(
                    multimodal_paths=multimodal_paths,
                    # stream_callback=stream_callback,
                )
            else:
                results = await executor.run(step_prompt)

            # Mark the step as completed after successful execution
            # 判断是否式因为交互而暂停的
            if results and "INTERACTION_REQUIRED:" not in results:
                await self._mark_step_completed()
                await executor.cleanup()
                logger.info(f"Finish executing step:{plan_step}")

            return results
        except Exception as e:
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
