# Gemini API 兼容性测试说明

## 📋 测试目的

验证 Gemini API 与 OpenAI 接口的兼容性，特别是：
1. **Planning Flow** 需要的 Function Calling（工具调用）功能
2. 图像生成和语音生成的可用方式

## 🚀 快速开始

### 步骤 1: 安装依赖

```powershell
# 必需依赖
pip install openai

# 可选依赖（用于测试原生 SDK）
pip install google-generativeai
```

### 步骤 2: 设置 API Key

**方式 A - 环境变量（推荐）**：

在 PowerShell 中：
```powershell
$env:GEMINI_API_KEY="your-gemini-api-key"
```

**方式 B - 运行时输入**：
直接运行测试，程序会提示你输入 API Key

### 步骤 3: 运行测试

```powershell
cd d:\python_project\openmanus-test
python tests/test_gemini_compatibility.py
```

## 📊 测试内容

### 测试 0: 列出可用模型 ✅
- 查看 Gemini API 提供的所有可用模型
- 为后续测试提供模型列表

### 测试 1: 基本对话完成 ✅
- 模型：`gemini-2.5-flash`
- 验证基本的 chat completion 功能

### 测试 2: 工具调用 / Function Calling 🎯
**这是 Planning Flow 的核心功能！**

- 模拟 PlanningTool 的调用
- 验证 Gemini 是否能正确返回结构化的工具调用
- **如果此测试通过**：Planning Flow 可直接切换到 Gemini
- **如果此测试失败**：需要适配层

### 测试 3: 图像生成探索 🔍
尝试多种方式查找图像生成 API：
1. OpenAI `images.generate` 接口
2. 检查模型列表中的图像模型
3. Google Generative AI 原生 SDK

### 测试 4: 语音生成探索 🔍
尝试多种方式查找语音生成 API：
1. OpenAI `audio.speech` 接口
2. 检查模型列表中的 TTS 模型
3. Google Generative AI 原生 SDK

## 📖 获取 Gemini API Key

1. 访问 [Google AI Studio](https://aistudio.google.com/app/apikey)
2. 点击 "Get API Key"
3. 创建或选择一个项目
4. 复制生成的 API Key

## 🎯 预期结果

### ✅ 应该通过的测试
- 列出模型
- 基本对话
- **工具调用**（Planning Flow 关键）

### ⚠️ 可能需要探索的测试
- 图像生成（可能需要独立 API）
- 语音生成（可能需要独立 API）

## 📝 测试后的步骤

### 如果工具调用测试通过 ✅

**Planning Flow 可以立即切换！**

实施步骤：
1. 在 `config/config.toml` 中添加配置：
```toml
[llm.planning]
model = "gemini-2.5-flash"
base_url = "https://generativelanguage.googleapis.com/v1beta/openai/"
api_key = "your-gemini-api-key"
api_type = "Openai"
temperature = 0.7
max_tokens = 4096
```

2. 修改 `app/flow/planning.py` 中 `PlanningFlow` 的 LLM 初始化
3. 无需修改其他代码

### 如果图像/语音生成测试失败 ⚠️

根据测试输出的建议：
- **图像生成**：使用 Imagen 3 API（独立端点）
  - 文档：https://ai.google.dev/gemini-api/docs/imagen
- **语音生成**：使用 Google Cloud Text-to-Speech
  - 文档：https://cloud.google.com/text-to-speech

## 🔧 故障排查

### 问题 1: 导入错误
```
ModuleNotFoundError: No module named 'openai'
```
**解决**：`pip install openai`

### 问题 2: API Key 无效
```
Error: API key is invalid
```
**解决**：
1. 检查 API Key 是否正确复制
2. 确认 API Key 已启用
3. 检查是否有配额限制

### 问题 3: 网络连接错误
```
Error: Connection timeout
```
**解决**：
1. 检查网络连接
2. 如果在中国大陆，可能需要代理
3. 检查防火墙设置

### 问题 4: 所有测试都失败
**可能原因**：
1. API Key 未正确设置
2. Base URL 不正确（应该是 `https://generativelanguage.googleapis.com/v1beta/openai/`）
3. 网络问题

## 📚 参考资源

- [Gemini OpenAI 兼容性文档](https://ai.google.dev/gemini-api/docs/openai?hl=zh-cn)
- [Gemini API 文档](https://ai.google.dev/gemini-api/docs)
- [Google AI Studio](https://aistudio.google.com/)
- [OpenAI SDK 文档](https://github.com/openai/openai-python)

## 💡 提示

1. **Planning Flow 是优先级最高的**：只要工具调用测试通过，就可以立即实施
2. **图像和语音生成是额外功能**：可以后续单独探索
3. **保存测试输出**：测试输出包含很多有用信息，建议保存下来
4. **测试是安全的**：只是读取 API，不会产生大量费用

## 📞 需要帮助？

如果测试遇到问题，请提供：
1. 完整的错误信息
2. 使用的 Python 版本
3. 安装的包版本（`pip list | grep -E "openai|google"`)
4. 测试输出的完整日志

---

**祝测试顺利！🎉**




