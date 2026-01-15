# Agent Docker 沙箱执行方案设计文档（修订版）

## 1. 需求概述

### 1.1 目标
将所有 agent 的 action（工具执行）迁移到 Docker 沙箱环境中执行，确保：
- 所有代码执行、文件操作、命令执行都在隔离的容器环境中
- 保持与现有工具接口的兼容性
- 支持本地文件系统与沙箱的同步
- 提供统一的沙箱生命周期管理

### 1.2 需求边界
- ✅ 所有代码执行、文件操作、命令执行必须在 Docker 沙箱中
- ✅ 支持文件读写操作（本地 ↔ 沙箱）
- ✅ 支持代码执行（Python、Bash 等）
- ✅ 支持资源限制（CPU、内存）
- ✅ 支持网络隔离（可选）
- ✅ 支持通过 MCP server 调用远程工具（兼容现有 MCPClients / MCPAgent 机制）
- ✅ MCP tool 返回的执行型结果必须通过 Docker 沙箱工具执行
- ❌ 浏览器操作不进入 Docker 代码沙箱（保持独立部署）
- ❌ 不改变现有工具接口（保持向后兼容）
- ❌ 不强制所有 agent 使用沙箱（通过配置控制）

### 1.3 现有实现分析
经过代码审查，发现以下现有实现：
- ✅ **SandboxManager 已存在**：`app/sandbox/core/manager.py` 已实现完整的沙箱管理器
- ✅ **SandboxFileOperator 已存在**：`app/tool/file_operators.py` 已实现沙箱文件操作
- ✅ **StrReplaceEditor 已支持沙箱**：通过 `config.sandbox.use_sandbox` 配置切换
- ❌ **PythonExecute 未支持沙箱**：目前只在本地执行
- ❌ **Bash 未支持沙箱**：目前只在本地执行
- ❌ **Agent 层面未管理沙箱**：当前使用全局 `SANDBOX_CLIENT` 单例

## 2. 架构设计（基于现有实现）

### 2.1 整体架构（调整后）

```
┌─────────────────────────────────────────────────────────┐
│                    Agent Layer                           │
│  (ToolCallAgent, Manus, etc.)                            │
│  ┌──────────────────────────────────────────────────┐  │
│  │  SandboxManager (Agent 级管理)                    │  │
│  │  - 每个 Agent 实例拥有独立的 sandbox_id            │  │
│  │  - Agent 生命周期内共享沙箱                        │  │
│  └──────────────────────────────────────────────────┘  │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│                  Tool Layer                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐ │
│  │ PythonExecute │  │ Bash         │  │ StrReplace   │ │
│  │ (需改造)      │  │ (需改造)      │  │ (已支持)      │ │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘ │
│         │                  │                  │         │
└─────────┼──────────────────┼──────────────────┼─────────┘
          │                  │                  │
          └──────────────────┼──────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────┐
│            FileOperator Layer (现有)                     │
│  ┌──────────────────────────────────────────────────┐  │
│  │  SandboxFileOperator (已存在)                     │  │
│  │  - 通过 SANDBOX_CLIENT 访问沙箱                    │  │
│  │  - 需要改为通过 SandboxManager 访问                │  │
│  └──────────────────────────────────────────────────┘  │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│            SandboxManager (已存在)                       │
│  ┌──────────────────────────────────────────────────┐  │
│  │  - 多沙箱管理                                      │  │
│  │  - 生命周期管理                                    │  │
│  │  - 自动清理                                        │  │
│  └──────────────────────────────────────────────────┘  │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│            DockerSandbox (已存在)                        │
│  ┌──────────────────────────────────────────────────┐  │
│  │  - 容器创建/销毁                                   │  │
│  │  - 命令执行                                       │  │
│  │  - 文件操作                                       │  │
│  └──────────────────────────────────────────────────┘  │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│                  Docker Engine                           │
└─────────────────────────────────────────────────────────┘
```

### 2.2 核心组件（调整后）

#### 2.2.1 SandboxManager（已存在，需集成）
- **位置**：`app/sandbox/core/manager.py`
- **现状**：已实现完整功能，但未被使用
- **改造**：
  - 在 Agent 初始化时创建 SandboxManager 实例
  - 为每个 Agent 创建独立的 sandbox_id
  - 在 Agent cleanup 时清理沙箱

#### 2.2.2 SandboxFileOperator（已存在，需改造）
- **位置**：`app/tool/file_operators.py`
- **现状**：使用全局 `SANDBOX_CLIENT` 单例
- **改造**：
  - 支持传入 `sandbox_id` 参数
  - 通过 SandboxManager 获取沙箱实例
  - 保持向后兼容（无 sandbox_id 时使用全局单例）

#### 2.2.3 工具改造（部分已完成）
- ✅ **StrReplaceEditor**：已支持沙箱，通过 `config.sandbox.use_sandbox` 切换
- ❌ **PythonExecute**：需改造支持沙箱执行
- ❌ **Bash**：需改造支持沙箱执行

#### 2.2.4 MCP Server 集成架构（新增）

**执行模型**：
```
┌─────────────────────────────────────────────────────────┐
│                    Agent Layer                           │
│  ┌──────────────────────────────────────────────────┐  │
│  │  MCPClients (已存在)                              │  │
│  │  - SSE / stdio 连接                                │  │
│  │  - 工具发现和调用                                   │  │
│  └──────────────────────────────────────────────────┘  │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│            MCP Server Layer                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐ │
│  │ 远程 MCP      │  │ 本地 MCP      │  │ Browser MCP  │ │
│  │ (HTTP/SSE)    │  │ (stdio)      │  │ (浏览器控制)  │ │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘ │
│         │                  │                  │         │
└─────────┼──────────────────┼──────────────────┼─────────┘
          │                  │                  │
          └──────────────────┼──────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────┐
│           结果处理层                                      │
│  ┌──────────────────────────────────────────────────┐  │
│  │  类型 A: 纯计算/API → 直接返回                      │  │
│  │  类型 B: 代码/命令 → 通过沙箱工具执行                │  │
│  │  类型 C: 文件操作 → 通过沙箱工具执行                 │  │
│  └──────────────────────────────────────────────────┘  │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│            Docker Sandbox (代码执行)                     │
└─────────────────────────────────────────────────────────┘
```

**关键原则**：
- MCP server 在宿主机侧运行（或作为独立服务）
- MCP 返回的"执行型"结果必须通过 Docker 沙箱工具执行
- 浏览器操作不进入代码沙箱，保持独立

## 3. 详细设计（基于现有实现）

### 3.1 沙箱管理策略（调整后）

**采用方案：Agent 级沙箱（基于现有 SandboxManager）**

- **实现方式**：
  - 每个 Agent 实例在初始化时通过 SandboxManager 创建独立的 sandbox_id
  - Agent 内的所有工具共享同一个沙箱实例
  - Agent cleanup 时通过 SandboxManager 清理沙箱
  - SandboxManager 提供自动清理机制（空闲超时）

- **优势**：
  - 利用现有的 SandboxManager 实现
  - Agent 间完全隔离
  - Agent 内工具状态共享
  - 自动资源管理

- **实现要点**：
  - Agent 初始化时：`sandbox_id = await sandbox_manager.create_sandbox(...)`
  - 工具执行时：通过 `sandbox_id` 获取沙箱实例
  - Agent 清理时：`await sandbox_manager.delete_sandbox(sandbox_id)`

### 3.2 文件系统映射（基于现有实现）

**现有实现**：
- `DockerSandbox._prepare_volume_bindings()` 已支持 volume 挂载
- 工作目录自动挂载到临时目录

**调整方案**：
1. **Volume 挂载**（推荐）：将本地 `workspace/` 目录挂载到容器的 `/workspace`
2. **配置支持**：在 `SandboxSettings` 中添加 `workspace_path` 和 `mount_workspace` 选项
3. **路径映射**：工具操作时自动转换路径（本地路径 → 沙箱路径）

**实现细节**：
```python
# 在创建沙箱时
volume_bindings = {
    str(config.workspace_root): "/workspace"  # 挂载本地工作区
}
sandbox_id = await sandbox_manager.create_sandbox(
    config=sandbox_config,
    volume_bindings=volume_bindings
)
```

### 3.3 工具改造方案（基于现有实现）

#### 3.3.1 PythonExecute（需改造）

**现有实现**：
- 使用 multiprocessing 在本地执行
- 直接访问本地文件系统

**改造方案**：
- 添加 `sandbox_id` 参数（可选）
- 如果提供 `sandbox_id`，在沙箱中执行 Python 代码
- 通过 `SandboxManager.get_sandbox(sandbox_id).run_command()` 执行
- 保持接口向后兼容（无 `sandbox_id` 时本地执行）

**实现思路**：
```python
async def execute(self, code: str, timeout: int = 5, sandbox_id: Optional[str] = None):
    if sandbox_id:
        # 在沙箱中执行
        sandbox = await sandbox_manager.get_sandbox(sandbox_id)
        # 将代码写入临时文件
        # 执行 python 命令
        result = await sandbox.run_command(f"python3 /tmp/script.py")
    else:
        # 本地执行（现有逻辑）
        ...
```

#### 3.3.2 Bash（需改造）

**现有实现**：
- 使用 `_BashSession` 在本地执行
- 使用 asyncio.subprocess

**改造方案**：
- 添加 `sandbox_id` 参数（可选）
- 如果提供 `sandbox_id`，使用沙箱的 `run_command` 方法
- 保持会话状态（通过沙箱的 terminal 或 session 机制）
- 保持接口向后兼容

**实现思路**：
```python
async def execute(self, command: str, sandbox_id: Optional[str] = None, ...):
    if sandbox_id:
        # 在沙箱中执行
        sandbox = await sandbox_manager.get_sandbox(sandbox_id)
        result = await sandbox.run_command(command, timeout=timeout)
        return CLIResult(output=result, error="")
    else:
        # 本地执行（现有逻辑）
        ...
```

#### 3.3.3 StrReplaceEditor（已支持，需优化）

**现有实现**：
- ✅ 已通过 `_get_operator()` 支持沙箱模式
- ✅ 使用 `SandboxFileOperator`（基于全局 `SANDBOX_CLIENT`）

**优化方案**：
- 支持传入 `sandbox_id` 参数
- 创建 Agent 级的 `SandboxFileOperator` 实例
- 通过 `SandboxManager` 获取沙箱实例
- 保持向后兼容（无 `sandbox_id` 时使用全局单例）

### 3.4 MCP Server 集成策略

#### 3.4.1 MCP Tool 类型分类

**类型 A：纯计算 / 远程 API 调用型**
- **示例**：搜索引擎、翻译服务、知识库问答、数据查询
- **特征**：不涉及本地文件系统或命令执行
- **策略**：
  - ✅ 允许直接使用，无需沙箱
  - ✅ 结果作为文本/结构化数据返回给 Agent
  - ✅ 可在宿主机侧直接处理

**类型 B：建议 / 计划 / 代码生成型**
- **示例**：代码生成、命令建议、补丁生成、配置生成
- **特征**：返回可执行内容（代码、命令、配置），但不直接执行
- **策略**：
  - ✅ MCP 只负责"生成"，不直接执行
  - ✅ Agent 接收结果后，通过沙箱工具执行：
    - 代码 → `PythonExecute`（在沙箱中）
    - 命令 → `Bash`（在沙箱中）
    - 文件修改 → `StrReplaceEditor`（在沙箱中）

**类型 C：文件/系统操作型 MCP**
- **示例**：声称可以操作文件系统、执行命令的 MCP tool
- **特征**：声明具有文件/系统操作能力
- **策略**：
  - ⚠️ **不允许直接操作本地或容器文件系统**
  - ✅ 只允许输出"操作描述"（patch、命令、变更说明）
  - ✅ 由 Agent 通过沙箱工具落地执行
  - ✅ 强制通过沙箱执行，确保隔离性

#### 3.4.2 MCP 执行流程

```
1. User Request
   ↓
2. Agent → LLM → 选择 MCP tool
   ↓
3. Agent → MCP Server (HTTP/SSE/stdio)
   ↓
4. MCP Server → 返回结果
   ↓
5. Agent 判断结果类型
   ├─ 类型 A → 直接返回给 User
   └─ 类型 B/C → 提取执行内容
      ↓
6. Agent → Docker 沙箱工具
   ├─ PythonExecute (代码)
   ├─ Bash (命令)
   └─ StrReplaceEditor (文件)
      ↓
7. 沙箱执行 → 返回结果
   ↓
8. Agent → User
```

#### 3.4.3 浏览器操作部署策略

**原则：浏览器操作不进入 Docker 代码沙箱**

**现有实现**：
- ✅ `BrowserUseTool`：宿主机侧通过 Playwright 执行
- ✅ `SandboxBrowserTool`：Daytona 远程沙箱（独立浏览器容器）

**设计规范**：

1. **代码沙箱职责**（DockerSandbox）：
   - ✅ Python 代码执行
   - ✅ Shell 命令执行
   - ✅ 工作区文件读写/编辑
   - ❌ 不包含浏览器

2. **浏览器操作职责**：
   - ✅ 由宿主机上的 `BrowserUseTool` 执行
   - ✅ 或通过 MCP browser server 执行
   - ✅ 返回页面内容/数据给 Agent
   - ✅ Agent 可决定是否在沙箱内处理结果（如解析 HTML、存文件）

3. **如需浏览器隔离**：
   - ✅ 使用**独立的浏览器容器/服务**（类似 Daytona sandbox）
   - ✅ 不与代码沙箱混用
   - ✅ 通过 MCP 或独立工具接口访问

**架构示意**：
```
┌─────────────────────────────────────────────────────────┐
│                    Agent Layer                           │
└──────┬──────────────────────────────┬───────────────────┘
       │                              │
       ▼                              ▼
┌──────────────────┐        ┌──────────────────┐
│  Docker 代码沙箱   │        │  浏览器服务         │
│  - PythonExecute  │        │  - BrowserUseTool │
│  - Bash           │        │  - MCP Browser     │
│  - 文件操作        │        │  - 独立容器         │
└──────────────────┘        └──────────────────┘
```

## 4. 实现步骤（调整后）

### 阶段 1：基础设施改造（利用现有实现）
1. ✅ **SandboxManager 已存在**，无需创建
2. ⚠️ **扩展 SandboxFileOperator**：支持 `sandbox_id` 参数
3. ⚠️ **扩展 SandboxSettings**：添加 `workspace_path` 和 `mount_workspace` 配置

### 阶段 2：工具改造
1. ⚠️ **改造 PythonExecute**：支持 `sandbox_id` 参数，在沙箱中执行
2. ⚠️ **改造 Bash**：支持 `sandbox_id` 参数，在沙箱中执行
3. ⚠️ **优化 StrReplaceEditor**：支持 `sandbox_id` 参数，使用 Agent 级沙箱

### 阶段 3：Agent 集成
1. ⚠️ **在 Agent 初始化时**：
   - 创建 SandboxManager 实例（或使用全局单例）
   - 创建独立的 sandbox_id
   - 将 sandbox_id 传递给工具
2. ⚠️ **在 Agent cleanup 时**：
   - 通过 SandboxManager 清理沙箱
3. ⚠️ **工具自动使用沙箱**：
   - 工具通过 sandbox_id 获取沙箱实例
   - 所有操作在沙箱中执行

### 阶段 4：测试和优化
1. ⚠️ 单元测试
2. ⚠️ 集成测试
3. ⚠️ 性能优化

**说明**：✅ 表示已完成，⚠️ 表示需要实现

## 5. 技术实现细节

### 5.1 沙箱配置（扩展现有配置）

```python
# 在 app/config.py 中扩展 SandboxSettings
class SandboxSettings(BaseModel):
    use_sandbox: bool = Field(False, description="Whether to use the sandbox")
    image: str = Field("python:3.12-slim", description="Base image")
    work_dir: str = Field("/workspace", description="Container working directory")
    memory_limit: str = Field("512m", description="Memory limit")
    cpu_limit: float = Field(1.0, description="CPU limit")
    timeout: int = Field(300, description="Default command timeout (seconds)")
    network_enabled: bool = Field(False, description="Whether network access is allowed")
    # 新增配置
    mount_workspace: bool = Field(True, description="是否挂载工作区")
    workspace_path: Optional[str] = Field(None, description="本地工作区路径，None 时使用 config.workspace_root")
```

### 5.2 沙箱管理器接口（已存在）

```python
# app/sandbox/core/manager.py 已实现
class SandboxManager:
    async def create_sandbox(
        self,
        config: Optional[SandboxSettings] = None,
        volume_bindings: Optional[Dict[str, str]] = None,
    ) -> str:  # 返回 sandbox_id

    async def get_sandbox(self, sandbox_id: str) -> DockerSandbox:
        """获取沙箱实例（已实现）"""

    async def delete_sandbox(self, sandbox_id: str) -> None:
        """删除沙箱（已实现）"""

    async def cleanup(self) -> None:
        """清理所有沙箱（已实现）"""
```

### 5.3 Agent 集成接口（新增）

```python
# 在 Agent 基类或 ToolCallAgent 中添加
class ToolCallAgent(ReActAgent):
    sandbox_manager: Optional[SandboxManager] = None
    sandbox_id: Optional[str] = None

    async def initialize_sandbox(self) -> str:
        """初始化 Agent 的沙箱"""
        if not self.sandbox_manager:
            from app.sandbox.core.manager import SandboxManager
            self.sandbox_manager = SandboxManager()

        # 准备 volume bindings
        workspace_path = config.sandbox.workspace_path or str(config.workspace_root)
        volume_bindings = {workspace_path: "/workspace"} if config.sandbox.mount_workspace else None

        # 创建沙箱
        self.sandbox_id = await self.sandbox_manager.create_sandbox(
            config=config.sandbox,
            volume_bindings=volume_bindings
        )
        return self.sandbox_id

    async def cleanup(self):
        """清理资源"""
        if self.sandbox_id and self.sandbox_manager:
            await self.sandbox_manager.delete_sandbox(self.sandbox_id)
        await super().cleanup()
```

### 5.4 工具接口调整（向后兼容）

```python
# PythonExecute 改造示例
class PythonExecute(BaseTool):
    async def execute(
        self,
        code: str,
        timeout: int = 5,
        sandbox_id: Optional[str] = None,  # 新增参数
    ) -> Dict:
        if sandbox_id:
            # 沙箱执行逻辑
            ...
        else:
            # 本地执行（现有逻辑）
            ...
```

### 5.5 MCP Server 集成实现

#### 5.5.1 MCP Tool 结果处理机制

```python
# 在 Agent 的 tool 执行逻辑中添加
async def execute_mcp_tool(self, tool_name: str, args: dict) -> ToolResult:
    """执行 MCP tool 并处理结果"""
    # 1. 调用 MCP server
    result = await self.mcp_clients.execute_tool(tool_name, args)

    # 2. 判断结果类型
    if self._is_executable_result(result):
        # 类型 B/C：需要执行的内容
        return await self._execute_in_sandbox(result)
    else:
        # 类型 A：直接返回
        return result

def _is_executable_result(self, result: dict) -> bool:
    """判断结果是否需要通过沙箱执行"""
    # 检查结果中是否包含代码、命令、文件操作指令
    executable_indicators = ['code', 'command', 'script', 'patch', 'file_content']
    return any(indicator in str(result).lower() for indicator in executable_indicators)

async def _execute_in_sandbox(self, result: dict) -> ToolResult:
    """在沙箱中执行 MCP 返回的内容"""
    if 'code' in result:
        # 通过 PythonExecute 执行
        return await self.available_tools.execute(
            'python_execute',
            {'code': result['code'], 'sandbox_id': self.sandbox_id}
        )
    elif 'command' in result:
        # 通过 Bash 执行
        return await self.available_tools.execute(
            'bash',
            {'command': result['command'], 'sandbox_id': self.sandbox_id}
        )
    # ... 其他类型处理
```

#### 5.5.2 MCP 与沙箱配置关系

```python
# config.sandbox.use_sandbox = True 时的行为
if config.sandbox.use_sandbox:
    # 所有本地工具执行在 Docker 沙箱中
    # MCP tool 若要"落地执行"，必须通过沙箱工具间接执行
    # MCP 自身配置通过 config.mcp_config 管理（不变）

# 已有 MCP 配置示例（config.example-*.toml 中已存在，以下仅为说明，无需新增字段）
[mcp.servers.browser_mcp]
type = "sse"
url = "http://localhost:8080/mcp"

[mcp.servers.codegen_mcp]
type = "stdio"
command = "python"
args = ["-m", "mcp_server"]
```

#### 5.5.3 安全策略

**网络隔离**：
- Docker 代码沙箱默认 `network_enabled = False`
- MCP server 的网络访问由宿主机进程负责
- 浏览器操作在宿主机或独立容器中，不穿透代码沙箱

**信任边界**：
- MCP server 被视为"半可信外部服务"
- 执行型结果需要经过：
  1. **命令白名单检查**（可选）
  2. **LLM 二次审查**（可选，通过 Agent 判断）
  3. **强制沙箱执行**（必须）

**实现示例**：
```python
async def _validate_mcp_result(self, result: dict) -> bool:
    """验证 MCP 返回的执行内容"""
    if 'command' in result:
        command = result['command']
        # 白名单检查
        allowed_prefixes = ['python', 'pip', 'ls', 'cat', 'grep']
        if not any(command.startswith(prefix) for prefix in allowed_prefixes):
            logger.warning(f"Command not in whitelist: {command}")
            return False
    return True
```

## 6. 文件路径映射

### 6.1 路径转换规则

```
本地路径: workspace/src/main.py
    ↓
沙箱路径: /workspace/src/main.py
```

### 6.2 挂载配置

```python
volume_bindings = {
    str(config.workspace_root): "/workspace"  # 读写挂载
}
```

## 7. 错误处理

### 7.1 沙箱创建失败
- 回退到本地执行（如果配置允许）
- 记录错误日志
- 返回明确的错误信息

### 7.2 命令执行超时
- 使用沙箱的 timeout 机制
- 返回超时错误
- 清理相关资源

### 7.3 文件操作失败
- 检查路径有效性
- 处理权限问题
- 提供清晰的错误信息

## 8. 性能考虑

### 8.1 沙箱启动时间
- 首次创建：~2-5 秒
- 复用现有容器：即时

### 8.2 文件同步开销
- Volume 挂载：几乎无开销
- 文件复制：取决于文件大小

### 8.3 资源限制
- 内存限制：防止资源耗尽
- CPU 限制：防止 CPU 占用过高
- 超时机制：防止长时间运行

## 9. 测试要点

### 9.1 功能测试
- ✅ Python 代码执行
- ✅ Bash 命令执行
- ✅ 文件读写操作
- ✅ 多工具协作
- ✅ MCP tool 调用和结果处理
- ✅ MCP 结果通过沙箱执行

### 9.2 隔离性测试
- ✅ 文件系统隔离
- ✅ 进程隔离
- ✅ 网络隔离（如启用）

### 9.3 性能测试
- ✅ 沙箱创建时间
- ✅ 命令执行延迟
- ✅ 并发执行能力

## 10. 迁移策略（调整后）

### 10.1 渐进式迁移
1. **阶段 1**：扩展 SandboxFileOperator 支持 sandbox_id（保持向后兼容）
2. **阶段 2**：改造 PythonExecute 和 Bash 支持 sandbox_id（保持向后兼容）
3. **阶段 3**：在 Agent 层面集成 SandboxManager
4. **阶段 4**：默认启用沙箱模式（通过配置控制）

### 10.2 兼容性保证
- ✅ **保持工具接口不变**：通过可选参数 `sandbox_id` 扩展功能
- ✅ **提供配置选项**：`config.sandbox.use_sandbox` 控制是否使用沙箱
- ✅ **支持回退**：无 `sandbox_id` 时使用现有本地执行逻辑
- ✅ **StrReplaceEditor 已支持**：无需改动，只需优化

### 10.3 实现优先级
1. **高优先级**：Agent 集成 SandboxManager（核心功能）
2. **高优先级**：PythonExecute 和 Bash 支持沙箱（主要工具）
3. **中优先级**：SandboxFileOperator 优化（已有基础）
4. **中优先级**：MCP tool 结果处理和沙箱执行集成
5. **低优先级**：性能优化和监控

## 11. 风险评估

### 11.1 技术风险
- **Docker 依赖**：需要 Docker 环境
- **性能开销**：容器化带来的延迟
- **资源限制**：可能影响某些任务

### 11.2 缓解措施
- 提供清晰的错误提示
- 支持配置降级
- 优化沙箱启动流程

## 12. 后续优化

### 12.1 沙箱镜像优化
- 预装常用工具
- 减小镜像体积
- 支持自定义镜像

### 12.2 缓存机制
- 文件系统缓存
- 命令结果缓存

### 12.3 监控和日志
- 沙箱使用统计
- 性能监控
- 错误追踪

## 13. MCP Server 集成详细设计

### 13.1 集成架构

**总体原则**：
- Agent/LLM + MCP client 在宿主机进程中运行
- Docker 沙箱只负责代码/命令/文件等"副作用"操作
- MCP server 可以是远程或本地进程，但对文件/命令类能力，应通过沙箱工具间接操作

**执行流程**：
```
User Request
    ↓
Agent → LLM → 选择工具（本地工具 或 MCP tool）
    ↓
┌─────────────────┬──────────────────┐
│  本地工具        │   MCP Tool        │
│  (Python/Bash)  │  (远程/本地服务)   │
└────────┬────────┴────────┬───────────┘
         │                 │
         │                 ▼
         │          MCP Server 执行
         │                 │
         │                 ▼
         │         返回结果（文本/结构化数据）
         │                 │
         │                 ▼
         │         判断结果类型
         │         ├─ 类型 A：直接返回
         │         └─ 类型 B/C：提取执行内容
         │                 │
         └─────────────────┘
                   │
                   ▼
         Docker 沙箱工具执行
         (PythonExecute/Bash/StrReplaceEditor)
                   │
                   ▼
         返回执行结果给 Agent
                   │
                   ▼
         返回给 User
```

### 13.2 MCP Tool 类型详细分类

#### 类型 A：纯计算 / 远程 API 调用型

**特征**：
- 不涉及本地文件系统操作
- 不涉及命令执行
- 只做数据查询、转换、计算

**示例**：
- 搜索引擎工具（Google、Bing）
- 翻译服务
- 知识库问答
- 数据查询 API

**处理策略**：
- ✅ 允许直接使用，无需沙箱
- ✅ 结果作为文本/结构化数据返回
- ✅ 可在宿主机侧直接处理

#### 类型 B：建议 / 计划 / 代码生成型

**特征**：
- 返回可执行内容（代码、命令、配置）
- 但不直接执行
- 需要 Agent 决策后执行

**示例**：
- 代码生成工具（生成 Python 代码）
- 命令建议工具（生成 shell 命令）
- 补丁生成工具（生成文件修改补丁）
- 配置生成工具

**处理策略**：
- ✅ MCP 只负责"生成"，不直接执行
- ✅ Agent 接收结果后，通过沙箱工具执行：
  - 代码 → `PythonExecute(sandbox_id=...)`
  - 命令 → `Bash(sandbox_id=...)`
  - 文件修改 → `StrReplaceEditor(sandbox_id=...)`

**实现示例**：
```python
# MCP 返回代码生成结果
mcp_result = {
    "code": "print('Hello from MCP')",
    "language": "python"
}

# Agent 通过沙箱执行
await agent.execute_tool('python_execute', {
    'code': mcp_result['code'],
    'sandbox_id': agent.sandbox_id
})
```

#### 类型 C：文件/系统操作型 MCP

**特征**：
- MCP tool 声明具有文件/系统操作能力
- 可能返回文件操作指令或直接尝试操作

**示例**：
- 文件操作 MCP（声称可以读写文件）
- 系统命令 MCP（声称可以执行命令）

**处理策略**：
- ⚠️ **不允许直接操作本地或容器文件系统**
- ✅ 只允许输出"操作描述"（patch、命令、变更说明）
- ✅ 由 Agent 通过沙箱工具落地执行
- ✅ 强制通过沙箱执行，确保隔离性

**安全约束**：
```python
# 禁止直接文件操作
if mcp_tool.has_file_operation_capability():
    # 不允许直接操作
    raise SecurityError("MCP tool cannot directly operate files")

# 只允许返回操作描述
if 'file_operation' in mcp_result:
    # 通过沙箱工具执行
    await agent.execute_tool('str_replace_editor', {
        'command': 'str_replace',
        'path': mcp_result['file_path'],
        'old_str': mcp_result['old_str'],
        'new_str': mcp_result['new_str'],
        'sandbox_id': agent.sandbox_id
    })
```

### 13.3 浏览器操作部署策略

#### 13.3.1 设计原则

**核心原则**：浏览器操作不进入 Docker 代码沙箱

**原因**：
1. **职责分离**：代码沙箱专注于代码/命令/文件执行，浏览器是独立的 UI 自动化能力
2. **资源效率**：浏览器需要大量依赖（Chromium、字体、Xvfb 等），会显著增加代码沙箱镜像体积
3. **现有实现**：已有 `BrowserUseTool`（宿主机）和 `SandboxBrowserTool`（Daytona），无需重复实现
4. **安全模型**：关键隔离是代码执行，浏览器访问外网在宿主机或独立容器中同样可控

#### 13.3.2 部署架构

```
┌─────────────────────────────────────────────────────────┐
│                    Agent Layer                           │
└──────┬──────────────────────────────┬───────────────────┘
       │                              │
       ▼                              ▼
┌──────────────────┐        ┌──────────────────┐
│  Docker 代码沙箱   │        │  浏览器服务         │
│  (python:3.12)    │        │  (独立部署)        │
│                   │        │                   │
│  - PythonExecute  │        │  选项 1:           │
│  - Bash           │        │  BrowserUseTool   │
│  - 文件操作        │        │  (宿主机 Playwright)│
│                   │        │                   │
│  - 网络隔离        │        │  选项 2:           │
│  - 资源限制        │        │  MCP Browser      │
│                   │        │  (远程/本地服务)    │
│                   │        │                   │
│                   │        │  选项 3:           │
│                   │        │  SandboxBrowserTool│
│                   │        │  (独立浏览器容器)   │
└──────────────────┘        └──────────────────┘
```

#### 13.3.3 浏览器操作流程

```
User: "访问网站并提取数据"
    ↓
Agent → 选择 BrowserUseTool 或 MCP Browser tool
    ↓
浏览器服务执行（宿主机或独立容器）
    ↓
返回页面内容/截图/结构化数据
    ↓
Agent 判断是否需要处理
    ├─ 需要保存 → 通过 StrReplaceEditor（在代码沙箱中）
    ├─ 需要分析 → 通过 PythonExecute（在代码沙箱中）
    └─ 直接返回 → 给 User
```

#### 13.3.4 实现规范

**代码沙箱职责**（DockerSandbox）：
- ✅ Python 代码执行
- ✅ Shell 命令执行
- ✅ 工作区文件读写/编辑
- ❌ 不包含浏览器

**浏览器操作职责**：
- ✅ 由宿主机上的 `BrowserUseTool` 执行（现有实现）
- ✅ 或通过 MCP browser server 执行（远程/本地）
- ✅ 或通过独立的浏览器容器（类似 Daytona sandbox）
- ✅ 返回页面内容/数据给 Agent
- ✅ Agent 可决定是否在沙箱内处理结果（如解析 HTML、存文件）

**如需浏览器隔离**：
- ✅ 使用**独立的浏览器容器/服务**（不与代码沙箱混用）
- ✅ 通过 MCP 或独立工具接口访问
- ✅ 保持与代码沙箱的生命周期独立

### 13.4 安全与网络策略

#### 13.4.1 网络隔离

**代码沙箱网络策略**：
- Docker 代码沙箱默认 `network_enabled = False`
- 防止在沙箱内部对外访问
- 确保代码执行环境隔离

**MCP Server 网络访问**：
- MCP server 的网络访问由**宿主机进程**负责
- 不穿透代码沙箱
- 通过 HTTP/SSE/stdio 协议通信

**浏览器网络访问**：
- 浏览器在宿主机或独立容器中运行
- 网络访问由浏览器进程负责
- 不依赖代码沙箱的网络配置

#### 13.4.2 信任边界

**MCP Server 信任模型**：
- MCP server 被视为"半可信外部服务"
- 执行型结果需要经过验证：

1. **命令白名单检查**（可选）：
```python
ALLOWED_COMMAND_PREFIXES = ['python', 'pip', 'ls', 'cat', 'grep', 'find']
if command not in whitelist:
    raise SecurityError("Command not allowed")
```

2. **LLM 二次审查**（可选）：
```python
# Agent 可以询问 LLM 是否安全
is_safe = await llm.ask(f"Is this command safe: {command}")
if not is_safe:
    raise SecurityError("Command deemed unsafe by LLM")
```

3. **强制沙箱执行**（必须）：
```python
# 所有执行型操作必须在沙箱中
await sandbox.run_command(command)
```

### 13.5 配置与集成

#### 13.5.1 配置关系

**沙箱配置**（`config.sandbox`）：
```toml
[sandbox]
use_sandbox = true
image = "python:3.12-slim"
mount_workspace = true
network_enabled = false
```

**MCP 配置**（`config.mcp_config`，不变）：
```toml
[mcp.servers.browser_mcp]
type = "sse"
url = "http://localhost:8080/mcp"

[mcp.servers.codegen_mcp]
type = "stdio"
command = "python"
args = ["-m", "mcp_server"]
```

**行为规则**：
- `config.sandbox.use_sandbox = true` 时：
  - 所有本地工具执行在 Docker 沙箱中
  - MCP tool 若要"落地执行"，必须通过沙箱工具间接执行
- MCP 自身配置仍通过 `config.mcp_config` 管理（不改变）

#### 13.5.2 Agent 集成示例

```python
class Manus(ToolCallAgent):
    async def execute_tool(self, tool_name: str, args: dict):
        # 检查是否是 MCP tool
        if tool_name in self.mcp_clients.tool_map:
            # 执行 MCP tool
            result = await self.mcp_clients.execute_tool(tool_name, args)

            # 判断是否需要通过沙箱执行
            if self._needs_sandbox_execution(result):
                return await self._execute_mcp_result_in_sandbox(result)
            else:
                return result
        else:
            # 本地工具，自动使用 sandbox_id
            if self.sandbox_id:
                args['sandbox_id'] = self.sandbox_id
            return await super().execute_tool(tool_name, args)

    def _needs_sandbox_execution(self, result: dict) -> bool:
        """判断 MCP 结果是否需要通过沙箱执行"""
        executable_keys = ['code', 'command', 'script', 'patch', 'file_content']
        return any(key in result for key in executable_keys)

    async def _execute_mcp_result_in_sandbox(self, result: dict):
        """在沙箱中执行 MCP 返回的内容"""
        if 'code' in result:
            return await self.execute_tool('python_execute', {
                'code': result['code'],
                'sandbox_id': self.sandbox_id
            })
        elif 'command' in result:
            return await self.execute_tool('bash', {
                'command': result['command'],
                'sandbox_id': self.sandbox_id
            })
        # ... 其他类型
```
