# OpenManus 多模态与生成功能使用指南

> **版本**: v1.0  
> **更新日期**: 2025-01-18  
> **功能**: 图像生成、语音合成、多模态理解

---

## 📋 目录

- [功能概述](#功能概述)
- [快速开始](#快速开始)
- [配置说明](#配置说明)
- [使用方法](#使用方法)
- [API参考](#api参考)
- [常见问题](#常见问题)

---

## 功能概述

### 新增特性

| 功能 | 模型 | 说明 |
|------|------|------|
| **多模态理解** | qwen3-omni-flash | PlanningFlow支持文本、图像、音频输入理解 |
| **图像生成** | qwen-image-plus | 通过文本描述生成高质量图像 |
| **语音合成** | cosyvoice-v1 | 将文本转换为自然流畅的语音 |

### 新增Agent

- **ImageGenerationAgent**: 图像生成专用Agent
- **AudioGenerationAgent**: 音频生成专用Agent

### 架构特点

- ✅ 统一的 `llm_dashscope.py` 模块管理DashScope SDK调用
- ✅ Agent直接调用生成功能，无中间Tool层
- ✅ PlanningFlow自动使用多模态LLM
- ✅ 支持多Agent协作完成复杂任务

---

## 快速开始

### 1. 安装依赖

```bash
pip install dashscope pydub httpx --upgrade
```

或使用requirements.txt:

```bash
pip install -r requirements.txt
```

### 2. 配置API密钥

#### 方法A：环境变量（推荐）

```bash
export DASHSCOPE_API_KEY="sk-your-api-key-here"
```

Windows PowerShell:

```powershell
$env:DASHSCOPE_API_KEY="sk-your-api-key-here"
```

#### 方法B：配置文件

编辑 `config/config.toml`:

```toml
[llm.planning]
api_key = "sk-your-actual-api-key"

[llm.image_gen]
api_key = "sk-your-actual-api-key"

[llm.audio_gen]
api_key = "sk-your-actual-api-key"
```

### 3. 快速测试

```bash
# 运行测试套件
python tests/test_multimodal_agents.py
```

选择要测试的功能：
1. 图像生成
2. 音频合成
3. 直接API调用
4. PlanningFlow协作
5. 查看生成文件

---

## 配置说明

### 完整配置示例

```toml
# Planning Flow 使用的多模态LLM
[llm.planning]
model = "qwen3-omni-flash"
base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
api_key = "sk-your-dashscope-api-key"
api_type = ""
api_version = ""
max_tokens = 8192
temperature = 0.7

# 图像生成Agent的LLM配置
[llm.image_gen]
model = "qwen-turbo"
base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
api_key = "sk-your-dashscope-api-key"
api_type = ""
api_version = ""
max_tokens = 4096
temperature = 0.6

# 音频生成Agent的LLM配置
[llm.audio_gen]
model = "qwen-turbo"
base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
api_key = "sk-your-dashscope-api-key"
api_type = ""
api_version = ""
max_tokens = 4096
temperature = 0.6
```

### 配置参数说明

| 参数 | 说明 | 推荐值 |
|------|------|--------|
| `model` | 模型名称 | planning用qwen3-omni-flash，其他用qwen-turbo |
| `base_url` | API端点 | 北京地域用dashscope.aliyuncs.com |
| `api_key` | API密钥 | 从阿里云百炼获取 |
| `max_tokens` | 最大tokens | planning: 8192, 其他: 4096 |
| `temperature` | 随机性控制 | 0.6-0.7 |

---

## 使用方法

### 场景1：单独使用图像生成Agent

```python
import asyncio
from app.agent.image_generation import ImageGenerationAgent

async def generate_image():
    agent = ImageGenerationAgent()
    
    result = await agent.run(
        "生成一只可爱的橘猫，坐在窗台上晒太阳，阳光洒在它的毛发上"
    )
    
    print(result)
    await agent.cleanup()

asyncio.run(generate_image())
```

**输出示例**:
```
✅ 图像生成成功！

**生成的图像**：
1. 本地路径: `D:\python_project\openmanus-test\workspace\images\1737273600_0.png`
   在线链接: https://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/...

**请求ID**: 1a2b3c4d-5e6f-7g8h-9i0j-k1l2m3n4o5p6
**提示词**: 一只橘色的猫咪慵懒地趴在窗台上，温暖的阳光透过窗户洒在它柔软的毛发上...
```

### 场景2：单独使用音频生成Agent

```python
import asyncio
from app.agent.audio_generation import AudioGenerationAgent

async def generate_audio():
    agent = AudioGenerationAgent()
    
    result = await agent.run(
        "请用女声、正常语速合成：欢迎使用OpenManus多模态AI系统"
    )
    
    print(result)
    await agent.cleanup()

asyncio.run(generate_audio())
```

**输出示例**:
```
✅ 语音合成成功！

**音频文件**：`D:\python_project\openmanus-test\workspace\audios\1737273620.mp3`
**时长**：3.45 秒
**音色**：longxiaochun
**语速**：1.0x

文本内容（前100字）：欢迎使用OpenManus多模态AI系统...
```

### 场景3：直接调用llm_dashscope模块

```python
import asyncio
from app.llm_dashscope import generate_image, synthesize_speech

async def direct_api_call():
    # 图像生成
    image_result = await generate_image(
        prompt="一朵盛开的玫瑰花，水珠点缀",
        size="1024*1024",
        watermark=False
    )
    print(f"图像: {image_result['images']}")
    
    # 语音合成
    audio_result = await synthesize_speech(
        text="这是一段测试语音",
        voice="longxiaochun",
        speech_rate=1.2
    )
    print(f"音频: {audio_result['audio_path']}")

asyncio.run(direct_api_call())
```

### 场景4：PlanningFlow多Agent协作

```python
import asyncio
from app.agent.manus import Manus
from app.agent.image_generation import ImageGenerationAgent
from app.agent.audio_generation import AudioGenerationAgent
from app.flow.planning import PlanningFlow

async def multiagent_workflow():
    # 创建多个Agent
    agents = {
        "manus": await Manus.create(),
        "image_gen": ImageGenerationAgent(),
        "audio_gen": AudioGenerationAgent()
    }
    
    # 创建PlanningFlow（自动使用qwen3-omni-flash）
    flow = PlanningFlow(agents)
    
    # 执行复杂任务
    request = """
    请完成以下任务：
    1. 生成一张"夕阳下的海滩"图片
    2. 为这张图片创作一段解说音频
    """
    
    result = await flow.execute(request)
    print(result)
    
    # 清理
    for agent in agents.values():
        await agent.cleanup()

asyncio.run(multiagent_workflow())
```

---

## API参考

### DashScopeImageGenerator

```python
class DashScopeImageGenerator:
    def __init__(self, api_key: Optional[str] = None, model: str = "qwen-image-plus")
    
    async def generate(
        self,
        prompt: str,                    # 图像描述
        negative_prompt: str = "",      # 负面提示词
        size: str = "1024*1024",        # 尺寸
        watermark: bool = False,        # 是否添加水印
        prompt_extend: bool = True,     # 是否自动扩展提示词
        seed: Optional[int] = None      # 随机种子
    ) -> Dict
```

**支持的尺寸**:
- `1024*1024` - 正方形
- `720*1280` - 竖屏
- `1280*720` - 横屏
- `1328*1328` - 大正方形

**返回值**:
```python
{
    'success': True,
    'images': ['/path/to/image1.png'],
    'urls': ['https://...'],
    'request_id': 'xxx',
    'message': 'Successfully generated 1 image(s)'
}
```

### DashScopeAudioGenerator

```python
class DashScopeAudioGenerator:
    def __init__(self, api_key: Optional[str] = None, model: str = "cosyvoice-v1")
    
    async def synthesize(
        self,
        text: str,                      # 要合成的文本（最多5000字）
        voice: str = "longxiaochun",    # 音色
        format: str = "mp3",            # 音频格式
        sample_rate: int = 22050,       # 采样率
        volume: int = 50,               # 音量 0-100
        speech_rate: float = 1.0,       # 语速 0.5-2.0
        pitch_rate: float = 1.0         # 音调 0.5-2.0
    ) -> Dict
```

**支持的音色**:
- `longxiaochun` - 温柔女声（默认）
- `longyaoyao` - 成熟男声
- `longxiaoxia` - 清脆童声

**返回值**:
```python
{
    'success': True,
    'audio_path': '/path/to/audio.mp3',
    'duration': 3.45,
    'message': 'Audio generated successfully'
}
```

---

## 常见问题

### Q1: API密钥错误

**问题**: `HTTP返回码：401，错误码：InvalidApiKey`

**解决**:
1. 检查环境变量是否设置正确
2. 检查config.toml中的api_key是否有效
3. 确认API密钥来自阿里云百炼平台

### Q2: 图像生成失败

**问题**: `Image generation failed: HTTP返回码：400`

**解决**:
1. 检查prompt是否包含敏感词
2. 确认size参数格式正确（用`*`不是`x`）
3. 检查API配额是否充足

### Q3: 音频合成超时

**问题**: `Error in speech synthesis: timeout`

**解决**:
1. 减少文本长度（<5000字）
2. 检查网络连接
3. 适当增加超时时间

### Q4: 文件保存位置

**问题**: 生成的文件保存在哪里？

**解决**:
- 图像: `workspace/images/`
- 音频: `workspace/audios/`

可以通过以下代码查看:
```python
from app.config import config
print(config.workspace_root)
```

### Q5: PlanningFlow不使用planning配置

**问题**: PlanningFlow没有使用qwen3-omni-flash

**解决**:
1. 确认config.toml中有 `[llm.planning]` 配置段
2. 检查日志中是否有 "Using 'planning' LLM config" 提示
3. 确认API密钥配置正确

### Q6: 依赖安装问题

**问题**: `ModuleNotFoundError: No module named 'dashscope'`

**解决**:
```bash
pip install dashscope pydub --upgrade
```

如果使用conda:
```bash
conda install -c conda-forge pydub
pip install dashscope
```

---

## 性能指标

| 操作 | 平均耗时 | 配额消耗 |
|------|---------|---------|
| 图像生成 (1张) | 10-30秒 | 1次调用 |
| 语音合成 (100字) | 1-5秒 | 1次调用 |
| Prompt优化 | 2-5秒 | 1次LLM调用 |

---

## 最佳实践

### 1. 图像生成

✅ **推荐做法**:
- 提供详细、具体的描述
- 包含光线、色彩、构图等细节
- 启用 `prompt_extend=True` 自动优化

❌ **避免**:
- 过于简短的描述（如"猫"）
- 包含敏感或违规内容
- 频繁使用相同seed（缺乏多样性）

### 2. 语音合成

✅ **推荐做法**:
- 使用标准标点符号断句
- 文本长度控制在500-2000字
- 根据内容选择合适音色

❌ **避免**:
- 没有标点的长文本
- 超过5000字的单次合成
- 极端语速参数（<0.5或>2.0）

### 3. 多Agent协作

✅ **推荐做法**:
- 明确任务分解
- 合理设置Agent顺序
- 充分利用PlanningFlow的规划能力

❌ **避免**:
- 任务描述模糊
- 过度依赖单个Agent
- 忽略错误处理

---

## 更新日志

### v1.0 (2025-01-18)

- ✅ 新增 `llm_dashscope.py` 模块
- ✅ 新增 `ImageGenerationAgent`
- ✅ 新增 `AudioGenerationAgent`
- ✅ PlanningFlow支持多模态LLM
- ✅ 完整的测试套件
- ✅ 详细的使用文档

---

## 参考链接

- [阿里云百炼官方文档](https://bailian.console.aliyun.com/)
- [DashScope API文档](https://help.aliyun.com/zh/model-studio/developer-reference/)
- [通义万相文档](https://help.aliyun.com/zh/model-studio/developer-reference/image-synthesis)
- [CosyVoice文档](https://help.aliyun.com/zh/model-studio/developer-reference/cosyvoice-tts)

---

**问题反馈**: 如有问题或建议，请提Issue或联系开发团队。

