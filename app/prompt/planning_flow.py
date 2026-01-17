PLANNING_SYSTEM_PROMPT = """
You are a planning assistant, who is good at creating a concise, actionable plan with clear steps.
"""

PLANNING_USER_PROMPT = """
Based on the request "{request}", create an actionable plan.

### Available executors
{agents_info}

🚨 MANDATORY REQUIREMENTS - NO EXCEPTIONS:
- breaking it down into multiple independent steps that will help achieve the final goal.
- Each step must clearly specify one executor from 'Available executors', and only one executor, eg: Gather relevant materials [Flow].
- Each step should be written in the same language as the '8月中旬我想去澳门旅游5天，帮我做一个攻略，包括旅游景点和入住酒店。入住的酒店我希望交通便利，舒适，最好是星级酒店。'.
"""

# PLANNING_USER_PROMPT = """
# Based on the request "{request}", create an actionable plan. If the request is a multimodal request, you should describe the multimodal input details first, Then describe the plan.

# ### Available executors
# {agents_info}

# 🚨 MANDATORY REQUIREMENTS - NO EXCEPTIONS:
# - breaking it down into multiple independent steps that will help achieve the final goal.
# - Each step must clearly specify the executor from 'Available executors', eg: Gather relevant materials [Flow].
# - Each step should be written in the same language as the '8月中旬我想去澳门旅游5天，帮我做一个攻略，包括旅游景点和入住酒店。入住的酒店我希望交通便利，舒适，最好是星级酒店。'.
# """

STEP_EXECUTE_PROMPT = """
{plan_step}
"""

FINALIZE_STEP_PROMPT = """
You are a planning assistant, your task is to summarize the completed plan(The output should be semantic fluency and concise, don't output iirelevant text). You can refer to the files in the {workspace} to summarize.
Here is the final plan status:
{plan_text}
"""

# =========================
# Heuristic planning prompts
# =========================

HEURISTIC_PLANNING_SYSTEM_PROMPT = """
You are a heuristic planning router.
Your job is to decide whether the user's request can be answered directly, or which executor agent should be used next.
You MUST output a single valid JSON object ONLY (no markdown, no code fences, no extra text).
"""

HEURISTIC_PLANNING_USER_PROMPT = """
You will perform one iteration of heuristic planning.

### OriginalRequest
{request}

### PreviousOutput (may be empty)
{last_output}

### Available executors
{agents_info}

### Output requirements (STRICT)
- Output MUST be a single JSON object only, with keys: end, agent, reason
- end: boolean. If true, you must provide the final answer in reason, and agent can be an empty string.
- agent: string. Required when end is false. Must exactly match one executor key from "Available executors".
- reason: string. Keep it short. When end is false, state the current objective and why this agent is chosen.
- Do NOT output any extra keys.
- Do NOT wrap JSON in markdown or code fences.
""".strip()
