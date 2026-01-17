import math
from typing import Dict, List, Optional, Union

import tiktoken
from openai import (
    APIError,
    AsyncAzureOpenAI,
    AsyncOpenAI,
    AuthenticationError,
    OpenAIError,
    RateLimitError,
)
from openai.types.chat import ChatCompletion, ChatCompletionMessage
from tenacity import (
    retry,
    retry_if_exception_type,
    retry_if_not_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

from app.bedrock import BedrockClient
from app.config import LLMSettings, config
from app.exceptions import TokenLimitExceeded
from app.logger import logger  # Assuming a logger is set up in your app
from app.schema import (
    ROLE_VALUES,
    Message,
    ToolChoice,
)

REASONING_MODELS = ["o1", "o3-mini"]
MULTIMODAL_MODELS = [
    "gpt-4-vision-preview",
    "gpt-4o",
    "gpt-4o-mini",
    "claude-3-opus-20240229",
    "claude-3-sonnet-20240229",
    "claude-3-haiku-20240307",
    # Qwen多模态模型
    "qwen3-omni-flash",
    "qwen-vl-max",
    "qwen-vl-plus",
    "qwen2-vl",
]


class TokenCounter:
    # Token constants
    BASE_MESSAGE_TOKENS = 4
    FORMAT_TOKENS = 2
    LOW_DETAIL_IMAGE_TOKENS = 85
    HIGH_DETAIL_TILE_TOKENS = 170

    # Image processing constants
    MAX_SIZE = 2048
    HIGH_DETAIL_TARGET_SHORT_SIDE = 768
    TILE_SIZE = 512

    def __init__(self, tokenizer):
        self.tokenizer = tokenizer

    def count_text(self, text: str) -> int:
        """Calculate tokens for a text string"""
        return 0 if not text else len(self.tokenizer.encode(text))

    def count_image(self, image_item: dict) -> int:
        """
        Calculate tokens for an image based on detail level and dimensions

        For "low" detail: fixed 85 tokens
        For "high" detail:
        1. Scale to fit in 2048x2048 square
        2. Scale shortest side to 768px
        3. Count 512px tiles (170 tokens each)
        4. Add 85 tokens
        """
        detail = image_item.get("detail", "medium")

        # For low detail, always return fixed token count
        if detail == "low":
            return self.LOW_DETAIL_IMAGE_TOKENS

        # For medium detail (default in OpenAI), use high detail calculation
        # OpenAI doesn't specify a separate calculation for medium

        # For high detail, calculate based on dimensions if available
        if detail == "high" or detail == "medium":
            # If dimensions are provided in the image_item
            if "dimensions" in image_item:
                width, height = image_item["dimensions"]
                return self._calculate_high_detail_tokens(width, height)

        return (
            self._calculate_high_detail_tokens(1024, 1024) if detail == "high" else 1024
        )

    def _calculate_high_detail_tokens(self, width: int, height: int) -> int:
        """Calculate tokens for high detail images based on dimensions"""
        # Step 1: Scale to fit in MAX_SIZE x MAX_SIZE square
        if width > self.MAX_SIZE or height > self.MAX_SIZE:
            scale = self.MAX_SIZE / max(width, height)
            width = int(width * scale)
            height = int(height * scale)

        # Step 2: Scale so shortest side is HIGH_DETAIL_TARGET_SHORT_SIDE
        scale = self.HIGH_DETAIL_TARGET_SHORT_SIDE / min(width, height)
        scaled_width = int(width * scale)
        scaled_height = int(height * scale)

        # Step 3: Count number of 512px tiles
        tiles_x = math.ceil(scaled_width / self.TILE_SIZE)
        tiles_y = math.ceil(scaled_height / self.TILE_SIZE)
        total_tiles = tiles_x * tiles_y

        # Step 4: Calculate final token count
        return (
            total_tiles * self.HIGH_DETAIL_TILE_TOKENS
        ) + self.LOW_DETAIL_IMAGE_TOKENS

    def count_content(self, content: Union[str, List[Union[str, dict]]]) -> int:
        """Calculate tokens for message content"""
        if not content:
            return 0

        if isinstance(content, str):
            return self.count_text(content)

        token_count = 0
        for item in content:
            if isinstance(item, str):
                token_count += self.count_text(item)
            elif isinstance(item, dict):
                if "text" in item:
                    token_count += self.count_text(item["text"])
                elif "image_url" in item:
                    token_count += self.count_image(item)
        return token_count

    def count_tool_calls(self, tool_calls: List[dict]) -> int:
        """Calculate tokens for tool calls"""
        token_count = 0
        for tool_call in tool_calls:
            if "function" in tool_call:
                function = tool_call["function"]
                token_count += self.count_text(function.get("name", ""))
                token_count += self.count_text(function.get("arguments", ""))
        return token_count

    def count_message_tokens(self, messages: List[dict]) -> int:
        """Calculate the total number of tokens in a message list"""
        total_tokens = self.FORMAT_TOKENS  # Base format tokens

        for message in messages:
            tokens = self.BASE_MESSAGE_TOKENS  # Base tokens per message

            # Add role tokens
            tokens += self.count_text(message.get("role", ""))

            # Add content tokens
            if "content" in message:
                tokens += self.count_content(message["content"])

            # Add tool calls tokens
            if "tool_calls" in message:
                tokens += self.count_tool_calls(message["tool_calls"])

            # Add name and tool_call_id tokens
            tokens += self.count_text(message.get("name", ""))
            tokens += self.count_text(message.get("tool_call_id", ""))

            total_tokens += tokens

        return total_tokens


class LLM:
    _instances: Dict[str, "LLM"] = {}

    def __new__(
        cls, config_name: str = "default", llm_config: Optional[LLMSettings] = None
    ):
        if config_name not in cls._instances:
            instance = super().__new__(cls)
            instance.__init__(config_name, llm_config)
            cls._instances[config_name] = instance
        return cls._instances[config_name]

    def __init__(
        self, config_name: str = "default", llm_config: Optional[LLMSettings] = None
    ):
        if not hasattr(self, "client"):  # Only initialize if not already initialized
            llm_config = llm_config or config.llm
            llm_config = llm_config.get(config_name, llm_config["default"])
            self.model = llm_config.model
            self.max_tokens = llm_config.max_tokens
            self.temperature = llm_config.temperature
            self.api_type = llm_config.api_type
            self.api_key = llm_config.api_key
            self.api_version = llm_config.api_version
            self.base_url = llm_config.base_url

            # Add token counting related attributes
            self.total_input_tokens = 0
            self.total_completion_tokens = 0
            self.max_input_tokens = (
                llm_config.max_input_tokens
                if hasattr(llm_config, "max_input_tokens")
                else None
            )

            # Initialize tokenizer
            try:
                self.tokenizer = tiktoken.encoding_for_model(self.model)
            except KeyError:
                # If the model is not in tiktoken's presets, use cl100k_base as default
                self.tokenizer = tiktoken.get_encoding("cl100k_base")

            if self.api_type == "azure":
                self.client = AsyncAzureOpenAI(
                    base_url=self.base_url,
                    api_key=self.api_key,
                    api_version=self.api_version,
                )
            elif self.api_type == "aws":
                self.client = BedrockClient()
            else:
                self.client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)

            self.token_counter = TokenCounter(self.tokenizer)

    def count_tokens(self, text: str) -> int:
        """Calculate the number of tokens in a text"""
        if not text:
            return 0
        return len(self.tokenizer.encode(text))

    def count_message_tokens(self, messages: List[dict]) -> int:
        return self.token_counter.count_message_tokens(messages)

    def update_token_count(self, input_tokens: int, completion_tokens: int = 0) -> None:
        """Update token counts"""
        # Only track tokens if max_input_tokens is set
        self.total_input_tokens += input_tokens
        self.total_completion_tokens += completion_tokens
        # logger.debug(
        #     f"Token usage: Input={input_tokens}, Completion={completion_tokens}, "
        #     f"Cumulative Input={self.total_input_tokens}, Cumulative Completion={self.total_completion_tokens}, "
        #     f"Total={input_tokens + completion_tokens}, Cumulative Total={self.total_input_tokens + self.total_completion_tokens}"
        # )

    def check_token_limit(self, input_tokens: int) -> bool:
        """Check if token limits are exceeded"""
        if self.max_input_tokens is not None:
            return (self.total_input_tokens + input_tokens) <= self.max_input_tokens
        # If max_input_tokens is not set, always return True
        return True

    def get_limit_error_message(self, input_tokens: int) -> str:
        """Generate error message for token limit exceeded"""
        if (
            self.max_input_tokens is not None
            and (self.total_input_tokens + input_tokens) > self.max_input_tokens
        ):
            return f"Request may exceed input token limit (Current: {self.total_input_tokens}, Needed: {input_tokens}, Max: {self.max_input_tokens})"

        return "Token limit exceeded"

    def truncate_messages(self, messages: List[dict], max_tokens: int) -> List[dict]:
        """
        Truncate messages to fit within token limit while preserving most recent interactions.

        Args:
            messages: List of messages to truncate
            max_tokens: Maximum allowed tokens

        Returns:
            List[dict]: Truncated list of messages
        """
        # Always keep system messages if present
        system_messages = [m for m in messages if m["role"] == "system"]
        non_system_messages = [m for m in messages if m["role"] != "system"]

        # Calculate tokens for system messages
        system_tokens = self.count_message_tokens(system_messages)
        remaining_tokens = max_tokens - system_tokens

        # If no space for other messages, return only system messages
        if remaining_tokens <= 0:
            logger.warning("Token limit only allows system messages")
            return system_messages

        # Start from most recent messages and work backwards
        truncated_messages = []
        current_tokens = 0

        for message in reversed(non_system_messages):
            message_tokens = self.count_message_tokens([message])
            if current_tokens + message_tokens <= remaining_tokens:
                truncated_messages.insert(0, message)
                current_tokens += message_tokens
            else:
                break

        return system_messages + truncated_messages

    # ----------------- 会话压缩（summarizer） -----------------
    _SUMMARIZER_SYSTEM_PROMPT = (
        "你是一个会话压缩助手。你的任务是将历史对话压缩为一段可供后续继续对话的摘要。\n"
        "要求：\n"
        "- 只总结历史中明确出现的信息，不要编造。\n"
        "- 保留关键事实、约束、用户偏好、已做出的决定、未完成事项、重要输出（含工具输出要点）。\n"
        "- 语言：中文。\n"
        "- 输出应尽量精炼，但不要遗漏关键细节。\n"
    )

    _SUMMARIZER_USER_PROMPT = (
        "请将以下【历史会话】压缩成一段摘要，供后续模型继续完成任务时作为上下文。\n\n"
        "【历史会话】\n"
        "{transcript}\n"
    )

    _TOKEN_LIMIT_REOPEN_SESSION_MSG = (
        "会话过长，已无法在当前上下文长度限制下继续执行。请重新开启会话后再试。"
    )

    def _require_summarizer_config(self) -> None:
        """强制要求存在独立 summarizer 配置（不允许静默回退到 default）。"""
        try:
            if "summarizer" not in config.llm:
                raise KeyError("llm.summarizer")
        except Exception as e:
            raise TokenLimitExceeded(
                "缺少独立 summarizer 配置（[llm.summarizer]）。请在 config.toml 中补充后重试。"
            ) from e

    @staticmethod
    def _get_role(msg: Union[dict, Message]) -> str:
        return msg.get("role") if isinstance(msg, dict) else str(msg.role)

    def _find_last_user_index(
        self, messages: List[Union[dict, Message]]
    ) -> Optional[int]:
        # 从第一条开始遍历，但返回最后一次出现的 user（保留“当前请求 user”语义）
        last_idx: Optional[int] = None
        for i in range(len(messages)):
            if self._get_role(messages[i]) == "user":
                last_idx = i
        return last_idx

    @staticmethod
    def _message_content_to_text(content: Union[str, list, None]) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        # 多模态/结构化 content：尽量提取 text 字段，其他内容用占位表示
        parts: List[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if "text" in item:
                    parts.append(str(item.get("text") or ""))
                elif "image_url" in item:
                    parts.append("[image]")
                else:
                    parts.append("[content]")
            else:
                parts.append(str(item))
        return "\n".join([p for p in parts if p])

    def _build_transcript(self, msgs: List[Union[dict, Message]]) -> str:
        lines: List[str] = []
        for m in msgs:
            role = self._get_role(m)
            if role == "system":
                continue
            if isinstance(m, dict):
                content = self._message_content_to_text(m.get("content"))
                name = m.get("name")
            else:
                content = self._message_content_to_text(m.content)
                name = m.name
            if role == "tool":
                prefix = f"Tool({name or ''})"
            elif role == "assistant":
                prefix = "Assistant"
            else:
                prefix = "User"
            if content:
                lines.append(f"{prefix}: {content}")
        return "\n".join(lines).strip()

    async def _compress_history_inplace_or_raise(
        self,
        *,
        messages: List[Union[dict, Message]],
        system_msgs: Optional[List[Union[dict, Message]]],
        supports_images: bool,
        tools_tokens: int = 0,
    ) -> None:
        """
        将非 system 历史压缩为 1 条 assistant 摘要，并保留“最近一条 user 消息”作为当前请求。
        - system 原样保留（system_msgs 不修改）
        - messages 原地替换为 [assistant(摘要), user(当前请求)]
        - 任何失败/超限：抛 TokenLimitExceeded，提示用户重新开启会话
        """
        if not self.max_input_tokens:
            return

        self._require_summarizer_config()

        last_user_idx = self._find_last_user_index(messages)
        if last_user_idx is None:
            raise TokenLimitExceeded(
                f"{self._TOKEN_LIMIT_REOPEN_SESSION_MSG}（未找到用户消息，无法压缩会话）"
            )

        current_user = messages[last_user_idx]
        if self._get_role(current_user) != "user":
            raise TokenLimitExceeded(
                f"{self._TOKEN_LIMIT_REOPEN_SESSION_MSG}（当前请求消息不合法）"
            )

        history_msgs = [m for i, m in enumerate(messages) if i != last_user_idx]
        transcript = self._build_transcript(history_msgs)
        if not transcript:
            # 单条 user 消息过长等情况，压缩无意义：直接报错
            raise TokenLimitExceeded(self._TOKEN_LIMIT_REOPEN_SESSION_MSG)

        try:
            summarizer = LLM(config_name="summarizer")
            summary_text = await summarizer.ask(
                messages=[
                    Message.user_message(
                        self._SUMMARIZER_USER_PROMPT.format(transcript=transcript)
                    )
                ],
                system_msgs=[Message.system_message(self._SUMMARIZER_SYSTEM_PROMPT)],
                stream=False,
                temperature=0.0,
                enable_history_compress=False,
            )
        except TokenLimitExceeded:
            raise TokenLimitExceeded(self._TOKEN_LIMIT_REOPEN_SESSION_MSG)
        except Exception as e:
            raise TokenLimitExceeded(self._TOKEN_LIMIT_REOPEN_SESSION_MSG) from e

        if not summary_text or not str(summary_text).strip():
            raise TokenLimitExceeded(self._TOKEN_LIMIT_REOPEN_SESSION_MSG)

        summary_msg: Union[dict, Message]
        if isinstance(current_user, dict):
            summary_msg = {"role": "assistant", "content": str(summary_text).strip()}
        else:
            summary_msg = Message.assistant_message(str(summary_text).strip())

        # 写回持久化：直接替换 messages 列表内容
        messages[:] = [summary_msg, current_user]

        # 重新校验 token：system + (摘要+user) + tools_tokens 必须 <= max_input_tokens
        formatted_system = (
            self.format_messages(system_msgs, supports_images) if system_msgs else []
        )
        formatted_conv = self.format_messages(messages, supports_images)
        total_tokens = self.count_message_tokens(
            formatted_system + formatted_conv
        ) + int(tools_tokens or 0)
        if total_tokens > self.max_input_tokens:
            raise TokenLimitExceeded(self._TOKEN_LIMIT_REOPEN_SESSION_MSG)

    @staticmethod
    def format_messages(
        messages: List[Union[dict, Message]], supports_images: bool = False
    ) -> List[dict]:
        """
        Format messages for LLM by converting them to OpenAI message format.

        Args:
            messages: List of messages that can be either dict or Message objects
            supports_images: Flag indicating if the target model supports image inputs

        Returns:
            List[dict]: List of formatted messages in OpenAI format

        Raises:
            ValueError: If messages are invalid or missing required fields
            TypeError: If unsupported message types are provided

        Examples:
            >>> msgs = [
            ...     Message.system_message("You are a helpful assistant"),
            ...     {"role": "user", "content": "Hello"},
            ...     Message.user_message("How are you?")
            ... ]
            >>> formatted = LLM.format_messages(msgs)
        """
        import json

        formatted_messages = []

        for message in messages:
            # Convert Message objects to dictionaries
            if isinstance(message, Message):
                message = message.to_dict()

            if isinstance(message, dict):
                # If message is a dict, ensure it has required fields
                if "role" not in message:
                    raise ValueError("Message dict must contain 'role' field")

                # ============ 新增：处理多模态消息（JSON格式） ============
                if supports_images and isinstance(message.get("content"), str):
                    try:
                        # 尝试解析JSON格式的多模态消息
                        content_obj = json.loads(message["content"])
                        if isinstance(content_obj, dict) and content_obj.get(
                            "multimodal"
                        ):
                            # 提取真实的多模态content数组
                            message["content"] = content_obj.get("content", [])
                            logger.debug(
                                f"✨ 解析多模态消息，包含 {len(message['content'])} 个部分"
                            )
                    except (json.JSONDecodeError, TypeError):
                        # 不是JSON格式，保持原样
                        pass

                # Process base64 images if present and model supports images
                if supports_images and message.get("base64_image"):
                    # Initialize or convert content to appropriate format
                    if not message.get("content"):
                        message["content"] = []
                    elif isinstance(message["content"], str):
                        message["content"] = [
                            {"type": "text", "text": message["content"]}
                        ]
                    elif isinstance(message["content"], list):
                        # Convert string items to proper text objects
                        message["content"] = [
                            (
                                {"type": "text", "text": item}
                                if isinstance(item, str)
                                else item
                            )
                            for item in message["content"]
                        ]

                    # Add the image to content
                    message["content"].append(
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{message['base64_image']}"
                            },
                        }
                    )

                    # Remove the base64_image field
                    del message["base64_image"]
                # If model doesn't support images but message has base64_image, handle gracefully
                elif not supports_images and message.get("base64_image"):
                    # Just remove the base64_image field and keep the text content
                    del message["base64_image"]

                if "content" in message or "tool_calls" in message:
                    formatted_messages.append(message)
                # else: do not include the message
            else:
                raise TypeError(f"Unsupported message type: {type(message)}")

        # Validate all messages have required fields
        for msg in formatted_messages:
            if msg["role"] not in ROLE_VALUES:
                raise ValueError(f"Invalid role: {msg['role']}")

        return formatted_messages

    @retry(
        wait=wait_random_exponential(min=1, max=60),
        stop=stop_after_attempt(6),
        retry=(
            retry_if_exception_type((OpenAIError, Exception, ValueError))
            & retry_if_not_exception_type(TokenLimitExceeded)
        ),
    )
    async def ask(
        self,
        messages: List[Union[dict, Message]],
        system_msgs: Optional[List[Union[dict, Message]]] = None,
        stream: bool = False,
        temperature: Optional[float] = None,
        enable_history_compress: bool = True,
    ) -> str:
        """
        Send a prompt to the LLM and get the response.

        Args:
            messages: List of conversation messages
            system_msgs: Optional system messages to prepend
            stream (bool): Whether to stream the response
            temperature (float): Sampling temperature for the response

        Returns:
            str: The generated response

        Raises:
            TokenLimitExceeded: If token limits are exceeded
            ValueError: If messages are invalid or response is empty
            OpenAIError: If API call fails after retries
            Exception: For unexpected errors
        """
        try:
            # Check if the model supports images
            supports_images = self.model in MULTIMODAL_MODELS

            # 先用原始入参计算 tokens（避免丢失 Message 列表引用，便于持久化写回）
            formatted_system = (
                self.format_messages(system_msgs, supports_images)
                if system_msgs
                else []
            )
            formatted_conv = self.format_messages(messages, supports_images)
            input_tokens = self.count_message_tokens(formatted_system + formatted_conv)

            if self.max_input_tokens and input_tokens > self.max_input_tokens:
                if not enable_history_compress:
                    raise TokenLimitExceeded(self._TOKEN_LIMIT_REOPEN_SESSION_MSG)
                await self._compress_history_inplace_or_raise(
                    messages=messages,
                    system_msgs=system_msgs,
                    supports_images=supports_images,
                    tools_tokens=0,
                )
                formatted_conv = self.format_messages(messages, supports_images)
                input_tokens = self.count_message_tokens(
                    formatted_system + formatted_conv
                )
                if input_tokens > self.max_input_tokens:
                    raise TokenLimitExceeded(self._TOKEN_LIMIT_REOPEN_SESSION_MSG)

            params = {
                "model": self.model,
                "messages": formatted_system + formatted_conv,
                "presence_penalty": 1.5,
            }

            if self.model in REASONING_MODELS:
                params["max_completion_tokens"] = self.max_tokens
            else:
                params["max_tokens"] = self.max_tokens
                params["temperature"] = (
                    temperature if temperature is not None else self.temperature
                )

            logger.debug(f"*****************llm params: {params}")
            if stream:
                # logger.info(f"llm request prompt: {messages}")
                # Streaming request
                try:
                    completion: ChatCompletion = (
                        await self.client.chat.completions.create(
                            **params,
                            extra_body={
                                "enable_thinking": False,
                            },
                            stream=True,
                        )
                    )

                    response = []
                    async for chunk in completion:
                        if not chunk.choices:
                            if hasattr(chunk, "usage") and chunk.usage:
                                print("\nUsage:")
                                print(chunk.usage)
                            continue

                        if (
                            hasattr(chunk.choices[0].delta, "content")
                            and chunk.choices[0].delta.content
                        ):
                            content = chunk.choices[0].delta.content
                            response.append(content)

                    final_response = "".join(response)
                    print(f"*****************llm response: {final_response}")
                    return final_response

                except Exception as e:
                    logger.error(f"Error in streaming response: {e}")
                    # 如果流式处理失败，尝试非流式请求作为备选
                    logger.info("Falling back to non-streaming request")
                    try:
                        completion: ChatCompletion = (
                            await self.client.chat.completions.create(
                                **params,
                                extra_body={
                                    "enable_thinking": False,
                                },
                                stream=False,
                            )
                        )
                        response_content = completion.choices[0].message.content
                        print(
                            f"*****************llm response (fallback): {response_content}"
                        )
                        return response_content
                    except Exception as fallback_error:
                        logger.error(f"Fallback request also failed: {fallback_error}")
                        raise fallback_error

            response = await self.client.chat.completions.create(**params, stream=False)
            print(f"*****************llm response: {response}")

            if not response.choices or not response.choices[0].message:
                logger.error(response)
                # raise ValueError("Invalid or empty response from LLM")
                return None
            # Update token counts
            self.update_token_count(
                response.usage.prompt_tokens, response.usage.completion_tokens
            )

            logger.debug(f"response content: {response.choices[0].message.content}")
            return response.choices[0].message.content

        except TokenLimitExceeded:
            # Re-raise token limit errors without logging
            raise
        except ValueError:
            logger.exception("Validation error")
            raise
        except OpenAIError as oe:
            logger.exception("OpenAI API error")
            if isinstance(oe, AuthenticationError):
                logger.error("Authentication failed. Check API key.")
            elif isinstance(oe, RateLimitError):
                logger.error("Rate limit exceeded. Consider increasing retry attempts.")
            elif isinstance(oe, APIError):
                logger.error(f"API error: {oe}")
            raise
        except Exception as e:
            logger.exception(f"Unexpected error in ask: {e}")
            raise

    @retry(
        wait=wait_random_exponential(min=1, max=60),
        stop=stop_after_attempt(6),
        retry=(
            retry_if_exception_type((OpenAIError, Exception, ValueError))
            & retry_if_not_exception_type(TokenLimitExceeded)
        ),
    )
    async def ask_with_images(
        self,
        messages: List[Union[dict, Message]],
        images: List[Union[str, dict]],
        system_msgs: Optional[List[Union[dict, Message]]] = None,
        stream: bool = False,
        temperature: Optional[float] = None,
        enable_history_compress: bool = True,
    ) -> str:
        """
        Send a prompt with images to the LLM and get the response.

        Args:
            messages: List of conversation messages
            images: List of image URLs or image data dictionaries
            system_msgs: Optional system messages to prepend
            stream (bool): Whether to stream the response
            temperature (float): Sampling temperature for the response

        Returns:
            str: The generated response

        Raises:
            TokenLimitExceeded: If token limits are exceeded
            ValueError: If messages are invalid or response is empty
            OpenAIError: If API call fails after retries
            Exception: For unexpected errors
        """
        try:
            # For ask_with_images, we always set supports_images to True because
            # this method should only be called with models that support images
            if self.model not in MULTIMODAL_MODELS:
                raise ValueError(
                    f"Model {self.model} does not support images. Use a model from {MULTIMODAL_MODELS}"
                )

            # Format messages with image support
            formatted_messages = self.format_messages(messages, supports_images=True)

            # Ensure the last message is from the user to attach images
            if not formatted_messages or formatted_messages[-1]["role"] != "user":
                raise ValueError(
                    "The last message must be from the user to attach images"
                )

            # Process the last user message to include images
            last_message = formatted_messages[-1]

            # Convert content to multimodal format if needed
            content = last_message["content"]
            multimodal_content = (
                [{"type": "text", "text": content}]
                if isinstance(content, str)
                else content if isinstance(content, list) else []
            )

            # Add images to content
            for image in images:
                if isinstance(image, str):
                    multimodal_content.append(
                        {"type": "image_url", "image_url": {"url": image}}
                    )
                elif isinstance(image, dict) and "url" in image:
                    multimodal_content.append({"type": "image_url", "image_url": image})
                elif isinstance(image, dict) and "image_url" in image:
                    multimodal_content.append(image)
                else:
                    raise ValueError(f"Unsupported image format: {image}")

            # Update the message with multimodal content
            last_message["content"] = multimodal_content

            # 同步回写到原始 messages（用于超限压缩时的持久化写回，避免丢失图片信息）
            raw_last_user_idx = self._find_last_user_index(messages)
            if raw_last_user_idx is None:
                raise ValueError(
                    "The last message must be from the user to attach images"
                )
            raw_last_user = messages[raw_last_user_idx]
            if isinstance(raw_last_user, dict):
                raw_last_user["content"] = multimodal_content
            else:
                raw_last_user.content = multimodal_content

            # Add system messages if provided
            if system_msgs:
                all_messages = (
                    self.format_messages(system_msgs, supports_images=True)
                    + formatted_messages
                )
            else:
                all_messages = formatted_messages

            # Calculate tokens and check limits
            input_tokens = self.count_message_tokens(all_messages)

            if self.max_input_tokens and input_tokens > self.max_input_tokens:
                if not enable_history_compress:
                    raise TokenLimitExceeded(self._TOKEN_LIMIT_REOPEN_SESSION_MSG)
                # ask_with_images 需要保留最后一条 user（包含图片）；压缩写回原始 messages 列表
                await self._compress_history_inplace_or_raise(
                    messages=messages,
                    system_msgs=system_msgs,
                    supports_images=True,
                    tools_tokens=0,
                )
                # 重新构造 all_messages：system + (摘要+当前 user + images 已附着在 current user 上)
                formatted_messages = self.format_messages(
                    messages, supports_images=True
                )
                if system_msgs:
                    all_messages = (
                        self.format_messages(system_msgs, supports_images=True)
                        + formatted_messages
                    )
                else:
                    all_messages = formatted_messages
                input_tokens = self.count_message_tokens(all_messages)
                if input_tokens > self.max_input_tokens:
                    raise TokenLimitExceeded(self._TOKEN_LIMIT_REOPEN_SESSION_MSG)

            # Set up API parameters
            params = {
                "model": self.model,
                "messages": all_messages,
                "stream": stream,
            }

            # Add model-specific parameters
            if self.model in REASONING_MODELS:
                params["max_completion_tokens"] = self.max_tokens
            else:
                params["max_tokens"] = self.max_tokens
                params["temperature"] = (
                    temperature if temperature is not None else self.temperature
                )

            # Handle non-streaming request
            if not stream:
                response = await self.client.chat.completions.create(**params)

                if not response.choices or not response.choices[0].message.content:
                    raise ValueError("Empty or invalid response from LLM")

                self.update_token_count(response.usage.prompt_tokens)
                return response.choices[0].message.content

            # Handle streaming request
            self.update_token_count(input_tokens)
            response = await self.client.chat.completions.create(**params)

            collected_messages = []
            async for chunk in response:
                chunk_message = chunk.choices[0].delta.content or ""
                collected_messages.append(chunk_message)
                print(chunk_message, end="", flush=True)

            print()  # Newline after streaming
            full_response = "".join(collected_messages).strip()

            if not full_response:
                raise ValueError("Empty response from streaming LLM")

            return full_response

        except TokenLimitExceeded:
            raise
        except ValueError as ve:
            logger.error(f"Validation error in ask_with_images: {ve}")
            raise
        except OpenAIError as oe:
            logger.error(f"OpenAI API error: {oe}")
            if isinstance(oe, AuthenticationError):
                logger.error("Authentication failed. Check API key.")
            elif isinstance(oe, RateLimitError):
                logger.error("Rate limit exceeded. Consider increasing retry attempts.")
            elif isinstance(oe, APIError):
                logger.error(f"API error: {oe}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error in ask_with_images: {e}")
            raise

    @retry(
        wait=wait_random_exponential(min=1, max=60),
        stop=stop_after_attempt(6),
        retry=(
            retry_if_exception_type((OpenAIError, Exception, ValueError))
            & retry_if_not_exception_type(TokenLimitExceeded)
        ),
    )
    async def ask_tool(
        self,
        messages: List[Union[dict, Message]],
        system_msgs: Optional[List[Union[dict, Message]]] = None,
        timeout: int = 300,
        tools: Optional[List[dict]] = None,
        tool_choice: str = ToolChoice.AUTO.value,  # type: ignore
        temperature: Optional[float] = None,
        enable_history_compress: bool = True,
        **kwargs,
    ) -> ChatCompletionMessage | None:
        """
        Ask LLM using functions/tools and return the response.

        Args:
            messages: List of conversation messages
            system_msgs: Optional system messages to prepend
            timeout: Request timeout in seconds
            tools: List of tools to use
            tool_choice: Tool choice strategy
            temperature: Sampling temperature for the response
            **kwargs: Additional completion arguments

        Returns:
            ChatCompletionMessage: The model's response

        Raises:
            TokenLimitExceeded: If token limits are exceeded
            ValueError: If tools, tool_choice, or messages are invalid
            OpenAIError: If API call fails after retries
            Exception: For unexpected errors
        """
        try:
            # Check if the model supports images
            supports_images = self.model in MULTIMODAL_MODELS

            # 计算 tools tokens
            # If there are tools, calculate token count for tool descriptions
            tools_tokens = 0
            if tools:
                for tool in tools:
                    tools_tokens += self.count_tokens(str(tool))

            formatted_system = (
                self.format_messages(system_msgs, supports_images)
                if system_msgs
                else []
            )
            formatted_conv = self.format_messages(messages, supports_images)
            input_tokens = (
                self.count_message_tokens(formatted_system + formatted_conv)
                + tools_tokens
            )

            if self.max_input_tokens and input_tokens > self.max_input_tokens:
                if not enable_history_compress:
                    raise TokenLimitExceeded(self._TOKEN_LIMIT_REOPEN_SESSION_MSG)
                await self._compress_history_inplace_or_raise(
                    messages=messages,
                    system_msgs=system_msgs,
                    supports_images=supports_images,
                    tools_tokens=tools_tokens,
                )
                formatted_conv = self.format_messages(messages, supports_images)
                input_tokens = (
                    self.count_message_tokens(formatted_system + formatted_conv)
                    + tools_tokens
                )
                logger.info(f"input tokens after compress: {input_tokens}")
                if input_tokens > self.max_input_tokens:
                    raise TokenLimitExceeded(self._TOKEN_LIMIT_REOPEN_SESSION_MSG)

            # Set up the completion request
            params = {
                "model": self.model,
                "messages": formatted_system + formatted_conv,
                "tools": tools,
                "tool_choice": tool_choice,
                "timeout": timeout,
                "presence_penalty": 2,
                "top_p": 0.95,
                "extra_body": {"top_k": 20},
                **kwargs,
            }

            # logger.info(f"llm request prompt: {messages}")
            if self.model in REASONING_MODELS:
                params["max_completion_tokens"] = self.max_tokens
            else:
                params["max_tokens"] = self.max_tokens
                params["temperature"] = (
                    temperature if temperature is not None else self.temperature
                )

            params["stream"] = False  # Always use non-streaming for tool requests
            logger.info(f"*****************llm params: {params}")
            response = await self.client.chat.completions.create(
                **params,
            )

            # Check if response is valid
            if not response.choices or not response.choices[0].message:
                logger.error(response)
                # raise ValueError("Invalid or empty response from LLM")
                return None

            # Update token counts
            self.update_token_count(
                response.usage.prompt_tokens, response.usage.completion_tokens
            )

            # logger.info(
            #     f"response content: {response.choices[0].message.content}, resoning content: {response.choices[0].message.reasoning_content}, tools: {response.choices[0].message.tool_calls}"
            # )
            return response.choices[0].message
        except TokenLimitExceeded:
            # Re-raise token limit errors without logging
            raise
        except ValueError as ve:
            logger.error(f"Validation error in ask_tool: {ve}")
            raise
        except OpenAIError as oe:
            logger.error(f"OpenAI API error: {oe}")
            if isinstance(oe, AuthenticationError):
                logger.error("Authentication failed. Check API key.")
            elif isinstance(oe, RateLimitError):
                logger.error("Rate limit exceeded. Consider increasing retry attempts.")
            elif isinstance(oe, APIError):
                logger.error(f"API error: {oe}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error in ask_tool: {e}")
            raise

    async def stream_ask(
        self,
        messages: List[Union[dict, Message]],
        system_msgs: Optional[List[Union[dict, Message]]] = None,
        temperature: Optional[float] = None,
        enable_history_compress: bool = True,
    ):
        """
        流式调用LLM，逐token返回（async generator）

        Args:
            messages: 对话消息列表
            system_msgs: 系统消息（可选）
            temperature: 采样温度

        Yields:
            str: 生成的文本chunk

        Example:
            async for chunk in llm.stream_ask(messages):
                print(chunk, end='', flush=True)
        """
        try:
            # 检查模型是否支持多模态
            supports_images = self.model in MULTIMODAL_MODELS

            formatted_system = (
                self.format_messages(system_msgs, supports_images)
                if system_msgs
                else []
            )
            formatted_conv = self.format_messages(messages, supports_images)
            input_tokens = self.count_message_tokens(formatted_system + formatted_conv)

            if self.max_input_tokens and input_tokens > self.max_input_tokens:
                if not enable_history_compress:
                    raise TokenLimitExceeded(self._TOKEN_LIMIT_REOPEN_SESSION_MSG)
                await self._compress_history_inplace_or_raise(
                    messages=messages,
                    system_msgs=system_msgs,
                    supports_images=supports_images,
                    tools_tokens=0,
                )
                formatted_conv = self.format_messages(messages, supports_images)
                input_tokens = self.count_message_tokens(
                    formatted_system + formatted_conv
                )
                logger.debug(f"流式输入tokens（压缩后）: {input_tokens}")
                if input_tokens > self.max_input_tokens:
                    raise TokenLimitExceeded(self._TOKEN_LIMIT_REOPEN_SESSION_MSG)

            # 构造请求参数
            params = {
                "model": self.model,
                "messages": formatted_system + formatted_conv,
                "stream": True,  # 启用流式
            }

            # 推理模型使用不同的参数
            if self.model in REASONING_MODELS:
                params["max_completion_tokens"] = self.max_tokens
            else:
                params["max_tokens"] = self.max_tokens
                params["temperature"] = (
                    temperature if temperature is not None else self.temperature
                )

            logger.debug(
                f"🌊 流式请求参数: model={self.model}, max_tokens={self.max_tokens}"
            )

            # 发起流式请求
            completion = await self.client.chat.completions.create(**params)

            # 逐chunk yield
            total_tokens = []
            async for chunk in completion:
                if not chunk.choices:
                    # 处理usage信息
                    if hasattr(chunk, "usage") and chunk.usage:
                        logger.debug(f"流式调用token使用: {chunk.usage}")
                    continue

                # 提取content
                if (
                    hasattr(chunk.choices[0].delta, "content")
                    and chunk.choices[0].delta.content
                ):
                    content = chunk.choices[0].delta.content
                    total_tokens.append(content)
                    yield content

            # 记录完整响应（用于调试）
            final_response = "".join(total_tokens)
            logger.debug(f"🌊 流式输出完成，总长度: {len(final_response)} 字符")

        except TokenLimitExceeded:
            raise
        except OpenAIError as oe:
            logger.error(f"流式调用OpenAI API错误: {oe}")
            raise
        except Exception as e:
            logger.error(f"流式调用意外错误: {e}")
            raise
