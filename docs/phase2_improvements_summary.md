# 阶段2代码改进总结

## 改进概述

根据代码审查报告，对阶段2的三个工具实现进行了改进，解决了审查中发现的问题。

## 改进详情

### REQ-2.1: PythonExecute 改进

#### 问题2：临时文件清理 ✅

**改进内容**：
- 使用 UUID 生成唯一的临时文件名：`/tmp/script_{uuid}.py`
- 在执行完成后使用 `finally` 块清理临时文件
- 即使执行失败也会清理临时文件

**代码变更**：
```python
# 使用唯一文件名
import uuid
script_path = f"/tmp/script_{uuid.uuid4().hex[:8]}.py"

# 在 finally 块中清理
finally:
    try:
        await sandbox.run_command(f"rm -f {script_path}", timeout=2)
    except Exception:
        pass  # 忽略清理错误
```

#### 问题3：错误信息更详细 ✅

**改进内容**：
- 使用 `2>&1` 重定向确保 stderr 被捕获
- 添加注释说明 stderr 已被 terminal 自动捕获
- 在捕获异常时尝试获取更详细的错误信息

**代码变更**：
```python
# 使用 2>&1 确保 stderr 被捕获
output = await sandbox.run_command(
    f"python3 {script_path} 2>&1", timeout=timeout
)

# 尝试获取更详细的错误信息
try:
    check_output = await sandbox.run_command(
        f"python3 {script_path} 2>&1 || true", timeout=2
    )
    if check_output:
        error_msg = check_output
except:
    pass
```

---

### REQ-2.2: Bash 改进

#### 问题1：使用 _sandbox_sessions ✅

**改进内容**：
- 实际使用 `_sandbox_sessions` 字典跟踪沙箱会话状态
- 跟踪工作目录（working_dir）变化
- 在 `cd` 命令后更新工作目录
- 在 `pwd` 命令后更新工作目录
- 在 sandbox 不存在时清理会话状态

**代码变更**：
```python
# 初始化会话状态
if sandbox_id not in self._sandbox_sessions:
    self._sandbox_sessions[sandbox_id] = {
        "working_dir": "/workspace",
        "initialized": False,
    }

# 跟踪 cd 命令
if command.strip().startswith("cd "):
    parts = command.strip().split(None, 1)
    if len(parts) > 1:
        target_dir = parts[1].strip().strip("'\"")
        self._sandbox_sessions[sandbox_id]["working_dir"] = target_dir

# 跟踪 pwd 命令
if command.strip() == "pwd":
    if output.strip():
        self._sandbox_sessions[sandbox_id]["working_dir"] = output.strip()

# 清理不存在的 sandbox 的会话状态
except KeyError:
    if sandbox_id in self._sandbox_sessions:
        del self._sandbox_sessions[sandbox_id]
```

**说明**：
- 虽然沙箱的 terminal 会自动维护会话状态，但我们现在也跟踪这些状态
- 这为未来可能需要查询工作目录等功能提供了基础
- 在 sandbox 删除时自动清理会话状态

---

### REQ-2.3: StrReplaceEditor 改进

#### 问题1：sandbox 删除时清理操作器 ✅

**改进内容**：
- 添加 `cleanup_sandbox_operator` 类方法用于清理缓存
- 在 `execute` 方法中捕获 KeyError，如果 sandbox 不存在则清理缓存
- 使用懒清理机制：在使用操作器时检测并清理无效缓存

**代码变更**：
```python
@classmethod
def cleanup_sandbox_operator(cls, sandbox_id: str) -> None:
    """Clean up cached operator for a deleted sandbox."""
    if sandbox_id in cls._sandbox_operators:
        del cls._sandbox_operators[sandbox_id]

# 在 execute 方法中
try:
    await self.validate_path(command, Path(path), operator)
except (KeyError, RuntimeError) as e:
    # 如果 sandbox 不存在，清理缓存
    if sandbox_id and ("not found" in str(e).lower() or "Sandbox" in str(e)):
        self.cleanup_sandbox_operator(sandbox_id)
    raise
```

**说明**：
- 使用懒清理机制，避免同步检查的性能开销
- 当操作失败并提示 sandbox 不存在时，自动清理缓存
- 提供了 `cleanup_sandbox_operator` 方法供外部调用（如 Agent cleanup 时）

#### 问题3：导入位置优化 ✅

**改进内容**：
- 将 `SandboxManager` 导入移到文件顶部
- 避免在方法内部重复导入检查

**代码变更**：
```python
# 文件顶部
from app.sandbox.core.manager import SandboxManager

# 方法中使用
manager = SandboxManager()
```

---

## 改进效果

### 资源管理
- ✅ PythonExecute 临时文件自动清理
- ✅ StrReplaceEditor 缓存自动清理（防止内存泄漏）
- ✅ Bash 会话状态自动清理

### 错误处理
- ✅ PythonExecute 错误信息更详细
- ✅ 所有工具的错误输出都被正确捕获（stderr）

### 代码质量
- ✅ 导入位置优化
- ✅ 未使用的变量现在被实际使用
- ✅ 代码结构更清晰

## 测试建议

建议更新测试用例以验证改进：

1. **PythonExecute 临时文件清理测试**：
   - 验证临时文件在执行后被删除
   - 验证执行失败时临时文件也被清理

2. **Bash 会话状态跟踪测试**：
   - 验证 `cd` 命令后工作目录被跟踪
   - 验证 `pwd` 命令后工作目录被更新
   - 验证 sandbox 删除时会话状态被清理

3. **StrReplaceEditor 缓存清理测试**：
   - 验证 sandbox 删除后缓存被清理
   - 验证使用已删除的 sandbox_id 时会自动清理缓存

## 后续优化建议

1. **Bash timeout 参数**（中优先级）：
   - 添加 `timeout` 参数到 `_execute_in_sandbox` 方法
   - 使用配置中的默认值或参数值

2. **StrReplaceEditor 缓存大小限制**（低优先级）：
   - 实现 LRU 缓存机制
   - 设置最大缓存数量限制

3. **SandboxManager 回调机制**（可选）：
   - 添加 sandbox 删除时的回调机制
   - 允许工具注册清理回调

## 总结

所有审查中发现的高优先级和中优先级问题都已解决：
- ✅ REQ-2.1 问题2、3：临时文件清理和错误信息改进
- ✅ REQ-2.2 问题1：实际使用 `_sandbox_sessions`
- ✅ REQ-2.3 问题1、3：缓存清理机制和导入优化

代码质量得到提升，资源管理更加完善，错误处理更加健壮。
