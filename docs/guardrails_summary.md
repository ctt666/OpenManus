# Agent/Flow 护栏（Guardrails）设计与代码变更总结

## 背景与目标
本次为 **Agent** 与 **Flow** 的 **用户层输入/输出** 增加护栏（guardrails），满足：

- **可扩展**：支持内置 guard + 外部自定义 guard（字符串 id / dotted path / callable）。
- **可配置启停**：支持全局开关、按边界开关、按边界配置启用的 guard 列表。
- **策略可控**：支持 `redact`（脱敏/确定性改写）、`retry`（LLM 修复）、`block`（阻断）。
- **最小侵入**：只在用户层 I/O 接入（不覆盖 tool I/O、不覆盖 flow step I/O）。

> 决策：
> - 配置解析失败采用 **fail-closed**（拒绝启动）。
> - `retry` 修复输出必须 **跟随当前 agent/flow 的 LLM config**。

## 需求边界（本期覆盖范围）
### 覆盖（用户层 I/O）
- **Agent 输入**：`BaseAgent.run(request, context, multimodal_paths)` 的 `request/context`
- **Agent 输出**：`BaseAgent.run()` 的最终返回值（`summary`）
- **Flow 输入**：`PlanningFlow.execute(input_text, ...)` 的 `input_text`
- **Flow 输出**：`PlanningFlow.execute()` 的最终返回值（`summary_result`）

### 不覆盖（明确不做）
- Flow 中间步骤 `step_prompt/step_result`
- 工具层 `tool_call` 参数（LLM → tool）与 `tool_result`（tool → LLM/用户）

## 核心设计
### 1) 统一概念：边界、动作与上下文
文件：`app/agent/guardrail.py`

- **边界（GuardrailBoundary）**
  - `agent_input` / `agent_output`
  - `flow_input` / `flow_output`
- **动作（GuardrailAction）**
  - `pass` / `redact` / `retry` / `block`
- **上下文（GuardrailContext）**
  - `boundary`, `raw_text`
  - `agent_name`, `flow_type`, `request_id`, `metadata`（为审计/扩展预留）

### 2) 插件接口（可扩展 Guardrail）
文件：`app/agent/guardrail.py`

- `Guardrail`（基类）
  - `id: str`
  - `applies_to: set[GuardrailBoundary]`（可选：为空表示不过滤边界）
  - `check(ctx) -> GuardrailCheckResult`（必选）
  - `redact(ctx) -> Optional[str]`（可选：用于确定性脱敏/改写）

- `CallableGuardrail`
  - 将 `callable` 适配为 guard，支持 `(ctx)->...` 或 `(text)->...` 两种形态。

### 3) 可插拔解析与注册
文件：`app/agent/guardrail.py`

- 内置 registry：`BUILTIN_GUARDRAILS`
  - `secrets` / `pii` / `prompt_injection`
- 动态加载：
  - 支持 `"module:ClassName"` 或 `"module.attr"` 形式
  - 若加载对象是 `Guardrail` 子类则实例化；若是 callable 则包装为 `CallableGuardrail`

### 4) 执行引擎（GuardrailEngine.enforce）
文件：`app/agent/guardrail.py`

`GuardrailEngine.enforce(...)` 负责：
- 根据边界过滤 guard
- 执行校验：任何 guard 失败即进入策略处理
- 按策略顺序执行动作（policy）：
  - `REDACT`：依次调用各 guard 的 `redact`（仅对当前失败 guard 尝试）
  - `RETRY`：调用 `GuardrailAgent.repair_text()`（LLM 修复），受 `max_retries` 限制
  - `BLOCK`：直接返回阻断文案（避免泄露规则细节）

返回结构：`GuardrailEnforceResult(blocked, text, action_taken, reasons, tags, retries)`

### 5) Retry（LLM 修复）与 LLM 跟随策略
文件：`app/agent/guardrail.py`

- `GuardrailAgent.repair_text()` 会用极短 prompt 进行“安全改写”，不写入业务 memory。

**LLM 跟随决策的落地：**
- Agent：在 `BaseAgent.initialize_agent()` 中将 `self.guardrail_agent.llm = self.llm`
  - 文件：`app/agent/base.py`
- Flow：在 `PlanningFlow.execute()` 中创建 `GuardrailAgent(llm=self.primary_agent.llm)`
  - 文件：`app/flow/planning.py`

## 策略（Policy）与行为约束
### 输入（agent_input / flow_input）
- 默认策略：`redact -> block`
- 说明：输入不做 `retry`，避免“LLM 自动改写用户输入”带来的语义漂移。

### 输出（agent_output / flow_output）
- 默认策略：`redact -> retry -> block`
- `retry` 次数由配置 `guardrail.retry.max_attempts` 与 agent 实例字段 `guardrail_retry` 合并决定（取更大值）。

### 交互结果（INTERACTION_REQUIRED）
为避免破坏交互协议：
- Agent 与 Flow 对该类输出仅执行 `redact/block`，不执行 `retry`。

## 配置模型（config.toml）
文件：`app/config.py`

新增配置结构 `GuardrailSettings`，可通过 `config.guardrail` 访问：
- `enabled: bool`（全局开关）
- `agent_input/agent_output/flow_input/flow_output`
  - 每个边界都有 `enabled: bool` 与 `guards: [str]`
- `retry.max_attempts: int`

### 配置示例
在 `config/config.toml` 中添加（示例）：

```toml
[guardrail]
enabled = true

[guardrail.agent_input]
enabled = true
guards = ["prompt_injection"]

[guardrail.agent_output]
enabled = true
guards = ["pii", "secrets"]

[guardrail.flow_input]
enabled = true
guards = ["prompt_injection"]

[guardrail.flow_output]
enabled = true
guards = ["pii", "secrets"]

[guardrail.retry]
max_attempts = 3
```

### fail-closed（重要）
若 `guardrail` 配置段解析失败，`app/config.py` 会抛出异常并拒绝启动（避免“配置坏了导致护栏悄悄失效”）。

## 代码接入点（重要逻辑定位）
### Agent：`BaseAgent.run()`
文件：`app/agent/base.py`

- run 入口：对 `request/context` 做 `agent_input`
- run 出口：对 `summary` 做 `agent_output`
- 若 `act()` 返回 `INTERACTION_REQUIRED:`：仅做最小护栏处理后直接返回

### Flow：`PlanningFlow.execute()`
文件：`app/flow/planning.py`

- execute 入口：对 `input_text` 做 `flow_input`
- Flow 完成返回前：对 `summary_result` 做 `flow_output`
- 若 step_result 返回 `INTERACTION_REQUIRED:`：仅做最小护栏处理后返回

## 内置 Guard 说明
文件：`app/agent/guardrail.py`

- **`secrets`**
  - 检测：`sk-...`、`ghp_...`、`AKIA...` 等模式
  - 输出侧默认可 `redact`；也可 `block`
- **`pii`**
  - 检测：邮箱、国内手机号、18 位身份证（含 X）
  - 输出侧默认 `redact`
- **`prompt_injection`**
  - 输入侧关键词/短语规则（轻量检测）
  - 默认 `block`

> 注：这些内置规则偏保守（减少误杀），但仍需结合业务进一步调优（例如更全面的 secret pattern、更强的 injection 检测等）。

## 变更文件清单
- 新增/实现：`app/agent/guardrail.py`
- 修改：`app/agent/base.py`（Agent I/O 接入 + LLM 跟随）
- 修改：`app/flow/planning.py`（Flow I/O 接入 + LLM 跟随）
- 修改：`app/config.py`（新增 GuardrailSettings + fail-closed）
- 修改：`app/agent/__init__.py`（lazy import，避免导入副作用）
- 修改：`app/prompt/browser.py`（修复 import-time f-string `NameError`）
- 新增测试：`tests/test_guardrails.py`
- 新增测试：`tests/test_flow_guardrails.py`

## 测试要点与运行方式
### 覆盖点
- 输入阻断：`prompt_injection` 命中时直接阻断
- 输出脱敏：`pii` 命中时替换为 `[REDACTED_*]`
- 输出 retry：当自定义 guard 要求满足条件时，通过 `repair_text()` 使输出合规
- Agent/Flow 接入链路：确保护栏在入口/出口生效

### 运行（本次新增用例）
```bash
python -m pytest -q tests/test_guardrails.py tests/test_flow_guardrails.py
```

## 已知限制与后续建议
- **审计能力**：当前通过后不会保留“曾触发过哪些 reasons/tags、经历过几次 action”的完整链路；如需可观测性，建议为 `GuardrailEnforceResult` 增加 history。
- **action_taken 语义**：当前 `action_taken` 的表达偏粗（pass/redact 推断），如需精确区分 retry 成功，建议显式记录最后一次生效 action。
- **更强 guard**：建议后续逐步补充
  - 更全面的 secret 检测（按你们支持的平台/模型 key）
  - 更鲁棒的 injection 检测（结构化特征、上下文信号、或专用模型）
  - 结构化输出校验（JSON schema / pydantic schema）与 re-ask
- **覆盖范围扩展**（如未来需要）：可新增边界 `tool_input/tool_output`、`flow_step_input/flow_step_output`，复用同一引擎与配置模式。

