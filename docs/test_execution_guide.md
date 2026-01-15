# 单元测试执行指南

## 前置条件

### 1. 安装依赖

确保已安装所有必需的依赖：

```bash
pip install -r requirements.txt
```

如果缺少 `structlog`，需要额外安装：

```bash
pip install structlog
```

### 2. Docker 环境

**重要**：`test_sandbox_file_operator.py` 需要 Docker 环境运行，因为测试会创建真实的 Docker 容器。

确保：
- Docker Desktop 已安装并运行
- Docker daemon 正在运行
- 可以通过 `docker ps` 命令验证

```bash
# 检查 Docker 是否运行
docker ps
```

## 执行测试

### 方法 1：执行单个测试文件

#### REQ-1.1 测试（不需要 Docker）

```bash
# 从项目根目录执行
python -m pytest tests/test_sandbox_settings.py -v
```

#### REQ-1.2 测试（需要 Docker）

```bash
# 从项目根目录执行
python -m pytest tests/test_sandbox_file_operator.py -v
```

### 方法 2：执行所有阶段1的测试

```bash
# 执行两个测试文件
python -m pytest tests/test_sandbox_settings.py tests/test_sandbox_file_operator.py -v
```

### 方法 3：执行特定测试用例

```bash
# 执行单个测试方法
python -m pytest tests/test_sandbox_settings.py::TestSandboxSettings::test_default_values -v

# 执行单个测试类
python -m pytest tests/test_sandbox_file_operator.py::TestSandboxFileOperator -v
```

### 方法 4：执行所有测试（包括现有测试）

```bash
# 执行 tests/ 目录下的所有测试
python -m pytest tests/ -v

# 或者只执行阶段1的测试
python -m pytest tests/test_sandbox_*.py -v
```

## 测试选项

### 显示详细输出

```bash
# -v: 详细模式
python -m pytest tests/test_sandbox_settings.py -v

# -vv: 更详细的输出
python -m pytest tests/test_sandbox_settings.py -vv

# -s: 显示 print 输出
python -m pytest tests/test_sandbox_settings.py -s
```

### 显示覆盖率

```bash
# 需要安装 pytest-cov
pip install pytest-cov

# 运行测试并显示覆盖率
python -m pytest tests/test_sandbox_settings.py --cov=app --cov-report=html
```

### 并行执行（如果安装了 pytest-xdist）

```bash
# 需要安装 pytest-xdist
pip install pytest-xdist

# 并行执行测试
python -m pytest tests/test_sandbox_*.py -n auto
```

## 常见问题

### 1. Docker 未运行

**错误信息**：
```
docker.errors.DockerException: Error while fetching server API version
```

**解决方法**：
- 启动 Docker Desktop
- 确保 Docker daemon 正在运行
- 运行 `docker ps` 验证连接

### 2. 缺少依赖

**错误信息**：
```
ModuleNotFoundError: No module named 'xxx'
```

**解决方法**：
```bash
pip install -r requirements.txt
pip install structlog  # 如果缺少
```

### 3. 配置问题

**错误信息**：
```
pydantic_core._pydantic_core.ValidationError
```

**解决方法**：
- 确保 `config/config.toml` 或 `config/config.example.toml` 存在
- 检查配置文件的格式是否正确

### 4. pytest-asyncio 警告

**警告信息**：
```
PytestDeprecationWarning: The configuration option "asyncio_default_fixture_loop_scope" is unset.
```

**解决方法**（可选）：
创建 `pytest.ini` 文件：

```ini
[pytest]
asyncio_mode = auto
asyncio_default_fixture_loop_scope = function
```

## 测试文件说明

### tests/test_sandbox_settings.py

**测试内容**：
- REQ-1.1: SandboxSettings 配置扩展
- 不需要 Docker 环境
- 5 个测试用例

**测试用例**：
1. `test_default_values` - 测试默认值
2. `test_custom_mount_workspace` - 测试自定义 mount_workspace
3. `test_custom_workspace_path` - 测试自定义 workspace_path
4. `test_all_fields` - 测试所有字段
5. `test_config_loading` - 测试配置加载

### tests/test_sandbox_file_operator.py

**测试内容**：
- REQ-1.2: SandboxFileOperator sandbox_id 支持
- **需要 Docker 环境**
- 6 个测试用例

**测试用例**：
1. `test_backward_compatibility_no_sandbox_id` - 测试向后兼容性
2. `test_with_sandbox_id` - 测试 sandbox_id 支持
3. `test_file_operations_with_sandbox_id` - 测试文件操作
4. `test_run_command_with_sandbox_id` - 测试命令执行
5. `test_multiple_sandbox_isolation` - 测试多沙箱隔离
6. `test_sandbox_manager_singleton` - 测试单例模式

## 预期结果

### test_sandbox_settings.py

```
============================= test session starts ==============================
tests/test_sandbox_settings.py::TestSandboxSettings::test_default_values PASSED
tests/test_sandbox_settings.py::TestSandboxSettings::test_custom_mount_workspace PASSED
tests/test_sandbox_settings.py::TestSandboxSettings::test_custom_workspace_path PASSED
tests/test_sandbox_settings.py::TestSandboxSettings::test_all_fields PASSED
tests/test_sandbox_settings.py::TestSandboxSettings::test_config_loading PASSED
============================== 5 passed in 0.15s ===============================
```

### test_sandbox_file_operator.py（需要 Docker）

```
============================= test session starts ==============================
tests/test_sandbox_file_operator.py::TestSandboxFileOperator::test_backward_compatibility_no_sandbox_id PASSED
tests/test_sandbox_file_operator.py::TestSandboxFileOperator::test_with_sandbox_id PASSED
tests/test_sandbox_file_operator.py::TestSandboxFileOperator::test_file_operations_with_sandbox_id PASSED
tests/test_sandbox_file_operator.py::TestSandboxFileOperator::test_run_command_with_sandbox_id PASSED
tests/test_sandbox_file_operator.py::TestSandboxFileOperator::test_multiple_sandbox_isolation PASSED
tests/test_sandbox_file_operator.py::TestSandboxFileOperator::test_sandbox_manager_singleton PASSED
============================== 6 passed in XX.XXs ===============================
```

## 快速开始

```bash
# 1. 确保 Docker 运行
docker ps

# 2. 安装依赖（如果需要）
pip install -r requirements.txt
pip install structlog

# 3. 执行测试
# REQ-1.1（不需要 Docker）
python -m pytest tests/test_sandbox_settings.py -v

# REQ-1.2（需要 Docker）
python -m pytest tests/test_sandbox_file_operator.py -v

# 或者一起执行
python -m pytest tests/test_sandbox_*.py -v
```
