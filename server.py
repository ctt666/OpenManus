import asyncio
import json
import os
import re
import threading
import time
import uuid
import webbrowser
from datetime import datetime
from functools import partial
from json import dumps
from pathlib import Path
from typing import Optional

import tomllib
from fastapi import Body, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app.agent.audio_generation import AudioGenerationAgent
from app.agent.data_analysis import DataAnalysis
from app.agent.flow_agent import FlowAgent
from app.agent.image_generation import ImageGenerationAgent
from app.agent.manus import Manus
from app.config import PROJECT_ROOT, config
from app.flow.flow_factory import FlowFactory, FlowType
from app.llm import LLM
from app.logger import logger
from app.schema import AgentState, Message

# 导入 AskHuman 工具
from app.tool.ask_human import AskHuman


def get_timestamp_ms() -> int:
    """获取毫秒级时间戳"""
    return int(time.time() * 1000)


def safe_json_dumps(obj):
    """安全的JSON序列化函数，处理不可序列化的对象"""

    def default_serializer(obj):
        if hasattr(obj, "__dict__"):
            return obj.__dict__
        elif hasattr(obj, "__str__"):
            return str(obj)
        else:
            return f"<{type(obj).__name__} object>"

    try:
        return json.dumps(obj, default=default_serializer, ensure_ascii=False)
    except Exception as e:
        # 如果序列化失败，返回错误信息
        return json.dumps(
            {"error": f"Serialization failed: {str(e)}"}, ensure_ascii=False
        )


app = FastAPI()

app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/assets", StaticFiles(directory="assets"), name="assets")
templates = Jinja2Templates(directory="templates")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class Task(BaseModel):
    id: str
    prompt: str
    created_at: datetime
    status: str
    session_id: Optional[str] = None
    chat_history: list = []
    steps: list = []

    def model_dump(self, *args, **kwargs):
        try:
            data = super().model_dump(*args, **kwargs)
            data["created_at"] = self.created_at.isoformat()
            # 确保 steps 字段是可序列化的
            if "steps" in data and data["steps"]:
                # 过滤掉可能包含不可序列化对象的步骤
                serializable_steps = []
                for step in data["steps"]:
                    if isinstance(step, dict):
                        # 只保留基本类型的数据
                        clean_step = {}
                        for key, value in step.items():
                            if (
                                isinstance(value, (str, int, float, bool, list, dict))
                                or value is None
                            ):
                                clean_step[key] = value
                        serializable_steps.append(clean_step)
                    elif isinstance(step, (str, int, float, bool)):
                        serializable_steps.append(step)
                data["steps"] = serializable_steps
            return data
        except Exception as e:
            # 如果序列化失败，返回一个简化的版本
            return {
                "id": self.id,
                "prompt": self.prompt,
                "created_at": self.created_at.isoformat(),
                "status": self.status,
                "session_id": self.session_id,
                "chat_history": self.chat_history,
                "steps": [],  # 清空可能有问题的 steps
            }


class TaskManager:
    def __init__(self):
        self.tasks = {}
        self.queues = {}
        self.sessions = {}  # 添加会话管理
        self.interactions = {}  # 新增：存储交互状态
        self.ask_human_tools = {}  # 新增：存储 ask_human 工具实例
        self.running_tasks = {}  # 新增：存储正在运行的任务

    def create_task(
        self, prompt: str, session_id: str = None, chat_history: list = None
    ) -> Task:
        task_id = str(uuid.uuid4())
        task = Task(
            id=task_id,
            prompt=prompt,
            created_at=datetime.now(),
            status="pending",
            session_id=session_id,
            chat_history=chat_history or [],
        )
        self.tasks[task_id] = task
        self.queues[task_id] = asyncio.Queue()

        # 更新会话历史
        if session_id:
            if session_id not in self.sessions:
                self.sessions[session_id] = []
            self.sessions[session_id].append(task_id)

        return task

    async def update_task_step(
        self, task_id: str, step: int, result: str, step_type: str = "step"
    ):
        if task_id in self.tasks:
            task = self.tasks[task_id]
            task.steps.append({"step": step, "result": result, "type": step_type})
            # 添加时间戳到事件数据
            timestamp = get_timestamp_ms()
            # 调试日志
            # if step_type == "stream_complete":
            # logger.debug(
            #     f"📤 [DEBUG] update_task_step被调用，type={step_type}, result长度={len(result)}"
            # )
            await self.queues[task_id].put(
                {
                    "type": step_type,
                    "step": step,
                    "result": result,
                    "timestamp": timestamp,
                }
            )
            await self.queues[task_id].put(
                {
                    "type": "status",
                    "status": task.status,
                    "steps": task.steps,
                    "timestamp": timestamp,
                }
            )

    async def complete_task(self, task_id: str, result: str):
        if task_id in self.tasks:
            task = self.tasks[task_id]
            task.status = "completed"
            timestamp = get_timestamp_ms()
            await self.queues[task_id].put(
                {
                    "type": "status",
                    "status": task.status,
                    "steps": task.steps,
                    "timestamp": timestamp,
                }
            )
            await self.queues[task_id].put(
                {"type": "complete", "result": result, "timestamp": timestamp}
            )

    async def fail_task(self, task_id: str, error: str):
        if task_id in self.tasks:
            task = self.tasks[task_id]
            task.status = f"failed: {error}"
            timestamp = get_timestamp_ms()
            await self.queues[task_id].put(
                {"type": "error", "message": error, "timestamp": timestamp}
            )

    # 新增：处理交互回答
    async def handle_interaction(self, task_id: str, user_response: str):
        if task_id in self.tasks:
            # 存储用户回答
            if task_id not in self.interactions:
                self.interactions[task_id] = {}
            self.interactions[task_id]["user_response"] = user_response
            self.interactions[task_id]["responded"] = True

            # 如果有 ask_human 工具在等待，设置响应并继续执行
            if task_id in self.ask_human_tools:
                ask_human_tool = self.ask_human_tools[task_id]
                await ask_human_tool.set_user_response(user_response)
                # 清除工具引用
                del self.ask_human_tools[task_id]

            # 通知任务继续执行
            timestamp = get_timestamp_ms()
            await self.queues[task_id].put(
                {
                    "type": "interaction_response",
                    "response": user_response,
                    "timestamp": timestamp,
                }
            )
            return True
        return False

    # 新增：注册 ask_human 工具
    def register_ask_human_tool(self, task_id: str, tool):
        self.ask_human_tools[task_id] = tool

    # 新增：注册正在运行的任务
    def register_running_task(self, task_id: str, task):
        self.running_tasks[task_id] = task

    # 新增：终止任务
    async def terminate_task(self, task_id: str):
        if task_id in self.running_tasks:
            task = self.running_tasks[task_id]
            task.cancel()
            del self.running_tasks[task_id]

        if task_id in self.tasks:
            task = self.tasks[task_id]
            task.status = "terminated"
            timestamp = get_timestamp_ms()
            await self.queues[task_id].put(
                {
                    "type": "status",
                    "status": task.status,
                    "steps": task.steps,
                    "timestamp": timestamp,
                }
            )
            await self.queues[task_id].put(
                {
                    "type": "terminated",
                    "message": "Task terminated by user",
                    "timestamp": timestamp,
                }
            )

        # 清理相关资源
        if task_id in self.interactions:
            del self.interactions[task_id]
        if task_id in self.ask_human_tools:
            del self.ask_human_tools[task_id]

        return True

    def get_session_history(self) -> list:
        """获取指定会话的历史记录"""
        session_tasks = []
        for session_id in self.sessions:
            for task_id in self.sessions[session_id]:
                if task_id in self.tasks:
                    task = self.tasks[task_id]
                    session_tasks.append(
                        {
                            "task_id": task_id,
                            "prompt": task.prompt,
                            "status": task.status,
                            "created_at": task.created_at.isoformat(),
                            "chat_history": task.chat_history,
                        }
                    )

        return session_tasks


task_manager = TaskManager()


# 新增：FlowManager（与 TaskManager 相同接口，用于 flow 流程）
class FlowManager:
    def __init__(self):
        self.flows = {}
        self.queues = {}
        self.sessions = {}
        self.interactions = {}  # 新增：存储交互状态
        self.ask_human_tools = {}  # 新增：存储 ask_human 工具实例
        self.running_flows = {}  # 新增：存储正在运行的流程

    def create_flow(
        self, prompt: str, session_id: str = None, chat_history: list = None
    ) -> Task:
        flow_id = str(uuid.uuid4())
        flow_task = Task(
            id=flow_id,
            prompt=prompt,
            created_at=datetime.now(),
            status="pending",
            session_id=session_id,
            chat_history=chat_history or [],
        )
        self.flows[flow_id] = flow_task
        self.queues[flow_id] = asyncio.Queue()
        if session_id:
            if session_id not in self.sessions:
                self.sessions[session_id] = []
            self.sessions[session_id].append(flow_id)
        return flow_task

    async def update_flow_step(
        self, flow_id: str, step: int, result: str, step_type: str = "step"
    ):
        if flow_id in self.flows:
            task = self.flows[flow_id]
            task.steps.append({"step": step, "result": result, "type": step_type})
            await self.queues[flow_id].put(
                {"type": step_type, "step": step, "result": result}
            )
            await self.queues[flow_id].put(
                {"type": "status", "status": task.status, "steps": task.steps}
            )

    async def complete_flow(self, flow_id: str, result: str):
        if flow_id in self.flows:
            task = self.flows[flow_id]
            task.status = "completed"
            await self.queues[flow_id].put(
                {"type": "status", "status": task.status, "steps": task.steps}
            )
            await self.queues[flow_id].put({"type": "complete", "result": result})

    async def fail_flow(self, flow_id: str, error: str):
        if flow_id in self.flows:
            self.flows[flow_id].status = f"failed: {error}"
            await self.queues[flow_id].put({"type": "error", "message": error})

    # 新增：处理交互回答
    async def handle_interaction(self, flow_id: str, user_response: str):
        if flow_id in self.flows:
            # 存储用户回答
            if flow_id not in self.interactions:
                self.interactions[flow_id] = {}
            self.interactions[flow_id]["user_response"] = user_response
            self.interactions[flow_id]["responded"] = True

            # 新增：调试日志
            logger.info(f"Flow {flow_id}: User response received: {user_response}")
            logger.info(
                f"Flow {flow_id}: Interactions state: {self.interactions[flow_id]}"
            )

            # 如果有 ask_human 工具在等待，设置响应并继续执行
            if flow_id in self.ask_human_tools:
                ask_human_tool = self.ask_human_tools[flow_id]
                await ask_human_tool.set_user_response(user_response)
                # 清除工具引用
                del self.ask_human_tools[flow_id]
                logger.info(
                    f"Flow {flow_id}: Ask_human tool response set, tool reference cleared"
                )
            else:
                logger.info(
                    f"Flow {flow_id}: No ask_human tool waiting, but interaction state set"
                )

            # 通知流程继续执行
            timestamp = get_timestamp_ms()
            await self.queues[flow_id].put(
                {
                    "type": "interaction_response",
                    "response": user_response,
                    "timestamp": timestamp,
                }
            )
            logger.info(f"Flow {flow_id}: Interaction response event queued")
            return True
        else:
            logger.error(f"Flow {flow_id}: Flow not found in handle_interaction")
            return False

    # 新增：注册 ask_human 工具
    def register_ask_human_tool(self, flow_id: str, tool):
        self.ask_human_tools[flow_id] = tool

    # 新增：注册正在运行的流程
    def register_running_flow(self, flow_id: str, flow):
        self.running_flows[flow_id] = flow

    # 新增：终止流程
    async def terminate_flow(self, flow_id: str):
        if flow_id in self.running_flows:
            flow = self.running_flows[flow_id]
            flow.cancel()
            del self.running_flows[flow_id]

        if flow_id in self.flows:
            flow = self.flows[flow_id]
            flow.status = "terminated"
            timestamp = get_timestamp_ms()
            await self.queues[flow_id].put(
                {
                    "type": "status",
                    "status": flow.status,
                    "steps": flow.steps,
                    "timestamp": timestamp,
                }
            )
            await self.queues[flow_id].put(
                {
                    "type": "terminated",
                    "message": "Flow terminated by user",
                    "timestamp": timestamp,
                }
            )

        # 清理相关资源
        if flow_id in self.interactions:
            del self.interactions[flow_id]
        if flow_id in self.ask_human_tools:
            del self.ask_human_tools[flow_id]

        return True

    def get_session_history(self) -> list:
        session_flows = []

        for session_id in self.sessions:
            for fid in self.sessions[session_id]:
                if fid in self.flows:
                    t = self.flows[fid]
                    session_flows.append(
                        {
                            "flow_id": fid,
                            "prompt": t.prompt,
                            "status": t.status,
                            "created_at": t.created_at.isoformat(),
                            "chat_history": t.chat_history,
                        }
                    )
        return session_flows


flow_manager = FlowManager()


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/test-static-files", response_class=HTMLResponse)
async def test_static_files():
    """静态文件测试页面"""
    with open("test_static_files.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


@app.get("/test-task-logo", response_class=HTMLResponse)
async def test_task_logo():
    """任务页面Logo测试页面"""
    with open("test_task_logo.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


@app.get("/test-message-structure", response_class=HTMLResponse)
async def test_message_structure():
    """消息结构测试页面"""
    with open("test_message_structure.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


@app.get("/health")
async def health_check():
    """健康检查端点"""
    import os

    return {
        "status": "ok",
        "message": "服务器运行正常",
        "static_files": {
            "assets_logo_exists": os.path.exists("assets/logo.jpg"),
            "static_css_exists": os.path.exists("static/manus-main.css"),
            "static_js_exists": os.path.exists("static/manus-main.js"),
        },
        "routes": {
            "main": "/",
            "static_test": "/test-static-files",
            "logo_test": "/test-task-logo",
            "health": "/health",
        },
    }


@app.get("/download")
async def download_file(file_path: str):
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(file_path, filename=os.path.basename(file_path))


@app.post("/upload-file")
async def upload_file(file: UploadFile = File(...)):
    """
    上传文件到临时目录，返回绝对路径

    Args:
        file: 上传的文件

    Returns:
        包含文件路径的JSON响应

    Raises:
        HTTPException: 文件验证失败时
    """
    from app.utils.file_handler import FileHandler

    try:
        # 创建tmp目录
        tmp_dir = PROJECT_ROOT / "tmp"
        tmp_dir.mkdir(exist_ok=True)

        # 验证文件大小
        file_size = 0
        content = await file.read()
        file_size = len(content)

        if file_size > FileHandler.MAX_FILE_SIZE:
            size_mb = file_size / (1024 * 1024)
            limit_mb = FileHandler.MAX_FILE_SIZE / (1024 * 1024)
            raise HTTPException(
                status_code=400,
                detail=f"文件大小 ({size_mb:.2f}MB) 超过限制 ({limit_mb}MB)",
            )

        # 生成唯一文件名（保留原始扩展名）
        original_filename = file.filename or "upload"
        file_ext = Path(original_filename).suffix
        unique_filename = f"{uuid.uuid4().hex}{file_ext}"
        file_path = tmp_dir / unique_filename

        # 保存文件
        with open(file_path, "wb") as f:
            f.write(content)

        # 返回绝对路径
        absolute_path = str(file_path.absolute())
        logger.info(f"📤 文件上传成功: {original_filename} -> {absolute_path}")

        return {
            "success": True,
            "file_path": absolute_path,
            "original_filename": original_filename,
            "file_size": file_size,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"文件上传失败: {e}")
        raise HTTPException(status_code=500, detail=f"文件上传失败: {str(e)}")


# 客户端task交互：
#  1. 如果task_id不存在，则创建task
#  2. 如果task_id存在，则处理交互
# 接口参数：
#  1. prompt: 提示词
#  2. session_id: 会话ID
#  3. chat_history: 聊天历史
#  4. task_id: 任务ID
@app.post("/task")
async def create_task(request_data: dict = Body(...)):
    """创建或处理task交互"""
    prompt = request_data.get("prompt")
    session_id = request_data.get("session_id")
    chat_history = request_data.get("chat_history", [])
    task_id = request_data.get("task_id")
    file_paths = request_data.get("file_paths", [])  # 新增：文件路径列表

    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt is required")

    if (
        task_id
        and task_id in task_manager.tasks
        and task_manager.tasks[task_id].status != "completed"
    ):
        success = await task_manager.handle_interaction(task_id, prompt)
        if not success:
            raise HTTPException(status_code=404, detail="Task not found")
        else:
            return {"status": "success", "message": "Interaction response received"}

    if not task_id:
        task_id = task_manager.create_task(prompt, session_id, chat_history).id

    asyncio.create_task(run_task(task_id, prompt, session_id, chat_history, file_paths))
    return {"task_id": task_id}


async def run_task(
    task_id: str,
    prompt: str,
    session_id: str = None,
    chat_history: list = None,
    file_paths: list = None,  # 新增：文件路径列表
):
    try:
        task_manager.tasks[task_id].status = "running"

        # 创建任务协程并注册到TaskManager
        task = asyncio.current_task()
        task_manager.register_running_task(task_id, task)

        # ============ 新增：处理多模态文件输入 ============
        from app.utils.file_handler import FileHandler

        classified_paths = []
        has_multimodal_input = False

        if file_paths:
            try:
                # 验证文件路径
                validated_paths = FileHandler.validate_file_paths(file_paths)
                has_multimodal_input = len(validated_paths) > 0
                logger.info(f"📦 Task {task_id}: 验证了 {len(validated_paths)} 个文件")
                classified_paths = FileHandler.classify_file_paths(validated_paths)
            except (ValueError, FileNotFoundError) as e:
                logger.error(f"❌ 文件验证失败: {e}")
                await task_manager.update_task_step(
                    task_id, 0, f"文件验证失败: {str(e)}", "error"
                )
                raise HTTPException(status_code=400, detail=str(e))

        # ============ 选择合适的Agent ============
        if has_multimodal_input:
            # 使用MultimodalAgent处理多模态输入
            from app.agent.multimodal import MultimodalAgent

            agent = await MultimodalAgent.create()
            logger.info(f"✨ Task {task_id}: 使用 MultimodalAgent 处理多模态输入")
        else:
            # 使用Manus Agent处理纯文本输入
            agent = await Manus.create()
            logger.info(f"✨ Task {task_id}: 使用 Manus Agent 处理文本输入")

        async def on_think(thought):
            await task_manager.update_task_step(task_id, 0, thought, "think")

        async def on_tool_execute(tool, input):
            await task_manager.update_task_step(
                task_id, 0, f"Executing tool: {tool}\nInput: {input}", "tool"
            )

            # 特殊处理 ask_human 工具
            if tool == "ask_human":
                inquire = input.get("inquire", "")
                # await task_manager.update_task_step(
                #     task_id, 0, f"Human interaction required: {inquire}", "interaction"
                # )

                # 等待用户响应
                response_event = asyncio.Event()

                async def wait_for_response():
                    while True:
                        if (
                            task_id in task_manager.interactions
                            and task_manager.interactions[task_id].get("responded")
                        ):
                            response_event.set()
                            break
                        await asyncio.sleep(0.1)

                wait_task = asyncio.create_task(wait_for_response())
                await response_event.wait()
                wait_task.cancel()

                await task_manager.update_task_step(
                    task_id, 0, f"User responded, continuing execution...", "log"
                )

        async def on_action(action):
            await task_manager.update_task_step(
                task_id, 0, f"Executing action: {action}", "act"
            )

            # 注意：不再在这里处理 INTERACTION_REQUIRED 标记
            # 因为交互已经在 on_tool_execute 中处理了

        async def on_run(step, result):
            await task_manager.update_task_step(task_id, step, result, "run")

        class SSELogHandler:
            def __init__(self, task_id):
                self.task_id = task_id
                self.pending_tasks = []  # 跟踪所有待处理的任务

            def __call__(self, message):
                """同步方法，被 loguru 调用（修复：loguru 不支持异步 handler）"""
                # Extract - Subsequent Content
                cleaned_message = re.sub(r"^.*? - ", "", message)

                event_type = "log"
                if "✨ Manus's thoughts:" or "Act content:" in cleaned_message:
                    event_type = "think"
                # elif "🛠 Manus selected" in cleaned_message:
                #     event_type = "tool"
                # elif "🎯 Tool" in cleaned_message:
                #     event_type = "act"
                elif "📝 Oops!" in cleaned_message:
                    event_type = "error"
                # elif "🏁 Summarization completed" in cleaned_message:
                #     event_type = "complete"
                # 新增：检测ask_human工具的执行结果
                elif "Tool 'ask_human' completed its mission!" in cleaned_message:
                    event_type = "interaction"
                    cleaned_message = cleaned_message.split(
                        "Tool 'ask_human' completed its mission! Result: INTERACTION_REQUIRED:"
                    )[1].strip()

                # 异步发送事件（带异常处理）
                async def safe_update():
                    try:
                        await task_manager.update_task_step(
                            self.task_id, 0, cleaned_message, event_type
                        )
                    except Exception as e:
                        print(f"❌ 事件发送失败 [{event_type}]: {e}")

                try:
                    loop = asyncio.get_running_loop()
                    task = loop.create_task(safe_update())
                    self.pending_tasks.append(task)
                except RuntimeError:
                    print(f"⚠️ 无法发送事件，没有运行中的事件循环")

            async def wait_all(self):
                """等待所有待处理的任务完成"""
                if self.pending_tasks:
                    await asyncio.gather(*self.pending_tasks, return_exceptions=True)
                    self.pending_tasks.clear()

        sse_handler = SSELogHandler(task_id)
        hwnd = logger.add(sse_handler)

        # 构建包含聊天历史的完整提示
        full_prompt = prompt
        if chat_history and len(chat_history) > 0:
            # 将聊天历史格式化为上下文
            context = "\n".join(
                [
                    f"{'User' if msg['role'] == 'user' else 'Assistant'}: {msg['content']}"
                    for msg in chat_history[-10:]  # 只保留最近10条消息作为上下文
                ]
            )
            full_prompt = (
                f"Previous conversation:\n{context}\n\nCurrent question: {prompt}"
            )

        # 注册 ask_human 工具到 TaskManager
        ask_human_tool = AskHuman()
        task_manager.register_ask_human_tool(task_id, ask_human_tool)

        # 定义流式回调函数 - 实时推送总结的chunk到前端
        async def stream_summary_callback(chunk: str):
            """推送summary的每个chunk到SSE事件流"""
            # logger.debug(f"📤 [DEBUG] 流式回调被触发，chunk: {chunk}")
            await task_manager.update_task_step(
                task_id, 0, chunk, "stream_complete"  # 新事件类型：流式complete
            )
            # logger.debug(f"📤 [DEBUG] stream_complete事件已发送到队列")

        # 运行 agent（传入流式回调）
        # full_prompt 已经包含了聊天历史，作为 request 传入
        result = await agent.run(
            request=full_prompt,
            stream_callback=stream_summary_callback,
            multimodal_paths=classified_paths,
            context=None,  # server.py 中暂时不传入 context
        )

        # 检查是否因为 ask_human 工具而暂停了执行
        while "INTERACTION_REQUIRED:" in result:
            # 提取询问内容
            inquire_parts = result.split("INTERACTION_REQUIRED:")
            if len(inquire_parts) > 1:
                inquire = inquire_parts[1].strip()
                # 如果有多个 INTERACTION_REQUIRED，取最后一个
                if len(inquire_parts) > 2:
                    inquire = inquire_parts[-1].strip()

                # await task_manager.update_task_step(
                #     task_id, 0, f"Human interaction required: {inquire}", "interaction"
                # )

                # 等待用户响应
                response_event = asyncio.Event()

                async def wait_for_response():
                    while True:
                        # 检查任务是否被取消
                        if task_id not in task_manager.running_tasks:
                            response_event.set()
                            break
                        if (
                            task_id in task_manager.interactions
                            and task_manager.interactions[task_id].get("responded")
                        ):
                            response_event.set()
                            break
                        await asyncio.sleep(0.1)

                wait_task = asyncio.create_task(wait_for_response())
                await response_event.wait()
                wait_task.cancel()

                # 检查任务是否被取消
                if task_id not in task_manager.running_tasks:
                    logger.info("Task was terminated during interaction wait")
                    return

                # 继续执行
                await task_manager.update_task_step(
                    task_id, 0, f"User responded, continuing execution...", "log"
                )

                # 获取用户响应并添加到 agent 的记忆中
                user_response = task_manager.interactions[task_id]["user_response"]

                # 重置交互状态
                task_manager.interactions[task_id]["responded"] = False

                # 重置 agent 状态并继续执行
                agent.state = AgentState.IDLE

                # 添加用户响应到 agent 的记忆
                from app.schema import Message

                agent.memory.add_message(
                    Message.user_message(f"用户回答: {user_response}")
                )

                # 继续执行 agent，从中断的地方继续（传入流式回调）
                result = await agent.run(
                    request=prompt,
                    stream_callback=stream_summary_callback,
                    multimodal_paths=classified_paths,
                    context=None,  # server.py 中暂时不传入 context
                )
            else:
                # 如果无法提取询问内容，退出循环
                break

        # ============ 提取多模态结果（如果有） ============
        try:
            from app.utils.file_handler import FileHandler

            multimodal_outputs = FileHandler.extract_multimodal_outputs(
                agent.memory.messages
            )

            if multimodal_outputs:
                await task_manager.update_task_step(
                    task_id, 0, multimodal_outputs, "multimodal_result"
                )
                logger.info(
                    f"📦 多模态结果: "
                    f"{len(multimodal_outputs.get('images', []))} 图像, "
                    f"{len(multimodal_outputs.get('audios', []))} 音频"
                )
        except Exception as e:
            logger.warning(f"提取多模态结果失败: {e}")

        # 清理资源
        await agent.cleanup()

        # 等待所有日志事件发送完成
        await sse_handler.wait_all()
        logger.remove(hwnd)

        # 发送完成标记（result为空，因为内容已通过stream_complete流式发送）
        await task_manager.complete_task(task_id, "")
    except asyncio.CancelledError:
        # 任务被取消
        await task_manager.update_task_step(
            task_id, 0, "Task was terminated by user", "terminated"
        )
        await task_manager.fail_task(task_id, "Task terminated by user")
    except Exception as e:
        await task_manager.fail_task(task_id, str(e))
    finally:
        # 清理任务注册
        if task_id in task_manager.running_tasks:
            del task_manager.running_tasks[task_id]


# 客户端flow交互：
#  1. 如果flow_id不存在，则创建flow
#  2. 如果flow_id存在，则处理交互
# 接口参数：
#  1. prompt: 提示词
#  2. session_id: 会话ID
#  3. chat_history: 聊天历史
#  4. flow_id: 流程ID
# 接口返回：
#  1. flow_id: 流程ID
#  2. status: 状态
#  3. message: 消息
@app.post("/flow")
async def create_flow(request_data: dict = Body(...)):
    prompt = request_data.get("prompt")
    session_id = request_data.get("session_id")
    chat_history = request_data.get("chat_history", [])
    flow_id = request_data.get("flow_id")
    file_paths = request_data.get("file_paths", [])  # 新增：文件路径列表

    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt is required")

    if (
        flow_id
        and flow_id in flow_manager.flows
        and flow_manager.flows[flow_id].status != "completed"
    ):
        success = await flow_manager.handle_interaction(flow_id, prompt)
        if not success:
            raise HTTPException(status_code=404, detail="Flow not found")
        else:
            return {"status": "success", "message": "Interaction response received"}

    flow_task = flow_manager.create_flow(prompt, session_id, chat_history)
    asyncio.create_task(
        run_flow_task(flow_task.id, prompt, session_id, chat_history, file_paths)
    )
    return {"flow_id": flow_task.id}


# 客户端adaptive交互：
#  1. 如果task_id不存在，则创建task
#  2. 如果task_id存在，则处理交互
# 接口参数：
#  1. prompt: 提示词
#  2. session_id: 会话ID
#  3. chat_history: 聊天历史
#  4. task_id: 任务ID
@app.post("/adaptive")
async def create_adpative_task(request_data: dict = Body(...)):
    """创建或处理task交互"""
    prompt = request_data.get("prompt")
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt is required")

    analyze_prompt = f"Analyze the user request: {prompt}. Judge its complexity. If it requires making a plan and then executing multiple steps, reply with 'flow', otherwise reply with 'task'."
    user_message = Message.user_message(analyze_prompt)
    # 初始化一个llm，然后调用llm.py中的ask执行prompt
    llm = LLM()
    judge = await llm.ask([user_message])

    if judge and "flow" in judge.lower():
        # 需要流程（flow），走流程创建接口
        logger.info(f"🔍 用户请求需要流程（flow），走流程创建接口")
        return await create_flow(request_data)

    else:
        # 默认为普通任务（task）
        logger.info(f"🔍 用户请求为普通任务（task），走任务创建接口")
        return await create_task(request_data)


async def run_flow_task(
    flow_id: str,
    prompt: str,
    session_id: str = None,
    chat_history: list = None,
    file_paths: list = None,  # 新增：文件路径列表
):
    try:
        flow_manager.flows[flow_id].status = "running"

        # 创建流程协程并注册到FlowManager
        current_task = asyncio.current_task()
        flow_manager.register_running_flow(flow_id, current_task)

        # ============ 新增：处理多模态文件输入 ============
        from app.utils.file_handler import FileHandler

        classified_paths = []
        has_multimodal_input = False

        if file_paths:
            try:
                # 验证文件路径
                validated_paths = FileHandler.validate_file_paths(file_paths)
                has_multimodal_input = len(validated_paths) > 0
                classified_paths = FileHandler.classify_file_paths(validated_paths)
                logger.info(f"📦 Flow {flow_id}: 验证了 {len(validated_paths)} 个文件")
            except (ValueError, FileNotFoundError) as e:
                logger.error(f"❌ 文件验证失败: {e}")
                await flow_manager.update_flow_step(
                    flow_id, 0, f"文件验证失败: {str(e)}", "error"
                )
                raise HTTPException(status_code=400, detail=str(e))

        # 组装 agents 与 flow
        agents = {
            "flow": await FlowAgent().create(),
            "imagegeneration": ImageGenerationAgent(),
            "audiogeneration": AudioGenerationAgent(),
        }

        # 创建PlanningFlow（会使用llm.planning配置的qwen3-omni-flash）
        flow = FlowFactory.create_flow(flow_type=FlowType.PLANNING, agents=agents)

        # 注册 ask_human 工具到 FlowManager
        ask_human_tool = AskHuman()
        flow_manager.register_ask_human_tool(flow_id, ask_human_tool)

        # 日志转发：loguru → SSE
        class FlowSSELoguruHandler:
            def __init__(self, fid):
                self.flow_id = fid
                self.pending_tasks = []  # 跟踪所有待处理的任务

            def __call__(self, message):
                """同步方法，被 loguru 调用（修复：loguru 不支持异步 handler）"""
                cleaned_message = re.sub(r"^.*? - ", "", message)
                event_type = "log"

                if "Plan creation result:" in cleaned_message:
                    event_type = "plan"
                elif "Start executing step:" in cleaned_message:
                    cleaned_message = cleaned_message.split("Start executing step:")[
                        1
                    ].strip()
                    event_type = "step_start"
                elif "Finish executing step:" in cleaned_message:
                    event_type = "step_finish"
                    cleaned_message = cleaned_message.split("Finish executing step:")[
                        1
                    ].strip()
                elif "Act content:" in cleaned_message:
                    event_type = "step"
                elif "Step result:" in cleaned_message:
                    cleaned_message = cleaned_message.split("Step result:")[1].strip()
                    event_type = "step"
                elif "🔧 Activating tool:" in cleaned_message:
                    event_type = "step"
                elif "📝 Oops!" in cleaned_message:
                    event_type = "error"
                # elif "Flow summary result:" in cleaned_message:
                #     event_type = "summary"
                # 检测ask_human工具的执行结果
                elif (
                    "Tool 'ask_human' completed its mission! Result: INTERACTION_REQUIRED:"
                    in cleaned_message
                ):
                    cleaned_message = cleaned_message.split(
                        "Tool 'ask_human' completed its mission! Result: INTERACTION_REQUIRED:"
                    )[1].strip()
                    event_type = "interaction"

                print(f"🔍 最终事件类型: {event_type}, 内容: {cleaned_message}")

                # 异步发送事件（带异常处理）
                async def safe_update():
                    try:
                        await flow_manager.update_flow_step(
                            self.flow_id, 0, cleaned_message, event_type
                        )
                    except Exception as e:
                        print(f"❌ Flow事件发送失败 [{event_type}]: {e}")

                try:
                    loop = asyncio.get_running_loop()
                    task = loop.create_task(safe_update())
                    self.pending_tasks.append(task)
                except RuntimeError:
                    print(f"⚠️ 无法发送Flow事件，没有运行中的事件循环")

            async def wait_all(self):
                """等待所有待处理的任务完成"""
                if self.pending_tasks:
                    await asyncio.gather(*self.pending_tasks, return_exceptions=True)
                    self.pending_tasks.clear()

        loguru_handler = FlowSSELoguruHandler(flow_id)
        loguru_hwnd = logger.add(loguru_handler)

        full_prompt = prompt
        if chat_history and len(chat_history) > 0:
            context = "\n".join(
                [
                    f"{'User' if msg['role'] == 'user' else 'Assistant'}: {msg['content']}"
                    for msg in chat_history[-10:]
                ]
            )
            full_prompt = (
                f"Previous conversation:\n{context}\n\nCurrent question: {prompt}"
            )

        # 定义流式回调函数 - 实时推送总结的chunk到前端
        async def stream_summary_callback(chunk: str):
            """推送summary的每个chunk到SSE事件流"""
            await flow_manager.update_flow_step(
                flow_id, 0, chunk, "stream_complete"  # 新事件类型：流式complete
            )

        # 执行 flow（传入流式回调）
        result = await asyncio.wait_for(
            flow.execute(
                full_prompt,
                stream_callback=stream_summary_callback,
                multimodal_paths=classified_paths,
            ),
            timeout=3600,
        )

        # 处理ask_human - 检查是否因为 ask_human 工具而暂停了执行
        while result and "INTERACTION_REQUIRED:" in result:
            # 提取询问内容
            inquire_parts = result.split("INTERACTION_REQUIRED:")
            if len(inquire_parts) > 1:
                inquire = inquire_parts[1].strip()
                # 如果有多个 INTERACTION_REQUIRED，取最后一个
                if len(inquire_parts) > 2:
                    inquire = inquire_parts[-1].strip()

                # await flow_manager.update_flow_step(
                #     flow_id, 0, f"Human interaction required: {inquire}", "interaction"
                # )

                # 等待用户响应
                response_event = asyncio.Event()

                async def wait_for_response():
                    while True:
                        # 检查流程是否被取消
                        if flow_id not in flow_manager.running_flows:
                            response_event.set()
                            break
                        if (
                            flow_id in flow_manager.interactions
                            and flow_manager.interactions[flow_id].get("responded")
                        ):
                            response_event.set()
                            break
                        await asyncio.sleep(0.1)

                wait_task = asyncio.create_task(wait_for_response())
                await response_event.wait()
                wait_task.cancel()

                # 检查流程是否被取消
                if flow_id not in flow_manager.running_flows:
                    logger.info("Flow was terminated during interaction wait")
                    return

                # 继续执行
                await flow_manager.update_flow_step(
                    flow_id, 0, f"User responded, continuing execution...", "log"
                )

                # 获取用户响应
                user_response = flow_manager.interactions[flow_id]["user_response"]

                # 重置交互状态
                flow_manager.interactions[flow_id]["responded"] = False

                # 修复：创建更智能的继续提示，避免重新执行整个流程
                # 分析当前结果，提取需要继续的部分
                if "INTERACTION_REQUIRED:" in result:
                    # 提取INTERACTION_REQUIRED之前的内容作为上下文
                    before_interaction = result.split("INTERACTION_REQUIRED:")[
                        1
                    ].strip()
                    continue_prompt = f"Ask Human: {before_interaction}\nUser response: {user_response}"
                else:
                    # 如果没有INTERACTION_REQUIRED标记，使用默认提示
                    continue_prompt = f"User response: {user_response}\nPlease continue with the task."

                logger.info(
                    f"Continuing flow execution with user response: {user_response}"
                )

                # 修复：继续执行流程，而不是重新执行（传入流式回调）
                result = await flow.execute(
                    continue_prompt,
                    stream_callback=stream_summary_callback,
                    multimodal_paths=classified_paths,
                )

                # 重要：检查是否还有INTERACTION_REQUIRED，如果有则继续循环
                if result and "INTERACTION_REQUIRED:" in result:
                    logger.info("Flow still requires interaction, continuing wait loop")
                    continue
                else:
                    logger.info(
                        "Flow execution completed or no more interactions required"
                    )
                    break
            else:
                # 如果无法提取询问内容，退出循环
                logger.warning("Could not extract interaction content, breaking loop")
                break

        # 清理资源
        for agent in agents.values():
            await agent.cleanup()

        # 等待所有日志事件发送完成
        await loguru_handler.wait_all()
        logger.remove(loguru_hwnd)

        # 发送完成标记（result为空，因为内容已通过stream_complete流式发送）
        await flow_manager.complete_flow(flow_id, "")
    except asyncio.CancelledError:
        # 流程被取消
        await flow_manager.update_flow_step(
            flow_id, 0, "Flow was terminated by user", "terminated"
        )
        await flow_manager.fail_flow(flow_id, "Flow terminated by user")
    except asyncio.TimeoutError:
        await flow_manager.fail_flow(
            flow_id, "Request processing timed out after 1 hour"
        )
    except Exception as e:
        logger.error(f"Error in run_flow_task: {str(e)}")
        await flow_manager.fail_flow(flow_id, str(e))
    finally:
        # 清理流程注册
        if flow_id in flow_manager.running_flows:
            del flow_manager.running_flows[flow_id]


@app.get("/flows/{flow_id}/events")
async def flow_events(flow_id: str):
    async def event_generator():
        if flow_id not in flow_manager.queues:
            yield f"event: error\ndata: {safe_json_dumps({'message': 'Flow not found'})}\n\n"
            return
        queue = flow_manager.queues[flow_id]
        task = flow_manager.flows.get(flow_id)
        if task:
            # 使用安全的序列化函数
            safe_steps = []
            for step in task.steps:
                if isinstance(step, dict):
                    clean_step = {}
                    for key, value in step.items():
                        if (
                            isinstance(value, (str, int, float, bool, list, dict))
                            or value is None
                        ):
                            clean_step[key] = value
                        else:
                            clean_step[key] = str(value)
                    safe_steps.append(clean_step)
                else:
                    safe_steps.append(str(step))

            yield f"event: status\ndata: {safe_json_dumps({'type': 'status', 'status': task.status, 'steps': safe_steps})}\n\n"
        while True:
            try:
                event = await queue.get()
                formatted_event = safe_json_dumps(event)
                yield ": heartbeat\n\n"
                if event["type"] == "complete":
                    yield f"event: complete\ndata: {formatted_event}\n\n"
                    break
                elif event["type"] == "error":
                    yield f"event: error\ndata: {formatted_event}\n\n"
                    break
                elif event["type"] in [
                    "think",
                    "tool",
                    "act",
                    "run",
                    "step",
                    "log",
                    "message",
                ]:
                    yield f"event: {event['type']}\ndata: {formatted_event}\n\n"
                else:
                    yield f"event: {event['type']}\ndata: {formatted_event}\n\n"
            except asyncio.CancelledError:
                print(f"Client disconnected for flow {flow_id}")
                break
            except Exception as e:
                print(f"Error in flow event stream: {str(e)}")
                yield f"event: error\ndata: {safe_json_dumps({'message': str(e)})}\n\n"
                break

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/flows")
async def get_flows():
    sorted_flows = sorted(
        flow_manager.flows.values(), key=lambda t: t.created_at, reverse=True
    )
    return JSONResponse(
        content=[t.model_dump() for t in sorted_flows],
        headers={"Content-Type": "application/json"},
    )


@app.get("/flows/{flow_id}")
async def get_flow(flow_id: str):
    if flow_id not in flow_manager.flows:
        raise HTTPException(status_code=404, detail="Flow not found")
    return flow_manager.flows[flow_id]


@app.get("/tasks/{task_id}/events")
async def task_events(task_id: str):
    async def event_generator():
        if task_id not in task_manager.queues:
            yield f"event: error\ndata: {safe_json_dumps({'message': 'Task not found'})}\n\n"
            return

        queue = task_manager.queues[task_id]

        task = task_manager.tasks.get(task_id)
        if task:
            # 使用安全的序列化函数
            safe_steps = []
            for step in task.steps:
                if isinstance(step, dict):
                    clean_step = {}
                    for key, value in step.items():
                        if (
                            isinstance(value, (str, int, float, bool, list, dict))
                            or value is None
                        ):
                            clean_step[key] = value
                        else:
                            clean_step[key] = str(value)
                    safe_steps.append(clean_step)
                else:
                    safe_steps.append(str(step))

            yield f"event: status\ndata: {safe_json_dumps({'type': 'status', 'status': task.status, 'steps': safe_steps})}\n\n"

        while True:
            try:
                event = await queue.get()
                formatted_event = safe_json_dumps(event)
                # 调试日志：记录发送的事件
                # if event["type"] == "stream_complete":
                #     logger.debug(f"📤 [DEBUG] 准备发送stream_complete事件到前端")
                # print(f"*********event:{event}")
                yield ": heartbeat\n\n"

                # 处理终止事件
                if event["type"] == "complete":
                    yield f"event: complete\ndata: {formatted_event}\n\n"
                    break
                elif event["type"] == "error":
                    yield f"event: error\ndata: {formatted_event}\n\n"
                    break

                # 处理step事件（需要同时发送status）
                elif event["type"] == "step":
                    task = task_manager.tasks.get(task_id)
                    if task:
                        # 使用安全的序列化函数处理steps
                        safe_steps = []
                        for step in task.steps:
                            if isinstance(step, dict):
                                clean_step = {}
                                for key, value in step.items():
                                    if (
                                        isinstance(
                                            value, (str, int, float, bool, list, dict)
                                        )
                                        or value is None
                                    ):
                                        clean_step[key] = value
                                    else:
                                        clean_step[key] = str(value)
                                safe_steps.append(clean_step)
                            else:
                                safe_steps.append(str(step))

                        yield f"event: status\ndata: {safe_json_dumps({'type': 'status', 'status': task.status, 'steps': safe_steps})}\n\n"
                    yield f"event: {event['type']}\ndata: {formatted_event}\n\n"

                # 处理特定类型事件（think, tool, act, run）
                elif event["type"] in ["think", "tool", "act", "run"]:
                    yield f"event: {event['type']}\ndata: {formatted_event}\n\n"

                # 处理其他所有事件（包括stream_complete等新类型）
                else:
                    yield f"event: {event['type']}\ndata: {formatted_event}\n\n"
                    # if event["type"] == "stream_complete":
                    #     logger.debug(f"📤 [DEBUG] stream_complete事件已yield到SSE流")

            except asyncio.CancelledError:
                print(f"Client disconnected for task {task_id}")
                break
            except Exception as e:
                print(f"Error in event stream: {str(e)}")
                yield f"event: error\ndata: {safe_json_dumps({'message': str(e)})}\n\n"
                break

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/tasks")
async def get_tasks():
    sorted_tasks = sorted(
        task_manager.tasks.values(), key=lambda task: task.created_at, reverse=True
    )
    return JSONResponse(
        content=[task.model_dump() for task in sorted_tasks],
        headers={"Content-Type": "application/json"},
    )


@app.get("/tasks/{task_id}")
async def get_task(task_id: str):
    if task_id not in task_manager.tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    return task_manager.tasks[task_id]


# 获取历史记录
# 接口返回：
#  1. chat_history: 聊天历史
#  2. flow_history: 流程历史
@app.get("/sessions/history")
async def get_session_history():
    """获取历史记录"""
    chat_history = task_manager.get_session_history()
    flow_history = flow_manager.get_session_history()

    return JSONResponse(
        content={"chat_history": chat_history, "flow_history": flow_history},
        headers={"Content-Type": "application/json"},
    )


# 新增：处理交互回答的端点
@app.post("/tasks/{task_id}/interact")
async def handle_task_interaction(task_id: str, request_data: dict = Body(...)):
    """处理任务的交互回答"""
    user_response = request_data.get("response", "")
    if not user_response:
        raise HTTPException(status_code=400, detail="Response is required")

    success = await task_manager.handle_interaction(task_id, user_response)
    if not success:
        raise HTTPException(status_code=404, detail="Task not found")

    return {"status": "success", "message": "Interaction response received"}


# 新增：终止任务的端点
@app.post("/tasks/{task_id}/terminate")
async def terminate_task(task_id: str):
    """终止指定的任务"""
    if task_id not in task_manager.tasks:
        raise HTTPException(status_code=404, detail="Task not found")

    success = await task_manager.terminate_task(task_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to terminate task")

    return {"status": "success", "message": "Task terminated successfully"}


# 新增：终止流程的端点
@app.post("/flows/{flow_id}/terminate")
async def terminate_flow(flow_id: str):
    """终止指定的流程"""
    if flow_id not in flow_manager.flows:
        raise HTTPException(status_code=404, detail="Flow not found")

    success = await flow_manager.terminate_flow(flow_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to terminate flow")

    return {"status": "success", "message": "Flow terminated successfully"}


@app.get("/config/status")
async def check_config_status():
    config_path = Path(__file__).parent / "config" / "config.toml"
    example_config_path = Path(__file__).parent / "config" / "config.example.toml"

    if config_path.exists():
        return {"status": "exists"}
    elif example_config_path.exists():
        try:
            with open(example_config_path, "rb") as f:
                example_config = tomllib.load(f)
            return {"status": "missing", "example_config": example_config}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    else:
        return {"status": "no_example"}


@app.post("/config/save")
async def save_config(config_data: dict = Body(...)):
    try:
        config_dir = Path(__file__).parent / "config"
        config_dir.mkdir(exist_ok=True)

        config_path = config_dir / "config.toml"

        toml_content = ""

        if "llm" in config_data:
            toml_content += "# Global LLM configuration\n[llm]\n"
            llm_config = config_data["llm"]
            for key, value in llm_config.items():
                if key != "vision":
                    if isinstance(value, str):
                        toml_content += f'{key} = "{value}"\n'
                    else:
                        toml_content += f"{key} = {value}\n"

        if "server" in config_data:
            toml_content += "\n# Server configuration\n[server]\n"
            server_config = config_data["server"]
            for key, value in server_config.items():
                if isinstance(value, str):
                    toml_content += f'{key} = "{value}"\n'
                else:
                    toml_content += f"{key} = {value}\n"

        with open(config_path, "w", encoding="utf-8") as f:
            f.write(toml_content)

        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500, content={"message": f"Server error: {str(exc)}"}
    )


def open_local_browser(config):
    webbrowser.open_new_tab(f"http://{config['host']}:{config['port']}")


def load_config():
    try:
        config_path = Path(__file__).parent / "config" / "config.toml"

        if not config_path.exists():
            return {"host": "localhost", "port": 5172}

        with open(config_path, "rb") as f:
            config = tomllib.load(f)

        return {"host": config["server"]["host"], "port": config["server"]["port"]}
    except FileNotFoundError:
        return {"host": "localhost", "port": 5172}
    except KeyError as e:
        print(
            f"The configuration file is missing necessary fields: {str(e)}, use default configuration"
        )
        return {"host": "localhost", "port": 5172}


if __name__ == "__main__":
    import uvicorn

    server_config = load_config()
    open_with_config = partial(open_local_browser, server_config)

    # 初始化agent

    threading.Timer(3, open_with_config).start()
    uvicorn.run(app, host=server_config["host"], port=server_config["port"])
