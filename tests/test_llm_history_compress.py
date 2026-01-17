import asyncio
from unittest.mock import AsyncMock

import pytest

from app.exceptions import TokenLimitExceeded
from app.llm import LLM
from app.schema import Message


class _DummyUsage:
    prompt_tokens = 1
    completion_tokens = 1


class _DummyChoiceMsg:
    content = "ok"


class _DummyChoice:
    message = _DummyChoiceMsg()


class _DummyResp:
    choices = [_DummyChoice()]
    usage = _DummyUsage()


def test_llm_compress_writes_back_messages_and_keeps_current_user(monkeypatch):
    llm = LLM()
    llm.max_input_tokens = 10  # forcing compress path

    # Make token counter think everything is huge until compressed
    def _count_message_tokens_stub(_msgs):
        # If already compressed to 2 messages (summary + user), pretend it fits
        return 1 if len(_msgs) <= 4 else 999999

    monkeypatch.setattr(llm, "count_message_tokens", _count_message_tokens_stub)

    # Ensure summarizer exists and returns a short summary
    summarizer = LLM(config_name="summarizer")
    monkeypatch.setattr(summarizer, "ask", AsyncMock(return_value="SUMMARY"))

    # Patch factory to reuse our summarizer instance
    def _llm_factory(config_name="default", llm_config=None):
        return summarizer if config_name == "summarizer" else llm

    import app.llm as llm_module

    monkeypatch.setattr(llm_module, "LLM", _llm_factory)

    # Stub client call so it doesn't hit network
    llm.client = AsyncMock()
    llm.client.chat = AsyncMock()
    llm.client.chat.completions = AsyncMock()
    llm.client.chat.completions.create = AsyncMock(return_value=_DummyResp())

    msgs = [
        Message.user_message("U1"),
        Message.assistant_message("A1"),
        Message.user_message("CURRENT_QUESTION"),
        Message.assistant_message("A2"),
    ]
    # Current user should be last user: "CURRENT_QUESTION"
    out = asyncio.run(
        llm.ask(messages=msgs, system_msgs=[Message.system_message("SYS")])
    )
    assert out == "ok"

    assert len(msgs) == 2
    assert msgs[0].role == "assistant"
    assert "SUMMARY" in (msgs[0].content or "")
    assert msgs[1].role == "user"
    assert msgs[1].content == "CURRENT_QUESTION"


def test_llm_compress_raises_when_still_exceeds(monkeypatch):
    llm = LLM()
    llm.max_input_tokens = 10

    # Always exceed even after compress
    monkeypatch.setattr(llm, "count_message_tokens", lambda _msgs: 999999)

    # summarizer still returns something, but final still exceeds
    summarizer = LLM(config_name="summarizer")
    monkeypatch.setattr(summarizer, "ask", AsyncMock(return_value="SUMMARY"))

    def _llm_factory(config_name="default", llm_config=None):
        return summarizer if config_name == "summarizer" else llm

    import app.llm as llm_module

    monkeypatch.setattr(llm_module, "LLM", _llm_factory)

    llm.client = AsyncMock()
    llm.client.chat = AsyncMock()
    llm.client.chat.completions = AsyncMock()
    llm.client.chat.completions.create = AsyncMock(return_value=_DummyResp())

    msgs = [Message.user_message("CURRENT_QUESTION"), Message.assistant_message("A")]
    with pytest.raises(TokenLimitExceeded):
        asyncio.run(llm.ask(messages=msgs, system_msgs=[Message.system_message("SYS")]))
