# Agent Docker 沙箱执行方案 - 需求列表

## 需求概述

将 Agent 的所有代码执行、文件操作、命令执行迁移到 Docker 沙箱环境中，确保隔离性和安全性。

## 需求分类

### 阶段 1：基础设施改造（前置依赖）

#### REQ-1.1: 扩展 SandboxSettings 配置
**优先级**: 高
**依赖**: 无
**描述**: 在 `SandboxSettings` 中添加工作区挂载相关配置

**具体任务**:
- [ ] 在 `app/config.py` 的 `SandboxSettings` 类中添加：
  - `mount_workspace: bool = Field(True, description="是否挂载工作区")`
  - `workspace_path: Optional[str] = Field(None, description="本地工作区路径，None 时使用 config.workspace_root")`
- [ ] 更新配置示例文件（`config/config.example.toml`）

**验收标准**:
- [ ] 配置可以正确读取
- [ ] 默认值符合预期
- [ ] 配置示例文件更新

**测试要点**:
- [ ] 测试配置读取
- [ ] 测试默认值
- [ ] 测试自定义路径

---

#### REQ-1.2: 改造 SandboxFileOperator 支持 sandbox_id
**优先级**: 高
**依赖**: REQ-1.1
**描述**: 让 `SandboxFileOperator` 支持通过 `sandbox_id` 访问特定沙箱实例

**具体任务**:
- [ ] 修改 `app/tool/file_operators.py` 中的 `SandboxFileOperator` 类
- [ ] 添加 `sandbox_id: Optional[str] = None` 参数到 `__init__`
- [ ] 添加 `sandbox_manager` 属性（通过全局或传入）
- [ ] 修改 `_ensure_sandbox_initialized` 方法：
  - 如果有 `sandbox_id`，通过 `SandboxManager.get_sandbox(sandbox_id)` 获取
  - 如果没有，使用现有的 `SANDBOX_CLIENT` 单例（保持向后兼容）
- [ ] 更新所有文件操作方法使用新的沙箱实例

**验收标准**:
- [ ] 支持通过 `sandbox_id` 访问特定沙箱
- [ ] 无 `sandbox_id` 时使用全局单例（向后兼容）
- [ ] 所有文件操作正常工作

**测试要点**:
- [ ] 测试有 `sandbox_id` 时的文件操作
- [ ] 测试无 `sandbox_id` 时的向后兼容性
- [ ] 测试多沙箱隔离

---

### 阶段 2：工具改造

#### REQ-2.1: 改造 PythonExecute 支持沙箱执行
**优先级**: 高
**依赖**: REQ-1.2
**描述**: 让 `PythonExecute` 工具支持在 Docker 沙箱中执行 Python 代码

**具体任务**:
- [ ] 修改 `app/tool/python_execute.py` 中的 `PythonExecute` 类
- [ ] 在 `execute` 方法中添加 `sandbox_id: Optional[str] = None` 参数
- [ ] 实现沙箱执行逻辑：
  - 如果有 `sandbox_id`，通过 `SandboxManager` 获取沙箱实例
  - 将代码写入沙箱临时文件（如 `/tmp/script.py`）
  - 使用 `sandbox.run_command("python3 /tmp/script.py")` 执行
  - 捕获输出和错误
- [ ] 保持向后兼容：无 `sandbox_id` 时使用现有本地执行逻辑

**验收标准**:
- [ ] 可以在沙箱中执行 Python 代码
- [ ] 输出和错误正确捕获
- [ ] 向后兼容本地执行
- [ ] 超时机制正常工作

**测试要点**:
- [ ] 测试简单 Python 代码执行
- [ ] 测试带错误的代码
- [ ] 测试超时处理
- [ ] 测试文件读写（在沙箱中）
- [ ] 测试向后兼容性

---

#### REQ-2.2: 改造 Bash 支持沙箱执行
**优先级**: 高
**依赖**: REQ-1.2
**描述**: 让 `Bash` 工具支持在 Docker 沙箱中执行命令

**具体任务**:
- [ ] 修改 `app/tool/bash.py` 中的 `Bash` 类
- [ ] 在 `execute` 方法中添加 `sandbox_id: Optional[str] = None` 参数
- [ ] 实现沙箱执行逻辑：
  - 如果有 `sandbox_id`，通过 `SandboxManager` 获取沙箱实例
  - 使用 `sandbox.run_command(command)` 执行
  - 保持会话状态（通过沙箱的 terminal 机制）
- [ ] 保持向后兼容：无 `sandbox_id` 时使用现有 `_BashSession` 逻辑

**验收标准**:
- [ ] 可以在沙箱中执行命令
- [ ] 命令输出正确返回
- [ ] 会话状态保持（如 cd 命令）
- [ ] 向后兼容本地执行

**测试要点**:
- [ ] 测试简单命令执行（ls, pwd）
- [ ] 测试会话状态保持
- [ ] 测试错误处理
- [ ] 测试超时处理
- [ ] 测试向后兼容性

---

#### REQ-2.3: 优化 StrReplaceEditor 支持 sandbox_id
**优先级**: 中
**依赖**: REQ-1.2
**描述**: 让 `StrReplaceEditor` 支持使用 Agent 级沙箱（而非全局单例）

**具体任务**:
- [ ] 修改 `app/tool/str_replace_editor.py` 中的 `StrReplaceEditor` 类
- [ ] 添加 `sandbox_id: Optional[str] = None` 参数支持
- [ ] 修改 `_get_operator` 方法：
  - 如果有 `sandbox_id`，创建使用该 `sandbox_id` 的 `SandboxFileOperator` 实例
  - 否则使用现有逻辑（基于 `config.sandbox.use_sandbox`）
- [ ] 保持向后兼容

**验收标准**:
- [ ] 支持通过 `sandbox_id` 使用特定沙箱
- [ ] 保持现有配置驱动的工作方式
- [ ] 所有文件操作正常工作

**测试要点**:
- [ ] 测试有 `sandbox_id` 时的文件操作
- [ ] 测试无 `sandbox_id` 时的现有逻辑
- [ ] 测试多 Agent 隔离

---

### 阶段 3：Agent 集成

#### REQ-3.1: 在 ToolCallAgent 基类中添加沙箱管理（延迟加载）
**优先级**: 高
**依赖**: REQ-1.1
**描述**: 在 Agent 基类中添加沙箱生命周期管理，采用延迟加载策略

**设计原则**:
- **延迟加载**：不在 Agent 初始化时创建沙箱，而是在真正需要时才创建
- **按需创建**：只有当工具需要沙箱时才创建沙箱实例
- **自动清理**：Agent cleanup 时自动清理沙箱

**具体任务**:
- [ ] 修改 `app/agent/toolcall.py` 中的 `ToolCallAgent` 类
- [ ] 添加属性：
  - `sandbox_manager: Optional[SandboxManager] = None`
  - `sandbox_id: Optional[str] = None`
  - `_sandbox_initialized: bool = False`（标记是否已初始化）
- [ ] 添加方法 `async def _ensure_sandbox(self) -> str`:
  - 如果 `_sandbox_initialized` 为 True，直接返回 `sandbox_id`
  - 如果 `config.sandbox.use_sandbox = False`，返回 None
  - 否则创建沙箱：
    - 创建或获取 `SandboxManager` 实例（可使用全局单例）
    - 准备 volume bindings（根据配置）
    - 调用 `sandbox_manager.create_sandbox()` 创建沙箱
    - 保存 `sandbox_id`，设置 `_sandbox_initialized = True`
- [ ] 修改 `cleanup` 方法：
  - 如果有 `sandbox_id`，调用 `sandbox_manager.delete_sandbox(sandbox_id)`
  - 重置 `_sandbox_initialized = False`
  - 调用父类 cleanup

**验收标准**:
- [ ] Agent 初始化时不创建沙箱（延迟加载）
- [ ] 首次需要沙箱时自动创建
- [ ] Agent cleanup 时正确清理沙箱
- [ ] 多个 Agent 实例拥有独立沙箱
- [ ] 配置禁用时不创建沙箱

**测试要点**:
- [ ] 测试延迟加载（初始化时不创建）
- [ ] 测试首次使用时的自动创建
- [ ] 测试沙箱清理
- [ ] 测试多 Agent 隔离
- [ ] 测试配置禁用时的行为
- [ ] 测试异常情况处理

---

#### REQ-3.2: 实现工具执行路由机制
**优先级**: 高
**依赖**: REQ-3.1, REQ-2.1, REQ-2.2, REQ-2.3
**描述**: 实现工具执行路由，自动判断工具是否需要沙箱，并路由到正确的执行环境

**工具分类**:
- **宿主机执行工具**（不需要沙箱）：
  - `BrowserUseTool`：浏览器操作
  - `AskHuman`：人机交互
  - `Terminate`：终止工具
  - `MCPClientTool`：MCP 工具（结果可能需要在沙箱执行）
  - `CreateChatCompletion`：LLM 调用
- **沙箱执行工具**（需要沙箱）：
  - `PythonExecute`：Python 代码执行
  - `Bash`：Shell 命令执行
  - `StrReplaceEditor`：文件操作（根据配置）

**具体任务**:
- [ ] 在 `ToolCallAgent` 中添加方法 `_tool_needs_sandbox(tool_name: str) -> bool`:
  - 定义沙箱工具列表：`SANDBOX_TOOLS = ['python_execute', 'bash', 'str_replace_editor']`
  - 检查工具名是否在列表中
  - 对于 `str_replace_editor`，还需检查 `config.sandbox.use_sandbox`
- [ ] 修改 `execute_tool` 方法：
  - 在执行前调用 `_tool_needs_sandbox` 判断
  - 如果需要沙箱：
    - 调用 `_ensure_sandbox()` 确保沙箱已创建
    - 将 `sandbox_id` 添加到工具参数中
  - 如果不需要沙箱：
    - 直接执行，不传递 `sandbox_id`
- [ ] 添加日志记录：
  - 记录工具执行环境（宿主机/沙箱）
  - 记录沙箱创建时机

**验收标准**:
- [ ] 正确识别需要沙箱的工具
- [ ] 需要沙箱的工具自动创建沙箱（延迟加载）
- [ ] 不需要沙箱的工具在宿主机执行
- [ ] 工具执行路由正确
- [ ] 日志记录清晰

**测试要点**:
- [ ] 测试 PythonExecute 自动创建沙箱
- [ ] 测试 Bash 自动创建沙箱
- [ ] 测试 StrReplaceEditor 根据配置路由
- [ ] 测试 BrowserUseTool 在宿主机执行
- [ ] 测试 MCP tool 在宿主机执行
- [ ] 测试工具执行路由日志

---

#### REQ-3.3: 细化宿主机和沙箱协同执行流程
**优先级**: 高
**依赖**: REQ-3.1, REQ-3.2
**描述**: 设计并实现宿主机和沙箱工具的协同执行流程

**执行流程设计**:

```
1. Agent 接收工具调用请求
   ↓
2. 判断工具类型（_tool_needs_sandbox）
   ├─ 需要沙箱 → 步骤 3A
   └─ 不需要沙箱 → 步骤 3B

3A. 沙箱工具执行流程：
   ├─ 调用 _ensure_sandbox()（延迟加载）
   │  ├─ 如果未初始化 → 创建沙箱
   │  └─ 如果已初始化 → 返回 sandbox_id
   ├─ 将 sandbox_id 添加到工具参数
   ├─ 在沙箱中执行工具
   └─ 返回结果

3B. 宿主机工具执行流程：
   ├─ 直接执行工具（不传递 sandbox_id）
   ├─ 如果是 MCP tool → 检查结果类型
   │  ├─ 类型 A（纯计算）→ 直接返回
   │  └─ 类型 B/C（需要执行）→ 步骤 4
   └─ 返回结果

4. MCP 结果沙箱执行流程：
   ├─ 调用 _ensure_sandbox()（延迟加载）
   ├─ 根据结果类型调用相应沙箱工具
   │  ├─ code → PythonExecute(sandbox_id)
   │  ├─ command → Bash(sandbox_id)
   │  └─ file_operation → StrReplaceEditor(sandbox_id)
   └─ 返回执行结果
```

**数据流设计**:
- **宿主机 → 沙箱**：
  - 文件内容：通过 volume 挂载或 `sandbox.write_file()`
  - 命令/代码：通过 `sandbox.run_command()`
- **沙箱 → 宿主机**：
  - 执行结果：通过 `sandbox.run_command()` 返回
  - 文件内容：通过 volume 挂载或 `sandbox.read_file()`
- **状态共享**：
  - 文件系统：通过 volume 挂载共享 `/workspace`
  - 环境变量：在沙箱创建时配置
  - 工作目录：统一使用 `/workspace`

**具体任务**:
- [ ] 在 `ToolCallAgent` 中添加执行流程文档注释
- [ ] 实现 `_ensure_sandbox()` 延迟加载机制
- [ ] 实现 `_tool_needs_sandbox()` 工具分类
- [ ] 在 `execute_tool` 中集成路由逻辑
- [ ] 添加执行流程日志（便于调试）
- [ ] 处理异常情况：
  - 沙箱创建失败 → 回退到本地执行（如果配置允许）
  - 工具执行失败 → 记录错误并返回

**验收标准**:
- [ ] 执行流程清晰，符合设计
- [ ] 延迟加载正常工作
- [ ] 工具路由正确
- [ ] 数据传递正常
- [ ] 异常处理完善
- [ ] 日志记录完整

**测试要点**:
- [ ] 测试完整执行流程（宿主机工具 → 沙箱工具）
- [ ] 测试 MCP tool → 沙箱执行流程
- [ ] 测试文件系统共享（volume 挂载）
- [ ] 测试多工具协作（文件操作 + 代码执行）
- [ ] 测试异常情况处理
- [ ] 测试日志记录

---

#### REQ-3.4: 实现工具自动获取 sandbox_id 机制
**优先级**: 高
**依赖**: REQ-3.1, REQ-3.2, REQ-2.1, REQ-2.2, REQ-2.3
**描述**: 让工具在执行时自动获取 Agent 的 `sandbox_id`（已包含在 REQ-3.2 中）

**说明**: 此需求已在 REQ-3.2 中实现，作为工具执行路由的一部分。
- 工具执行路由会自动判断是否需要沙箱
- 如果需要，自动调用 `_ensure_sandbox()` 获取 `sandbox_id`
- 自动将 `sandbox_id` 添加到工具参数中

**验收标准**: 已在 REQ-3.2 中覆盖

**测试要点**: 已在 REQ-3.2 中覆盖

---

### 阶段 4：MCP 集成

#### REQ-4.1: 实现 MCP tool 结果类型判断
**优先级**: 中
**依赖**: REQ-3.1
**描述**: 判断 MCP tool 返回的结果是否需要通过沙箱执行

**具体任务**:
- [ ] 在 `ToolCallAgent` 或 `Manus` 中添加方法 `_needs_sandbox_execution(result: dict) -> bool`
- [ ] 检查结果中是否包含可执行内容：
  - `code`, `command`, `script`, `patch`, `file_content` 等关键字
- [ ] 返回布尔值

**验收标准**:
- [ ] 正确识别类型 A（纯计算，直接返回）
- [ ] 正确识别类型 B/C（需要执行，通过沙箱）

**测试要点**:
- [ ] 测试各种结果格式的识别
- [ ] 测试边界情况

---

#### REQ-4.2: 实现 MCP 结果通过沙箱执行
**优先级**: 中
**依赖**: REQ-4.1, REQ-2.1, REQ-2.2, REQ-2.3
**描述**: 将 MCP 返回的执行型结果通过沙箱工具执行

**具体任务**:
- [ ] 在 `ToolCallAgent` 或 `Manus` 中添加方法 `_execute_mcp_result_in_sandbox(result: dict)`
- [ ] 根据结果类型调用相应工具：
  - `code` → `PythonExecute`
  - `command` → `Bash`
  - `file_operation` → `StrReplaceEditor`
- [ ] 在 `execute_tool` 中集成：
  - 如果是 MCP tool，执行后判断结果类型
  - 如果需要执行，调用 `_execute_mcp_result_in_sandbox`

**验收标准**:
- [ ] MCP 返回的代码可以通过沙箱执行
- [ ] MCP 返回的命令可以通过沙箱执行
- [ ] MCP 返回的文件操作可以通过沙箱执行

**测试要点**:
- [ ] 测试代码生成型 MCP tool
- [ ] 测试命令生成型 MCP tool
- [ ] 测试文件操作型 MCP tool
- [ ] 测试纯计算型 MCP tool（直接返回）

---

### 阶段 5：测试和优化

#### REQ-5.1: 编写单元测试
**优先级**: 高
**依赖**: 所有阶段 1-4
**描述**: 为所有改造的功能编写单元测试

**具体任务**:
- [ ] 为 `SandboxSettings` 配置扩展编写测试
- [ ] 为 `SandboxFileOperator` 改造编写测试
- [ ] 为 `PythonExecute` 沙箱执行编写测试
- [ ] 为 `Bash` 沙箱执行编写测试
- [ ] 为 `StrReplaceEditor` 优化编写测试
- [ ] 为 Agent 沙箱管理编写测试
- [ ] 为 MCP 集成编写测试

**验收标准**:
- [ ] 所有测试通过
- [ ] 测试覆盖率 > 80%
- [ ] 包含正常流程和异常流程测试

---

#### REQ-5.2: 编写集成测试
**优先级**: 高
**依赖**: REQ-5.1
**描述**: 编写端到端的集成测试

**具体任务**:
- [ ] 测试完整 Agent 工作流程（创建 → 执行工具 → 清理）
- [ ] 测试多工具协作（文件操作 + 代码执行）
- [ ] 测试 MCP tool 集成流程
- [ ] 测试多 Agent 并发场景
- [ ] 测试资源清理

**验收标准**:
- [ ] 所有集成测试通过
- [ ] 测试覆盖主要使用场景

---

#### REQ-5.3: 性能测试和优化
**优先级**: 低
**依赖**: REQ-5.2
**描述**: 测试性能并进行优化

**具体任务**:
- [ ] 测试沙箱创建时间
- [ ] 测试命令执行延迟
- [ ] 测试并发执行能力
- [ ] 优化启动流程（如需要）
- [ ] 优化资源使用（如需要）

**验收标准**:
- [ ] 性能指标符合预期
- [ ] 无明显性能瓶颈

---

## 需求依赖关系图

```
REQ-1.1 (配置扩展)
    ↓
REQ-1.2 (SandboxFileOperator)
    ↓
    ├─→ REQ-2.1 (PythonExecute)
    ├─→ REQ-2.2 (Bash)
    └─→ REQ-2.3 (StrReplaceEditor)
        ↓
REQ-3.1 (Agent 基类 - 延迟加载)
    ↓
    ├─→ REQ-3.2 (工具执行路由)
    │   └─→ REQ-3.4 (工具自动获取 sandbox_id - 已包含)
    └─→ REQ-3.3 (宿主机和沙箱协同执行流程)
        ↓
        ├─→ REQ-4.1 (MCP 结果判断)
        └─→ REQ-4.2 (MCP 沙箱执行)
            ↓
            REQ-5.1 (单元测试)
                ↓
                REQ-5.2 (集成测试)
                    ↓
                    REQ-5.3 (性能测试)
```

## 实施计划

### Sprint 1: 基础设施（REQ-1.1, REQ-1.2）
- 目标：完成配置扩展和 SandboxFileOperator 改造
- 预计时间：2-3 天
- 验收：配置可读，文件操作支持 sandbox_id

### Sprint 2: 工具改造（REQ-2.1, REQ-2.2, REQ-2.3）
- 目标：完成所有工具的沙箱支持
- 预计时间：3-4 天
- 验收：所有工具可在沙箱中执行

### Sprint 3: Agent 集成（REQ-3.1, REQ-3.2, REQ-3.3, REQ-3.4）
- 目标：完成 Agent 层面的沙箱管理（延迟加载）和工具执行路由
- 预计时间：3-4 天
- 验收：
  - Agent 延迟加载沙箱（按需创建）
  - 工具执行路由正确（宿主机/沙箱）
  - 宿主机和沙箱协同执行流程完善

### Sprint 4: MCP 集成（REQ-4.1, REQ-4.2）
- 目标：完成 MCP tool 与沙箱的集成
- 预计时间：2-3 天
- 验收：MCP 结果可以通过沙箱执行

### Sprint 5: 测试（REQ-5.1, REQ-5.2, REQ-5.3）
- 目标：完成所有测试
- 预计时间：3-4 天
- 验收：所有测试通过，功能稳定

## 风险控制

### 技术风险
- **Docker 环境依赖**：确保开发环境有 Docker
- **向后兼容性**：每个改造都要保持向后兼容
- **资源清理**：确保沙箱正确清理，避免资源泄漏

### 缓解措施
- 每个需求完成后立即进行测试
- 保持现有功能不受影响
- 添加详细的日志记录

## 验收标准总结

### 功能验收
- [ ] 所有代码执行在 Docker 沙箱中
- [ ] 所有文件操作在 Docker 沙箱中
- [ ] 所有命令执行在 Docker 沙箱中
- [ ] MCP tool 结果可以通过沙箱执行
- [ ] 浏览器操作保持独立（不进入代码沙箱）

### 质量验收
- [ ] 所有单元测试通过
- [ ] 所有集成测试通过
- [ ] 代码覆盖率 > 80%
- [ ] 向后兼容性保持
- [ ] 性能指标符合预期

### 文档验收
- [ ] 代码注释完整
- [ ] API 文档更新
- [ ] 使用示例更新
