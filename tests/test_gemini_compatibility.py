# """
# 测试 Gemini API 与 OpenAI 接口的兼容性

# 本测试验证以下内容：
# 1. 基本对话完成 (Chat Completion) - 使用 gemini-2.5-flash
# 2. 工具调用 (Function Calling) - Planning Flow 核心功能
# 3. 探索图像生成的正确调用方式
# 4. 探索语音生成的正确调用方式
# 5. 列出可用模型

# 运行前请先配置环境变量：
# export GEMINI_API_KEY="your-api-key"

# 或在测试中直接填写 API Key

# 参考文档：https://ai.google.dev/gemini-api/docs/openai?hl=zh-cn
# """

# import asyncio
# import base64
# import json
# import os
# from typing import Optional

# # 尝试导入 OpenAI 客户端
# try:
#     from openai import AsyncOpenAI
# except ImportError:
#     print("请先安装 openai: pip install openai")
#     exit(1)

# # 尝试导入 Google Generative AI SDK（用于测试原生 API）
# try:
#     import google.generativeai as genai

#     HAS_GENAI = True
# except ImportError:
#     HAS_GENAI = False
#     print("⚠️  未安装 google-generativeai，将跳过原生 API 测试")


# class GeminiCompatibilityTester:
#     """Gemini API 兼容性测试器"""

#     def __init__(self, api_key: Optional[str] = None):
#         self.api_key = api_key or os.getenv("GEMINI_API_KEY")
#         if not self.api_key:
#             raise ValueError("请设置 GEMINI_API_KEY 环境变量或传入 api_key 参数")

#         # Gemini OpenAI 兼容端点（官方文档确认）
#         self.base_url = "https://generativelanguage.googleapis.com/v1beta/openai/"

#         # 创建 OpenAI 兼容的客户端
#         self.client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)

#         # 如果有 Google Generative AI SDK，也初始化原生客户端
#         if HAS_GENAI:
#             genai.configure(api_key=self.api_key)
#             self.has_native_client = True
#         else:
#             self.has_native_client = False

#     async def test_list_models(self) -> bool:
#         """测试0: 列出可用模型"""
#         print("\n" + "=" * 60)
#         print("测试 0: 列出可用模型")
#         print("=" * 60)

#         try:
#             models = await self.client.models.list()
#             model_list = []

#             async for model in models:
#                 model_list.append(model.id)
#                 print(f"  - {model.id}")

#             print(f"\n✅ 成功: 找到 {len(model_list)} 个模型")

#             # 保存模型列表供后续测试使用
#             self.available_models = model_list
#             return True

#         except Exception as e:
#             print(f"❌ 失败: {type(e).__name__}: {str(e)}")
#             self.available_models = ["gemini-2.5-flash"]  # 使用默认模型
#             return False

#     async def test_basic_chat(self) -> bool:
#         """测试1: 基本对话完成（gemini-2.5-flash）"""
#         print("\n" + "=" * 60)
#         print("测试 1: 基本对话完成（gemini-2.5-flash）")
#         print("=" * 60)

#         try:
#             response = await self.client.chat.completions.create(
#                 model="gemini-2.5-flash",
#                 messages=[{"role": "user", "content": "你好，请用一句话介绍你自己"}],
#                 max_tokens=100,
#                 temperature=0.7,
#             )

#             content = response.choices[0].message.content
#             print(f"✅ 成功!")
#             print(f"   模型: gemini-2.5-flash")
#             print(f"   响应: {content[:100]}...")
#             return True

#         except Exception as e:
#             print(f"❌ 失败: {type(e).__name__}: {str(e)}")
#             return False

#     async def test_tool_calling(self) -> bool:
#         """测试2: 工具调用 (Planning Flow 关键功能)"""
#         print("\n" + "=" * 60)
#         print("测试 2: 工具调用 / Function Calling【Planning Flow 核心】")
#         print("=" * 60)
#         print("说明: Planning Flow 依赖此功能进行任务分解")
#         print()

#         # 定义一个模拟 PlanningTool 的函数
#         planning_tool = {
#             "type": "function",
#             "function": {
#                 "name": "planning",
#                 "description": "创建一个任务执行计划，将复杂任务分解为多个步骤",
#                 "parameters": {
#                     "type": "object",
#                     "properties": {
#                         "plan_id": {
#                             "type": "string",
#                             "description": "计划的唯一标识符",
#                         },
#                         "title": {"type": "string", "description": "计划的标题"},
#                         "steps": {
#                             "type": "array",
#                             "items": {"type": "string"},
#                             "description": "执行步骤列表，按顺序排列",
#                         },
#                     },
#                     "required": ["title", "steps"],
#                 },
#             },
#         }

#         try:
#             print("发送请求: 为'分析一个 CSV 文件并生成可视化图表'创建执行计划...")

#             response = await self.client.chat.completions.create(
#                 model="gemini-2.5-flash",
#                 messages=[
#                     {
#                         "role": "user",
#                         "content": "请为任务'分析一个 CSV 文件并生成可视化图表'创建一个执行计划，包含4-6个步骤",
#                     }
#                 ],
#                 tools=[planning_tool],
#                 tool_choice={"type": "function", "function": {"name": "planning"}},
#                 temperature=0.7,
#             )

#             # 检查是否返回了工具调用
#             message = response.choices[0].message

#             if message.tool_calls:
#                 tool_call = message.tool_calls[0]
#                 print(f"\n✅ 成功! Gemini 正确调用了工具")
#                 print(f"   工具名称: {tool_call.function.name}")

#                 # 解析参数
#                 if isinstance(tool_call.function.arguments, str):
#                     args = json.loads(tool_call.function.arguments)
#                 else:
#                     args = tool_call.function.arguments

#                 print(f"   计划标题: {args.get('title')}")
#                 print(f"   步骤数量: {len(args.get('steps', []))}")
#                 print(f"   执行步骤:")
#                 for i, step in enumerate(args.get("steps", []), 1):
#                     print(f"      {i}. {step}")

#                 print(f"\n🎉 Planning Flow 可以直接使用 gemini-2.5-flash!")
#                 return True
#             else:
#                 print(f"\n❌ 失败: 没有返回工具调用")
#                 if message.content:
#                     print(f"   模型返回的是文本响应: {message.content[:200]}...")
#                 print(f"\n⚠️  Planning Flow 可能需要适配")
#                 return False

#         except Exception as e:
#             print(f"\n❌ 失败: {type(e).__name__}")
#             print(f"   错误详情: {str(e)}")
#             print(f"\n⚠️  Planning Flow 需要适配或调整")
#             return False

#     async def test_image_generation(self) -> bool:
#         """测试3: 图像生成【探索正确调用方式】"""
#         print("\n" + "=" * 60)
#         print("测试 3: 图像生成功能探索")
#         print("=" * 60)
#         print("说明: 尝试找到正确的图像生成 API 调用方式")
#         print()

#         # 方式1: 尝试 OpenAI images.generate 接口
#         print("方式 1: 尝试 OpenAI images.generate 接口...")
#         try:
#             # 尝试不同的可能模型名
#             for model_name in ["imagen-3.0", "gemini-2.5-flash-image", "imagen"]:
#                 try:
#                     print(f"   尝试模型: {model_name}")
#                     response = await self.client.images.generate(
#                         model=model_name,
#                         prompt="a cute cartoon cat",
#                         n=1,
#                         size="1024x1024",
#                     )
#                     print(f"✅ 成功! 模型 {model_name} 可用")
#                     print(f"   图像 URL: {response.data[0].url[:100]}...")
#                     return True
#                 except Exception as e:
#                     print(f"   ✗ {model_name}: {str(e)[:80]}")
#         except Exception as e:
#             print(f"   ❌ OpenAI 格式不支持")

#         # 方式2: 检查是否有 imagen 模型在模型列表中
#         print("\n方式 2: 检查可用模型列表中是否有图像生成模型...")
#         if hasattr(self, "available_models"):
#             image_models = [
#                 m
#                 for m in self.available_models
#                 if "image" in m.lower() or "imagen" in m.lower()
#             ]
#             if image_models:
#                 print(f"   找到可能的图像模型: {image_models}")
#             else:
#                 print(f"   ✗ 未在模型列表中找到图像生成模型")

#         # 方式3: 尝试 Google Generative AI 原生 SDK（如果可用）
#         if self.has_native_client:
#             print("\n方式 3: 尝试 Google Generative AI 原生 SDK...")
#             try:
#                 # 列出所有模型，查找图像生成模型
#                 models = genai.list_models()
#                 image_models = [
#                     m
#                     for m in models
#                     if "image" in m.display_name.lower()
#                     or "imagen" in m.display_name.lower()
#                 ]

#                 if image_models:
#                     print(f"✅ 找到图像生成模型:")
#                     for model in image_models:
#                         print(f"   - {model.name}: {model.display_name}")
#                     print(f"\n💡 建议: 使用 google-generativeai SDK 调用图像生成")
#                     return True
#                 else:
#                     print(f"   ✗ 未找到专门的图像生成模型")
#             except Exception as e:
#                 print(f"   ❌ 原生 SDK 调用失败: {str(e)[:80]}")

#         print(f"\n⚠️  结论: 图像生成可能需要使用独立的 API")
#         print(f"   建议: 查看 Imagen 3 的专门文档")
#         print(f"   参考: https://ai.google.dev/gemini-api/docs/imagen")
#         return False

#     async def test_speech_generation(self) -> bool:
#         """测试4: 语音生成【探索正确调用方式】"""
#         print("\n" + "=" * 60)
#         print("测试 4: 语音生成/TTS 功能探索")
#         print("=" * 60)
#         print("说明: 尝试找到正确的语音生成 API 调用方式")
#         print()

#         # 方式1: 尝试 OpenAI audio.speech 接口
#         print("方式 1: 尝试 OpenAI audio.speech 接口...")
#         try:
#             for model_name in ["gemini-2.5-flash-preview-tts", "tts-1", "gemini-tts"]:
#                 try:
#                     print(f"   尝试模型: {model_name}")
#                     response = await self.client.audio.speech.create(
#                         model=model_name, voice="alloy", input="Hello, this is a test."
#                     )

#                     # 保存音频文件
#                     output_path = f"test_speech_{model_name}.mp3"
#                     with open(output_path, "wb") as f:
#                         f.write(response.content)

#                     print(f"✅ 成功! 模型 {model_name} 可用")
#                     print(f"   音频已保存到: {output_path}")
#                     return True

#                 except Exception as e:
#                     print(f"   ✗ {model_name}: {str(e)[:80]}")
#         except Exception as e:
#             print(f"   ❌ OpenAI 格式不支持")

#         # 方式2: 检查模型列表中是否有 TTS 模型
#         print("\n方式 2: 检查可用模型列表中是否有 TTS 模型...")
#         if hasattr(self, "available_models"):
#             tts_models = [
#                 m
#                 for m in self.available_models
#                 if "tts" in m.lower() or "speech" in m.lower() or "audio" in m.lower()
#             ]
#             if tts_models:
#                 print(f"   找到可能的 TTS 模型: {tts_models}")
#             else:
#                 print(f"   ✗ 未在模型列表中找到 TTS 模型")

#         # 方式3: 尝试 Google Generative AI 原生 SDK
#         if self.has_native_client:
#             print("\n方式 3: 尝试 Google Generative AI 原生 SDK...")
#             try:
#                 models = genai.list_models()
#                 audio_models = [
#                     m
#                     for m in models
#                     if "audio" in m.display_name.lower()
#                     or "speech" in m.display_name.lower()
#                     or "tts" in m.display_name.lower()
#                 ]

#                 if audio_models:
#                     print(f"✅ 找到语音相关模型:")
#                     for model in audio_models:
#                         print(f"   - {model.name}: {model.display_name}")
#                     print(f"\n💡 建议: 使用 google-generativeai SDK 或查看文档")
#                     return True
#                 else:
#                     print(f"   ✗ 未找到专门的 TTS 模型")
#             except Exception as e:
#                 print(f"   ❌ 原生 SDK 调用失败: {str(e)[:80]}")

#         print(f"\n⚠️  结论: 语音生成可能需要使用独立的 API")
#         print(f"   建议: 使用 Google Cloud Text-to-Speech API")
#         print(f"   或查看: https://ai.google.dev/gemini-api/docs/audio")
#         return False

#     async def run_all_tests(self):
#         """运行所有测试"""
#         print("\n" + "🔬" + "=" * 58 + "🔬")
#         print("  Gemini API 与 OpenAI 兼容性测试")
#         print("  参考: https://ai.google.dev/gemini-api/docs/openai")
#         print("🔬" + "=" * 58 + "🔬")

#         # 按顺序运行测试
#         results = {}

#         # 先列出模型，供后续测试使用
#         # results["列出模型"] = await self.test_list_models()

#         # 基本功能测试
#         results["基本对话"] = await self.test_basic_chat()

#         # # Planning Flow 关键测试
#         # results["工具调用 (Planning Flow)"] = await self.test_tool_calling()

#         # # 图像和语音生成探索
#         # results["图像生成探索"] = await self.test_image_generation()
#         # results["语音生成探索"] = await self.test_speech_generation()

#         # # 总结
#         # print("\n" + "=" * 60)
#         # print("📊 测试总结")
#         # print("=" * 60)

#         # for test_name, passed in results.items():
#         #     status = "✅ 通过" if passed else "❌ 未通过/需探索"
#         #     print(f"{test_name}: {status}")

#         # passed_count = sum(results.values())
#         # total_count = len(results)

#         # print(f"\n总计: {passed_count}/{total_count} 测试通过")

#         # # 给出详细建议
#         # print("\n" + "=" * 60)
#         # print("💡 实施建议")
#         # print("=" * 60)

#         # # Planning Flow 建议
#         # if results["工具调用 (Planning Flow)"]:
#         #     print("\n🎉 Planning Flow 优化 - 可以立即实施!")
#         #     print("   ✅ gemini-2.5-flash 完全支持 Function Calling")
#         #     print("   ✅ 与 OpenAI SDK 100% 兼容")
#         #     print("   📝 实施步骤:")
#         #     print("      1. 在 config.toml 中添加 [llm.planning] 配置")
#         #     print("      2. 设置 model = 'gemini-2.5-flash'")
#         #     print(
#         #         "      3. 设置 base_url = 'https://generativelanguage.googleapis.com/v1beta/openai/'"
#         #     )
#         #     print("      4. 无需修改代码逻辑")
#         # else:
#         #     print("\n⚠️  Planning Flow 优化 - 需要进一步调查")
#         #     print("   工具调用功能未通过测试")
#         #     print("   建议: 检查 API 密钥和网络连接")

#         # # 图像生成建议
#         # print(f"\n🖼️  图像生成 Agent")
#         # if results["图像生成探索"]:
#         #     print("   ✅ 找到可用的图像生成方式")
#         #     print("   查看测试输出获取具体模型名称和调用方式")
#         # else:
#         #     print("   ⚠️  需要使用专门的 API")
#         #     print("   建议方案:")
#         #     print("      - 使用 Imagen 3 API（独立端点）")
#         #     print("      - 参考: https://ai.google.dev/gemini-api/docs/imagen")
#         #     print("      - 或使用 Google Cloud Vertex AI")

#         # # 语音生成建议
#         # print(f"\n🔊 语音生成 Agent")
#         # if results["语音生成探索"]:
#         #     print("   ✅ 找到可用的语音生成方式")
#         #     print("   查看测试输出获取具体模型名称和调用方式")
#         # else:
#         #     print("   ⚠️  需要使用专门的 API")
#         #     print("   建议方案:")
#         #     print("      - 使用 Google Cloud Text-to-Speech API")
#         #     print("      - 参考: https://cloud.google.com/text-to-speech")
#         #     print(
#         #         "      - 或查看 Gemini 音频文档: https://ai.google.dev/gemini-api/docs/audio"
#         #     )

#         # print("\n" + "=" * 60)


# async def main():
#     """主函数"""
#     # 从环境变量或配置读取 API Key
#     api_key = os.getenv("GEMINI_API_KEY")

#     if not api_key:
#         print("=" * 60)
#         print("⚠️  请设置 GEMINI_API_KEY 环境变量")
#         print("=" * 60)
#         print("\n在 Windows PowerShell 中:")
#         print('  $env:GEMINI_API_KEY="your-api-key"')
#         print("\n在 Windows CMD 中:")
#         print("  set GEMINI_API_KEY=your-api-key")
#         print("\n在 Linux/Mac 中:")
#         print('  export GEMINI_API_KEY="your-api-key"')
#         print("\n或直接在代码中设置:")
#         print('  api_key = "your-api-key"')
#         print("=" * 60)

#         # 提示用户输入
#         api_key = input("\n请输入你的 Gemini API Key (或按 Enter 退出): ").strip()
#         if not api_key:
#             print("退出测试")
#             return

#     try:
#         tester = GeminiCompatibilityTester(api_key=api_key)
#         await tester.run_all_tests()

#     except Exception as e:
#         print(f"\n❌ 测试初始化失败: {type(e).__name__}: {str(e)}")
#         print("\n可能的原因:")
#         print("1. API Key 无效")
#         print("2. Base URL 配置错误")
#         print("3. 网络连接问题")
#         print("4. Gemini API 版本变更")


# if __name__ == "__main__":
#     asyncio.run(main())


from openai import OpenAI

client = OpenAI(
    api_key="AIzaSyC9QjzvGG5oSILZ0Y7l5vSXCTGId0ZEKu8",
    base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
)

response = client.chat.completions.create(
    model="gemini-2.5-flash",
    reasoning_effort="low",
    messages=[
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Explain to me how AI works"},
    ],
)

print(response.choices[0].message.content)
